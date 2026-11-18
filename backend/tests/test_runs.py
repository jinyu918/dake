"""后台任务的逐周期进度、失败恢复与 MySQL 持久化行为测试。"""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import os
from uuid import uuid4

import pytest

from dake.config import Settings, bound
from dake.domain.alert_policy import ALERT_SCHEMA, append_alert_delivery
from dake.domain.models import Candidate, Message
from dake.jobs.runner import RunRunner, prepare_request
from dake.repositories.runs import RunConflict, RunRepository
from dake.services.processing import IncrementalService
from dake.services.queries import QueryService
from fakes import FakeProvider, FakeSource, MemoryStore


class MemoryRuns:
    """仅用于行为测试的持久任务替身，保留状态转换与恢复定位的语义。"""

    def __init__(self):
        self.items = {}
        self.fail_completion_once = False
        self.worker_lock = "synthetic-runner-lock"

    def create(self, request, total):
        if any(item["uid"] == request["uid"] and item["status"] in ("queued", "running") for item in self.items.values()):
            raise RunConflict()
        item = dict(run_id=str(uuid4()), uid=request["uid"], mode=request["mode"], request=deepcopy(request),
                    status="queued", completed_windows=0, total_windows=total, cycles=[],
                    current_window=None, current_baseline=None, recovered_by=None, error=None)
        self.items[item["run_id"]] = item
        return deepcopy(item)

    def claim_next(self, runner_id):
        item = next((item for item in self.items.values() if item["status"] == "queued"), None)
        if item is not None:
            item.update(status="running", runner_id=runner_id)
            return deepcopy(item)

    def begin_window(self, run_id, runner_id, window, baseline):
        item = self.items[run_id]
        assert item["runner_id"] == runner_id and item["status"] == "running"
        item.update(current_window=deepcopy(window), current_baseline=deepcopy(baseline))

    def complete_window(self, run_id, runner_id, cycle):
        if self.fail_completion_once:
            self.fail_completion_once = False
            raise RuntimeError("synthetic progress write failure")
        item = self.items[run_id]
        assert item["runner_id"] == runner_id and item["status"] == "running"
        item["cycles"].append(deepcopy(cycle))
        item["completed_windows"] += 1
        item.update(current_window=None, current_baseline=None)

    def finish(self, run_id, runner_id, error=None):
        item = self.items[run_id]
        assert item["runner_id"] == runner_id
        item.update(status="failed" if error else "succeeded", error=error)

    def interrupted_window(self, uid, mode, window):
        return next((deepcopy(item) for item in reversed(list(self.items.values()))
                     if item["uid"] == uid and item["mode"] == mode and item["status"] == "failed"
                     and item["current_window"] == window and item["recovered_by"] is None), None)

    def mark_recovered(self, old, new):
        self.items[old]["recovered_by"] = new


def run_fixture(tmp_path, *, days=2):
    uid = "synthetic-run-customer"
    settings = replace(Settings(), uid=uid, data_dir=tmp_path, start=bound("2026-11-08"),
                       end=bound("2026-11-08") + timedelta(days=days), interval=timedelta(days=1))
    messages = [Message(uid, "1", bound("2026-11-08T09:00:00"), "合成运行消息一"),
                Message(uid, "2", bound("2026-11-09T09:00:00"), "合成运行消息二")]
    source = FakeSource(messages)
    provider = FakeProvider({"1": Candidate("排障", "synthetic-topic", "合成首日进展", ["1"]),
                             "2": Candidate("排障", "synthetic-topic", "合成次日进展", ["2"])})
    store, repository = MemoryStore(), MemoryRuns()
    seen_settings = []

    @contextmanager
    def service_factory(configuration, **kwargs):
        seen_settings.append(configuration)
        yield IncrementalService(store, source, provider, configuration)

    runner = RunRunner(settings, repository, service_factory=service_factory)
    request, total = prepare_request(settings, {"mode": "official", "interval": "1d"})
    return settings, repository, runner, store, source, provider, request, total, seen_settings


def execute_next(repository, runner, request, total):
    submitted = repository.create(request, total)
    claimed = repository.claim_next(runner.runner_id)
    runner.execute(claimed)
    return repository.items[submitted["run_id"]]


@pytest.mark.parametrize("enabled", [False, True])
def test_regular_stored_task_respects_alert_opt_in_and_group_scope(tmp_path, monkeypatch, enabled):
    _, repository, runner, store, _, provider, request, total, seen = run_fixture(tmp_path, days=1)
    env = tmp_path / "isolated.env"
    env.write_text("SYNTHETIC_ALERT_SOURCE=local-fixture\nSYNTHETIC_ALERT_REMINDER_MINUTES=5\n" if enabled else "",
                   encoding="utf-8")
    monkeypatch.setattr("dake.config.os.environ", {})
    monkeypatch.setattr(repository, "has_interrupted_range", lambda *_: False, raising=False)
    loaded = Settings.load(env)
    runner.settings = replace(runner.settings, synthetic_alert_source=loaded.synthetic_alert_source,
                              alert_reminder_minutes=loaded.alert_reminder_minutes)
    request.update(message_source="stored", allowed_groups=["allowed"], strict_window=True)
    body = json.dumps(dict(schema=ALERT_SCHEMA, source="local-fixture", alert_id="fixture", round_id="one",
                           kind="recovery", severity="warning", affected_uid=request["uid"]))
    allowed = Message(request["uid"], "alert-in", bound("2026-11-08T09:00:00"), body,
                      source="official_chat", conversation_id="allowed", sender_id="fixture-bot")
    outside = replace(allowed, message_id="alert-out", conversation_id="outside")
    store.messages.update({m.key: m for m in (allowed, outside)})
    monkeypatch.setattr("dake.services.processing.load_sender_kinds", lambda _: {
        (allowed.uid, allowed.source, allowed.sender_id, m.conversation_id): "bot" for m in (allowed, outside)})
    saved = []
    preview = None

    def save_alert(delivery, minutes):
        nonlocal preview
        saved.append((delivery["message_id"], minutes))
        preview = append_alert_delivery(preview, delivery, lambda _: None, minutes)

    monkeypatch.setattr(store, "save_alert_delivery", save_alert, raising=False)
    result = execute_next(repository, runner, request, total)
    assert result["status"] == "succeeded" and seen[-1].synthetic_alert_source == loaded.synthetic_alert_source
    assert saved == ([(allowed.key, 5)] if enabled else [])
    assert (allowed.key, provider.name) in store.processed and (outside.key, provider.name) not in store.processed
    if enabled:
        assert preview["state"] == "待匹配恢复"
        trigger = replace(allowed, message_id="late-trigger", sent_at=allowed.sent_at - timedelta(hours=1),
                          content=body.replace('"recovery"', '"trigger"'))
        store.messages[trigger.key] = trigger
        later = dict(request, data_start=request["start"], start=bound("2026-11-12").isoformat(),
                     end=bound("2026-11-13").isoformat())
        result = execute_next(repository, runner, later, total)
        assert result["status"] == "succeeded"
        assert preview["state"] == "已恢复" and preview["recovered_at"] == allowed.sent_at.isoformat()
        assert [(r["kind"], r["at"]) for r in preview["reminders"]] == [("历史首次触发", trigger.sent_at.isoformat())]
    assert not provider.calls


def test_removed_demo_task_never_builds_official_source(tmp_path):
    """旧任务不能在移除演示实现后被误交给官方取数入口。"""
    _, repository, runner, _, _, _, request, total, seen = run_fixture(tmp_path)
    request["mode"] = "demo"
    result = execute_next(repository, runner, request, total)
    assert result["status"] == "failed" and result["completed_windows"] == 0
    assert seen == []


def test_run_saves_each_cycle_and_repeated_run_has_zero_increment(tmp_path):
    _, repository, runner, store, _, _, request, total, _ = run_fixture(tmp_path)
    run = execute_next(repository, runner, request, total)
    assert run["status"] == "succeeded" and run["completed_windows"] == 2
    assert [len(cycle["created"]) for cycle in run["cycles"]] == [1, 0]
    assert [len(cycle["updated"]) for cycle in run["cycles"]] == [0, 1]
    stable_id = run["cycles"][0]["created"][0]
    assert run["cycles"][1]["updated"] == [stable_id]
    repeated = execute_next(repository, runner, request, total)
    assert repeated["status"] == "succeeded"
    assert all(not cycle["created"] and not cycle["updated"] and cycle["processed"] == 0 for cycle in repeated["cycles"])
    assert len(store.events) == 1


def test_failure_preserves_completed_cycles_and_sanitizes_error(tmp_path):
    _, repository, runner, _, _, provider, request, total, _ = run_fixture(tmp_path)
    provider.mapping["2"] = Candidate("排障", "synthetic-topic", "合成错误", ["missing-id"])
    run = execute_next(repository, runner, request, total)
    assert run["status"] == "failed" and run["completed_windows"] == 1
    assert len(run["cycles"]) == 1 and run["current_window"]["start"].startswith("2026-11-09")
    assert "missing-id" not in run["error"] and "合成错误" not in run["error"]


def test_progress_write_failure_restores_committed_cycle_and_fresh_late_changes(tmp_path):
    _, repository, runner, store, source, provider, request, total, _ = run_fixture(tmp_path, days=1)
    repository.fail_completion_once = True
    failed = execute_next(repository, runner, request, total)
    assert failed["status"] == "failed" and failed["completed_windows"] == 0
    original_event = next(iter(store.events))
    late = Message(request["uid"], "3", bound("2026-11-08T10:00:00"), "合成迟到的另一事件")
    source.messages.append(late)
    provider.mapping["3"] = Candidate("商务需求", "synthetic-invoice", "合成商务需求", ["3"])
    retried = execute_next(repository, runner, request, total)
    cycle = retried["cycles"][0]
    assert retried["status"] == "succeeded" and cycle["recovered_from_history"] is True
    assert original_event in cycle["created"] and len(cycle["created"]) == 2
    assert cycle["processed"] == 2 and len(store.events) == 2
    assert failed["recovered_by"] == retried["run_id"]
    repeated = execute_next(repository, runner, request, total)
    assert repeated["cycles"][0]["created"] == [] and repeated["cycles"][0]["processed"] == 0


def test_loss_of_worker_lock_after_commit_is_failed_and_recoverable(tmp_path):
    _, repository, runner, store, _, _, request, total, _ = run_fixture(tmp_path, days=1)

    class LostLock:
        def __init__(self):
            self.calls = 0

        def _execute(self, *args):
            self.calls += 1
            return [{"ok": int(self.calls == 1)}], 1

    runner._keeper = LostLock()
    failed = execute_next(repository, runner, request, total)
    assert failed["status"] == "failed" and len(store.events) == 1 and failed["completed_windows"] == 0
    runner._keeper = None
    retried = execute_next(repository, runner, request, total)
    cycle = retried["cycles"][0]
    assert cycle["recovered_from_history"] is True and cycle["counts_complete"] is False
    assert len(cycle["created"]) == 1 and len(store.events) == 1


def test_queued_run_uses_frozen_rules_after_configuration_changes(tmp_path):
    _, repository, runner, _, _, _, request, total, seen = run_fixture(tmp_path, days=1)
    runner.settings = replace(runner.settings, match_window=timedelta(days=99), default_status="合成新缺省")
    execute_next(repository, runner, request, total)
    assert seen[0].match_window == timedelta(days=7)
    assert seen[0].default_status == "未知"


@pytest.fixture
def persistent_runs():
    if os.environ.get("RUN_MYSQL_TESTS") != "1":
        pytest.skip("RUN_MYSQL_TESTS=1 enables the local MySQL task repository check")
    # 存储测试固定使用 stub，避免本地工作线程将测试任务交给已启用的真实模型。
    settings = replace(Settings.load(), provider="stub")
    repository = RunRepository(settings)
    repository.init_schema()
    uid = "test-api-run-" + uuid4().hex
    try:
        yield settings, repository, uid
    finally:
        with repository.connection() as store:
            store._execute("DELETE FROM processing_runs WHERE uid=%s", (uid,))
            store._execute("DELETE FROM messages WHERE uid=%s", (uid,))


@pytest.mark.mysql
def test_mysql_run_repository_keeps_progress_and_rejects_duplicate_customer(persistent_runs):
    settings, repository, uid = persistent_runs
    request, total = prepare_request(settings, {"uid": uid, "interval": "1d"})
    run = repository.create(request, total)
    with pytest.raises(RunConflict):
        repository.create(request, total)
    owner = str(uuid4())
    with repository.connection() as store:
        store._execute("UPDATE processing_runs SET status='running',runner_id=%s WHERE run_id=%s", (owner, run["run_id"]))
    window = {"start": request["start"], "end": bound("2026-11-09").isoformat()}
    repository.begin_window(run["run_id"], owner, window, {})
    cycle = dict(uid=uid, stream="stub-v1", window=window, processed=0, ingested=0,
                 created=[], updated=[], review=[], events=[], watermark={})
    repository.complete_window(run["run_id"], owner, cycle)
    repository.finish(run["run_id"], owner, error="合成测试中断")
    restored = RunRepository(settings).get(run["run_id"])
    assert restored["completed_windows"] == 1 and restored["cycles"] == [cycle]
    assert restored["status"] == "failed"
    assert repository.list(uid)["items"][0]["cycles"] == []
    assert repository.list(uid)["total"] == 1


@pytest.mark.mysql
def test_config_includes_new_official_customer_without_synthetic_source_pollution(persistent_runs):
    settings, repository, uid = persistent_runs
    message = Message(uid, str(uuid4().int), bound("2026-11-08T09:00:00"), "合成客户统计测试")
    with repository.connection() as store:
        with store.transaction(uid):
            store.upsert_messages([message])
    config = QueryService(settings).config()
    customer = next(item for item in config["customers"] if item["uid"] == uid)
    assert customer["message_count"] == 1 and customer["event_count"] == 0
    assert customer["stream"] == "stub-v1"


@pytest.mark.mysql
def test_recovery_and_list_do_not_sort_large_cycle_bodies(persistent_runs):
    """小排序缓冲区下也能列出大快照任务，复现真实回放发现的容量边界。"""
    settings, repository, uid = persistent_runs
    request, _ = prepare_request(settings, {"uid": uid, "interval": "1d"})
    window = {"start": request["start"], "end": bound("2026-11-09").isoformat()}
    ids = [str(uuid4()), str(uuid4())]
    with repository.connection() as store:
        for index, run_id in enumerate(ids):
            store._execute("""INSERT INTO processing_runs
                (run_id,uid,mode,status,request,total_windows,created_at,finished_at,current_window,cycles)
                VALUES (%s,%s,'official','failed',%s,5,%s,%s,%s,%s)""",
                (run_id, uid, json.dumps(request), f"2026-11-13 10:00:0{index}",
                 f"2026-11-13 10:01:0{index}", json.dumps(window),
                 json.dumps([{"synthetic_padding": "x" * 400000}])))

    @contextmanager
    def limited_connection():
        with repository.connection() as store:
            store._execute("SET SESSION sort_buffer_size=32768")
            yield store

    limited = RunRepository(settings)
    limited.connection = limited_connection
    recovered = limited.interrupted_window(uid, "official", window)
    assert recovered["run_id"] == ids[-1] and recovered["cycles"] == []
    listed = limited.list(uid, "official")
    assert len(listed["items"]) == 2 and all(item["cycles"] == [] for item in listed["items"])


def test_empty_progress_does_not_repeat_event_snapshots(tmp_path):
    """无变化窗口保留计数但不复制历史，实际变化仍带对应事件供旧页面读取。"""
    from dake.jobs.runner import compact_progress
    payload = dict(created=["changed"], updated=[], processed=1, events=[
        dict(event_id="changed", summary="合成变化"), dict(event_id="unchanged", summary="合成旧事")])
    compact = compact_progress(payload)
    assert [e["event_id"] for e in compact["events"]] == ["changed"]
    assert len(payload["events"]) == 2
    _, repository, runner, store, _, _, request, total, _ = run_fixture(tmp_path)
    execute_next(repository, runner, request, total)
    repeated = execute_next(repository, runner, request, total)
    assert repeated["status"] == "succeeded"
    assert all(c["events"] == [] and c["processed"] == 0 for c in repeated["cycles"])
    assert len(store.events) == 1


def test_resume_same_run_starts_at_saved_boundary_without_moving_settings(tmp_path):
    """完成周期交接后跳过旧窗口，业务历史起点和原窗口编号保持不变。"""
    settings, repository, runner, store, source, provider, request, total, seen = run_fixture(tmp_path)
    submitted = repository.create(request, total)
    claimed = repository.claim_next(runner.runner_id)
    original_complete = repository.complete_window

    def pause_after_first(*args):
        original_complete(*args)
        runner._stop.set()

    repository.complete_window = pause_after_first
    runner.execute(claimed)
    saved = repository.items[submitted["run_id"]]
    assert saved["completed_windows"] == 1 and len(store.events) == 1
    first_cycle = deepcopy(saved["cycles"][0])
    saved.update(status="running", error=None)
    runner._stop.clear()
    repository.complete_window = original_complete
    runner.execute(deepcopy(saved))
    assert saved["status"] == "succeeded" and saved["completed_windows"] == 2
    assert saved["cycles"][0] == first_cycle and len(saved["cycles"]) == 2
    assert len(next(iter(store.events.values())).updated_by) == 2
    assert all(configuration.start == settings.start for configuration in seen)


@pytest.mark.parametrize("still_owned", [True, False])
def test_begin_same_window_zero_affected_rows_checks_ownership(still_owned):
    """MySQL 幂等更新返回零时仍可续跑，真正失去执行权必须阻止。"""
    from dake.repositories.mysql import StorageError
    calls = []
    class Connection:
        def _execute(self, sql, args):
            calls.append((sql, args))
            if sql.startswith("SELECT"):
                return ([dict(run_id="synthetic-run")] if still_owned else []), 1
            return [], 0
    class Repository(RunRepository):
        @contextmanager
        def connection(self):
            yield Connection()
    repo = Repository(Settings())
    if still_owned:
        repo.begin_window("synthetic-run", "synthetic-owner", dict(start="synthetic-start", end="synthetic-end"), {})
    else:
        with pytest.raises(StorageError):
            repo.begin_window("synthetic-run", "synthetic-owner", {}, {})
    assert calls[1][1] == ("synthetic-run", "synthetic-owner")
    assert "status='running' AND runner_id=%s" in calls[1][0]
