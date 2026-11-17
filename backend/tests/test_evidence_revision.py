from copy import deepcopy
from dataclasses import replace

import pytest

from final_replay import Replay, attribute, common, fact, record
from offline_http import offline_only
from test_classification_replay import msg, ref
from test_classification_final import boundary, case, recover
from test_field_decisions import change


def seed(case, text, labels, specs):
    anchor = msg("identity", 1, "合成申请R已受理，请继续办理。", hour=8)
    case.source.messages.append(anchor)
    case.versions[(anchor.source, anchor.message_id, anchor.digest)] = anchor
    first = msg("revisable", 1, text)
    item = record(first, labels, [fact(first, *spec) for spec in specs])
    item["evidence_ids"].append(anchor.key)
    item["changes"].append(dict(kind="progress", text=anchor.content, evidence=[ref(anchor)], replaces=None))
    case.send(first, item)
    return anchor, first, case.event()


def independent_proof(case, first, labels, specs):
    proof = msg("independent", 2, first.content, hour=9)
    item = record(proof, labels, [fact(proof, *spec) for spec in specs], old=case.event(), anchor=first,
        changes=[dict(kind="confirmation", text=proof.content, evidence=[ref(proof)], replaces=None)])
    case.send(proof, item)
    return proof


def pending(event, name=None):
    return [item for item in event.field_issues if item["status"] == "open"
            and (name is None or item["target"]["name"] == name)]


@pytest.mark.parametrize("independent", [False, True])
def test_R1_purchase_label_revision(case, independent):
    labels = ["商机", "商务需求", "资源需求"]
    specs = [("商机", "采购意向与评估事实", "付费增购A配额", "采购意向")]
    anchor, first, event = seed(case, "付费增购A配额并报价。", labels, specs)
    if independent:
        independent_proof(case, first, labels, specs)
    revised = replace(first, content="刚才采购报价误述，本次仅申请原权益内提额。")
    item = record(revised, ["资源需求"], old=case.event(), anchor=anchor,
        removed=[] if independent else ["商机", "商务需求"], changes=[
            dict(kind="correction", text="申请原权益内提额", replaces="付费增购A配额", evidence=[ref(revised)]),
            dict(kind="correction", text="，采购报价误述", replaces="并报价", evidence=[ref(revised)])])
    case.send(revised, item, day=3)
    current = case.event()
    assert current.event_id == event.event_id
    assert set(current.types) == set(labels if independent else ["资源需求"])
    if independent:
        valid = [proof for evidence in current.type_evidence["商机"] for proof in evidence["evidence_refs"]
                 if proof.get("evidence_status") != "superseded"]
        assert {proof["message_id"] for proof in valid} == {"independent"}
    else:
        assert "商机" not in current.typed_attributes and "采购意向与评估事实" not in current.attributes
    assert current.updated_by[0]["snapshot"]["types"] == labels


@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize("explicit_error", [False, True])
def test_R2_fault_basis_disappears_or_is_false(case, independent, explicit_error):
    specs = [("排障", "症状与对照表现", "A下载失败", "客户报告")]
    anchor, first, event = seed(case, "A下载失败。", ["排障"], specs)
    if independent:
        independent_proof(case, first, ["排障"], specs)
    revised = replace(first, content="刚才误报，A并没有故障，原误报作废。" if explicit_error else "[文件]", markers=() if explicit_error else ("[文件]",))
    item = []
    if explicit_error and not independent:
        item = record(revised, [], [change(revised, common("status"), "原误报作废", "更正结果")], old=case.event(), anchor=anchor,
            removed=["排障"], changes=[dict(kind="correction", text="A并没有故障，原误报作废", replaces="A下载失败", evidence=[ref(revised)])])
    before = deepcopy(case.event().updated_by)
    case.send(revised, item, day=3)
    current = case.event()
    assert current.updated_by[:len(before)] == before and current.event_id == event.event_id
    if explicit_error and not independent:
        assert current.types == [] and "排障" not in current.typed_attributes and current.status == "原误报作废"
    else:
        assert current.types == ["排障"]
        assert bool(pending(current, "排障")) is (not independent)
        assert ("概述依据已修订" in (current.review_flag or "")) is (not independent)
    state = current.field_states.get("label::排障")
    if state:
        assert any(proof.get("evidence_status") == "superseded" for value in state["facts"] for proof in value["evidence"])
    if not independent and not explicit_error:
        case.send(msg("unrelated", 4, "无关的普通沟通。"), [])
        assert "概述依据已修订" in case.event().review_flag


@pytest.mark.parametrize("independent", [False, True])
def test_R3_derived_increment_loses_its_base(case, independent):
    specs = [("资源需求", "现有可用量", "1000", "基准"),
        ("资源需求", "目标总量与增减量", "2000", "目标总量"),
        ("资源需求", "目标总量与增减量", "1000", "计算增量")]
    anchor, first, _ = seed(case, "A现有1000次/分钟，申请提到2000次/分钟。", ["资源需求"], specs)
    if independent:
        independent_proof(case, first, ["资源需求"], specs)
    old = case.event()
    base = case.adopted(old, attribute("资源需求", "现有可用量"))[0]
    revised = replace(first, content="A基准更正为1500次/分钟，目标仍2000次/分钟。")
    update = fact(revised, "资源需求", "现有可用量", "1500", "基准", intent="correction", replaces=[base["id"]])
    case.send(revised, record(revised, ["资源需求"], [update], old=old, anchor=anchor,
        changes=[dict(kind="correction", text="1500", replaces="1000", evidence=[ref(revised)])]), day=3)
    current = case.event()
    state = current.field_states["attribute:资源需求:目标总量与增减量"]
    stale = [value for value in state["facts"] if value["stage"] == "计算增量" and value["evidence_status"] == "superseded"]
    assert stale and all(proof["digest"] == first.digest for value in stale for proof in value["evidence"])
    assert bool(pending(current, "目标总量与增减量")) is (not independent)
    assert current.updated_by[0]["snapshot"]["attributes"]["现有可用量"] == "1000"


@pytest.mark.parametrize("independent", [False, True])
def test_R4_migration_percentage_reversed(case, independent):
    specs = [("流失", "减少或转移规模", "已迁走30%", "实际迁移")]
    anchor, first, _ = seed(case, "A业务已迁走30%，余下留在这里。", ["流失"], specs)
    if independent:
        independent_proof(case, first, ["流失"], specs)
    old = case.event()
    previous = case.adopted(old, attribute("流失", "减少或转移规模"))[0]["id"]
    revised = replace(first, content="说反了，A业务还剩30%，已迁走70%。")
    update = fact(revised, "流失", "减少或转移规模", "已迁走70%", "实际迁移", intent="correction", replaces=[previous])
    case.send(revised, record(revised, ["流失"], [update], old=old, anchor=anchor,
        changes=[dict(kind="correction", text="已迁走70%", replaces="已迁走30%", evidence=[ref(revised)])]), day=3)
    current = case.event()
    assert current.attributes["减少或转移规模"] == ("已迁走30%" if independent else "已迁走70%")
    assert bool(pending(current, "减少或转移规模")) is independent
    assert current.updated_by[0]["snapshot"]["attributes"]["减少或转移规模"] == "已迁走30%"


@pytest.mark.parametrize("independent", [False, True])
def test_R5_recovery_and_completion_revision(case, independent):
    first, fixed, event = recover(case, completed=True)
    assert event.closed_at is not None and event.status == "虚构已恢复"
    if independent:
        proof = msg("independent-recovery", 3, "A上传虚构已恢复")
        item = record(proof, ["排障"], [fact(proof, "排障", "恢复事实", "已恢复", "客户验证")], old=event, anchor=first,
            changes=[dict(kind="confirmation", text=proof.content, replaces=None, evidence=[ref(proof)])])
        item["continuity"] = boundary("confirm_recovery", proof)
        item["field_updates"].append(change(proof, common("status"), "虚构已恢复", "跟进状态"))
        case.send(proof, item)
    old = case.event()
    revised = replace(fixed, content="上次测错环境，A上传一直没有恢复，正在排查。")
    previous = case.adopted(old, attribute("排障", "恢复事实"))[0]["id"]
    item = record(revised, ["排障"], [fact(revised, "排障", "恢复事实", "一直没有恢复", "客户验证", intent="correction", replaces=[previous])], old=old, anchor=first,
        changes=[dict(kind="correction", text="A上传一直没有恢复", replaces=fixed.content, evidence=[ref(revised)])])
    item["continuity"] = boundary("retract_recovery", revised)
    status = case.adopted(old, common("status"))[0]
    item["field_updates"].append(change(revised, common("status"), "正在排查", "跟进状态", intent="correction", replaces=[status["id"]]))
    case.send(revised, item, day=4)
    current = case.event()
    if independent:
        assert current.recovery is not None
        assert current.recovery.get("review_status") != "pending"
        assert current.status == "虚构已恢复" and current.closed_at is not None
    else:
        assert current.recovery is None and current.closed_at is None
        assert current.status == "正在排查"
        assert current.attributes["恢复事实"] == "一直没有恢复"
    assert current.updated_by[1]["snapshot"]["recovery"] is not None


@pytest.mark.parametrize("independent", [False, True])
def test_R6_payment_changed_to_plan(case, independent):
    specs = [("商务需求", "办理事实与结果", "客户已经付了", "付款陈述")]
    anchor, first, _ = seed(case, "客户已经付了。报价答复已完成。", ["商务需求"], specs)
    if independent:
        independent_proof(case, first, ["商务需求"], specs)
    old = case.event()
    previous = case.adopted(old, attribute("商务需求", "办理事实与结果"))[0]["id"]
    revised = replace(first, content="更正：客户只是准备付款，尚未支付。报价答复已完成。")
    item = record(revised, ["商务需求"], [fact(revised, "商务需求", "办理事实与结果", "准备付款，尚未支付", "付款陈述", intent="correction", replaces=[previous])], old=old, anchor=anchor,
        changes=[dict(kind="correction", text="准备付款，尚未支付", replaces="客户已经付了", evidence=[ref(revised)])])
    case.send(revised, item, day=3)
    current = case.event()
    assert current.attributes["办理事实与结果"] == ("客户已经付了" if independent else "准备付款，尚未支付")
    assert "报价答复已完成" in current.summary
    assert bool(pending(current, "办理事实与结果")) is independent


@pytest.mark.parametrize("independent", [False, True])
def test_R7_recurrence_basis_revision(case, independent):
    first, fixed, parent = recover(case)
    if independent:
        proof = msg("independent-recovery", 3, "A上传已恢复")
        item = record(proof, ["排障"], old=parent, anchor=first,
            changes=[dict(kind="confirmation", text=proof.content, replaces=None, evidence=[ref(proof)])])
        item["continuity"] = boundary("confirm_recovery", proof)
        case.send(proof, item)
    again = msg("new-round", 4, "A上传恢复后新一次失败。")
    item = record(again, ["排障"])
    item["basis"].append(ref(first))
    item["continuity"] = boundary("recurrence", again, parent.event_id)
    result = case.send(again, item)
    child = case.event(result.created[0])
    original = deepcopy(child)
    revised = replace(fixed, content="[文件]", markers=("[文件]",))
    case.send(revised, [], day=5)
    child = case.event(child.event_id)
    assert child.recurrence_of.get("review_status") == ("confirmed" if independent else "pending")
    assert bool(child.review_flag) is (not independent)
    assert child.updated_by[0] == original.updated_by[0]
    assert len(case.store.events) == 2


@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_R8_causal_relation_revision(case, independent, explicit):
    first = msg("T-fault", 1, "工单T：A下载故障，请排查。")
    case.send(first, record(first, ["排障"]))
    parent = case.event()
    relation_message = msg("M-plan", 2, "因T故障另立迁移项目M，决定迁出A。")
    item = record(relation_message, ["流失"])
    relation = dict(kind="caused_by", event_id=parent.event_id, intent="attach", reason="因T故障另立迁移项目M",
        evidence=[ref(relation_message)])
    item["relations"] = [relation]
    item["basis"].append(ref(first))
    result = case.send(relation_message, item)
    child = case.event(result.created[0])
    anchor = msg("M-identity", 3, "迁移项目M继续办理，已受理。")
    item = record(anchor, ["流失"], old=child, anchor=relation_message)
    if independent:
        anchor = replace(anchor, content="迁移项目M因T故障设立，继续办理。")
        item = record(anchor, ["流失"], old=child, anchor=relation_message)
        item["relations"] = [dict(relation, evidence=[ref(anchor)])]
        item["basis"].append(ref(first))
    case.send(anchor, item)
    before = deepcopy(case.event(child.event_id))
    revised = replace(relation_message, content="迁移项目M早已计划，与T无关。" if explicit else "迁移项目M继续办理。")
    item = []
    if explicit and not independent:
        item = record(revised, ["流失"], old=before, anchor=anchor)
        item["relations"] = [dict(relation, intent="retract", reason="迁移项目M早已计划，与T无关", evidence=[ref(revised)])]
        item["basis"].append(ref(first))
    case.send(revised, item, day=4)
    current = case.event(child.event_id)
    expected = "confirmed" if independent else "retracted" if explicit else "pending"
    assert current.relations[0]["review_status"] == expected
    response = case.client.get(f"/api/v1/events/{current.event_id}", params=dict(uid=current.uid, stream=current.stream))
    assert response.json()["relations"][0]["review_status"] == expected
    assert current.updated_by[:len(before.updated_by)] == before.updated_by
    assert len(case.store.events) == 2 and case.event(parent.event_id).types == ["排障"]
