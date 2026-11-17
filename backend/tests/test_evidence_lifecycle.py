from copy import deepcopy
from dataclasses import replace

import pytest

from final_replay import attribute, fact, record
from test_classification_final import boundary, case, recover
from test_classification_replay import msg, ref
from test_field_decisions import body


def open_issues(event, name):
    return [item for item in event.field_issues if item["status"] == "open" and item["target"]["name"] == name]


@pytest.mark.parametrize("original", [True, False])
def test_original_digest_returns_to_current_but_another_version_does_not(case, original):
    first = msg("basis-cycle", 1, "A下载失败。")
    case.send(first, record(first, ["排障"], [fact(first, "排障", "症状与对照表现", "A下载失败", "客户报告")]))
    initial = deepcopy(case.event().updated_by)
    case.send(replace(first, content="[文件]", markers=("[文件]",)), [], day=2)
    invalid = deepcopy(case.event().updated_by)
    final = first if original else replace(first, content="A下载仍失败，具体表现已更正。")
    result = case.send(final, [], day=3)
    event = case.event()
    assert result.processed == 1 and event.updated_by[:len(invalid)] == invalid
    assert event.updated_by[:len(initial)] == initial
    for name in ("排障", "症状与对照表现"):
        assert bool(open_issues(event, name)) is (not original)
    assert ("概述依据已修订" in (event.review_flag or "")) is (not original)
    for state in event.field_states.values():
        for value in state["facts"]:
            assert (value["evidence_status"] == "superseded") is (not original)
            assert all((proof.get("evidence_status") == "superseded") is (not original) for proof in value["evidence"])
    assert event.typed_attributes["排障"]["症状与对照表现"]["quote"] == (first.content if original else None)


def test_restoring_one_source_only_resolves_its_own_version_issues(case):
    first = msg("first-basis", 1, "A下载失败。")
    case.send(first, record(first, ["排障"], [fact(first, "排障", "症状与对照表现", "A下载失败", "客户报告")]))
    second = msg("second-basis", 2, "A根因为参数错误；另称A下载正常。")
    item = record(second, ["排障"], [fact(second, "排障", "排查发现与根因", "参数错误", "根因"),
        fact(second, "排障", "症状与对照表现", "A下载正常", "客户报告", intent="conflict")], old=case.event(), anchor=first)
    case.send(second, item)
    case.send(replace(first, content="[文件]", markers=("[文件]",)), [], day=3)
    case.send(replace(second, content="[图片]", markers=("[图片]",)), [], day=4)
    case.event().review_flag = "另一个业务疑点；" + (case.event().review_flag or "")
    case.send(first, [], day=5)
    event = case.event()
    assert not [item for item in open_issues(event, "症状与对照表现") if item["reason"] == "采用事实的直接消息依据已修订"]
    assert open_issues(event, "排查发现与根因")
    assert any(item["reason"] == "同一事实存在互斥说法" for item in open_issues(event, "症状与对照表现"))
    assert "另一个业务疑点" in event.review_flag and "概述依据已修订" in event.review_flag


def test_restored_source_cannot_reintroduce_a_manually_rejected_locked_label(case):
    first = msg("rejected-basis", 1, "申请A配额2000。")
    case.send(first, record(first, ["资源需求"], [fact(first, "资源需求", "目标总量与增减量", "2000", "目标")]))
    case.send(replace(first, content="[文件]", markers=("[文件]",)), [], day=2)
    event = case.event()
    target = dict(kind="label", name="资源需求", type_label=None)
    response = case.client.post(f"/api/v1/events/{event.event_id}/field-decisions", json=body(event.version,
        "reject-restored", [dict(target=target, action="set", value=False, archive_dependents=True), dict(target=target, action="lock")]))
    assert response.status_code == 200
    case.send(first, [], day=3)
    event = case.event()
    assert event.types == [] and not event.attributes and not event.typed_attributes.get("资源需求")
    assert event.field_states["label::资源需求"]["locked"]
    state = event.field_states["attribute:资源需求:目标总量与增减量"]
    assert state["archived"] and state["facts"][0]["status"] == "archived"
    assert state["facts"][0]["evidence_status"] != "superseded"


def test_restored_source_cannot_readopt_a_corrected_fact(case):
    first = msg("corrected-basis", 1, "申请A配额2000。")
    case.send(first, record(first, ["资源需求"], [fact(first, "资源需求", "目标总量与增减量", "2000", "目标")]))
    target = attribute("资源需求", "目标总量与增减量")
    identity = case.adopted(case.event(), target)[0]["id"]
    correction = msg("correction-basis", 2, "更正为200，原2000多写一个零。")
    case.send(correction, record(correction, ["资源需求"], [fact(correction, "资源需求", target["name"], "200", "目标",
        intent="correction", replaces=[identity])], old=case.event(), anchor=first,
        changes=[dict(kind="correction", text="200", replaces="2000", evidence=[ref(correction)])]))
    case.send(replace(first, content="[文件]", markers=("[文件]",)), [], day=3)
    case.send(first, [], day=4)
    event = case.event()
    assert event.attributes[target["name"]] == "200"
    old = next(value for value in event.field_states["attribute:资源需求:目标总量与增减量"]["facts"] if value["id"] == identity)
    assert old["status"] == "corrected" and old["evidence_status"] != "superseded"
    assert identity not in event.field_states["attribute:资源需求:目标总量与增减量"]["adopted_ids"]


def test_restored_source_keeps_manual_lock_and_its_conflicting_suggestion(case):
    from test_evidence_revision import seed

    anchor, first, event = seed(case, "申请A配额2000。", ["资源需求"], [("资源需求", "目标总量与增减量", "2000", "目标")])
    target = attribute("资源需求", "目标总量与增减量")
    response = case.client.post(f"/api/v1/events/{event.event_id}/field-decisions", json=body(event.version,
        "lock-lifecycle", [dict(target=target, action="confirm"), dict(target=target, action="lock")]))
    assert response.status_code == 200
    identity = case.adopted(case.event(), target)[0]["id"]
    revised = replace(first, content="申请A配额200，之前多写一个零。")
    item = record(revised, ["资源需求"], [fact(revised, "资源需求", target["name"], "200", "目标",
        intent="correction", replaces=[identity])], old=case.event(), anchor=anchor, changes=[])
    case.send(revised, item, day=2)
    conflict = next(item["id"] for item in case.event().field_issues if item["reason"] == "锁定采用值出现新建议")
    case.send(first, [], day=3)
    event = case.event()
    state = event.field_states["attribute:资源需求:目标总量与增减量"]
    assert event.attributes[target["name"]] == "2000" and state["locked"]
    assert next(value for value in state["facts"] if value["id"] == identity)["evidence_status"] == "current"
    assert any(value["value"] == "200" and value["status"] == "suggested" for value in state["facts"])
    assert next(item for item in event.field_issues if item["id"] == conflict)["status"] == "open"


def test_recovery_and_recurrence_recover_only_when_the_original_basis_returns(case):
    first, fixed, parent = recover(case)
    again = msg("lifecycle-again", 3, "A上传恢复后新一次失败。")
    item = record(again, ["排障"])
    item["basis"].append(ref(first))
    item["continuity"] = boundary("recurrence", again, parent.event_id)
    child = case.send(again, item).created[0]
    case.send(replace(fixed, content="[文件]", markers=("[文件]",)), [], day=4)
    assert case.event(parent.event_id).recovery["review_status"] == "pending"
    assert case.event(child).recurrence_of["review_status"] == "pending"
    history = deepcopy(case.event(child).updated_by)
    case.send(fixed, [], day=5)
    assert case.event(parent.event_id).recovery["review_status"] == "confirmed"
    assert case.event(child).recurrence_of["review_status"] == "confirmed"
    assert case.event(child).updated_by[:len(history)] == history
    assert len(case.store.events) == 2


@pytest.mark.parametrize("retracted", [False, True])
def test_relation_proofs_restore_without_reversing_an_explicit_retraction(case, retracted):
    first = msg("relation-lifecycle-parent", 1, "事项T的A下载失败。")
    case.send(first, record(first, ["排障"]))
    parent = case.event()
    second = msg("relation-lifecycle-child", 2, "因事项T另立迁移M。")
    relation = dict(kind="caused_by", event_id=parent.event_id, intent="attach", reason=second.content, evidence=[ref(second)])
    item = record(second, ["流失"])
    item["relations"] = [relation]
    item["basis"].append(ref(first))
    child = case.send(second, item).created[0]
    anchor = msg("relation-lifecycle-anchor", 3, "迁移M继续办理。")
    case.send(anchor, record(anchor, ["流失"], old=case.event(child), anchor=second))
    revised = replace(second, content="迁移M与T无关。" if retracted else "[文件]", markers=() if retracted else ("[文件]",))
    item = []
    if retracted:
        item = record(revised, ["流失"], old=case.event(child), anchor=anchor)
        item["relations"] = [dict(relation, intent="retract", reason=revised.content, evidence=[ref(revised)])]
        item["basis"].append(ref(first))
    case.send(revised, item, day=4)
    history = deepcopy(case.event(child).updated_by)
    case.send(second, [], day=5)
    current = case.event(child).relations[0]
    assert current["review_status"] == ("retracted" if retracted else "confirmed")
    assert current["evidence"][0].get("evidence_status") != "superseded"
    assert case.event(child).updated_by[:len(history)] == history
