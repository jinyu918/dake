"""共用的消息摄入流程：按来源取数、筛选业务窗口，再原子保存消息。"""

from datetime import datetime
from typing import Protocol

from ..domain.models import Message, Window, message_order


class Source(Protocol):
    """来源实现只返回统一消息，平台取数细节由来源适配器处理。"""

    def load_window(self, uid: str | None, start: datetime, end: datetime) -> list[Message]:
        """返回指定窗口的统一消息；UID 为空用于跨客户发现，不在源内分析事件。"""
        ...


class IngestionService:
    """按发送时间过滤来源窗口响应，避免未来窗口消息提前入库或被分析。"""

    def __init__(self, store, source: Source):
        self.store = store
        self.source = source

    def load_window(self, uid: str, start: datetime, end: datetime) -> list[Message]:
        """允许重叠取数补漏，统一校验客户并按发送时间全局排序。"""
        Window(start, end)
        messages = {}
        for message in self.source.load_window(uid, start, end):
            if message.uid != uid:
                raise ValueError("适配器返回了其他客户的消息")
            if start <= message.sent_at < end:
                previous = messages.get(message.key)
                if previous is not None and previous != message:
                    raise ValueError("同一来源消息出现冲突记录，已停止摄入")
                messages[message.key] = message
        return sorted(messages.values(), key=message_order)

    def fetch(self, uid: str, window: Window) -> int:
        """只取数、适配并保存消息，不推进分析水位，不生成事件。"""
        messages = self.load_window(uid, window.start, window.end)
        with self.store.transaction(uid):
            return self.store.upsert_messages(messages)
