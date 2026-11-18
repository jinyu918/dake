"""增量分析业务流程，供命令行、接口和后台任务共用，不依赖 Web 框架。"""

from copy import deepcopy
from dataclasses import asdict

from ..analysis.matching import match, merge_batch
from ..analysis.providers import Provider
from ..domain.alert_policy import parse_synthetic_alert
from ..analysis.updates import apply_candidate, snapshot_as_of
from ..analysis.validation import validate_candidates, validate_priority_evidence
from ..config import REVIEW, Settings
from ..domain.models import CycleResult, Window, message_order, parse_time
from ..repositories.local_state import LocalState
from ..repositories.mysql import StorageError
from .ingestion import IngestionService, Source
from .history_retrieval import HistoryRetriever
from .history_context import HistoryContextService
from .attribution import apply_attributions, preflight_attributions
from .sender_kinds import FILTER_VERSION, kinds_fingerprint, load_sender_kinds, sender_kind
from .item_priority import refresh_item_priority, save_priority_refresh
from .sender_identity import confirmed_sender_role, load_sender_roles
from .executions import run_recorded, message_ref
from .late import classify_pending


def _load_customer_profile(store, uid: str) -> dict | None:
    """只读读取已持久化的客户等级资料；缺少客户目录表或未同步时保守返回 None。"""
    reader = getattr(store, "customer_profile", None)
    if reader is not None:
        return reader(uid)
    executor = getattr(store, "_execute", None)
    if executor is None:
        return None
    try:
        rows, _ = executor("SELECT uid, level, level_state FROM customer_profiles WHERE uid=%s", (uid,))
        return dict(rows[0]) if rows else None
    except StorageError:
        return None


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
        # 按独立补漏范围回查近期窗口，容纳晚于发送时间入库的消息。
        kinds = load_sender_kinds(self.settings.data_dir)
        roles = load_sender_roles(self.settings.data_dir)
        fingerprint = kinds_fingerprint(kinds)
        fetch_start = max(self.settings.start, window.start - self.settings.ingest_lookback)
        if self.settings.priority_preview:
            profile = _load_customer_profile(self.store, uid) or {}
            if uid != "1383603926" and (window.start < parse_time("2026-11-10")
                    or profile.get("level_state") != "fresh" or profile.get("level") not in {"L6", "L7", "L8", "L9", "L10"}):
                raise ValueError("事项定级预览仅限现有授权范围")
            messages = []
        else:
            messages = self.ingestion.load_window(uid, fetch_start, window.end)
        result = CycleResult(uid, self.stream, window)
        if recorder is not None:
            result.attempt_id = recorder.attempt_id
            recorder.record("ingestion", message_versions=[message_ref(m) for m in messages])
        pending, context, events, isolated = self._preview_window(uid, window, messages, result)
        # 模型调用放在业务事务之外；失败时尚未写入消息和事件。
        bot_pending = [m for m in pending if sender_kind(m.to_dict(), kinds) == "bot"]
        bot_keys = {m.key for m in bot_pending}
        eligible = [m for m in pending if m.key not in bot_keys]
        auxiliary = context + bot_pending
        candidates, history, background = self._extract_outside(uid, window, result, recorder, observer, observe,
            pending, eligible, auxiliary, events, kinds, roles)
        withheld = isolated
        with self.store.transaction(uid):
            writable = getattr(self.store, "store", self.store)
            previous_suppress = getattr(writable, "suppress_revision", False)
            writable.suppress_revision = True
            try:
                result.ingested = self.store.upsert_messages(messages)
                self._require_same_input(uid, window, pending, withheld, events)
                if withheld and getattr(self, "gate", None) is not None:
                    self.gate.isolate(self.store, withheld, window)
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
                    if self.settings.synthetic_alert_source:
                        delivery = parse_synthetic_alert(message, self.settings.synthetic_alert_source)
                        if delivery is not None:
                            self.store.save_alert_delivery(delivery, self.settings.alert_reminder_minutes)
                    result.filter_audit.append(dict(
                        reason="confirmed_bot_auxiliary_only" if eligible else "confirmed_bot_without_new_discussion",
                        message_keys=[message.key], policy_version=FILTER_VERSION, identity_config_hash=fingerprint))
                blocked = set()
                customer_profile = _load_customer_profile(self.store, uid)
                customer_keys = {m.key for m in eligible if confirmed_sender_role(m.to_dict(), roles) == "customer"}
                explicit = any(candidate.decision is not None for candidate in candidates)
                if explicit:
                    if any(candidate.decision is None for candidate in candidates):
                        raise ValueError("同一批候选不能混用明确归属和旧匹配规则")
                    events = apply_attributions(candidates, store=self.store, events=events,
                        visible_events=history.events, available=available, pending=pending, eligible=eligible,
                        background=background, result=result, settings=self.settings, observe=observe,
                        customer_keys=customer_keys,
                        customer_profile=customer_profile, sender_roles=roles,
                        recorder=recorder)
                if not explicit:
                    for candidate in candidates:
                        validate_priority_evidence(candidate, available, uid=uid, human_keys=eligible_keys,
                                                   customer_keys=customer_keys)
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
                    updated = apply_candidate(candidate, old, available, uid, self.stream, window, self.settings, pending_keys,
                                              customer_profile=customer_profile, sender_roles=roles)
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
                if self.settings.priority_preview:
                    profiles = {uid: customer_profile}

                    def profile_for(customer_uid):
                        if customer_uid not in profiles:
                            profiles[customer_uid] = _load_customer_profile(self.store, customer_uid)
                        return profiles[customer_uid]

                    for event in events:
                        if refresh_item_priority(event, profile_for, self.settings.priority_calendar, window.end):
                            updated = event.event_id in result.updated or event.event_id in result.created
                            save_priority_refresh(self.store, event, window, updated, attempt_id=result.attempt_id)
                            if not updated:
                                result.updated.append(event.event_id)
                completed = [message for message in pending if message.key not in blocked]
                self.store.mark_processed(completed, self.stream)
                result.processed = len(completed)
                latest = self.store.latest_message(uid, window.end, self.stream)
                result.watermark = dict(
                    next_start=window.end.isoformat(),
                    last_send_time=latest.sent_at.isoformat() if latest else None,
                    last_message_id=latest.message_id if latest else None,
                )
                result.events = [snapshot for event in events if (snapshot := snapshot_as_of(event, window.end))]
                if recorder is not None:
                    recorder.record("program_result", attributions=result.filter_audit, review=result.review,
                                    created=result.created, updated=result.updated)
                    recorder.receipt(self.store, result, events, completed)
                gate = getattr(self, "gate", None)
                if gate is not None:
                    gate.fence(self.store, incomplete=any(item.get("incomplete") for item in result.filter_audit if isinstance(item, dict)))
            finally:
                writable.suppress_revision = previous_suppress
        if recorder is not None:
            recorder.committed = True
            recorder.repository.confirm_commit(uid, self.stream, recorder.attempt_id)
            recorder.files_started = True
        # 文件只提供恢复便利；正式链路写失败后按原收据恢复展示，不重新分析。
        self.state.snapshot(result)
        self.state.advance(uid, self.stream, result.watermark)
        return result

    def _preview_window(self, uid, window, messages, result):
        """在事务外冻结本窗输入。严格窗口把迟到和修订隔离开，不混入当前模型输入。"""
        lower = self.settings.start
        incoming = [message for message in messages if lower <= message.sent_at < window.end]
        stored = {message.key: message for message in self.store.context_messages(uid, lower, window.end)}
        pending = {message.key: message for message in self.store.pending_messages(uid, lower, window.end, self.stream)}
        for message in incoming:
            previous = stored.get(message.key)
            if previous is None or previous.digest != message.digest:
                pending[message.key] = message
        pending_list = sorted(pending.values(), key=message_order)
        events = self.store.list_events(uid, self.stream)
        later = any(parse_time(revision["window_end"]) > window.end for event in events for revision in event.updated_by)
        if later and not getattr(self, "strict_window", False) and pending_list:
            raise ValueError("该处理流已有更晚的事件；请在当前窗口处理迟到消息，或使用新的分析版本回放")
        if later and getattr(self, "strict_window", False):
            raise ValueError("已进入实时或已有更晚结果，旧历史不能倒灌主流")
        isolated = []
        if getattr(self, "strict_window", False):
            reader = getattr(self.store, "revised_message_keys", None)
            if reader is not None:
                revised = reader(uid, self.stream, lower, window.end)
            else:
                evidence = set()
                for event in events:
                    for item in getattr(event, "evidence", []) or []:
                        key = getattr(item, "key", None)
                        if key is None and isinstance(item, dict):
                            key = item.get("key")
                        if key:
                            evidence.add(key)
                revised = {message.key for message in pending_list if message.key in evidence}
            pending_list, backfill, isolated = classify_pending(
                pending_list, window, events, self.settings.ingest_lookback, revised)
            if backfill:
                result.filter_audit.append(dict(kind="controlled_backfill", count=len(backfill),
                    grace_seconds=int(self.settings.ingest_lookback.total_seconds())))
            if isolated:
                result.filter_audit.append(dict(kind="withheld", count=len(isolated), incomplete=True,
                    reasons=sorted({item["kind"] for item in isolated})))
        context_start = max(self.settings.start, window.start - self.settings.context_lookback)
        context = {message.key: message for message in self.store.context_messages(uid, context_start, window.end)}
        for message in messages:
            if context_start <= message.sent_at < window.end:
                context[message.key] = message
        pending_keys = {message.key for message in pending_list}
        ordered = [message for message in sorted(context.values(), key=message_order) if message.key not in pending_keys]
        return pending_list, ordered, events, isolated

    def _extract_outside(self, uid, window, result, recorder, observer, observe, pending, eligible, auxiliary, events, kinds, roles):
        """调用模型前不持有业务事务。"""
        context = [message for message in auxiliary if message not in pending]
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
        candidates, history, background = [], None, None
        if eligible:
            history = self.history_retriever.retrieve(uid, self.stream, eligible, window.end)
            if recorder is not None:
                recorder.record("history", candidates=[c.to_input() for c in history.candidates], metadata=history.metadata())
            if observer is not None:
                observe("history", candidates=[c.to_input() for c in history.candidates], metadata=history.metadata())
            configure_history = getattr(self.provider, "configure_history_retrieval", None)
            if configure_history is not None:
                configure_history(history)
            configure_context = getattr(self.provider, "configure_history_context", None)
            if configure_context is not None:
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
                        customer_keys={m.key for m in eligible if confirmed_sender_role(m.to_dict(), roles) == "customer"},
                        customer_profile=_load_customer_profile(self.store, uid), sender_roles=roles)
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
        return candidates, history, background

    def _require_same_input(self, uid, window, pending, withheld, events):
        """提交前再次读取。模型期间插入或修订的消息不能带着旧结果落库。"""
        current = self.store.pending_messages(uid, self.settings.start, window.end, self.stream)
        expected = {(message.key, message.digest) for message in pending}
        expected.update((item["source"] + ":" + item["message_id"], item["digest"]) for item in withheld)
        actual = {(message.key, message.digest) for message in current}
        if actual != expected:
            raise ValueError("输入已变化，本窗口结果不提交")
        revisions = {(event.event_id, len(event.updated_by)) for event in events}
        stored = {(event.event_id, len(event.updated_by)) for event in self.store.list_events(uid, self.stream)}
        if stored != revisions:
            raise ValueError("事件基线已变化，本窗口结果不提交")
