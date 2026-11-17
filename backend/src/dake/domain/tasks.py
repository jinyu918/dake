"""任务中心配置和范围校验；调度频率与消息窗口相互独立。"""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import bound, duration
from .models import TZ
from .customer_policy import KNOWN_LEVELS
from ..ingestion.client import validate_uid

TASK_MAX_CUSTOMERS = 500
TASK_MAX_DAYS = 31
TASK_ROUND_LIMIT = timedelta(days=1)


class TaskConfig(BaseModel):
    """可保存但不自动启用的配置；指定客户也遵守等级及排除规则。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    operation: Literal["sync", "analyze"] = "analyze"
    scope: Literal["levels", "selected"] = "levels"
    levels: list[str] = Field(default_factory=lambda: list(KNOWN_LEVELS[6:]), min_length=1, max_length=11)
    uids: list[str] = Field(default_factory=list, max_length=TASK_MAX_CUSTOMERS)
    include_new: bool = True
    excluded_uids: list[str] = Field(default_factory=list, max_length=TASK_MAX_CUSTOMERS)
    excluded_groups: list[str] = Field(default_factory=list, max_length=TASK_MAX_CUSTOMERS)
    mode: Literal["manual", "automatic"] = "manual"
    frequency: Literal["5m", "15m", "1h", "1d"] = "5m"
    interval: Literal["5m", "15m", "1h", "1d"] = "15m"
    start_mode: Literal["specified", "activation"] = "specified"
    start: str | None = None
    end: str | None = None
    profile_ttl: Literal["5m", "15m", "1h", "6h", "1d"] = "1h"
    lookback: Literal["5m", "15m", "1h", "6h", "1d", "2d"] = "15m"
    retries: int = Field(default=1, ge=0, le=3)
    retry_delay: Literal["1m", "5m", "15m"] = "1m"

    @model_validator(mode="after")
    def valid_scope(self):
        """过滤规则不接受空白名称、未知等级或未界定的时间范围。"""
        self.name = self.name.strip()
        if not self.name or any(level not in KNOWN_LEVELS for level in self.levels):
            raise ValueError("名称或等级无效")
        for uid in self.uids + self.excluded_uids:
            validate_uid(uid)
        if any(not group.strip() or len(group) > 128 for group in self.excluded_groups):
            raise ValueError("群标识无效")
        if self.scope == "selected" and not self.uids:
            raise ValueError("请选择客户")
        for field in ("levels", "uids", "excluded_uids", "excluded_groups"):
            setattr(self, field, list(dict.fromkeys(getattr(self, field))))
        if self.start_mode == "specified" or self.mode == "manual":
            if not self.start:
                raise ValueError("缺少起始时间")
            start = bound(self.start)
            if start > datetime.now(TZ):
                raise ValueError("起始时间不能在未来")
            self.start = start.isoformat()
        if self.mode == "manual":
            if not self.end:
                raise ValueError("手动运行需要结束时间")
            end = bound(self.end, end=True)
            if not bound(self.start) < end <= datetime.now(TZ) or end - bound(self.start) > timedelta(days=TASK_MAX_DAYS):
                raise ValueError("范围必须已发生且不超过31天")
            self.end = end.isoformat()
        else:
            self.end = None
        return self


def eligibility(config, profile, frozen_uids=None, now=None):
    """返回不准入原因；等级错误不会被默认为普通客户或被指定名单绕过。"""
    now = now or datetime.now(TZ)
    uid = profile["uid"]
    if uid in config["excluded_uids"]:
        return "客户在排除清单中"
    if config["scope"] == "selected" and uid not in config["uids"]:
        return "不在指定客户范围"
    if not config["include_new"] and frozen_uids is not None and uid not in frozen_uids:
        return "未启用自动纳入新客户"
    synced = profile.get("level_synced_at")
    if (profile.get("level_state") != "fresh" or not synced
            or now - bound(synced) > duration(config["profile_ttl"])):
        return "等级待确认或资料已过期"
    if profile.get("level") not in config["levels"]:
        return "等级不在所选范围"
    return None
