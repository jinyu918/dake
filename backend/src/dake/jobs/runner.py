"""本地持久任务执行器：单工作线程、跨进程执行锁、逐周期保存可见进度。"""

from dataclasses import asdict, replace
from datetime import timedelta
import json
import math
import logging
import threading
from uuid import uuid4

from ..bootstrap import build_service
from ..analysis.providers.xhub import XHubError, configured_stream
from ..analysis.attribution import attribution_version
from ..config import API_MAX_RANGE_DAYS, API_MAX_WINDOWS, HistoryContextSettings, HistoryRetrievalSettings, bound, duration
from ..domain.models import parse_time, windows
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
                attribution_version=attribution_version() if settings.provider == "xhub" else None,
                default_status=settings.default_status, default_severity=settings.default_severity,
                closed_statuses=list(settings.closed_statuses),
                provider=settings.provider,
                llm_model=settings.llm_model if settings.provider == "xhub" else None,
                llm_max_tokens=settings.llm_max_tokens if settings.provider == "xhub" else None,
                llm_timeout_seconds=settings.llm_timeout_seconds if settings.provider == "xhub" else None,
                analysis_stream=configured_stream(settings)), total


class RunRunner:
    """任务始终串行处理；只有拥有 MySQL 命名锁的实例可以恢复和领取任务。"""

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
        self._keeper = None
        self.coordinator = None

    def start(self):
        """启动一个本地工作线程；数据库执行锁防止多 API 实例重复工作。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="dake-runner", daemon=True)
        self._thread.start()

    def notify(self):
        """提交后唤醒本实例，其他实例提交的任务由短轮询发现。"""
        self._wake.set()

    def stop(self):
        """在当前周期完成后停止，不中断数据库事务；下一次启动会标记异常中断。"""
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _wait(self):
        self._wake.wait(self.poll_interval)
        self._wake.clear()

    def _leader_alive(self):
        if self._keeper is None:
            return False
        rows, _ = self._keeper._execute("SELECT IS_USED_LOCK(%s)=CONNECTION_ID() AS ok", (self.repository.worker_lock,))
        return bool(rows and rows[0]["ok"] == 1)

    def _loop(self):
        while not self._stop.is_set():
            try:
                with self.repository.connection() as keeper:
                    rows, _ = keeper._execute("SELECT GET_LOCK(%s,0) AS ok", (self.repository.worker_lock,))
                    if not rows or rows[0]["ok"] != 1:
                        self._wait()
                        continue
                    self._keeper = keeper
                    # 只有新取得执行锁的实例恢复中断，旁观实例不触碰别人的 running。
                    self.repository.recover_interrupted()
                    while not self._stop.is_set() and self._leader_alive():
                        if self.coordinator is not None:
                            self.coordinator.tick()
                        run = self.repository.claim_next(self.runner_id)
                        if run is None:
                            self._wait()
                        else:
                            self.execute(run)
            except Exception as error:
                # 只记录异常类型与已脱敏数据库错误，不输出聊天或模型响应。
                from ..repositories.mysql import StorageError
                logging.getLogger(__name__).error("执行器循环异常：%s %s", type(error).__name__, str(error) if isinstance(error, StorageError) else "")
                # 数据库暂时不可达时保留持久状态；重连取得锁后再明确恢复中断。
                if not self._stop.is_set():
                    self._stop.wait(min(1.0, self.poll_interval * 4))
            finally:
                self._keeper = None

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
                    and request.get("attribution_version") != attribution_version()):
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
                    if stored and not recover_range:
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
            logging.getLogger(__name__).error("客户执行异常：%s %s", type(error).__name__, str(error) if isinstance(error, StorageError) else "")
            message = ("服务已停止，已完成周期保留；可重新提交相同范围"
                       if isinstance(error, RunInterrupted) else
                       "事务结果未确认，请核实后再重试" if error.__class__.__name__ == "TransactionOutcomeUnknown" else
                       str(error) if isinstance(error, (XHubError, AttemptError)) else
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
            check()
            self.repository.finish(run["run_id"], self.runner_id,
                error="部分资料或群搜索查询失败，已保存有效结果；请查看客户状态后重新同步" if errors else None)
        except Exception:
            self.repository.finish(run["run_id"], self.runner_id,
                error="客户同步未完成，已提交结果保留；请检查接口、数据库和时间范围后重新同步")
