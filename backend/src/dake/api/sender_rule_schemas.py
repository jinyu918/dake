"""机器人配置的页面契约；不提供聊天原文或外部服务调用。"""

from pydantic import ConfigDict, Field
from .schemas import APIModel
from ..domain.models import SenderKind
from ..domain.sender_rules import SenderRule


class SenderRuleResponse(SenderRule):
    model_config = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=True)
    rule_id: str


class SenderRuleList(APIModel):
    version: str
    items: list[SenderRuleResponse]


class SenderRuleSave(APIModel):
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rule: SenderRule


class SenderCandidate(APIModel):
    uid: str
    source: str
    sender_id: str
    conversation_id: str
    sender_name: str
    conversation_name: str
    message_count: int
    last_seen_at: str
    sender_kind: SenderKind
    rule_id: str
    configured: bool


class SenderCandidateList(APIModel):
    items: list[SenderCandidate]
    total: int
