"""可配置的发送账号身份；不根据名称自动推定机器人。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator
from .models import SenderKind
from ..ingestion.client import validate_uid


class SenderRule(BaseModel):
    """账号、客户与群明确限定范围，备注只供用户维护。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    uid: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=32)
    sender_id: str = Field(min_length=1, max_length=256)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    kind: SenderKind = "bot"
    label: str = Field(default="", max_length=256)
    basis: str = Field(min_length=1, max_length=500)
    enabled: bool = True

    @field_validator("uid")
    @classmethod
    def valid_uid(cls, value):
        """沿用官方客户标识校验。"""
        validate_uid(value)
        return value
