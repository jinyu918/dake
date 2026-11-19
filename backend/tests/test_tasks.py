"""任务中心使用合成资料和内存队列验证调度，不连接外部模型或官方接口。"""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dake.config import Settings, bound
from dake.domain.tasks import TaskConfig, eligibility
from dake.repositories.runs import RunConflict
from dake.services.tasks import TaskService

NOW = bound("2026-11-16T10:30:00+08:00")


class MemoryTasks:
    def __init__(self):
        self.items, self.history = {}, {}

    def init_schema(self):
        pass

    def create(self, config, frozen):
        tid = str(uuid4())
        self.items[tid] = dict(task_id=tid, config=deepcopy(config), frozen_uids=frozen, enabled=False,
            version=1, cursor_at=None, next_run_at=None, created_at=NOW.isoformat(), updated_at=NOW.isoformat())
        return self.get(tid)

    def get(self, tid):
        return deepcopy(self.items.get(tid))

    def list(self):
        return deepcopy(list(self.items.values()))

    def update_config(self, tid, config, version, frozen):
        if version != self.items[tid]["version"]:
            raise RunConflict()
        self.items[tid].update(config=deepcopy(config), version=version + 1, frozen_uids=frozen)
        return self.get(tid)

    def set_enabled(self, tid, enabled):
        self.items[tid].update(enabled=enabled, next_run_at=None)
        return self.get(tid)

    def executions(self, tid=None, active=False):
        return deepcopy([e for e in reversed(list(self.history.values())) if (tid is None or tid == e["task_id"])
            and (not active or e["status"] in ("queued", "running"))])

    def execution(self, eid):
        return deepcopy(self.history.get(eid))

    def execution_heads(self, tid, *, limit=100):
        return [{k: v for k, v in row.items() if k != "state"} for row in self.executions(tid)[:limit]]

    def start_execution(self, tid, state, automatic=False):
        if self.executions(tid, active=True):
            raise RunConflict()
        eid = str(uuid4())
        self.history[eid] = dict(execution_id=eid, task_id=tid, status="queued", stage="discovery",
            state=deepcopy(state), created_at=NOW.isoformat(), finished_at=None)
        self.items[tid]["cursor_at"] = self.items[tid]["cursor_at"] or state["start"]
        return self.execution(eid)

    def save_execution(self, execution, cursor=None, next_at=None):
        self.history[execution["execution_id"]] = deepcopy(execution)
        if cursor:
            self.items[execution["task_id"]]["cursor_at"] = cursor.isoformat() + "+08:00"
        if next_at:
            self.items[execution["task_id"]]["next_run_at"] = next_at.isoformat() + "+08:00"


class MemoryRuns:
    def __init__(self):
        self.items = {}
        self.busy = set()

    def get(self, rid, **kwargs):
        return deepcopy(self.items.get(rid))

    def progress_rows(self, run_ids, *, include_cycles=False):
        return {rid: deepcopy(self.items[rid]) for rid in run_ids if rid in self.items}

    def working_ids(self, run_ids):
        return {rid for rid in run_ids if self.items[rid]["status"] == "running"}

    def create(self, request, total, run_id=None):
        if run_id in self.items:
            return self.get(run_id)
        if request["uid"] in self.busy:
            raise RunConflict()
        self.items[run_id] = dict(run_id=run_id, uid=request["uid"], request=request, status="queued",
            total_windows=total, completed_windows=0, cycles=[], error=None)
        return self.get(run_id)

    @contextmanager
    def connection(self):
        yield self

    def pending_messages(self, *args):
        return [SimpleNamespace(conversation_id="synthetic-group", sent_at=bound("2026-11-16T10:01:00+08:00"))]

    def list_events(self, *args):
        return []

    def finish(self, rid, error=None):
        self.items[rid].update(status="failed" if error else "succeeded", error=error,
            completed_windows=self.items[rid]["total_windows"])

    def cancel_queued(self, rid):
        if self.items[rid]["status"] == "queued":
            self.items[rid].update(status="failed", error="已暂停，未开始的窗口不再执行")


class Customers:
    def __init__(self):
        self.items = [dict(uid=uid, level=level, level_state=state, level_synced_at=NOW.isoformat(), tam=None)
            for uid, level, state in (("555001", "L6", "fresh"), ("555002", "L10", "fresh"),
                                     ("555003", "L5", "fresh"), ("555004", "L9", "stale"))]

    def list_customers(self, **kwargs):
        return dict(items=deepcopy(self.items), total=len(self.items))

    def list_groups(self, **kwargs):
        return dict(items=[dict(group_id="synthetic-group", status="confirmed")], total=1)


@pytest.fixture
def service(tmp_path):
    return TaskService(replace(Settings(), data_dir=tmp_path), MemoryTasks(), MemoryRuns(), Customers(), clock=lambda: NOW)


def config(**changes):
    return dict(name="合成任务", start="2026-11-16T10:00:00+08:00", end=NOW.isoformat(), retries=0, **changes)


def sync_done(service, eid):
    service.tick(force=True)
    execution = service.tasks.execution(eid)
    service.runs.finish(execution["state"]["sync_run_id"])
    service.tick(force=True)
    return service.tasks.execution(eid)


def test_waiting_and_working_match_the_worker_in_detail_and_list(service):
    task = service.create(config())
    service.action(task["task_id"], "run")
    eid = next(iter(service.tasks.history))
    execution = sync_done(service, eid)
    child = next(c for c in execution["state"]["children"] if c.get("run_id"))
    child["status"] = "running"
    previous = service.progress(execution)
    assert previous["status"] == "queued" and previous["capacity_used"] == 0
    assert previous["phases"]["analysis"] == "idle"
    assert next(c for c in previous["customers"] if c["uid"] == child["uid"])["status"] == "queued"
    service.runs.items[child["run_id"]]["status"] = "running"
    service.tick(force=True)
    execution = service.tasks.execution(eid)
    working = service.progress(execution)
    assert execution["status"] == working["status"] == "running"
    assert working["capacity_used"] == 1 and working["phases"]["analysis"] == "running"


def test_caught_up_customer_continues_after_automatic_horizon_extends(service):
    task = service.create(dict(config(), mode="automatic", end=None))
    service.action(task["task_id"], "resume")
    service.tick(force=True)
    eid = next(iter(service.tasks.history))
    execution = sync_done(service, eid)
    state = execution["state"]
    child = next(c for c in state["children"] if c.get("run_id"))
    service.runs.finish(child["run_id"])
    child.update(status="succeeded", run_id=None, cursor=state["end"])
    old_end = state["end"]
    service.clock = lambda: NOW + timedelta(minutes=15)
    service._extend_horizon(state, state["config"])
    state["published_until"] = state["end"]
    service._drain(execution)
    next_run = service.runs.get(child["run_id"])
    assert next_run["request"]["start"] == old_end
    assert next_run["request"]["end"] == state["end"]


def test_outdated_policy_finishes_round_without_blocking_next_round(service):
    task = service.create(config())
    service.action(task["task_id"], "run")
    eid = next(iter(service.tasks.history))
    execution = sync_done(service, eid)
    state = execution["state"]
    for run in list(service.runs.items.values()):
        if run["status"] == "queued":
            service.runs.finish(run["run_id"], error=None if run["request"]["mode"] == "customer_sync"
                                else "增量归属策略已变化，请重新提交任务")
    service.tick(force=True)
    execution = service.tasks.execution(eid)
    assert execution["status"] == "partial"
    assert "分析规则已更新" in execution["state"]["error"]
    assert not execution["state"]["blocked_customers"]
    assert service.launch(task)["state"]["customer_cursors"] == execution["state"]["customer_cursors"]


def test_save_does_not_launch_and_preview_matches_exact_rule(service):
    task = service.create(config())
    assert not task["enabled"] and not service.tasks.history and not service.runs.items
    assert [p["uid"] for p in service.preview(config())["items"]] == ["555001", "555002"]
    assert service.preview(config(scope="selected", uids=["555003"]))["total"] == 0
    assert service.preview(config(excluded_uids=["555001"]))["total"] == 1
    old = deepcopy(task["config"])
    service.update(task["task_id"], dict(config(), name="修改合成任务"), 1)
    with pytest.raises(RunConflict): service.update(task["task_id"], old, 1)


@pytest.mark.parametrize("change", [dict(levels=["L11"]), dict(name="  "), dict(scope="selected", uids=[]),
    dict(frequency="0m"), dict(retries=4), dict(end="2026-11-16T09:00:00+08:00")])
def test_invalid_config_rejected(change):
    with pytest.raises(ValueError): TaskConfig.model_validate(dict(config(), **change))


def test_manual_snapshot_children_and_resume_after_coordinator_restart(service):
    task = service.create(config())
    service.action(task["task_id"], "run")
    eid = next(iter(service.tasks.history))
    with pytest.raises(RunConflict): service.action(task["task_id"], "run")
    execution = sync_done(service, eid)
    selected = [c for c in execution["state"]["children"] if c["status"] != "skipped"]
    assert [c["uid"] for c in selected] == ["555001", "555002"]
    run_ids = [c["run_id"] for c in selected]
    before = set(service.runs.items)
    replacement = TaskService(service.settings, service.tasks, service.runs, service.customers, clock=lambda: NOW)
    replacement.tick(force=True)
    assert set(service.runs.items) == before
    for rid in run_ids:
        assert service.runs.items[rid]["request"]["message_source"] == "stored"
        service.runs.finish(rid)
    replacement.tick(force=True)
    while service.tasks.execution(eid)["status"] in ("queued", "running"):
        for rid, run in list(service.runs.items.items()):
            if run["status"] == "queued":
                service.runs.finish(rid)
        replacement.tick(force=True)
    final = service.tasks.execution(eid)
    assert final["status"] == "succeeded"
    assert service.tasks.get(task["task_id"])["cursor_at"] == NOW.isoformat()
    assert replacement.progress(final)["customers"][0]["status"] == "succeeded"


def test_pause_blocks_new_round_but_preserves_current_work(service):
    task = service.create(config(mode="automatic"))
    service.action(task["task_id"], "resume")
    service.tick(force=True)
    eid = next(iter(service.tasks.history))
    service.action(task["task_id"], "pause")
    execution = sync_done(service, eid)
    for child in execution["state"]["children"]:
        if child["run_id"]: service.runs.finish(child["run_id"])
    service.tick(force=True)
    service.clock = lambda: NOW + timedelta(hours=1)
    service.tick(force=True)
    assert len(service.tasks.history) == 1
    assert service.tasks.execution(eid)["status"] == "succeeded"


def test_busy_customer_does_not_block_other_customers_and_failed_source_does_not_advance(service):
    task = service.create(config())
    service.action(task["task_id"], "run")
    eid = next(iter(service.tasks.history))
    service.runs.busy.add("555001")
    execution = sync_done(service, eid)
    second = next(c for c in execution["state"]["children"] if c["uid"] == "555002")
    assert service.runs.get(second["run_id"]) is not None
    service.runs.busy.clear()
    service.tick(force=True)
    first = service.tasks.execution(eid)["state"]["children"][0]
    assert service.runs.get(first["run_id"]) is not None


def test_unknown_transaction_never_automatically_retries(service):
    task = service.create(dict(config(), retries=3))
    service.action(task["task_id"], "run")
    eid = next(iter(service.tasks.history))
    execution = sync_done(service, eid)
    first = execution["state"]["children"][0]
    service.runs.finish(first["run_id"], "事务结果未确认，请核实")
    service.tick(force=True)
    child = service.tasks.execution(eid)["state"]["children"][0]
    assert child["status"] == "failed" and len(child["run_ids"]) == 1


def test_new_customer_policy_and_profile_expiration(service):
    value = TaskConfig.model_validate(config(include_new=False)).model_dump()
    profile = service.customers.items[0]
    assert eligibility(value, profile, [], NOW) == "未启用自动纳入新客户"
    assert eligibility(value, profile, [profile["uid"]], NOW + timedelta(hours=2)) == "等级待确认或资料已过期"


def automatic(service, **changes):
    task = service.create(dict(config(mode="automatic", interval="15m"), **changes))
    service.action(task["task_id"], "resume")
    service.tick(force=True)
    return task, next(iter(service.tasks.history))


def finish_customers(service, eid, failed_uid=None, error="合成网络失败"):
    for child in service.tasks.execution(eid)["state"]["children"]:
        if child["run_id"]:
            service.runs.finish(child["run_id"], error if child["uid"] == failed_uid else None)
    service.tick(force=True)


def test_timer_keeps_customer_windows_ordered_and_pause_stops_the_next_one(service):
    task, eid = automatic(service)
    execution = sync_done(service, eid)
    assert execution["state"]["end"] == NOW.isoformat()
    analysis = [run for run in service.runs.items.values() if run["request"]["mode"] != "customer_sync"]
    assert analysis and all(run["request"]["start"] == "2026-11-16T10:00:00+08:00" for run in analysis)
    service.action(task["task_id"], "pause")
    finish_customers(service, eid)
    for _ in range(6):
        for run in list(service.runs.items.values()):
            if run["status"] == "queued":
                service.runs.finish(run["run_id"])
        service.tick(force=True)
        if service.tasks.execution(eid)["status"] not in ("queued", "running"):
            break
    assert service.tasks.execution(eid)["status"] == "succeeded"
    assert service.tasks.get(task["task_id"])["cursor_at"] == "2026-11-16T10:15:00+08:00"
    assert len(service.tasks.history) == 1
    service.action(task["task_id"], "resume")
    service.tick(force=True)
    latest = service.tasks.executions(task["task_id"])[0]
    assert latest["state"]["start"] == "2026-11-16T10:15:00+08:00"


def test_fast_customer_gets_the_next_window_while_another_is_still_running(service):
    service.create(config(interval="15m"))
    task = service.tasks.list()[0]
    service.action(task["task_id"], "run")
    eid = next(iter(service.tasks.history))
    execution = sync_done(service, eid)
    assert execution["stage"] == "analysis"
    first = [run for run in service.runs.items.values() if run["request"]["mode"] != "customer_sync"]
    assert first and all(run["request"]["end"] == "2026-11-16T10:15:00+08:00" for run in first)
    for run in list(service.runs.items.values()):
        if run["request"]["mode"] == "customer_sync" and run["status"] == "queued":
            service.runs.finish(run["run_id"])
    service.tick(force=True)
    children = {child["uid"]: child for child in service.tasks.execution(eid)["state"]["children"]}
    service.runs.finish(children["555002"]["run_id"])
    service.tick(force=True)
    second = [run for run in service.runs.items.values()
              if run["uid"] == "555002" and run["request"]["start"] == "2026-11-16T10:15:00+08:00"]
    assert second
    assert any(run["uid"] == "555001" and run["status"] == "queued" and run["request"]["start"] == "2026-11-16T10:00:00+08:00"
               for run in service.runs.items.values())


def test_failed_customer_stays_on_the_unfinished_window_while_others_advance(service):
    task, eid = automatic(service)
    sync_done(service, eid)
    for run in list(service.runs.items.values()):
        if run["request"]["mode"] == "customer_sync" and run["status"] == "queued":
            service.runs.finish(run["run_id"])
    service.tick(force=True)
    finish_customers(service, eid, failed_uid="555001")
    state = service.tasks.execution(eid)["state"]
    children = {child["uid"]: child for child in state["children"]}
    assert children["555001"]["status"] == "failed"
    assert children["555001"]["cursor"] == "2026-11-16T10:00:00+08:00"
    other = service.runs.get(children["555002"]["run_id"])
    assert other["request"]["start"] == "2026-11-16T10:15:00+08:00"
    assert task["task_id"]


def test_late_message_uses_current_window_if_event_already_progressed(service):
    state = dict(config=TaskConfig.model_validate(config(interval="15m")).model_dump(),
        frozen_uids=[], origin="2026-11-16T10:00:00+08:00", start="2026-11-16T10:15:00+08:00",
        end=NOW.isoformat(), template={"analysis_stream": "stub-v1"})
    service.runs.list_events = lambda *args: [SimpleNamespace(updated_by=[{"window_end": "2026-11-16T10:15:00+08:00"}])]
    assert service.select_customers(state)[0]["cursor"] == state["start"]


def test_empty_window_uses_persistent_job_and_waits_when_caught_up(service):
    service.runs.pending_messages = lambda *args: []
    task, eid = automatic(service, start="2026-11-16T10:15:00+08:00")
    sync_done(service, eid)
    jobs = [r for r in service.runs.items.values() if r["request"]["mode"] == "official"]
    assert jobs and all(r["request"]["strict_window"] for r in jobs)
    for job in jobs:
        service.runs.finish(job["run_id"])
    service.tick(force=True)
    assert service.tasks.execution(eid)["status"] == "succeeded"
    assert service.tasks.get(task["task_id"])["cursor_at"] == NOW.isoformat()
    service.tick(force=True)
    assert len(service.tasks.history) == 1
    service.clock = lambda: NOW + timedelta(minutes=15)
    service.tick(force=True)
    assert len(service.tasks.history) == 2


def test_initial_empty_window_does_not_skip_later_messages(service):
    """首窗为空仍保留客户，后续来源窗口出现消息时继续按序排入分析。"""
    service.runs.pending_messages = lambda *args: []
    _, eid = automatic(service, scope="selected", uids=["555001"])
    state = sync_done(service, eid)["state"]
    child = next(c for c in state["children"] if c["uid"] == "555001")
    first = service.runs.get(child["run_id"])
    assert first and first["request"]["start"] == "2026-11-16T10:00:00+08:00"
    service.runs.finish(first["run_id"])
    service.runs.finish(state["sync_run_id"])
    service.runs.pending_messages = lambda *args: [SimpleNamespace(
        conversation_id="synthetic-group", sent_at=bound("2026-11-16T10:16:00+08:00"))]
    service.tick(force=True)
    child = next(c for c in service.tasks.execution(eid)["state"]["children"] if c["uid"] == "555001")
    second = service.runs.get(child["run_id"])
    assert second and second["request"]["start"] == "2026-11-16T10:15:00+08:00"


def test_source_failure_resumes_after_already_completed_customer_window(service):
    """来源先失败时仍核对成功子任务，重启编排不会重复提交已完成的分析窗口。"""
    task, eid = automatic(service, scope="selected", uids=["555001"])
    state = sync_done(service, eid)["state"]
    child = next(c for c in state["children"] if c["uid"] == "555001")
    service.runs.finish(child["run_id"])
    service.runs.finish(state["sync_run_id"], "合成取数失败")
    service.tick(force=True)
    assert service.tasks.execution(eid)["status"] == "failed"
    service.runs.pending_messages = lambda *args: []
    service.action(task["task_id"], "run")
    latest = service.tasks.executions(task["task_id"])[0]
    assert latest["state"]["customer_cursors"]["555001"] == "2026-11-16T10:15:00+08:00"


def test_unconfirmed_transaction_remains_blocked_after_restart_and_next_timer(service):
    task, eid = automatic(service)
    sync_done(service, eid)
    finish_customers(service, eid, "555001", "事务结果未确认，请核实")
    replacement = TaskService(service.settings, service.tasks, service.runs, service.customers, clock=lambda: NOW)
    next_id = service.tasks.executions(task["task_id"])[0]["execution_id"]
    before = len([r for r in service.runs.items.values() if r["uid"] == "555001"])
    latest = sync_done(replacement, next_id)
    child = next(c for c in latest["state"]["children"] if c["uid"] == "555001")
    assert child["status"] == "failed" and "未确认" in child["reason"]
    assert len([r for r in service.runs.items.values() if r["uid"] == "555001"]) == before


def test_all_customer_scope_pages_beyond_500_and_includes_new_customer(service):
    profiles = [dict(service.customers.items[0], uid=str(600000 + n)) for n in range(503)]
    calls = []
    def listing(offset=0, limit=50, **kwargs):
        calls.append(offset)
        return {"total": len(profiles), "items": profiles[offset:offset + limit]}
    service.customers.list_customers = listing
    assert service.preview(config())["total"] == 503
    assert calls == [0, 500]
    task = service.create(config())
    profiles.append(dict(profiles[0], uid="700001"))
    service.action(task["task_id"], "run")
    eid = next(iter(service.tasks.history))
    execution = sync_done(service, eid)
    assert any(c["uid"] == "700001" and c["status"] == "queued" for c in execution["state"]["children"])


def test_stale_cursor_reconciles_with_committed_pipeline_state_without_backflow_failure(service):
    """重启后若数据库权威水位已领先任务游标，调度器应自动追平游标而非停留在防倒灌假失败。"""
    service.runs.pending_messages = lambda *args: []
    pipeline_rows = {
        "555001": dict(
            uid="555001",
            active_stream="stub-v1",
            analyzed_until=bound("2026-11-16T10:15:00+08:00").replace(tzinfo=None),
            block_reason=None,
        )
    }

    def fake_execute(sql, args=()):
        if "FROM uid_pipeline_state" in sql:
            return [pipeline_rows[uid] for uid in args if uid in pipeline_rows], len(args)
        return [], 0

    service.runs._execute = fake_execute
    _, eid = automatic(service, scope="selected", uids=["555001"])
    execution = sync_done(service, eid)
    child = next(c for c in execution["state"]["children"] if c["uid"] == "555001")
    # 首窗 [10:00 -> 10:15) 已在 uid_pipeline_state 原子提交，不应再排 10:00 的重复任务。
    assert child["cursor"] == "2026-11-16T10:15:00+08:00"
    assert not any(
        r["uid"] == "555001" and r["request"]["start"] == "2026-11-16T10:00:00+08:00"
        for r in service.runs.items.values()
    )

    # 模拟此前因重启游标滞后被误标为防倒灌失败的子任务，数据库已推进至 10:30 时应自动恢复为 succeeded。
    execution["state"]["published_until"] = NOW.isoformat()
    for c in execution["state"]["children"]:
        if c["uid"] == "555001":
            c.update(status="failed", reason="已进入实时或已有更晚结果，旧历史不能倒灌主流", cursor="2026-11-16T10:15:00+08:00", run_id=None)
    execution["state"]["blocked_customers"]["555001"] = dict(run_id="stale-run", reason="已进入实时或已有更晚结果，旧历史不能倒灌主流", stream="stub-v1")
    service.tasks.save_execution(execution)
    pipeline_rows["555001"]["analyzed_until"] = NOW.replace(tzinfo=None)

    service.tick(force=True)
    recovered = next(c for c in service.tasks.execution(eid)["state"]["children"] if c["uid"] == "555001")
    assert recovered["status"] == "succeeded"
    assert recovered["cursor"] == NOW.isoformat()
    assert "555001" not in service.tasks.execution(eid)["state"]["blocked_customers"]
