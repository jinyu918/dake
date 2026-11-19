"""验证统一窗口增量管线：多标签分类、生命周期状态、优先级评估与人工锁协同。"""

from dataclasses import replace
from datetime import timedelta
import json

from dake.analysis.attribution import AttributionOutput, parse_attribution_output
from dake.analysis.contract import strict_schema
from dake.config import Settings
from dake.domain.manual_controls import set_field_lock
from dake.domain.models import Candidate, Message, Window, parse_time
from dake.services.processing import IncrementalService
from fakes import FakeProvider, FakeSource, MemoryStore


UID = "synthetic-unified-customer"


def msg(mid: str, at: str, content: str, *, uid: str = UID, sender_id: str = "acct-default", group: str = "grp-a") -> Message:
    """构造完全合成的消息对象。"""
    return Message(
        uid=uid,
        message_id=mid,
        sent_at=parse_time(at),
        content=content,
        conversation_id=group,
        sender_id=sender_id,
    )


def win(hour_start: str, minutes: int = 15) -> Window:
    """构造标准 15 分钟左闭右开窗口。"""
    start = parse_time(hour_start)
    return Window(start, start + timedelta(minutes=minutes))


def change_item(message: Message, text: str, *, kind: str = "progress", replaces: str | None = None) -> dict:
    """构造符合 Schema 4.0 的摘要变化条目。"""
    return {
        "kind": kind,
        "text": text,
        "replaces": replaces,
        "evidence": [{"message_id": message.key, "quote": message.content}],
    }


def write_sender_roles(data_dir, accounts: list[dict]) -> None:
    """写入测试用已确认发送者角色配置。"""
    cfg = data_dir / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "sender_roles.json").write_text(
        json.dumps({"accounts": accounts}, ensure_ascii=False),
        encoding="utf-8",
    )


class AttributionProvider(FakeProvider):
    """支持归属协议与历史上下文接收的测试 Provider。"""

    def configure_history_context(self, background):
        self.background = background


def build_pipeline(tmp_path, messages=(), mapping=None, *, uid: str = UID):
    """装配内存增量服务，启用归属模式以对齐真实 XHub 流程。"""
    settings = replace(Settings(), data_dir=tmp_path, uid=uid)
    source = FakeSource(list(messages))
    provider = AttributionProvider(mapping or {})
    store = MemoryStore()
    service = IncrementalService(store, source, provider, settings)
    return service, store, source, provider


def test_pending_to_multilabel_and_label_retraction_preserve_event_identity(tmp_path):
    """待分类澄清为双标签、后续再撤销其中一类时，应保持同一 event_id 并同步清理属性与待核对项。"""
    m1 = msg("m1", "2026-11-10 09:00:00", "合成客户询问 CDN 近期合作事宜，具体诉求待补充。")
    m2 = msg("m2", "2026-11-10 09:20:00", "合成客户明确拟新增 50Gbps CDN 带宽采购，并要求同步签署年度框架合同。")
    m3 = msg("m3", "2026-11-10 09:35:00", "合成客户澄清年度框架合同沿用既有协议无需重签，仅推进新增 50Gbps 采购评估。")

    c1 = Candidate(
        type="待分类",
        types=[],
        classification_status="待分类",
        topic_key="cdn:cooperation",
        summary="客户询问 CDN 合作事宜，待进一步明确具体诉求",
        evidence_ids=[m1.key],
        decision="new",
        decision_reason="首次出现该合作咨询",
        basis=[{"message_id": m1.key, "quote": m1.content}],
        changes=[change_item(m1, "客户询问 CDN 合作事宜，待进一步明确具体诉求")],
        status_proposal={
            "status": "已解决",
            "waiting_reason": None,
            "resolution": "已答复或已交付",
            "evidence_ids": [m1.key],
            "quote": "合作事宜",
            "note": "测试待分类误报已解决拦截",
        },
        priority_proposal={
            "impact": "unknown",
            "urgency": "unknown",
            "evidence_ids": [m1.key],
            "quote": "具体诉求待补充",
            "note": "影响与时效未知",
        },
    )
    service, store, source, provider = build_pipeline(tmp_path, [m1], {m1.message_id: c1})
    r1 = service.run_window(UID, win("2026-11-10 09:00:00"))
    assert len(r1.created) == 1
    event_id = r1.created[0]
    ev1 = store.events[event_id]
    assert ev1.type == "待分类"
    assert ev1.types == []
    assert ev1.classification_status == "待分类"
    # 待分类事项不得直接进入已解决，确定性规则应降回跟进中并挂出待核对提示。
    assert ev1.lifecycle_status == "跟进中"
    assert ev1.priority == "待评估"
    assert ev1.review_flag and "待分类" in ev1.review_flag

    # 第二窗口：客户澄清为 商机 + 商务需求 双标签，并给出专属属性与等待内部支撑状态。
    source.messages.append(m2)
    provider.mapping[m2.message_id] = Candidate(
        type="商机",
        types=["商机", "商务需求"],
        classification_status="已分类",
        type_evidence={
            "商机": [{"evidence_ids": [m2.key], "quote": "新增 50Gbps CDN 带宽采购"}],
            "商务需求": [{"evidence_ids": [m2.key], "quote": "同步签署年度框架合同"}],
        },
        typed_attributes={
            "商机": {
                "计划规模": {"value": "50Gbps", "evidence_ids": [m2.key], "quote": "新增 50Gbps CDN 带宽采购"},
            },
            "商务需求": {
                "合同与商务条件": {"value": "年度框架合同", "evidence_ids": [m2.key], "quote": "同步签署年度框架合同"},
            },
        },
        topic_key="cdn:cooperation",
        summary="客户明确拟新增 50Gbps CDN 带宽采购并同步签署年度框架合同",
        evidence_ids=[m2.key],
        decision="update",
        referenced_event_id=event_id,
        decision_reason="延续同一 CDN 合作咨询并明确采购与合同范围",
        basis=[
            {"message_id": m1.key, "quote": m1.content},
            {"message_id": m2.key, "quote": m2.content},
        ],
        changes=[
            change_item(
                m2,
                "明确拟新增 50Gbps CDN 带宽采购并同步签署年度框架合同",
                replaces="待进一步明确具体诉求",
            )
        ],
        status_proposal={
            "status": "等待中",
            "waiting_reason": "等待内部支撑",
            "resolution": None,
            "evidence_ids": [m2.key],
            "quote": "要求同步签署年度框架合同",
            "note": "等待商务与销售内部评估报价和合同",
        },
        priority_proposal={
            "impact": "medium",
            "urgency": "soon",
            "evidence_ids": [m2.key],
            "quote": "新增 50Gbps CDN 带宽采购",
            "note": "明确采购规模并要求近期推进",
        },
    )
    r2 = service.run_window(UID, win("2026-11-10 09:15:00"))
    assert r2.updated == [event_id]
    ev2 = store.events[event_id]
    assert ev2.type == "商机"
    assert ev2.types == ["商机", "商务需求"]
    assert ev2.classification_status == "已分类"
    assert ev2.attributes["计划规模"] == "50Gbps"
    assert ev2.attributes["合同与商务条件"] == "年度框架合同"
    assert ev2.lifecycle_status == "等待中"
    assert ev2.waiting_reason == "等待内部支撑"
    assert ev2.priority == "P2"
    assert ev2.review_flag is None

    # 第三窗口：客户明确撤回商务需求标签，仅保留商机。
    source.messages.append(m3)
    provider.mapping[m3.message_id] = Candidate(
        type="商机",
        types=["商机"],
        classification_status="已分类",
        type_evidence={
            "商机": [{"evidence_ids": [m3.key], "quote": "推进新增 50Gbps 采购评估"}],
        },
        removed_types={
            "商务需求": [{"evidence_ids": [m3.key], "quote": "年度框架合同沿用既有协议无需重签"}],
        },
        topic_key="cdn:cooperation",
        summary="客户推进新增 50Gbps 采购评估，合同沿用既有协议无需重签",
        evidence_ids=[m3.key],
        decision="update",
        referenced_event_id=event_id,
        decision_reason="客户就同一事项撤回重签合同诉求",
        basis=[
            {"message_id": m2.key, "quote": m2.content},
            {"message_id": m3.key, "quote": m3.content},
        ],
        changes=[
            change_item(
                m3,
                "推进新增 50Gbps 采购评估，合同沿用既有协议无需重签",
                kind="correction",
                replaces="同步签署年度框架合同",
            )
        ],
    )
    r3 = service.run_window(UID, win("2026-11-10 09:30:00"))
    assert r3.updated == [event_id]
    ev3 = store.events[event_id]
    assert ev3.types == ["商机"]
    assert "商务需求" not in ev3.typed_attributes
    assert "合同与商务条件" not in ev3.attributes
    assert ev3.attributes["计划规模"] == "50Gbps"
    assert "商务需求" in ev3.removed_types


def test_support_fix_reopens_same_event_then_customer_recovery_allows_recurrence(tmp_path):
    """支撑声明已修复不写 recovery，后续反馈未好应重开同一事件；客户确认恢复后再次发生才新建关联事件。"""
    write_sender_roles(
        tmp_path,
        [
            {"uid": UID, "source": "official_chat", "sender_id": "cust-zhang", "role": "customer"},
            {"uid": UID, "source": "official_chat", "sender_id": "sup-li", "role": "support"},
        ],
    )
    m1 = msg("s1", "2026-11-10 10:00:00", "合成工单 INC-901 直播拉流出现全量 502 报错，线上业务中断且无绕行。", sender_id="cust-zhang")
    m2 = msg("s2", "2026-11-10 10:20:00", "合成工单 INC-901 支撑已完成边缘节点回滚，请客户验证是否恢复。", sender_id="sup-li")
    m3 = msg("s3", "2026-11-10 10:35:00", "合成工单 INC-901 客户反馈华东节点仍然出现 502 报错，未恢复。", sender_id="cust-zhang")
    m4 = msg("s4", "2026-11-10 10:50:00", "合成工单 INC-901 客户确认拉流已完全恢复正常。", sender_id="cust-zhang")
    m5 = msg("s5", "2026-11-10 11:20:00", "合成工单 INC-901 恢复半小时后再次出现大面积 502 报错。", sender_id="cust-zhang")

    service, store, source, provider = build_pipeline(tmp_path)
    store.profiles[UID] = {"level": "L10", "level_state": "fresh"}

    # W1: 线上业务中断 -> P0 + 跟进中
    source.messages.append(m1)
    provider.mapping[m1.message_id] = Candidate(
        type="排障",
        types=["排障"],
        classification_status="已分类",
        type_evidence={"排障": [{"evidence_ids": [m1.key], "quote": "全量 502 报错，线上业务中断"}]},
        topic_key="live:inc-901",
        summary="INC-901 直播拉流出现全量 502 报错导致线上业务中断",
        evidence_ids=[m1.key],
        decision="new",
        decision_reason="首次报告 INC-901 故障",
        basis=[{"message_id": m1.key, "quote": m1.content}],
        changes=[change_item(m1, "INC-901 直播拉流出现全量 502 报错导致线上业务中断")],
        status_proposal={
            "status": "跟进中",
            "waiting_reason": None,
            "resolution": None,
            "evidence_ids": [m1.key],
            "quote": "全量 502 报错",
            "note": "故障刚报出正在排查",
        },
        priority_proposal={
            "impact": "critical",
            "urgency": "immediate",
            "evidence_ids": [m1.key],
            "quote": "线上业务中断",
            "note": "线上核心拉流中断",
            "impact_evidence": {"evidence_ids": [m1.key], "quote": "全量 502 报错，线上业务中断且无绕行"},
        },
    )
    event_id = service.run_window(UID, win("2026-11-10 10:00:00")).created[0]
    assert store.events[event_id].priority == "P0"
    assert store.events[event_id].lifecycle_status == "跟进中"

    # W2: 支撑声明已修复 -> 已解决(支撑声明已修复)，但 recovery 仍为 None
    source.messages.append(m2)
    provider.mapping[m2.message_id] = Candidate(
        type="排障",
        types=["排障"],
        classification_status="已分类",
        type_evidence={"排障": [{"evidence_ids": [m2.key], "quote": "已完成边缘节点回滚"}]},
        topic_key="live:inc-901",
        summary="INC-901 直播拉流全量 502，支撑已完成边缘节点回滚待验证",
        evidence_ids=[m2.key],
        decision="update",
        referenced_event_id=event_id,
        decision_reason="支撑反馈同一工单处置结果",
        basis=[
            {"message_id": m1.key, "quote": m1.content},
            {"message_id": m2.key, "quote": m2.content},
        ],
        changes=[
            change_item(
                m2,
                "支撑已完成边缘节点回滚待验证",
            )
        ],
        status_proposal={
            "status": "已解决",
            "waiting_reason": None,
            "resolution": "支撑声明已修复",
            "evidence_ids": [m2.key],
            "quote": "已完成边缘节点回滚",
            "note": "支撑声明已回滚修复",
        },
    )
    service.run_window(UID, win("2026-11-10 10:15:00"))
    ev_w2 = store.events[event_id]
    assert ev_w2.lifecycle_status == "已解决"
    assert ev_w2.resolution == "支撑声明已修复"
    assert ev_w2.recovery is None

    # W3: 客户反馈仍未恢复 -> 继续更新同一 event_id 并重开回跟进中
    source.messages.append(m3)
    provider.mapping[m3.message_id] = Candidate(
        type="排障",
        types=["排障"],
        classification_status="已分类",
        type_evidence={"排障": [{"evidence_ids": [m3.key], "quote": "华东节点仍然出现 502 报错"}]},
        topic_key="live:inc-901",
        summary="INC-901 回滚后华东节点仍出现 502 报错，继续排查",
        evidence_ids=[m3.key],
        decision="update",
        referenced_event_id=event_id,
        decision_reason="支撑声明修复后客户验证未恢复，属同一故障延续",
        basis=[
            {"message_id": m2.key, "quote": m2.content},
            {"message_id": m3.key, "quote": m3.content},
        ],
        changes=[
            change_item(
                m3,
                "回滚后华东节点仍出现 502 报错，继续排查",
            )
        ],
        status_proposal={
            "status": "跟进中",
            "waiting_reason": None,
            "resolution": None,
            "evidence_ids": [m3.key],
            "quote": "仍然出现 502 报错，未恢复",
            "note": "客户验证未好，重开跟进",
        },
    )
    r3 = service.run_window(UID, win("2026-11-10 10:30:00"))
    assert r3.updated == [event_id] and not r3.created
    ev_w3 = store.events[event_id]
    assert ev_w3.lifecycle_status == "跟进中"
    assert ev_w3.resolution is None

    # W4: 已确认客户账号反馈完全恢复 -> 写入 recovery 并进入 已解决(客户确认恢复)
    source.messages.append(m4)
    provider.mapping[m4.message_id] = Candidate(
        type="排障",
        types=["排障"],
        classification_status="已分类",
        type_evidence={"排障": [{"evidence_ids": [m4.key], "quote": "已完全恢复正常"}]},
        topic_key="live:inc-901",
        summary="INC-901 直播拉流已完全恢复正常",
        evidence_ids=[m4.key],
        decision="update",
        referenced_event_id=event_id,
        decision_reason="客户明确确认 INC-901 已恢复",
        basis=[
            {"message_id": m3.key, "quote": m3.content},
            {"message_id": m4.key, "quote": m4.content},
        ],
        continuity={
            "kind": "confirm_recovery",
            "previous_event_id": None,
            "evidence": [{"message_id": m4.key, "quote": "客户确认拉流已完全恢复正常"}],
        },
        changes=[
            change_item(
                m4,
                "客户确认拉流已完全恢复正常",
            )
        ],
        status_proposal={
            "status": "已解决",
            "waiting_reason": None,
            "resolution": "客户确认恢复",
            "evidence_ids": [m4.key],
            "quote": "客户确认拉流已完全恢复正常",
            "note": "客户已确认恢复",
        },
    )
    service.run_window(UID, win("2026-11-10 10:45:00"))
    ev_w4 = store.events[event_id]
    assert ev_w4.recovery is not None
    assert ev_w4.lifecycle_status == "已解决"
    assert ev_w4.resolution == "客户确认恢复"

    # W5: 恢复后再次出现同类故障 -> 新建事件并挂接 recurrence_of
    source.messages.append(m5)
    provider.mapping[m5.message_id] = Candidate(
        type="排障",
        types=["排障"],
        classification_status="已分类",
        type_evidence={"排障": [{"evidence_ids": [m5.key], "quote": "再次出现大面积 502 报错"}]},
        topic_key="live:inc-901",
        summary="INC-901 恢复后再次出现大面积 502 报错",
        evidence_ids=[m5.key],
        decision="new",
        decision_reason="前次客户确认恢复后再次发生",
        basis=[
            {"message_id": m4.key, "quote": m4.content},
            {"message_id": m5.key, "quote": m5.content},
        ],
        continuity={
            "kind": "recurrence",
            "previous_event_id": event_id,
            "evidence": [{"message_id": m5.key, "quote": "恢复半小时后再次出现大面积 502 报错"}],
        },
        changes=[change_item(m5, "INC-901 恢复后再次出现大面积 502 报错")],
        status_proposal={
            "status": "跟进中",
            "waiting_reason": None,
            "resolution": None,
            "evidence_ids": [m5.key],
            "quote": "再次出现大面积 502 报错",
            "note": "复发后新事件跟进中",
        },
    )
    r5 = service.run_window(UID, win("2026-11-10 11:15:00"))
    assert len(r5.created) == 1
    new_id = r5.created[0]
    assert new_id != event_id
    assert store.events[new_id].recurrence_of["event_id"] == event_id
    assert store.events[new_id].lifecycle_status == "跟进中"


def test_vip_priority_boost_cap_supporting_evidence_retention_and_lock_protection(tmp_path):
    """验证 L9/L10 提档封顶 P1、仅状态/优先级引用的支撑消息保留，以及增量更新尊重人工锁。"""
    m1 = msg("p1", "2026-11-10 13:00:00", "合成需求 REQ-77 客户咨询导出报表加字段排期。")
    m2 = msg("p2", "2026-11-10 13:18:00", "合成需求 REQ-77 产品经理回复已纳入下周需求评审。")
    m3 = msg("p3", "2026-11-10 13:22:00", "合成需求 REQ-77 客户补充本周五前必须给出结论，否则影响月度结算。")

    service, store, source, provider = build_pipeline(tmp_path, [m1])
    store.profiles[UID] = {"level": "L9", "level_state": "fresh"}

    provider.mapping[m1.message_id] = Candidate(
        type="功能需求",
        types=["功能需求"],
        classification_status="已分类",
        type_evidence={"功能需求": [{"evidence_ids": [m1.key], "quote": "导出报表加字段"}]},
        topic_key="report:req-77",
        summary="客户咨询导出报表加字段排期",
        evidence_ids=[m1.key],
        decision="new",
        decision_reason="首次提出报表字段需求",
        basis=[{"message_id": m1.key, "quote": m1.content}],
        changes=[change_item(m1, "客户咨询导出报表加字段排期")],
        priority_proposal={
            "impact": "low",
            "urgency": "normal",
            "evidence_ids": [m1.key],
            "quote": "咨询导出报表加字段排期",
            "note": "常规咨询",
        },
    )
    event_id = service.run_window(UID, win("2026-11-10 13:00:00")).created[0]
    ev1 = store.events[event_id]
    assert (ev1.priority_detail["objective_priority"], ev1.base_priority, ev1.priority) == ("P3", "P2", "P1")
    assert ev1.priority_detail["vip_boosted"] is True

    # 人工锁定 status 为 等待中(等待内部支撑)
    ev1.lifecycle_status = "等待中"
    ev1.waiting_reason = "等待内部支撑"
    ev1.status = "等待中"
    set_field_lock(ev1, "status", True, reason="人工锁定等待评审")

    # 第二窗口同时收到 m2（进 changes）与 m3（仅进 status_proposal 与 priority_proposal）
    source.messages.extend([m2, m3])
    provider.mapping[m2.message_id] = []
    provider.mapping[m3.message_id] = Candidate(
        type="功能需求",
        types=["功能需求"],
        classification_status="已分类",
        type_evidence={"功能需求": [{"evidence_ids": [m2.key], "quote": "纳入下周需求评审"}]},
        topic_key="report:req-77",
        summary="导出报表加字段已纳入下周需求评审",
        evidence_ids=[m2.key, m3.key],
        decision="update",
        referenced_event_id=event_id,
        decision_reason="同一报表字段需求的排期进展与时效补充",
        basis=[
            {"message_id": m1.key, "quote": m1.content},
            {"message_id": m2.key, "quote": m2.content},
        ],
        changes=[
            change_item(
                m2,
                "产品经理回复已纳入下周需求评审",
            )
        ],
        status_proposal={
            "status": "跟进中",
            "waiting_reason": None,
            "resolution": None,
            "evidence_ids": [m3.key],
            "quote": "本周五前必须给出结论",
            "note": "客户催促结论",
        },
        priority_proposal={
            "impact": "high",
            "urgency": "soon",
            "evidence_ids": [m3.key],
            "quote": "否则影响月度结算",
            "note": "高影响且本周五截止",
        },
    )
    service.run_window(UID, win("2026-11-10 13:15:00"))
    ev2 = store.events[event_id]
    # high × soon => base P1；L9 封顶仍在 P1，绝不因大客户等级跳入 P0
    assert ev2.base_priority == "P1"
    assert ev2.priority == "P1"
    assert ev2.priority_detail["vip_boosted"] is False
    # m3 虽未出现在 changes 中，但支撑了状态和优先级提案，必须保留在 event.evidence 中
    assert {item["message_id"] for item in ev2.evidence} == {"p1", "p2", "p3"}
    # status 被人工锁定，自动结果不得覆盖为跟进中，而应记录锁定冲突待核对
    assert ev2.lifecycle_status == "等待中"
    assert ev2.waiting_reason == "等待内部支撑"
    assert ev2.review_flag and "锁定状态" in ev2.review_flag


def test_unified_contract_schema_and_json_parsing():
    """验证 Schema 4.0 严格模式及完整 JSON 解析。"""
    schema = strict_schema(AttributionOutput.model_json_schema())
    defs = schema.get("$defs", {})
    candidate_props = defs["AttributedCandidate"]["properties"]
    assert {
        "type_labels",
        "removed_types",
        "typed_attributes",
        "other_reason",
        "status_proposal",
        "priority_proposal",
        "decision",
        "changes",
    } <= set(candidate_props)

    m1 = msg("c1", "2026-11-10 15:00:00", "合成消息：客户申请为华北集群扩容 20 台 GPU 节点，要求明早交付。")
    raw_json = json.dumps(
        {
            "schema_version": "4.0",
            "candidates": [
                {
                    "type": "资源需求",
                    "classification_status": "已分类",
                    "type_labels": [
                        {"type": "资源需求", "evidence_ids": [m1.key], "quote": "扩容 20 台 GPU 节点"}
                    ],
                    "removed_types": [],
                    "typed_attributes": [
                        {
                            "type_label": "资源需求",
                            "name": "目标总量与增减量",
                            "value": "20 台 GPU 节点",
                            "evidence_ids": [m1.key],
                            "quote": "扩容 20 台 GPU 节点",
                        }
                    ],
                    "other_reason": None,
                    "topic_key": "gpu:north-expand",
                    "summary": "客户申请华北集群扩容 20 台 GPU 节点并要求明早交付",
                    "evidence_ids": [m1.key],
                    "status": None,
                    "severity": None,
                    "status_proposal": {
                        "status": "跟进中",
                        "waiting_reason": None,
                        "resolution": None,
                        "completed_labels": None,
                        "reason": "刚提出扩容申请",
                        "evidence_ids": [m1.key],
                        "quote": "申请为华北集群扩容 20 台 GPU 节点",
                    },
                    "priority_proposal": {
                        "impact": "medium",
                        "urgency": "soon",
                        "reason": "明确要求次日早上交付",
                        "evidence_ids": [m1.key],
                        "quote": "要求明早交付",
                    },
                    "assignees": [],
                    "attributes": [],
                    "extensions": [],
                    "decision": "new",
                    "referenced_event_id": None,
                    "possible_event_ids": [],
                    "decision_reason": "首次出现该扩容申请",
                    "basis": [{"message_id": m1.key, "quote": m1.content}],
                    "continuity": None,
                    "changes": [
                        {
                            "kind": "progress",
                            "text": "客户申请华北集群扩容 20 台 GPU 节点并要求明早交付",
                            "replaces": None,
                            "evidence": [{"message_id": m1.key, "quote": m1.content}],
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )
    parsed = parse_attribution_output(raw_json, [m1], [], [], require_type_labels=True)
    assert len(parsed) == 1
    cand = parsed[0]
    assert cand.types == ["资源需求"]
    assert cand.typed_attributes["资源需求"]["目标总量与增减量"]["value"] == "20 台 GPU 节点"
    assert cand.status_proposal["status"] == "跟进中"
    assert cand.priority_proposal == {
        "impact": "medium",
        "urgency": "soon",
        "reason": "明确要求次日早上交付",
        "evidence_ids": [m1.key],
        "quote": "要求明早交付",
    }


def test_other_category_cross_window_update_and_basis_completion():
    """验证『其他』类别分析性排除理由、跨窗口上下文标签依据及漏填新消息 basis 时的自动补足。"""
    from dake.domain.models import Event

    m_ctx = msg("o1", "2026-11-10 10:20:00", "合成消息：请为新入职运维团队安排一场存储控制台操作培训。")
    m_new = msg("o2", "2026-11-10 10:35:00", "合成消息：就是上面那场培训，讲师正在排周四下午的时间。")
    old_event = Event(
        event_id="evt-other-1",
        uid=UID,
        stream="syn-stream",
        type="其他",
        types=["其他"],
        classification_status="已分类",
        other_reason={
            "value": "专项操作培训服务责任，不涉及采购、流失、故障、资源、新功能或商务办理",
            "evidence_ids": [m_ctx.key],
            "quote": "安排一场存储控制台操作培训",
        },
        topic_key="training:storage-console",
        start_time=m_ctx.sent_at,
        end_time=m_ctx.sent_at,
        status="等待中",
        severity="P3",
        summary="客户申请存储控制台操作培训",
        created_on=m_ctx.sent_at,
        evidence=[{"source": m_ctx.source, "message_id": m_ctx.message_id, "digest": m_ctx.digest}],
    )
    raw_update = json.dumps(
        {
            "schema_version": "4.0",
            "candidates": [
                {
                    "type": "其他",
                    "classification_status": "已分类",
                    "type_labels": [
                        {
                            "type": "其他",
                            "evidence_ids": [m_ctx.key, m_ctx.key],
                            "quote": "安排一场存储控制台操作培训",
                        }
                    ],
                    "removed_types": [],
                    "typed_attributes": [],
                    "other_reason": {
                        "value": "属于专项使用培训服务责任，排除商机、流失、排障、资源、功能及商务六类",
                        "evidence_ids": [m_ctx.key, m_ctx.key],
                        "quote": "安排一场存储控制台操作培训",
                    },
                    "topic_key": "training:storage-console",
                    "summary": "存储控制台培训讲师正在协调周四下午时间",
                    "evidence_ids": [m_new.key],
                    "status": None,
                    "severity": None,
                    "status_proposal": None,
                    "priority_proposal": None,
                    "assignees": [],
                    "attributes": [],
                    "extensions": [],
                    "decision": "update",
                    "referenced_event_id": "evt-other-1",
                    "possible_event_ids": [],
                    "decision_reason": "口语指代延续同一培训安排事项",
                    "basis": [{"message_id": m_ctx.key, "quote": "安排一场存储控制台操作培训"}],
                    "continuity": None,
                    "changes": [
                        {
                            "kind": "progress",
                            "text": "讲师正在排周四下午的培训时间",
                            "replaces": None,
                            "evidence": [{"message_id": m_new.key, "quote": "讲师正在排周四下午的时间"}],
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )
    normalizations = []
    parsed = parse_attribution_output(
        raw_update,
        [m_new],
        [m_ctx],
        [old_event],
        normalizations=normalizations,
        require_type_labels=True,
    )
    assert len(parsed) == 1
    cand = parsed[0]
    assert cand.decision == "update"
    assert cand.other_reason["evidence_ids"] == [m_ctx.key]
    assert any(ref["message_id"] == m_new.key for ref in cand.basis)
    assert any(item["reason"] == "verified_change_quote_completed_basis" for item in normalizations)


def test_burst_neighbor_quote_alignment_duplicate_attrs_and_context_stripping():
    """验证连发消息邻近错号唯一对齐、同名专属属性合并、混入旧上下文证据剥离与无目标 correction 降级。"""
    from dake.domain.models import Event

    m_ctx = msg("b0", "2026-11-10 09:50:00", "合成消息：华东直播域名 stream-east.example 偶发 502 报错。")
    m_new_1 = msg("b1", "2026-11-10 10:01:00", "合成消息：补充复现时间段为上午 09:40 至 09:48。")
    m_new_2 = msg("b2", "2026-11-10 10:01:05", "合成消息：边缘节点回源探测发现上游网关连接池耗尽，已扩容恢复观察。")
    old_event = Event(
        event_id="evt-burst-1",
        uid=UID,
        stream="syn-stream",
        type="排障",
        types=["排障"],
        classification_status="已分类",
        topic_key="live:stream-east-502",
        start_time=m_ctx.sent_at,
        end_time=m_ctx.sent_at,
        status="跟进中",
        severity="P2",
        summary="华东直播域名偶发 502 报错",
        created_on=m_ctx.sent_at,
        evidence=[{"source": m_ctx.source, "message_id": m_ctx.message_id, "digest": m_ctx.digest}],
    )

    raw_json = json.dumps(
        {
            "schema_version": "4.0",
            "candidates": [
                {
                    "type": "排障",
                    "classification_status": "已分类",
                    "type_labels": [
                        {"type": "排障", "evidence_ids": [m_ctx.key], "quote": "偶发 502 报错"}
                    ],
                    "removed_types": [],
                    "typed_attributes": [
                        # 模拟混入仅引用旧上下文的未变属性（应自动剥离）
                        {
                            "type_label": "排障",
                            "name": "异常对象与环境",
                            "value": "stream-east.example",
                            "evidence_ids": [m_ctx.key],
                            "quote": "华东直播域名 stream-east.example",
                        },
                        # 模拟同一类型下重复输出两条同名属性（应合并保留最新一条）
                        {
                            "type_label": "排障",
                            "name": "排查发现与根因",
                            "value": "上午 09:40 至 09:48",
                            "evidence_ids": [m_new_1.key],
                            "quote": "复现时间段为上午 09:40 至 09:48",
                        },
                        {
                            "type_label": "排障",
                            "name": "排查发现与根因",
                            "value": "上游网关连接池耗尽",
                            "evidence_ids": [m_new_1.key],  # 故意错写成紧邻的 b1，引文实际在 b2 中唯一出现
                            "quote": "上游网关连接池耗尽",
                        },
                    ],
                    "other_reason": None,
                    "topic_key": "live:stream-east-502",
                    "summary": "定位上游网关连接池耗尽并已扩容观察",
                    # 故意混入旧上下文 m_ctx.key 且漏写 m_new_2.key
                    "evidence_ids": [m_ctx.key, m_new_1.key],
                    "status": None,
                    "severity": None,
                    "status_proposal": None,
                    "priority_proposal": None,
                    "assignees": [],
                    "attributes": [],
                    "extensions": [],
                    "decision": "update",
                    "referenced_event_id": "evt-burst-1",
                    "possible_event_ids": [],
                    "decision_reason": "延续同一华东直播 502 故障排查",
                    "basis": [
                        {"message_id": m_ctx.key, "quote": "华东直播域名 stream-east.example 偶发 502 报错"},
                        {"message_id": m_new_1.key, "quote": "补充复现时间段为上午 09:40 至 09:48"},
                    ],
                    "continuity": None,
                    "changes": [
                        {
                            "kind": "correction",  # 故意漏填 replaces=None，应降级为 progress
                            "text": "定位上游网关连接池耗尽并已扩容恢复观察",
                            "replaces": None,
                            # 故意把 b2 的引文错标在邻近消息 b1 上
                            "evidence": [{"message_id": m_new_1.key, "quote": "边缘节点回源探测发现上游网关连接池耗尽，已扩容恢复观察"}],
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )

    normalizations = []
    parsed = parse_attribution_output(
        raw_json,
        [m_new_1, m_new_2],
        [m_ctx],
        [old_event],
        normalizations=normalizations,
        require_type_labels=True,
    )
    assert len(parsed) == 1
    cand = parsed[0]
    # 顶层 evidence_ids 已自动补入 b2 并剥离混入的旧上下文 b0
    assert cand.evidence_ids == [m_new_1.key, m_new_2.key]
    # changes[0] 已降级为 progress 且证据 message_id 已唯一对齐至 b2
    assert cand.changes[0]["kind"] == "progress"
    assert cand.changes[0]["evidence"][0]["message_id"] == m_new_2.key
    # 仅引用旧上下文的『异常对象与环境』已剥离；两条『排查发现与根因』已合并保留最新值并聚合证据
    assert set(cand.typed_attributes["排障"]) == {"排查发现与根因"}
    assert cand.typed_attributes["排障"]["排查发现与根因"]["value"] == "上游网关连接池耗尽"
    assert cand.typed_attributes["排障"]["排查发现与根因"]["evidence_ids"] == [m_new_1.key, m_new_2.key]
    reasons = {item["reason"] for item in normalizations}
    assert {
        "correction_without_replaces_to_progress",
        "unique_quote_message_aligned",
        "context_only_unchanged_fields_stripped",
        "mixed_context_ids_stripped",
    } <= reasons
