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
            return self.tasks.set_enabled(task_id, False)
        if action == "resume":
            if task["config"]["mode"] != "automatic":
                raise ValueError("只有自动任务可以启用")
            if not task["config"].get("start"):
                config = dict(task["config"], start=self.clock().replace(second=0, microsecond=0).isoformat())
                self.tasks.update_config(task_id, config, task["version"], task["frozen_uids"])
            return self.tasks.set_enabled(task_id, True)
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
            # 自动执行一次只推进一个窗口，暂停在该窗口完成后生效。
            end = start + interval
        else:
            end = bound(config["end"])
        # 模型和业务版本随本轮冻结，编辑配置只影响下一轮。
        template, _ = prepare_request(self.settings, {"uid": self.settings.uid, "start": start.isoformat(),
            "end": min(end, start + TASK_ROUND_LIMIT).isoformat(), "interval": config["interval"], "offline": True})
        previous = next(iter(self.tasks.executions(task["task_id"])), None)
        blocked = dict(previous["state"].get("blocked_customers", {})) if previous else {}
        state = dict(config=config, version=task["version"], frozen_uids=task["frozen_uids"], origin=origin.isoformat(),
            start=start.isoformat(), end=end.isoformat(), template=template, sync_run_id=str(uuid4()),
            window_start=start.isoformat(), window_end=min(start + duration(config["interval"]), end).isoformat(),
            sync_run_ids=[], blocked_customers=blocked, had_errors=False,
            sync_attempts=[], children=[], error=None, source_messages=0, source_errors=0)
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
        state, config = execution["state"], execution["state"]["config"]
        window_start = bound(state.get("window_start", state["start"]))
        window_end = bound(state.get("window_end", state["end"]))
        execution["status"] = "running"
        if execution["stage"] == "discovery":
            sync = self.runs.get(state["sync_run_id"])
            if sync is None:
                if state.get("retry_at") and bound(state["retry_at"]) > self.clock():
                    return
                lower = max(bound(state["origin"]), window_start - duration(config["lookback"]))
                request = dict(mode=SYNC_MODE, uid=SYNC_UID, start=lower.isoformat(), end=window_end.isoformat(),
                    interval=config["interval"], offline=False, task_options=config)
                total = math.ceil((window_end - lower) / duration(config["interval"]))
                self.runs.create(request, total, run_id=state["sync_run_id"])
                if state["sync_run_id"] not in state.setdefault("sync_run_ids", []):
                    state["sync_run_ids"].append(state["sync_run_id"])
                self.tasks.save_execution(execution)
                return
            if sync["status"] not in TERMINAL:
                return
            if sync["completed_windows"] != sync["total_windows"]:
                if len(state["sync_attempts"]) < config["retries"]:
                    state["sync_attempts"].append(state["sync_run_id"])
                    state["sync_run_id"] = str(uuid4())
                    state["retry_at"] = (self.clock() + duration(config["retry_delay"])).isoformat()
                    self.tasks.save_execution(execution)
                    return
                state["error"] = "聊天同步未完成，未推进数据范围；可重新执行"
                return self.finish(execution, "failed", advance=False)
            state["source_messages"] += sum(c.get("messages", 0) for c in sync["cycles"])
            state["source_errors"] += sum(c.get("profile_errors", 0) + c.get("lookup_errors", 0) + c.get("review_groups", 0)
                                         for c in sync["cycles"])
            execution["stage"] = "analysis"
            previous = {c["uid"]: c for c in state["children"]}
            state["children"] = self.select_customers(state)
            for child in state["children"]:
                child["run_ids"] = previous.get(child["uid"], {}).get("run_ids", [])
            self.tasks.save_execution(execution)
        unfinished = False
        for child in state["children"]:
            if child["status"] in ("succeeded", "failed", "skipped"):
                continue
            unfinished = True
            current = self.runs.get(child["run_id"]) if child.get("run_id") else None
            if current and current["status"] not in TERMINAL:
                continue
            if current:
                if current["status"] == "failed":
                    retryable = not any(word in (current.get("error") or "") for word in ("未确认", "已提交", "核实", "策略已变化", "合约已变化"))
                    if child["retries_used"] >= config["retries"] or not retryable:
                        child.update(status="failed", reason=current.get("error") or "处理失败")
                        if not retryable:
                            # 结果未核实的请求跨执行保留阻断，不能借下一次定时器绕过重试保护。
                            state.setdefault("blocked_customers", {})[child["uid"]] = dict(
                                stream=state["template"]["analysis_stream"], run_id=child["run_id"], reason=child["reason"])
                        self.tasks.save_execution(execution)
                        continue
                    child["retries_used"] += 1
                    child["retry_at"] = (self.clock() + duration(config["retry_delay"])).isoformat()
                else:
                    child["cursor"] = current["request"]["end"]
                    child["retries_used"] = 0
                child["run_id"] = None
                if bound(child["cursor"]) >= window_end:
                    child["status"] = "succeeded"
                    self.tasks.save_execution(execution)
                    continue
                self.tasks.save_execution(execution)
            if child.get("retry_at") and bound(child["retry_at"]) > self.clock():
                continue
            if child.get("run_id") is None:
                child["run_id"] = str(uuid4())
                child["run_ids"].append(child["run_id"])
                self.tasks.save_execution(execution)
            start = bound(child["cursor"])
            end = min(start + duration(config["interval"]), window_end)
            request = dict(state["template"], uid=child["uid"], start=start.isoformat(), end=end.isoformat(),
                data_start=state["origin"], message_source="stored", allowed_groups=child["allowed_groups"])
            try:
                self.runs.create(request, math.ceil((end - start) / duration(config["interval"])), run_id=child["run_id"])
            except RunConflict:
                # 同客户的其他任务或队列占满时保留已分配 ID，继续检查其他客户。
                continue
        if not unfinished or all(c["status"] in ("succeeded", "failed", "skipped") for c in state["children"]):
            partial = state["source_errors"] or any(c["status"] == "failed" for c in state["children"])
            state["had_errors"] = bool(state.get("had_errors") or partial)
            if window_end < bound(state["end"]):
                # 手动范围也沿用逐窗口编排，已分析结果立即可见。
                state.update(window_start=window_end.isoformat(),
                    window_end=min(window_end + duration(config["interval"]), bound(state["end"])).isoformat(),
                    sync_run_id=str(uuid4()), sync_attempts=[])
                state.pop("retry_at", None)
                execution["stage"] = "discovery"
                self.tasks.save_execution(execution)
            else:
                self.finish(execution, "partial" if state["had_errors"] else "succeeded", advance=True)

    def select_customers(self, state):
        config, children = state["config"], []
        profiles = self.profiles()
        known = {p["uid"] for p in profiles}
        profiles += [{"uid": uid, "level": None} for uid in config["uids"] if uid not in known]
        for profile in profiles:
            uid = profile["uid"]
            cursor = bound(state.get("window_start", state["start"]))
            end = bound(state.get("window_end", state["end"]))
            reason = eligibility(config, profile, state["frozen_uids"], self.clock())
            blocked = state.get("blocked_customers", {}).get(uid)
            if blocked and blocked["stream"] == state["template"]["analysis_stream"]:
                prior_run = self.runs.get(blocked["run_id"], internal=True)
                if prior_run and prior_run.get("recovered_by"):
                    state["blocked_customers"].pop(uid)
                else:
                    children.append(dict(uid=uid, level=profile.get("level"), status="failed", reason=blocked["reason"],
                        allowed_groups=[], cursor=cursor.isoformat(), run_id=blocked["run_id"], run_ids=[], retries_used=0))
                    continue
            groups = self.customers.list_groups(uid=uid, limit=10000)["items"] if not reason else []
            allowed = [g["group_id"] for g in groups if g["status"] == "confirmed" and g["group_id"] not in config["excluded_groups"]]
            if not reason and not allowed:
                reason = "没有已确认且未排除的关联群"
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
                            # 已有后续事件时将迟到消息放入当前窗口，禁止倒放未来事件。
                            if latest > end:
                                reason = "等待当前窗口追平已有事件进度"
                            elif latest <= earliest:
                                cursor = earliest
            children.append(dict(uid=uid, level=profile.get("level"), status="skipped" if reason else
                "succeeded" if config["operation"] == "sync" else "queued", reason=reason,
                allowed_groups=allowed, cursor=cursor.isoformat(), run_id=None, run_ids=[], retries_used=0))
        return children

    def finish(self, execution, status, *, advance):
        execution["status"] = status
        execution["stage"] = "completed"
        config = execution["state"]["config"]
        end = bound(execution["state"]["end"])
        next_at = self.clock() + duration(config["frequency"])
        if advance and end + duration(config["interval"]) <= self.clock():
            next_at = self.clock()
        self.tasks.save_execution(execution, cursor=_sql_time(end) if advance else None, next_at=_sql_time(next_at))

    def progress(self, execution):
        state = execution["state"]
        customers, created, updated, processed = [], set(), set(), 0
        for child in state["children"]:
            item = {k: child.get(k) for k in ("uid", "level", "status", "reason", "run_id")}
            item["run_id"] = child.get("run_id") or next(iter(reversed(child["run_ids"])), None)
            item["stream"] = state["template"]["analysis_stream"]
            item.update(processed=0, created=0, updated=0, completed_windows=0, total_windows=0)
            own_created, own_updated = set(), set()
            for run_id in child["run_ids"]:
                run = self.runs.get(run_id)
                if not run:
                    continue
                item["completed_windows"] += run["completed_windows"]
                item["total_windows"] += run["total_windows"]
                if run_id == child.get("run_id") and item["status"] == "queued":
                    item["status"] = run["status"] if run["status"] in ("queued", "running") else "queued"
                for cycle in run["cycles"]:
                    item["processed"] += cycle.get("processed", 0)
                    own_created.update(cycle.get("created", []))
                    own_updated.update(cycle.get("updated", []))
            item["created"], item["updated"] = len(own_created), len(own_updated)
            processed += item["processed"]
            created.update(own_created)
            updated.update(own_updated)
            customers.append(item)
        syncs = [self.runs.get(rid) for rid in state.get("sync_run_ids", [state["sync_run_id"]])]
        return dict(execution_id=execution["execution_id"], task_id=execution["task_id"], status=execution["status"],
            stage=execution["stage"], start=state["start"], end=state["end"], created_at=execution["created_at"],
            finished_at=execution["finished_at"], config=state["config"], error=state.get("error"),
            source_messages=state["source_messages"], source_errors=state["source_errors"], processed=processed,
            created_events=len(created), updated_events=len(updated),
            sync_completed=sum(s["completed_windows"] for s in syncs if s),
            sync_total=sum(s["total_windows"] for s in syncs if s),
            customers=customers)
