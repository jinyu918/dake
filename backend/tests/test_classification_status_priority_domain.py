"""验证分类、生命周期状态与业务优先级的领域纯规则和存量兼容映射。"""

import pytest

from dake.domain.manual_controls import (
    MAX_REVIEW_FLAG_CHARS,
    compose_review_flag,
    is_locked,
    record_lock_conflict,
)
from dake.domain.models import EVENT_LABELS, PENDING_TYPE, Event, parse_time
from dake.domain.priority_policy import (
    apply_customer_tier,
    calculate_base_priority,
    customer_level_number,
    evaluate_priority_proposal,
    merge_priority_proposals,
    merge_affected_customers,
    event_priority_sort_key,
)
from dake.domain.status_policy import evaluate_status_proposal, validate_status_shape


def _sample_event(**overrides) -> Event:
    moment = parse_time("2038-06-01T10:00:00+08:00")
    values = dict(
        event_id="evt-syn-1",
        uid="synthetic-uid",
        stream="synthetic-stream",
        type="排障",
        topic_key="存储/样例空间/下载失败",
        start_time=moment,
        end_time=moment,
        status="未知",
        severity="未知",
        summary="合成空间下载报 500 错误。",
        created_on=moment,
    )
    values.update(overrides)
    return Event(**values)


def test_legacy_event_payload_normalizes_without_inventing_evidence():
    moment = "2038-06-01T10:00:00+08:00"
    legacy = dict(
        event_id="evt-legacy",
        uid="synthetic-uid",
        stream="synthetic-stream",
        type="商务需求",
        topic_key="账单/核对",
        start_time=moment,
        end_time=moment,
        created_on=moment,
        status="虚构自由状态词",
        severity="高",
        summary="合成账单核对诉求。",
        evidence=[],
        updated_by=[{"action": "created", "message_ids": ["1"]}],
    )
    event = Event.from_dict(legacy)
    assert event.types == ["商务需求"]
    assert event.classification_evidence_status == "未记录"
    assert event.classification_status == "已分类"
    assert event.lifecycle_status == "状态未明"
    assert event.status_detail["legacy_status"] == "虚构自由状态词"
    assert event.priority == "待评估"
    assert event.base_priority == "待评估"
    assert event.priority_detail["legacy_severity"] == "高"
    assert event.version == 1
    assert Event.from_dict(event.to_dict()) == event


def test_pending_classification_blocks_resolved_status():
    result = evaluate_status_proposal(
        dict(status="已解决", waiting_reason=None, resolution="已答复或已交付", reason="回复已完成"),
        classification_status="待分类",
        active_labels=[],
        created=True,
    )
    assert result["lifecycle_status"] == "跟进中"
    assert result["resolution"] is None
    assert result["review_items"][0]["kind"] == "pending_classification_completion"


def test_multi_label_slowest_obligation_keeps_event_in_progress():
    result = evaluate_status_proposal(
        dict(
            status="已解决",
            waiting_reason=None,
            resolution="已答复或已交付",
            completed_labels=["资源需求"],
            reason="配额已开通，报价单仍在准备",
        ),
        current_status="跟进中",
        active_labels=["资源需求", "商务需求"],
    )
    assert result["lifecycle_status"] == "跟进中"
    assert result["resolution"] is None
    assert "商务需求" in result["detail"]["note"]


def test_incident_support_claim_vs_customer_recovery_and_reopen():
    # 支持方声称修复但无已确认客户恢复事实：自动归为支撑声明已修复。
    support_done = evaluate_status_proposal(
        dict(status="已解决", waiting_reason=None, resolution="客户确认恢复", reason="工程师称已修复"),
        current_status="跟进中",
        active_labels=["排障"],
        has_customer_recovery=False,
        evidence_roles={"internal"},
    )
    assert support_done["lifecycle_status"] == "已解决"
    assert support_done["resolution"] == "支撑声明已修复"

    # 解决后同一问题反馈未好：允许重开回跟进中并清空结束结果。
    reopened = evaluate_status_proposal(
        dict(status="跟进中", waiting_reason=None, resolution=None, reason="客户反馈线上仍然报错"),
        current_status="已解决",
        current_resolution="支撑声明已修复",
        active_labels=["排障"],
    )
    assert reopened["lifecycle_status"] == "跟进中"
    assert reopened["resolution"] is None
    assert reopened["reopened"] is True


def test_late_status_proposal_does_not_regress_terminal_status():
    result = evaluate_status_proposal(
        dict(status="等待中", waiting_reason="等待客户", resolution=None, reason="补发较早的日志请求"),
        current_status="已解决",
        current_resolution="已答复或已交付",
        active_labels=["资源需求"],
        late=True,
    )
    assert result["adopted"] is False
    assert result["lifecycle_status"] == "已解决"
    assert result["review_items"][0]["kind"] == "late_status_conflict"


@pytest.mark.parametrize(
    "status,waiting_reason,resolution,valid",
    [
        ("等待中", "等待客户", None, True),
        ("等待中", None, None, False),
        ("等待中", "等待客户", "已撤回", False),
        ("已解决", None, "已答复或已交付", True),
        ("已解决", None, "已拒绝", False),
        ("已取消", None, "确认不成立", True),
        ("跟进中", None, "已撤回", False),
    ],
)
def test_validate_status_shape_enforces_three_layer_constraints(status, waiting_reason, resolution, valid):
    if valid:
        validate_status_shape(status, waiting_reason, resolution)
    else:
        with pytest.raises(ValueError):
            validate_status_shape(status, waiting_reason, resolution)


def test_priority_matrix_and_super_large_customer_cap():
    assert calculate_base_priority("unknown", "immediate") == "待评估"
    assert calculate_base_priority("critical", "unknown") == "P0"
    assert calculate_base_priority("low", "unknown") == "P3"
    assert calculate_base_priority("medium", "soon") == "P2"

    boosted_p3 = apply_customer_tier("P3", "L10", "fresh")
    assert (boosted_p3["objective_priority"], boosted_p3["base_priority"], boosted_p3["priority"]) == ("P3", "P2", "P1")
    assert boosted_p3["vip_boosted"] is True
    assert "L10 战略级客户" in boosted_p3["level_note"]

    # L9 中等影响 P2 -> 提升一档至 P1。
    boosted_p2 = apply_customer_tier("P2", "L9", "fresh")
    assert boosted_p2["priority"] == "P1" and boosted_p2["vip_boosted"] is True
    assert "L9 超级大客" in boosted_p2["level_note"]

    # L10 的 P1 封顶保持 P1，禁止单凭客户身份冲入 P0。
    capped_p1 = apply_customer_tier("P1", "L10", "fresh")
    assert capped_p1["priority"] == "P1" and capped_p1["vip_boosted"] is False

    # 过期或未知等级不按 L0 降权，也不触发超级大客提档，必须给出待确认说明。
    stale = apply_customer_tier("P2", "L10", "stale")
    assert stale["priority"] == "P2" and stale["vip_boosted"] is False
    assert "待确认" in stale["level_note"]

    # L10 数字序号必须大于 L9 和 L6，杜绝字符串比较误判。
    assert customer_level_number("L10", "fresh") == 10 > customer_level_number("L9", "fresh") > customer_level_number("L6", "fresh")


def test_priority_sorting_places_real_outage_above_super_large_minor_request():
    l6_outage = dict(lifecycle_status="跟进中", priority="P0")
    l10_p1 = dict(lifecycle_status="跟进中", priority="P1")
    l6_p1 = dict(lifecycle_status="跟进中", priority="P1")
    l10_minor = dict(lifecycle_status="跟进中", priority="P2")
    resolved_p0 = dict(lifecycle_status="已解决", priority="P0")

    ordered = [
        ("resolved_p0", resolved_p0, {"level": "L10", "level_state": "fresh"}),
        ("l10_minor", l10_minor, {"level": "L10", "level_state": "fresh"}),
        ("l6_p1", l6_p1, {"level": "L6", "level_state": "fresh"}),
        ("l10_p1", l10_p1, {"level": "L10", "level_state": "fresh"}),
        ("l6_outage", l6_outage, {"level": "L6", "level_state": "fresh"}),
    ]
    ordered.sort(key=lambda item: event_priority_sort_key(item[1], item[2]))
    assert [item[0] for item in ordered] == ["l6_outage", "l10_p1", "l6_p1", "l10_minor", "resolved_p0"]


def test_late_priority_proposal_does_not_downgrade_higher_current_priority():
    evaluated = evaluate_priority_proposal(
        dict(impact="low", urgency="normal", reason="较早阶段曾是普通询价"),
        current_priority="P1",
        current_base_priority="P1",
        current_detail={"priority": "P1", "base_priority": "P1"},
        late=True,
    )
    assert evaluated["adopted"] is False
    assert evaluated["priority"] == "P1"


def test_lock_conflict_and_review_flag_length_cap():
    event = _sample_event()
    event.manual_controls["priority"] = dict(field="priority", locked=True, adopted_value="P1")
    assert is_locked(event, "priority")
    record_lock_conflict(event, "priority", "P3", "锁定优先级 P1 收到新建议 P3，待核对")

    long_items = [
        dict(status="open", summary=f"合成字段冲突待核对条目编号{index:02d}_超长属性名称测试验证")
        for index in range(10)
    ]
    flag = compose_review_flag(event.review_items + long_items, extra_flags=["归属边界待核对"])
    assert flag is not None
    assert len(flag) <= MAX_REVIEW_FLAG_CHARS < 128
    assert "待核对" in flag
    assert PENDING_TYPE == "待分类" and "其他" in EVENT_LABELS


@pytest.mark.parametrize(("impact", "expected"), [
    ("critical", ("P0", "P0", "P0", "P0")),
    ("high", ("P1", "P1", "P1", "P1")),
    ("medium", ("P1", "P2", "P2", "P2")),
    ("low", ("P2", "P2", "P3", "P3")),
    ("unknown", ("待评估", "待评估", "待评估", "待评估")),
])
def test_full_base_priority_matrix(impact, expected):
    assert tuple(calculate_base_priority(impact, urgency)
                 for urgency in ("immediate", "soon", "normal", "unknown")) == expected


@pytest.mark.parametrize(("impact", "signal", "level", "state", "expected"), [
    ("unknown", "escalated_emotion", "L3", "fresh", ("待评估", "待评估", "P1", True)),
    ("unknown", "strong_followup", "L3", "fresh", ("待评估", "待评估", "待评估", True)),
    ("unknown", None, "L9", "fresh", ("待评估", "待评估", "待评估", False)),
    ("low", None, "L9", "fresh", ("P2", "P1", "P1", False)),
    ("low", None, "L10", "expired", ("P3", "P3", "P3", False)),
    ("high", None, "L10", "fresh", ("P1", "P1", "P1", False)),
    ("low", "strong_followup", "L6", "fresh", ("P3", "P3", "P2", False)),
    ("medium", "escalated_emotion", "L10", "expired", ("P2", "P2", "P1", False)),
    ("medium", None, "L10", "fresh", ("P2", "P1", "P1", False)),
    ("critical", "escalated_emotion", "L10", "fresh", ("P0", "P0", "P0", False)),
])
def test_response_priority_keeps_objective_and_customer_steps(impact, signal, level, state, expected):
    proposal = dict(impact=impact, urgency="normal", reason="合成证据")
    if impact == "critical":
        proposal["impact_evidence"] = dict(evidence_ids=["synthetic:impact"], quote="关键生产链路全量中断且无绕行")
    if signal:
        proposal[signal] = dict(evidence_ids=["synthetic:quote"], quote="合成反馈")
    result = evaluate_priority_proposal(proposal, customer_profile=dict(level=level, level_state=state), created=True)
    detail = result["detail"]
    assert (result["base_priority"], detail["customer_priority"], result["priority"], detail["review_required"]) == expected
    assert detail["pre_timer_priority"] == result["priority"]


def test_legacy_critical_without_dedicated_impact_evidence_requires_review():
    proposal = dict(impact="critical", urgency="immediate", reason="待核实",
                    evidence_ids=["synthetic:report"], quote="请立即查看")
    result = evaluate_priority_proposal(proposal, customer_profile=dict(level="L10", level_state="fresh"), created=True)
    assert (result["base_priority"], result["priority"], result["detail"]["review_required"]) == ("待评估", "待评估", True)


def test_batch_combines_independently_supported_impact_and_immediate_deadline():
    impact = dict(evidence_ids=["synthetic:impact"], quote="一个节点降级，存在绕行")
    urgency = dict(evidence_ids=["synthetic:urgency"], quote="目前持续写入错误数据，须立即停止写入")
    merged = merge_priority_proposals(
        dict(impact="medium", urgency="unknown", impact_evidence=impact, reason="局部降级", **impact),
        dict(impact="unknown", urgency="immediate", urgency_evidence=urgency, reason="立即止损", **urgency),
    )
    assert evaluate_priority_proposal(merged, created=True)["base_priority"] == "P1"
    assert (merged["impact_evidence"], merged["urgency_evidence"]) == (impact, urgency)


def test_only_unique_confirmation_resolves_name_only_candidate():
    pending = dict(customer="合成客户", status="pending")
    first = dict(customer="合成客户", status="confirmed", uid="synthetic-a")
    second = dict(customer="合成客户", status="confirmed", uid="synthetic-b")
    assert merge_affected_customers([pending], [first]) == [first]
    assert merge_affected_customers([pending], [first, second]) == [pending, first, second]


def test_workbench_priority_order_uses_saved_level_response_and_absolute_deadline():
    def item(event_id, level="L6", *, priority="P1", due="2038-06-01T10:00:00+08:00", **signals):
        overdue = signals.pop("overdue", False)
        return dict(event_id=event_id, priority=priority, lifecycle_status="跟进中", priority_detail=dict(
            rule_version="item-priority-v1", customer_level=level, level_state="fresh",
            clock=dict(deadline=due, phase="handoff", overdue=overdue), **signals))

    fixtures = [item("late-due", due="2038-06-01T03:00:00Z"), item("early-due"),
                item("b-tie"), item("a-tie"), item("followup", strong_followup=True),
                item("emotion", escalated_emotion=True), item("higher-level", "L10"),
                item("overdue", "L1", overdue=True), item("review-first", priority="待评估", review_required=True),
                item("review-later", priority="待评估", due="2038-06-01T01:00:00Z")]
    assert [e["event_id"] for e in sorted(fixtures, key=event_priority_sort_key)] == [
        "higher-level", "emotion", "followup", "a-tie", "b-tie", "early-due", "late-due", "overdue",
        "review-first", "review-later"]
