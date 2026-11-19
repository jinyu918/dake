"""MongoDB 产品属性配置中心与事件多维聚合分析仓储（含单元测试内存替身）。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..config import Settings
from ..domain.attribute_schemas import (
    UNIT_FAMILY_META,
    seed_catalog_copy,
    validate_field_definition,
    validate_product_definition,
)
from ..domain.models import TZ

REGISTRY_DOC_ID = "attribute_schema_registry"


class SchemaConflictError(RuntimeError):
    """多人并发修改产品或属性配置时的乐观锁版本冲突。"""


class AnalyticsStorageError(RuntimeError):
    """脱敏的 MongoDB 存储访问异常。"""


def _now_iso() -> str:
    return datetime.now(TZ).isoformat()


def scope_bucket_key(scope_type: str, scope_key: str) -> str:
    """生成不含特殊符号的 MongoDB 二级属性命名空间键（如 product_ai_maas、type_排障）。"""
    return f"product_{scope_key}" if scope_type == "product" else f"type_{scope_key}"


def _unit_families_payload() -> list[dict[str, str]]:
    return [
        {"family": key, "label": meta["label"], "default_unit": meta["default_unit"]}
        for key, meta in UNIT_FAMILY_META.items()
    ]


def merge_event_props(
    existing_props: Mapping[str, Mapping[str, Any]] | None,
    existing_evidence: Sequence[Mapping[str, Any]] | None,
    new_props: Mapping[str, Mapping[str, Any]] | None,
    new_evidence: Sequence[Mapping[str, Any]] | None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """按作用域和 field_key 增量合并强类型属性与原文证据，新值覆盖同名旧值，保留未重提字段。"""
    merged_props: dict[str, dict[str, Any]] = {
        scope: dict(values) for scope, values in (existing_props or {}).items() if isinstance(values, Mapping)
    }
    for scope, values in (new_props or {}).items():
        if not isinstance(values, Mapping):
            continue
        bucket = merged_props.setdefault(scope, {})
        for k, v in values.items():
            if v is not None:
                bucket[k] = v

    evidence_by_id: dict[str, dict[str, Any]] = {}
    for item in existing_evidence or []:
        if isinstance(item, Mapping) and item.get("field_id"):
            evidence_by_id[str(item["field_id"])] = deepcopy(dict(item))
    for item in new_evidence or []:
        if isinstance(item, Mapping) and item.get("field_id"):
            evidence_by_id[str(item["field_id"])] = deepcopy(dict(item))

    return merged_props, list(evidence_by_id.values())


def _clean_doc(doc: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(doc)
    result.pop("_id", None)
    return result


class MemoryAnalyticsStore:
    """无外部数据库依赖的内存版属性配置与聚合仓储，供离线测试与契约导出使用。"""

    def __init__(self) -> None:
        products, fields = seed_catalog_copy()
        self.version = 1
        self.updated_at = _now_iso()
        self.products: dict[str, dict[str, Any]] = {p["product_key"]: p for p in products}
        self.fields: dict[str, dict[str, Any]] = {f["field_id"]: f for f in fields}
        self.events: dict[str, dict[str, Any]] = {}

    def list_registry(self) -> dict[str, Any]:
        products = sorted(
            deepcopy(list(self.products.values())),
            key=lambda p: (int(p.get("sort_order", 100)), p["product_key"]),
        )
        fields = sorted(
            deepcopy(list(self.fields.values())),
            key=lambda f: (
                0 if f["scope_type"] == "product" else 1,
                f["scope_key"],
                int(f.get("sort_order", 100)),
                f["field_key"],
            ),
        )
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "products": products,
            "fields": fields,
            "unit_families": _unit_families_payload(),
        }

    def save_product(self, raw: Mapping[str, Any], expected_version: int) -> dict[str, Any]:
        if expected_version != self.version:
            raise SchemaConflictError("配置已被其他会话更新，请刷新后重试")
        existing = self.products.get(str(raw.get("product_key") or "").strip())
        payload = dict(raw)
        if existing is not None:
            payload["builtin"] = existing.get("builtin", False)
        validated = validate_product_definition(payload)
        self.products[validated["product_key"]] = validated
        self.version += 1
        self.updated_at = _now_iso()
        return self.list_registry()

    def save_field(
        self,
        raw: Mapping[str, Any],
        expected_version: int,
        field_id: str | None = None,
    ) -> dict[str, Any]:
        if expected_version != self.version:
            raise SchemaConflictError("配置已被其他会话更新，请刷新后重试")
        existing = self.fields.get(field_id) if field_id else None
        if field_id and existing is None:
            raise ValueError("待编辑的字段不存在")
        candidate_id = (
            f"{str(raw.get('scope_type') or '').strip()}:"
            f"{str(raw.get('scope_key') or '').strip()}:"
            f"{str(raw.get('field_key') or '').strip()}"
        )
        if not field_id and candidate_id in self.fields:
            raise ValueError("相同范围下该字段编码已存在，请直接编辑现有字段或更换编码")
        validated = validate_field_definition(
            raw,
            valid_product_keys=set(self.products.keys()),
            existing=existing,
        )
        self.fields[validated["field_id"]] = validated
        self.version += 1
        self.updated_at = _now_iso()
        return self.list_registry()

    def delete_field(self, field_id: str, expected_version: int) -> dict[str, Any]:
        if expected_version != self.version:
            raise SchemaConflictError("配置已被其他会话更新，请刷新后重试")
        existing = self.fields.get(field_id)
        if existing is None:
            raise ValueError("待删除的字段不存在")
        if existing.get("builtin"):
            raise ValueError("系统预置字段请使用停用功能，仅自定义新增字段支持删除")
        del self.fields[field_id]
        self.version += 1
        self.updated_at = _now_iso()
        return self.list_registry()

    def delete_product(self, product_key: str, expected_version: int) -> dict[str, Any]:
        if expected_version != self.version:
            raise SchemaConflictError("配置已被其他会话更新，请刷新后重试")
        existing = self.products.get(product_key)
        if existing is None:
            raise ValueError("待删除的产品不存在")
        if existing.get("builtin"):
            raise ValueError("系统预置产品请使用停用功能，仅自定义新增产品支持删除")
        del self.products[product_key]
        for fid in [k for k, v in self.fields.items() if v["scope_type"] == "product" and v["scope_key"] == product_key]:
            del self.fields[fid]
        self.version += 1
        self.updated_at = _now_iso()
        return self.list_registry()

    def upsert_event_document(self, doc: Mapping[str, Any], *, replace_props: bool = False) -> None:
        doc_id = f"{doc['stream']}:{doc['event_id']}"
        prev = self.events.get(doc_id)
        payload = deepcopy(dict(doc))
        if prev is not None and not replace_props:
            merged_props, merged_ev = merge_event_props(
                prev.get("props"),
                prev.get("prop_evidence"),
                payload.get("props"),
                payload.get("prop_evidence"),
            )
            payload["props"] = merged_props
            payload["prop_evidence"] = merged_ev
            if not payload.get("products") and prev.get("products"):
                payload["products"] = deepcopy(prev["products"])
                payload["primary_product"] = prev.get("primary_product")
        payload["_id"] = doc_id
        payload["synced_at"] = _now_iso()
        self.events[doc_id] = payload

    def get_event_documents(self, stream: str, event_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for eid in event_ids:
            doc = self.events.get(f"{stream}:{eid}")
            if doc is not None:
                result[eid] = _clean_doc(doc)
        return result

    def aggregate_by_product(
        self,
        *,
        stream: str,
        product_key: str = "",
        event_type: str = "",
    ) -> dict[str, Any]:
        docs = [d for d in self.events.values() if d.get("stream") == stream]
        return _compute_python_aggregation(
            docs=docs,
            registry=self.list_registry(),
            product_key=product_key,
            event_type=event_type,
        )


def _compute_python_aggregation(
    *,
    docs: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
    product_key: str,
    event_type: str,
) -> dict[str, Any]:
    """用于 MemoryAnalyticsStore 的等价内存聚合实现，保持与 MongoDB Pipeline 完全一致的输出结构。"""
    type_filtered = [
        d for d in docs if not event_type or event_type in (d.get("types") or [d.get("primary_type")])
    ]

    product_summaries: list[dict[str, Any]] = []
    for prod in registry["products"]:
        pkey = prod["product_key"]
        matched = [d for d in type_filtered if pkey in (d.get("products") or [])]
        uids = {d.get("uid") for d in matched if d.get("uid")}
        high_prio = sum(1 for d in matched if d.get("priority") in ("P0", "P1"))
        product_summaries.append(
            {
                "product_key": pkey,
                "name": prod["name"],
                "enabled": prod["enabled"],
                "event_count": len(matched),
                "customer_count": len(uids),
                "high_priority_count": high_prio,
            }
        )
    unclassified = [d for d in type_filtered if not (d.get("products") or [])]
    if unclassified:
        product_summaries.append(
            {
                "product_key": "unclassified",
                "name": "待识别产品",
                "enabled": True,
                "event_count": len(unclassified),
                "customer_count": len({d.get("uid") for d in unclassified if d.get("uid")}),
                "high_priority_count": sum(1 for d in unclassified if d.get("priority") in ("P0", "P1")),
            }
        )

    scoped_docs = [
        d
        for d in type_filtered
        if not product_key
        or (product_key == "unclassified" and not (d.get("products") or []))
        or product_key in (d.get("products") or [])
    ]

    numeric_metrics: list[dict[str, Any]] = []
    term_distributions: list[dict[str, Any]] = []

    for fdef in registry["fields"]:
        if not fdef.get("enabled", True):
            continue
        if fdef["scope_type"] == "product" and product_key and fdef["scope_key"] != product_key:
            continue
        if fdef["scope_type"] == "event_type" and event_type and fdef["scope_key"] != event_type:
            continue
        bucket_key = scope_bucket_key(fdef["scope_type"], fdef["scope_key"])
        fkey = fdef["field_key"]
        values = [
            val
            for d in scoped_docs
            if (val := (d.get("props") or {}).get(bucket_key, {}).get(fkey)) is not None
        ]
        if not values:
            continue
        if fdef["data_type"] == "number":
            nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if nums:
                total = round(sum(nums), 4)
                avg_val = round(total / len(nums), 4)
                max_val = round(max(nums), 4)
                numeric_metrics.append(
                    {
                        "field_id": fdef["field_id"],
                        "scope_type": fdef["scope_type"],
                        "scope_key": fdef["scope_key"],
                        "field_key": fkey,
                        "label": fdef["label"],
                        "unit": fdef["unit"],
                        "agg_ops": fdef["agg_ops"],
                        "hit_count": len(nums),
                        "sum_value": total,
                        "avg_value": avg_val,
                        "max_value": max_val,
                    }
                )
        else:
            counts: dict[str, int] = {}
            for v in values:
                key_str = "是" if v is True else ("否" if v is False else str(v))
                counts[key_str] = counts.get(key_str, 0) + 1
            buckets = [
                {"value": k, "count": c}
                for k, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
            ]
            term_distributions.append(
                {
                    "field_id": fdef["field_id"],
                    "scope_type": fdef["scope_type"],
                    "scope_key": fdef["scope_key"],
                    "field_key": fkey,
                    "label": fdef["label"],
                    "hit_count": len(values),
                    "buckets": buckets,
                }
            )

    sorted_events = sorted(scoped_docs, key=lambda d: str(d.get("end_time") or ""), reverse=True)[:30]
    event_items = [
        {
            "event_id": str(d["event_id"]),
            "uid": str(d["uid"]),
            "customer_level": d.get("customer_level"),
            "summary": str(d.get("summary") or ""),
            "primary_type": str(d.get("primary_type") or ""),
            "types": list(d.get("types") or []),
            "priority": str(d.get("priority") or "待评估"),
            "lifecycle_status": str(d.get("lifecycle_status") or "状态未明"),
            "primary_product": d.get("primary_product"),
            "products": list(d.get("products") or []),
            "end_time": str(d.get("end_time") or ""),
            "props": deepcopy(dict(d.get("props") or {})),
            "prop_evidence": deepcopy(list(d.get("prop_evidence") or [])),
        }
        for d in sorted_events
    ]

    term_distributions.sort(
        key=lambda item: (0 if item["scope_type"] == "product" else 1, -int(item["hit_count"]))
    )
    numeric_metrics.sort(
        key=lambda item: (0 if item["scope_type"] == "product" else 1, -int(item["hit_count"]))
    )

    return {
        "total_events": len(scoped_docs),
        "total_customers": len({d.get("uid") for d in scoped_docs if d.get("uid")}),
        "selected_product": product_key or None,
        "selected_event_type": event_type or None,
        "product_summaries": product_summaries,
        "numeric_metrics": numeric_metrics,
        "term_distributions": term_distributions,
        "events": event_items,
    }


class MongoAnalyticsStore:
    """基于独立 Docker MongoDB 的动态属性配置与 Aggregation Pipeline 分析仓储。"""

    def __init__(self, settings: Settings, *, server_selection_timeout_ms: int = 2500) -> None:
        self.settings = settings
        self.timeout_ms = server_selection_timeout_ms
        self._initialized = False

    def _client(self) -> MongoClient:
        return MongoClient(
            self.settings.mongo_uri,
            serverSelectionTimeoutMS=self.timeout_ms,
            connectTimeoutMS=self.timeout_ms,
            socketTimeoutMS=10_000,
            tz_aware=True,
        )

    def _ensure_ready(self, db) -> None:
        if self._initialized:
            return
        db.product_catalog.create_index([("product_key", ASCENDING)], unique=True)
        db.attribute_schemas.create_index([("field_id", ASCENDING)], unique=True)
        db.events_analytics.create_index(
            [("stream", ASCENDING), ("products", ASCENDING), ("end_time", DESCENDING)]
        )
        db.events_analytics.create_index([("stream", ASCENDING), ("uid", ASCENDING)])
        db.events_analytics.create_index([("props.$**", ASCENDING)])

        products, fields = seed_catalog_copy()
        now = _now_iso()
        db.schema_meta.update_one(
            {"_id": REGISTRY_DOC_ID},
            {"$setOnInsert": {"version": 1, "updated_at": now}},
            upsert=True,
        )
        if products:
            db.product_catalog.bulk_write(
                [
                    UpdateOne(
                        {"product_key": item["product_key"]},
                        {"$setOnInsert": {**item, "created_at": now}},
                        upsert=True,
                    )
                    for item in products
                ],
                ordered=False,
            )
        if fields:
            db.attribute_schemas.bulk_write(
                [
                    UpdateOne(
                        {"field_id": item["field_id"]},
                        {"$setOnInsert": {**item, "created_at": now}},
                        upsert=True,
                    )
                    for item in fields
                ],
                ordered=False,
            )
        self._initialized = True

    def ping(self) -> bool:
        """检查本地 MongoDB 是否就绪可用。"""
        try:
            with self._client() as client:
                client.admin.command("ping")
                return True
        except PyMongoError:
            return False

    def list_registry(self) -> dict[str, Any]:
        try:
            with self._client() as client:
                db = client[self.settings.mongo_database]
                self._ensure_ready(db)
                meta = db.schema_meta.find_one({"_id": REGISTRY_DOC_ID}) or {
                    "version": 1,
                    "updated_at": _now_iso(),
                }
                products = sorted(
                    [_clean_doc(d) for d in db.product_catalog.find()],
                    key=lambda p: (int(p.get("sort_order", 100)), p["product_key"]),
                )
                fields = sorted(
                    [_clean_doc(d) for d in db.attribute_schemas.find()],
                    key=lambda f: (
                        0 if f["scope_type"] == "product" else 1,
                        f["scope_key"],
                        int(f.get("sort_order", 100)),
                        f["field_key"],
                    ),
                )
                return {
                    "version": int(meta["version"]),
                    "updated_at": str(meta["updated_at"]),
                    "products": products,
                    "fields": fields,
                    "unit_families": _unit_families_payload(),
                }
        except PyMongoError as exc:
            raise AnalyticsStorageError("MongoDB 属性配置库暂不可用，请确认 mongo 容器已启动") from exc

    def save_product(self, raw: Mapping[str, Any], expected_version: int) -> dict[str, Any]:
        try:
            with self._client() as client:
                db = client[self.settings.mongo_database]
                self._ensure_ready(db)
                pkey = str(raw.get("product_key") or "").strip()
                existing = db.product_catalog.find_one({"product_key": pkey})
                payload = dict(raw)
                if existing is not None:
                    payload["builtin"] = existing.get("builtin", False)
                validated = validate_product_definition(payload)

                now = _now_iso()
                bumped = db.schema_meta.update_one(
                    {"_id": REGISTRY_DOC_ID, "version": expected_version},
                    {"$inc": {"version": 1}, "$set": {"updated_at": now}},
                )
                if bumped.modified_count == 0:
                    raise SchemaConflictError("配置已被其他会话更新，请刷新后重试")
                db.product_catalog.update_one(
                    {"product_key": validated["product_key"]},
                    {"$set": {**validated, "updated_at": now}},
                    upsert=True,
                )
            return self.list_registry()
        except SchemaConflictError:
            raise
        except PyMongoError as exc:
            raise AnalyticsStorageError("保存产品配置失败，请检查 MongoDB 服务状态") from exc

    def save_field(
        self,
        raw: Mapping[str, Any],
        expected_version: int,
        field_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            with self._client() as client:
                db = client[self.settings.mongo_database]
                self._ensure_ready(db)
                existing = db.attribute_schemas.find_one({"field_id": field_id}) if field_id else None
                if field_id and existing is None:
                    raise ValueError("待编辑的字段不存在")
                candidate_id = (
                    f"{str(raw.get('scope_type') or '').strip()}:"
                    f"{str(raw.get('scope_key') or '').strip()}:"
                    f"{str(raw.get('field_key') or '').strip()}"
                )
                if not field_id and db.attribute_schemas.find_one({"field_id": candidate_id}):
                    raise ValueError("相同范围下该字段编码已存在，请直接编辑现有字段或更换编码")

                valid_pkeys = {d["product_key"] for d in db.product_catalog.find({}, {"product_key": 1})}
                validated = validate_field_definition(
                    raw,
                    valid_product_keys=valid_pkeys,
                    existing=_clean_doc(existing) if existing else None,
                )

                now = _now_iso()
                bumped = db.schema_meta.update_one(
                    {"_id": REGISTRY_DOC_ID, "version": expected_version},
                    {"$inc": {"version": 1}, "$set": {"updated_at": now}},
                )
                if bumped.modified_count == 0:
                    raise SchemaConflictError("配置已被其他会话更新，请刷新后重试")
                try:
                    db.attribute_schemas.update_one(
                        {"field_id": validated["field_id"]},
                        {"$set": {**validated, "updated_at": now}},
                        upsert=True,
                    )
                except DuplicateKeyError as dup:
                    raise ValueError("该字段编码已存在") from dup
            return self.list_registry()
        except SchemaConflictError:
            raise
        except PyMongoError as exc:
            raise AnalyticsStorageError("保存属性字段配置失败，请检查 MongoDB 服务状态") from exc

    def delete_field(self, field_id: str, expected_version: int) -> dict[str, Any]:
        try:
            with self._client() as client:
                db = client[self.settings.mongo_database]
                self._ensure_ready(db)
                existing = db.attribute_schemas.find_one({"field_id": field_id})
                if existing is None:
                    raise ValueError("待删除的字段不存在")
                if existing.get("builtin"):
                    raise ValueError("系统预置字段请使用停用功能，仅自定义新增字段支持删除")
                now = _now_iso()
                bumped = db.schema_meta.update_one(
                    {"_id": REGISTRY_DOC_ID, "version": expected_version},
                    {"$inc": {"version": 1}, "$set": {"updated_at": now}},
                )
                if bumped.modified_count == 0:
                    raise SchemaConflictError("配置已被其他会话更新，请刷新后重试")
                db.attribute_schemas.delete_one({"field_id": field_id})
            return self.list_registry()
        except SchemaConflictError:
            raise
        except PyMongoError as exc:
            raise AnalyticsStorageError("删除属性字段失败，请检查 MongoDB 服务状态") from exc

    def delete_product(self, product_key: str, expected_version: int) -> dict[str, Any]:
        try:
            with self._client() as client:
                db = client[self.settings.mongo_database]
                self._ensure_ready(db)
                existing = db.product_catalog.find_one({"product_key": product_key})
                if existing is None:
                    raise ValueError("待删除的产品不存在")
                if existing.get("builtin"):
                    raise ValueError("系统预置产品请使用停用功能，仅自定义新增产品支持删除")
                now = _now_iso()
                bumped = db.schema_meta.update_one(
                    {"_id": REGISTRY_DOC_ID, "version": expected_version},
                    {"$inc": {"version": 1}, "$set": {"updated_at": now}},
                )
                if bumped.modified_count == 0:
                    raise SchemaConflictError("配置已被其他会话更新，请刷新后重试")
                db.product_catalog.delete_one({"product_key": product_key})
                db.attribute_schemas.delete_many({"scope_type": "product", "scope_key": product_key})
            return self.list_registry()
        except SchemaConflictError:
            raise
        except PyMongoError as exc:
            raise AnalyticsStorageError("删除产品配置失败，请检查 MongoDB 服务状态") from exc

    def upsert_event_document(self, doc: Mapping[str, Any], *, replace_props: bool = False) -> None:
        doc_id = f"{doc['stream']}:{doc['event_id']}"
        try:
            with self._client() as client:
                db = client[self.settings.mongo_database]
                self._ensure_ready(db)
                prev = db.events_analytics.find_one({"_id": doc_id})
                payload = deepcopy(dict(doc))
                if prev is not None and not replace_props:
                    merged_props, merged_ev = merge_event_props(
                        prev.get("props"),
                        prev.get("prop_evidence"),
                        payload.get("props"),
                        payload.get("prop_evidence"),
                    )
                    payload["props"] = merged_props
                    payload["prop_evidence"] = merged_ev
                    if not payload.get("products") and prev.get("products"):
                        payload["products"] = prev["products"]
                        payload["primary_product"] = prev.get("primary_product")
                payload["_id"] = doc_id
                payload["synced_at"] = _now_iso()
                db.events_analytics.replace_one({"_id": doc_id}, payload, upsert=True)
        except PyMongoError as exc:
            raise AnalyticsStorageError("同步事件结构化数据至 MongoDB 失败") from exc

    def get_event_documents(self, stream: str, event_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """批量读取事件挂载的产品与结构化指标；MongoDB 异常时优雅降级返回空字典，不阻塞主事件列表。"""
        if not event_ids:
            return {}
        ids = [f"{stream}:{eid}" for eid in event_ids]
        try:
            with self._client() as client:
                db = client[self.settings.mongo_database]
                cursor = db.events_analytics.find({"_id": {"$in": ids}})
                return {str(doc["event_id"]): _clean_doc(doc) for doc in cursor if doc.get("event_id")}
        except (PyMongoError, RuntimeError, OSError):
            return {}

    def aggregate_by_product(
        self,
        *,
        stream: str,
        product_key: str = "",
        event_type: str = "",
    ) -> dict[str, Any]:
        """利用 MongoDB 原生 Aggregation Pipeline 执行多产品分布、强类型数值求和/均值/峰值与枚举分布聚合。"""
        registry = self.list_registry()
        try:
            with self._client() as client:
                db = client[self.settings.mongo_database]
                self._ensure_ready(db)

                base_match: dict[str, Any] = {"stream": stream}
                if event_type:
                    base_match["types"] = event_type

                # 1. 各产品线全景聚合（MongoDB $facet 同时算各产品分布与未分类产品）
                prod_facets: dict[str, list[dict[str, Any]]] = {
                    "by_product": [
                        {"$match": {"products.0": {"$exists": True}}},
                        {"$unwind": "$products"},
                        {
                            "$group": {
                                "_id": "$products",
                                "event_count": {"$sum": 1},
                                "customers": {"$addToSet": "$uid"},
                                "high_priority_count": {
                                    "$sum": {"$cond": [{"$in": ["$priority", ["P0", "P1"]]}, 1, 0]}
                                },
                            }
                        },
                    ],
                    "unclassified": [
                        {"$match": {"products.0": {"$exists": False}}},
                        {
                            "$group": {
                                "_id": "unclassified",
                                "event_count": {"$sum": 1},
                                "customers": {"$addToSet": "$uid"},
                                "high_priority_count": {
                                    "$sum": {"$cond": [{"$in": ["$priority", ["P0", "P1"]]}, 1, 0]}
                                },
                            }
                        },
                    ],
                }
                overview_raw = next(
                    db.events_analytics.aggregate([{"$match": base_match}, {"$facet": prod_facets}]),
                    {"by_product": [], "unclassified": []},
                )
                by_prod_map = {row["_id"]: row for row in overview_raw.get("by_product", [])}
                product_summaries: list[dict[str, Any]] = []
                for prod in registry["products"]:
                    pkey = prod["product_key"]
                    row = by_prod_map.get(pkey, {})
                    product_summaries.append(
                        {
                            "product_key": pkey,
                            "name": prod["name"],
                            "enabled": prod["enabled"],
                            "event_count": int(row.get("event_count", 0)),
                            "customer_count": len(row.get("customers") or []),
                            "high_priority_count": int(row.get("high_priority_count", 0)),
                        }
                    )
                unclass_rows = overview_raw.get("unclassified") or []
                if unclass_rows and unclass_rows[0].get("event_count", 0) > 0:
                    u_row = unclass_rows[0]
                    product_summaries.append(
                        {
                            "product_key": "unclassified",
                            "name": "待识别产品",
                            "enabled": True,
                            "event_count": int(u_row["event_count"]),
                            "customer_count": len(u_row.get("customers") or []),
                            "high_priority_count": int(u_row.get("high_priority_count", 0)),
                        }
                    )

                # 2. 按选定产品过滤后，根据配置动态生成 MongoDB $facet 数值指标与分组分布管道
                scoped_match = dict(base_match)
                if product_key == "unclassified":
                    scoped_match["products.0"] = {"$exists": False}
                elif product_key:
                    scoped_match["products"] = product_key

                active_fields = [
                    fdef
                    for fdef in registry["fields"]
                    if fdef.get("enabled", True)
                    and (fdef["scope_type"] != "product" or not product_key or fdef["scope_key"] == product_key)
                    and (fdef["scope_type"] != "event_type" or not event_type or fdef["scope_key"] == event_type)
                ]

                metric_facets: dict[str, list[dict[str, Any]]] = {
                    "_totals": [
                        {
                            "$group": {
                                "_id": None,
                                "total_events": {"$sum": 1},
                                "customers": {"$addToSet": "$uid"},
                            }
                        }
                    ],
                    "_top_events": [
                        {"$sort": {"end_time": -1}},
                        {"$limit": 30},
                    ],
                }
                field_lookup: dict[str, dict[str, Any]] = {}
                for idx, fdef in enumerate(active_fields):
                    facet_key = f"f_{idx}"
                    field_lookup[facet_key] = fdef
                    bucket_key = scope_bucket_key(fdef["scope_type"], fdef["scope_key"])
                    mongo_path = f"props.{bucket_key}.{fdef['field_key']}"
                    if fdef["data_type"] == "number":
                        metric_facets[facet_key] = [
                            {"$match": {mongo_path: {"$type": "number"}}},
                            {
                                "$group": {
                                    "_id": None,
                                    "hit_count": {"$sum": 1},
                                    "sum_value": {"$sum": f"${mongo_path}"},
                                    "avg_value": {"$avg": f"${mongo_path}"},
                                    "max_value": {"$max": f"${mongo_path}"},
                                }
                            },
                        ]
                    else:
                        metric_facets[facet_key] = [
                            {"$match": {mongo_path: {"$exists": True, "$ne": None}}},
                            {"$group": {"_id": f"${mongo_path}", "count": {"$sum": 1}}},
                            {"$sort": {"count": -1, "_id": 1}},
                            {"$limit": 10},
                        ]

                agg_result = next(
                    db.events_analytics.aggregate([{"$match": scoped_match}, {"$facet": metric_facets}]),
                    {},
                )
                totals_row = (agg_result.get("_totals") or [{}])[0]
                numeric_metrics: list[dict[str, Any]] = []
                term_distributions: list[dict[str, Any]] = []

                for facet_key, fdef in field_lookup.items():
                    rows = agg_result.get(facet_key) or []
                    if not rows:
                        continue
                    if fdef["data_type"] == "number":
                        r = rows[0]
                        numeric_metrics.append(
                            {
                                "field_id": fdef["field_id"],
                                "scope_type": fdef["scope_type"],
                                "scope_key": fdef["scope_key"],
                                "field_key": fdef["field_key"],
                                "label": fdef["label"],
                                "unit": fdef["unit"],
                                "agg_ops": fdef["agg_ops"],
                                "hit_count": int(r["hit_count"]),
                                "sum_value": round(float(r["sum_value"]), 4),
                                "avg_value": round(float(r["avg_value"]), 4),
                                "max_value": round(float(r["max_value"]), 4),
                            }
                        )
                    else:
                        buckets = [
                            {
                                "value": "是"
                                if item["_id"] is True
                                else ("否" if item["_id"] is False else str(item["_id"])),
                                "count": int(item["count"]),
                            }
                            for item in rows
                        ]
                        term_distributions.append(
                            {
                                "field_id": fdef["field_id"],
                                "scope_type": fdef["scope_type"],
                                "scope_key": fdef["scope_key"],
                                "field_key": fdef["field_key"],
                                "label": fdef["label"],
                                "hit_count": sum(b["count"] for b in buckets),
                                "buckets": buckets,
                            }
                        )

                event_items = [
                    {
                        "event_id": str(d["event_id"]),
                        "uid": str(d["uid"]),
                        "customer_level": d.get("customer_level"),
                        "summary": str(d.get("summary") or ""),
                        "primary_type": str(d.get("primary_type") or ""),
                        "types": list(d.get("types") or []),
                        "priority": str(d.get("priority") or "待评估"),
                        "lifecycle_status": str(d.get("lifecycle_status") or "状态未明"),
                        "primary_product": d.get("primary_product"),
                        "products": list(d.get("products") or []),
                        "end_time": str(d.get("end_time") or ""),
                        "props": deepcopy(dict(d.get("props") or {})),
                        "prop_evidence": deepcopy(list(d.get("prop_evidence") or [])),
                    }
                    for d in (agg_result.get("_top_events") or [])
                ]

                term_distributions.sort(
                    key=lambda item: (0 if item["scope_type"] == "product" else 1, -int(item["hit_count"]))
                )
                numeric_metrics.sort(
                    key=lambda item: (0 if item["scope_type"] == "product" else 1, -int(item["hit_count"]))
                )

                return {
                    "total_events": int(totals_row.get("total_events", 0)),
                    "total_customers": len(totals_row.get("customers") or []),
                    "selected_product": product_key or None,
                    "selected_event_type": event_type or None,
                    "product_summaries": product_summaries,
                    "numeric_metrics": numeric_metrics,
                    "term_distributions": term_distributions,
                    "events": event_items,
                }
        except PyMongoError as exc:
            raise AnalyticsStorageError("执行 MongoDB 产品聚合分析失败，请检查 mongo 服务状态") from exc
