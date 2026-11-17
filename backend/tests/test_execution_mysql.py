"""过程记录的真实 MySQL 多连接验收；仅由专用隔离入口启用。"""

from contextlib import contextmanager
from dataclasses import replace
from threading import Event as ThreadEvent, Thread
from types import SimpleNamespace
from uuid import uuid4
import json

import pymysql
import pytest

from dake.config import Settings
from dake.analysis.providers.xhub import XHubProvider
from dake.ingestion.adapter import OfficialChatSource
from dake.jobs.runner import RunRunner, prepare_request
from dake.repositories.attempts import AttemptError, AttemptPendingVerification, AttemptRepository, DeliveryError
from dake.repositories.mysql import MySQLStore, StorageError
from dake.repositories.runs import RunRepository
from dake.services.executions import restore_receipt, verify_attempt
from dake.services.processing import IncrementalService
from fakes import FakeSource
from test_attribution import AttributionProvider, candidate, message, window
from test_summary_history import change


pytestmark = pytest.mark.isolated_mysql


@pytest.fixture(autouse=True)
def explicit_isolation(request, monkeypatch):
    """没有专用入口就跳过；启用后每次连接仍须与明确指定的目标完全一致。"""
    target = getattr(request.config, "_dake_isolated_mysql", None)
    if target is None:
        pytest.skip("须经 scripts/verify_execution_mysql.py 显式指定全新隔离库；没有默认数据库")
    original = MySQLStore.__init__
    def connect(self, host, port, user, password, database):
        supplied = dict(host=host, port=port, user=user, password=password, database=database)
        if supplied != target.options():
            raise RuntimeError("拒绝连接与显式隔离目标不一致的数据库")
        original(self, **supplied)
    def forbidden(*args, **kwargs):
        raise RuntimeError("隔离验收禁止默认配置、真实取数或真实模型")
    monkeypatch.setattr(MySQLStore, "__init__", connect)
    monkeypatch.setattr(Settings, "load", forbidden)
    monkeypatch.setattr(XHubProvider, "extract", forbidden)
    monkeypatch.setattr(OfficialChatSource, "load_day", forbidden)
    monkeypatch.setattr(OfficialChatSource, "load_window", forbidden)
    monkeypatch.setattr("dake.config.dotenv_values", forbidden)
    return target


@pytest.fixture
def case(explicit_isolation, tmp_path):
    """消息均新造，先入库但未消费；业务和阶段记录使用真实独立连接。"""
    target = explicit_isolation
    uid = "synthetic-mysql-" + uuid4().hex
    first = replace(message(uuid4().hex, 1), uid=uid)
    source = FakeSource([first])
    provider = AttributionProvider({first.message_id: candidate(first, changes=[change(first)])}, name="stub-v1")
    settings = target.settings(tmp_path, uid=uid, start=window(1).start, end=window(15).end)
    attempts = AttemptRepository(settings)
    stores = []
    def fresh(configuration=None):
        store = MySQLStore(**target.options())
        stores.append(store)
        return IncrementalService(store, source, provider, configuration or settings, attempts=attempts)
    service = fresh()
    with service.store.transaction(uid):
        service.store.upsert_messages([first])
    def latest():
        with attempts.connection() as store:
            rows, _ = store._execute("SELECT attempt_id FROM processing_attempts WHERE uid=%s AND stream=%s ORDER BY created_at DESC,attempt_id DESC LIMIT 1",
                                     (uid, provider.name))
        return attempts.get(uid, provider.name, rows[0]["attempt_id"])
    def inspect():
        with attempts.connection() as store:
            events = store.list_events(uid, provider.name)
            pending = store.pending_messages(uid, window(1).start, window(15).end, provider.name)
            rows, _ = store._execute("SELECT processed_by FROM messages WHERE uid=%s AND source=%s AND message_id=%s",
                                     (uid, first.source, first.message_id))
            marks = json.loads(rows[0]["processed_by"]) if isinstance(rows[0]["processed_by"], str) else rows[0]["processed_by"]
        return events, pending, marks
    try:
        yield SimpleNamespace(uid=uid, first=first, source=source, provider=provider, settings=settings,
            attempts=attempts, service=service, fresh=fresh, latest=latest, inspect=inspect, target=target)
    finally:
        for store in stores:
            store.close()


def test_atomic_commit_visibility_and_cross_day_evidence(case, monkeypatch):
    original = case.attempts.write_receipt
    observations = []
    def receipt(*args, **kwargs):
        original(*args, **kwargs)
        # 此时业务事务尚未提交；另一个连接应只看见已独立保存的阶段。
        events, pending, marks = case.inspect()
        record = case.latest()
        observations.append((len(events), len(pending), bool(marks), record["receipt"]))
        assert any(s["stage"] == "decision" for s in record["stages"])
    monkeypatch.setattr(case.attempts, "write_receipt", receipt)
    result = case.service.run_window(case.uid, window(1), execution=dict(run_id=str(uuid4()), window_index=0))
    assert observations == [(0, 1, False, None)]
    events, pending, marks = case.inspect()
    record = case.latest()
    assert not pending and marks[case.provider.name] == case.first.digest
    assert marks["attribution:" + case.provider.name]["records"][0]["attempt_id"] == result.attempt_id
    assert record["outcome"] == "committed" and record["receipt"]["writes"][0]["event_id"] == events[0].event_id
    assert restore_receipt(case.service, record["receipt"]).to_dict() == result.to_dict()
    monkeypatch.setattr(case.attempts, "write_receipt", original)


def test_classification_evidence_version_survives_mysql_roundtrip(case):
    fact = dict(value="报告上传失败", evidence_ids=[case.first.key], quote=case.first.content,
                name="症状与对照表现", type_label="排障")
    case.provider.mapping[case.first.message_id] = candidate(
        case.first, changes=[change(case.first)], types=["排障"],
        type_evidence={"排障": [dict(evidence_ids=[case.first.key], quote=case.first.content)]},
        typed_attributes={"排障": {"症状与对照表现": fact}})

    result = case.service.run_window(case.uid, window(1))

    events, pending, _ = case.inspect()
    assert result.created == [events[0].event_id] and not pending
    stored = events[0].typed_attributes["排障"]["症状与对照表现"]
    assert stored["value"] == fact["value"]
    assert stored["evidence_refs"] == [dict(source=case.first.source,
        message_id=case.first.message_id, id=case.first.message_id, digest=case.first.digest,
        conversation_id=case.first.conversation_id, send_time=case.first.sent_at.isoformat(),
        quote=case.first.content)]
    assert events[0].updated_by[0]["snapshot"]["typed_attributes"]["排障"]["症状与对照表现"] == stored
    follow = replace(message(uuid4().hex, 10, "虚构工单 CASE-SYN-42 上次上传失败的问题已定位为参数错误。"), uid=case.uid)
    case.source.messages.append(follow)
    case.provider.mapping[follow.message_id] = candidate(follow, "update", events[0].event_id, [case.first], changes=[change(follow)])
    next_result = case.fresh().run_window(case.uid, window(10))
    assert next_result.updated == result.created and not next_result.created
    record = case.latest()
    stages = {s["stage"]: s["payload"] for s in record["stages"]}
    assert stages["history"]["candidates"] and stages["history_context"]["result"]["items"]
    assert [r["message_id"] for r in record["receipt"]["processed_versions"]] == [follow.message_id]
    assert len(case.provider.calls) == 2


def test_common_fields_and_final_adopted_status_keep_closure_consistent_in_mysql(case):
    from dake.api.app_factory import create_app
    from dake.analysis.field_changes import field_key
    from dake.services.queries import QueryService
    from offline_http import TestClient
    from test_classification_replay import output_candidate
    from test_field_decisions import FieldsProvider, change as field_change

    first = replace(case.first, content="虚构申请R正在办理，合成等级甲")
    closed = replace(first, message_id=uuid4().hex, sent_at=first.sent_at.replace(day=2), content="虚构申请R虚构已恢复")
    followup = replace(first, message_id=uuid4().hex, sent_at=first.sent_at.replace(day=3), content="虚构申请R补充依据")
    suggestion = replace(first, message_id=uuid4().hex, sent_at=first.sent_at.replace(day=4), content="虚构申请R正在办理")
    target = dict(kind="common", name="status", type_label=None)
    severity = dict(kind="common", name="severity", type_label=None)
    opening = output_candidate(first, label="资源需求")
    opening["field_updates"] = [field_change(first, target, "正在办理", stage="跟进状态", reference="status"),
        field_change(first, severity, "合成等级甲", stage="等级", reference="severity")]
    provider = FieldsProvider({first.message_id: opening})
    provider.name = "stub-v1"
    settings = replace(case.settings, closed_statuses=("虚构已恢复",))
    case.service.provider, case.service.settings = provider, settings
    case.source.messages = [first, closed, followup, suggestion]
    event_id = case.service.run_window(case.uid, window(1)).created[0]
    app = create_app(settings, query_service=QueryService(settings, attempts=case.attempts), start_worker=False,
        runner=object(), run_repository=SimpleNamespace(init_schema=lambda: None))
    path = f"/api/v1/events/{event_id}"
    with TestClient(app) as client:
        def current():
            response = client.get(path, params=dict(uid=case.uid, stream="stub-v1"))
            assert response.status_code == 200
            return response.json()

        def decide(operation, action, value=None, lock=False):
            event = current()
            state = event["field_states"][field_key(target)]
            actions = [dict(target=target, action=action, value=value, fact_id=state["adopted_ids"][-1])]
            if lock:
                actions.append(dict(target=target, action="lock"))
            response = client.post(path + "/field-decisions", json=dict(uid=case.uid, stream="stub-v1",
                expected_version=event["version"], operation_id=operation, operator="虚构操作人",
                reason="合成状态与关闭宽限核对", actions=actions))
            assert response.status_code == 200
            return response.json()

        initial = current()
        assert initial["status"] == "正在办理" and initial["severity"] == "合成等级甲"
        assert initial["closed_at"] is None and not initial["field_issues"]
        closing = output_candidate(closed, label="资源需求", action="update", event_id=event_id, old_message=first)
        closing["field_updates"] = [field_change(closed, target, "虚构已恢复", stage="完成", intent="progress")]
        provider.mapping[closed.message_id] = closing
        case.service.run_window(case.uid, window(2))
        assert current()["closed_at"] == closed.sent_at.isoformat()
        addition = output_candidate(followup, label="资源需求", action="update", event_id=event_id,
            old_message=first, changes=[])
        addition["field_updates"] = []
        provider.mapping[followup.message_id] = addition
        case.service.run_window(case.uid, window(3))
        assert current()["end_time"] == followup.sent_at.isoformat()
        assert current()["closed_at"] == closed.sent_at.isoformat()
        for action in ("set", "unknown", "clear"):
            reopened = decide("reopen-" + action, action, "正在办理" if action == "set" else None)
            assert reopened["closed_at"] is None
            saved = decide("close-after-" + action, "set", "虚构已恢复", lock=action == "clear")
            assert saved["closed_at"] == followup.sent_at.isoformat()
            assert saved["updated_by"][-1]["message_ids"] == []
            fact = saved["field_states"][field_key(target)]["facts"][-1]
            assert fact["business_time"] is None and fact["decided_at"] != saved["closed_at"]
        proposed = output_candidate(suggestion, label="资源需求", action="update", event_id=event_id,
            old_message=first, changes=[])
        proposed["field_updates"] = [field_change(suggestion, target, "正在办理", stage="新阶段", intent="progress")]
        provider.mapping[suggestion.message_id] = proposed
        case.service.run_window(case.uid, window(4))
        final = current()
        assert final["status"] == "虚构已恢复" and final["closed_at"] == followup.sent_at.isoformat()
        assert final["field_states"][field_key(target)]["facts"][-1]["status"] == "suggested"
        assert final["updated_by"][0]["snapshot"] == initial["updated_by"][0]["snapshot"]


def test_revised_relation_without_proposal_is_atomic_and_queryable(case, monkeypatch):
    from copy import deepcopy
    from datetime import timedelta
    from dake.api.app_factory import create_app
    from dake.services.queries import QueryService
    from final_replay import record
    from offline_http import TestClient
    from test_classification_replay import ref
    from test_field_decisions import FieldsProvider

    first = replace(case.first, content="虚构事项T的A下载故障，请排查。")
    second = replace(first, message_id=uuid4().hex, sent_at=first.sent_at.replace(day=2),
        content="因T故障另立迁移项目M，决定迁出A。")
    provider = FieldsProvider({first.message_id: record(first, ["排障"])})
    provider.name = "stub-v1"
    settings = replace(case.settings, ingest_lookback=timedelta(days=20), context_lookback=timedelta(days=20))
    case.service.provider, case.service.settings = provider, settings
    case.source.messages = [first]
    parent = case.service.run_window(case.uid, window(1)).created[0]
    item = record(second, ["流失"])
    item["relations"] = [dict(kind="caused_by", event_id=parent, intent="attach",
        reason="因T故障另立迁移项目M", evidence=[ref(second)])]
    item["basis"].append(ref(first))
    provider.mapping[second.message_id] = item
    case.source.messages.append(second)
    child = case.service.run_window(case.uid, window(2)).created[0]
    before = {event.event_id: deepcopy(event.to_dict()) for event in case.inspect()[0]}
    revised = replace(second, content="迁移项目M继续办理。")
    case.source.messages = [first, revised]
    provider.mapping[second.message_id] = []
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise RuntimeError("合成依据复核保存失败")
        patch.setattr(case.service.store, "save_event", fail)
        with pytest.raises(RuntimeError, match="合成依据复核保存失败"):
            case.service.run_window(case.uid, window(3))
    assert {event.event_id: event.to_dict() for event in case.inspect()[0]} == before
    with case.attempts.connection() as store:
        rows, _ = store._execute("SELECT digest FROM messages WHERE uid=%s AND source=%s AND message_id=%s",
                                 (case.uid, second.source, second.message_id))
        assert rows[0]["digest"] == second.digest
    result = case.service.run_window(case.uid, window(3))
    saved = {event.event_id: event for event in case.inspect()[0]}
    assert result.updated == [child] and not result.created
    assert saved[child].relations[0]["review_status"] == "pending"
    assert saved[parent].to_dict() == before[parent]
    assert saved[child].updated_by[:len(before[child]["updated_by"])] == before[child]["updated_by"]
    revision = saved[child].updated_by[-1]
    assert revision["change_summary"] == "消息依据修订复核" and "relations" in revision["changed_fields"]
    receipt = case.latest()["receipt"]
    assert any(write["event_id"] == child for write in receipt["writes"])
    assert restore_receipt(case.service, receipt).to_dict() == result.to_dict()
    app = create_app(settings, query_service=QueryService(settings, attempts=case.attempts),
        start_worker=False, runner=object(), run_repository=SimpleNamespace(init_schema=lambda: None))
    with TestClient(app) as client:
        response = client.get(f"/api/v1/events/{child}", params=dict(uid=case.uid, stream="stub-v1"))
        assert response.status_code == 200
        assert response.json()["relations"][0]["review_status"] == "pending"
        assert response.json()["relations"][0]["evidence"][0]["digest"] == second.digest
        assert response.json()["relations"][0]["evidence"][0]["evidence_status"] == "superseded"
    history = deepcopy(saved[child].updated_by)
    case.source.messages = [first, second]
    restored = case.service.run_window(case.uid, window(4))
    saved = {event.event_id: event for event in case.inspect()[0]}
    assert restored.updated == [child]
    assert saved[child].relations[0]["review_status"] == "confirmed"
    assert saved[child].relations[0]["evidence"][0].get("evidence_status") != "superseded"
    assert saved[child].updated_by[:len(history)] == history
    assert any(write["event_id"] == child for write in case.latest()["receipt"]["writes"])


def test_field_decision_a36_http_roundtrip_concurrency_rollback_and_receipt(case, monkeypatch):
    from contextlib import contextmanager
    from copy import deepcopy
    from dake.api.app_factory import create_app
    from dake.domain.fields import FieldDecisionRequest
    from dake.repositories.mysql import TransactionOutcomeUnknown
    from dake.services.field_decisions import FieldDecisionService
    from dake.services.queries import QueryService
    from offline_http import TestClient
    from test_field_decisions import FieldsProvider, RESOURCE, change as field_change, proposal
    from dake.analysis.field_changes import field_key

    first = replace(case.first, content="虚构申请R目标2000，请继续办理")
    anchor = replace(first, message_id=uuid4().hex, content="虚构申请R已经受理", sent_at=first.sent_at.replace(hour=8))
    initial = proposal(first, [field_change(first)])
    initial["evidence_ids"].append(anchor.key)
    initial["changes"].append(dict(kind="progress", text=anchor.content,
        evidence=[dict(message_id=anchor.key, quote=anchor.content)], replaces=None))
    provider = FieldsProvider({first.message_id: initial})
    provider.name = "stub-v1"
    case.service.provider = provider
    case.source.messages = [first, anchor]
    initial_result = case.service.run_window(case.uid, window(1))
    event_id = initial_result.created[0]
    initial_receipt = case.latest()["receipt"]
    queries = QueryService(case.settings, attempts=case.attempts)
    app = create_app(case.settings, query_service=queries, start_worker=False, runner=object(),
                     run_repository=SimpleNamespace(init_schema=lambda: None))
    path = f"/api/v1/events/{event_id}"

    def request(version, operation, actions):
        return dict(uid=case.uid, stream="stub-v1", expected_version=version, operation_id=operation,
                    operator="虚构操作人", reason="合成实库裁定", actions=actions)

    with TestClient(app) as client:
        current = client.get(path, params=dict(uid=case.uid, stream="stub-v1")).json()
        locked = client.post(path + "/field-decisions", json=request(current["version"], "lock",
            [dict(target=RESOURCE, action="confirm"), dict(target=RESOURCE, action="lock")]))
        assert locked.status_code == 200
        fact_id = locked.json()["field_states"][field_key(RESOURCE)]["adopted_ids"][0]
        old_snapshot = deepcopy(locked.json()["updated_by"][0]["snapshot"])
        revised = replace(first, content="虚构申请R目标200，之前多写一个零；本次办理继续")
        case.source.messages = [revised, anchor]
        provider.mapping[first.message_id] = proposal(revised,
            [field_change(revised, value="200", intent="correction", replaces=[fact_id])], action="update",
            event_id=event_id, old_message=anchor, changes=[dict(kind="progress", text="本次办理继续",
                evidence=[dict(message_id=revised.key, quote="本次办理继续")], replaces=None)])
        started, release = ThreadEvent(), ThreadEvent()
        extract = provider.extract

        def paused(*args):
            started.set()
            assert release.wait(5)
            return extract(*args)

        provider.extract = paused
        outcomes = []
        errors = []

        def automatic():
            try:
                outcomes.append(case.service.run_window(case.uid, window(2)))
            except BaseException as error:
                errors.append(type(error).__name__)

        thread = Thread(target=automatic)
        thread.start()
        assert started.wait(5)
        posted = []
        stale = Thread(target=lambda: posted.append(client.post(path + "/field-decisions", json=request(
            locked.json()["version"], "stale-concurrent", [dict(target=RESOURCE, action="unlock")]))))
        stale.start()
        release.set()
        thread.join(8)
        stale.join(8)
        assert not thread.is_alive() and not stale.is_alive() and not errors
        assert posted[0].status_code == 409 and outcomes[0].updated == [event_id]
        current = client.get(path, params=dict(uid=case.uid, stream="stub-v1")).json()
        state = current["field_states"][field_key(RESOURCE)]
        assert current["typed_attributes"]["资源需求"][RESOURCE["name"]]["value"] == "2000" and state["locked"]
        assert current["updated_by"][0]["snapshot"] == old_snapshot
        suggested = next(fact for fact in state["facts"] if fact["status"] == "suggested")
        assert suggested["evidence"][0]["digest"] == revised.digest
        assert "本次办理继续" in current["summary"]
        before = deepcopy(current)
        invalid = client.post(path + "/field-decisions", json=request(current["version"], "rollback", [
            dict(target=RESOURCE, action="unknown"), dict(target=dict(kind="common", name="assignees"), action="set", value="无效类型")]))
        assert invalid.status_code == 400
        assert client.get(path, params=dict(uid=case.uid, stream="stub-v1")).json() == before
        unlocked = client.post(path + "/field-decisions", json=request(current["version"], "unlock", [dict(target=RESOURCE, action="unlock")]))
        assert unlocked.status_code == 200
        assert unlocked.json()["typed_attributes"]["资源需求"][RESOURCE["name"]]["value"] == "2000"
        problems = [item["id"] for item in unlocked.json()["field_issues"] if item["field_key"] == field_key(RESOURCE)]
        adopted = client.post(path + "/field-decisions", json=request(unlocked.json()["version"], "adopt", [
            dict(target=RESOURCE, action="adopt", fact_id=suggested["id"], issue_ids=problems)]))
        assert adopted.status_code == 200
        assert adopted.json()["typed_attributes"]["资源需求"][RESOURCE["name"]]["value"] == "200"
        assert client.get(path + "/field-decisions/adopt", params=dict(uid=case.uid, stream="stub-v1")).status_code == 200

        class SaveFailedQueries(QueryService):
            @contextmanager
            def connection(self):
                with super().connection() as store:
                    save = store.save_event

                    def failed(event):
                        save(event)
                        raise StorageError("合成裁定写入后、提交前失败")

                    store.save_event = failed
                    yield store

        before_failure = client.get(path, params=dict(uid=case.uid, stream="stub-v1")).json()
        with pytest.raises(StorageError):
            FieldDecisionService(SaveFailedQueries(case.settings)).submit(event_id, FieldDecisionRequest.model_validate(
                request(adopted.json()["version"], "write-rollback", [dict(target=RESOURCE, action="clear")])))
        assert client.get(path, params=dict(uid=case.uid, stream="stub-v1")).json() == before_failure
        assert client.get(path + "/field-decisions/write-rollback", params=dict(uid=case.uid, stream="stub-v1")).status_code == 404

        class AckLostQueries(QueryService):
            @contextmanager
            def connection(self):
                with super().connection() as store:
                    commit = store._conn.commit

                    def lost():
                        commit()
                        raise pymysql.OperationalError(2013, "synthetic acknowledgement lost")

                    store._conn.commit = lost
                    yield store

        service = FieldDecisionService(AckLostQueries(case.settings))
        with pytest.raises(TransactionOutcomeUnknown):
            service.submit(event_id, FieldDecisionRequest.model_validate(request(adopted.json()["version"], "ack-lost",
                [dict(target=RESOURCE, action="confirm")])))
        receipt = client.get(path + "/field-decisions/ack-lost", params=dict(uid=case.uid, stream="stub-v1"))
        assert receipt.status_code == 200 and receipt.json()["operation_id"] == "ack-lost"
        retried = client.post(path + "/field-decisions", json=request(adopted.json()["version"], "ack-lost",
            [dict(target=RESOURCE, action="confirm")]))
        assert retried.status_code == 200 and len(retried.json()["manual_operations"]) == 4
        assert restore_receipt(case.service, initial_receipt).to_dict() == initial_result.to_dict()
        no_messages = case.service.run_window(case.uid, window(3))
        assert not no_messages.processed and not no_messages.updated
        assert case.latest()["receipt"]["writes"] == []
        assert restore_receipt(case.service, case.latest()["receipt"]).to_dict() == no_messages.to_dict()
def test_all_customer_timer_window_pause_failure_resume_and_empty_window(explicit_isolation, tmp_path, monkeypatch):
    """真实队列和数据库验证自动任务；所有来源及模型输入均为现场合成。"""
    from datetime import timedelta
    from dake.config import PROJECT_ROOT, bound
    from dake.repositories.customers import CustomerRepository
    from dake.repositories.tasks import TaskRepository
    from dake.services.customers import CustomerSyncService
    from dake.services.tasks import TaskService
    from test_ingestion import record
    from test_ingestion_windows import window_payload

    origin = bound("2026-11-16T10:00:00+08:00")
    now = [origin + timedelta(minutes=30)]
    monkeypatch.setattr("dake.repositories.tasks._now", lambda: now[0].replace(tzinfo=None))
    uids = ["synthetic-timer-" + uuid4().hex for _ in range(4)]
    settings = explicit_isolation.settings(tmp_path, uid=uids[0], start=origin, end=now[0])
    runs, tasks, customers = RunRepository(settings), TaskRepository(settings), CustomerRepository(settings)
    tasks.init_schema()
    with runs.connection() as store:
        for statement in (PROJECT_ROOT / "backend/migrations/003_customer_directory.sql").read_text().split(";"):
            if statement.strip():
                store._execute(statement)
    fetched, analyzed = [], []
    rows = []
    for index, uid in enumerate(uids):
        for minute in ([31] if index == 3 else [1, 16, 31]):
            at = origin + timedelta(minutes=minute)
            content = {1: "虚构工单 CASE-SYN-42 报告上传失败，等待排查。",
                       16: "虚构工单 CASE-SYN-42 已复现报告上传失败。",
                       31: "虚构工单 CASE-SYN-42 已增加日志，等待下一次复现。"}[minute]
            rows.append((uid, at, record(str(uuid4().int), group=uid,
                time=at.strftime("%Y-%m-%d %H:%M:%S"), content=content)))

    class Chats:
        def fetch_window(self, start, end):
            fetched.append((start, end))
            groups = [(uid, [row for owner, at, row in rows if owner == uid and start <= at < end]) for uid in uids]
            groups = [(uid, items) for uid, items in groups if items]
            result = window_payload(*groups, start=start, end=end)
            if not groups:
                result.update(total=0, groups=[])
            return result

    class Directory:
        def level(self, uid):
            return {"uid": uid, "level": ["L6", "L10", "L5", "L9"][uids.index(uid)]}

        def tam(self, uid):
            return {"uid": uid, "tam": "合成负责人", "tam_b": None}

    class Provider(AttributionProvider):
        def __init__(self):
            super().__init__(name="stub-v1")

        def extract(self, pending, context, events):
            analyzed.append([(m.uid, m.sent_at) for m in pending])
            return [candidate(m, "update" if events else "new", events[0].event_id if events else None,
                evidence=context[:1], changes=[change(m)]) for m in pending]

    @contextmanager
    def analysis(configuration, **kwargs):
        store = MySQLStore(**explicit_isolation.options())
        try:
            yield IncrementalService(store, FakeSource([]), Provider(), configuration,
                attempts=AttemptRepository(configuration))
        finally:
            store.close()

    runner = RunRunner(settings, runs, service_factory=analysis,
        customer_factory=lambda s: CustomerSyncService(s, customers, Chats(), Directory()))
    service = TaskService(settings, tasks, runs, customers, clock=lambda: now[0])
    task = service.create(dict(name="合成全客户定时任务", mode="automatic", start=origin.isoformat(),
        interval="15m", frequency="5m", retries=0, include_new=True))
    tid = task["task_id"]
    service.action(tid, "resume")

    def step():
        now[0] += timedelta(microseconds=1)
        service.tick(force=True)
        run = runs.claim_next(runner.runner_id)
        if run:
            runner.execute(run)

    def complete(eid):
        for _ in range(40):
            if tasks.execution(eid)["status"] not in ("queued", "running"):
                return
            step()
        pytest.fail("合成自动执行未在预期步数内结束")

    service.tick(force=True)
    first = tasks.executions(tid)[0]
    step()
    service.action(tid, "pause")
    complete(first["execution_id"])
    assert tasks.get(tid)["cursor_at"] == (origin + timedelta(minutes=15)).isoformat()
    assert len(tasks.executions(tid)) == 1
    assert max(end for _, end in fetched) == origin + timedelta(minutes=15)
    with runs.connection() as store:
        original_ids = {uid: [e.event_id for e in store.list_events(uid, "stub-v1")] for uid in uids}
    assert len(original_ids[uids[0]]) == len(original_ids[uids[1]]) == 1
    assert original_ids[uids[2]] == []

    service.action(tid, "resume")
    step()
    step()
    service.tick(force=True)
    interrupted = runs.claim_next(runner.runner_id)
    assert interrupted and interrupted["request"]["mode"] == "official"
    failed_uid = interrupted["uid"]
    runs.finish(interrupted["run_id"], runner.runner_id, error="合成网络失败")
    service = TaskService(settings, TaskRepository(settings), RunRepository(settings), customers, clock=lambda: now[0])
    service.action(tid, "pause")
    second = tasks.executions(tid)[0]
    complete(second["execution_id"])
    assert tasks.execution(second["execution_id"])["status"] == "partial"

    now[0] += timedelta(minutes=15)
    service.action(tid, "resume")
    step()
    step()
    service.tick(force=True)
    third = tasks.executions(tid)[0]
    child = next(c for c in third["state"]["children"] if c["uid"] == failed_uid)
    assert runs.get(child["run_id"])["request"]["start"] == (origin + timedelta(minutes=15)).isoformat()
    assert any(c["uid"] == uids[3] and c["status"] == "queued" for c in third["state"]["children"])
    complete(third["execution_id"])
    assert tasks.execution(third["execution_id"])["status"] == "succeeded"
    with runs.connection() as store:
        for uid in uids[:2]:
            events = store.list_events(uid, "stub-v1")
            assert [e.event_id for e in events] == original_ids[uid]
            assert len(events[0].evidence) == 3
            assert store.pending_messages(uid, origin, now[0], "stub-v1") == []
    assert not any(uid == uids[2] for call in analyzed for uid, _ in call)

    before = len(analyzed)
    now[0] += timedelta(minutes=15)
    service.tick(force=True)
    fourth = tasks.executions(tid)[0]
    complete(fourth["execution_id"])
    assert tasks.execution(fourth["execution_id"])["status"] == "succeeded"
    assert len(analyzed) == before
    assert tasks.get(tid)["cursor_at"] == (origin + timedelta(hours=1)).isoformat()
    service.action(tid, "pause")


@pytest.mark.parametrize("failed_stage", ["input", "decision", "program_result", "commit"])
def test_stage_failure_rolls_back_business_but_preserves_previous_stages(case, monkeypatch, failed_stage):
    original = case.attempts.stage
    def fail(uid, stream, attempt_id, stage, *args):
        if stage == failed_stage:
            # 在阶段独立连接上制造真实 SQL 错误，观察业务连接能否完整回滚。
            with case.attempts.connection() as connection:
                connection._execute("SELECT synthetic_missing_execution_record_column")
            pytest.fail("虚构非法 SQL 不应成功")
        return original(uid, stream, attempt_id, stage, *args)
    monkeypatch.setattr(case.attempts, "stage", fail)
    with pytest.raises(StorageError):
        case.service.run_window(case.uid, window(1))
    events, pending, marks = case.inspect()
    record = case.latest()
    assert not events and pending == [case.first] and not marks
    assert record["receipt"] is None and record["outcome"] == "not_committed"
    assert record["stages"] and all(s["stage"] != failed_stage for s in record["stages"])


def test_receipt_already_written_then_failure_rolls_back_all_business(case, monkeypatch):
    original = case.attempts.write_receipt
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise AttemptError("虚构收据 SQL 完成后仍未提交即失败")
    monkeypatch.setattr(case.attempts, "write_receipt", fail)
    with pytest.raises(AttemptError):
        case.service.run_window(case.uid, window(1))
    events, pending, marks = case.inspect()
    assert not events and not marks and pending == [case.first]
    assert case.latest()["receipt"] is None
    assert any(s["stage"] == "decision" for s in case.latest()["stages"])


@pytest.mark.parametrize("committed", [False, True])
def test_commit_ack_loss_requires_verification_and_only_uncommitted_can_reanalyze(case, monkeypatch, committed):
    original = case.service.store._conn.commit
    def lost_ack():
        if committed:
            original()
        raise pymysql.OperationalError(2013, "synthetic commit acknowledgement lost")
    monkeypatch.setattr(case.service.store._conn, "commit", lost_ack)
    with pytest.raises(AttemptPendingVerification):
        case.service.run_window(case.uid, window(1))
    prior = case.latest()
    assert prior["outcome"] == "commit_unknown" and bool(prior["receipt"]) == committed
    events, pending, marks = case.inspect()
    assert bool(events) == bool(marks) == committed
    assert bool(pending) != committed
    fresh = case.fresh()
    with pytest.raises(AttemptPendingVerification):
        fresh.run_window(case.uid, window(1), execution=dict(retry_of=prior["attempt_id"]))
    assert len(case.provider.calls) == 1
    assert verify_attempt(fresh, case.uid, prior["attempt_id"])["outcome"] == ("committed" if committed else "not_committed")
    if committed:
        monkeypatch.setattr(case.source, "load_day", lambda *args: pytest.fail("已提交恢复不得重新取数"))
    result = fresh.run_window(case.uid, window(1), execution=dict(retry_of=prior["attempt_id"]))
    assert len(case.provider.calls) == (1 if committed else 2)
    assert len(case.inspect()[0]) == 1 and not case.inspect()[1]
    record = case.latest()
    assert record["retry_of"] == prior["attempt_id"] and result.attempt_id != prior["attempt_id"]
    if committed:
        assert record["receipt"]["kind"] == "delivery_recovery" and record["receipt"]["writes"] == []
        assert record["receipt"]["processed_versions"] == []


def test_rollback_ack_loss_closes_connection_and_is_verified_before_retry(case, monkeypatch):
    original = case.attempts.stage
    def fail(uid, stream, attempt_id, stage, *args):
        if stage == "commit":
            raise AttemptError("虚构提交前失败")
        return original(uid, stream, attempt_id, stage, *args)
    def lost_ack():
        raise pymysql.OperationalError(2013, "synthetic rollback acknowledgement lost")
    monkeypatch.setattr(case.attempts, "stage", fail)
    monkeypatch.setattr(case.service.store._conn, "rollback", lost_ack)
    with pytest.raises(AttemptPendingVerification):
        case.service.run_window(case.uid, window(1))
    prior = case.latest()
    fresh = case.fresh()
    assert verify_attempt(fresh, case.uid, prior["attempt_id"])["outcome"] == "not_committed"
    assert not case.inspect()[0] and not case.inspect()[2] and prior["receipt"] is None


@pytest.mark.parametrize("destination", ["snapshot", "advance"])
def test_committed_file_failure_only_recovers_original_result(case, monkeypatch, destination):
    def fail(*args, **kwargs):
        raise OSError("虚构展示文件保存失败")
    monkeypatch.setattr(case.service.state, destination, fail)
    with pytest.raises(DeliveryError):
        case.service.run_window(case.uid, window(1))
    prior = case.latest()
    assert prior["outcome"] == "committed" and prior["receipt"]
    before = case.inspect()[0]
    monkeypatch.setattr(case.source, "load_day", lambda *args: pytest.fail("恢复不得重新取数"))
    result = case.fresh().run_window(case.uid, window(1), execution=dict(retry_of=prior["attempt_id"]))
    assert case.inspect()[0] == before and len(case.provider.calls) == 1
    assert result.recovered_from_attempt_id == prior["attempt_id"]


@pytest.mark.parametrize("progress_saved", [False, True])
def test_committed_run_progress_failure_recovers_exact_attempt(case, monkeypatch, progress_saved):
    settings = replace(case.settings, end=window(1).end)
    runs = RunRepository(settings)
    @contextmanager
    def factory(configuration, **kwargs):
        yield case.fresh(configuration)
    runner = RunRunner(settings, runs, service_factory=factory)
    request, count = prepare_request(settings, dict(interval="1d"))
    original = runs.complete_window
    def fail(*args, **kwargs):
        if progress_saved:
            original(*args, **kwargs)
        raise StorageError("虚构任务进度保存失败")
    monkeypatch.setattr(runs, "complete_window", fail)
    first = runs.create(request, count)
    runner.execute(runs.claim_next(runner.runner_id))
    failed = runs.get(first["run_id"])
    prior = case.latest()
    assert failed["status"] == "failed" and failed["completed_windows"] == int(progress_saved)
    assert prior["receipt"] and not case.inspect()[1]
    assert prior["delivery"]["run_progress"]["status"] == "unconfirmed"
    monkeypatch.setattr(runs, "complete_window", original)
    second = runs.create(request, count)
    runner.execute(runs.claim_next(runner.runner_id))
    restored = runs.get(second["run_id"])
    assert restored["status"] == "succeeded" and restored["completed_windows"] == 1
    if progress_saved:
        assert case.latest()["retry_of"] is None and case.latest()["receipt"]["writes"] == []
    else:
        assert restored["cycles"][0]["recovered_from_attempt_id"] == prior["attempt_id"]
        assert case.latest()["retry_of"] == prior["attempt_id"]
    assert len(case.provider.calls) == len(case.inspect()[0]) == 1


def test_explicit_verification_waits_for_same_customer_lock(case):
    result = case.service.run_window(case.uid, window(1))
    entered, done = ThreadEvent(), ThreadEvent()
    outcomes, errors = [], []
    fresh = case.fresh()
    def worker():
        entered.set()
        try:
            outcomes.append(verify_attempt(fresh, case.uid, result.attempt_id))
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            done.set()
    with case.service.store.customer_lock(case.uid):
        thread = Thread(target=worker)
        thread.start()
        assert entered.wait(2)
        assert not done.wait(0.2)
    thread.join(5)
    assert not thread.is_alive() and not errors
    assert outcomes == [dict(attempt_id=result.attempt_id, outcome="committed")]


def test_attempt_reads_and_mutations_reject_foreign_scope(case):
    result = case.service.run_window(case.uid, window(1))
    original = case.latest()
    for uid, stream in [(case.uid + "-other", case.provider.name), (case.uid, "other-stream")]:
        assert case.attempts.get(uid, stream, result.attempt_id) is None
        with pytest.raises(AttemptError):
            case.attempts.stage(uid, stream, result.attempt_id, "synthetic", "completed", {})
        with pytest.raises(DeliveryError):
            case.attempts.delivery(uid, stream, result.attempt_id, "files", "failed")
    assert case.latest() == original
