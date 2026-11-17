"""仅用于测试的内存仓库与合成数据源，不作为生产存储或真实分析替代品。"""

from contextlib import contextmanager
from copy import deepcopy

from dake.domain.models import message_order


class MemoryStore:
    """模拟仓库事务边界，包括失败回滚和按分析版本保存处理标记。"""

    def __init__(self):
        self.messages = {}
        self.events = {}
        self.processed = {}
        self.attributions = {}
        self._uid = None

    def init_schema(self):
        pass

    @contextmanager
    def customer_lock(self, uid):
        yield self

    @contextmanager
    def transaction(self, uid):
        if self._uid is not None:
            raise RuntimeError("测试仓库不允许嵌套事务")
        before = deepcopy((self.messages, self.events, self.processed, self.attributions))
        self._uid = uid
        try:
            yield self
        except BaseException:
            self.messages, self.events, self.processed, self.attributions = before
            raise
        finally:
            self._uid = None

    def _require(self, uid):
        if self._uid != uid:
            raise RuntimeError("测试写入必须位于对应客户事务内")

    def upsert_messages(self, messages):
        changed = 0
        for message in messages:
            self._require(message.uid)
            existing = self.messages.get(message.key)
            if existing is not None and existing.uid != message.uid:
                raise RuntimeError("同一消息 ID 已属于其他客户")
            if existing is None or existing.digest != message.digest:
                self.messages[message.key] = deepcopy(message)
                changed += 1
        return changed

    def context_messages(self, uid, start, end):
        return sorted((deepcopy(m) for m in self.messages.values()
                       if m.uid == uid and start <= m.sent_at < end), key=message_order)

    def pending_messages(self, uid, start, end, stream):
        return [message for message in self.context_messages(uid, start, end)
                if self.processed.get((message.key, stream)) != message.digest]

    def mark_processed(self, messages, stream):
        for message in messages:
            self._require(message.uid)
            existing = self.messages.get(message.key)
            if existing is None or existing.digest != message.digest:
                raise RuntimeError("消息不存在或已变化")
            self.processed[(message.key, stream)] = message.digest

    def record_attributions(self, messages, stream, records):
        """模拟与事件、处理摘要共同提交的当前消息版本归属收据。"""
        for message in messages:
            self._require(message.uid)
            relevant = [record for record in records if any(
                ref["message_id"] == message.key for ref in record["new_evidence"])]
            if relevant:
                if self.messages[message.key].digest != message.digest:
                    raise RuntimeError("归属消息版本已变化")
                self.attributions[(message.key, stream)] = dict(digest=message.digest, records=deepcopy(relevant))

    def latest_message(self, uid, before, stream=None):
        messages = [m for m in self.messages.values() if m.uid == uid and m.sent_at < before]
        if stream is not None:
            messages = [m for m in messages if self.processed.get((m.key, stream)) == m.digest]
        return deepcopy(max(messages, key=message_order)) if messages else None

    def history_messages(self, uid, references, before):
        """模拟按客户、发送时间和证据 digest 读取，不提供不匹配的版本。"""
        expected = {(ref["source"], ref["message_id"], ref["digest"]): ref["send_time"] for ref in references}
        return sorted((deepcopy(m) for m in self.messages.values()
                       if m.uid == uid and m.sent_at < before
                       and expected.get((m.source, m.message_id, m.digest)) == m.sent_at.isoformat()), key=message_order)

    def history_neighbor_heads(self, uid, anchor, before, *, span, limit):
        """邻域仅返回元数据，保持正文与版本校验之间的边界。"""
        messages = [m for m in self.messages.values()
                    if m.uid == uid and m.source == anchor.source and m.conversation_id == anchor.conversation_id
                    and anchor.sent_at - span <= m.sent_at < min(before, anchor.sent_at + span)
                    and m.key != anchor.key]
        messages.sort(key=lambda m: (abs(m.sent_at - anchor.sent_at), m.sent_at, m.message_id))
        return [dict(source=m.source, message_id=m.message_id, digest=m.digest, send_time=m.sent_at.isoformat())
                for m in messages[:limit]]

    def list_events(self, uid, stream):
        return sorted((deepcopy(e) for e in self.events.values() if e.uid == uid and e.stream == stream),
                      key=lambda event: (event.start_time, event.event_id))

    def history_event_heads(self, uid, stream, before, *, limit, since=None, cursor=None, event_ids=None):
        """模拟已在仓库层限定范围的键集分页，保留与生产查询一致的边界。"""
        events = [event for event in self.events.values()
                  if event.uid == uid and event.stream == stream and event.end_time < before
                  and (since is None or event.end_time >= since)
                  and (cursor is None or (event.end_time, event.event_id) < cursor)
                  and (event_ids is None or event.event_id in event_ids)]
        events.sort(key=lambda event: (event.end_time, event.event_id), reverse=True)
        return [dict(event_id=event.event_id, end_time=event.end_time,
                     revision_ends=[revision.get("window_end") for revision in event.updated_by])
                for event in events[:limit]]

    def history_event_snapshots(self, uid, stream, heads):
        """只返回同一客户和流、元数据没有变化的最后快照，不读取消息正文。"""
        from dake.domain.models import Event

        result = []
        for head in heads:
            event = self.events.get(head["event_id"])
            if (event is not None and event.uid == uid and event.stream == stream
                    and [revision.get("window_end") for revision in event.updated_by] == head["revision_ends"]
                    and event.updated_by and event.updated_by[-1].get("snapshot")):
                result.append(Event.from_dict(deepcopy(event.updated_by[-1]["snapshot"])))
        return result

    def save_event(self, event):
        self._require(event.uid)
        existing = self.events.get(event.event_id)
        if existing and (existing.uid, existing.stream) != (event.uid, event.stream):
            raise RuntimeError("事件不能覆盖其他客户或分析流")
        self.events[event.event_id] = deepcopy(event)

    def close(self):
        pass


class FakeSource:
    """模拟日期粒度上游，允许测试期间追加迟到消息，始终返回完整当天数据。"""

    def __init__(self, messages=()):
        self.messages = list(messages)
        self.calls = []

    def load_window(self, uid, start, end):
        """保留按日假来源以覆盖旧回放，同时实现统一窗口协议。"""
        from datetime import timedelta

        day = start.date()
        messages = []
        while day <= (end - timedelta(microseconds=1)).date():
            messages.extend(m for m in self.load_day(uid, day) if start <= m.sent_at < end)
            day += timedelta(days=1)
        return messages

    def load_day(self, uid, day):
        self.calls.append((uid, day))
        return deepcopy([m for m in self.messages if m.uid == uid and m.sent_at.date() == day])


class FakeProvider:
    """按合成消息 ID 返回预设候选，并记录输入，便于验证没有泄漏未来信息。"""

    def __init__(self, mapping=None, name="fixture-test-v1"):
        self.mapping = mapping or {}
        self.name = name
        self.calls = []

    def extract(self, new_messages, context, existing_events):
        self.calls.append(deepcopy((new_messages, context, existing_events)))
        candidates = []
        for message in new_messages:
            selected = self.mapping.get(message.message_id, [])
            candidates.extend(deepcopy(selected if isinstance(selected, list) else [selected]))
        return candidates
