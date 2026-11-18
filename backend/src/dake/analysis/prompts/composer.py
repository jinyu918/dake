"""模块化提示词加载、插槽渲染与按模式有序组装器。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal

from ...domain.models import TYPE_ATTRIBUTE_NAMES

PromptMode = Literal["extraction", "attribution"]

PROMPTS_DIR = Path(__file__).resolve().parent
SECTIONS_DIR = PROMPTS_DIR / "sections"
TEMPLATES_DIR = PROMPTS_DIR / "templates"

PROMPT_BUNDLE_VERSION = "modular-v1"
# 保持 v1 分析流血统锚点稳定，避免拆分文件、优化排版或微调属性说明导致存量事件断流。
# 单次模型调用的实际提示词变更由 prompt_hash 与 effective_prompt_hash 独立精确审计。
STREAM_LINEAGE_V1 = "97ca9b78ad86adcd88f21ce4cf2981c18555cc4e5113bc05ba27aedd3efc9489"

# 沿用本分支隔离验收使用的预览流锚点，去除旧文件换行格式对流标识的影响。
PRIORITY_PREVIEW_LINEAGE_V1 = "0d3731b48af91e087fc9026a37ceefb6a30b0c129b0984d53ffa6f240319d6ee"

PRIORITY_PREVIEW_SECTIONS = (
    "06_priority_evidence_preview",
    "06_affected_customers_preview",
    "06_clock_evidence_preview",
)

BASE_EXTRACTION_SECTIONS: tuple[str, ...] = (
    "00_role_and_guardrails",
    "01_evidence_and_bot_rules",
    "02_slicing_and_identity",
    "03_classification_rules",
    "04_typed_attributes",
    "05_lifecycle_status",
    "06_priority_signals",
)
ATTRIBUTION_CORE_SECTIONS: tuple[str, ...] = (
    "09_attribution_decisions",
    "10_summary_changes",
    "11_recovery_and_continuity",
)
CHECKLIST_SECTION = "99_output_checklist"

_SLOT_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_CN_DIGITS = ("", "一", "二", "三", "四", "五", "六", "七", "八", "九")


@dataclass(frozen=True)
class PromptSection:
    """单个职责独立的提示词分块及其内容指纹。"""

    section_id: str
    title: str | None
    body: str
    sha256: str


@dataclass(frozen=True)
class AssembledPrompt:
    """完成插槽注入、章节自动编号与 Schema 拼接后的可执行提示词。"""

    mode: PromptMode
    bundle_version: str
    section_ids: tuple[str, ...]
    sections: tuple[PromptSection, ...]
    content: str
    bundle_hash: str
    content_hash: str


def chinese_section_number(index: int) -> str:
    """将 1..99 的章节正整数序号格式化为中文序号（一、二、…、十一、…）。"""
    if not 1 <= index <= 99:
        raise ValueError(f"章节序号超出支持范围: {index}")
    tens, ones = divmod(index, 10)
    if tens == 0:
        return _CN_DIGITS[ones]
    if tens == 1:
        return "十" + _CN_DIGITS[ones]
    return _CN_DIGITS[tens] + "十" + _CN_DIGITS[ones]


def render_slots(template: str, slots: Mapping[str, str] | None = None) -> str:
    """单遍替换模板插槽；遇到未声明插槽或残缺花括号立即报错，不对运行时值做二次展开。"""
    stripped = _SLOT_PATTERN.sub("", template)
    if "{{" in stripped or "}}" in stripped:
        raise ValueError("提示词模板存在未闭合或语法非法的插槽标记")
    provided = dict(slots or {})

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in provided:
            raise ValueError(f"提示词模板缺少必填插槽: {name}")
        return provided[name]

    return _SLOT_PATTERN.sub(_replace, template)


def _format_attribute_item(item: str | Mapping[str, Any]) -> str | None:
    """兼容当前纯名称字符串与后续界面化结构字段定义（含类型、枚举、说明与启停状态）。"""
    if isinstance(item, str):
        text = item.strip()
        return text or None
    if not isinstance(item, Mapping):
        raise ValueError("属性定义项必须是字符串或映射对象")
    if item.get("enabled", True) is False:
        return None
    label = str(item.get("label") or item.get("name") or "").strip()
    if not label:
        raise ValueError("结构化属性定义缺少 name/label")
    details: list[str] = []
    value_type = str(item.get("value_type") or "").strip()
    if value_type:
        details.append(f"类型:{value_type}")
    options = item.get("enum_options")
    if isinstance(options, Sequence) and not isinstance(options, (str, bytes)) and options:
        joined = "/".join(str(opt).strip() for opt in options if str(opt).strip())
        if joined:
            details.append(f"可选值:{joined}")
    hint = str(item.get("extraction_hint") or item.get("description") or "").strip()
    if hint:
        details.append(hint)
    return f"{label}（{'；'.join(details)}）" if details else label


def render_typed_attribute_catalog(
    attribute_catalog: Mapping[str, Sequence[str | Mapping[str, Any]]] | None = None,
) -> str:
    """将分类属性字典渲染为提示词段落，消除代码常量与提示词文本的双处手工维护。"""
    source = TYPE_ATTRIBUTE_NAMES if attribute_catalog is None else attribute_catalog
    lines: list[str] = []
    for type_label, raw_items in source.items():
        rendered_items = [formatted for item in raw_items if (formatted := _format_attribute_item(item))]
        if rendered_items:
            lines.append(f"- **{type_label}**：{'、'.join(rendered_items)}。")
    if not lines:
        return "- 当前未启用类型专属属性，保持 `typed_attributes=[]`。"
    return "\n".join(lines)


def section_plan(mode: PromptMode, *, include_history_context: bool = False,
                 priority_preview: bool = False) -> tuple[str, ...]:
    """返回指定抽取模式所需加载的分块 ID 顺序列表。"""
    extraction_sections = (
        (*BASE_EXTRACTION_SECTIONS[:-1], *PRIORITY_PREVIEW_SECTIONS) if priority_preview else BASE_EXTRACTION_SECTIONS
    )
    if mode == "extraction":
        return (*extraction_sections, CHECKLIST_SECTION)
    if mode == "attribution":
        history_sections = (
            ("07_history_candidates", "08_history_context")
            if include_history_context
            else ("07_history_candidates",)
        )
        return (
            *extraction_sections,
            *history_sections,
            *ATTRIBUTION_CORE_SECTIONS,
            CHECKLIST_SECTION,
        )
    raise ValueError(f"未知提示词组装模式: {mode}")


def _default_slots(
    mode: PromptMode,
    *,
    attribute_catalog: Mapping[str, Sequence[str | Mapping[str, Any]]] | None = None,
) -> dict[str, str]:
    """构造当前模式的标准动态插槽字典。"""
    if mode == "attribution":
        schema_rule = '`schema_version` 固定填写 `"4.0"`。'
        checklist_tail = (
            "\n- `decision` 与 `referenced_event_id`、`possible_event_ids`、`changes`、`continuity` 约束一致；"
            "`update`/`duplicate` 的 `basis` 同时具备本轮新依据与指向目标旧事件的旧依据，"
            "且 `evidence_ids` 未混入 `history_context` 只读消息。"
        )
    else:
        schema_rule = '`schema_version` 固定填写 `"1.0"`。'
        checklist_tail = ""
    return {
        "typed_attribute_catalog": render_typed_attribute_catalog(attribute_catalog),
        "schema_version_rule": schema_rule,
        "mode_checklist_tail": checklist_tail,
    }


def load_section(section_id: str, slots: Mapping[str, str] | None = None) -> PromptSection:
    """加载单个 Markdown 分块文件，解析一级标题并完成插槽渲染。"""
    path = SECTIONS_DIR / f"{section_id}.md"
    if not path.is_file():
        raise FileNotFoundError(f"提示词分块文件不存在: {path}")
    raw = path.read_text(encoding="utf-8").strip()
    rendered = render_slots(raw, slots).strip()
    title: str | None = None
    body = rendered
    lines = rendered.splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        body = "\n".join(lines[1:]).strip()
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return PromptSection(section_id=section_id, title=title, body=body, sha256=digest)


def prompt_bundle_hash(
    attribute_catalog: Mapping[str, Sequence[str | Mapping[str, Any]]] | None = None,
    *, priority_preview: bool = False,
) -> str:
    """计算所选规则集的模块化源文件与属性目录指纹。"""
    hasher = hashlib.sha256()
    hasher.update(f"bundle:{PROMPT_BUNDLE_VERSION}\n".encode("utf-8"))
    section_ids = section_plan("attribution", include_history_context=True, priority_preview=priority_preview)
    repair_templates = ("repair_feedback.md", "repair_priority_preview.md") if priority_preview else ("repair_feedback.md",)
    files = sorted(
        [*(SECTIONS_DIR / f"{name}.md" for name in section_ids), *(TEMPLATES_DIR / name for name in repair_templates)],
        key=lambda p: p.relative_to(PROMPTS_DIR).as_posix(),
    )
    for file_path in files:
        rel = file_path.relative_to(PROMPTS_DIR).as_posix()
        content = file_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        hasher.update(f"file:{rel}\n{content}\n".encode("utf-8"))
    catalog_text = render_typed_attribute_catalog(attribute_catalog)
    hasher.update(f"catalog:\n{catalog_text}\n".encode("utf-8"))
    return hasher.hexdigest()


def render_attribution_policy(*, priority_preview: bool = False) -> str:
    """渲染正式增量归属专属规则快照，供归属策略版本指纹与审计使用。"""
    blocks: list[str] = []
    section_ids = (*ATTRIBUTION_CORE_SECTIONS, *PRIORITY_PREVIEW_SECTIONS) if priority_preview else ATTRIBUTION_CORE_SECTIONS
    for idx, section_id in enumerate(section_ids, start=1):
        section = load_section(section_id)
        heading = f"{chinese_section_number(idx)}、{section.title}" if section.title else ""
        blocks.append(f"{heading}\n{section.body}".strip() if heading else section.body)
    return "\n\n".join(blocks)


def compose_system_prompt(
    *,
    mode: PromptMode = "attribution",
    include_history_context: bool = False,
    schema: Mapping[str, Any] | None = None,
    priority_preview: bool = False,
    attribute_catalog: Mapping[str, Sequence[str | Mapping[str, Any]]] | None = None,
) -> AssembledPrompt:
    """按抽取或归属模式组装完整系统提示词，自动编排连续中文大节序号并拼接输出 Schema。"""
    plan = section_plan(mode, include_history_context=include_history_context, priority_preview=priority_preview)
    slots = _default_slots(mode, attribute_catalog=attribute_catalog)
    sections = tuple(load_section(section_id, slots) for section_id in plan)

    blocks: list[str] = []
    numbered_index = 0
    for section in sections:
        if section.title:
            numbered_index += 1
            header = f"{chinese_section_number(numbered_index)}、{section.title}"
            blocks.append(f"{header}\n{section.body}")
        else:
            blocks.append(section.body)

    prompt_text = "\n\n".join(blocks)
    if schema is not None:
        prompt_text = f"{prompt_text}\n\n输出 JSON Schema：\n{json.dumps(schema, ensure_ascii=False)}"

    b_hash = prompt_bundle_hash(attribute_catalog, priority_preview=priority_preview)
    c_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    return AssembledPrompt(
        mode=mode,
        bundle_version=PROMPT_BUNDLE_VERSION,
        section_ids=plan,
        sections=sections,
        content=prompt_text,
        bundle_hash=b_hash,
        content_hash=c_hash,
    )


def render_repair_prompt(validation_error: str, *, priority_preview: bool = False) -> str:
    """加载并渲染结构或证据校验失败时的一次性修正提示词。"""
    path = TEMPLATES_DIR / "repair_feedback.md"
    if not path.is_file():
        raise FileNotFoundError(f"修正提示词模板不存在: {path}")
    raw = path.read_text(encoding="utf-8").strip()
    rendered = render_slots(raw, {"validation_error": str(validation_error).strip()})
    if priority_preview:
        extra = (TEMPLATES_DIR / "repair_priority_preview.md").read_text(encoding="utf-8").strip()
        return f"{rendered}\n\n{extra}"
    return rendered
