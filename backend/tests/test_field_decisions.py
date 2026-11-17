from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from dake.analysis.attribution import parse_attribution_output
from dake.analysis.field_changes import field_key
from dake.api.app_factory import create_app
from offline_http import TestClient
from test_classification_replay import ContractReplayProvider, UID, STREAM, day_window, msg, output_candidate, ref, replay


RESOURCE = dict(kind="attribute", type_label="资源需求", name="目标总量与增减量")


def change(message, target=RESOURCE, value="2000", stage="目标总量", intent="supplement", replaces=(), scope="申请R", reference=None):
    return dict(target=target, ref=reference or message.message_id, value=value, stage=stage, scope=scope,
        business_time=None, time_kind="unknown", intent=intent, replaces=list(replaces),
        reason="合成字段事实依据", evidence=[ref(message)])


def proposal(message, updates, **kwargs):
    result = output_candidate(message, label=updates[0]["target"]["type_label"] if updates else "资源需求", **kwargs)
    result["field_updates"] = updates
    return result


class FieldsProvider(ContractReplayProvider):
    def extract(self, new_messages, context, existing_events):
        candidates = []
        for message in new_messages:
            value = self.mapping.get(message.message_id, [])
            candidates.extend(deepcopy(value if isinstance(value, list) else [value]))
        return parse_attribution_output(json.dumps(dict(schema_version="5.0", candidates=candidates), ensure_ascii=False),
            new_messages, context, existing_events, history_items=self.background.items if self.background else ())


class Queries:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings

    def scope(self, uid=None, stream=None):
        if (uid, stream) != (UID, STREAM):
            raise ValueError("无效合成范围")
        return uid, stream

    @contextmanager
    def connection(self):
        yield self.store

    def event(self, event_id, uid, stream):
        return next((event.to_dict() for event in self.store.list_events(*self.scope(uid, stream))
                     if event.event_id == event_id), None)


def http(store, settings):
    app = create_app(settings, query_service=Queries(store, settings), start_worker=False, runner=object(),
        run_repository=SimpleNamespace(init_schema=lambda: None))
    return TestClient(app)


def body(version, operation, actions, **extra):
    return dict(uid=UID, stream=STREAM, expected_version=version, operation_id=operation,
        operator="合成操作人", reason="合成裁定理由", actions=actions, **extra)


def post(client, event_id, version, operation, *actions):
    return client.post(f"/api/v1/events/{event_id}/field-decisions", json=body(version, operation, list(actions)))


def setup_replay(tmp_path, messages, mapping):
    service, store, _, source = replay(tmp_path, messages)
    provider = FieldsProvider(mapping)
    service.provider = provider
    return service, store, provider, source


@pytest.mark.parametrize("name,value", [("status", "正在办理"), ("status", "虚构已恢复"), ("severity", "合成等级甲")])
@pytest.mark.parametrize("default", ["未知", "合成缺省"])
def test_first_common_fact_does_not_conflict_with_an_unprovided_default(tmp_path, name, value, default):
    first = msg("common-first", 1, "合成申请R" + value)
    target = dict(kind="common", name=name, type_label=None)
    opening = output_candidate(first, label="资源需求")
    opening["field_updates"] = [change(first, target, value, stage="跟进状态" if name == "status" else "等级")]
    service, store, _, _ = setup_replay(tmp_path, [first], {first.message_id: opening})
    service.settings = replace(service.settings, **{"default_" + name: default})
    event_id = service.run_window(UID, day_window(1)).created[0]
    event = store.events[event_id]
    assert getattr(event, name) == value
    state = event.field_states[field_key(target)]
    assert [fact["value"] for fact in state["facts"] if fact["id"] in state["adopted_ids"]] == [value]
    assert not event.field_issues
    assert event.closed_at == (first.sent_at if value in service.settings.closed_statuses else None)


def test_manual_closed_status_uses_chat_anchor_and_reopening_clears_it(tmp_path):
    first = msg("manual-close", 1, "合成申请R请继续办理")
    opening = output_candidate(first, label="资源需求")
    opening["field_updates"] = []
    service, store, _, _ = setup_replay(tmp_path, [first], {first.message_id: opening})
    event_id = service.run_window(UID, day_window(1)).created[0]
    target = dict(kind="common", name="status", type_label=None)
    with http(store, service.settings) as client:
        closed = post(client, event_id, store.events[event_id].version, "manual-close",
            dict(target=target, action="set", value="虚构已恢复"))
        assert closed.status_code == 200
        assert closed.json()["closed_at"] == first.sent_at.isoformat()
        assert closed.json()["updated_by"][-1]["message_ids"] == []
        assert closed.json()["field_states"][field_key(target)]["facts"][-1]["business_time"] is None
        reopened = post(client, event_id, closed.json()["version"], "manual-reopen",
            dict(target=target, action="set", value="正在办理"))
        assert reopened.status_code == 200 and reopened.json()["closed_at"] is None


@pytest.mark.parametrize("name,value", [("status", "正在办理"), ("severity", "合成等级甲")])
@pytest.mark.parametrize("old_kind", ["missing", "recorded_default", "recorded_default_history", "recorded_value", "manual_unknown", "manual_clear"])
def test_common_legacy_migration_distinguishes_missing_and_recorded_decisions(tmp_path, name, value, old_kind):
    first = msg("legacy-common", 1, "合成申请R已受理，原值未知")
    second = msg("new-common", 2, "合成申请R" + value)
    target = dict(kind="common", name=name, type_label=None)
    opening = output_candidate(first, label="资源需求")
    opening["field_updates"] = []
    service, store, provider, _ = setup_replay(tmp_path, [first, second], {first.message_id: opening})
    event_id = service.run_window(UID, day_window(1)).created[0]
    event = store.events[event_id]
    expected = "未知"
    stage, scope = "跟进状态", "申请R"
    if old_kind == "recorded_default":
        event.extensions["extraction_v1"][name] = dict(value="未知", quote="原值未知", evidence_ids=[first.key])
    elif old_kind == "recorded_default_history":
        event.updated_by[0]["snapshot"]["extensions"]["extraction_v1"][name] = dict(
            value="未知", quote="原值未知", evidence_ids=[first.key])
    elif old_kind == "recorded_value":
        expected = "历史已记录值"
        setattr(event, name, expected)
    elif old_kind in {"manual_unknown", "manual_clear"}:
        with http(store, service.settings) as client:
            action = "unknown" if old_kind == "manual_unknown" else "clear"
            saved = post(client, event_id, event.version, "manual-initial", dict(
                target=target, action=action, stage=stage, scope=scope))
            assert saved.status_code == 200
        expected = "未知" if old_kind == "manual_unknown" else ""
    update = output_candidate(second, label="资源需求", action="update", event_id=event_id, old_message=first)
    update["field_updates"] = [change(second, target, value, stage=stage, scope=scope)]
    provider.mapping[second.message_id] = update
    service.run_window(UID, day_window(2))
    current = store.events[event_id]
    assert getattr(current, name) == (value if old_kind == "missing" else expected)
    state = current.field_states[field_key(target)]
    assert state["facts"][-1]["status"] == ("adopted" if old_kind == "missing" else "suggested")
    if old_kind != "missing":
        assert any(issue["status"] == "open" for issue in current.field_issues)
    if old_kind in {"recorded_default", "recorded_default_history"}:
        assert state["facts"][0]["evidence"][0]["quote"] == "原值未知"


@pytest.mark.parametrize("action", ["set", "clear", "unknown", "automatic"])
def test_final_adopted_status_controls_closure_after_progress_and_manual_decision(tmp_path, action):
    from dake.analysis.matching import match
    from dake.domain.models import Candidate

    first = msg("status-first", 1, "合成申请R正在办理")
    closed = msg("status-closed", 2, "合成申请R虚构已恢复")
    followup = msg("status-followup", 3, "合成申请R补充依据")
    target = dict(kind="common", name="status", type_label=None)
    opening = output_candidate(first, label="资源需求")
    opening["field_updates"] = [change(first, target, "正在办理", stage="跟进状态")]
    service, store, provider, _ = setup_replay(tmp_path, [first, closed, followup], {first.message_id: opening})
    event_id = service.run_window(UID, day_window(1)).created[0]
    closing = output_candidate(closed, label="资源需求", action="update", event_id=event_id, old_message=first)
    closing["field_updates"] = [change(closed, target, "虚构已恢复", stage="完成", intent="progress")]
    provider.mapping[closed.message_id] = closing
    service.run_window(UID, day_window(2))
    event = store.events[event_id]
    assert event.status == "虚构已恢复" and event.closed_at == closed.sent_at
    assert event.updated_by[-1]["snapshot"]["closed_at"] == closed.sent_at.isoformat()
    provider.mapping[followup.message_id] = output_candidate(followup, label="资源需求", action="update",
        event_id=event_id, old_message=first, changes=[])
    provider.mapping[followup.message_id]["field_updates"] = []
    service.run_window(UID, day_window(3))
    event = store.events[event_id]
    assert event.end_time == followup.sent_at and event.closed_at == closed.sent_at
    candidate = Candidate("资源需求", event.topic_key, "合成宽限检查", [followup.key])
    outside = replace(followup, sent_at=closed.sent_at + service.settings.closed_grace + service.settings.interval)
    assert match(candidate, [event], {followup.key: outside}, service.settings)[0] is None
    state = event.field_states[field_key(target)]
    fact_id = state["adopted_ids"][-1]
    with http(store, service.settings) as client:
        if action == "automatic":
            reopening = msg("automatic-reopen", 4, "合成申请R重新打开并正在办理")
            service.source.messages.append(reopening)
            record = output_candidate(reopening, label="资源需求", action="update", event_id=event_id,
                old_message=first, changes=[])
            record["field_updates"] = [change(reopening, target, "正在办理", stage="重新办理", intent="progress")]
            provider.mapping[reopening.message_id] = record
            service.run_window(UID, day_window(4))
            saved = client.get(f"/api/v1/events/{event_id}", params=dict(uid=UID, stream=STREAM))
            assert saved.json()["status"] == "正在办理"
            assert saved.json()["end_time"] == reopening.sent_at.isoformat()
        else:
            saved = post(client, event_id, event.version, "manual-reopen-" + action,
                dict(target=target, action=action, fact_id=fact_id, value="正在办理" if action == "set" else None))
            assert saved.json()["end_time"] == followup.sent_at.isoformat()
            assert saved.json()["updated_by"][-1]["message_ids"] == []
        assert saved.status_code == 200 and saved.json()["closed_at"] is None


@pytest.mark.parametrize("locked_closed", [False, True])
def test_locked_adopted_status_rejects_suggestion_without_changing_closure(tmp_path, locked_closed):
    first = msg("status-lock", 1, "合成申请R已受理")
    value = "正在办理" if locked_closed else "虚构已恢复"
    second = msg("status-suggestion", 2, "合成申请R" + value)
    target = dict(kind="common", name="status", type_label=None)
    opening = output_candidate(first, label="资源需求")
    opening["field_updates"] = []
    service, store, provider, _ = setup_replay(tmp_path, [first, second], {first.message_id: opening})
    event_id = service.run_window(UID, day_window(1)).created[0]
    adopted = "虚构已恢复" if locked_closed else "正在办理"
    with http(store, service.settings) as client:
        locked = post(client, event_id, store.events[event_id].version, "status-lock",
            dict(target=target, action="set", value=adopted), dict(target=target, action="lock"))
        assert locked.status_code == 200
    update = output_candidate(second, label="资源需求", action="update", event_id=event_id, old_message=first, changes=[])
    update["field_updates"] = [change(second, target, value, stage="新阶段", intent="progress")]
    provider.mapping[second.message_id] = update
    service.run_window(UID, day_window(2))
    event = store.events[event_id]
    assert event.status == adopted
    assert event.closed_at == (first.sent_at if locked_closed else None)
    assert event.field_states[field_key(target)]["facts"][-1]["status"] == "suggested"


def test_a36_http_lock_message_revision_unlock_and_local_resolution(tmp_path):
    first = msg("request", 1, "合成申请R目标2000，请继续办理")
    anchor = msg("anchor", 1, "合成申请R已经受理", hour=8)
    opening = proposal(first, [change(first)])
    opening["evidence_ids"].append(anchor.key)
    opening["changes"].append(dict(kind="progress", text=anchor.content, evidence=[ref(anchor)], replaces=None))
    service, store, provider, source = setup_replay(tmp_path, [first, anchor], {first.message_id: opening})
    event_id = service.run_window(UID, day_window(1)).created[0]
    path = f"/api/v1/events/{event_id}"
    with http(store, service.settings) as client:
        current = client.get(path, params=dict(uid=UID, stream=STREAM)).json()
        locked = post(client, event_id, current["version"], "lock",
            dict(target=RESOURCE, action="confirm"), dict(target=RESOURCE, action="lock"))
        assert locked.status_code == 200
        locked_snapshot = deepcopy(locked.json())
        fact_id = locked.json()["field_states"][field_key(RESOURCE)]["adopted_ids"][0]
        revised = replace(first, content="合成申请R目标200，之前多写一个零；本次办理继续")
        source.messages = [revised, anchor]
        updates = [change(revised, value="200", intent="correction", replaces=[fact_id])]
        provider.mapping[first.message_id] = proposal(revised, updates, action="update", event_id=event_id, old_message=anchor,
            changes=[dict(kind="progress", text="本次办理继续", evidence=[ref(revised, "本次办理继续")], replaces=None)])
        outcome = service.run_window(UID, day_window(2))
        assert outcome.updated == [event_id] and not outcome.created
        current = client.get(path, params=dict(uid=UID, stream=STREAM)).json()
        state = current["field_states"][field_key(RESOURCE)]
        assert current["typed_attributes"]["资源需求"][RESOURCE["name"]]["value"] == "2000"
        assert state["locked"] and "本次办理继续" in current["summary"]
        assert "字段建议待复核：本次办理继续" not in current["summary"]
        proposed = next(fact for fact in state["facts"] if fact["status"] == "suggested")
        assert proposed["value"] == "200" and proposed["evidence"][0]["digest"] == revised.digest
        assert state["facts"][0]["evidence_status"] == "superseded"
        assert current["updated_by"][1]["snapshot"] == locked_snapshot["updated_by"][1]["snapshot"]
        unlocked = post(client, event_id, current["version"], "unlock", dict(target=RESOURCE, action="unlock"))
        assert unlocked.status_code == 200
        assert unlocked.json()["typed_attributes"]["资源需求"][RESOURCE["name"]]["value"] == "2000"
        assert any(item["status"] == "open" for item in unlocked.json()["field_issues"])
        store.events[event_id].review_flag = "合成独立疑点保留"
        issue_ids = [item["id"] for item in unlocked.json()["field_issues"] if item["field_key"] == field_key(RESOURCE)]
        adopted = post(client, event_id, unlocked.json()["version"], "adopt",
            dict(target=RESOURCE, action="adopt", fact_id=proposed["id"], issue_ids=issue_ids))
        assert adopted.status_code == 200
        assert adopted.json()["typed_attributes"]["资源需求"][RESOURCE["name"]]["value"] == "200"
        assert adopted.json()["review_flag"] == "合成独立疑点保留"
        assert adopted.json()["event_id"] == event_id
        receipt = client.get(path + "/field-decisions/adopt", params=dict(uid=UID, stream=STREAM))
        assert receipt.status_code == 200 and receipt.json()["operation_id"] == "adopt"


@pytest.mark.parametrize("same_batch", [False, True])
@pytest.mark.parametrize("label,name,stages", [
    ("商机", "采购意向与评估事实", ("采购评估", "签约")),
    ("流失", "风险、计划与实际变化", ("风险", "实际迁移")),
    ("排障", "处置与验证结果", ("处置", "客户验证")),
    ("资源需求", "审批、交付与可用验证", ("受理", "交付")),
    ("功能需求", "上线与需求验证结果", ("上线", "客户验收")),
    ("商务需求", "办理事实与结果", ("客户付款陈述", "到账核实")),
])
def test_each_type_keeps_progress_and_rejects_same_fact_conflict(tmp_path, same_batch, label, name, stages):
    target = dict(kind="attribute", type_label=label, name=name)
    first = msg("first", 1, "合成初始事实甲")
    second = msg("second", 2, "合成后续事实乙")
    conflict = msg("conflict", 2 if same_batch else 3, "合成互斥说法丙", hour=11)
    service, store, provider, _ = setup_replay(tmp_path, [first, second, conflict],
        {first.message_id: proposal(first, [change(first, target, "事实甲", stages[0])])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    provider.mapping[second.message_id] = proposal(second,
        [change(second, target, "事实乙", stages[1], intent="progress")], action="update", event_id=event_id, old_message=first)
    provider.mapping[conflict.message_id] = proposal(conflict,
        [change(conflict, target, "说法丙", stages[1])], action="update", event_id=event_id, old_message=first)
    service.run_window(UID, day_window(2))
    if not same_batch:
        service.run_window(UID, day_window(3))
    event = store.events[event_id]
    state = event.field_states[field_key(target)]
    assert [fact["value"] for fact in state["facts"] if fact["id"] in state["adopted_ids"]] == ["事实甲", "事实乙"]
    assert next(fact for fact in state["facts"] if fact["value"] == "说法丙")["status"] == "suggested"
    assert any(item["field_key"] == field_key(target) and item["status"] == "open" for item in event.field_issues)


def test_manual_clear_unknown_omission_atomic_failure_stale_and_idempotency(tmp_path):
    first = msg("opening", 1, "合成申请R目标2000")
    service, store, _, _ = setup_replay(tmp_path, [first], {first.message_id: proposal(first, [change(first)])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    assignees = dict(kind="common", name="assignees", type_label=None)
    with http(store, service.settings) as client:
        version = store.events[event_id].version
        cleared = post(client, event_id, version, "clear", dict(target=assignees, action="clear"))
        assert cleared.status_code == 200 and cleared.json()["assignees"] == []
        assert cleared.json()["field_states"][field_key(assignees)]["facts"][-1]["value_state"] == "cleared"
        stale = post(client, event_id, version, "old-page", dict(target=RESOURCE, action="unknown"))
        assert stale.status_code == 409
        before = deepcopy(store.events[event_id].to_dict())
        failed = post(client, event_id, cleared.json()["version"], "invalid-batch",
            dict(target=RESOURCE, action="unknown"), dict(target=assignees, action="set", value="错误类型"))
        assert failed.status_code == 400 and store.events[event_id].to_dict() == before
        unknown = post(client, event_id, cleared.json()["version"], "unknown", dict(target=RESOURCE, action="unknown"))
        assert unknown.status_code == 200
        assert unknown.json()["field_states"][field_key(RESOURCE)]["facts"][-1]["value_state"] == "unknown"
        manual_fact = unknown.json()["field_states"][field_key(RESOURCE)]["facts"][-1]
        assert manual_fact["operator"] == "合成操作人" and manual_fact["operation_id"] == "unknown" and manual_fact["decided_at"]
        assert unknown.json()["field_states"][field_key(assignees)] == cleared.json()["field_states"][field_key(assignees)]
        retry = post(client, event_id, cleared.json()["version"], "unknown", dict(target=RESOURCE, action="unknown"))
        assert retry.status_code == 200 and retry.json()["version"] == unknown.json()["version"]
        collision = post(client, event_id, cleared.json()["version"], "unknown", dict(target=RESOURCE, action="clear"))
        assert collision.status_code == 409


def test_another_fields_summary_correction_does_not_authorize_quantity(tmp_path):
    first = msg("first", 1, "合成申请R目标2000，办理人甲")
    second = msg("second", 2, "更正办理人应为乙；另有人称合成申请R目标200")
    service, store, provider, _ = setup_replay(tmp_path, [first, second],
        {first.message_id: proposal(first, [change(first)])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    provider.mapping[second.message_id] = proposal(second, [change(second, value="200")],
        action="update", event_id=event_id, old_message=first,
        changes=[dict(kind="correction", text="办理人乙", evidence=[ref(second, "更正办理人应为乙")], replaces="办理人甲")])
    service.run_window(UID, day_window(2))
    event = store.events[event_id]
    assert event.typed_attributes["资源需求"][RESOURCE["name"]]["value"] == "2000"
    assert "办理人乙" in event.summary


def test_locked_quantity_allows_independent_stage_and_blocks_label_hiding(tmp_path):
    first = msg("first", 1, "合成申请R目标2000")
    second = msg("second", 2, "合成申请R已获批")
    revoked = msg("revoked", 3, "合成更正：资源标签不成立，继续排查")
    service, store, provider, _ = setup_replay(tmp_path, [first, second, revoked],
        {first.message_id: proposal(first, [change(first)])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    with http(store, service.settings) as client:
        assert post(client, event_id, store.events[event_id].version, "lock-quantity",
                    dict(target=RESOURCE, action="lock")).status_code == 200
        provider.mapping[second.message_id] = proposal(second,
            [change(second, value="已获批", stage="审批", intent="progress")], action="update", event_id=event_id, old_message=first)
        service.run_window(UID, day_window(2))
        assert store.events[event_id].typed_attributes["资源需求"][RESOURCE["name"]]["value"] == "2000；已获批"
        provider.mapping[revoked.message_id] = output_candidate(revoked, label="排障", removed=["资源需求"],
            action="update", event_id=event_id, old_message=first)
        provider.mapping[revoked.message_id]["field_updates"] = []
        service.run_window(UID, day_window(3))
        event = store.events[event_id]
        assert "资源需求" in event.types and event.field_states[field_key(RESOURCE)]["locked"]
        target = dict(kind="label", name="资源需求", type_label=None)
        state = event.field_states[field_key(target)]
        suggestion = next(fact for fact in state["facts"] if fact["status"] == "suggested")
        problems = [item["id"] for item in event.field_issues if item["field_key"] == field_key(target) and item["status"] == "open"]
        rejected = post(client, event_id, event.version, "unsafe-revoke",
            dict(target=target, action="adopt", fact_id=suggestion["id"], issue_ids=problems))
        assert rejected.status_code == 400
        accepted = post(client, event_id, event.version, "revoke-with-archive",
            dict(target=target, action="adopt", fact_id=suggestion["id"], issue_ids=problems, archive_dependents=True))
        assert accepted.status_code == 200
        assert "资源需求" not in accepted.json()["types"]
        archived = accepted.json()["field_states"][field_key(RESOURCE)]
        assert archived["archived"] and not archived["locked"] and archived["facts"]


def test_field_correction_has_explicit_target_and_preserves_other_stage(tmp_path):
    first = msg("first", 1, "合成目标2000")
    second = msg("second", 2, "合成目标更正为200，办理已受理")
    service, store, provider, _ = setup_replay(tmp_path, [first, second],
        {first.message_id: proposal(first, [change(first)])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    identity = store.events[event_id].field_states[field_key(RESOURCE)]["adopted_ids"][0]
    provider.mapping[second.message_id] = proposal(second,
        [change(second, value="已受理", stage="受理", intent="progress", reference="accepted"),
         change(second, value="200", intent="correction", replaces=[identity], reference="corrected")],
        action="update", event_id=event_id, old_message=first)
    service.run_window(UID, day_window(2))
    event = store.events[event_id]
    assert event.typed_attributes["资源需求"][RESOURCE["name"]]["value"] == "已受理；200"
    assert event.field_states[field_key(RESOURCE)]["facts"][0]["status"] == "corrected"
    assert event.updated_by[0]["snapshot"]["typed_attributes"]["资源需求"][RESOURCE["name"]]["value"] == "2000"


def test_locked_summary_and_legacy_attributes_cannot_bypass_adoption(tmp_path):
    first = msg("first", 1, "合成目标2000")
    second = msg("second", 2, "合成目标200，继续办理")
    service, store, provider, _ = setup_replay(tmp_path, [first, second],
        {first.message_id: proposal(first, [change(first)])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    original = store.events[event_id].summary
    with http(store, service.settings) as client:
        summary = dict(kind="common", name="summary", type_label=None)
        assert post(client, event_id, store.events[event_id].version, "lock-fields",
            dict(target=summary, action="lock"), dict(target=RESOURCE, action="lock")).status_code == 200
        from test_classification_replay import ContractReplayProvider
        legacy = ContractReplayProvider()
        legacy.mapping[second.message_id] = output_candidate(second, label="资源需求", action="update", event_id=event_id,
            old_message=first, attributes=[("资源需求", RESOURCE["name"], "200")])
        service.provider = legacy
        service.run_window(UID, day_window(2))
        event = store.events[event_id]
        assert event.summary == original
        assert event.attributes[RESOURCE["name"]] == "2000"
        assert event.typed_attributes["资源需求"][RESOURCE["name"]]["value"] == "2000"
        assert any(item["field_key"] == field_key(RESOURCE) and item["status"] == "open" for item in event.field_issues)
        assert any(item["field_key"] == field_key(summary) and item["status"] == "open" for item in event.field_issues)


def test_unlocked_confirmed_summary_advances_and_records_its_new_basis(tmp_path):
    first = msg("first", 1, "合成申请R目标2000")
    second = msg("second", 2, "合成申请R已经受理")
    service, store, provider, _ = setup_replay(tmp_path, [first, second],
        {first.message_id: proposal(first, [change(first)])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    target = dict(kind="common", name="summary", type_label=None)
    with http(store, service.settings) as client:
        assert post(client, event_id, store.events[event_id].version, "summary-confirm",
                    dict(target=target, action="confirm")).status_code == 200
        old = deepcopy(store.events[event_id].to_dict())
        provider.mapping[second.message_id] = proposal(second, [], action="update", event_id=event_id, old_message=first)
        service.run_window(UID, day_window(2))
        event = store.events[event_id]
        assert second.content in event.summary
        state = event.field_states[field_key(target)]
        current = next(fact for fact in state["facts"] if fact["id"] in state["adopted_ids"])
        assert current["value"] == event.summary and current["evidence"][0]["digest"] == second.digest
        assert event.updated_by[1]["snapshot"] == old["updated_by"][1]["snapshot"]


def test_manual_adoption_preserves_another_business_time_and_unrelated_issue(tmp_path):
    first = msg("first", 1, "合成第一期目标2000")
    second = msg("second", 2, "合成第二期目标3000；另称第一期目标200")
    service, store, provider, _ = setup_replay(tmp_path, [first, second],
        {first.message_id: proposal(first, [dict(change(first), business_time="第一期", time_kind="planned")])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    provider.mapping[second.message_id] = proposal(second, [
        dict(change(second, value="3000", reference="phase2"), business_time="第二期", time_kind="planned"),
        dict(change(second, value="200", reference="conflict"), business_time="第一期", time_kind="planned")],
        action="update", event_id=event_id, old_message=first)
    service.run_window(UID, day_window(2))
    event = store.events[event_id]
    state = event.field_states[field_key(RESOURCE)]
    suggestion = next(fact for fact in state["facts"] if fact["status"] == "suggested")
    problem = next(item for item in event.field_issues if item["fact_id"] == suggestion["id"])
    unrelated = dict(problem, id="unrelated-issue", reason="同字段另一独立疑点", fact_id=None)
    event.field_issues.append(unrelated)
    with http(store, service.settings) as client:
        adopted = post(client, event_id, event.version, "adopt-phase1", dict(target=RESOURCE, action="adopt",
            fact_id=suggestion["id"], issue_ids=[problem["id"]]))
        assert adopted.status_code == 200
        assert adopted.json()["attributes"][RESOURCE["name"]] == "3000；200"
        assert next(item for item in adopted.json()["field_issues"] if item["id"] == unrelated["id"])["status"] == "open"
        invalid_time = post(client, event_id, adopted.json()["version"], "invalid-time", dict(target=RESOURCE,
            action="set", value="100", business_time="第三期", time_kind="unknown"))
        assert invalid_time.status_code == 422


def test_batch_reference_cannot_reach_a_previous_round(tmp_path):
    from dake.analysis.contract import ContractError

    first = msg("first", 1, "合成目标2000")
    second = msg("second", 2, "合成目标更正为200")
    service, store, provider, _ = setup_replay(tmp_path, [first, second],
        {first.message_id: proposal(first, [change(first, reference="old-local")])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    before = deepcopy(store.events[event_id].to_dict())
    provider.mapping[second.message_id] = proposal(second,
        [change(second, value="200", intent="correction", replaces=["batch:old-local"])],
        action="update", event_id=event_id, old_message=first)
    with pytest.raises(ContractError, match="目标不存在"):
        service.run_window(UID, day_window(2))
    assert store.events[event_id].to_dict() == before


def test_batch_correction_and_separate_field_evidence_are_kept(tmp_path):
    first = msg("first", 1, "合成目标2000，更正为200")
    progress = msg("progress", 1, "合成办理已经受理", hour=11)
    opening = proposal(first, [change(first, reference="initial"),
        change(first, value="200", intent="correction", replaces=["batch:initial"], reference="fixed")],
        changes=[dict(kind="progress", text=progress.content, evidence=[ref(progress)], replaces=None)])
    opening["evidence_ids"].append(progress.key)
    service, store, _, _ = setup_replay(tmp_path, [first, progress], {first.message_id: opening})
    event_id = service.run_window(UID, day_window(1)).created[0]
    event = store.events[event_id]
    assert event.attributes[RESOURCE["name"]] == "200"
    assert {item["message_id"] for item in event.evidence} == {first.message_id, progress.message_id}
    assert event.field_states[field_key(RESOURCE)]["facts"][0]["status"] == "corrected"


def test_resolve_rejects_only_the_selected_suggestion_and_history_stays_automatic(tmp_path):
    first = msg("first", 1, "合成目标2000")
    second = msg("second", 2, "合成目标200")
    service, store, provider, _ = setup_replay(tmp_path, [first, second],
        {first.message_id: proposal(first, [change(first)])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    provider.mapping[second.message_id] = proposal(second, [change(second, value="200")],
        action="update", event_id=event_id, old_message=first)
    automatic = service.run_window(UID, day_window(2))
    problem = store.events[event_id].field_issues[0]
    with http(store, service.settings) as client:
        resolved = post(client, event_id, store.events[event_id].version, "keep-original",
            dict(target=RESOURCE, action="resolve", issue_ids=[problem["id"]]))
        assert resolved.status_code == 200 and resolved.json()["attributes"][RESOURCE["name"]] == "2000"
        assert next(fact for fact in resolved.json()["field_states"][field_key(RESOURCE)]["facts"]
                    if fact["id"] == problem["fact_id"])["status"] == "rejected"
        repeat = service.run_window(UID, day_window(2))
        assert not repeat.processed and not repeat.updated
        assert repeat.events == automatic.events
        current = client.get(f"/api/v1/events/{event_id}", params=dict(uid=UID, stream=STREAM)).json()
        assert current["manual_operations"][-1]["operation_id"] == "keep-original"
        assert current["updated_by"][-1]["source"] == "manual" and not current["updated_by"][-1]["message_ids"]


def test_an_inactive_labels_attribute_requires_explicit_label_adoption(tmp_path):
    first = msg("first", 1, "合成目标2000")
    service, store, _, _ = setup_replay(tmp_path, [first], {first.message_id: proposal(first, [change(first)])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    target = dict(kind="attribute", type_label="商机", name="计划规模")
    with http(store, service.settings) as client:
        rejected = post(client, event_id, store.events[event_id].version, "hidden-attribute",
            dict(target=target, action="set", value="合成规模"))
        assert rejected.status_code == 400
        accepted = post(client, event_id, store.events[event_id].version, "label-and-attribute",
            dict(target=dict(kind="label", name="商机"), action="set", value=True),
            dict(target=target, action="set", value="合成规模"))
        assert accepted.status_code == 200 and "商机" in accepted.json()["types"]
        assert accepted.json()["attributes"][target["name"]] == "合成规模"


@pytest.mark.parametrize("invalid", ["other_field", "other_stage", "invented_time", "missing_value_proof"])
def test_field_correction_rejects_unverifiable_targets_and_metadata(tmp_path, invalid):
    from dake.analysis.contract import ContractError

    first = msg("first", 1, "合成目标2000")
    second = msg("second", 2, "合成目标更正为200")
    service, store, provider, _ = setup_replay(tmp_path, [first, second],
        {first.message_id: proposal(first, [change(first)])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    before = deepcopy(store.events[event_id].to_dict())
    identity = before["field_states"][field_key(RESOURCE)]["adopted_ids"][0]
    update = change(second, value="200", intent="correction", replaces=[identity])
    if invalid == "other_field":
        update["target"] = dict(RESOURCE, name="现有可用量")
    elif invalid == "other_stage":
        update["stage"] = "其他环节"
    elif invalid == "invented_time":
        update.update(business_time="原文没有的日期", time_kind="planned")
    else:
        update["value"] = "300"
    provider.mapping[second.message_id] = proposal(second, [update], action="update", event_id=event_id, old_message=first)
    with pytest.raises(ContractError):
        service.run_window(UID, day_window(2))
    assert store.events[event_id].to_dict() == before


def test_correcting_a_historical_fact_cannot_replace_a_different_current_fact(tmp_path):
    first = msg("first", 1, "合成目标2000")
    second = msg("second", 2, "合成目标更正为200")
    third = msg("third", 3, "合成原目标2000应更正为100")
    service, store, provider, _ = setup_replay(tmp_path, [first, second, third],
        {first.message_id: proposal(first, [change(first)])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    original = store.events[event_id].field_states[field_key(RESOURCE)]["adopted_ids"][0]
    for message, value, day in [(second, "200", 2), (third, "100", 3)]:
        provider.mapping[message.message_id] = proposal(message,
            [change(message, value=value, intent="correction", replaces=[original])],
            action="update", event_id=event_id, old_message=first)
        service.run_window(UID, day_window(day))
    event = store.events[event_id]
    assert event.attributes[RESOURCE["name"]] == "200"
    assert next(fact for fact in event.field_states[field_key(RESOURCE)]["facts"] if fact["value"] == "100")["status"] == "suggested"


def test_locked_common_summary_cannot_be_replaced_through_another_stage(tmp_path):
    first = msg("first", 1, "合成申请R目标2000")
    second = msg("second", 2, "合成申请R已经受理")
    service, store, provider, _ = setup_replay(tmp_path, [first, second],
        {first.message_id: proposal(first, [change(first)])})
    event_id = service.run_window(UID, day_window(1)).created[0]
    target = dict(kind="common", name="summary", type_label=None)
    with http(store, service.settings) as client:
        saved = post(client, event_id, store.events[event_id].version, "manual-summary-lock",
            dict(target=target, action="set", value=first.content), dict(target=target, action="lock"))
        assert saved.status_code == 200
        update = output_candidate(second, label="资源需求", action="update", event_id=event_id, old_message=first)
        update["field_updates"] = [change(second, target=target, value="已经受理", stage="独立环节", intent="progress")]
        provider.mapping[second.message_id] = update
        service.run_window(UID, day_window(2))
        event = store.events[event_id]
        assert event.summary == first.content and event.field_states[field_key(target)]["locked"]
        assert any(fact["value"] == "已经受理" and fact["status"] == "suggested"
                   for fact in event.field_states[field_key(target)]["facts"])


def test_legacy_other_reason_migrates_as_text_with_its_own_evidence(tmp_path):
    first = msg("first", 1, "合成专项培训，属于六类之外的培训交付")
    opening = output_candidate(first, label="其他", other_reason=dict(value="专项培训",
        evidence_ids=[first.key], quote=first.content))
    opening["field_updates"] = []
    service, store, _, _ = setup_replay(tmp_path, [first], {first.message_id: opening})
    event_id = service.run_window(UID, day_window(1)).created[0]
    target = dict(kind="common", name="other_reason", type_label=None)
    with http(store, service.settings) as client:
        confirmed = post(client, event_id, store.events[event_id].version, "confirm-other-reason",
            dict(target=target, action="confirm"))
        assert confirmed.status_code == 200
        fact = confirmed.json()["field_states"][field_key(target)]["facts"][0]
        assert fact["value"] == "专项培训" and fact["evidence"][0]["message_id"] == first.message_id
