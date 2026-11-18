"""本地持久任务执行器：单工作线程、跨进程执行锁、逐周期保存可见进度。"""

from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
import math
import logging
import threading
from uuid import uuid4

from ..bootstrap import build_service
from ..analysis.providers.xhub import XHubError, configured_stream
from ..analysis.attribution import attribution_version
from ..config import API_MAX_RANGE_DAYS, API_MAX_WINDOWS, HistoryContextSettings, HistoryRetrievalSettings, bound, duration
from ..domain.models import TZ, parse_time, windows
from ..ingestion.client import validate_uid
from ..repositories.local_state import private_json
from ..repositories.attempts import AttemptError, DeliveryError
from ..services.queries import duration_label
from ..services.replay import ReplayService


class RunInterrupted(RuntimeError):
    """服务关闭或失去执行锁时停止继续处理下一周期。"""


def compact_progress(payload):
    """任务进度只带本轮变化事件；完整历史仍由事件版本、收据和周期快照保存。"""
    changed = set(payload.get("created", [])) | set(payload.get("updated", []))
    return {**payload, "events": [event for event in payload.get("events", [])
                                  if event["event_id"] in changed]}


def prepare_request(settings, values: dict) -> tuple[dict, int]:
    """冻结官方来源请求并限制单次工作量。"""
    mode = values.get("mode", "official")
    if mode != "official":
        raise ValueError("运行模式无效")
    uid = values.get("uid") or settings.uid
    start = bound(values["start"]) if values.get("start") else settings.start
    end = bound(values["end"], end=True) if values.get("end") else settings.end
    interval_value = values.get("interval") or duration_label(settings.interval)
    offline = bool(values.get("offline", True))
    validate_uid(uid)
    try:
        interval = duration(interval_value)
    except OverflowError:
        raise ValueError("处理周期超出支持范围") from None
    if start >= end or end - start > timedelta(days=API_MAX_RANGE_DAYS):
        raise ValueError("一次处理的时间范围须大于零且不能超过本地上限")
    total = math.ceil((end - start) / interval)
    if total > API_MAX_WINDOWS:
        raise ValueError("处理周期数量超过本地上限，请缩小范围或增大周期")
    return dict(mode=mode, uid=uid, start=start.isoformat(), end=end.isoformat(),
                interval=duration_label(interval), offline=offline,
                context_lookback=duration_label(settings.context_lookback),
                match_window=duration_label(settings.match_window),
                closed_grace=duration_label(settings.closed_grace),
                ingest_lookback=duration_label(settings.ingest_lookback),
                history_retrieval=asdict(settings.history_retrieval),
                history_context=asdict(settings.history_context),
                attribution_version=attribution_version(priority_preview=settings.priority_preview) if settings.provider == "xhub" else None,
                default_status=settings.default_status, default_severity=settings.default_severity,
                closed_statuses=list(settings.closed_statuses),
                provider=settings.provider,
                llm_model=settings.llm_model if settings.provider == "xhub" else None,
                llm_max_tokens=settings.llm_max_tokens if settings.provider == "xhub" else None,
                llm_timeout_seconds=settings.llm_timeout_seconds if settings.provider == "xhub" else None,
                analysis_stream=configured_stream(settings)), total


class RunRunner:
    """协调器只编排持久工作；分析工人按名额并行，不再等整轮客户都结束。"""

    def __init__(self, settings, repository, *, service_factory=build_service, poll_interval=0.25, customer_factory=None):
        self.customer_factory = customer_factory
        self.settings = settings
        self.repository = repository
        self.service_factory = service_factory
        self.poll_interval = poll_interval
        self.runner_id = str(uuid4())
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._coordinator_context = threading.local()
        self._keeper = None
        self.coordinator = None
        self.pipeline = None
        self.attempts = None
        self._workers = []

    @property
    def _keeper(self):
        """协调锁连接只属于当前协调线程，不能被分析或抓取线程读取。"""
        return getattr(self._coordinator_context, "keeper", None)

    @_keeper.setter
    def _keeper(self, value):
        self._coordinator_context.keeper = value

    def start(self):
        """协调线程只负责编排；抓取和分析各自领取持久工作。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._coordinate, name="dake-coordinator", daemon=True)
        self._thread.start()
        slots = max(1, int(getattr(self.settings, "analysis_slots", 2)))
        self._workers = []
        for index in range(slots):
            thread = threading.Thread(target=self._consume, args=(False,), name=f"dake-analysis-{index}", daemon=True)
            thread.start()
            self._workers.append(thread)
        fetch = threading.Thread(target=self._consume, args=(True,), name="dake-fetch", daemon=True)
        fetch.start()
        self._workers.append(fetch)

    def notify(self):
        """提交后唤醒本实例，其他实例提交的任务由短轮询发现。"""
        self._wake.set()

    def stop(self):
        """当前窗口提交后停止；不把仍在运行的窗口批量改成失败。"""
        self._stop.set()
        self._wake.set()
        for thread in [self._thread, *self._workers]:
            if thread is not None:
                thread.join(timeout=5)

    def _wait(self):
        self._wake.wait(self.poll_interval)
        self._wake.clear()

    def _leader_alive(self):
        if self._keeper is None:
            return False
        rows, _ = self._keeper._execute("SELECT IS_USED_LOCK(%s)=CONNECTION_ID() AS ok", (self.repository.worker_lock,))
        return bool(rows and rows[0]["ok"] == 1)

    def _coordinate(self):
        """编排锁只包住调度和租约核实，不包住模型或客户分析。"""
        while not self._stop.is_set():
            try:
                with self.repository.connection() as keeper:
                    rows, _ = keeper._execute("SELECT GET_LOCK(%s,0) AS ok", (self.repository.worker_lock,))
                    if rows and rows[0]["ok"] == 1:
                        self._keeper = keeper
                        try:
                            if self.coordinator is not None:
                                self.coordinator.tick()
                            if self.pipeline is not None and self.attempts is not None:
                                self.pipeline.verify_expired(self.attempts)
                            if self.settings.priority_calendar is not None:
                                from ..services.item_priority import check_due_item_priorities

                                check_due_item_priorities(keeper, self.settings.priority_calendar, now=datetime.now(TZ))
                        finally:
                            keeper._execute("SELECT RELEASE_LOCK(%s)", (self.repository.worker_lock,))
                            self._keeper = None
            except Exception as error:
                from ..repositories.mysql import StorageError
                logging.getLogger(__name__).error("执行器循环异常：%s %s", type(error).__name__, str(error) if isinstance(error, StorageError) else "")
                if not self._stop.is_set():
                    self._stop.wait(min(1.0, self.poll_interval * 4))
            self._wait()

    def _consume(self, source):
        """空闲名额领取下一份持久工作；退避中的客户不会占着工人。"""
        while not self._stop.is_set():
            try:
                run = self.repository.claim_source(self.runner_id) if source else self.repository.claim_fair(self.runner_id)
            except Exception as error:
                from ..repositories.mysql import StorageError
                logging.getLogger(__name__).error("领取工作异常：%s %s", type(error).__name__, str(error) if isinstance(error, StorageError) else "")
                self._wait()
                continue
            if run is None:
                self._wait()
                continue
            try:
                self.execute(run)
            finally:
                slot = run.get("_capacity_slot")
                if slot is not None:
                    try:
                        self.repository.release_slot(self.runner_id, source=bool(run.get("_capacity_source")), slot=slot)
                    except Exception as error:
                        from ..repositories.mysql import StorageError
                        logging.getLogger(__name__).error("归还名额异常：%s %s", type(error).__name__, str(error) if isinstance(error, StorageError) else "")

    def _start_heartbeat(self, claim):
        stop = threading.Event()

        def beat():
            interval = max(1, int(getattr(self.settings, "lease_seconds", 30) / 3))
            while not stop.wait(interval):
                try:
                    self.pipeline.heartbeat(claim["uid"], claim["owner"], claim["epoch"], self.settings.lease_seconds)
                except Exception:
                    return

        threading.Thread(target=beat, name="dake-lease", daemon=True).start()
        return stop.set

    @contextmanager
    def _bind_window(self, service, request, window):
        """有执行权时才允许严格窗口提交；失败释放本代次，倒灌则按客户阻断。"""
        service.strict_window = bool(request.get("strict_window"))
        service.gate = None
        claim = None
        stop = None
        pipeline = self.pipeline if service.strict_window else None
        try:
            if pipeline is not None:
                from ..services.pipeline import WindowLease
                target = parse_time(request["catchup_target"]) if request.get("catchup_target") else window.end
                claim = pipeline.acquire(service.settings.uid, window, self.runner_id, service.stream,
                    phase=request.get("phase") or "catchup", catchup_target=target,
                    lease_seconds=getattr(self.settings, "lease_seconds", 30))
                service.gate = WindowLease(pipeline, claim)
                stop = self._start_heartbeat(claim)
            yield
        except Exception as error:
            from ..repositories.pipeline import PipelineError
            if claim is not None:
                if isinstance(error, PipelineError) and "倒灌" in str(error):
                    pipeline.block(claim["uid"], claim["owner"], claim["epoch"], "history_blocked", str(error))
                elif pipeline.blocked(claim["uid"]) is None:
                    pipeline.release_claim(claim)
            raise
        finally:
            if stop is not None:
                stop()
            service.gate = None
            service.strict_window = False

    def _journal_path(self, run_id, index):
        return self.settings.data_dir / "run-results" / run_id / f"{index:04d}.json"

    def _restore_cycle(self, result, interrupted, service, before_revisions):
        """恢复已提交但未记入任务进度的变化，普通重复运行仍返回零增量。"""
        payload = result.to_dict()
        if interrupted is None:
            return payload
        restored = None
        journal = self._journal_path(interrupted["run_id"], interrupted["completed_windows"])
        if journal.exists():
            saved = json.loads(journal.read_text(encoding="utf-8"))
            if (saved.get("window") == payload["window"] and saved.get("uid") == result.uid
                    and saved.get("stream") == result.stream):
                restored = saved
        baseline = interrupted.get("current_baseline") or {}
        created, updated = [], []
        for event in result.events:
            for revision in event.get("updated_by", [])[baseline.get(event["event_id"], 0):before_revisions.get(event["event_id"], 0)]:
                if (revision["window_start"] == payload["window"]["start"]
                        and revision["window_end"] == payload["window"]["end"]):
                    target = created if revision["action"] == "created" else updated
                    if event["event_id"] not in target:
                        target.append(event["event_id"])
        if restored is None and (created or updated):
            # 旧修订可证明事件变化，但本次重试可能已覆盖快照；不猜测旧消息计数。
            restored = dict(created=created, updated=updated, processed=0, ingested=0, counts_complete=False)
        if restored is not None:
            # 重试可能同时摄入迟到消息；恢复旧变化时仍保留本次新变化和最新事件版本。
            payload["created"] = list(dict.fromkeys(restored.get("created", []) + payload["created"]))
            payload["updated"] = list(dict.fromkeys(restored.get("updated", []) + payload["updated"]))
            payload["processed"] += restored.get("processed", 0)
            payload["ingested"] += restored.get("ingested", 0)
            payload["recovered_from_history"] = True
            payload["counts_complete"] = restored.get("counts_complete", True)
        return payload

    def execute(self, run):
        """每个任务独占业务连接，通过既有 ReplayService 按配置周期处理。"""
        request = run["request"]
        if request["mode"] == "customer_sync":
            return self._execute_customer_sync(run)
        settings = replace(self.settings, uid=request["uid"], start=parse_time(request.get("data_start", request["start"])),
                           provider=request.get("provider", "stub"),
                           llm_model=request.get("llm_model") or self.settings.llm_model,
                           llm_max_tokens=request.get("llm_max_tokens") or self.settings.llm_max_tokens,
                           llm_timeout_seconds=request.get("llm_timeout_seconds") or self.settings.llm_timeout_seconds,
                           end=parse_time(request["end"]), interval=duration(request["interval"]),
                           context_lookback=duration(request.get("context_lookback", duration_label(self.settings.context_lookback))),
                           match_window=duration(request.get("match_window", duration_label(self.settings.match_window))),
                           closed_grace=duration(request.get("closed_grace", duration_label(self.settings.closed_grace))),
                           ingest_lookback=duration(request.get("ingest_lookback", duration_label(self.settings.ingest_lookback))),
                           history_retrieval=HistoryRetrievalSettings(**request.get(
                               "history_retrieval", asdict(self.settings.history_retrieval))),
                           history_context=HistoryContextSettings(**request.get(
                               "history_context", asdict(self.settings.history_context))),
                           default_status=request.get("default_status", self.settings.default_status),
                           default_severity=request.get("default_severity", self.settings.default_severity),
                           closed_statuses=tuple(request.get("closed_statuses", self.settings.closed_statuses)))
        try:
            if request["mode"] != "official":
                raise ValueError("该运行模式已移除")
            if (request["mode"] == "official" and settings.provider == "xhub"
                    and request.get("attribution_version") != attribution_version(priority_preview=settings.priority_preview)):
                raise XHubError("增量归属策略已变化，请重新提交任务；原分析范围及已处理消息保持不变")
            if request.get("analysis_stream") and request["analysis_stream"] != configured_stream(settings):
                raise XHubError("提示词或输出合约已变化，请重新提交任务")
            with self.service_factory(settings, offline=request["offline"]) as service:
                if request.get("message_source") == "stored":
                    from ..ingestion.stored import ScopedStore, StoredChatSource
                    scoped = ScopedStore(service.store, request.get("allowed_groups", []))
                    service.store = scoped
                    service.ingestion.store = scoped
                    service.source = service.ingestion.source = StoredChatSource(scoped)
                    service.history_retriever.store = scoped
                    service.history_context.store = scoped
                retry_attempts = {}
                # 仅使用本任务已持久化的进度；不移动业务起点，也不把文件水位当成提交证明。
                start_index = run["completed_windows"]
                if not 0 <= start_index <= run["total_windows"]:
                    raise RunInterrupted()
                results = ReplayService(service).run_range(settings.uid, parse_time(request["start"]), settings.end, settings.interval,
                                                          run_id=run["run_id"], retry_attempts=retry_attempts,
                                                          start_index=start_index)
                all_windows = list(windows(parse_time(request["start"]), settings.end, settings.interval))
                stored = request.get("message_source") == "stored"
                skip_until = start_index
                recover_range = stored and (bool(run.get("current_window")) or self.repository.has_interrupted_range(
                    settings.uid, request["start"], request["end"]))
                for index, window in enumerate(all_windows):
                    if index < skip_until:
                        continue
                    if self._stop.is_set() or (self._keeper is not None and not self._leader_alive()):
                        raise RunInterrupted()
                    if stored and not recover_range and not request.get("strict_window"):
                        from ..services.empty_windows import empty_prefix
                        with service.store.customer_lock(settings.uid):
                            empty = empty_prefix(service, settings.uid, all_windows[index:])
                            if empty:
                                self.repository.complete_empty_windows(run["run_id"], self.runner_id, index, empty)
                                skip_until = index + len(empty)
                                continue
                    window_value = {"start": window.start.isoformat(), "end": window.end.isoformat()}
                    interrupted = self.repository.interrupted_window(settings.uid, request["mode"], window_value)
                    if interrupted:
                        prior = interrupted.get("request", {})
                        if (prior.get("provider", "stub") != request.get("provider", "stub") or
                                (prior.get("analysis_stream") is not None and
                                 prior["analysis_stream"] != request.get("analysis_stream"))):
                            interrupted = None
                    baseline = {event.event_id: len(event.updated_by)
                                for event in service.store.list_events(settings.uid, service.stream)}
                    self.repository.begin_window(run["run_id"], self.runner_id, window_value, baseline)
                    if interrupted and service.attempts is not None:
                        previous = service.attempts.for_run_window(settings.uid, service.stream,
                            interrupted["run_id"], interrupted["completed_windows"], window)
                        if previous is not None:
                            retry_attempts[index] = previous["attempt_id"]
                    elif (service.attempts is not None and index == start_index
                          and run.get("current_window") == window_value):
                        # 显式恢复同一任务也保留失败尝试关系；有收据时仅恢复展示。
                        previous = service.attempts.for_run_window(settings.uid, service.stream,
                            run["run_id"], index, window)
                        if previous is not None:
                            retry_attempts[index] = previous["attempt_id"]
                    with self._bind_window(service, request, window):
                        if stored:
                            identity = dict(run_id=run["run_id"], window_index=index, retry_of=retry_attempts.get(index))
                            result = service.run_window(settings.uid, window, **({"execution": identity} if service.attempts is not None else {}))
                        else:
                            result = next(results)
                    if self._keeper is not None and not self._leader_alive():
                        if result.attempt_id:
                            raise DeliveryError(f"执行 {result.attempt_id} 的业务已提交，执行权变化导致进度未保存；请恢复展示")
                        raise RunInterrupted()
                    # 有新执行收据时使用精确恢复结果，不把旧成果合并进本次分析收据。
                    payload = result.to_dict() if result.attempt_id else self._restore_cycle(result, interrupted, service, baseline)
                    payload = compact_progress(payload)
                    # 事件已提交后先留下私有结果，再追加任务进度；后者失败时可恢复真实变化。
                    progress_requested = False
                    try:
                        private_json(self._journal_path(run["run_id"], index), payload)
                        progress_requested = True
                        self.repository.complete_window(run["run_id"], self.runner_id, payload)
                    except Exception:
                        if result.attempt_id:
                            try:
                                service.attempts.delivery(settings.uid, service.stream, result.attempt_id, "run_progress",
                                    "unconfirmed" if progress_requested else "not_attempted")
                            except Exception:
                                pass
                            detail = "任务进度写入未确认" if progress_requested else "任务结果文件保存失败，尚未写入任务进度"
                            raise DeliveryError(f"执行 {result.attempt_id} 的业务已提交，{detail}；请恢复展示") from None
                        raise
                    if result.attempt_id:
                        service.attempts.delivery(settings.uid, service.stream, result.attempt_id, "run_progress", "saved")
                    if interrupted is not None:
                        self.repository.mark_recovered(interrupted["run_id"], run["run_id"])
            if self._keeper is not None and not self._leader_alive():
                raise RunInterrupted()
            self.repository.finish(run["run_id"], self.runner_id)
        except Exception as error:
            from ..repositories.mysql import StorageError
            from ..repositories.pipeline import PipelineError
            logging.getLogger(__name__).error("客户执行异常：%s %s", type(error).__name__, str(error) if isinstance(error, StorageError) else "")
            message = ("服务已停止，已完成周期保留；可重新提交相同范围"
                       if isinstance(error, RunInterrupted) else
                       "事务结果未确认，请核实后再重试" if error.__class__.__name__ == "TransactionOutcomeUnknown" else
                       str(error) if isinstance(error, (XHubError, AttemptError, PipelineError, ValueError)) else
                       "处理失败，已完成周期保留；请检查本地缓存、MySQL 或官方取数网络后重试")
            self.repository.finish(run["run_id"], self.runner_id, error=message)

    def _execute_customer_sync(self, run):
        """客户同步沿用同一执行锁、领取机制及终态，不装配抽取 Provider。"""
        from ..services.customers import CustomerSyncService

        def check():
            if self._stop.is_set() or (self._keeper is not None and not self._leader_alive()):
                raise RunInterrupted()

        try:
            service = (self.customer_factory or CustomerSyncService)(self.settings)
            request = run["request"]
            if request.get("task_options"):
                service.task_options = request["task_options"]
            errors = sum(c.get("profile_errors", 0) + c.get("lookup_errors", 0) for c in run.get("cycles", []))
            for index, window in enumerate(windows(parse_time(request["start"]), parse_time(request["end"]), duration(request["interval"]))):
                if index < run["completed_windows"]:
                    continue
                check()
                self.repository.begin_window(run["run_id"], self.runner_id,
                    {"start": window.start.isoformat(), "end": window.end.isoformat()}, {})
                cycle = service.sync_window(window, run["run_id"], self.runner_id, index, check)
                errors += cycle["profile_errors"] + cycle["lookup_errors"]
                if self.pipeline is not None:
                    self.pipeline.publish_source(window, message_count=cycle.get("messages", 0),
                        isolated_count=cycle.get("isolated", 0), complete=True)
            check()
            self.repository.finish(run["run_id"], self.runner_id,
                error="部分资料或群搜索查询失败，已保存有效结果；请查看客户状态后重新同步" if errors else None)
        except Exception as error:
            from ..ingestion.client import ChatRecordsError
            from ..repositories.mysql import StorageError
            detail = str(error) if isinstance(error, (ChatRecordsError, StorageError)) else ""
            logging.getLogger(__name__).error("客户同步异常：%s %s", type(error).__name__, detail)
            self.repository.finish(run["run_id"], self.runner_id,
                error=detail or "客户同步未完成，已提交结果保留；请检查接口、数据库和时间范围后重新同步")
