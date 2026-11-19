"""面向页面的只读查询，不读取或公开原始响应目录。"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import re

from ..analysis.providers.xhub import configured_stream
from ..domain.alert_policy import ALERT_EVENT_TYPE
from ..domain.customer_policy import customer_segment, is_super_large
from ..domain.models import EVENT_LABELS, EVENT_TYPES, PENDING_TYPE, message_order, parse_time
from ..domain.priority_history import initial_priority_basis
from ..domain.priority_policy import PRIORITIES, clear_legacy_timeout_upgrade, event_priority_sort_key
from ..domain.status_policy import LIFECYCLE_STATUSES, RESOLUTIONS, WAITING_REASONS
from ..ingestion.client import validate_uid
from ..repositories.mysql import MySQLStore, StorageError
from ..repositories.attempts import AttemptRepository
from .enrich import enrich_event, enrich_events, enrich_evidence
from .item_priority import refresh_item_priority
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

    def __init__(self, settings, store_factory=MySQLStore, *, attempts=None, analytics_store=None):
        self.settings = settings
        self.store_factory = store_factory
        self.attempts = attempts if attempts is not None else AttemptRepository(settings, store_factory)
        self.analytics_store = analytics_store

    @contextmanager
    def connection(self):
        store = self.store_factory(**self.settings.store_kwargs())
        try:
            yield store
        finally:
            store.close()

    def scope(self, uid=None, stream=None):
        """缺省读取当前版本；显式历史版本也必须按原客户、原分析流查询。"""
        uid = uid or self.settings.uid
        validate_uid(uid)
        expected = configured_stream(self.settings)
        if stream is not None and stream != expected and not self._historical_stream(stream):
            raise ValueError("客户与分析流不匹配")
        return uid, stream or expected

    def _historical_stream(self, stream):
        """真实工作台只展示正式 XHub 分析版本，隔离其他来源和实验。"""
        return self.settings.provider == "xhub" and re.fullmatch(r"xhub-v1-[0-9a-f]{20}", stream) is not None

    def config(self):
        """返回公开缺省及真实计数，不泄露数据库或网络凭据。"""
        customers = []
        stream = configured_stream(self.settings)
        with self.connection() as store:
            rows, _ = store._execute(
                "SELECT uid,COUNT(*) AS total FROM messages WHERE source='official_chat' GROUP BY uid ORDER BY uid")
            message_counts = {row["uid"]: row["total"] for row in rows}
            event_rows, _ = store._execute(
                "SELECT uid,stream,COUNT(*) AS total,MAX(end_time) AS latest_end FROM events GROUP BY uid,stream")
            event_versions = {}
            for row in event_rows:
                if row["stream"] == stream or self._historical_stream(row["stream"]):
                    event_versions.setdefault(row["uid"], {})[row["stream"]] = row
            profiles_by_uid = {}
            groups_by_uid = {}
            try:
                profile_rows, _ = store._execute(
                    "SELECT uid, level, level_state, tam, tam_b FROM customer_profiles ORDER BY uid"
                )
                for row in profile_rows:
                    profiles_by_uid[row["uid"]] = row
                group_rows, _ = store._execute(
                    "SELECT uid, group_name FROM customer_groups WHERE uid IS NOT NULL AND status='confirmed' "
                    "ORDER BY synced_at DESC, group_id"
                )
                for row in group_rows:
                    name = (row.get("group_name") or "").strip()
                    if name:
                        groups_by_uid.setdefault(row["uid"], [])
                        if name not in groups_by_uid[row["uid"]]:
                            groups_by_uid[row["uid"]].append(name)
            except StorageError:
                profiles_by_uid = {}
                groups_by_uid = {}
            official_uids = list(message_counts)
            for uid, profile in profiles_by_uid.items():
                if uid not in official_uids and customer_segment(profile.get("level"), profile.get("level_state")) == "large":
                    official_uids.append(uid)
            for uid in official_uids:
                label = f"客户 {uid}"
                message_count = message_counts.get(uid, 0)
                versions = event_versions.get(uid, {})
                current = versions.get(stream, {})
                profile = profiles_by_uid.get(uid, {})
                level = profile.get("level")
                level_state = profile.get("level_state") or "unknown"
                group_names = list(groups_by_uid.get(uid, []))
                if not group_names and message_count > 0:
                    try:
                        conv_rows, _ = store._execute(
                            "SELECT DISTINCT JSON_UNQUOTE(JSON_EXTRACT(payload,'$.conversation_name')) AS name "
                            "FROM messages WHERE uid=%s AND source=%s LIMIT 10",
                            (uid, "official_chat"),
                        )
                        group_names = [
                            r["name"].strip()
                            for r in conv_rows
                            if r.get("name") and r["name"].strip()
                        ]
                    except StorageError:
                        group_names = []
                latest_end = current.get("latest_end")
                latest_event_time = (
                    parse_time(latest_end).isoformat() if latest_end is not None else None
                )
                customers.append(
                    dict(
                        uid=uid,
                        label=label,
                        stream=stream,
                        message_count=message_count,
                        event_count=current.get("total", 0),
                        archived_event_counts={key: row["total"] for key, row in versions.items() if key != stream},
                        level=level,
                        level_state=level_state,
                        segment=customer_segment(level, level_state),
                        is_super_large=is_super_large(level, level_state),
                        tam=profile.get("tam"),
                        tam_b=profile.get("tam_b"),
                        group_count=len(group_names),
                        group_names=group_names[:8],
                        latest_event_time=latest_event_time,
                    )
                )
        end = self.settings.end
        end_text = (end - timedelta(days=1)).date().isoformat() if end.time().isoformat() == "00:00:00" else end.isoformat()
        return dict(uid=self.settings.uid, start=self.settings.start.isoformat(), end=end_text,
                    interval=duration_label(self.settings.interval),
                    context_lookback=duration_label(self.settings.context_lookback),
                    match_window=duration_label(self.settings.match_window),
                    ingest_lookback=duration_label(self.settings.ingest_lookback),
                    closed_grace=duration_label(self.settings.closed_grace),
                    event_types=list(EVENT_TYPES),
                    filter_event_types=[*EVENT_LABELS, PENDING_TYPE, ALERT_EVENT_TYPE],
                    lifecycle_statuses=list(LIFECYCLE_STATUSES),
                    waiting_reasons=list(WAITING_REASONS),
                    resolutions=list(RESOLUTIONS),
                    priorities=list(PRIORITIES),
                    provider=self.settings.provider, customers=customers)

    def alert_previews(self, uid=None, limit=100):
        if uid is not None:
            validate_uid(uid)
        if self.settings.synthetic_alert_source is None:
            return {"items": []}
        with self.connection() as store:
            return {"items": store.list_alert_previews(self.settings.synthetic_alert_source, uid, configured_stream(self.settings), limit)}

    @staticmethod
    def _project_event(store, event, profiles):
        def profile_for(uid):
            if uid not in profiles:
                reader = getattr(store, "customer_profile", None)
                profiles[uid] = reader(uid) if reader else None
            return profiles[uid]

        event = deepcopy(event)
        clear_legacy_timeout_upgrade(event)
        event.priority_detail = dict(event.priority_detail or {}, initial_basis=initial_priority_basis(event))
        try:
            refresh_item_priority(event, profile_for, projection=True)
        except StorageError:
            pass
        return event

    def events(self, uid=None, stream=None, event_type=None, status=None, priority=None):
        """返回当前事件版本，支持多标签、生命周期状态和业务优先级筛选与分层排序。"""
        uid, stream = self.scope(uid, stream)
        if event_type is not None and event_type not in (*EVENT_LABELS, PENDING_TYPE, ALERT_EVENT_TYPE):
            raise ValueError("事件类型须为官方六类、其他或待分类之一")
        if status is not None and status not in LIFECYCLE_STATUSES:
            raise ValueError("事件状态不在允许的五类生命周期状态中")
        if priority is not None and priority not in PRIORITIES:
            raise ValueError("事件优先级不在允许档位中")
        with self.connection() as store:
            events = store.list_events(uid, stream)
            profiles = {}
            items = []
            for event in events:
                event = self._project_event(store, event, profiles)
                if event_type is not None and event.type != event_type and event_type not in (event.types or []):
                    continue
                if status is not None and event.lifecycle_status != status:
                    continue
                if priority is not None and event.priority != priority:
                    continue
                items.append(event.to_dict())
            items = enrich_events(store, uid, items, load_sender_roles(self.settings.data_dir),
                                  load_sender_kinds(self.settings.data_dir))
            for item in items:
                # 旧版硬编码自由文本属性已下线，统一由 MongoDB 可配置结构化属性驱动展示。
                item["typed_attributes"] = {}
                item["attributes"] = {}
            if self.analytics_store is not None and items:
                mongo_docs = self.analytics_store.get_event_documents(
                    stream, [item["event_id"] for item in items]
                )
                for item in items:
                    mdoc = mongo_docs.get(item["event_id"])
                    if mdoc is not None:
                        item["primary_product"] = mdoc.get("primary_product")
                        item["products"] = list(mdoc.get("products") or [])
                        item["structured_props"] = dict(mdoc.get("props") or {})
                        item["structured_evidence"] = list(mdoc.get("prop_evidence") or [])
            items.sort(key=lambda item: event_priority_sort_key(item, profiles.get(uid)))
        return {"items": items, "total": len(items)}

    def event(self, event_id, uid=None, stream=None):
        """详情也进行客户与流校验，不能仅凭猜测的事件 ID 越过隔离。"""
        items = self.events(uid, stream)["items"]
        return next((event for event in items if event["event_id"] == event_id), None)

    def event_context(self, event_id: str, uid=None, stream=None, padding_minutes: int = 15, limit: int = 80):
        """只读加载事件所在群聊前后邻近的本地消息上下文，不改写事件证据或水位。"""
        uid, stream = self.scope(uid, stream)
        if padding_minutes < 1 or padding_minutes > 120 or limit < 1 or limit > 200:
            raise ValueError("前后上下文时间窗或条数超出允许范围")
        roles = load_sender_roles(self.settings.data_dir)
        kinds = load_sender_kinds(self.settings.data_dir)
        with self.connection() as store:
            event = next((e for e in store.list_events(uid, stream) if e.event_id == event_id), None)
            if event is None:
                return None
            event = self._project_event(store, event, {})
            ev_dict = event.to_dict()
            if hasattr(store, "_execute"):
                enriched_ev = enrich_events(store, uid, [ev_dict], roles, kinds)[0]
            else:
                mem_map = {
                    (m.source, m.message_id): m.to_dict()
                    for m in getattr(store, "messages", {}).values()
                    if m.uid == uid
                }
                enriched_ev = enrich_event(ev_dict, mem_map, roles, kinds)

            conv_ids = {
                item["conversation_id"]
                for item in enriched_ev.get("evidence", [])
                if item.get("conversation_id")
            }
            for raw_item in event.evidence:
                if raw_item.get("conversation_id"):
                    conv_ids.add(raw_item["conversation_id"])

            start = event.start_time - timedelta(minutes=padding_minutes)
            end = event.end_time + timedelta(minutes=padding_minutes) + timedelta(seconds=1)
            raw_messages = store.context_messages(uid, start, end)
            matched = [m for m in raw_messages if not conv_ids or m.conversation_id in conv_ids]
            evidence_pairs = {(e["source"], e["message_id"]) for e in event.evidence}

            truncated = len(matched) > limit
            if truncated:
                ev_msgs = [m for m in matched if (m.source, m.message_id) in evidence_pairs]
                other_msgs = [m for m in matched if (m.source, m.message_id) not in evidence_pairs]

                def distance_seconds(msg):
                    if msg.sent_at < event.start_time:
                        return (event.start_time - msg.sent_at).total_seconds()
                    if msg.sent_at > event.end_time:
                        return (msg.sent_at - event.end_time).total_seconds()
                    return 0.0

                other_msgs.sort(key=distance_seconds)
                budget = max(0, limit - len(ev_msgs))
                matched = sorted(ev_msgs + other_msgs[:budget], key=message_order)

            msg_payloads = {(m.source, m.message_id): m.to_dict() for m in matched}
            items = []
            for m in matched:
                base = dict(
                    source=m.source,
                    message_id=m.message_id,
                    send_time=m.sent_at.isoformat(),
                    excerpt=m.content[:160],
                    digest=m.digest,
                )
                enriched_msg = enrich_evidence(base, msg_payloads, roles, kinds)
                enriched_msg["is_evidence"] = (m.source, m.message_id) in evidence_pairs
                items.append(enriched_msg)

        return dict(
            uid=uid,
            stream=stream,
            event_id=event_id,
            window=dict(start=start.isoformat(), end=end.isoformat()),
            items=items,
            truncated=truncated,
        )

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
