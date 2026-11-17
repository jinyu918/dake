"""抽取字段的基础合约与证据校验，供正式归属合约复用。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..domain.models import Candidate, PENDING_TYPE, TYPE_ATTRIBUTE_NAMES, Event, Message
from .validation import validate_candidates


class ContractError(ValueError):
    """不携带模型正文或聊天内容的校验错误。"""


class StrictObject(BaseModel):
    """拒绝多余字段与隐式类型转换。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class Fact(StrictObject):
    """附有原文片段及消息引用的字段值。"""

    value: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    quote: str = Field(min_length=1)


class Attribute(Fact):
    """可扩展的类型专属属性，不自造官方枚举。"""

    name: str = Field(min_length=1)


class TypeLabel(StrictObject):
    """一个类型标签及其独立的直接证据。"""

    type: Literal["商机", "流失", "排障", "资源需求", "功能需求", "商务需求", "其他"]
    evidence_ids: list[str] = Field(min_length=1)
    quote: str = Field(min_length=1)


class RemovedTypeLabel(TypeLabel):
    """有本轮更正依据支持的标签撤销。"""


class TypedAttribute(Attribute):
    """明确隶属某一类型的属性值。"""

    type_label: Literal["商机", "流失", "排障", "资源需求", "功能需求", "商务需求", "其他"]


class ExtractedCandidate(StrictObject):
    """模型候选与关联意图；是否真正更新仍由业务匹配器决定。"""

    type: Literal["商机", "流失", "排障", "资源需求", "功能需求", "商务需求", "其他", "待分类"]
    topic_key: str | None
    summary: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    status: Fact | None
    severity: Fact | None
    assignees: list[Fact]
    attributes: list[Attribute]
    extensions: list[Attribute]
    decision: Literal["new", "update", "review"]
    referenced_event_id: str | None
    decision_reason: str = Field(min_length=1)
    type_labels: list[TypeLabel] = Field(default_factory=list)
    removed_types: list[RemovedTypeLabel] = Field(default_factory=list)
    typed_attributes: list[TypedAttribute] = Field(default_factory=list)
    classification_status: Literal["已分类", "待分类"] = "已分类"
    other_reason: Fact | None = None


class ExtractionOutput(StrictObject):
    """版本化的模型输出，不包含由程序生成的时间和事件编号。"""

    schema_version: Literal["1.0"]
    candidates: list[ExtractedCandidate]


def parse_output(content: str, new_messages: list[Message], context: list[Message],
                 existing_events: list[Event], *, output_model=ExtractionOutput) -> list[Candidate]:
    """先验证结构，再验证来源、字段引文和旧事件引用；失败不伪装成空结果。"""
    try:
        output = output_model.model_validate_json(content)
    except (ValidationError, ValueError):
        raise ContractError("模型输出不符合清洗合约") from None
    messages = {message.key: message for message in [*context, *new_messages]}
    uids = {message.uid for message in messages.values()}
    if len(uids) != 1 or any(event.uid not in uids for event in existing_events):
        raise ContractError("清洗输入必须属于同一客户")
    old = {event.event_id: event for event in existing_events}
    candidates = []
    for candidate_index, item in enumerate(output.candidates):
        ids = set(item.evidence_ids)
        if len(ids) != len(item.evidence_ids) or not ids.issubset(messages):
            raise ContractError("证据必须唯一且引用本次输入的完整消息 ID")
        if not item.summary.strip() or not item.decision_reason.strip():
            raise ContractError("概述及关联依据不能为空")
        if item.decision != "review" and not (item.topic_key and item.topic_key.strip()):
            raise ContractError("明确的新建或更新必须有主题")
        labels = [label.type for label in item.type_labels]
        if len(set(labels)) != len(labels):
            raise ContractError("类型标签不得重复")
        # 旧版本只返回 type；新结构显式返回 type_labels 时逐标签校验，未显式返回时保留兼容回退。
        adopted_labels = labels or ([] if item.type == PENDING_TYPE else [item.type])
        if item.classification_status == "待分类":
            if item.type != PENDING_TYPE or adopted_labels:
                raise ContractError("待分类事项不能同时采用六类或其他标签")
        elif item.type == PENDING_TYPE:
            raise ContractError("待分类状态必须显式标记 classification_status")
        elif item.type not in adopted_labels:
            raise ContractError("主类型必须包含在类型标签中")
        if item.type == "其他" or "其他" in adopted_labels:
            if item.other_reason is None:
                raise ContractError("其他事项必须说明排除六类的直接理由")
        explicit = output_model is not ExtractionOutput
        if explicit and item.type != PENDING_TYPE and not item.type_labels:
            raise ContractError("正式归属输出必须为每个采用类型提供逐项证据")
        if item.decision in {"update", "duplicate"}:
            event = old.get(item.referenced_event_id)
            event_types = set(event.types or ([] if event.type == PENDING_TYPE else [event.type])) if event else set()
            item_types = set(adopted_labels)
            if event is None:
                raise ContractError("关联必须引用本次输入中的已有事件")
            if not explicit and not event_types.intersection(item_types):
                raise ContractError("旧版关联必须引用同类型的已有事件")
            if not explicit and event.topic_key != item.topic_key:
                raise ContractError("旧版更新必须复用原主题")
        elif item.referenced_event_id is not None:
            raise ContractError("新建或待确认不得指定旧事件")
        for facts in (item.attributes, item.extensions):
            if len({fact.name for fact in facts}) != len(facts):
                raise ContractError("属性名称不得重复")
        facts = [(name, getattr(item, name)) for name in ("status", "severity", "other_reason")]
        facts += [(f"type_labels[{index}]", label) for index, label in enumerate(item.type_labels)]
        facts += [(f"removed_types[{index}]", label) for index, label in enumerate(item.removed_types)]
        facts += [(f"{name}[{index}]", fact) for name in ("attributes", "extensions", "assignees")
                  for index, fact in enumerate(getattr(item, name))]
        facts += [(f"typed_attributes[{index}]", fact) for index, fact in enumerate(item.typed_attributes)]
        errors = []
        for path, fact in facts:
            if fact is None:
                continue
            refs = set(fact.evidence_ids)
            if not refs.issubset(ids) or len(refs) != len(fact.evidence_ids):
                errors.append(f"candidates[{candidate_index}].{path}：字段证据必须是候选证据的无重复子集")
                continue
            quoted = [messages[key] for key in refs if fact.quote in messages[key].content]
            if not quoted:
                errors.append(f"candidates[{candidate_index}].{path}：字段引文无法在所引用消息中核对")
                continue
            if path.startswith("type_labels[") or path.startswith("removed_types["):
                continue
            is_assignee = any(fact is assignee for assignee in item.assignees)
            if fact.value not in fact.quote and not (
                is_assignee and any(fact.value == message.sender_id for message in quoted)
            ):
                errors.append(f"candidates[{candidate_index}].{path}：字段值必须来自引文或明确认领者标识")
        if errors:
            raise ContractError("；".join(errors))
        type_evidence = {
            label.type: [dict(evidence_ids=label.evidence_ids, quote=label.quote)]
            for label in item.type_labels
        }
        removed_types = {
            label.type: [dict(evidence_ids=label.evidence_ids, quote=label.quote)]
            for label in item.removed_types
        }
        typed_attributes = {}
        for fact in item.typed_attributes:
            if fact.name in typed_attributes.get(fact.type_label, {}):
                raise ContractError("同一类型的属性名称不得重复")
            typed_attributes.setdefault(fact.type_label, {})[fact.name] = fact.model_dump()
        if any(label not in adopted_labels for label in type_evidence):
            raise ContractError("类型证据不能指向未采用的标签")
        if set(removed_types) & set(adopted_labels):
            raise ContractError("同一标签不能同时采用和撤销")
        if item.decision != "update" and removed_types:
            raise ContractError("只有更新已有事项时才能撤销类型标签")
        if item.referenced_event_id and removed_types:
            old_event = old.get(item.referenced_event_id)
            old_labels = set(old_event.types or ([] if old_event.type == PENDING_TYPE else [old_event.type])) if old_event else set()
            if not set(removed_types).issubset(old_labels):
                raise ContractError("撤销标签必须存在于被更新事项")
        for label, attrs in typed_attributes.items():
            if label not in adopted_labels:
                raise ContractError("类型专属属性必须隶属于当前类型标签")
            if set(attrs) - set(TYPE_ATTRIBUTE_NAMES.get(label, ())):
                raise ContractError("类型专属属性名称不属于该类型字典")
        candidate = Candidate(
            type=item.type, topic_key=item.topic_key, summary=item.summary,
            evidence_ids=item.evidence_ids,
            status=item.status.value if item.status else None,
            severity=item.severity.value if item.severity else None,
            assignees=[fact.value for fact in item.assignees],
            attributes={fact.name: fact.value for fact in item.attributes},
            extensions={
                "extraction_v1": item.model_dump(exclude={"summary", "evidence_ids", "basis", "possible_event_ids", "changes", "continuity"}),
            },
            ambiguous=item.decision == "review", referenced_event_id=item.referenced_event_id,
            decision=item.decision if explicit else None,
            decision_reason=item.decision_reason if explicit else "",
            basis=[ref.model_dump() for ref in item.basis] if explicit else [],
            possible_event_ids=item.possible_event_ids if explicit else [],
            changes=[change.model_dump() for change in item.changes] if hasattr(item, "changes") else None,
            continuity=item.continuity.model_dump() if getattr(item, "continuity", None) else None,
            types=labels if labels else ([] if item.type == PENDING_TYPE else None),
            type_evidence=type_evidence,
            classification_status=item.classification_status,
            other_reason=item.other_reason.model_dump() if item.other_reason else None,
            removed_types=removed_types,
            typed_attributes=typed_attributes,
            field_updates=[fact.model_dump() for fact in item.field_updates] if hasattr(item, "field_updates") else None,
            relations=[relation.model_dump() for relation in item.relations] if hasattr(item, "relations") else [],
        )
        candidates.append(candidate)
    try:
        validate_candidates(candidates, new_messages, context)
    except ValueError:
        raise ContractError("候选类型、主题或新增证据未通过业务校验") from None
    return candidates
