"""可配置产品目录与结构化事件属性业务服务（含增量自动同步与存量事件一键补抽）。"""

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from typing import Any

from ..analysis.providers.xhub import XHubConfig, configured_stream
from ..analysis.structured_fields import XHubStructuredExtractor, heuristic_product_projection
from ..config import Settings
from ..domain.models import Event, Message, message_order
from ..repositories.mongo_analytics import MongoAnalyticsStore
from ..repositories.mysql import MySQLStore, StorageError


def _event_base_document(event: Event, customer_level: str | None = None) -> dict[str, Any]:
    """构造写入 MongoDB 分析集合的基础事件元数据。"""
    types = list(event.types or ([event.type] if event.type else []))
    return {
        "event_id": event.event_id,
        "uid": event.uid,
        "stream": event.stream,
        "customer_level": customer_level,
        "primary_type": event.type,
        "types": types,
        "topic_key": event.topic_key,
        "summary": event.summary,
        "lifecycle_status": event.lifecycle_status,
        "waiting_reason": event.waiting_reason,
        "resolution": event.resolution,
        "priority": event.priority,
        "base_priority": event.base_priority,
        "start_time": event.start_time.isoformat(),
        "end_time": event.end_time.isoformat(),
        "revision_no": len(event.updated_by),
    }


def _load_event_messages(store, event: Event) -> list[Message]:
    """从消息表精确加载事件已关联的全部原始聊天消息（按发送时间排序）。"""
    pairs = list(
        dict.fromkeys(
            (str(item.get("source") or "official_chat"), str(item["message_id"]))
            for item in (event.evidence or [])
            if isinstance(item, Mapping) and item.get("message_id")
        )
    )
    if not pairs:
        return []
    if hasattr(store, "_execute"):
        placeholders = ",".join(["(%s,%s)"] * len(pairs))
        args: list[str] = []
        for src, mid in pairs:
            args.extend([src, mid])
        rows, _ = store._execute(
            f"SELECT payload FROM messages WHERE (source, message_id) IN ({placeholders})",
            tuple(args),
        )
        msgs = []
        for row in rows:
            raw = row["payload"]
            data = raw if isinstance(raw, dict) else __import__("json").loads(raw)
            msgs.append(Message.from_dict(data))
        return sorted(msgs, key=message_order)

    mem = getattr(store, "messages", {})
    found = [mem[f"{src}:{mid}"] for src, mid in pairs if f"{src}:{mid}" in mem]
    return sorted(found, key=message_order)


class AttributeSchemaService:
    """统一封装产品目录、属性模板配置、MongoDB 聚合查询及 AI 字段补齐。"""

    def __init__(
        self,
        settings: Settings,
        *,
        store_factory=MySQLStore,
        analytics_store=None,
        extractor_factory: Callable[[Settings], XHubStructuredExtractor] | None = None,
    ) -> None:
        self.settings = settings
        self.store_factory = store_factory
        self.analytics = analytics_store or MongoAnalyticsStore(settings)
        self._extractor_factory = extractor_factory

    @contextmanager
    def _mysql_connection(self):
        store = self.store_factory(**self.settings.store_kwargs())
        try:
            yield store
        finally:
            store.close()

    def _create_extractor(self) -> XHubStructuredExtractor:
        if self._extractor_factory is not None:
            return self._extractor_factory(self.settings)
        cfg = XHubConfig.load(
            self.settings.llm_model,
            max_tokens=min(self.settings.llm_max_tokens, 4096),
            timeout_seconds=self.settings.llm_timeout_seconds,
        )
        return XHubStructuredExtractor(cfg, self.settings)

    def list_registry(self) -> dict[str, Any]:
        """获取当前完整的产品目录、结构化字段定义与量纲换算元数据。"""
        return self.analytics.list_registry()

    def save_product(self, product: Mapping[str, Any], expected_version: int) -> dict[str, Any]:
        """新增或修改一条七牛产品种类配置。"""
        return self.analytics.save_product(product, expected_version)

    def save_field(
        self,
        field: Mapping[str, Any],
        expected_version: int,
        field_id: str | None = None,
    ) -> dict[str, Any]:
        """新增或修改一个结构化属性字段定义。"""
        return self.analytics.save_field(field, expected_version, field_id=field_id)

    def delete_field(self, field_id: str, expected_version: int) -> dict[str, Any]:
        """删除一个自定义结构化字段定义（系统预置字段禁止物理删除，仅可停用）。"""
        return self.analytics.delete_field(field_id, expected_version)

    def delete_product(self, product_key: str, expected_version: int) -> dict[str, Any]:
        """删除一个自定义产品目录配置及其下属自定义字段。"""
        return self.analytics.delete_product(product_key, expected_version)

    def product_analytics(
        self,
        *,
        stream: str | None = None,
        product_key: str = "",
        event_type: str = "",
    ) -> dict[str, Any]:
        """按分析流、产品种类与事件类型执行 MongoDB 聚合汇总查询。"""
        target_stream = stream or configured_stream(self.settings)
        return self.analytics.aggregate_by_product(
            stream=target_stream,
            product_key=product_key.strip(),
            event_type=event_type.strip(),
        )

    def sync_committed_events(
        self,
        uid: str,
        events: Sequence[Event],
        recent_messages: Sequence[Message] = (),
        *,
        customer_level: str | None = None,
        use_llm: bool = True,
    ) -> None:
        """在增量窗口成功提交后，自动提取本轮更新事件的结构化指标并幂等投射至 MongoDB。"""
        if not events:
            return
        registry = self.analytics.list_registry()
        extractor = None
        can_use_llm = use_llm and self.settings.provider == "xhub"
        try:
            if can_use_llm:
                extractor = self._create_extractor()
            recent_by_key = {m.key: m for m in recent_messages}
            for event in events:
                ev_keys = [
                    f"{item.get('source', 'official_chat')}:{item['message_id']}"
                    for item in (event.evidence or [])
                    if isinstance(item, Mapping) and item.get("message_id")
                ]
                msgs = [recent_by_key[k] for k in ev_keys if k in recent_by_key]
                if extractor is not None:
                    structured = extractor.extract_for_event(event, msgs, registry)
                else:
                    structured = heuristic_product_projection(event, msgs, registry)
                doc = {**_event_base_document(event, customer_level), **structured}
                self.analytics.upsert_event_document(doc, replace_props=False)
        finally:
            if extractor is not None:
                extractor.close()

    def backfill(
        self,
        *,
        uid: str = "",
        product_key: str = "",
        event_type: str = "",
        only_missing: bool = False,
        limit: int = 120,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        """扫描本地已落库的存量事件，按最新配置由 AI 自动补抽结构化字段并写入 MongoDB。"""
        if not 1 <= limit <= 500:
            raise ValueError("单次补齐事件上限须在 1～500 之间")

        stream = configured_stream(self.settings)
        registry = self.analytics.list_registry()

        with self._mysql_connection() as store:
            target_events: list[Event] = []
            levels_by_uid: dict[str, str | None] = {}
            if hasattr(store, "_execute"):
                try:
                    p_rows, _ = store._execute(
                        "SELECT uid, level, level_state FROM customer_profiles"
                    )
                    for r in p_rows:
                        if r.get("level_state") == "fresh":
                            levels_by_uid[str(r["uid"])] = r.get("level")
                except StorageError:
                    levels_by_uid = {}

                if uid.strip():
                    target_events = store.list_events(uid.strip(), stream)
                else:
                    uid_rows, _ = store._execute(
                        "SELECT DISTINCT uid FROM events WHERE stream=%s ORDER BY uid",
                        (stream,),
                    )
                    for r in uid_rows:
                        target_events.extend(store.list_events(str(r["uid"]), stream))
            else:
                all_ev = list(getattr(store, "events", {}).values())
                target_events = [
                    e for e in all_ev if e.stream == stream and (not uid.strip() or e.uid == uid.strip())
                ]

            if event_type.strip():
                et = event_type.strip()
                target_events = [
                    e for e in target_events if e.type == et or et in (e.types or [])
                ]

            target_events.sort(key=lambda e: e.end_time, reverse=True)
            existing_docs = self.analytics.get_event_documents(
                stream, [e.event_id for e in target_events]
            )

            filtered_events: list[Event] = []
            for ev in target_events:
                existing = existing_docs.get(ev.event_id)
                if product_key.strip() and existing is not None:
                    prods = existing.get("products") or []
                    if prods and product_key.strip() not in prods:
                        continue
                if only_missing and existing is not None and existing.get("props"):
                    continue
                filtered_events.append(ev)
                if len(filtered_events) >= limit:
                    break

            work_items = [
                (ev, _load_event_messages(store, ev), levels_by_uid.get(ev.uid))
                for ev in filtered_events
            ]

        can_use_llm = use_llm and self.settings.provider == "xhub"
        updated_count = 0
        llm_count = 0
        failed_count = 0

        def _process_one(item: tuple[Event, list[Message], str | None]) -> tuple[dict[str, Any], bool]:
            ev, msgs, level = item
            if can_use_llm:
                ext = self._create_extractor()
                try:
                    structured = ext.extract_for_event(ev, msgs, registry)
                    return {**_event_base_document(ev, level), **structured}, True
                finally:
                    ext.close()
            structured = heuristic_product_projection(ev, msgs, registry)
            return {**_event_base_document(ev, level), **structured}, False

        max_workers = 4 if can_use_llm and len(work_items) > 1 else 1
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_process_one, item) for item in work_items]
            for fut in as_completed(futures):
                try:
                    doc, used_llm = fut.result()
                    self.analytics.upsert_event_document(doc, replace_props=False)
                    updated_count += 1
                    if used_llm:
                        llm_count += 1
                except Exception:
                    failed_count += 1

        return {
            "stream": stream,
            "scanned": len(work_items),
            "updated": updated_count,
            "llm_enriched": llm_count,
            "failed": failed_count,
            "schema_version": int(registry["version"]),
        }
