"""客户独立推进的隔离库验收：真实连接、线程和事务，消息全部现场合成。"""

from contextlib import contextmanager
from datetime import timedelta
from threading import Event as ThreadEvent, Thread
from uuid import uuid4

import pytest

from dake.analysis.providers.xhub import XHubProvider
from dake.config import Settings, bound
from dake.domain.models import Event, Message, Window
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


def _open_slots(runs):
    with runs.connection() as store:
        store._execute("""UPDATE runtime_limits SET held_reason='general', owner=NULL, lease_until=NULL
            WHERE resource='analysis'""")


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
