"""持久任务编排：配置、定时触发、范围筛选及进度汇总；分析仍复用原运行器。"""

from datetime import datetime, timedelta
import math
import time
from uuid import uuid4

from ..config import bound, duration
from ..domain.models import TZ
from ..domain.tasks import TaskConfig, TASK_ROUND_LIMIT, eligibility
from ..jobs.runner import prepare_request
from ..repositories.runs import RunConflict
from ..repositories.mysql import _sql_time
from ..repositories.customers import SYNC_MODE, SYNC_UID, all_profiles

TERMINAL = {"succeeded", "failed"}


class TaskService:
    """只有持有现有执行锁的运行器会调用 tick，HTTP 只保存配置或提交。"""

    def __init__(self, settings, tasks, runs, customers, clock=None):
        self.settings, self.tasks, self.runs, self.customers = settings, tasks, runs, customers
        self.clock = clock or (lambda: datetime.now(TZ))
        self._last_tick = 0

    def profiles(self):
        return all_profiles(self.customers)

    def preview(self, config):
        value = TaskConfig.model_validate(config).model_dump()
        items = []
        for profile in self.profiles():
            reason = eligibility(value, profile, now=self.clock())
            if reason is None:
                items.append({"uid": profile["uid"], "level": profile.get("level"), "tam": profile.get("tam")})
        return {"items": items, "total": len(items), "notice": "按当前有效资料预览，执行前会更新过期资料并重新筛选"}

    def create(self, config):
        value = TaskConfig.model_validate(config).model_dump()
        frozen = sorted({p["uid"] for p in self.profiles()} | set(value["uids"]))
        return self.tasks.create(value, frozen)

    def update(self, task_id, config, version):
        old = self.require(task_id)
        value = TaskConfig.model_validate(config).model_dump()
        if self.tasks.executions(task_id) and any(old["config"].get(k) != value.get(k) for k in ("start", "start_mode")):
            raise ValueError("已有执行的任务不能修改首次起点，请新建任务")
        frozen = old["frozen_uids"]
        if old["config"]["include_new"] and not value["include_new"]:
            frozen = sorted({p["uid"] for p in self.profiles()} | set(value["uids"]))
        return self.tasks.update_config(task_id, value, version, frozen)

    def require(self, task_id):
        value = self.tasks.get(task_id)
        if value is None:
            raise ValueError("任务不存在")
        return value

    def action(self, task_id, action):
        task = self.require(task_id)
        if action == "pause":
            paused = self.tasks.set_enabled(task_id, False)
            for execution in self.tasks.executions(task_id, active=True):
                execution["state"]["plan_windows"] = False
                for run_id in execution["state"].get("sync_run_ids", []):
                    run = self.runs.get(run_id)
                    if run and run["status"] == "queued":
                        self.runs.cancel_queued(run_id)
                self.tasks.save_execution(execution)
            return paused
        if action == "resume":
            if task["config"]["mode"] != "automatic":
                raise ValueError("只有自动任务可以启用")
            if not task["config"].get("start"):
                config = dict(task["config"], start=self.clock().replace(second=0, microsecond=0).isoformat())
                self.tasks.update_config(task_id, config, task["version"], task["frozen_uids"])
            resumed = self.tasks.set_enabled(task_id, True)
            for execution in self.tasks.executions(task_id, active=True):
                execution["state"]["plan_windows"] = True
                self.tasks.save_execution(execution)
            return resumed
        execution = self.launch(task)
        if execution is None:
            raise ValueError("尚无完整消息窗口，请稍后执行")
        return self.tasks.get(task_id)

    def launch(self, task, automatic=False):
        config = task["config"]
        now = self.clock()
        origin = bound(config["start"]) if config.get("start") else now.replace(second=0, microsecond=0)
        start = origin
        if config["mode"] == "automatic":
            start = bound(task["cursor_at"]) if task.get("cursor_at") else origin
            interval = duration(config["interval"])
            count = int((now - start) / interval)
            if count <= 0:
                return None
            # 一次编排可覆盖已经结束的多个窗口；每个客户按自己的水位前进。
            end = min(start + count * interval, start + TASK_ROUND_LIMIT)
        else:
            end = bound(config["end"])
        # 模型和业务版本随本轮冻结，编辑配置只影响下一轮。
        template, _ = prepare_request(self.settings, {"uid": self.settings.uid, "start": start.isoformat(),
            "end": min(end, start + TASK_ROUND_LIMIT).isoformat(), "interval": config["interval"], "offline": True})
        previous = next(iter(self.tasks.executions(task["task_id"])), None)
        blocked = dict(previous["state"].get("blocked_customers", {})) if previous else {}
        cursors = dict(previous["state"].get("customer_cursors", {})) if previous else {}
        if previous:
            for child in previous["state"].get("children", []):
                if child.get("cursor"):
                    cursors[child["uid"]] = child["cursor"]
        state = dict(config=config, version=task["version"], frozen_uids=task["frozen_uids"], origin=origin.isoformat(),
            start=start.isoformat(), end=end.isoformat(), template=template, sync_run_id=None,
            window_start=start.isoformat(), window_end=min(start + duration(config["interval"]), end).isoformat(),
            published_until=start.isoformat(), released_until=start.isoformat(), plan_windows=True,
            customer_cursors=cursors, sync_run_ids=[], blocked_customers=blocked, had_errors=False,
            sync_attempts=[], children=[], error=None, source_messages=0, source_errors=0,
            isolated_count=0, phases=dict(fetch="idle", discovery="idle", analysis="idle"))
        return self.tasks.start_execution(task["task_id"], state, automatic=automatic)

    def tick(self, *, force=False):
        if not force and time.monotonic() - self._last_tick < 1:
            return
        self._last_tick = time.monotonic()
        for execution in self.tasks.executions(active=True):
            try:
                self.advance(execution)
            except RunConflict:
                continue
            except Exception:
                execution["state"]["error"] = "任务编排暂时失败，已保存结果保留；请检查服务后重试"
                self.finish(execution, "failed", advance=False)
        for task in self.tasks.list():
            if not task["enabled"] or task["config"]["mode"] != "automatic":
                continue
            if task.get("next_run_at") and bound(task["next_run_at"]) > self.clock():
                continue
            if any(e["status"] in ("queued", "running") for e in self.tasks.executions(task["task_id"])):
                continue
            try:
                self.launch(task, automatic=True)
            except RunConflict:
                pass

    def advance(self, execution):
        """推进来源发布和已经就绪的客户窗口，不等待其他客户结束。"""
        state, config = execution["state"], execution["state"]["config"]
        execution["status"] = "running"
        self._extend_horizon(state, config)
        if self._settle_source(execution):
            return
        self._open_source(execution)
        if not state["children"] and bound(state["published_until"]) > bound(state["start"]):
            self.select_customers(state)
            execution["stage"] = "analysis"
        if state["children"]:
            execution["stage"] = "analysis"
            self._drain(execution)
        self._phases(execution)
        self._maybe_finish(execution)
        if execution["status"] in ("queued", "running"):
            self.tasks.save_execution(execution)

    def _extend_horizon(self, state, config):
        if config["mode"] != "automatic" or not state.get("plan_windows", True):
            return
        interval = duration(config["interval"])
        start = bound(state["start"])
        count = int((self.clock() - start) / interval)
        if count <= 0:
            return
        horizon = min(start + count * interval, start + TASK_ROUND_LIMIT)
        if horizon > bound(state["end"]):
            state["end"] = horizon.isoformat()

    def _syncs(self, state):
        return [item for rid in state.get("sync_run_ids", []) if (item := self.runs.get(rid))]

    def _settle_source(self, execution):
        """成功的抓取才发布水位；失败按任务重试，不把缺口当成已覆盖。"""
        state, config = execution["state"], execution["state"]["config"]
        current = self.runs.get(state.get("sync_run_id")) if state.get("sync_run_id") else None
        if current is None or current["status"] in ("queued", "running"):
            return False
        if current["status"] == "failed" and current.get("error") == "已暂停，未开始的窗口不再执行":
            state["sync_run_id"] = None
            return False
        if current["status"] == "succeeded":
            state["published_until"] = state.get("sync_window_end", state["published_until"])
            for cycle in current.get("cycles") or []:
                state["source_messages"] += cycle.get("messages", 0)
                state["source_errors"] += cycle.get("profile_errors", 0) + cycle.get("lookup_errors", 0)
                state["isolated_count"] += cycle.get("isolated", 0)
            state["sync_attempts"] = []
            state["sync_run_id"] = None
            state.pop("retry_at", None)
            return False
        state["had_errors"] = True
        state["sync_attempts"].append(current["run_id"])
        error = current.get("error") or "取数失败"
        retryable = not any(word in error for word in ("未确认", "不完整", "截断"))
        if len(state["sync_attempts"]) <= config["retries"] and retryable:
            state["retry_at"] = (self.clock() + duration(config["retry_delay"])).isoformat()
            state["sync_run_id"] = None
            return False
        state["error"] = error
        self.finish(execution, "failed", advance=False)
        return True

    def _open_source(self, execution):
        """全局分钟窗口只排一个抓取任务，领先最慢客户有限个窗口。"""
        state, config = execution["state"], execution["state"]["config"]
        if any(item["status"] in ("queued", "running") for item in self._syncs(state)):
            return
        if state.get("retry_at") and bound(state["retry_at"]) > self.clock():
            return
        published = bound(state["published_until"])
        if not state.get("plan_windows", True) and published > bound(state["start"]):
            return
        interval = duration(config["interval"])
        ahead = interval * int(getattr(self.settings, "fetch_ahead", 2))
        active = [bound(child.get("cursor") or state["start"]) for child in state["children"]
                  if child["status"] not in ("failed", "skipped")]
        slowest = min(active) if active else published
        target = min(bound(state["end"]), slowest + ahead)
        if published >= target or published >= bound(state["end"]):
            return
        window_end = min(published + interval, bound(state["end"]))
        lower = max(bound(state["origin"]), published - duration(config["lookback"]))
        request = dict(mode=SYNC_MODE, uid=SYNC_UID, start=lower.isoformat(), end=window_end.isoformat(),
            interval=config["interval"], offline=False, task_options=config)
        run_id = str(uuid4())
        total = max(1, math.ceil((window_end - lower) / interval))
        self.runs.create(request, total, run_id=run_id)
        state["sync_run_id"] = run_id
        state["sync_window_end"] = window_end.isoformat()
        state["released_until"] = window_end.isoformat()
        state["sync_run_ids"].append(run_id)
        execution["stage"] = "discovery" if not state["children"] else execution["stage"]

    def _drain(self, execution):
        """一个客户完成后立刻排它自己的下一窗，不等待同轮其他客户。"""
        state, config = execution["state"], execution["state"]["config"]
        published = bound(state["published_until"])
        horizon = bound(state["end"])
        interval = duration(config["interval"])
        for child in state["children"]:
            if child["status"] in ("failed", "skipped", "succeeded"):
                continue
            if child.get("retry_at") and bound(child["retry_at"]) > self.clock():
                child["status"] = "queued"
                continue
            run = self.runs.get(child["run_id"]) if child.get("run_id") else None
            if run and run["status"] in ("queued", "running"):
                child["status"] = run["status"]
                continue
            if run and run["status"] == "failed":
                self._fail_or_retry(execution, child, run)
                continue
            if run and run["status"] == "succeeded":
                child["cursor"] = run["request"]["end"]
                state.setdefault("customer_cursors", {})[child["uid"]] = child["cursor"]
                child["run_id"] = None
                child.pop("retry_at", None)
            cursor = bound(child["cursor"])
            limit = published if not state.get("plan_windows", True) else min(published, horizon)
            if cursor >= horizon or (not state.get("plan_windows", True) and cursor >= published):
                child["status"] = "succeeded"
                state.setdefault("customer_cursors", {})[child["uid"]] = child["cursor"]
                continue
            if cursor >= limit:
                child["status"] = "queued"
                continue
            window_end = min(cursor + interval, limit)
            pending = self._pending(state, child, cursor, window_end)
            if not pending:
                child["cursor"] = window_end.isoformat()
                state.setdefault("customer_cursors", {})[child["uid"]] = child["cursor"]
                child["status"] = "queued"
                continue
            child["run_id"] = str(uuid4())
            child.setdefault("run_ids", []).append(child["run_id"])
            phase = "live" if cursor >= bound(child.get("catchup_target") or state["end"]) else "catchup"
            request = dict(state["template"], uid=child["uid"], start=cursor.isoformat(), end=window_end.isoformat(),
                data_start=state["origin"], message_source="stored", allowed_groups=child["allowed_groups"],
                strict_window=True, phase=phase, catchup_target=child.get("catchup_target"))
            try:
                self.runs.create(request, 1, run_id=child["run_id"])
            except RunConflict:
                child["run_ids"].pop()
                child["run_id"] = None
                child["status"] = "queued"
                continue
            child["status"] = "queued"

    def _pending(self, state, child, start, end):
        with self.runs.connection() as store:
            return [message for message in store.pending_messages(child["uid"], start, end, state["template"]["analysis_stream"])
                    if getattr(message, "conversation_id", None) in child["allowed_groups"]]

    def _fail_or_retry(self, execution, child, run):
        state = execution["state"]
        error = run.get("error") or "处理失败"
        child["retries_used"] = child.get("retries_used", 0) + 1
        blocked = any(word in error for word in ("未确认", "已提交", "核实", "策略已变化", "合约已变化", "倒灌", "执行权", "模型结果未知"))
        if child["retries_used"] <= state["config"]["retries"] and not blocked:
            child["status"] = "queued"
            child["run_id"] = None
            child["retry_at"] = (self.clock() + duration(state["config"]["retry_delay"])).isoformat()
            return
        child["status"] = "failed"
        child["reason"] = error
        state["had_errors"] = True
        # 普通失败停在本轮未完成窗口，下一轮从该水位重试；只有核实、倒灌等明确阻断才跨轮保留。
        if blocked:
            state.setdefault("blocked_customers", {})[child["uid"]] = dict(
                run_id=run["run_id"], reason=error, stream=state["template"]["analysis_stream"])

    def _maybe_finish(self, execution):
        state = execution["state"]
        if execution["status"] not in ("queued", "running") or not state["children"]:
            return
        if any(item["status"] in ("queued", "running") for item in self._syncs(state)):
            return
        if any(child["status"] in ("queued", "running") for child in state["children"]):
            return
        paused = not state.get("plan_windows", True)
        reached = bound(state["published_until"]) >= bound(state["end"])
        if not paused and not reached:
            return
        self.finish(execution, "partial" if state.get("had_errors") or any(
            child["status"] == "failed" for child in state["children"]) else "succeeded", advance=not paused and reached)

    def _phases(self, execution):
        state = execution["state"]
        syncing = any(item["status"] in ("queued", "running") for item in self._syncs(state))
        analyzing = any(child["status"] in ("queued", "running") for child in state["children"])
        state["phases"] = dict(
            fetch="running" if syncing else "done" if bound(state.get("published_until", state["start"])) > bound(state["start"]) else "idle",
            discovery="running" if execution["stage"] == "discovery" and not state["children"] else "done",
            analysis="running" if analyzing else "idle")

    def select_customers(self, state):
        config, children = state["config"], []
        profiles = self.profiles()
        known = {p["uid"] for p in profiles}
        profiles += [{"uid": uid, "level": None} for uid in config["uids"] if uid not in known]
        for profile in profiles:
            uid = profile["uid"]
            cursor = bound(state.get("customer_cursors", {}).get(uid, state.get("window_start", state["start"])))
            end = bound(state["end"])
            reason = eligibility(config, profile, state["frozen_uids"], self.clock())
            blocked = state.get("blocked_customers", {}).get(uid)
            if blocked:
                prior_run = self.runs.get(blocked["run_id"], internal=True) if blocked.get("run_id") else None
                if prior_run and prior_run.get("recovered_by"):
                    state["blocked_customers"].pop(uid, None)
                else:
                    children.append(dict(uid=uid, level=profile.get("level"), status="failed", reason=blocked.get("reason"),
                        allowed_groups=[], cursor=cursor.isoformat(), run_id=blocked.get("run_id"), run_ids=[],
                        retries_used=0, catchup_target=state["end"], late_withheld=False))
                    continue
            groups = self.customers.list_groups(uid=uid, limit=10000)["items"] if not reason else []
            allowed = [g["group_id"] for g in groups if g["status"] == "confirmed" and g["group_id"] not in config["excluded_groups"]]
            if not reason and not allowed:
                reason = "没有已确认且未排除的关联群"
            late_withheld = False
            if not reason and config["operation"] == "analyze":
                with self.runs.connection() as store:
                    pending = [m for m in store.pending_messages(uid, bound(state["origin"]), end,
                        state["template"]["analysis_stream"]) if m.conversation_id in allowed]
                    if not pending:
                        reason = "没有待处理的新消息"
                    else:
                        origin, interval = bound(state["origin"]), duration(config["interval"])
                        earliest = origin + ((min(m.sent_at for m in pending) - origin) // interval) * interval
                        if earliest < cursor:
                            events = store.list_events(uid, state["template"]["analysis_stream"])
                            latest = max((bound(r["window_end"]) for e in events for r in e.updated_by), default=origin)
                            if latest > end:
                                reason = "等待当前窗口追平已有事件进度"
                            elif latest <= earliest:
                                cursor = earliest
                            else:
                                late_withheld = True
            if reason:
                cursor = end
            children.append(dict(uid=uid, level=profile.get("level"), status="skipped" if reason else
                "succeeded" if config["operation"] == "sync" else "queued", reason=reason,
                allowed_groups=allowed, cursor=cursor.isoformat(), run_id=None, run_ids=[], retries_used=0,
                catchup_target=end.isoformat(), late_withheld=late_withheld))
            state.setdefault("customer_cursors", {})[uid] = cursor.isoformat()
        state["children"] = children
        return children

    def finish(self, execution, status, *, advance):
        execution["status"] = status
        execution["stage"] = "completed"
        state = execution["state"]
        config = state["config"]
        end = bound(state["end"])
        published = bound(state.get("published_until", state["start"]))
        point = end if advance else published
        next_at = self.clock() + duration(config["frequency"])
        if advance and end + duration(config["interval"]) <= self.clock():
            next_at = self.clock()
        cursor = _sql_time(point) if advance or point > bound(state["start"]) else None
        self.tasks.save_execution(execution, cursor=cursor, next_at=_sql_time(next_at))

    def progress(self, execution):
        state = execution["state"]
        customers, created, updated, processed = [], set(), set(), 0
        waiting = []
        for child in state["children"]:
            item = {k: child.get(k) for k in ("uid", "level", "status", "reason", "run_id")}
            item["run_id"] = child.get("run_id") or next(iter(reversed(child.get("run_ids") or [])), None)
            item["stream"] = state["template"]["analysis_stream"]
            item.update(processed=0, created=0, updated=0, completed_windows=0, total_windows=0,
                phase="live" if child.get("cursor") and bound(child["cursor"]) >= bound(child.get("catchup_target") or state["end"]) else "catchup",
                incomplete=bool(child.get("late_withheld")), withheld=1 if child.get("late_withheld") else 0,
                analyzed_until=child.get("cursor"), duration_seconds=None)
            own_created, own_updated = set(), set()
            for run_id in child.get("run_ids") or []:
                run = self.runs.get(run_id)
                if not run:
                    continue
                item["completed_windows"] += run["completed_windows"]
                item["total_windows"] += run["total_windows"]
                if run_id == child.get("run_id") and item["status"] == "queued":
                    item["status"] = run["status"] if run["status"] in ("queued", "running") else "queued"
                if run["status"] == "queued" and run.get("created_at"):
                    waiting.append(bound(run["created_at"]))
                for cycle in run["cycles"]:
                    item["processed"] += cycle.get("processed", 0)
                    own_created.update(cycle.get("created", []))
                    own_updated.update(cycle.get("updated", []))
                    item["withheld"] += sum(entry.get("count", 0) for entry in cycle.get("filter_audit", [])
                                            if isinstance(entry, dict) and entry.get("kind") == "withheld")
                    item["incomplete"] = item["incomplete"] or any(
                        isinstance(entry, dict) and entry.get("incomplete") for entry in cycle.get("filter_audit", []))
            item["created"], item["updated"] = len(own_created), len(own_updated)
            processed += item["processed"]
            created.update(own_created)
            updated.update(own_updated)
            customers.append(item)
        syncs = self._syncs(state)
        slots = int(getattr(self.settings, "analysis_slots", 2))
        oldest = int((self.clock() - min(waiting)).total_seconds()) if waiting else None
        return dict(execution_id=execution["execution_id"], task_id=execution["task_id"], status=execution["status"],
            stage=execution["stage"], start=state["start"], end=state["end"], created_at=execution["created_at"],
            finished_at=execution["finished_at"], config=state["config"], error=state.get("error"),
            source_messages=state["source_messages"], source_errors=state["source_errors"], processed=processed,
            created_events=len(created), updated_events=len(updated),
            sync_completed=sum(s["completed_windows"] for s in syncs),
            sync_total=sum(s["total_windows"] for s in syncs),
            customers=customers, backlog=len(waiting), oldest_wait_seconds=oldest,
            capacity_used=sum(child["status"] == "running" for child in state["children"]), capacity_total=slots,
            isolated_count=state.get("isolated_count", 0), phases=state.get("phases") or {},
            capacity_notice="共享并发额度仍会排队，不保证五分钟内完成。")
