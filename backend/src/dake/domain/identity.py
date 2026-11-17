from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .fields import FieldTarget


class FactOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str = Field(min_length=1, max_length=64)
    fact_id: str = Field(min_length=1)


class CorrectionProof(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    send_time: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class CorrectionResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target: FieldTarget
    adopt: list[FactOrigin] = Field(min_length=1)
    acknowledged_locks: list[FactOrigin] = Field(default_factory=list)
    locked: bool
    reason: str = Field(min_length=1, max_length=2000)


class SameRoundCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    uid: str = Field(min_length=1, max_length=128)
    stream: str = Field(min_length=1, max_length=128)
    retained_event_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=0)
    retained_expected_version: int = Field(ge=0)
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    operator: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)
    same_round: Literal[True]
    scope: str = Field(min_length=1, max_length=500)
    evidence: list[CorrectionProof] = Field(min_length=1, max_length=20)
    resolutions: list[CorrectionResolution] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def nonblank(self):
        if not all(value.strip() for value in (self.operator, self.reason, self.scope)):
            raise ValueError("操作人、理由和同轮范围不能为空")
        if any(not proof.quote.strip() for proof in self.evidence):
            raise ValueError("同轮纠正须有直接引文")
        if any(not resolution.reason.strip() for resolution in self.resolutions):
            raise ValueError("字段处置理由不能为空")
        return self


class IdentityCorrectionOperation(BaseModel):
    operation_id: str
    request_digest: str
    outcome: Literal["committed"]
    duplicate_event_id: str
    retained_event_id: str
    uid: str
    stream: str
    before_versions: dict[str, int]
    after_versions: dict[str, int]
    operator: str
    reason: str
    scope: str
    decided_at: str
    effective_after: str
    evidence: list[CorrectionProof]
    resolutions: list[CorrectionResolution]


class CorrectionNotCommitted(BaseModel):
    outcome: Literal["not_committed"]
    operation_id: str
