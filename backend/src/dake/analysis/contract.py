"""抽取字段的基础合约与证据校验，供正式归属合约复用。"""

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..domain.models import Candidate, PENDING_TYPE, TYPE_ATTRIBUTE_NAMES, Event, Message
from ..domain.priority_policy import PRIORITY_EVIDENCE_FIELDS, ImpactLevel, UrgencyLevel
from ..domain.status_policy import LifecycleStatus, Resolution, WaitingReason, validate_status_shape
from .validation import priority_quote_matches, validate_candidates

LabelLiteral = Literal["商机", "流失", "排障", "资源需求", "功能需求", "商务需求", "其他"]
EventTypeLiteral = Literal["商机", "流失", "排障", "资源需求", "功能需求", "商务需求", "其他", "待分类"]
TypedAttributeNameLiteral = Literal[
    "交易对象与采购范围",
    "采购意向与评估事实",
    "计划规模",
    "预算或交易金额",
    "采购/上线计划时间",
    "成交事实及范围",
    "流失对象与范围",
    "风险、计划与实际变化",
    "原因与触发条件",
    "减少或转移规模",
    "去向与执行时间",
    "挽留或退出办理结果",
    "异常对象与环境",
    "症状与对照表现",
    "客户影响",
    "发生及影响范围",
    "排查发现与根因",
    "处置与验证结果",
    "恢复事实",
    "资源对象与交付范围",
    "请求动作与用途",
    "现有可用量",
    "目标总量与增减量",
    "资源缺口",
    "期望交付与承诺",
    "审批、交付与可用验证",
    "能力对象与使用场景",
    "当前行为及限制",
    "期望行为与改善目标",
    "验收条件",
    "期望可用时间",
    "评估与承诺范围",
    "上线与需求验证结果",
    "办理对象与事项范围",
    "价格与金额口径",
    "合同与商务条件",
    "续费或终止安排",
    "账单与发票要求",
    "办理期限与约定",
    "办理事实与结果",
]


class ContractError(ValueError):
    """不携带模型正文或聊天内容的校验错误。"""


class StrictObject(BaseModel):
    """拒绝多余字段与隐式类型转换。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class Fact(StrictObject):
    """附有原文片段及消息引用的逐字字段值。"""

    value: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    quote: str = Field(min_length=1)


class Attribute(Fact):
    """可扩展的属性，不自造官方枚举。"""

    name: str = Field(min_length=1)


class TypeLabel(StrictObject):
    """一个类型标签及其独立的直接原文依据。"""

    type: LabelLiteral
    evidence_ids: list[str] = Field(min_length=1)
    quote: str = Field(min_length=1)


class RemovedTypeLabel(TypeLabel):
    """有本轮明确更正依据支持的标签撤销。"""


class TypedAttribute(Attribute):
    """明确隶属某一已采用类型标签的专属属性值。"""

    name: TypedAttributeNameLiteral
    type_label: LabelLiteral


class StatusProposal(StrictObject):
    """结构化生命周期状态提案；由后端确定性规则做完成标准与防倒退核验。"""

    status: LifecycleStatus
    waiting_reason: WaitingReason | None = None
    resolution: Resolution | None = None
    completed_labels: list[LabelLiteral] | None = None
    reason: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    quote: str = Field(min_length=1)


class PriorityEvidence(StrictObject):
    evidence_ids: list[str] = Field(min_length=1)
    quote: str = Field(min_length=1)


class PriorityProposal(StrictObject):
    """客观业务影响与时间紧迫度信号提案；客户等级加权由后端独立计算。"""

    impact: ImpactLevel
    urgency: UrgencyLevel
    reason: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    quote: str = Field(min_length=1)


class AffectedCustomerEvidence(PriorityEvidence):
    customer: str = Field(min_length=1)
    uid: str | None
    status: Literal["confirmed", "pending"]
    impact_quote: str | None


class EvidencePriorityProposal(PriorityProposal):
    impact_evidence: PriorityEvidence | None = None
    urgency_evidence: PriorityEvidence | None = None
    escalated_emotion: PriorityEvidence | None = None
    strong_followup: PriorityEvidence | None = None
    affected_customers: list[AffectedCustomerEvidence] = Field(default_factory=list, max_length=20)


class ExtractedCandidate(StrictObject):
    """模型候选与关联意图；是否真正更新仍由业务匹配器与确定性策略决定。"""

    type: EventTypeLiteral
    topic_key: str | None
    summary: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    status: Fact | None = None
    severity: Fact | None = None
    assignees: list[Fact] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    extensions: list[Attribute] = Field(default_factory=list)
    decision: Literal["new", "update", "review"]
    referenced_event_id: str | None
    decision_reason: str = Field(min_length=1)
    type_labels: list[TypeLabel] = Field(default_factory=list)
    removed_types: list[RemovedTypeLabel] = Field(default_factory=list)
    typed_attributes: list[TypedAttribute] = Field(default_factory=list)
    classification_status: Literal["已分类", "待分类"] = "已分类"
    other_reason: Fact | None = None
    status_proposal: StatusProposal | None = None
    priority_proposal: PriorityProposal | None = None


class ExtractionOutput(StrictObject):
    """版本化的模型输出，不包含由程序生成的时间和事件编号。"""

    schema_version: Literal["1.0"]
    candidates: list[ExtractedCandidate]


class EvidenceExtractedCandidate(ExtractedCandidate):
    priority_proposal: EvidencePriorityProposal | None = None


class EvidenceExtractionOutput(ExtractionOutput):
    candidates: list[EvidenceExtractedCandidate]


def strict_schema(schema: dict) -> dict:
    """递归将所有 object 节点的属性列入 required 并禁止额外字段，适配严格 JSON Schema。"""
    value = deepcopy(schema)
    pending = [value]
    while pending:
        item = pending.pop()
        if item.get("type") == "object":
            item["additionalProperties"] = False
            item["required"] = list(item.get("properties", {}))
        for nested in item.values():
            if isinstance(nested, dict):
                pending.append(nested)
            elif isinstance(nested, list):
                pending.extend(entry for entry in nested if isinstance(entry, dict))
    return value


def parse_output(
    content: str,
    new_messages: list[Message],
    context: list[Message],
    existing_events: list[Event],
    *,
    output_model=ExtractionOutput,
    require_type_labels: bool = False,
) -> list[Candidate]:
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
        adopted_labels = labels or ([] if item.type == PENDING_TYPE else [item.type])
        if item.classification_status == "待分类":
            if item.type != PENDING_TYPE or adopted_labels:
                raise ContractError("待分类事项不能同时采用六类或其他标签")
        elif item.type == PENDING_TYPE:
            raise ContractError("待分类状态必须显式标记 classification_status")
        elif item.type not in adopted_labels:
            raise ContractError("主类型必须包含在类型标签中")
        if item.type == "其他" or "其他" in adopted_labels:
            old_event_for_other = old.get(item.referenced_event_id) if item.decision in {"update", "duplicate"} else None
            if item.other_reason is None and not (old_event_for_other and old_event_for_other.other_reason):
                raise ContractError("其他事项必须说明排除六类的直接理由")

        explicit = not issubclass(output_model, ExtractionOutput)
        if require_type_labels and item.type != PENDING_TYPE and not item.type_labels:
            raise ContractError("正式输出必须为每个采用类型提供逐项证据")

        if item.decision in {"update", "duplicate"}:
            event = old.get(item.referenced_event_id)
            event_types = set(event.types or ([] if event.type == PENDING_TYPE else [event.type])) if event else set()
            item_types = set(adopted_labels)
            if event is None:
                raise ContractError("关联必须引用本次输入中的已有事件")
            if not explicit and not event_types.intersection(item_types) and item.type != PENDING_TYPE:
                raise ContractError("旧版关联必须引用同类型的已有事件")
            if not explicit and event.topic_key != item.topic_key:
                raise ContractError("旧版更新必须复用原主题")
        elif item.referenced_event_id is not None:
            raise ContractError("新建或待确认不得指定旧事件")

        for facts in (item.attributes, item.extensions):
            if len({fact.name for fact in facts}) != len(facts):
                raise ContractError("属性名称不得重复")

        # 逐字事实与枚举提案统一核对消息范围和连续原文；排除理由与枚举提案不要求分析结论字面出现在原话中。
        verbatim_facts = [(name, getattr(item, name)) for name in ("status", "severity")]
        verbatim_facts += [
            (f"{name}[{index}]", fact)
            for name in ("attributes", "extensions", "assignees")
            for index, fact in enumerate(getattr(item, name))
        ]
        verbatim_facts += [(f"typed_attributes[{index}]", fact) for index, fact in enumerate(item.typed_attributes)]

        quote_only_items = [(f"type_labels[{index}]", label) for index, label in enumerate(item.type_labels)]
        quote_only_items += [(f"removed_types[{index}]", label) for index, label in enumerate(item.removed_types)]
        if item.other_reason is not None:
            quote_only_items.append(("other_reason", item.other_reason))
        if item.status_proposal is not None:
            quote_only_items.append(("status_proposal", item.status_proposal))
        if item.priority_proposal is not None:
            quote_only_items.append(("priority_proposal", item.priority_proposal))
            for field in PRIORITY_EVIDENCE_FIELDS:
                fact = getattr(item.priority_proposal, field, None)
                if fact is not None:
                    quote_only_items.append((f"priority_proposal.{field}", fact))

            if isinstance(item.priority_proposal, EvidencePriorityProposal):
                quote_only_items.extend((f"priority_proposal.affected_customers[{i}]", fact)
                                        for i, fact in enumerate(item.priority_proposal.affected_customers))

        errors = []
        if item.other_reason is not None and not item.other_reason.value.strip():
            errors.append(f"candidates[{candidate_index}].other_reason：排除六类理由不能为空白")
        for path, fact in verbatim_facts:
            if fact is None:
                continue
            dedup_refs = list(dict.fromkeys(fact.evidence_ids))
            fact.evidence_ids = dedup_refs
            refs = set(dedup_refs)
            if not refs or not refs.issubset(ids):
                errors.append(f"candidates[{candidate_index}].{path}：字段证据必须是候选证据的无重复子集")
                continue
            quoted = [messages[key] for key in refs if fact.quote.strip() and fact.quote in messages[key].content]
            if not quoted:
                errors.append(f"candidates[{candidate_index}].{path}：字段引文无法在所引用消息中核对")
                continue
            is_assignee = any(fact is assignee for assignee in item.assignees)
            if fact.value not in fact.quote and not (
                is_assignee and any(fact.value == message.sender_id for message in quoted)
            ):
                errors.append(f"candidates[{candidate_index}].{path}：字段值必须来自引文或明确认领者标识")

        for path, proposal in quote_only_items:
            dedup_refs = list(dict.fromkeys(proposal.evidence_ids))
            proposal.evidence_ids = dedup_refs
            refs = set(dedup_refs)
            allow_context_refs = item.decision in {"update", "duplicate"} and (
                path.startswith("type_labels[") or path == "other_reason"
            )
            allowed_scope = set(messages) if allow_context_refs else ids
            if not refs or not refs.issubset(allowed_scope):
                errors.append(f"candidates[{candidate_index}].{path}：提案证据必须是候选证据的无重复子集")
                continue
            quoted_messages = [messages[key] for key in refs]
            valid_quote = (priority_quote_matches(proposal.quote, quoted_messages) if path.startswith("priority_proposal")
                           else bool(proposal.quote.strip() and any(proposal.quote in msg.content for msg in quoted_messages)))
            if not valid_quote:
                errors.append(f"candidates[{candidate_index}].{path}：提案引文无法在所引用消息中核对")

        if item.status_proposal is not None:
            if not item.status_proposal.reason.strip():
                errors.append(f"candidates[{candidate_index}].status_proposal：状态理由不能为空白")
            try:
                validate_status_shape(
                    item.status_proposal.status,
                    item.status_proposal.waiting_reason,
                    item.status_proposal.resolution,
                )
            except ValueError as err:
                errors.append(f"candidates[{candidate_index}].status_proposal：{err}")
            if item.status_proposal.completed_labels is not None:
                if len(set(item.status_proposal.completed_labels)) != len(item.status_proposal.completed_labels):
                    errors.append(f"candidates[{candidate_index}].status_proposal.completed_labels：已完成标签不得重复")

        if isinstance(item.priority_proposal, EvidencePriorityProposal):
            for axis in ("impact", "urgency"):
                if getattr(item.priority_proposal, axis) != "unknown" and not getattr(item.priority_proposal, f"{axis}_evidence"):
                    errors.append(f"candidates[{candidate_index}].priority_proposal.{axis}：已知维度必须有独立证据")
        if item.priority_proposal is not None and not item.priority_proposal.reason.strip():
            errors.append(f"candidates[{candidate_index}].priority_proposal：优先级理由不能为空白")

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
        typed_attributes: dict[str, dict[str, dict]] = {}
        demoted_typed_attributes: list[dict] = []
        for fact in item.typed_attributes:
            allowed_names = TYPE_ATTRIBUTE_NAMES.get(fact.type_label, ())
            if fact.type_label not in adopted_labels or fact.name not in allowed_names:
                demoted_typed_attributes.append(fact.model_dump())
                continue
            dumped = fact.model_dump()
            existing_attr = typed_attributes.get(fact.type_label, {}).get(fact.name)
            if existing_attr is not None:
                demoted_typed_attributes.append({**existing_attr, "superseded": True})
                dumped["evidence_ids"] = list(dict.fromkeys([*existing_attr["evidence_ids"], *dumped["evidence_ids"]]))
            typed_attributes.setdefault(fact.type_label, {})[fact.name] = dumped

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

        flat_attributes = {fact.name: fact.value for fact in item.attributes}
        for attrs in typed_attributes.values():
            for name, fact in attrs.items():
                flat_attributes.setdefault(name, fact["value"])
        ext_payload = {
            "extraction_v1": item.model_dump(
                exclude={"summary", "evidence_ids", "basis", "possible_event_ids", "changes", "continuity"}
            ),
        }
        if demoted_typed_attributes:
            ext_payload["demoted_typed_attributes"] = demoted_typed_attributes

        candidate = Candidate(
            type=item.type,
            topic_key=item.topic_key,
            summary=item.summary,
            evidence_ids=item.evidence_ids,
            status=item.status.value if item.status else None,
            severity=item.severity.value if item.severity else None,
            assignees=[fact.value for fact in item.assignees],
            attributes=flat_attributes,
            extensions=ext_payload,
            ambiguous=item.decision == "review",
            referenced_event_id=item.referenced_event_id,
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
            status_proposal=item.status_proposal.model_dump() if item.status_proposal else None,
            priority_proposal=item.priority_proposal.model_dump(exclude_none=True) if item.priority_proposal else None,
        )
        candidates.append(candidate)
    try:
        validate_candidates(candidates, new_messages, context)
    except ValueError:
        raise ContractError("候选类型、主题或新增证据未通过业务校验") from None
    return candidates
