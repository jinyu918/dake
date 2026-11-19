"""任务中心复用已摄入消息；读取时限定已确认群，并排除指定群的新消息。"""


class ScopedStore:
    """沿用原事务和事件实现，仅过滤消息输入；历史事件不删除或重新编号。"""

    def __init__(self, store, allowed_groups):
        self.store = store
        self.allowed = set(allowed_groups)

    def __getattr__(self, name):
        return getattr(self.store, name)

    def keep(self, messages):
        return [m for m in messages if m.conversation_id in self.allowed]

    def pending_messages(self, *args):
        return self.keep(self.store.pending_messages(*args))

    def context_messages(self, *args):
        return self.keep(self.store.context_messages(*args))

    def history_messages(self, *args):
        return self.keep(self.store.history_messages(*args))

    def legacy_alert_messages(self, message, delivery):
        reader = getattr(self.store, "legacy_alert_messages", None)
        if reader is None:
            return []
        return [(item, source) for item, source in reader(message, delivery)
                if item.conversation_id in self.allowed]

    def history_neighbor_heads(self, uid, anchor, before, **kwargs):
        if anchor.conversation_id not in self.allowed:
            return []
        return self.store.history_neighbor_heads(uid, anchor, before, **kwargs)


class StoredChatSource:
    """来源只返回统一消息，事件分析继续交给既有增量服务。"""

    def __init__(self, store):
        self.store = store

    def load_window(self, uid, start, end):
        return self.store.context_messages(uid, start, end)
