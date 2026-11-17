"""面向页面的只读查询，不读取或公开原始响应目录。"""

from contextlib import contextmanager
from datetime import timedelta

from ..analysis.providers.xhub import configured_stream
from ..domain.models import EVENT_LABELS, EVENT_TYPES, PENDING_TYPE
from ..domain.models import TYPE_ATTRIBUTE_NAMES
from ..domain.event_identity import followup_events
from ..ingestion.client import validate_uid
from ..repositories.mysql import MySQLStore
from ..repositories.attempts import AttemptRepository
from .enrich import enrich_events
from .event_changes import event_changes
from .execution_changes import execution_changes
from .sender_identity import load_sender_roles
from .sender_kinds import load_sender_kinds


def duration_label(value: timedelta) -> str:
    """将配置周期转换成页面可重新提交的紧凑形式。"""
    seconds = int(value.total_seconds())
    for divisor, suffix in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds % divisor == 0:
            return f"{seconds // divisor}{suffix}"
    return f"{seconds}s"


class QueryService:
    """每次读取建立独立连接，查询必须限定客户和分析流。"""

    def __init__(self, settings, store_factory=MySQLStore, *, attempts=None):
        self.settings = settings
        self.store_factory = store_factory
        self.attempts = attempts if attempts is not None else AttemptRepository(settings, store_factory)

    @contextmanager
    def connection(self):
        store = self.store_factory(**self.settings.store_kwargs())
        try:
            yield store
        finally:
            store.close()

    def scope(self, uid=None, stream=None):
        """查询限定客户及当前分析版本，不能跨范围读取事件。"""
        uid = uid or self.settings.uid
        validate_uid(uid)
        expected = configured_stream(self.settings)
        if stream is not None and stream != expected:
            raise ValueError("客户与分析流不匹配")
        return uid, expected

    def config(self):
        """返回公开缺省及真实计数，不泄露数据库或网络凭据。"""
        customers = []
        with self.connection() as store:
            rows, _ = store._execute("SELECT DISTINCT uid FROM messages WHERE source='official_chat' ORDER BY uid")
            official_uids = [self.settings.uid] + [row["uid"] for row in rows
                                                  if row["uid"] != self.settings.uid]
            scopes = [(uid, "官方样例客户" if uid == self.settings.uid else f"客户 {uid}",
                       "official_chat", configured_stream(self.settings)) for uid in official_uids]
            for uid, label, source, stream in scopes:
                rows, _ = store._execute("SELECT COUNT(*) AS total FROM messages WHERE uid=%s AND source=%s", (uid, source))
                count = len(followup_events(store.list_events(uid, stream)))
                customers.append(dict(uid=uid, label=label, stream=stream,
                                      message_count=rows[0]["total"], event_count=count))
        end = self.settings.end
        end_text = (end - timedelta(days=1)).date().isoformat() if end.time().isoformat() == "00:00:00" else end.isoformat()
        return dict(uid=self.settings.uid, start=self.settings.start.isoformat(), end=end_text,
                    interval=duration_label(self.settings.interval),
                    context_lookback=duration_label(self.settings.context_lookback),
                    match_window=duration_label(self.settings.match_window),
                    ingest_lookback=duration_label(self.settings.ingest_lookback),
                    closed_grace=duration_label(self.settings.closed_grace),
                    event_types=list(EVENT_TYPES), provider=self.settings.provider, customers=customers,
                    field_targets=[dict(kind="common", name=name, type_label=None) for name in
                        ("summary", "status", "severity", "assignees", "other_reason")]
                    + [dict(kind="label", name=label, type_label=None) for label in EVENT_LABELS]
                    + [dict(kind="attribute", name=name, type_label=label)
                       for label, names in TYPE_ATTRIBUTE_NAMES.items() for name in names])

    def events(self, uid=None, stream=None, event_type=None):
        """按当前标签筛选，旧单类型记录保留主类型兼容。"""
        uid, stream = self.scope(uid, stream)
        if event_type is not None and event_type not in (*EVENT_LABELS, PENDING_TYPE):
            raise ValueError("事件类型须为官方六类、其他或待分类")
        with self.connection() as store:
            events = followup_events(store.list_events(uid, stream))
            items = [event.to_dict() for event in events if event_type is None
                     or event_type in (event.types or [event.type])]
            items = enrich_events(store, uid, items, load_sender_roles(self.settings.data_dir),
                                  load_sender_kinds(self.settings.data_dir))
        return {"items": items, "total": len(items)}

    def event(self, event_id, uid=None, stream=None):
        """详情也进行客户与流校验，不能仅凭猜测的事件 ID 越过隔离。"""
        uid, stream = self.scope(uid, stream)
        with self.connection() as store:
            event = next((event for event in store.list_events(uid, stream) if event.event_id == event_id), None)
            if event is None:
                return None
            return enrich_events(store, uid, [event.to_dict()], load_sender_roles(self.settings.data_dir),
                                 load_sender_kinds(self.settings.data_dir))[0]

    def event_changes(self, event_id, uid, stream, runs, offset=0, execution_offset=0, *, include_details=True):
        """变化面板只读已保存事件与批次，不补查当前消息或私有模型日志。"""
        uid, stream = self.scope(uid, stream)
        with self.connection() as store:
            event = next((e for e in store.list_events(uid, stream) if e.event_id == event_id), None)
        if event is None:
            return None
        value = event.to_dict()
        attempts = self.attempts.list_for_event_changes(uid, stream, event_id,
                {r["attempt_id"] for r in value.get("updated_by", []) if r.get("attempt_id")}, execution_offset)
        result = event_changes(value, runs.list_for_event_changes(uid, stream, offset, event_id=event_id), offset,
                               set(attempts["known_ids"]))
        result.update(execution_changes(value, result["revisions"], attempts, execution_offset,
                                        include_details=include_details))
        result["details_included"] = include_details
        if not include_details:
            # 时间线仍保留修订和提交核对结果；旧批次的大段依据在展开详情后读取。
            for batch in result["batches"]:
                batch["attributions"] = []
        return result
