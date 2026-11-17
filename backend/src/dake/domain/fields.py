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
    source: Literal["automatic", "legacy"]
    evidence_status: str
    status: str
    input_ref: str | None


class FieldState(BaseModel):
    target: FieldTarget
    facts: list[FieldFact]
    adopted_ids: list[str]
    archived: bool


class FieldIssue(BaseModel):
    id: str
    field_key: str
    target: FieldTarget
    reason: str
    fact_id: str | None
    affected_ids: list[str]
    status: Literal["open", "resolved"]
    resolution: dict[str, JsonValue] | None
