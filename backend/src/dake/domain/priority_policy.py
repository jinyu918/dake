"""事件业务优先级口径、查表矩阵与超级大客加权规则；本地可配置工程缺省。"""

from copy import deepcopy
from typing import Literal

from .customer_policy import CONFIRMED_LEVEL_STATE, KNOWN_LEVELS, customer_segment, is_super_large

PRIORITIES = ("P0", "P1", "P2", "P3", "待评估")
PRIORITY_RANKS = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "待评估": 4}
IMPACT_LEVELS = ("critical", "high", "medium", "low", "unknown")
URGENCY_LEVELS = ("immediate", "soon", "normal", "unknown")

PriorityLevel = Literal["P0", "P1", "P2", "P3", "待评估"]
ImpactLevel = Literal["critical", "high", "medium", "low", "unknown"]
UrgencyLevel = Literal["immediate", "soon", "normal", "unknown"]

# 本地工程缺省查表矩阵：unknown 紧迫度下，critical 按即时处理，其余已明确影响按常规期限起评。
BASE_PRIORITY_MATRIX: dict[str, dict[str, str]] = {
    "critical": {"immediate": "P0", "soon": "P0", "normal": "P0", "unknown": "P0"},
    "high": {"immediate": "P1", "soon": "P1", "normal": "P1", "unknown": "P1"},
    "medium": {"immediate": "P1", "soon": "P2", "normal": "P2", "unknown": "P2"},
    "low": {"immediate": "P2", "soon": "P2", "normal": "P3", "unknown": "P3"},
    "unknown": dict.fromkeys(URGENCY_LEVELS, "待评估"),
}

ITEM_PRIORITY_RULE_VERSION = "item-priority-v1"
PRIORITY_EVIDENCE_FIELDS = ("impact_evidence", "urgency_evidence", "escalated_emotion", "strong_followup")

SUPER_LARGE_BOOST_MAP = {
    "P3": "P2",
    "P2": "P1",
    # 封顶 P1：禁止仅凭客户身份进入代表真实生产中断或数据安全风险的 P0。
    "P1": "P1",
    "P0": "P0",
    "待评估": "待评估",
}


def normalize_legacy_priority(severity: str | None) -> tuple[str, str | None]:
    """保守映射存量事件等级；未知置为待评估，旧自由文本原样保留在 legacy_severity。"""
    if severity in PRIORITIES:
        return severity, None
    if severity is None or severity.strip() in {"", "未知"}:
        return "待评估", None
    return "待评估", severity


def customer_level_number(level: str | None, level_state: str | None) -> int:
    """解析有效客户等级数字序号（0~10），避免字符串字典序将 L10 排在 L2 后。"""
    if level_state == CONFIRMED_LEVEL_STATE and level in KNOWN_LEVELS:
        return int(level[1:])
    return -1


def calculate_base_priority(impact: str, urgency: str) -> str:
    """根据影响与紧迫度计算不含客户身份加权的基础优先级。"""
    if impact not in IMPACT_LEVELS:
        raise ValueError("业务影响档位不在允许枚举中")
    if urgency not in URGENCY_LEVELS:
        raise ValueError("时间紧迫度档位不在允许枚举中")
    return BASE_PRIORITY_MATRIX[impact][urgency]


def apply_customer_tier(base_priority: str, level: str | None = None, level_state: str | None = None) -> dict:
    """结合客户目录等级计算最终优先级；L9/L10 提一档并封顶 P1，未知等级标明待确认。"""
    if base_priority not in PRIORITIES:
        raise ValueError("基础优先级不在允许档位中")
    state = level_state or "unknown"
    segment = customer_segment(level, state)
    super_large = is_super_large(level, state)
    priority = SUPER_LARGE_BOOST_MAP[base_priority] if super_large else base_priority
    vip_boosted = priority != base_priority
    level_note = None
    if base_priority != "待评估":
        if vip_boosted:
            level_note = f"因 {level} 超级大客由基础 {base_priority} 提升一档至 {priority}"
        elif super_large and base_priority in {"P0", "P1"}:
            level_note = f"{level} 超级大客基础已为 {base_priority}，同档优先排序"
        elif segment == "pending":
            level_note = "客户等级待确认，暂按基础优先级展示"
    return dict(
        priority=priority,
        base_priority=base_priority,
        vip_boosted=vip_boosted,
        customer_level=level if customer_level_number(level, state) >= 0 else None,
        level_state=state,
        level_note=level_note,
    )


def _has_dedicated_impact(proposal: dict) -> bool:
    evidence = proposal.get("impact_evidence") or {}
    return bool(evidence.get("evidence_ids") and (evidence.get("quote") or "").strip())


def merge_priority_proposals(left: dict | None, right: dict | None) -> dict | None:
    if left is None or right is None:
        return deepcopy(right if left is None else left)
    impact_source = min((right, left), key=lambda proposal: IMPACT_LEVELS.index(
        "unknown" if proposal["impact"] == "critical" and not _has_dedicated_impact(proposal) else proposal["impact"]
    ))
    urgency_source = min((right, left), key=lambda proposal: URGENCY_LEVELS.index(proposal["urgency"]))
    merged = {**impact_source, "urgency": urgency_source["urgency"]}
    for field, source in (("impact_evidence", impact_source), ("urgency_evidence", urgency_source)):
        fact = source.get(field)
        axis = field.removesuffix("_evidence")
        if fact is None and source[axis] not in {"unknown", "critical"}:
            fact = {key: source[key] for key in ("quote", "evidence_ids") if key in source} or None
        if fact:
            merged[field] = fact
        else:
            merged.pop(field, None)
    for field in PRIORITY_EVIDENCE_FIELDS[2:]:
        if fact := right.get(field) or left.get(field):
            merged[field] = fact
    merged["affected_customers"] = merge_affected_customers(left.get("affected_customers", []), right.get("affected_customers", []))
    return deepcopy(merged)


def merge_affected_customers(previous: list[dict], incoming: list[dict]) -> list[dict]:
    facts = {}
    for fact in (*previous, *incoming):
        key = ("uid", fact["uid"]) if fact.get("uid") else ("name", fact["customer"])
        if fact["status"] == "confirmed" or facts.get(key, {}).get("status") != "confirmed":
            facts[key] = fact
    confirmed_by_name: dict[str, set[str]] = {}
    for fact in facts.values():
        if fact["status"] == "confirmed" and fact.get("uid"):
            confirmed_by_name.setdefault(fact["customer"].strip(), set()).add(fact["uid"])
    return deepcopy([fact for fact in facts.values() if fact.get("uid") or fact["status"] != "pending"
                     or len(confirmed_by_name.get(fact["customer"].strip(), set())) != 1])


def response_priority(customer_priority: str, signals: dict) -> str:
    if signals.get("escalated_emotion") and customer_priority != "P0":
        return "P1"
    if signals.get("strong_followup") and customer_priority == "P3":
        return "P2"
    return customer_priority


def evaluate_priority_proposal(
    proposal: dict | None,
    *,
    current_priority: str = "待评估",
    current_base_priority: str = "待评估",
    current_detail: dict | None = None,
    customer_profile: dict | None = None,
    evidence_refs: list[dict] | None = None,
    late: bool = False,
    created: bool = False,
) -> dict:
    """仅当本轮有新依据时重评优先级；迟到消息不压低已有更高判断，客户沉默不自动降级。"""
    level = (customer_profile or {}).get("level")
    level_state = (customer_profile or {}).get("level_state") or "unknown"
    if proposal is None:
        # 若已有基础优先级且客户等级刷新（如刚同步出 L10），允许在不改变事实的前提下刷新等级加权。
        if current_base_priority != "待评估" and customer_profile is not None:
            recalculated = apply_customer_tier(current_base_priority, level, level_state)
            detail = deepcopy(current_detail or {})
            customer_priority = recalculated["priority"]
            priority = response_priority(customer_priority, detail)
            if detail.get("rule_version") == ITEM_PRIORITY_RULE_VERSION:
                recalculated.update(customer_priority=customer_priority, pre_timer_priority=priority, priority=priority)
            if priority != current_priority or any(
                detail and detail.get(key) != recalculated[key] for key in ("customer_level", "level_state", "level_note")
            ):
                detail.update(recalculated)
                return dict(
                    adopted=True,
                    priority=priority,
                    base_priority=recalculated["base_priority"],
                    detail=detail,
                )
        return dict(
            adopted=False,
            priority=current_priority,
            base_priority=current_base_priority,
            detail=deepcopy(current_detail),
        )

    unverified_critical = proposal["impact"] == "critical" and not _has_dedicated_impact(proposal)
    impact = "unknown" if unverified_critical else proposal["impact"]
    urgency = proposal["urgency"]
    reason = (proposal.get("reason") or "").strip()
    base = calculate_base_priority(impact, urgency)
    refs = deepcopy(evidence_refs or proposal.get("evidence_refs", []))

    if base == "待评估" and not created and current_base_priority != "待评估" and not any(
        proposal.get(field) for field in PRIORITY_EVIDENCE_FIELDS[2:]
    ):
        return dict(
            adopted=False,
            priority=current_priority,
            base_priority=current_base_priority,
            detail=deepcopy(current_detail),
        )

    if late and not created and current_base_priority != "待评估":
        # 迟到补证只有在揭示更高客观影响时才升级，不把当前更高优先级拉低。
        if PRIORITY_RANKS[base] >= PRIORITY_RANKS[current_base_priority]:
            return dict(
                adopted=False,
                priority=current_priority,
                base_priority=current_base_priority,
                detail=deepcopy(current_detail),
            )

    objective = dict(impact=impact, urgency=urgency, reason=reason, evidence_refs=refs)
    objective.update({field: deepcopy(proposal[field]) for field in PRIORITY_EVIDENCE_FIELDS[:2] if proposal.get(field)})
    if base == "待评估" and not created and current_base_priority != "待评估":
        base = current_base_priority
        previous = current_detail or {}
        objective = {field: deepcopy(previous[field]) for field in (*objective, *PRIORITY_EVIDENCE_FIELDS[:2])
                     if field in previous}
    tiered = apply_customer_tier(base, level, level_state)
    customer_priority = tiered["priority"]
    signals = {field: deepcopy(proposal[field]) for field in PRIORITY_EVIDENCE_FIELDS[2:] if proposal.get(field)}
    pre_timer_priority = response_priority(customer_priority, signals)
    detail = dict(
        **tiered,
        **objective,
        **signals,
        source="automatic",
        customer_priority=customer_priority,
        pre_timer_priority=pre_timer_priority,
        review_required=unverified_critical or (base == "待评估" and bool(signals)),
        rule_version=ITEM_PRIORITY_RULE_VERSION,
    )
    detail["priority"] = pre_timer_priority
    return dict(
        adopted=True,
        priority=pre_timer_priority,
        base_priority=tiered["base_priority"],
        detail=detail,
    )


def event_priority_sort_key(event: dict, customer_profile: dict | None = None) -> tuple:
    """列表排序复合键：未结束优先 -> 优先级档位 -> 超级大客 -> 客户等级序号 -> 最近消息时间倒序。"""
    from datetime import datetime

    lifecycle = event.get("lifecycle_status") or "状态未明"
    ended = 1 if lifecycle in {"已解决", "已取消"} else 0
    priority = event.get("priority") or "待评估"
    rank = PRIORITY_RANKS.get(priority, 4)
    detail = event.get("priority_detail") or {}
    profile = {} if detail.get("rule_version") == ITEM_PRIORITY_RULE_VERSION else customer_profile or {}
    level = profile.get("level") or detail.get("customer_level")
    state = profile.get("level_state") or detail.get("level_state")
    super_rank = 0 if is_super_large(level, state) else 1
    level_num = customer_level_number(level, state)
    end_ts = 0.0
    raw_end = event.get("end_time")
    if isinstance(raw_end, str) and raw_end:
        try:
            end_ts = datetime.fromisoformat(raw_end).timestamp()
        except ValueError:
            end_ts = 0.0
    elif isinstance(raw_end, datetime):
        end_ts = raw_end.timestamp()
    return (ended, rank, super_rank, -level_num, -end_ts)
