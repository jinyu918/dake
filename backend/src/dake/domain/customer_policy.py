"""客户月销等级口径；与事件等级、群归属和模型使用授权相互独立。"""

from typing import Literal

CustomerSegment = Literal["large", "non_large", "pending"]
CustomerFilter = Literal["all", "super_large", "large", "non_large", "pending"]

# 2026-11-17 用户确认 L6 起为大客户，其中 L9 及以上为超级大客；明确集合避免 L10 的字典序误判。
KNOWN_LEVELS = tuple(f"L{number}" for number in range(11))
LARGE_CUSTOMER_MIN_LEVEL = 6
SUPER_LARGE_CUSTOMER_MIN_LEVEL = 9
SUPER_LARGE_LEVELS = KNOWN_LEVELS[SUPER_LARGE_CUSTOMER_MIN_LEVEL:]
SEGMENT_LEVELS = {
    "large": KNOWN_LEVELS[LARGE_CUSTOMER_MIN_LEVEL:],
    "non_large": KNOWN_LEVELS[:LARGE_CUSTOMER_MIN_LEVEL],
}
CONFIRMED_LEVEL_STATE = "fresh"


def customer_segment(level: str | None, level_state: str) -> CustomerSegment:
    """只用查询成功的有效等级分类；失败保留的旧等级先待确认，不猜测有效期。"""
    if level_state == CONFIRMED_LEVEL_STATE:
        for segment, levels in SEGMENT_LEVELS.items():
            if level in levels:
                return segment
    return "pending"


def is_super_large(level: str | None, level_state: str | None) -> bool:
    """L9 及以上且等级状态为 fresh 时判定为超级大客。"""
    return level_state == CONFIRMED_LEVEL_STATE and level in SUPER_LARGE_LEVELS
