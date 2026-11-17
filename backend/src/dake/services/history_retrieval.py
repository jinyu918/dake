"""在同客户、同分析流内有界检索历史候选；只读，不决定事项归属。"""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import re

from ..config import HistoryRetrievalSettings
from ..domain.models import Event, Message, parse_time
from ..analysis.continuity import recovery_is_safe


RETRIEVAL_VERSION = "bounded-keywords-v1"
HISTORY_POLICY = """
历史候选是只读背景，不是新增消息或归属决定。零命中不证明应该 new，多命中不代表应该 merge。
hit_reasons 是检索线索，不是已确认事实；evidence_ids 只是历史引用，未提供原文的 ID 不得用作本轮证据。
history_retrieval.incomplete 为 true 表示预算、版本或快照限制导致候选可能不完整。
较早或已关闭事件也可作为背景；是否允许更新仍遵守现有输出合约及匹配规则。
"""

_UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_EVENT_REF = re.compile(r"\bevent_id\s*[:=]\s*(" + _UUID + r")(?![\w-])")
_MESSAGE_REF = re.compile(r"\bmessage_id\s*[:=]\s*([a-zA-Z][a-zA-Z0-9_]*:[a-zA-Z0-9_.-]+)")
_WORDS = re.compile(r"[\u3400-\u9fff]+|[a-zA-Z0-9][a-zA-Z0-9_.-]*")


def _clues(messages, limits):
    """只认显式 UUID 事件引用及完整消息键；请求号仅作为词法线索。"""
    content = "\n".join(message.content for message in messages)
    event_ids = list(dict.fromkeys(match.group(1).lower() for match in _EVENT_REF.finditer(content)))
    message_ids = list(dict.fromkeys(match.group(1) for match in _MESSAGE_REF.finditer(content)))
    keywords = {}
    text = _MESSAGE_REF.sub("", _EVENT_REF.sub("", content)).casefold()
    for match in _WORDS.finditer(text):
        word = match.group()
        if "\u3400" <= word[0] <= "\u9fff":
            terms = (word[index:index + 2] for index in range(len(word) - 1))
        else:
            terms = [word] if 2 <= len(word) <= 64 else []
        for term in terms:
            keywords.setdefault(term, None)
            if len(keywords) > limits.keyword_limit:
                break
        if len(keywords) > limits.keyword_limit:
            break
    limited = (len(event_ids) > limits.exact_limit or len(message_ids) > limits.exact_limit
               or len(keywords) > limits.keyword_limit)
    return (event_ids[:limits.exact_limit], message_ids[:limits.exact_limit],
            list(keywords)[:limits.keyword_limit], limited)


@dataclass(frozen=True)
class HistoryCandidate:
    """脱离存储对象的候选副本，不含旧聊天原文、属性或完整修订。"""

    event: Event
    hit_reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    evidence_limited: bool = False
    evidence_refs: tuple[dict, ...] = field(default=(), repr=False)

    def to_input(self):
        """模型仅获得有预算的概述、身份、时间、状态和引用线索。"""
        event = self.event
        recovery = event.recovery
        return dict(event_id=event.event_id, type=event.type,
                    types=event.types or ([event.type] if event.type != "待分类" else []),
                    type_evidence=event.type_evidence,
                    removed_types=event.removed_types,
                    classification_evidence_status=event.classification_evidence_status,
                    classification_status=event.classification_status,
                    other_reason=event.other_reason,
                    typed_attributes=event.typed_attributes,
                    field_states=event.field_states, field_issues=event.field_issues, version=event.version,
                    topic_key=event.topic_key,
                    start_time=event.start_time.isoformat(), end_time=event.end_time.isoformat(),
                    summary=event.summary, status=event.status, severity=event.severity,
                    assignees=event.assignees,
                    closed_at=event.closed_at.isoformat() if event.closed_at else None,
                    recovery=dict(confirmed_at=recovery["confirmed_at"], evidence_ids=[
                        f"{ref['source']}:{ref['message_id']}" for ref in recovery["evidence"]],
                        identity_basis=recovery["identity_basis"], scope=recovery.get("scope"),
                        review_status=recovery.get("review_status", "confirmed")) if recovery else None,
                    recurrence_of_event_id=event.recurrence_of["event_id"] if event.recurrence_of else None,
                    relations=event.relations,
                    hit_reasons=list(self.hit_reasons), evidence_ids=list(self.evidence_ids),
                    evidence_limited=self.evidence_limited)


@dataclass
class HistoryRetrievalResult:
    """分别报告扫描与模型输入限制；空结果不携带新建或合并意图。"""

    uid: str
    stream: str
    before: datetime
    input_versions: tuple[tuple[str, str], ...]
    candidates: list[HistoryCandidate] = field(default_factory=list)
    scanned: int = 0
    pages: int = 0
    unsafe_skipped: int = 0
    snapshot_unavailable: int = 0
    scan_limited: bool = False
    clues_limited: bool = False
    context_limited: bool = False
    context_chars: int = 2

    @property
    def events(self):
        """沿用 Provider 的已有事件参数，不把历史证据添加到新增消息。"""
        return [candidate.event for candidate in self.candidates]

    def metadata(self):
        """限制元数据不包含消息正文、检索词或外部凭据。"""
        return dict(version=RETRIEVAL_VERSION, mode="forward_only", before=self.before.isoformat(),
                    scanned=self.scanned, pages=self.pages, unsafe_skipped=self.unsafe_skipped,
                    snapshot_unavailable=self.snapshot_unavailable, scan_limited=self.scan_limited,
                    clues_limited=self.clues_limited, context_limited=self.context_limited,
                    context_chars=self.context_chars,
                    incomplete=bool(self.scan_limited or self.clues_limited or self.context_limited
                                    or self.unsafe_skipped or self.snapshot_unavailable
                                    or any(c.evidence_limited for c in self.candidates)))


class HistoryRetriever:
    """分页先取元数据，安全核验后取最后快照；不做任意历史 as-of 重建。"""

    def __init__(self, store, limits: HistoryRetrievalSettings):
        self.store, self.limits = store, limits

    def retrieve(self, uid: str, stream: str, messages: list[Message], before: datetime):
        """由增量服务在现有客户事务内调用，只使用本批可分析消息生成线索。"""
        if not uid or not stream or before.tzinfo is None:
            raise ValueError("历史检索必须有客户、分析流及带时区的上界")
        if any(message.uid != uid or message.sent_at >= before for message in messages):
            raise ValueError("历史检索输入超出客户或时间范围")
        result = HistoryRetrievalResult(uid, stream, before, tuple((m.key, m.digest) for m in messages))
        if not messages:
            return result
        event_refs, message_refs, keywords, result.clues_limited = _clues(messages, self.limits)
        recent_start = before - timedelta(days=self.limits.recent_days)
        rooms = {(message.source, message.conversation_id) for message in messages if message.conversation_id}
        matches, seen = [], set()

        def consume(heads):
            result.scanned += len(heads)
            safe = []
            for head in heads:
                if head["event_id"] in seen:
                    continue
                seen.add(head["event_id"])
                try:
                    ends = [parse_time(value) for value in head["revision_ends"]]
                    valid = bool(ends) and ends == sorted(ends) and max(ends) <= before
                except (ValueError, TypeError, AttributeError):
                    valid = False
                if valid:
                    safe.append(head)
                else:
                    result.unsafe_skipped += 1
            snapshots = self.store.history_event_snapshots(uid, stream, safe) if safe else []
            result.snapshot_unavailable += len(safe) - len(snapshots)
            by_id = {head["event_id"]: head for head in safe}
            for event in snapshots:
                if (event.uid, event.stream) != (uid, stream) or event.event_id not in by_id:
                    raise ValueError("历史快照越过客户或分析流范围")
                head = by_id[event.event_id]
                try:
                    valid = (event.created_on < before and event.start_time <= event.end_time < before
                             and event.end_time < parse_time(head["revision_ends"][-1])
                             and (event.closed_at is None or event.closed_at < before)
                             and recovery_is_safe(event, before)
                             and bool(event.evidence)
                             and all(event.start_time <= parse_time(item["send_time"]) <= event.end_time
                                     and item["source"] and item["message_id"] for item in event.evidence))
                except (ValueError, TypeError, KeyError):
                    valid = False
                if not valid:
                    result.unsafe_skipped += 1
                    continue
                evidence_ids = list(dict.fromkeys(f"{item['source']}:{item['message_id']}" for item in event.evidence))
                facts = " ".join(f"{key} {value}" for key, value in event.attributes.items() if isinstance(value, str))
                text = f"{event.topic_key or ''} {event.summary} {facts}".casefold()
                hits = [word for word in keywords if word in text]
                exact_event = event.event_id in event_refs
                exact_message = any(key in evidence_ids for key in message_refs)
                contextual = event.end_time >= recent_start and any(
                    (proof.get("source"), proof.get("conversation_id")) in rooms
                    for facts in event.type_evidence.values() for fact in facts
                    for proof in fact.get("evidence_refs", []))
                if not (exact_event or exact_message or hits or contextual):
                    continue
                reasons = (["exact_event_id"] if exact_event else []) + (["exact_message_key"] if exact_message else [])
                reasons += ["keyword:" + word for word in hits]
                if contextual:
                    reasons.append("recent_conversation_context")
                reasons.append("recent" if event.end_time >= recent_start else "older")
                # 关闭与未关闭只影响候选排序，不在这里认定恢复、持续或复发。
                rank = (-int(exact_event), -int(exact_message), event.end_time < recent_start,
                        -len(hits), event.closed_at is not None, -event.end_time.timestamp(), event.event_id)
                event = deepcopy(event)
                event.attributes, event.extensions, event.updated_by = {}, {}, []
                # 显式命中的引用优先保留；同消息的多个版本仅输出一次引用。
                recovery_refs = (event.recovery or {}).get("evidence", [])
                recovery_ids = list(dict.fromkeys(f"{ref['source']}:{ref['message_id']}" for ref in recovery_refs))
                ordered_ids = list(dict.fromkeys([key for key in message_refs if key in evidence_ids] + recovery_ids))
                ordered_ids += [key for key in evidence_ids if key not in ordered_ids]
                selected_ids = ordered_ids[:self.limits.evidence_limit]
                evidence_by_id = {f"{item['source']}:{item['message_id']}": item for item in event.evidence}
                for ref in recovery_refs:
                    # 恢复依据使用当时的版本，不能被同 ID 后来的编辑替代。
                    evidence_by_id[f"{ref['source']}:{ref['message_id']}"] = next(item for item in event.evidence if
                        (item["source"], item["message_id"], item.get("digest")) ==
                        (ref["source"], ref["message_id"], ref["digest"]))
                # 仅保留本次候选的有限版本依据，模型候选列表仍不携带旧正文。
                chronology = sorted(evidence_by_id, key=lambda key: parse_time(evidence_by_id[key]["send_time"]))
                context_ids = list(dict.fromkeys(
                    [key for key in message_refs if key in evidence_by_id]
                    + recovery_ids + [chronology[0], chronology[-1]] + ordered_ids))[:self.limits.evidence_limit]
                evidence_refs = tuple(deepcopy(evidence_by_id[key]) for key in context_ids)
                event.evidence = [dict(source=evidence_by_id[key]["source"], message_id=evidence_by_id[key]["message_id"],
                                       send_time=evidence_by_id[key]["send_time"],
                                       digest=evidence_by_id[key].get("digest")) for key in selected_ids]
                matches.append((rank, HistoryCandidate(event, tuple(reasons), tuple(selected_ids),
                                                       len(evidence_ids) > len(selected_ids), evidence_refs)))

        if event_refs:
            consume(self.store.history_event_heads(uid, stream, before, limit=self.limits.exact_limit,
                                                   event_ids=event_refs))
        # 近期与更早历史各有页数，近期记录很多也不会挤掉更早历史的扫描名额。
        for upper, lower, page_limit in ((before, recent_start, self.limits.recent_pages),
                                         (recent_start, None, self.limits.older_pages)):
            cursor = None
            for page_index in range(page_limit):
                heads = self.store.history_event_heads(uid, stream, upper, since=lower, cursor=cursor,
                                                       limit=self.limits.page_size)
                result.pages += 1
                consume(heads)
                if len(heads) < self.limits.page_size:
                    break
                cursor = heads[-1]["end_time"], heads[-1]["event_id"]
                if page_index == page_limit - 1:
                    result.scan_limited = True
        values = []
        for _, candidate in sorted(matches, key=lambda item: item[0]):
            proposed = [*values, candidate.to_input()]
            chars = len(json.dumps(proposed, ensure_ascii=False))
            if len(values) >= self.limits.max_candidates or chars > self.limits.max_chars:
                result.context_limited = True
                continue
            result.candidates.append(candidate)
            values, result.context_chars = proposed, chars
        return result
