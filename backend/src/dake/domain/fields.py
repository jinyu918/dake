from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .models import EVENT_LABELS, TYPE_ATTRIBUTE_NAMES


class FieldTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["common", "label", "attribute"]
    name: str = Field(min_length=1, max_length=128)
    type_label: str | None = None

    @model_validator(mode="after")
    def valid_target(self):
        if self.kind == "common":
            valid = self.type_label is None and self.name in {"summary", "status", "severity", "assignees", "other_reason"}
        elif self.kind == "label":
            valid = self.type_label is None and self.name in EVENT_LABELS
        else:
            valid = self.name in TYPE_ATTRIBUTE_NAMES.get(self.type_label, ())
        if not valid:
            raise ValueError("字段目标不在业务字典中")
        return self


class FieldReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    message_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class FieldUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target: FieldTarget
    ref: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    business_time: str | None
    time_kind: Literal["unknown", "planned", "actual"]
    intent: Literal["supplement", "confirmation", "progress", "correction", "conflict"]
    replaces: list[str]
    reason: str = Field(min_length=1)
    evidence: list[FieldReference] = Field(min_length=1)


class FieldAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target: FieldTarget
    action: Literal["confirm", "adopt", "set", "clear", "unknown", "not_applicable", "lock", "unlock", "resolve"]
    value: str | bool | list[str] | None = None
    fact_id: str | None = None
    stage: str = "人工确认"
    scope: str = "当前事项"
    business_time: str | None = None
    time_kind: Literal["unknown", "planned", "actual"] = "unknown"
    issue_ids: list[str] = Field(default_factory=list)
    archive_dependents: bool = False

    @model_validator(mode="after")
    def consistent_time(self):
        if (self.time_kind == "unknown") != (self.business_time is None):
            raise ValueError("业务时间未知时保持空值，已知时间须区分计划和实际")
        if self.business_time is not None and not self.business_time.strip():
            raise ValueError("业务时间不能为空白")
        return self


class FieldDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    uid: str = Field(min_length=1, max_length=128)
    stream: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=0)
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    operator: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)
    actions: list[FieldAction] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def nonblank(self):
        if not self.operator.strip() or not self.reason.strip():
            raise ValueError("操作人和裁定理由不能为空")
        return self


class FieldEvidence(BaseModel):
    source: str
    message_id: str
    digest: str | None = None
    send_time: str | None = None
    conversation_id: str | None = None
    quote: str | None = None
    evidence_status: Literal["superseded"] | None = None


class FieldFact(BaseModel):
    id: str
    value: str | bool | list[str] | None
    value_state: Literal["value", "cleared", "unknown", "not_applicable"]
    stage: str
    scope: str
    business_time: str | None
    time_kind: Literal["unknown", "planned", "actual"]
    intent: str
    replaces: list[str]
    reason: str
    evidence: list[FieldEvidence]
    source: Literal["automatic", "manual", "legacy"]
    evidence_status: str
    status: str
    input_ref: str | None
    operation_id: str | None = None
    operator: str | None = None
    decided_at: str | None = None
    origin_event_id: str | None = None
    origin_fact_id: str | None = None


class FieldState(BaseModel):
    target: FieldTarget
    facts: list[FieldFact]
    adopted_ids: list[str]
    locked_ids: list[str]
    locked: bool
    decision: dict[str, JsonValue] | None
    archived: bool
    inherited_decisions: list[dict[str, JsonValue]] = Field(default_factory=list)


class FieldIssue(BaseModel):
    id: str
    field_key: str
    target: FieldTarget
    reason: str
    fact_id: str | None
    affected_ids: list[str]
    status: Literal["open", "resolved"]
    resolution: dict[str, JsonValue] | None
    origin_event_id: str | None = None
    origin_issue_id: str | None = None


class FieldOperation(BaseModel):
    operation_id: str
    request_digest: str
    operator: str
    reason: str
    decided_at: str
    version: int
    actions: list[FieldAction]
