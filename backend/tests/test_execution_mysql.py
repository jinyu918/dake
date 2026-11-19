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


def test_alert_events_upgrade_with_optional_legacy_tables(case, monkeypatch):
    from datetime import timedelta
    from pathlib import Path

    from dake.api.schemas import EventResponse
    from dake.domain.alert_policy import ALERT_SCHEMA
    from dake.ingestion.stored import ScopedStore
    from dake.repositories.pipeline import apply_statements
    from dake.services.queries import QueryService

    service = case.service
    store = service.store
    uid = case.uid
    # 先核对新库不依赖旧告警表，再装入升级前的合成记录。
    start = window(1).start
    recovery = replace(message(uuid4().hex, 2), uid=uid, sender_id="fixture-bot", conversation_id="allowed",
                       content="【告警恢复】\n告警ID：rule\n轮次ID：one")
    assert store.legacy_alert_messages(recovery, dict(alert_id="rule", round_id="one")) == []
    apply_statements(store, Path(__file__).resolve().parents[1] / "migrations/007_alert_preview.sql")
    other_uid = "synthetic-mysql-" + uuid4().hex
    trigger = replace(recovery, message_id=uuid4().hex, sent_at=start - timedelta(minutes=1),
                      content=json.dumps(dict(schema=ALERT_SCHEMA, source="fixture", alert_id="rule", round_id="one",
                                              kind="trigger", severity="warning", affected_uid=uid)))
    foreign = replace(trigger, uid=other_uid, message_id=uuid4().hex)
    for item in (trigger, foreign):
        with store.transaction(item.uid):
            store.upsert_messages([item])
            store._execute("INSERT INTO alert_preview_deliveries (source,message_id) VALUES (%s,%s)",
                           ("fixture", item.key))
    old_round = json.dumps(dict(members=[uid, other_uid], triggered_at=trigger.sent_at.isoformat()))
    with store.transaction(uid):
        store._execute("INSERT INTO alert_preview_rounds (source,alert_id,round_id,payload) VALUES (%s,%s,%s,%s)",
                       ("fixture", "rule", "one", old_round))
    monkeypatch.setattr("dake.services.processing.load_sender_kinds", lambda _: {
        (uid, recovery.source, recovery.sender_id, recovery.conversation_id): "bot"})
    service.store = ScopedStore(store, {"allowed"})
    service.source = FakeSource([recovery])
    service.ingestion.source = service.source
    cycle = service._run_window(uid, window(2))
    event, = store.list_events(uid, service.stream)
    assert cycle.created == [event.event_id]
    assert event.lifecycle_status == "已解决"
    assert {ref["message_id"] for ref in event.evidence} == {trigger.message_id, recovery.message_id}
    assert event.extensions["robot_alert"]["members"] == [uid]
    assert event.extensions["robot_alert"]["reminders"][0]["kind"] == "历史首次触发"
    assert store.list_events(other_uid, service.stream) == []
    items = QueryService(service.settings).events(uid, service.stream, "机器人告警")["items"]
    assert [EventResponse.model_validate(item).event_id for item in items] == [event.event_id]
    assert service._run_window(uid, window(2)).created == []
    assert store.list_events(uid, service.stream) == [event]
    rows, _ = store._execute("SELECT COUNT(*) AS n FROM alert_preview_deliveries")
    assert rows[0]["n"] == 2
