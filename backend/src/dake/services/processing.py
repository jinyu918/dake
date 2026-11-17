"""增量分析业务流程，供命令行、接口和后台任务共用，不依赖 Web 框架。"""

from copy import deepcopy
from dataclasses import asdict

from ..analysis.matching import match, merge_batch
from ..analysis.providers import Provider
from ..analysis.updates import apply_candidate, snapshot_for_cycle
from ..analysis.validation import validate_candidates
from ..analysis.evidence_review import review_revised_evidence
from ..config import REVIEW, Settings
from ..domain.models import CycleResult, Window, parse_time
from ..repositories.local_state import LocalState
from .ingestion import IngestionService, Source
from .history_retrieval import HistoryRetriever
from .history_context import HistoryContextService
from .attribution import apply_attributions, preflight_attributions
from .sender_kinds import FILTER_VERSION, kinds_fingerprint, load_sender_kinds, sender_kind
from .sender_identity import confirmed_sender_role, load_sender_roles
from .executions import run_recorded, message_ref


class IncrementalService:
    """维持一次分析的原子性、可重试性和稳定事件身份。"""

    def __init__(self, store, source: Source, provider: Provider, settings: Settings,
                 state: LocalState | None = None, *, attempts=None):
        self.store = store
        self.source = source
        self.provider = provider
        self.settings = settings
        self.state = state or LocalState(settings.data_dir)
        self.ingestion = IngestionService(store, source)
        self.history_retriever = HistoryRetriever(store, settings.history_retrieval)
        self.history_context = HistoryContextService(store, settings.history_context)
        self.attempts = attempts

    @property
    def stream(self):
        """分析版本是独立处理流，确保 stub 不妨碍后续真实模型重放。"""
        return self.provider.name

    def fetch(self, uid: str, window: Window) -> int:
        """保留共用入口，实际摄入职责交给独立 IngestionService。"""
        return self.ingestion.fetch(uid, window)

    def run_window(self, uid: str, window: Window, *, observer=None, execution=None) -> CycleResult:
        """正式装配强制过程记录；独立演示和旧测试可保持原观察接口。"""
        if self.attempts is not None:
            return run_recorded(self, uid, window, observer=observer, execution=execution)
        return self._run_window(uid, window, observer=observer)

    def _run_window(self, uid: str, window: Window, *, observer=None, recorder=None) -> CycleResult:
        """处理一个左闭右开窗口；每个分析版本分别去重，并保留本次变化。"""
        def observe(stage, **payload):
            # 演示显式注入观察者；副本避免观察代码改变匹配输入，记录不表示已提交。
            if observer is not None:
                observer(stage, deepcopy(payload))
            if recorder is not None and stage == "decision":
                recorder.decision(**deepcopy(payload))

        if window.start < self.settings.start:
            raise ValueError("处理窗口不能早于配置的起始时间")
        # 官方接口只有日期精度；回查近期日期，容纳晚于发送时间入库的消息。
        kinds = load_sender_kinds(self.settings.data_dir)
        roles = load_sender_roles(self.settings.data_dir)
        fingerprint = kinds_fingerprint(kinds)
        fetch_start = max(self.settings.start, window.start - self.settings.ingest_lookback)
        messages = self.ingestion.load_window(uid, fetch_start, window.end)
        result = CycleResult(uid, self.stream, window)
        if recorder is not None:
            result.attempt_id = recorder.attempt_id
            recorder.record("ingestion", message_versions=[message_ref(m) for m in messages])
        with self.store.transaction(uid):
            result.ingested = self.store.upsert_messages(messages)
            # 用已处理摘要找新增；只比较最大时间会漏掉较早时间的迟到消息。
            pending = self.store.pending_messages(uid, self.settings.start, window.end, self.stream)
            events = self.store.list_events(uid, self.stream)
            if pending and any(
                parse_time(revision["window_end"]) > window.end
                for event in events for revision in event.updated_by
            ):
                raise ValueError("该处理流已有更晚的事件；请在当前窗口处理迟到消息，或使用新的分析版本回放")
            context_start = max(self.settings.start, window.start - self.settings.context_lookback)
            context = self.store.context_messages(uid, context_start, window.end)
            pending_keys = {message.key for message in pending}
            context = [message for message in context if message.key not in pending_keys]
            bot_pending = [m for m in pending if sender_kind(m.to_dict(), kinds) == "bot"]
            bot_keys = {m.key for m in bot_pending}
            eligible = [m for m in pending if m.key not in bot_keys]
            auxiliary = context + bot_pending
            if recorder is not None:
                recorder.input(pending, eligible, auxiliary)
            if observer is not None:
                observe("input", pending=[m.to_dict() | {"key": m.key, "digest": m.digest} for m in pending],
                        context=[m.to_dict() | {"key": m.key, "digest": m.digest} for m in auxiliary])
            configure = getattr(self.provider, "configure_sender_kinds", None)
            if configure is not None:
                configure(kinds)
            configure_roles = getattr(self.provider, "configure_sender_roles", None)
            if configure_roles is not None:
                configure_roles(roles)
            candidates = []
            history, background = None, None
            if eligible:
                # 历史候选和补取背景限定本轮可引用的旧事件，明确决定不再按主题预合并。
                history = self.history_retriever.retrieve(uid, self.stream, eligible, window.end)
                if recorder is not None:
                    recorder.record("history", candidates=[c.to_input() for c in history.candidates], metadata=history.metadata())
                if observer is not None:
                    observe("history", candidates=[c.to_input() for c in history.candidates],
                            metadata=history.metadata())
                configure_history = getattr(self.provider, "configure_history_retrieval", None)
                if configure_history is not None:
                    configure_history(history)
                configure_context = getattr(self.provider, "configure_history_context", None)
                if configure_context is not None:
                    # 补取结果走独立只读输入，不加入 pending、available 或事件写入证据。
                    background = self.history_context.retrieve(history, eligible, auxiliary, events, kinds)
                    configure_context(background)
                if recorder is not None:
                    recorder.record("history_context", "completed" if background is not None else "skipped",
                                    result=background.to_input() if background is not None else None)
                    recorder.record("extraction", "started")
                    if not hasattr(self.provider, "configure_execution_observer"):
                        recorder.record("provider_input", new_messages=[m.to_dict() | {"digest": m.digest} for m in eligible],
                            context_messages=[m.to_dict() | {"digest": m.digest} for m in auxiliary])
                configure_validator = getattr(self.provider, "configure_candidate_validator", None)
                if configure_validator is not None:
                    def validate_proposals(proposals):
                        available = validate_candidates(proposals, pending, context)
                        human_keys = {m.key for m in eligible}
                        accepted = [c for c in proposals if human_keys.intersection(c.evidence_ids)]
                        preflight_attributions(accepted, events=events, visible_events=history.events,
                            available=available, pending=pending, eligible=eligible, background=background,
                            result=result, settings=self.settings,
                            customer_keys={m.key for m in eligible
                                if confirmed_sender_role(m.to_dict(), roles) == "customer"},
                            support_keys={m.key for m in eligible
                                if confirmed_sender_role(m.to_dict(), roles) == "internal"})
                    configure_validator(validate_proposals)
                try:
                    candidates = self.provider.extract(eligible, auxiliary, history.events)
                finally:
                    if configure_validator is not None:
                        configure_validator(None)
            if recorder is not None:
                if not eligible:
                    for stage in ("history", "history_context", "extraction"):
                        recorder.record(stage, "skipped", reason="no_eligible_new_messages")
                recorder.extracted(candidates)
            if observer is not None:
                observe("extracted", candidates=[asdict(c) for c in candidates])
            available = validate_candidates(candidates, pending, context)
            eligible_keys = {m.key for m in eligible}
            accepted = []
            for candidate in candidates:
                if any(available[key].key in eligible_keys for key in candidate.evidence_ids):
                    accepted.append(candidate)
                else:
                    result.filter_audit.append(dict(
                        reason="bot_only_candidate", message_keys=[available[key].key for key in candidate.evidence_ids],
                        policy_version=FILTER_VERSION, identity_config_hash=fingerprint))
            candidates = accepted
            pending_keys = eligible_keys
            for message in bot_pending:
                result.filter_audit.append(dict(
                    reason="confirmed_bot_auxiliary_only" if eligible else "confirmed_bot_without_new_discussion",
                    message_keys=[message.key], policy_version=FILTER_VERSION, identity_config_hash=fingerprint))
            blocked = set()
            explicit = any(candidate.decision is not None for candidate in candidates)
            if explicit:
                if any(candidate.decision is None for candidate in candidates):
                    raise ValueError("同一批候选不能混用明确归属和旧匹配规则")
                events = apply_attributions(candidates, store=self.store, events=events,
                    visible_events=history.events, available=available, pending=pending, eligible=eligible,
                    background=background, result=result, settings=self.settings, observe=observe,
                    customer_keys={m.key for m in eligible if confirmed_sender_role(m.to_dict(), roles) == "customer"},
                    support_keys={m.key for m in eligible if confirmed_sender_role(m.to_dict(), roles) == "internal"},
                    recorder=recorder)
            # 未声明归属的可替换 Provider 使用主题匹配，正式 XHub 统一返回明确归属。
            for candidate in merge_batch([] if explicit else candidates, available, self.settings.match_window, pending_keys,
                                         self.settings.closed_statuses,
                                         {(e.type, e.topic_key) for e in events if e.closed_at is not None}):
                old, possible_ids, uncertain = match(candidate, events, available, self.settings, pending_keys)
                if uncertain:
                    blocked.update(available[key].key for key in candidate.evidence_ids)
                    result.review.append(dict(
                        review_flag=REVIEW, type=candidate.type,
                        message_ids=[available[key].message_id for key in candidate.evidence_ids],
                        possible_event_ids=possible_ids,
                        reason="主题或指代不明确，保留待复核，不自动新建或合并",
                    ))
                    if observer is not None or recorder is not None:
                        observe("decision", candidate=asdict(candidate), action="review", event_id=None,
                                possible_event_ids=possible_ids, reason=result.review[-1]["reason"],
                                before=None, after=None)
                    continue
                updated = apply_candidate(candidate, old, available, uid, self.stream, window, self.settings, pending_keys)
                if recorder is not None and updated is not None:
                    updated.updated_by[-1]["attempt_id"] = recorder.attempt_id
                if observer is not None or recorder is not None:
                    observe("decision", candidate=asdict(candidate),
                            action="no_change" if updated is None else "created" if old is None else "updated",
                            event_id=updated.event_id if updated else old.event_id if old else None,
                            possible_event_ids=[], reason=None,
                            before=old.to_dict() if old else None,
                            after=updated.to_dict() if updated else old.to_dict() if old else None)
                if updated is None:
                    continue
                self.store.save_event(updated)
                if old is None:
                    events.append(updated)
                    result.created.append(updated.event_id)
                else:
                    events = [updated if event.event_id == updated.event_id else event for event in events]
                    if updated.event_id not in result.updated:
                        result.updated.append(updated.event_id)
            if pending:
                events = review_revised_evidence(events, pending, store=self.store, window=window,
                                                result=result, settings=self.settings)
            completed = [message for message in pending if message.key not in blocked]
            self.store.mark_processed(completed, self.stream)
            result.processed = len(completed)
            latest = self.store.latest_message(uid, window.end, self.stream)
            result.watermark = dict(
                next_start=window.end.isoformat(),
                last_send_time=latest.sent_at.isoformat() if latest else None,
                last_message_id=latest.message_id if latest else None,
            )
            result.events = [snapshot for event in events if (snapshot := snapshot_for_cycle(event, window.end))]
            if recorder is not None:
                recorder.record("program_result", attributions=result.filter_audit, review=result.review,
                                created=result.created, updated=result.updated)
                recorder.receipt(self.store, result, events, completed)
        if recorder is not None:
            recorder.committed = True
            recorder.repository.confirm_commit(uid, self.stream, recorder.attempt_id)
            recorder.files_started = True
        # 文件只提供恢复便利；正式链路写失败后按原收据恢复展示，不重新分析。
        self.state.snapshot(result)
        self.state.advance(uid, self.stream, result.watermark)
        return result
