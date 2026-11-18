"""围绕已找到的候选补本地聊天，不检索新候选、不消费消息、不写事件。"""

from copy import deepcopy
from datetime import timedelta
from itertools import zip_longest
import re

from ..domain.history_context import HistoryContextResult
from ..domain.models import Event, parse_time
from .sender_kinds import sender_kind


CONTEXT_POLICY = """
history_context 是只读背景，items 中的消息不能因为被补取就成为本轮新增或新事件证据。
links 的 event_evidence 表示原事件已有引用；neighbor 仅表示附近聊天，不证明事件归属。
availability、fragments、reasons 明示缺失和截断，不得补造未提供的正文或结论。
本轮输出 evidence_ids 仍仅允许引用 new_messages 和 context_messages，不得引用仅在 history_context 中的消息。
没有候选、正文缺失或预算不足不证明应该 new；关键词相似也不证明应该 merge。
背景只按已记录的窗口和证据版本核验，arrival_history_available=false 不代表完整还原当时已知信息。
"""
_REFERENCE = re.compile(r"上次|之前|那个|这个|上述|如下|message_id\s*[:=]")


def _identity(ref):
    """同消息不同版本分别定位，避免旧正文与本轮修订混淆。"""
    return ref["source"], ref["message_id"], ref.get("digest")


def _fragments(content, limit):
    """保留可核对的首尾片段与原始偏移，不把截断后的文本构造成 Message。"""
    if len(content) <= limit:
        return [dict(start=0, end=len(content), text=content)]
    first = limit // 2
    last = len(content) - (limit - first)
    return [dict(start=0, end=first, text=content[:first]),
            dict(start=last, end=len(content), text=content[last:])]


class HistoryContextService:
    """证据优先，存在阅读缺口时最多补一页同群邻域；资源上限集中配置。"""

    def __init__(self, store, limits):
        self.store, self.limits = store, limits

    def retrieve(self, history, new_messages, context, events, kinds, *, exclude_message=None):
        """沿用客户事务的输入视图；只读 SQL，不联网、不读取原始缓存或水位。"""
        uid, stream, before = history.uid, history.stream, history.before
        if (not uid or not stream or before.tzinfo is None
                or history.input_versions != tuple((m.key, m.digest) for m in new_messages)
                or any(m.uid != uid or m.sent_at >= before for m in [*new_messages, *context])
                or any((e.uid, e.stream) != (uid, stream) for e in events)
                or any((c.event.uid, c.event.stream) != (uid, stream) for c in history.candidates)):
            raise ValueError("历史上下文与客户、分析流或当前批次不一致")
        result = HistoryContextResult(uid, stream, before, history.input_versions,
                                      tuple((m.key, m.digest) for m in context),
                                      tuple(c.event.event_id for c in history.candidates))
        if history.metadata()["incomplete"]:
            result.reasons.add("candidate_retrieval_incomplete")
        if not history.candidates:
            result.reasons.add("no_candidate")
            return result
        supplied = {(m.source, m.message_id, m.digest): m for m in [*context, *new_messages]}
        new_versions = {(m.source, m.message_id, m.digest) for m in new_messages}
        requests = {}
        # 轮流给每个候选直接证据名额，避免某个长事件占满输入。
        rows = [[(candidate.event.event_id, ref) for ref in candidate.evidence_refs]
                for candidate in history.candidates]
        for row in zip_longest(*rows):
            for entry in row:
                if entry is None:
                    continue
                event_id, ref = entry
                if (not ref.get("source") or not ref.get("message_id")
                        or parse_time(ref["send_time"]) >= before):
                    raise ValueError("候选证据超出历史读取边界")
                identity = _identity(ref)
                link = dict(event_id=event_id, kind="event_evidence", anchor_key=None)
                if identity in requests:
                    requests[identity][1].append(link)
                elif len(requests) < self.limits.max_messages:
                    requests[identity] = (ref, [link])
                else:
                    result.omitted += 1
                    result.reasons.add("message_budget")
        if not requests:
            result.reasons.add("evidence_references_unavailable")
            return result.finish(self.limits)
        pending = [ref for identity, (ref, _) in requests.items() if identity not in supplied and ref.get("digest")]
        loaded = self._load(uid, before, pending, result)
        anchors, needs = {}, set()
        for identity, (ref, links) in requests.items():
            message = supplied.get(identity) or loaded.get(identity)
            if exclude_message and (message is None or exclude_message(message)):
                continue
            item = self._item(ref, links, message, kinds)
            if identity in supplied:
                item.update(availability="already_in_input", fragments=[], truncated=False,
                            input_role="new_messages" if identity in new_versions else "context_messages")
            needs_neighbor = message is None or item["truncated"] or _REFERENCE.search(message.content)
            if needs_neighbor:
                needs.update(link["event_id"] for link in links)
            if message is not None and message.conversation_id:
                for link in links:
                    if needs_neighbor or link["event_id"] not in anchors:
                        anchors[link["event_id"]] = message
            if message is None:
                result.reasons.add("evidence_version_unavailable")
            if item["truncated"]:
                result.reasons.add("message_truncated")
            result.add(item, self.limits)

        neighbor_requests = {}
        for candidate in history.candidates:
            if "context_budget" in result.reasons:
                break
            event_id = candidate.event.event_id
            if event_id not in needs:
                continue
            anchor = anchors.get(event_id)
            if anchor is None:
                result.reasons.add("neighbor_anchor_unavailable")
                continue
            remaining = self.limits.max_messages - len(requests) - len(neighbor_requests)
            if remaining <= 0:
                result.reasons.add("message_budget")
                break
            limit = min(remaining, self.limits.neighbor_messages)
            result.queries += 1
            heads = self.store.history_neighbor_heads(uid, anchor, before,
                        span=timedelta(minutes=self.limits.neighbor_minutes), limit=limit)
            if not heads:
                result.reasons.add("neighbor_not_found")
            if len(heads) >= limit:
                result.reasons.add("neighbor_budget")
            for ref in heads:
                if (ref["source"] != anchor.source or parse_time(ref["send_time"]) >= before
                        or abs(parse_time(ref["send_time"]) - anchor.sent_at) > timedelta(minutes=self.limits.neighbor_minutes)):
                    raise ValueError("邻近消息元数据超出允许范围")
                identity = _identity(ref)
                if identity in requests:
                    for item in result.items:
                        if (item["source"], item["message_id"], item["digest"]) == identity:
                            item["links"].append(dict(event_id=event_id, kind="neighbor", anchor_key=anchor.key))
                    continue
                link = dict(event_id=event_id, kind="neighbor", anchor_key=anchor.key)
                if identity in neighbor_requests:
                    if neighbor_requests[identity][2] != anchor.conversation_id:
                        raise ValueError("同版本邻近消息被返回到不同群")
                    neighbor_requests[identity][1].append(link)
                else:
                    neighbor_requests[identity] = (ref, [link], anchor.conversation_id)
        # 没有版本档案时，只读取有同流安全快照依据的邻居正文；processed_by 不能证明历史版本。
        safe = self._safe_neighbor_versions(events, before, {
            identity: parse_time(ref["send_time"]) for identity, (ref, _, _) in neighbor_requests.items()})
        pending = [ref for identity, (ref, _, _) in neighbor_requests.items()
                   if identity in safe and identity not in supplied]
        neighbors = self._load(uid, before, pending, result)
        for identity, (ref, links, group) in neighbor_requests.items():
            message = (supplied.get(identity) or neighbors.get(identity)) if identity in safe else None
            if message is not None and message.conversation_id != group:
                raise ValueError("邻近消息正文不属于锚点所在群")
            if exclude_message and (message is None or exclude_message(message)):
                continue
            item = self._item(ref, links, message, kinds)
            if message is None:
                item.update(conversation_id=group, availability="historical_version_unavailable")
                result.reasons.add("neighbor_version_unavailable")
            elif identity in supplied:
                item.update(availability="already_in_input", fragments=[], truncated=False,
                            input_role="new_messages" if identity in new_versions else "context_messages")
            if item["truncated"]:
                result.reasons.add("message_truncated")
            result.add(item, self.limits)
        return result.finish(self.limits)

    def _load(self, uid, before, refs, result):
        """核验仓库返回，防止误实现把其他客户、未来或错误版本混入输入。"""
        if not refs:
            return {}
        result.queries += 1
        expected = {_identity(ref): parse_time(ref["send_time"]) for ref in refs}
        loaded = {}
        for message in self.store.history_messages(uid, refs, before):
            identity = (message.source, message.message_id, message.digest)
            if message.uid != uid or message.sent_at >= before or expected.get(identity) != message.sent_at:
                raise ValueError("历史正文与指定客户、时间或证据版本不一致")
            loaded[identity] = message
        return loaded

    def _item(self, ref, links, message, kinds):
        """来源信息不完整时保留空值；旧摘录不冒充完整消息或当前版本的群信息。"""
        text = message.content if message is not None else str(ref.get("excerpt") or "")[:160]
        return dict(id=f"{ref['source']}:{ref['message_id']}", source=ref["source"], message_id=ref["message_id"],
                    digest=ref.get("digest"), send_time=ref["send_time"], links=deepcopy(links),
                    conversation_id=message.conversation_id if message else None,
                    sender_id=message.sender_id if message else None,
                    sender_kind=sender_kind(message.to_dict(), kinds) if message else "unknown",
                    availability="full" if message else "excerpt_only" if text else "missing",
                    fragments=_fragments(text, self.limits.message_chars) if text else [],
                    truncated=len(text) > self.limits.message_chars,
                    original_chars=len(text) if message else None)

    @staticmethod
    def _safe_neighbor_versions(events, before, wanted):
        """只从已有安全修订取版本证明，不把当前投影或未来结论当历史依据。"""
        safe = set()
        if not wanted:
            return safe
        for event in events:
            try:
                ends = [parse_time(revision["window_end"]) for revision in event.updated_by]
                if not ends or ends != sorted(ends) or ends[-1] > before:
                    continue
                snapshot = Event.from_dict(deepcopy(event.updated_by[-1]["snapshot"]))
                if ((snapshot.uid, snapshot.stream, snapshot.event_id) != (event.uid, event.stream, event.event_id)
                        or not snapshot.created_on < before or not snapshot.start_time <= snapshot.end_time < ends[-1]
                        or (snapshot.closed_at is not None and snapshot.closed_at >= before)):
                    continue
                if any(not snapshot.start_time <= parse_time(ref["send_time"]) <= snapshot.end_time
                       or not ref["source"] or not ref["message_id"] for ref in snapshot.evidence):
                    continue
                for ref in snapshot.evidence:
                    if wanted.get(_identity(ref)) == parse_time(ref["send_time"]):
                        safe.add(_identity(ref))
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
        return safe
