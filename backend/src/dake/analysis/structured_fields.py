"""事件产品归属识别与动态结构化字段抽取器（含严格原文引证核验与量纲归一）。"""

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any
from uuid import uuid4

import httpx
from pydantic import Field, ValidationError

from ..config import Settings
from ..domain.attribute_schemas import match_products_by_keywords, normalize_structured_value
from ..domain.models import Event, Message
from ..repositories.local_state import private_json
from ..repositories.mongo_analytics import scope_bucket_key
from .attribution import _find_exact_or_ws_quote
from .contract import StrictObject, strict_schema
from .prompts.composer import TEMPLATES_DIR, render_slots
from .providers.xhub import XHubConfig, XHubError


class ExtractedFieldCandidate(StrictObject):
    """模型针对单个动态配置字段返回的结构化提取项。"""

    field_id: str = Field(min_length=1)
    raw_value: str = Field(min_length=1)
    numeric_value: float | None = None
    bool_value: bool | None = None
    message_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class StructuredExtractionOutput(StrictObject):
    """事件产品归属与动态结构化属性提取输出合约。"""

    products: list[str]
    fields: list[ExtractedFieldCandidate]


def render_product_catalog_prompt(products: Sequence[Mapping[str, Any]]) -> str:
    """将已启用的产品目录渲染为提示词说明。"""
    lines: list[str] = []
    for p in products:
        if not p.get("enabled", True):
            continue
        kws = " / ".join(str(k) for k in (p.get("keywords") or [])[:12])
        hint = f"（常见线索：{kws}）" if kws else ""
        desc = f" — {p['description']}" if p.get("description") else ""
        lines.append(f"- `{p['product_key']}`：{p['name']}{desc}{hint}")
    return "\n".join(lines) if lines else "- 当前无启用产品目录。"


def select_candidate_fields(
    registry: Mapping[str, Any],
    *,
    event_types: Sequence[str],
    hint_products: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """筛选与当前事件分类及候选产品相关的启用字段，避免无关分类字段干扰模型。"""
    enabled_products = {p["product_key"] for p in registry.get("products", []) if p.get("enabled", True)}
    type_set = {t for t in event_types if t}
    all_enabled = [
        f
        for f in registry.get("fields", [])
        if f.get("enabled", True)
        and (f["scope_type"] != "product" or f["scope_key"] in enabled_products)
    ]

    selected: list[dict[str, Any]] = []
    restrict_products = set(hint_products or []) if len(all_enabled) > 45 and hint_products else None

    for fdef in all_enabled:
        if fdef["scope_type"] == "event_type":
            if not type_set or fdef["scope_key"] in type_set:
                selected.append(dict(fdef))
            continue
        if restrict_products is not None and fdef["scope_key"] not in restrict_products:
            continue
        app_types = fdef.get("applicable_types") or []
        if app_types and type_set and not (set(app_types) & type_set):
            continue
        selected.append(dict(fdef))
    return selected


def render_field_catalog_prompt(fields: Sequence[Mapping[str, Any]]) -> str:
    """将候选结构化字段列表渲染为清晰、强约束的提取清单。"""
    if not fields:
        return "- 当前无启用的候选结构化字段，保持 `fields=[]`。"
    lines: list[str] = []
    for f in fields:
        meta = [f"类型:{f['data_type']}"]
        if f.get("unit"):
            meta.append(f"标准单位:{f['unit']}")
        if f["data_type"] == "enum" and f.get("enum_options"):
            meta.append(f"可选值:{' / '.join(f['enum_options'])}")
        scope_label = f"产品:{f['scope_key']}" if f["scope_type"] == "product" else f"事件:{f['scope_key']}"
        lines.append(
            f"- `{f['field_id']}`【{scope_label} · {f['label']}】（{'；'.join(meta)}）：{f['extraction_hint']}"
        )
    return "\n".join(lines)


def _locate_verified_quote(
    message_id: str,
    quote: str,
    msg_by_id: Mapping[str, str],
) -> tuple[str, str] | None:
    """核验引文真实存在于事件关联消息中；若同事件相邻消息编号轻微错位且唯一精确匹配则自动对齐。"""
    body = msg_by_id.get(message_id)
    if body is not None:
        matched = _find_exact_or_ws_quote(quote, body)
        if matched is not None:
            return message_id, matched

    cleaned = (quote or "").strip()
    if len(cleaned) < 2:
        return None
    hits: list[tuple[str, str]] = []
    for mid, text in msg_by_id.items():
        found = _find_exact_or_ws_quote(cleaned, text)
        if found is not None:
            hits.append((mid, found))
    return hits[0] if len(hits) == 1 else None


def validate_and_normalize_structured_output(
    raw_json: str,
    *,
    registry: Mapping[str, Any],
    msg_by_id: Mapping[str, str],
    fallback_products: Sequence[str] = (),
) -> dict[str, Any]:
    """解析模型输出，执行严格引文存在性核验、产品范围过滤与确定性数值单位换算。"""
    try:
        parsed = StructuredExtractionOutput.model_validate_json(raw_json)
    except ValidationError as exc:
        raise ValueError("结构化字段输出未通过 JSON 契约校验") from exc

    valid_products = {
        p["product_key"] for p in registry.get("products", []) if p.get("enabled", True)
    }
    products = list(dict.fromkeys(p for p in parsed.products if p in valid_products))
    if not products and fallback_products:
        products = [p for p in fallback_products if p in valid_products]

    fields_by_id = {
        f["field_id"]: f for f in registry.get("fields", []) if f.get("enabled", True)
    }

    props: dict[str, dict[str, Any]] = {}
    prop_evidence: list[dict[str, Any]] = []
    seen_field_ids: set[str] = set()

    for item in parsed.fields:
        if item.field_id in seen_field_ids:
            continue
        fdef = fields_by_id.get(item.field_id)
        if fdef is None:
            continue
        verified = _locate_verified_quote(item.message_id, item.quote, msg_by_id)
        if verified is None:
            # 无可靠原文依据的字段直接丢弃，保证“宁缺毋滥、零幻觉”
            continue
        verified_mid, verified_quote = verified

        normalized = normalize_structured_value(
            fdef,
            item.raw_value,
            numeric_hint=item.numeric_value,
            bool_hint=item.bool_value,
        )
        if normalized is None:
            continue

        # 若抽出了某产品专属指标但 products 漏列了该产品，自动补齐该产品标签保证归属自洽
        if fdef["scope_type"] == "product" and fdef["scope_key"] in valid_products:
            if fdef["scope_key"] not in products:
                products.append(fdef["scope_key"])

        bucket_key = scope_bucket_key(fdef["scope_type"], fdef["scope_key"])
        props.setdefault(bucket_key, {})[fdef["field_key"]] = normalized
        prop_evidence.append(
            {
                "field_id": fdef["field_id"],
                "scope_type": fdef["scope_type"],
                "scope_key": fdef["scope_key"],
                "field_key": fdef["field_key"],
                "label": fdef["label"],
                "data_type": fdef["data_type"],
                "unit": fdef["unit"],
                "normalized_value": normalized,
                "raw_value": item.raw_value.strip(),
                "quote": verified_quote,
                "evidence_ids": [verified_mid],
                "schema_version": int(registry.get("version", 1)),
            }
        )
        seen_field_ids.add(item.field_id)

    return {
        "primary_product": products[0] if products else None,
        "products": products,
        "props": props,
        "prop_evidence": prop_evidence,
    }


def heuristic_product_projection(
    event: Event,
    messages: Sequence[Message],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    """当处于离线 stub 模式或未启用模型调用时，仅做基于配置线索词的确定性产品归属标记，不臆造指标值。"""
    text_parts = [event.topic_key or "", event.summary or ""]
    text_parts.extend(m.content for m in messages[:20])
    for ev_item in event.evidence[:20]:
        if isinstance(ev_item, Mapping) and ev_item.get("quote"):
            text_parts.append(str(ev_item["quote"]))
    matched = match_products_by_keywords(" ".join(text_parts), registry.get("products", []))
    return {
        "primary_product": matched[0] if matched else None,
        "products": matched,
        "props": {},
        "prop_evidence": [],
    }


class XHubStructuredExtractor:
    """调用已授权 XHub 网关对已识别事件按动态配置模板补齐产品归属与强类型属性。"""

    def __init__(
        self,
        config: XHubConfig,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        config.validate_endpoint()
        self.config = config
        self.settings = settings
        self.schema = strict_schema(StructuredExtractionOutput.model_json_schema())
        self.client = httpx.Client(
            timeout=config.timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def extract_for_event(
        self,
        event: Event,
        messages: Sequence[Message],
        registry: Mapping[str, Any],
    ) -> dict[str, Any]:
        """针对单条事件及其关联聊天消息提取可配置产品与结构化属性。"""
        if self.settings.provider != "xhub":
            return heuristic_product_projection(event, messages, registry)

        msg_items: list[dict[str, str]] = []
        msg_by_id: dict[str, str] = {}
        for m in messages:
            if m.source != "official_chat":
                raise XHubError("真实结构化抽取仅接受官方聊天消息")
            msg_items.append(
                {
                    "id": m.key,
                    "send_time": m.sent_at.isoformat(),
                    "sender_name": m.sender_name,
                    "content": m.content,
                }
            )
            msg_by_id[m.key] = m.content

        # 若消息表缺失部分条目，回退使用事件自身已存的验证引文片段
        if not msg_items:
            for idx, ev_ref in enumerate(event.evidence):
                if isinstance(ev_ref, Mapping) and ev_ref.get("quote") and ev_ref.get("message_id"):
                    key = f"{ev_ref.get('source', 'official_chat')}:{ev_ref['message_id']}"
                    if key not in msg_by_id:
                        quote_str = str(ev_ref["quote"])
                        msg_by_id[key] = quote_str
                        msg_items.append(
                            {
                                "id": key,
                                "send_time": str(ev_ref.get("send_time") or event.end_time.isoformat()),
                                "sender_name": str(ev_ref.get("sender_name") or ""),
                                "content": quote_str,
                            }
                        )

        if not msg_items:
            return heuristic_product_projection(event, messages, registry)

        hint_prods = match_products_by_keywords(
            " ".join([event.topic_key or "", event.summary or "", *(m["content"] for m in msg_items[:15])]),
            registry.get("products", []),
        )
        event_types = list(event.types or ([event.type] if event.type else []))
        candidate_fields = select_candidate_fields(
            registry,
            event_types=event_types,
            hint_products=hint_prods,
        )

        template_raw = (TEMPLATES_DIR / "structured_fields_extraction.md").read_text(encoding="utf-8").strip()
        system_prompt = render_slots(
            template_raw,
            {
                "product_catalog": render_product_catalog_prompt(registry.get("products", [])),
                "field_catalog": render_field_catalog_prompt(candidate_fields),
            },
        )
        system_content = f"{system_prompt}\n\n输出 JSON Schema：\n{json.dumps(self.schema, ensure_ascii=False)}"

        user_payload = {
            "event": {
                "event_id": event.event_id,
                "type": event.type,
                "types": event_types,
                "topic_key": event.topic_key,
                "summary": event.summary,
            },
            "messages": msg_items[:40],
        }
        request_body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "aftersales_structured_fields_v1",
                    "strict": True,
                    "schema": self.schema,
                },
            },
            "max_tokens": min(self.config.max_tokens, 2048),
            "stream": False,
        }

        base = self.config.base_url.rstrip("/")
        endpoint = (base if base.endswith("/v1") else base + "/v1") + "/chat/completions"
        trace = {
            "model": self.config.model,
            "event_id": event.event_id,
            "schema_version": registry.get("version", 1),
            "prompt_hash": hashlib.sha256(system_content.encode("utf-8")).hexdigest(),
            "input": user_payload,
        }
        try:
            resp = self.client.post(
                endpoint,
                json=request_body,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
            )
            trace["http_status"] = resp.status_code
            if resp.status_code != 200:
                raise XHubError(f"结构化属性提取请求失败（HTTP {resp.status_code}）")
            body = resp.json()
            choice = body["choices"][0]
            content = choice["message"].get("content")
            trace["output"] = content
            if choice.get("finish_reason") != "stop" or not isinstance(content, str):
                raise XHubError("结构化属性提取输出未正常完成")
            result = validate_and_normalize_structured_output(
                content,
                registry=registry,
                msg_by_id=msg_by_id,
                fallback_products=hint_prods,
            )
            trace["ok"] = True
            return result
        except Exception as exc:
            trace["ok"] = False
            trace["error_type"] = type(exc).__name__
            raise
        finally:
            trace_path = self.settings.data_dir / "raw" / "xhub_structured" / f"{uuid4().hex}.json"
            private_json(trace_path, trace)
