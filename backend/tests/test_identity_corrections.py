from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
import json

import pytest

from final_replay import Replay, attribute, common, fact, record
from dake.analysis.field_changes import field_key
from dake.analysis.updates import snapshot_as_of
from dake.domain.models import Candidate
from dake.repositories.mysql import TransactionOutcomeUnknown
from dake.services.executions import receipt_for, restore_receipt
from test_classification_final import boundary, recover
from test_classification_replay import UID, STREAM, day_window, msg, ref


@pytest.fixture
def split(tmp_path, monkeypatch):
    monkeypatch.setattr("dake.services.processing.load_sender_roles", lambda _: {
        (UID, "synthetic", "synthetic-human"): "customer"})
    return build_split(tmp_path)


def build_split(path, *, again_content="A上传再次失败，请继续排查。"):
    case = Replay(path)
    first, fixed, parent = recover(case)
    again = msg("mistaken-round", 3, again_content, room="另一个合成群")
    item = record(again, ["排障"])
    item["basis"].append(ref(first))
    item["continuity"] = boundary("recurrence", again, parent.event_id)
    second = case.send(again, item).created[0]
    corrected = msg("same-round", 4, "之前测试用错环境，A上传实际一直未恢复，两项是同一次故障。")
    update = fact(corrected, "排障", "恢复事实", "一直未恢复", "客户验证", intent="correction",
                  replaces=[case.adopted(parent, dict(kind="attribute", type_label="排障", name="恢复事实"))[0]["id"]])
    item = record(corrected, ["排障"], [update], old=parent, anchor=first,
                  changes=[dict(kind="correction", text=corrected.content, replaces=fixed.content, evidence=[ref(corrected)])])
    item["continuity"] = boundary("retract_recovery", corrected)
    case.send(corrected, item)
    case.parent_id, case.duplicate_id = parent.event_id, second
    case.correction_message, case.first_message, case.again_message = corrected, first, again
    return case


def request_for(case, **overrides):
    message = case.correction_message
    body = dict(uid=UID, stream=STREAM, retained_event_id=case.parent_id,
        expected_version=case.event(case.duplicate_id).version,
        retained_expected_version=case.event(case.parent_id).version,
        operation_id="same-round-correction", operator="合成操作人", reason="核对原环境及同轮故障",
        same_round=True, scope="A上传", evidence=[dict(source=message.source, message_id=message.message_id,
            digest=message.digest, send_time=message.sent_at.isoformat(), quote=message.content)], resolutions=[])
    return dict(body, **overrides)


def submit(case, **overrides):
    return case.client.post(f"/api/v1/events/{case.duplicate_id}/same-round-corrections",
                            json=request_for(case, **overrides))


def test_A30_already_split_becomes_one_followup_and_keeps_old_identity(split):
    before = deepcopy(split.store.events)
    response = submit(split)
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "committed"
    params = dict(uid=UID, stream=STREAM)
    listing = split.client.get("/api/v1/events", params=params).json()
    assert listing["total"] == 1 and listing["items"][0]["event_id"] == split.parent_id
    alias = split.client.get(f"/api/v1/events/{split.duplicate_id}", params=params).json()
    assert alias["corrected_into"] == split.parent_id
    assert len(split.store.events) == 2
    for identity, old in before.items():
        current = split.event(identity)
        assert current.updated_by[:-1] == old.updated_by
        assert current.version == old.version + 1
    assert alias["status"] == before[split.duplicate_id].status
    assert alias["recurrence_of"]["review_status"] == "retracted"
    assert split.event(split.parent_id).recovery is None


@pytest.mark.parametrize("boundary_case", ["valid", "wrong-scope", "stale-version"])
def test_scope_uses_exact_saved_source_version_beyond_excerpt(split, tmp_path, boundary_case):
    content = "合成背景说明。" * 40 + ("B上传" if boundary_case == "wrong-scope" else "A上传") + "再次失败，请继续排查。"
    case = build_split(tmp_path / boundary_case, again_content=content)
    source = case.again_message
    assert "A上传" not in case.event(case.duplicate_id).evidence[0]["excerpt"]
    if boundary_case == "stale-version":
        case.store.messages[source.key] = replace(source, content=source.content + "合成修订")
    before = deepcopy(case.store.events)
    response = submit(case)
    if boundary_case != "valid":
        assert response.status_code == 400, response.text
        assert case.store.events == before
        return
    assert response.status_code == 200, response.text
    for identity, old in before.items():
        assert case.event(identity).updated_by[:-1] == old.updated_by
        assert case.event(identity).evidence[0]["digest"] == old.evidence[0]["digest"]
    assert case.store.messages[source.key].digest == source.digest


def field_decision(case, event_id, target, action="set", value="合成值", **kwargs):
    response = case.client.post(f"/api/v1/events/{event_id}/field-decisions", json=dict(uid=UID, stream=STREAM,
        expected_version=case.event(event_id).version, operation_id="field-" + str(case.event(event_id).version),
        operator="合成人工", reason="合成字段裁定", actions=[dict(target=target, action=action, value=value,
            stage="同轮事实", scope="本次事项", **kwargs)]))
    assert response.status_code == 200, response.text


def send_after(case, message, candidate=()):
    case.source.messages.append(message)
    case.provider.mapping[message.message_id] = list(candidate) if isinstance(candidate, tuple) else candidate
    return case.service.run_window(UID, day_window(message.sent_at.day))


def test_followup_through_alias_only_updates_retained_and_new_same_round_is_review(split):
    from dake.analysis.attribution import parse_attribution_output

    assert submit(split).status_code == 200
    before_alias = deepcopy(split.event(split.duplicate_id))
    follow = msg("alias-follow", 5, "A上传这同一次故障今天仍失败，请继续处理。")
    proposal = record(follow, ["排障"], old=before_alias, anchor=split.again_message)
    normalizations = []
    parsed = parse_attribution_output(json.dumps(dict(schema_version="5.0", candidates=[proposal])), [follow],
        [split.again_message], [split.event(split.parent_id)], normalizations=normalizations)
    assert parsed[0].referenced_event_id == split.parent_id
    assert any(item.get("aliases") == {split.duplicate_id: split.parent_id} for item in normalizations)
    result = send_after(split, follow, proposal)
    assert result.updated == [split.parent_id] and not result.created and not result.review, [
        (event_id, split.event(event_id).updated_by[-1].get("changed_fields")) for event_id in result.updated]
    assert split.event(split.duplicate_id) == before_alias
    assert len(result.events) == 1 and result.events[0]["event_id"] == split.parent_id
    mistake = msg("recreate", 6, "A上传仍失败。")
    result = send_after(split, mistake, record(mistake, ["排障"]))
    assert result.review and not result.created and len(split.store.events) == 2


def test_old_cycles_and_both_old_and_new_execution_receipts_remain_exact(split):
    old_result = split.rounds[-1]
    old_receipt = receipt_for(old_result, list(split.store.events.values()), [])
    old_snapshots = deepcopy(old_result.events)
    assert submit(split).status_code == 200
    assert restore_receipt(split.service, old_receipt).events == old_snapshots
    for event in split.store.events.values():
        expected = next(value for value in old_snapshots if value["event_id"] == event.event_id)
        assert snapshot_as_of(event, old_result.window.end) == expected
    result = split.service.run_window(UID, day_window(5))
    assert len(result.events) == 1 and result.events[0]["identity_corrections"]
    receipt = receipt_for(result, list(split.store.events.values()), [])
    assert receipt["event_versions"][0]["include_manual"] is True
    assert restore_receipt(split.service, receipt).events == result.events


@pytest.mark.parametrize("side", ["duplicate", "retained"])
def test_either_stale_version_is_409_with_no_partial_write(split, side):
    before = deepcopy(split.store.events)
    key = "expected_version" if side == "duplicate" else "retained_expected_version"
    response = submit(split, **{key: 0})
    assert response.status_code == 409
    assert split.store.events == before


def test_operation_retry_conflict_and_unknown_response_receipt_after_customer_lock(split):
    body = request_for(split)
    url = f"/api/v1/events/{split.duplicate_id}/same-round-corrections"
    first = split.client.post(url, json=body)
    assert first.status_code == 200
    before = deepcopy(split.store.events)
    assert split.client.post(url, json=body).json() == first.json()
    assert split.client.post(url, json=dict(body, reason="不同请求")).status_code == 409
    locked = []

    @contextmanager
    def customer_lock(uid):
        locked.append(uid)
        yield split.store

    split.store.customer_lock = customer_lock
    receipt = split.client.get(url + "/" + body["operation_id"], params=dict(uid=UID, stream=STREAM))
    assert receipt.json() == first.json() and locked == [UID]
    missing = split.client.get(url + "/missing-operation", params=dict(uid=UID, stream=STREAM))
    assert missing.json()["outcome"] == "not_committed" and locked == [UID, UID]
    assert split.store.events == before


def test_second_save_failure_rolls_back_both_sides_and_receipt(split, monkeypatch):
    before = deepcopy(split.store.events)
    save = split.store.save_event
    calls = []

    def fail_second(event):
        calls.append(event.event_id)
        if len(calls) == 2:
            raise RuntimeError("合成第二侧保存失败")
        save(event)

    monkeypatch.setattr(split.store, "save_event", fail_second)
    assert submit(split).status_code == 503
    assert split.store.events == before and len(calls) == 2


@pytest.mark.parametrize("mutation", ["self", "cross-customer", "cross-stream", "chain", "reverse", "stale-evidence", "A29"])
def test_invalid_identity_or_independent_round_cannot_be_corrected(split, mutation):
    overrides = {}
    if mutation == "self":
        overrides["retained_event_id"] = split.duplicate_id
    elif mutation == "cross-customer":
        split.event(split.parent_id).uid = "different-synthetic-customer"
    elif mutation == "cross-stream":
        split.event(split.parent_id).stream = "different-stream"
    elif mutation == "chain":
        split.event(split.parent_id).corrected_into = split.duplicate_id
    elif mutation == "reverse":
        split.event(split.duplicate_id).corrected_into = split.parent_id
    elif mutation == "stale-evidence":
        message = replace(split.correction_message, content="合成修订：该更正需重新核对")
        split.store.messages[message.key] = message
    elif mutation == "A29":
        split.event(split.parent_id).recovery = deepcopy(split.event(split.parent_id).updated_by[1]["snapshot"]["recovery"])
    before = deepcopy(split.store.events)
    assert submit(split, **overrides).status_code in {400, 404, 409}
    assert split.store.events == before


def test_independent_relations_and_retracted_recurrence_survive_message_version_roundtrip(split):
    third = deepcopy(split.event(split.parent_id))
    third.event_id = "synthetic-earlier-event"
    third.relations, third.recurrence_of = [], None
    split.store.events[third.event_id] = third
    relation = dict(kind="caused_by", event_id=third.event_id, reason="合成独立原因", review_status="confirmed",
        review_reason=None, evidence=[dict(source=split.first_message.source, message_id=split.first_message.message_id,
            digest=split.first_message.digest, send_time=split.first_message.sent_at.isoformat(),
            conversation_id=split.first_message.conversation_id, quote=split.first_message.content)], retraction_evidence=[])
    split.event(split.parent_id).relations.append(relation)
    assert submit(split).status_code == 200
    original = split.correction_message
    changed = replace(original, content="[文件]")
    split.source.messages = [message for message in split.source.messages if message.key != original.key]
    send_after(split, changed)
    assert split.event(split.duplicate_id).corrected_into == split.parent_id
    assert split.event(split.duplicate_id).recurrence_of["review_status"] == "retracted"
    split.source.messages = [message for message in split.source.messages if message.key != original.key]
    send_after(split, original)
    assert split.event(split.duplicate_id).recurrence_of["review_status"] == "retracted"
    assert split.event(split.parent_id).relations[0] == relation


def test_unlocked_conflict_preserves_original_facts_and_manual_operations(split):
    target = attribute("排障", "排查发现与根因")
    field_decision(split, split.parent_id, target, value="合成假设甲")
    field_decision(split, split.duplicate_id, target, value="合成假设乙")
    source = deepcopy(split.event(split.duplicate_id))
    old_operations = deepcopy(split.event(split.parent_id).manual_operations)
    assert submit(split).status_code == 200
    retained = split.event(split.parent_id)
    state = retained.field_states[field_key(target)]
    assert retained.attributes[target["name"]] == "合成假设甲"
    imported = next(value for value in state["facts"] if value["origin_event_id"] == split.duplicate_id)
    assert imported["origin_fact_id"] == source.field_states[field_key(target)]["adopted_ids"][0]
    assert imported["status"] == "suggested" and state["inherited_decisions"]
    assert retained.manual_operations == old_operations
    assert any(problem["fact_id"] == imported["id"] and problem["status"] == "open" for problem in retained.field_issues)


@pytest.mark.parametrize("action", ["unknown", "clear", "not_applicable"])
def test_manual_absence_is_not_default_missing(split, action):
    target = common("severity")
    field_decision(split, split.parent_id, target, action=action)
    field_decision(split, split.duplicate_id, target, value="合成等级")
    assert submit(split).status_code == 200
    state = split.event(split.parent_id).field_states[field_key(target)]
    assert next(value for value in state["facts"] if value["id"] in state["adopted_ids"])["value_state"] == {
        "unknown": "unknown", "clear": "cleared", "not_applicable": "not_applicable"}[action]
    assert any(value["value"] == "合成等级" and value["status"] == "suggested" for value in state["facts"])


def test_one_side_lock_is_carried_and_conflicting_locks_require_exact_local_resolution(split):
    target = attribute("排障", "排查发现与根因")
    field_decision(split, split.duplicate_id, target, value="合成假设乙")
    field_decision(split, split.duplicate_id, target, action="lock")
    field_decision(split, split.parent_id, target, value="合成假设甲")
    field_decision(split, split.parent_id, target, action="lock")
    assert submit(split).status_code == 400
    refs = [dict(event_id=event_id, fact_id=split.event(event_id).field_states[field_key(target)]["locked_ids"][0])
            for event_id in (split.parent_id, split.duplicate_id)]
    response = submit(split, resolutions=[dict(target=target, adopt=[refs[0]], acknowledged_locks=refs,
        locked=True, reason="明确核对两侧，保留原采用假设，另一假设保留历史")])
    assert response.status_code == 200, response.text
    state = split.event(split.parent_id).field_states[field_key(target)]
    assert state["locked"] and len(state["locked_ids"]) == 1 and state["inherited_decisions"]
    assert split.event(split.parent_id).attributes[target["name"]] == "合成假设甲"


def test_alias_field_entry_is_explicit_conflict(split):
    assert submit(split).status_code == 200
    body = dict(uid=UID, stream=STREAM, expected_version=split.event(split.duplicate_id).version,
        operation_id="alias-field", operator="合成人工", reason="合成", actions=[dict(target=common("status"), action="unknown")])
    response = split.client.post(f"/api/v1/events/{split.duplicate_id}/field-decisions", json=body)
    assert response.status_code == 409 and split.parent_id in response.text


def test_single_side_compatible_lock_is_carried_without_unlocking(split):
    target = attribute("排障", "排查发现与根因")
    field_decision(split, split.duplicate_id, target, value="合成已核实发现")
    field_decision(split, split.duplicate_id, target, action="lock")
    source = deepcopy(split.event(split.duplicate_id).field_states[field_key(target)])
    assert submit(split).status_code == 200
    retained = split.event(split.parent_id).field_states[field_key(target)]
    assert retained["locked"] and len(retained["locked_ids"]) == 1
    adopted = next(value for value in retained["facts"] if value["id"] in retained["locked_ids"])
    assert adopted["value"] == "合成已核实发现"
    assert adopted["origin_fact_id"] == source["locked_ids"][0]
    assert retained["inherited_decisions"][0]["decision"] == source["decision"]


def test_locked_recovery_conclusion_requires_explicit_local_resolution(split):
    target = attribute("排障", "恢复事实")
    field_decision(split, split.parent_id, target, action="lock")
    before = deepcopy(split.store.events)
    assert submit(split).status_code == 400 and split.store.events == before
    identity = split.event(split.parent_id).field_states[field_key(target)]["locked_ids"][0]
    reference = dict(event_id=split.parent_id, fact_id=identity)
    response = submit(split, resolutions=[dict(target=target, adopt=[reference], acknowledged_locks=[reference],
        locked=True, reason="人工核对并保留一直未恢复的采用结论及锁定")])
    assert response.status_code == 200, response.text
    assert split.event(split.parent_id).field_states[field_key(target)]["locked"]


def test_commit_response_unknown_is_verified_before_same_request_retry(split):
    transaction = split.store.transaction
    body = request_for(split)
    url = f"/api/v1/events/{split.duplicate_id}/same-round-corrections"

    @contextmanager
    def unknown(uid):
        with transaction(uid):
            yield split.store
        raise TransactionOutcomeUnknown("合成提交应答丢失")

    split.store.transaction = unknown
    assert split.client.post(url, json=body).status_code == 503
    receipt = split.client.get(url + "/" + body["operation_id"], params=dict(uid=UID, stream=STREAM)).json()
    assert receipt["outcome"] == "committed"
    before = deepcopy(split.store.events)
    split.store.transaction = transaction
    assert split.client.post(url, json=body).json() == receipt
    assert split.store.events == before


def test_revoked_label_and_archived_attributes_are_not_resurrected(split):
    source = split.event(split.duplicate_id)
    source.types.append("资源需求")
    target = attribute("资源需求", "目标总量与增减量")
    field_decision(split, source.event_id, target, value="合成目标200")
    field_decision(split, split.parent_id, dict(kind="label", type_label=None, name="资源需求"), value=False)
    assert submit(split).status_code == 200
    retained = split.event(split.parent_id)
    assert "资源需求" not in retained.types and target["name"] not in retained.attributes
    state = retained.field_states[field_key(target)]
    assert state["archived"] and state["facts"][0]["origin_event_id"] == source.event_id


def test_legacy_matching_resolves_alias_and_does_not_recreate_after_window(split):
    from fakes import FakeProvider

    assert submit(split).status_code == 200
    follow = msg("legacy-follow", 5, "A上传同一次故障仍失败。")
    candidate = Candidate(type="排障", topic_key="synthetic-topic", summary=follow.content,
                          evidence_ids=[follow.key], referenced_event_id=split.duplicate_id)
    split.service.provider = FakeProvider({follow.message_id: candidate}, name=STREAM)
    split.source.messages.append(follow)
    result = split.service.run_window(UID, day_window(5))
    assert result.updated == [split.parent_id] and not result.created
    late = msg("legacy-late", 20, "A上传仍失败。")
    split.source.messages.append(late)
    split.service.provider.mapping[late.message_id] = replace(candidate, evidence_ids=[late.key], referenced_event_id=None)
    result = split.service.run_window(UID, day_window(20))
    assert result.review and not result.created


def test_real_recovery_then_independent_new_round_remains_possible(split):
    assert submit(split).status_code == 200
    fixed = msg("real-fixed", 5, "A上传这次已恢复。")
    item = record(fixed, ["排障"], old=split.event(split.parent_id), anchor=split.first_message)
    item["continuity"] = boundary("confirm_recovery", fixed)
    send_after(split, fixed, item)
    assert split.event(split.parent_id).recovery is not None
    again = msg("real-recurrence", 6, "A上传恢复后今天新的影响窗口再次失败。")
    item = record(again, ["排障"])
    item["basis"].append(ref(split.first_message))
    item["continuity"] = boundary("recurrence", again, split.parent_id)
    result = send_after(split, again, item)
    assert len(result.created) == 1
    assert split.event(result.created[0]).recurrence_of["event_id"] == split.parent_id
