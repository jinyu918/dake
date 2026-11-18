"""模块化提示词组装、插槽渲染、章节连续编号与流血统兼容性测试。"""

from dataclasses import replace
from datetime import timedelta
from shutil import copytree
import hashlib
import json

import httpx
import pytest

from dake.analysis.attribution import AttributionOutput, EvidenceAttributionOutput, attribution_version
from dake.analysis.contract import EvidenceExtractionOutput, ExtractionOutput, strict_schema
from dake.analysis.prompts import (
    composer,
    PROMPT_BUNDLE_VERSION,
    chinese_section_number,
    compose_system_prompt,
    prompt_bundle_hash,
    render_repair_prompt,
    render_slots,
    render_typed_attribute_catalog,
)
from dake.analysis.prompts.__main__ import main as prompts_cli_main
from dake.analysis.providers.xhub import XHubConfig, XHubProvider, configured_stream
from dake.config import Settings
from dake.domain.models import TYPE_ATTRIBUTE_NAMES
from dake.services.history_retrieval import HistoryRetrievalResult
from provider_fakes import candidate, message, output, response


def test_compose_extraction_and_attribution_sections_with_continuous_headings():
    extraction = compose_system_prompt(mode="extraction")
    assert extraction.bundle_version == PROMPT_BUNDLE_VERSION
    assert extraction.section_ids == (
        "00_role_and_guardrails",
        "01_evidence_and_bot_rules",
        "02_slicing_and_identity",
        "03_classification_rules",
        "04_typed_attributes",
        "05_lifecycle_status",
        "06_priority_signals",
        "99_output_checklist",
    )
    assert "一、增量抽取与证据纪律" in extraction.content
    assert "七、输出协议与提交前自检" in extraction.content
    assert '`schema_version` 固定填写 `"1.0"`' in extraction.content
    assert "正式增量归属决策" not in extraction.content
    assert "{{" not in extraction.content and "}}" not in extraction.content

    attr_no_ctx = compose_system_prompt(mode="attribution", include_history_context=False)
    assert "08_history_context" not in attr_no_ctx.section_ids
    assert "八、正式增量归属决策（decision 与 basis）" in attr_no_ctx.content
    assert "十一、输出协议与提交前自检" in attr_no_ctx.content
    assert '`schema_version` 固定填写 `"4.0"`' in attr_no_ctx.content

    attr_with_ctx = compose_system_prompt(mode="attribution", include_history_context=True)
    assert "08_history_context" in attr_with_ctx.section_ids
    assert "八、补充历史聊天背景（history_context）" in attr_with_ctx.content
    assert "九、正式增量归属决策（decision 与 basis）" in attr_with_ctx.content
    assert "十二、输出协议与提交前自检" in attr_with_ctx.content


def test_typed_attribute_catalog_syncs_domain_dict_and_supports_structured_schema():
    default_catalog = render_typed_attribute_catalog()
    for category, names in TYPE_ATTRIBUTE_NAMES.items():
        assert f"**{category}**" in default_catalog
        for attr_name in names:
            assert attr_name in default_catalog

    structured = {
        "排障": [
            {
                "label": "故障等级评估",
                "value_type": "enum",
                "enum_options": ["P0", "P1", "P2"],
                "extraction_hint": "仅依据明确生产受损程度填写",
                "enabled": True,
            },
            {
                "label": "已停用测试字段",
                "value_type": "string",
                "enabled": False,
            },
        ]
    }
    rendered = render_typed_attribute_catalog(structured)
    assert "故障等级评估（类型:enum；可选值:P0/P1/P2；仅依据明确生产受损程度填写）" in rendered
    assert "已停用测试字段" not in rendered
    assert prompt_bundle_hash(structured) != prompt_bundle_hash()


def test_slot_renderer_rejects_missing_or_malformed_slots_and_protects_runtime_values():
    with pytest.raises(ValueError, match="缺少必填插槽"):
        render_slots("规则：{{missing_slot}}", {})
    with pytest.raises(ValueError, match="未闭合"):
        render_slots("规则：{{broken_slot", {"broken_slot": "ok"})

    # 运行时错误消息本身含双花括号时，单遍替换不应误报或二次展开。
    repaired = render_repair_prompt("字段 {{malformed_user_text}} 不在枚举中")
    assert "字段 {{malformed_user_text}} 不在枚举中" in repaired


def test_chinese_section_numbers_cover_expected_bounds():
    assert chinese_section_number(1) == "一"
    assert chinese_section_number(10) == "十"
    assert chinese_section_number(12) == "十二"
    assert chinese_section_number(20) == "二十"
    assert chinese_section_number(23) == "二十三"
    with pytest.raises(ValueError):
        chinese_section_number(0)
    with pytest.raises(ValueError):
        chinese_section_number(100)


def test_provider_records_modular_sections_without_breaking_v1_stream_lineage(tmp_path):
    settings = Settings(provider="xhub", uid="synthetic-uid", data_dir=tmp_path)
    assert configured_stream(settings) == "xhub-v1-33a5598942ac1baebd6d"

    msg = replace(message(), uid="synthetic-uid", source="official_chat")
    provider = XHubProvider(
        XHubConfig(model=settings.llm_model, api_key="synthetic-key", max_tokens=settings.llm_max_tokens),
        settings,
        transport=httpx.MockTransport(lambda _: response(output(candidate(evidence_ids=[msg.key])))),
    )
    try:
        assert provider.extract([msg], [], [])
        trace = provider.traces[0]
        assert trace["prompt_bundle_version"] == PROMPT_BUNDLE_VERSION
        assert "04_typed_attributes" in trace["prompt_sections"]
        assert trace["prompt_hash"] == prompt_bundle_hash()
    finally:
        provider.close()


def test_prompts_cli_supports_list_and_full_preview(capsys):
    assert prompts_cli_main(["--mode", "attribution", "--with-context", "--list"]) == 0
    listed = capsys.readouterr().out
    assert "08_history_context" in listed
    assert "bundle_version: modular-v1" in listed

    assert prompts_cli_main(["--mode", "extraction"]) == 0
    preview = capsys.readouterr().out
    assert "一、增量抽取与证据纪律" in preview
    assert json.dumps("1.0") in preview

    assert prompts_cli_main(["--priority-preview", "--list"]) == 0
    selected = capsys.readouterr().out
    assert "06_affected_customers_preview" in selected and "06_priority_signals" not in selected


@pytest.mark.parametrize("priority_preview", [False, True])
@pytest.mark.parametrize("formal", [False, True])
def test_request_and_repair_audit_use_matching_prompt_and_schema(tmp_path, priority_preview, formal):
    settings = Settings(provider="xhub", uid="synthetic-uid", data_dir=tmp_path, priority_preview=priority_preview)
    expected_stream = "xhub-v1-c34db987c2ea98df396e" if priority_preview else "xhub-v1-33a5598942ac1baebd6d"
    msg = replace(message(), uid=settings.uid, source="official_chat")
    requests, observations = [], []
    mode = "attribution" if formal else "extraction"
    schema_version = "4.0" if formal else "1.0"
    model = ((EvidenceAttributionOutput if priority_preview else AttributionOutput) if formal
             else (EvidenceExtractionOutput if priority_preview else ExtractionOutput))
    schema = strict_schema(model.model_json_schema())

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return response("{}")
        return response(json.dumps(dict(schema_version=schema_version, candidates=[])))

    provider = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-key",
                                       max_tokens=settings.llm_max_tokens),
                            settings, transport=httpx.MockTransport(respond))
    try:
        assert provider.name == configured_stream(settings) == expected_stream
        provider.configure_execution_observer(lambda stage, **payload: observations.append((stage, payload)))
        if formal:
            history = HistoryRetrievalResult(settings.uid, provider.name, msg.sent_at + timedelta(minutes=1),
                                             ((msg.key, msg.digest),))
            provider.configure_history_retrieval(history)
        assert provider.extract([msg], [], []) == []
        assert len(requests) == len(provider.traces) == 2
        assembled = compose_system_prompt(mode=mode, schema=schema, priority_preview=priority_preview)
        for request, trace in zip(requests, provider.traces, strict=True):
            assert request["messages"][0]["content"] == assembled.content
            assert request["response_format"]["json_schema"]["schema"] == schema
            assert trace["prompt_sections"] == list(assembled.section_ids)
            assert trace["prompt_hash"] == assembled.bundle_hash
            assert trace["effective_prompt_hash"] == hashlib.sha256(assembled.content.encode()).hexdigest()
            if formal:
                assert trace["attribution_policy"]["version"] == attribution_version(priority_preview=priority_preview)
        assert ("06_priority_signals" in assembled.section_ids) is not priority_preview
        for section_id in ("06_priority_evidence_preview", "06_affected_customers_preview", "06_clock_evidence_preview"):
            assert (section_id in assembled.section_ids) is priority_preview
        assert ("事项定级预览补充核对" in requests[1]["messages"][-1]["content"]) is priority_preview
        audit = [value for stage, value in observations if stage == "model_started"]
        assert [value["repair_attempt"] for value in audit] == [False, True]
        assert audit[0]["request_digest"] != audit[1]["request_digest"]
        assert all(value["prompt_bundle_hash"] == assembled.bundle_hash and
                   value["prompt_sections"] == list(assembled.section_ids) for value in audit)
    finally:
        provider.close()


def test_preview_hash_tracks_its_rules_without_invalidating_regular_bundle(monkeypatch, tmp_path):
    sections = tmp_path / 'sections'
    templates = tmp_path / 'templates'
    copytree(composer.SECTIONS_DIR, sections)
    copytree(composer.TEMPLATES_DIR, templates)
    monkeypatch.setattr(composer, 'PROMPTS_DIR', tmp_path)
    monkeypatch.setattr(composer, 'SECTIONS_DIR', sections)
    monkeypatch.setattr(composer, 'TEMPLATES_DIR', templates)
    regular = prompt_bundle_hash()
    preview = prompt_bundle_hash(priority_preview=True)
    policy = attribution_version(priority_preview=True)
    path = sections / '06_clock_evidence_preview.md'
    path.write_text(path.read_text(encoding='utf-8') + '\n合成规则修改。\n', encoding='utf-8')
    assert prompt_bundle_hash() == regular
    assert prompt_bundle_hash(priority_preview=True) != preview
    assert attribution_version(priority_preview=True) != policy
