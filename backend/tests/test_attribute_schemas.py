"""可配置产品目录、强类型属性校验、量纲换算、AI 提取校验与内存聚合仓储测试。"""

import json

import httpx
import pytest

from dake.analysis.providers.xhub import XHubConfig
from dake.analysis.structured_fields import (
    XHubStructuredExtractor,
    validate_and_normalize_structured_output,
)
from dake.config import Settings, bound
from dake.domain.attribute_schemas import (
    match_products_by_keywords,
    normalize_structured_value,
    parse_number_with_unit,
    validate_field_definition,
)
from dake.domain.models import Event, Message
from dake.repositories.mongo_analytics import MemoryAnalyticsStore, SchemaConflictError
from dake.services.attribute_schemas import AttributeSchemaService


def test_unit_normalizer_handles_qiniu_colloquial_units_deterministically():
    assert parse_number_with_unit("500万 TPM", "count_scale") == 5_000_000
    assert parse_number_with_unit("1.5w RPM", "count_scale") == 15_000
    assert parse_number_with_unit("5000万 token", "million_tokens") == 50.0
    assert parse_number_with_unit("2亿", "million_tokens") == 200.0
    assert parse_number_with_unit("20Gbps", "bandwidth_mbps") == 20_000.0
    assert parse_number_with_unit("500Mbps", "bandwidth_mbps") == 500.0
    assert parse_number_with_unit("2PB", "storage_tb") == 2048.0
    assert parse_number_with_unit("512GB", "storage_tb") == 0.5
    assert parse_number_with_unit("12万", "currency_cny") == 120_000.0
    assert parse_number_with_unit("3.5s", "duration_ms") == 3500.0


def test_field_immutability_guard_blocks_type_or_key_mutation():
    base = validate_field_definition(
        {
            "scope_type": "product",
            "scope_key": "ai_maas",
            "field_key": "sandbox_concurrency",
            "label": "沙箱并发数",
            "data_type": "number",
            "unit": "个",
            "unit_family": "count_scale",
            "agg_ops": ["sum", "max"],
            "extraction_hint": "明确提及沙箱实例并发数时提取",
        },
        valid_product_keys={"ai_maas"},
    )
    assert normalize_structured_value(base, "3千") == 3000

    # 允许修改中文名、提取提示词和启停状态
    updated = validate_field_definition(
        {**base, "label": "Agent沙箱并发上限", "enabled": False},
        valid_product_keys={"ai_maas"},
        existing=base,
    )
    assert updated["label"] == "Agent沙箱并发上限" and updated["enabled"] is False

    # 禁止原地更改数据类型，防止 MongoDB 数值聚合混入字符串
    with pytest.raises(ValueError, match="不能更改数据类型"):
        validate_field_definition(
            {**base, "data_type": "string"},
            valid_product_keys={"ai_maas"},
            existing=base,
        )


def test_registry_crud_optimistic_locking_and_aggregation():
    store = MemoryAnalyticsStore()
    reg = store.list_registry()
    assert reg["version"] == 1
    product_keys = {p["product_key"] for p in reg["products"]}
    assert {"ai_maas", "kodo", "cdn_dcdn", "rtc_media", "compute_gpu", "cert_sms_ops"} <= product_keys

    # 新增自定义产品与自定义字段
    reg2 = store.save_product(
        {
            "product_key": "edge_kv",
            "name": "边缘键值存储",
            "description": "合成测试边缘 KV 产品",
            "keywords": ["edgekv", "边缘kv"],
            "sort_order": 80,
            "enabled": True,
        },
        expected_version=reg["version"],
    )
    assert reg2["version"] == 2
    with pytest.raises(SchemaConflictError):
        store.save_product(
            {"product_key": "edge_kv", "name": "冲突更新", "keywords": []},
            expected_version=1,
        )

    reg3 = store.save_field(
        {
            "scope_type": "product",
            "scope_key": "ai_maas",
            "field_key": "context_window_k",
            "label": "上下文窗口长度",
            "data_type": "number",
            "unit": "K",
            "unit_family": "none",
            "agg_ops": ["max", "avg"],
            "extraction_hint": "提取客户明确要求的上下文长度 K 数",
        },
        expected_version=reg2["version"],
    )
    assert reg3["version"] == 3

    # 写入两条合成事件文档验证多维聚合
    store.upsert_event_document(
        {
            "event_id": "evt-syn-1",
            "uid": "uid-syn-a",
            "stream": "stream-syn",
            "customer_level": "L10",
            "primary_type": "资源需求",
            "types": ["资源需求"],
            "priority": "P1",
            "lifecycle_status": "跟进中",
            "primary_product": "ai_maas",
            "products": ["ai_maas"],
            "summary": "申请调高合成模型 TPM 与 RPM",
            "end_time": "2026-11-18T10:00:00+08:00",
            "props": {
                "product_ai_maas": {
                    "model_name": "deepseek-v3",
                    "tpm_quota": 5_000_000,
                    "rpm_quota": 3_000,
                }
            },
            "prop_evidence": [],
        }
    )
    store.upsert_event_document(
        {
            "event_id": "evt-syn-2",
            "uid": "uid-syn-b",
            "stream": "stream-syn",
            "customer_level": "L8",
            "primary_type": "排障",
            "types": ["排障", "资源需求"],
            "priority": "P2",
            "lifecycle_status": "跟进中",
            "primary_product": "ai_maas",
            "products": ["ai_maas"],
            "summary": "合成模型触发 429 限流申请提额",
            "end_time": "2026-11-18T11:00:00+08:00",
            "props": {
                "product_ai_maas": {
                    "model_name": "deepseek-v3",
                    "tpm_quota": 3_000_000,
                    "ai_issue_kind": "429限流或配额不足",
                }
            },
            "prop_evidence": [],
        }
    )

    agg = store.aggregate_by_product(stream="stream-syn", product_key="ai_maas")
    assert agg["total_events"] == 2 and agg["total_customers"] == 2
    tpm_metric = next(m for m in agg["numeric_metrics"] if m["field_key"] == "tpm_quota")
    assert tpm_metric["hit_count"] == 2
    assert tpm_metric["sum_value"] == 8_000_000
    assert tpm_metric["avg_value"] == 4_000_000
    assert tpm_metric["max_value"] == 5_000_000

    model_dist = next(t for t in agg["term_distributions"] if t["field_key"] == "model_name")
    assert model_dist["buckets"] == [{"value": "deepseek-v3", "count": 2}]

    # 预置字段禁止物理删除，自定义字段与自定义产品可删除
    with pytest.raises(ValueError, match="系统预置字段"):
        store.delete_field("product:ai_maas:tpm_quota", expected_version=reg3["version"])
    reg4 = store.delete_field("product:ai_maas:context_window_k", expected_version=reg3["version"])
    assert "product:ai_maas:context_window_k" not in {f["field_id"] for f in reg4["fields"]}
    reg5 = store.delete_product("edge_kv", expected_version=reg4["version"])
    assert "edge_kv" not in {p["product_key"] for p in reg5["products"]}


def test_structured_extractor_verifies_verbatim_quotes_and_drops_hallucinations(tmp_path):
    store = MemoryAnalyticsStore()
    registry = store.list_registry()
    msg_text = "合成客户反馈 deepseek-v3 接口返回 429，申请将 TPM 提升到 500万，RPM 提升到 2000。"
    raw_output = json.dumps(
        {
            "products": ["ai_maas", "nonexistent_prod"],
            "fields": [
                {
                    "field_id": "product:ai_maas:model_name",
                    "raw_value": "deepseek-v3",
                    "numeric_value": None,
                    "bool_value": None,
                    "message_id": "official_chat:syn-1",
                    "quote": "反馈 deepseek-v3 接口返回 429",
                },
                {
                    "field_id": "product:ai_maas:tpm_quota",
                    "raw_value": "500万",
                    "numeric_value": 5_000_000,
                    "bool_value": None,
                    "message_id": "official_chat:syn-1",
                    "quote": "申请将 TPM 提升到 500万",
                },
                {
                    "field_id": "product:ai_maas:token_usage_m",
                    "raw_value": "9999万",
                    "numeric_value": 99.99,
                    "bool_value": None,
                    "message_id": "official_chat:syn-1",
                    "quote": "这段话在原文里根本不存在应当被拦截丢弃",
                },
            ],
        },
        ensure_ascii=False,
    )
    normalized = validate_and_normalize_structured_output(
        raw_output,
        registry=registry,
        msg_by_id={"official_chat:syn-1": msg_text},
    )
    assert normalized["products"] == ["ai_maas"]
    assert normalized["props"]["product_ai_maas"] == {
        "model_name": "deepseek-v3",
        "tpm_quota": 5_000_000,
    }
    assert "token_usage_m" not in normalized["props"]["product_ai_maas"]
    assert len(normalized["prop_evidence"]) == 2


def test_service_backfill_enriches_stored_events_with_mock_xhub(tmp_path):
    class FakeMySQL:
        def __init__(self, **_kwargs):
            self.msg = Message(
                uid="syn-uid-1",
                message_id="m-101",
                sent_at=bound("2026-11-18T09:00:00+08:00"),
                content="麻烦帮查下合成域名 cdn.syn.example 报 502，峰值带宽大概 15Gbps。",
                source="official_chat",
            )
            self.messages = {self.msg.key: self.msg}
            self.ev = Event(
                event_id="evt-syn-cdn",
                uid="syn-uid-1",
                stream="xhub-v1-33a5598942ac1baebd6d",
                type="排障",
                types=["排障"],
                topic_key="CDN/cdn.syn.example/502报错",
                start_time=self.msg.sent_at,
                end_time=self.msg.sent_at,
                status="跟进中",
                lifecycle_status="跟进中",
                severity="P2",
                priority="P2",
                base_priority="P2",
                summary="排查合成域名 502 报错",
                created_on="2026-11-18",
                evidence=[{"source": "official_chat", "message_id": "m-101", "quote": self.msg.content}],
            )
            self.events = {self.ev.event_id: self.ev}

        def close(self):
            pass

    def mock_reply(_req):
        body = {
            "products": ["cdn_dcdn"],
            "fields": [
                {
                    "field_id": "product:cdn_dcdn:accel_domain",
                    "raw_value": "cdn.syn.example",
                    "numeric_value": None,
                    "bool_value": None,
                    "message_id": "official_chat:m-101",
                    "quote": "合成域名 cdn.syn.example 报 502",
                },
                {
                    "field_id": "product:cdn_dcdn:bandwidth_mbps",
                    "raw_value": "15Gbps",
                    "numeric_value": 15_000,
                    "bool_value": None,
                    "message_id": "official_chat:m-101",
                    "quote": "峰值带宽大概 15Gbps",
                },
                {
                    "field_id": "product:cdn_dcdn:http_status_code",
                    "raw_value": "502",
                    "numeric_value": None,
                    "bool_value": None,
                    "message_id": "official_chat:m-101",
                    "quote": "合成域名 cdn.syn.example 报 502",
                },
            ],
        }
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body, ensure_ascii=False)}}]},
        )

    settings = Settings(provider="xhub", uid="syn-uid-1", data_dir=tmp_path)
    analytics = MemoryAnalyticsStore()
    service = AttributeSchemaService(
        settings,
        store_factory=FakeMySQL,
        analytics_store=analytics,
        extractor_factory=lambda s: XHubStructuredExtractor(
            XHubConfig(model=s.llm_model, api_key="syn-key"),
            s,
            transport=httpx.MockTransport(mock_reply),
        ),
    )
    res = service.backfill(use_llm=True)
    assert res["scanned"] == 1 and res["updated"] == 1 and res["llm_enriched"] == 1
    agg = service.product_analytics(product_key="cdn_dcdn")
    assert agg["total_events"] == 1
    bw = next(m for m in agg["numeric_metrics"] if m["field_key"] == "bandwidth_mbps")
    assert bw["sum_value"] == 15_000.0

    # 验证线索词匹配函数对多产品文本行为确定
    prods = match_products_by_keywords("客户询问 Kodo Bucket 与 CDN 回源 502", analytics.list_registry()["products"])
    assert prods == ["kodo", "cdn_dcdn"]
