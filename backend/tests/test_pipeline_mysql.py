"""客户独立推进的隔离库验收：真实连接、线程和事务，消息全部现场合成。"""

from contextlib import contextmanager
from datetime import timedelta
import json
from threading import Event as ThreadEvent, Thread
from uuid import uuid4

import pytest

from dake.analysis.providers.xhub import XHubProvider
from dake.config import Settings, bound
from dake.domain.models import Candidate, Event, Message, Window
from dake.ingestion.adapter import OfficialChatSource
from dake.jobs.runner import RunRunner, prepare_request
from dake.repositories.attempts import AttemptRepository
from dake.repositories.customers import CustomerRepository
from dake.repositories.mysql import MySQLStore
from dake.repositories.pipeline import PipelineError, PipelineRepository
from dake.repositories.runs import RunRepository
from dake.services.customers import CustomerSyncService
from dake.services.processing import IncrementalService
from fakes import FakeProvider, FakeSource


pytestmark = pytest.mark.isolated_mysql


@pytest.fixture(autouse=True)
def explicit_isolation(request, monkeypatch):
    """没有专用入口就跳过；启用后拒绝任何非本次隔离目标的连接。"""
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


def _message(uid, mid, at):
    return Message(uid, mid, at, "虚构工单 CASE-SYN-42 报告上传失败，等待排查。",
        conversation_id="synthetic-group", sender_id="synthetic-human")


def _window(start):
    return Window(start, start + timedelta(minutes=15))


def _request(settings, uid, start):
    request, _ = prepare_request(settings, {"uid": uid, "start": start.isoformat(),
        "end": (start + timedelta(minutes=15)).isoformat(), "interval": "15m", "offline": True})
    request.update(message_source="stored", allowed_groups=["synthetic-group"], strict_window=True,
        phase="catchup", catchup_target=(start + timedelta(hours=1)).isoformat())
    return request


def test_progress_rows_project_old_and_new_cycles_without_snapshots(explicit_isolation, tmp_path):
    """批量进度保留旧周期和隔离计数，跳过缺失运行，不传回事件快照。"""
    runs = RunRepository(explicit_isolation.settings(tmp_path))
    uid = "synthetic-progress-" + uuid4().hex[:8]
    rid = runs.create(dict(uid=uid, mode="official"), 2)["run_id"]
    audit = [dict(kind="withheld", count=2, incomplete=True)]
    cycles = [dict(processed=3, created=["synthetic-event"], filter_audit=audit, events=[{"summary": "合成摘要"}]),
              dict(updated=["synthetic-event"])]
    with runs.connection() as store:
        store._execute("UPDATE processing_runs SET cycles=%s,completed_windows=2,status='succeeded' WHERE run_id=%s",
                       (json.dumps(cycles), rid))
    projected = runs.progress_rows([rid, rid, str(uuid4())], include_cycles=True)
    assert set(projected) == {rid}
    assert sorted(projected[rid]["cycles"], key=lambda c: -c["processed"]) == [
        dict(processed=3, created=["synthetic-event"], updated=[], filter_audit=audit),
        dict(processed=0, created=[], updated=["synthetic-event"], filter_audit=[])]
    head = runs.progress_rows([rid])[rid]
    assert head["completed_windows"] == head["total_windows"] == 2
    assert "cycles" not in head and "request" not in head


def _open_slots(runs):
    with runs.connection() as store:
        store._execute("""UPDATE runtime_limits SET held_reason='general', owner=NULL, lease_until=NULL
            WHERE resource='analysis'""")


def test_orphan_recovery_respects_live_worker_even_after_slot_expires(explicit_isolation, tmp_path):
    settings = explicit_isolation.settings(tmp_path, analysis_slots=1, live_reserved=0)
    runs, pipeline = RunRepository(settings), PipelineRepository(settings)
    _open_slots(runs)
    origin = bound("2026-11-16T08:00:00+08:00")
    rid = runs.create(_request(settings, "synthetic-orphan-" + uuid4().hex[:8], origin), 1)["run_id"]
    with runs.claimed_work(source=False) as (run, _):
        assert run["run_id"] == rid
        with runs.connection() as store:
            store._execute("UPDATE runtime_limits SET lease_until=NOW(6) WHERE owner=%s", (run["runner_id"],))
        pipeline.recover_interrupted()
        assert runs.working_ids([rid]) == {rid}
        assert runs.get(rid)["status"] == "running"
        other = runs.create(_request(settings, "synthetic-next-" + uuid4().hex[:8], origin), 1)["run_id"]
        assert runs.claim_fair("synthetic-competitor") is None
        runs.cancel_queued(other)
    with runs.connection() as legacy:
        with legacy.customer_lock(run["uid"]):
            runs.recover_interrupted()
            assert runs.get(rid)["status"] == "running"
    pipeline.recover_interrupted()
    pipeline.recover_interrupted()
    assert runs.working_ids([rid]) == set()
    assert runs.get(rid)["status"] == "queued"
    runs.cancel_queued(rid)


def test_orphan_completed_window_uses_existing_receipt(explicit_isolation, tmp_path, monkeypatch):
    uid = "synthetic-committed-" + uuid4().hex[:8]
    settings = explicit_isolation.settings(tmp_path, uid=uid, analysis_slots=1, live_reserved=0)
    runs, pipeline, attempts = RunRepository(settings), PipelineRepository(settings), AttemptRepository(settings)
    _open_slots(runs)
    origin = bound("2026-11-16T08:15:00+08:00")
    rid = runs.create(_request(settings, uid, origin), 1)["run_id"]
    with _factory(explicit_isolation, FakeProvider(name="stub-v1"), attempts) as factory:
        runner = RunRunner(settings, runs, service_factory=factory)
        runner.pipeline = pipeline
        monkeypatch.setattr(runs, "finish", lambda *args, **kwargs: None)
        with runs.claimed_work(source=False) as (run, _):
            runner.execute(run)
    before = runs.get(rid)
    assert before["completed_windows"] == 1 and before["status"] == "running"
    pipeline.recover_interrupted()
    pipeline.recover_interrupted()
    after = runs.get(rid)
    assert after["status"] == "succeeded" and after["cycles"] == before["cycles"]
    with runs.connection() as store:
        receipts, _ = store._execute("SELECT receipt IS NOT NULL AS saved FROM processing_attempts WHERE run_id=%s", (rid,))
    assert receipts == [{"saved": 1}]


def test_orphan_unknown_window_does_not_return_to_queue(explicit_isolation, tmp_path):
    uid = "synthetic-unknown-" + uuid4().hex[:8]
    settings = explicit_isolation.settings(tmp_path, uid=uid, analysis_slots=1, live_reserved=0)
    runs, pipeline, attempts = RunRepository(settings), PipelineRepository(settings), AttemptRepository(settings)
    _open_slots(runs)
    origin = bound("2026-11-16T08:30:00+08:00")
    window = _window(origin)
    rid = runs.create(_request(settings, uid, origin), 1)["run_id"]
    with runs.claimed_work(source=False) as (run, _):
        runs.begin_window(rid, run["runner_id"], {"start": origin.isoformat(), "end": window.end.isoformat()}, {})
        aid = attempts.create(uid, "stub-v1", window, run_id=rid, window_index=0)
        attempts.failure(uid, "stub-v1", aid, unknown=False, model_unknown=True, error_type="SyntheticTimeout")
    pipeline.recover_interrupted()
    assert runs.get(rid)["status"] == "failed" and "核实" in runs.get(rid)["error"]
    assert runs.claim_fair("synthetic-next") is None
    assert attempts.get(uid, "stub-v1", aid)["outcome"] == "model_unknown"


@pytest.mark.parametrize("published", [True, False])
def test_orphan_fetch_checks_published_window(explicit_isolation, tmp_path, published):
    settings = explicit_isolation.settings(tmp_path)
    runs, pipeline = RunRepository(settings), PipelineRepository(settings)
    start = bound("2026-11-15T02:00:00+08:00") + timedelta(hours=int(published))
    window = _window(start)
    uid = "synthetic-fetch-" + uuid4().hex[:8]
    rid = runs.create(dict(mode="customer_sync", uid=uid), 1)["run_id"]
    with runs.claimed_work(source=True) as (run, _):
        assert run["run_id"] == rid
        runs.complete_window(rid, run["runner_id"], {"window": {"start": start.isoformat(), "end": window.end.isoformat()}})
        if published:
            with runs.connection() as store:
                store._execute("""INSERT INTO source_windows
                    (fetch_id,source,window_start,window_end,status,created_at)
                    VALUES (%s,'official_chat',%s,%s,'complete',NOW(6))""",
                    (str(uuid4()), start.replace(tzinfo=None), window.end.replace(tzinfo=None)))
    pipeline.recover_interrupted()
    assert runs.get(rid)["status"] == ("succeeded" if published else "failed")


@contextmanager
def _factory(target, provider, attempts):
    @contextmanager
    def build(configuration, **kwargs):
        store = MySQLStore(**target.options())
        try:
            yield IncrementalService(store, FakeSource([]), provider, configuration, attempts=attempts)
        finally:
            store.close()
    yield build


def test_fair_claim_prefers_the_customer_not_recently_served(explicit_isolation, tmp_path):
    """最近刚被服务的客户让出名额；没有排队任务的客户不会占住工人。"""
    origin = bound("2026-11-16T14:00:00+08:00")
    served, waiting = "synthetic-served-" + uuid4().hex[:8], "synthetic-waiting-" + uuid4().hex[:8]
    settings = explicit_isolation.settings(tmp_path, uid=waiting, start=origin, end=origin + timedelta(hours=1),
        analysis_slots=2, live_reserved=0)
    runs = RunRepository(settings)
    _open_slots(runs)
    runs.create(_request(settings, served, origin), 1)
    runs.create(_request(settings, waiting, origin), 1)
    with runs.connection() as store:
        store._execute("""INSERT INTO uid_pipeline_state (uid,phase,last_served_at,updated_at)
            VALUES (%s,'catchup',NOW(6),NOW(6))""", (served,))
    claimed = runs.claim_fair("fair-worker")
    assert claimed["uid"] == waiting
    runs.release_slot("fair-worker", source=False, slot=claimed["_capacity_slot"])
    runs.finish(claimed["run_id"], "fair-worker", error="合成验收结束")
    with runs.connection() as store:
        rows, _ = store._execute("SELECT run_id FROM processing_runs WHERE uid=%s AND status='queued'", (served,))
    for row in rows:
        runs.cancel_queued(row["run_id"])


def test_slow_customer_does_not_stop_another_customers_next_window(explicit_isolation, tmp_path):
    """A 仍在模型调用时，B 完成当前窗并继续下一窗；同一客户不能并行两窗。"""
    origin = bound("2026-11-16T10:00:00+08:00")
    slow, fast = "synthetic-slow-" + uuid4().hex[:12], "synthetic-fast-" + uuid4().hex[:12]
    settings = explicit_isolation.settings(tmp_path, uid=slow, start=origin, end=origin + timedelta(hours=1),
        analysis_slots=2, live_reserved=0, lease_seconds=30)
    runs, attempts = RunRepository(settings), AttemptRepository(settings)
    _open_slots(runs)
    started, release = ThreadEvent(), ThreadEvent()

    class Provider(FakeProvider):
        def extract(self, new_messages, context, existing_events):
            self.calls.append(new_messages[0].uid if new_messages else None)
            if new_messages and new_messages[0].uid == slow:
                started.set()
                assert release.wait(10)
            return []

    provider = Provider(name="stub-v1")
    with runs.connection() as store:
        with store.transaction(slow):
            store.upsert_messages([_message(slow, uuid4().hex, origin + timedelta(minutes=1))])
        with store.transaction(fast):
            store.upsert_messages([
                _message(fast, uuid4().hex, origin + timedelta(minutes=1)),
                _message(fast, uuid4().hex, origin + timedelta(minutes=16)),
            ])
    runs.create(_request(settings, slow, origin), 1)
    runs.create(_request(settings, fast, origin), 1)
    runner = RunRunner(settings, runs, service_factory=None)
    runner.pipeline = PipelineRepository(settings)
    errors = []

    def run_claimed(expected):
        try:
            with _factory(explicit_isolation, provider, attempts) as factory:
                runner.service_factory = factory
                claimed = runs.claim_fair(runner.runner_id)
                assert claimed and claimed["uid"] == expected
                try:
                    runner.execute(claimed)
                finally:
                    runs.release_slot(runner.runner_id, source=False, slot=claimed["_capacity_slot"])
        except Exception as error:
            errors.append(error)
            release.set()

    first = Thread(target=run_claimed, args=(slow,))
    first.start()
    assert started.wait(10)
    with _factory(explicit_isolation, provider, attempts) as factory:
        runner.service_factory = factory
        current = runs.claim_fair(runner.runner_id)
        assert current and current["uid"] == fast
        try:
            runner.execute(current)
        finally:
            runs.release_slot(runner.runner_id, source=False, slot=current["_capacity_slot"])
        assert runs.get(current["run_id"])["status"] == "succeeded"
        runs.create(_request(settings, fast, origin + timedelta(minutes=15)), 1)
        nxt = runs.claim_fair(runner.runner_id)
        assert nxt and nxt["uid"] == fast and nxt["request"]["start"] == (origin + timedelta(minutes=15)).isoformat()
        try:
            runner.execute(nxt)
        finally:
            runs.release_slot(runner.runner_id, source=False, slot=nxt["_capacity_slot"])
        assert runs.get(nxt["run_id"])["status"] == "succeeded"
        assert runs.claim_fair(runner.runner_id) is None
    release.set()
    first.join(15)
    assert not errors
    assert provider.calls.count(fast) >= 2


def test_empty_gap_commits_watermark_without_model_and_later_window_updates_event(explicit_isolation, tmp_path):
    """有消息、空窗、再有消息连续提交，同一事件更新且模型只处理两个非空窗。"""
    origin = bound("2026-11-16T11:00:00+08:00")
    uid = "synthetic-gap-" + uuid4().hex[:12]
    settings = explicit_isolation.settings(tmp_path, uid=uid, start=origin,
        end=origin + timedelta(minutes=45), analysis_slots=2, live_reserved=0)
    runs, attempts = RunRepository(settings), AttemptRepository(settings)
    _open_slots(runs)
    messages = [_message(uid, uuid4().hex, origin + timedelta(minutes=offset)) for offset in (1, 31)]
    provider = FakeProvider({message.message_id: Candidate("排障", "synthetic-upload", "虚构上传问题的进展",
        [message.key]) for message in messages}, name="stub-v1")
    with runs.connection() as store:
        with store.transaction(uid):
            store.upsert_messages(messages)
    runner = RunRunner(settings, runs)
    runner.pipeline = PipelineRepository(settings)
    with _factory(explicit_isolation, provider, attempts) as factory:
        runner.service_factory = factory
        for index in range(3):
            request = _request(settings, uid, origin + timedelta(minutes=15 * index))
            request["data_start"] = origin.isoformat()
            runs.create(request, 1)
            claimed = runs.claim_fair(runner.runner_id)
            assert claimed and claimed["uid"] == uid
            try:
                runner.execute(claimed)
            finally:
                runs.release_slot(runner.runner_id, source=False, slot=claimed["_capacity_slot"])
            saved = runs.get(claimed["run_id"])
            assert saved["status"] == "succeeded", saved["error"]
            with runs.connection() as store:
                rows, _ = store._execute("SELECT analyzed_until FROM uid_pipeline_state WHERE uid=%s", (uid,))
            assert rows[0]["analyzed_until"] == (origin + timedelta(minutes=15 * (index + 1))).replace(tzinfo=None)
            if index == 1:
                assert saved["cycles"][0]["processed"] == 0
                assert len(provider.calls) == 1
    assert len(provider.calls) == 2
    with runs.connection() as store:
        events = store.list_events(uid, "stub-v1")
        rows, _ = store._execute("SELECT COUNT(*) AS n FROM processing_attempts WHERE uid=%s AND receipt IS NOT NULL", (uid,))
    assert rows[0]["n"] == 3
    assert len(events) == 1 and len(events[0].updated_by) == 2


def test_analysis_worker_does_not_use_coordinator_connection(explicit_isolation, tmp_path):
    """协调线程的锁和连接生命周期不影响独立工人的业务提交及进度保存。"""
    origin = bound("2026-11-16T11:45:00+08:00")
    uid = "synthetic-thread-" + uuid4().hex[:12]
    settings = explicit_isolation.settings(tmp_path, uid=uid, start=origin,
        end=origin + timedelta(minutes=15), analysis_slots=2, live_reserved=0)
    runs, attempts = RunRepository(settings), AttemptRepository(settings)
    _open_slots(runs)
    with runs.connection() as store:
        with store.transaction(uid):
            store.upsert_messages([_message(uid, uuid4().hex, origin + timedelta(minutes=1))])
    runs.create(_request(settings, uid, origin), 1)
    runner = RunRunner(settings, runs)
    runner.pipeline = PipelineRepository(settings)

    class CoordinatorConnection:
        def _execute(self, *args):
            raise AssertionError("工人不得访问另一个线程的协调连接")

    runner._keeper = CoordinatorConnection()
    claimed = runs.claim_fair(runner.runner_id)
    with _factory(explicit_isolation, FakeProvider(name="stub-v1"), attempts) as factory:
        runner.service_factory = factory
        worker = Thread(target=runner.execute, args=(claimed,))
        worker.start()
        worker.join(10)
    runs.release_slot(runner.runner_id, source=False, slot=claimed["_capacity_slot"])
    assert not worker.is_alive()
    saved = runs.get(claimed["run_id"])
    assert saved["status"] == "succeeded", saved["error"]
    assert saved["completed_windows"] == 1


def test_expired_lease_rejects_stale_commit_and_unknown_model_is_not_resent(explicit_isolation, tmp_path):
    """租约过期后旧工人不能提交；模型已发出但结果未知时换流也不能重跑。"""
    origin = bound("2026-11-16T12:00:00+08:00")
    uid = "synthetic-fence-" + uuid4().hex[:12]
    settings = explicit_isolation.settings(tmp_path, uid=uid, start=origin, end=origin + timedelta(hours=1), lease_seconds=30)
    pipeline, attempts = PipelineRepository(settings), AttemptRepository(settings)
    window = _window(origin)
    claim = pipeline.acquire(uid, window, "owner-old", "stub-v1", phase="catchup",
        catchup_target=window.end, lease_seconds=30)
    with pipeline.connection() as store:
        store._execute("UPDATE uid_pipeline_state SET lease_until=DATE_SUB(NOW(6), INTERVAL 1 SECOND) WHERE uid=%s", (uid,))
    with pytest.raises(PipelineError, match="待核实"):
        pipeline.acquire(uid, window, "owner-new", "stub-v1", phase="catchup",
            catchup_target=window.end, lease_seconds=30)
    attempt_id = attempts.create(uid, "stub-v1", window)
    attempts.stage(uid, "stub-v1", attempt_id, "model_started", "started", {"request_digest": "synthetic"})
    action = pipeline.verify_expired(attempts)[0]
    assert action["action"] == "model_unknown"
    with pytest.raises(PipelineError):
        pipeline.acquire(uid, window, "owner-new", "replacement-stream", phase="live",
            catchup_target=window.end, lease_seconds=30)
    fresh = "synthetic-fence-" + uuid4().hex[:12]
    first = pipeline.acquire(fresh, window, "owner-old", "stub-v1", phase="catchup",
        catchup_target=window.end, lease_seconds=30)
    with pipeline.connection() as store:
        store._execute("UPDATE uid_pipeline_state SET lease_until=DATE_SUB(NOW(6), INTERVAL 1 SECOND) WHERE uid=%s", (fresh,))
    assert pipeline.verify_expired(attempts)[-1]["action"] == "requeued"
    second = pipeline.acquire(fresh, window, "owner-new", "stub-v1", phase="catchup",
        catchup_target=window.end, lease_seconds=30)
    store = MySQLStore(**explicit_isolation.options())
    try:
        with store.transaction(fresh):
            with pytest.raises(PipelineError, match="执行权"):
                pipeline.fence(store, first, incomplete=False)
        with store.transaction(fresh):
            pipeline.fence(store, second, incomplete=False)
    finally:
        store.close()
    with pytest.raises(PipelineError, match="不能跳过"):
        pipeline.acquire(fresh, _window(origin + timedelta(minutes=30)), "owner-new", "stub-v1",
            phase="catchup", catchup_target=window.end, lease_seconds=30)


def test_incomplete_fetch_does_not_advance_and_late_or_unattributed_rows_stay_isolated(explicit_isolation, tmp_path):
    """不完整来源不推进抓取水位；迟到和无归属隔离不阻止其他客户。"""
    origin = bound("2026-11-16T13:00:00+08:00")
    settings = explicit_isolation.settings(tmp_path, uid="synthetic-source", start=origin, end=origin + timedelta(hours=2))
    pipeline = PipelineRepository(settings)
    first, skipped = _window(origin), _window(origin + timedelta(minutes=30))
    assert pipeline.publish_source(first, message_count=1, isolated_count=0, complete=False, reason="响应不完整") is False
    assert pipeline.snapshot()["source"].get("fetched_until") in (None, "")
    assert pipeline.publish_source(first, message_count=1, isolated_count=0, complete=True) is True
    assert pipeline.publish_source(first, message_count=2, isolated_count=0, complete=True) is True
    with pipeline.connection() as store:
        rows, _ = store._execute("SELECT message_count FROM source_windows WHERE window_start=%s AND window_end=%s",
            (first.start.replace(tzinfo=None), first.end.replace(tzinfo=None)))
    assert len(rows) == 1 and rows[0]["message_count"] == 2
    with pytest.raises(PipelineError, match="不能跳过"):
        pipeline.publish_source(skipped, message_count=1, isolated_count=0, complete=True)
    fetched = pipeline.snapshot()["source"]["fetched_until"]
    assert fetched.replace(tzinfo=None) == (origin + timedelta(minutes=15)).replace(tzinfo=None)

    uid = "synthetic-late-" + uuid4().hex[:12]
    other = "synthetic-ready-" + uuid4().hex[:12]
    late_settings = explicit_isolation.settings(tmp_path, uid=uid, start=origin, end=origin + timedelta(hours=1))
    attempts = AttemptRepository(late_settings)
    provider = FakeProvider(name="stub-v1")
    store = MySQLStore(**explicit_isolation.options())
    try:
        current = _message(uid, uuid4().hex, origin + timedelta(minutes=20))
        late = _message(uid, uuid4().hex, origin + timedelta(minutes=5))
        with store.transaction(uid):
            store.upsert_messages([current, late])
            event = Event("synthetic-event-" + uuid4().hex[:8], uid, "stub-v1", "排障", "synthetic-topic",
                origin, origin + timedelta(minutes=15), "待确认", "待确认", "虚构概述", origin)
            snapshot = event.to_dict()
            snapshot.pop("updated_by")
            event.updated_by = [{"action": "created", "window_start": origin.isoformat(),
                "window_end": (origin + timedelta(minutes=15)).isoformat(), "message_ids": [], "snapshot": snapshot}]
            store.save_event(event)
        runs = RunRepository(late_settings)
        _open_slots(runs)
        request = _request(late_settings, uid, origin + timedelta(minutes=15))
        request["data_start"] = origin.isoformat()
        runs.create(request, 1)
        runner = RunRunner(late_settings, runs)
        runner.pipeline = pipeline
        with _factory(explicit_isolation, provider, attempts) as factory:
            runner.service_factory = factory
            claimed = runs.claim_fair(runner.runner_id)
            try:
                runner.execute(claimed)
            finally:
                runs.release_slot(runner.runner_id, source=False, slot=claimed["_capacity_slot"])
        rows, _ = store._execute("SELECT kind FROM isolated_records WHERE message_id=%s", (late.message_id,))
        kept, _ = store._execute("SELECT message_id FROM messages WHERE message_id=%s", (late.message_id,))
        assert rows and rows[0]["kind"] == "late_message" and kept
        pipeline.acquire(other, _window(origin), "owner-other", "stub-v1", phase="live",
            catchup_target=origin, lease_seconds=30)
    finally:
        store.close()

    sync = CustomerSyncService(settings, CustomerRepository(settings))
    missing = origin + timedelta(minutes=1)
    raw = {"groups": [{"group_number": "synthetic-unassigned", "records": [
        {"id": "synthetic-unassigned-1", "send_time": missing.strftime("%Y-%m-%d %H:%M:%S")}]}]}
    assert sync._remember_unattributed(raw, first, [
        {"group_id": "synthetic-unassigned", "status": "review", "uid": None}]) == 1
    stored = _message("synthetic-delete-" + uuid4().hex[:8], "synthetic-delete-1", missing)
    writer = MySQLStore(**explicit_isolation.options())
    try:
        with writer.transaction(stored.uid):
            writer.upsert_messages([stored])
        sync._note_deletions({"groups": []}, first)
        still, _ = writer._execute("SELECT message_id FROM messages WHERE message_id=%s", (stored.message_id,))
        note, _ = writer._execute("SELECT kind FROM isolated_records WHERE message_id=%s", (stored.message_id,))
        assert still and note and note[0]["kind"] == "source_deletion"
    finally:
        writer.close()
