"""客户确认恢复与复发关联的合成验收；禁止连接真实服务及读取真实配置。"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import socket

import httpx
import pytest

from dake.analysis.contract import ContractError
from dake.analysis.providers.xhub import XHubConfig, XHubProvider, configured_stream
from dake.analysis.updates import snapshot_as_of
from dake.api.schemas import EventResponse
from dake.config import HistoryRetrievalSettings, Settings
from dake.services.history_retrieval import HistoryRetriever
from dake.services.processing import IncrementalService
from dake.services.sender_identity import confirmed_sender_role, sender_role
from fakes import FakeSource, MemoryStore
from test_attribution import UID, candidate, message, model_output, window
from test_summary_history import change, followup, opening


@pytest.fixture(autouse=True)
def offline_customers(monkeypatch):
    """只配置虚构客户账号，显示标签和机器人配置另行构造。"""
    def forbidden(*args, **kwargs):
        raise RuntimeError("复发验收只允许合成输入和本地替身")
    monkeypatch.setattr(Settings, "load", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("pymysql.connect", forbidden)
    monkeypatch.setattr("dake.services.processing.load_sender_roles", lambda _: {
        (UID, "official_chat", "synthetic-human"): "customer",
        (UID, "official_chat", "synthetic-support"): "support"})


def boundary(kind, msg, parent=None, quote=None):
    return dict(kind=kind, previous_event_id=parent,
                evidence=[dict(message_id=msg.key, quote=quote or msg.content)])


def recovered(tmp_path):
    setup = opening(tmp_path)
    confirmation = message("recovered", 2, "虚构工单 CASE-SUM-42 我已重新测试，报告上传确实恢复正常。")
    followup(setup, confirmation, continuity=boundary("confirm_recovery", confirmation))
    assert setup[1].events[setup[-1]].recovery is not None
    return setup, confirmation


def recur(setup, msg, parent=None):
    service, _, provider, source, old, event_id = setup
    source.messages.append(msg)
    provider.mapping[msg.message_id] = candidate(msg, evidence=[old], changes=[change(msg)],
        continuity=boundary("recurrence", msg, parent or event_id))
    return service.run_window(UID, window(msg.sent_at.day))


def test_customer_confirmation_saves_proof_without_closing_ticket(tmp_path):
    setup, msg = recovered(tmp_path)
    service, store, _, _, old, event_id = setup
    event = store.events[event_id]
    assert event.closed_at is None and event.status == "未知"
    assert event.recovery["confirmed_at"] == msg.sent_at.isoformat()
    assert event.recovery["evidence"] == [dict(source=msg.source, message_id=msg.message_id, digest=msg.digest,
        conversation_id=msg.conversation_id, send_time=msg.sent_at.isoformat(), sender_id=msg.sender_id,
        sender_role="customer", quote=msg.content)]
    assert "recovery" in event.updated_by[-1]["changed_fields"]
    assert snapshot_as_of(event, window(1).end)["recovery"] is None
    api = EventResponse.model_validate(event.to_dict())
    assert api.recovery.evidence[0].digest == msg.digest
    before = deepcopy(event)
    assert service.run_window(UID, window(2)).processed == 0
    assert store.events[event_id] == before


@pytest.mark.parametrize("role", ["support", "internal", "external", "unknown", "other_customer", "other_source"])
def test_noncustomer_or_unconfirmed_identity_cannot_confirm(tmp_path, monkeypatch, role):
    setup = opening(tmp_path)
    actual_role = role if role in {"support", "internal", "external"} else "customer"
    account_uid = "synthetic-other-customer" if role == "other_customer" else UID
    source = "synthetic-other-source" if role == "other_source" else "official_chat"
    roles = {(account_uid, source, "synthetic-human"): actual_role} if role != "unknown" else {}
    monkeypatch.setattr("dake.services.processing.load_sender_roles", lambda _: roles)
    msg = message("claim", 2, "虚构工单报告已经恢复正常。", internal_hint=0)
    assert sender_role(msg.to_dict(), {}) == "customer"
    assert confirmed_sender_role(msg.to_dict(), {}) == "unknown"
    before = deepcopy(setup[1].events[setup[-1]])
    result = followup(setup, msg, continuity=boundary("confirm_recovery", msg))
    assert result.review and not result.updated and not result.created
    assert setup[1].events[setup[-1]] == before


def test_bot_recovery_with_customer_ack_is_not_customer_confirmation(tmp_path, monkeypatch):
    setup = opening(tmp_path)
    service, store, provider, source, old, event_id = setup
    bot = message("bot", 2, "虚构系统自动通知报告上传恢复。", sender_id="synthetic-bot")
    ack = message("ack", 2, "虚构工单的通知收到。")
    monkeypatch.setattr("dake.services.processing.load_sender_kinds", lambda _: {
        (UID, "official_chat", bot.sender_id, ""): "bot"})
    # 故意模拟错误模型输出；客户的“收到”不能为机器人的恢复结论背书。
    provider.mapping[ack.message_id] = candidate(ack, "update", event_id, [old], evidence_ids=[ack.key, bot.key],
        changes=[dict(kind="progress", text="虚构恢复结论", replaces=None,
            evidence=[dict(message_id=m.key, quote=m.content) for m in [ack, bot]])],
        continuity=boundary("confirm_recovery", bot))
    source.messages += [bot, ack]
    before = deepcopy(store.events[event_id])
    result = service.run_window(UID, window(2))
    assert result.review and store.events[event_id] == before


def test_cross_day_recurrence_creates_link_and_keeps_parent_unchanged(tmp_path):
    setup, confirmation = recovered(tmp_path)
    service, store, provider, _, old, event_id = setup
    parent = deepcopy(store.events[event_id])
    new = message("again", 10, "虚构工单 CASE-SUM-42 之前恢复了，今天同一报告上传再次失败。",
                  conversation_id="synthetic-other-room")
    result = recur(setup, new)
    assert not result.updated and not result.review and len(result.created) == 1
    event = store.events[result.created[0]]
    assert event.event_id != event_id and event.start_time == new.sent_at
    assert event.recurrence_of["event_id"] == event_id
    assert event.recurrence_of["recovery"] == parent.recovery
    assert event.recurrence_of["evidence"][0]["digest"] == new.digest
    assert event.recovery is None and store.events[event_id] == parent
    assert [ref["message_id"] for ref in event.evidence] == [new.message_id]
    assert {item["id"] for item in provider.background.items} >= {old.key, confirmation.key}
    assert EventResponse.model_validate(event.to_dict()).recurrence_of.event_id == event_id
    committed = deepcopy(store.events)
    assert service.run_window(UID, window(10)).processed == 0 and store.events == committed


@pytest.mark.parametrize("legacy_closed", [False, True])
def test_without_customer_recovery_suspected_recurrence_is_review(tmp_path, legacy_closed):
    setup = opening(tmp_path)
    if legacy_closed:
        msg = message("internal-fixed", 2, "虚构内部通知已经修复。")
        followup(setup, msg, status="虚构已恢复")
    before = deepcopy(setup[1].events)
    result = recur(setup, message("again", 10, "虚构工单又上传失败了。"))
    assert result.review and not result.created and not result.updated and setup[1].events == before


@pytest.mark.parametrize("explicit_history,day", [(True, 3), (False, 1)])
def test_late_or_retrospective_feedback_only_supplements_evidence(tmp_path, explicit_history, day):
    setup, confirmation = recovered(tmp_path)
    service, store, provider, source, old, event_id = setup
    msg = message("late", day, "虚构工单补充：这是恢复之前报告上传失败的日志。")
    before = deepcopy(store.events[event_id])
    source.messages.append(msg)
    provider.mapping[msg.message_id] = candidate(msg, "update", event_id, [old], changes=[change(msg)],
        status="虚构仍失败", continuity=boundary("historical", msg) if explicit_history else None)
    result = service.run_window(UID, window(3))
    event = store.events[event_id]
    assert result.updated == [event_id] and not result.created and not result.review
    assert event.recovery == before.recovery and event.status == before.status and event.summary == before.summary
    assert event.updated_by[-1]["change_kind"] == "evidence_only"


def test_ambiguous_update_does_not_reopen_recovered_event(tmp_path):
    setup, _ = recovered(tmp_path)
    before = deepcopy(setup[1].events)
    result = followup(setup, message("vague", 3, "虚构工单又不行了。"))
    assert result.review and not result.updated and setup[1].events == before


def test_customer_correction_retracts_recovery_and_preserves_old_version(tmp_path):
    setup, confirmation = recovered(tmp_path)
    before = deepcopy(setup[1].events[setup[-1]])
    correction = message("correction", 3, "虚构工单更正：此前测试错了，实际上一直没有恢复。")
    result = followup(setup, correction, continuity=boundary("retract_recovery", correction),
        changes=[change(correction, kind="correction", replaces=confirmation.content)])
    event = setup[1].events[setup[-1]]
    assert result.updated == [event.event_id] and not result.created and event.recovery is None
    assert event.closed_at is None and event.status == "未知"
    assert event.updated_by[:-1] == before.updated_by
    assert event.updated_by[-1]["change_kind"] == "correction"
    assert event.updated_by[-2]["snapshot"]["recovery"] == before.recovery
    assert followup(setup, message("ongoing", 4, "虚构工单 CASE-SUM-42 今天还是失败。")).updated == [event.event_id]


@pytest.mark.parametrize("reverse", [False, True])
def test_same_batch_recovery_and_recurrence_do_not_merge(tmp_path, reverse):
    setup = opening(tmp_path)
    service, store, provider, source, old, event_id = setup
    confirmation = message("confirmed", 2, "虚构工单测试上传已经恢复。")
    new = message("again", 2, "虚构工单恢复后同一上传问题再次发生。", sent_at=confirmation.sent_at + timedelta(hours=1))
    pair = [candidate(confirmation, "update", event_id, [old], changes=[change(confirmation)],
                      continuity=boundary("confirm_recovery", confirmation)),
            candidate(new, evidence=[old], changes=[change(new)], continuity=boundary("recurrence", new, event_id))]
    source.messages += [confirmation, new]
    provider.mapping[confirmation.message_id] = list(reversed(pair)) if reverse else pair
    result = service.run_window(UID, window(2))
    assert result.updated == [event_id] and len(result.created) == 1 and not result.review
    assert store.events[result.created[0]].recurrence_of["event_id"] == event_id
    assert [ref["message_id"] for ref in store.events[event_id].evidence] == [old.message_id, confirmation.message_id]


def test_single_customer_message_can_confirm_past_recovery_and_report_recurrence(tmp_path):
    setup = opening(tmp_path)
    service, store, provider, source, old, event_id = setup
    msg = message("both", 3, "虚构工单昨天我测试已恢复，今天同一上传问题再次发生。")
    confirmation = candidate(msg, "update", event_id, [old], changes=[change(msg, "客户说明昨天已恢复")],
        continuity=boundary("confirm_recovery", msg, quote="昨天我测试已恢复"))
    again = candidate(msg, evidence=[old], changes=[change(msg, "今天上传再次失败")],
        continuity=boundary("recurrence", msg, event_id, quote="今天同一上传问题再次发生"))
    provider.mapping[msg.message_id] = [again, confirmation]
    source.messages.append(msg)
    result = service.run_window(UID, window(3))
    assert result.updated == [event_id] and len(result.created) == 1 and not result.review
    assert store.events[event_id].recovery["confirmed_at"] == msg.sent_at.isoformat()


def test_later_recovery_in_same_batch_cannot_justify_earlier_recurrence(tmp_path):
    setup = opening(tmp_path)
    service, store, provider, source, old, event_id = setup
    again = message("again", 2, "虚构工单报告上传再次失败。")
    confirmation = message("fixed", 2, "虚构工单我测试上传已经恢复。", sent_at=again.sent_at + timedelta(hours=1))
    source.messages += [again, confirmation]
    provider.mapping[again.message_id] = candidate(again, evidence=[old], changes=[change(again)],
        continuity=boundary("recurrence", again, event_id))
    provider.mapping[confirmation.message_id] = candidate(confirmation, "update", event_id, [old],
        changes=[change(confirmation)], continuity=boundary("confirm_recovery", confirmation))
    result = service.run_window(UID, window(2))
    assert result.review and not result.created and result.updated == [event_id]
    assert store.events[event_id].recovery is not None


@pytest.mark.parametrize("recurrence_id,allowed", [("100", False), ("102", True)])
def test_same_second_messages_keep_numeric_id_order(tmp_path, recurrence_id, allowed):
    setup = opening(tmp_path)
    service, store, provider, source, old, event_id = setup
    fixed = message("101", 2, "虚构工单我测试确认上传恢复了。")
    again = message(recurrence_id, 2, "虚构工单上传恢复后再次发生同一故障。")
    source.messages += [fixed, again]
    provider.mapping[fixed.message_id] = candidate(fixed, "update", event_id, [old], changes=[change(fixed)],
        continuity=boundary("confirm_recovery", fixed))
    provider.mapping[again.message_id] = candidate(again, evidence=[old], changes=[change(again)],
        continuity=boundary("recurrence", again, event_id))
    result = service.run_window(UID, window(2))
    assert bool(result.created) is allowed and bool(result.review) is not allowed
    assert result.updated == [event_id]


@pytest.mark.parametrize("violation", ["unknown_parent", "other_uid", "other_stream", "bad_quote", "history_as_new", "update_target_on_new"])
def test_invalid_recurrence_rolls_back_whole_batch(tmp_path, violation):
    setup, _ = recovered(tmp_path)
    service, store, provider, source, old, event_id = setup
    msg = message("again", 3, "虚构工单 CASE-SUM-42 恢复后再次失败。")
    parent_id = event_id
    if violation in {"other_uid", "other_stream", "other_type"}:
        # 修改合成存储和其快照，使候选确实位于其他范围或主类型。
        event = store.events[event_id]
        key = {"other_uid": "uid", "other_stream": "stream", "other_type": "type"}[violation]
        value = "功能需求" if key == "type" else "synthetic-other"
        setattr(event, key, value)
        event.updated_by[-1]["snapshot"][key] = value
    if violation == "unknown_parent":
        parent_id = "synthetic-nonexistent-event"
    item = candidate(msg, evidence=[old], changes=[change(msg)], continuity=boundary("recurrence", msg, parent_id))
    if violation == "bad_quote":
        item.continuity["evidence"][0]["quote"] = "未出现在输入里的虚构引文"
    if violation == "history_as_new":
        item.continuity["evidence"] = [dict(message_id=old.key, quote=old.content)]
    if violation == "update_target_on_new":
        item.referenced_event_id = event_id
    provider.mapping[msg.message_id] = item
    source.messages.append(msg)
    before = deepcopy((store.events, store.messages, store.processed, store.attributions))
    with pytest.raises(ContractError):
        service.run_window(UID, window(3))
    assert (store.events, store.messages, store.processed, store.attributions) == before


def test_retrieval_prioritizes_recovery_proof_and_excludes_future_snapshot(tmp_path):
    setup, confirmation = recovered(tmp_path)
    service, store, _, _, _, event_id = setup
    latest = message("latest", 10, "虚构工单补充恢复前的测试记录。")
    followup(setup, latest, continuity=boundary("historical", latest))
    retriever = HistoryRetriever(store, HistoryRetrievalSettings(evidence_limit=1))
    new = message("query", 11, "虚构工单报告上传恢复后再次失败。")
    result = retriever.retrieve(UID, service.stream, [new], window(11).end)
    assert result.candidates[0].evidence_refs[0]["message_id"] == confirmation.message_id
    assert result.candidates[0].to_input()["recovery"]["confirmed_at"] == confirmation.sent_at.isoformat()
    assert "quote" not in result.candidates[0].to_input()["recovery"]
    # 元数据可见但恢复依据来自未来时，拒绝使用该快照。
    snapshot = store.events[event_id].updated_by[-1]["snapshot"]
    snapshot["recovery"]["confirmed_at"] = window(14).start.isoformat()
    unsafe = retriever.retrieve(UID, service.stream, [new], window(11).end)
    assert not unsafe.candidates and unsafe.unsafe_skipped == 1


def test_formal_provider_sends_confirmed_identity_and_roundtrips_recurrence(tmp_path):
    old = message("opening", 1)
    fixed = message("fixed", 2, "虚构工单 CASE-SYN-42 我测试报告上传已经恢复。")
    again = message("again", 10, "虚构工单 CASE-SYN-42 恢复后同一问题再次发生。")
    settings = Settings(uid=UID, data_dir=tmp_path, start=window(1).start, end=window(10).end, provider="xhub")
    payloads = []
    def respond(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        payloads.append(payload)
        msg = [old, fixed, again][len(payloads) - 1]
        fields = {}
        if msg != old:
            target = payload["existing_events"][0]["event_id"]
            fields = dict(basis=[dict(message_id=m.key, quote=m.content) for m in [msg, old]],
                continuity=boundary("confirm_recovery" if msg == fixed else "recurrence", msg,
                                    target if msg == again else None))
            if msg == fixed:
                fields.update(decision="update", referenced_event_id=target)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": model_output(msg, **fields)}}]})
    provider = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-placeholder", max_tokens=8192),
                            settings, transport=httpx.MockTransport(respond))
    store = MemoryStore()
    service = IncrementalService(store, FakeSource([old, fixed, again]), provider, settings)
    try:
        first = service.run_window(UID, window(1))
        service.run_window(UID, window(2))
        last = service.run_window(UID, window(10))
        assert len(last.created) == 1 and store.events[last.created[0]].recurrence_of["event_id"] == first.created[0]
        assert payloads[1]["new_messages"][0]["confirmed_sender_role"] == "customer"
        assert payloads[2]["existing_events"][0]["recovery"] is not None
        assert provider.name == configured_stream(settings) and len(payloads) == 3
    finally:
        provider.close()


def test_confirmation_is_material_even_if_old_summary_already_mentions_recovery(tmp_path):
    setup = opening(tmp_path)
    internal = message("internal", 2, "虚构工单内部报告已经恢复。", sender_id="synthetic-support")
    followup(setup, internal)
    customer = message("customer", 3, "虚构工单我重新测试确认恢复了。")
    result = followup(setup, customer, changes=[change(customer, internal.content)],
                      continuity=boundary("confirm_recovery", customer))
    event = setup[1].events[setup[-1]]
    assert result.updated == [event.event_id] and event.recovery is not None
    assert event.updated_by[-1]["summary_changed"] is False
    assert event.updated_by[-1]["changed_fields"] == ["recovery"]


def test_repeat_confirmation_does_not_move_original_recovery_boundary(tmp_path):
    setup, _ = recovered(tmp_path)
    saved = deepcopy(setup[1].events[setup[-1]].recovery)
    again = message("still-recovered", 3, "虚构工单今天再测试仍然正常。")
    followup(setup, again, continuity=boundary("confirm_recovery", again), changes=[change(again, kind="confirmation")])
    current = setup[1].events[setup[-1]].recovery
    assert {key: value for key, value in current.items() if key != "supporting_evidence"} == saved
    assert current["supporting_evidence"][0]["message_id"] == again.message_id
    before = deepcopy(setup[1].events)
    ack = message("ack", 4, "虚构工单的恢复反馈收到。")
    service, _, provider, source, old, event_id = setup
    source.messages.append(ack)
    provider.mapping[ack.message_id] = candidate(ack, "duplicate", event_id, [old], changes=[])
    result = service.run_window(UID, window(4))
    assert not result.updated and not result.created and setup[1].events == before


def test_late_recovery_does_not_override_newer_unresolved_feedback(tmp_path):
    setup = opening(tmp_path)
    followup(setup, message("ongoing", 10, "虚构工单今天上传仍然失败。"))
    service, store, provider, source, old, event_id = setup
    late = message("late-fixed", 9, "虚构工单昨天测试上传正常。")
    before = deepcopy(store.events)
    source.messages.append(late)
    provider.mapping[late.message_id] = candidate(late, "update", event_id, [old], changes=[change(late)],
        continuity=boundary("confirm_recovery", late))
    result = service.run_window(UID, window(11))
    assert result.review and not result.updated and store.events == before


def test_edited_recovery_message_can_retract_without_erasing_original_proof(tmp_path):
    setup, confirmation = recovered(tmp_path)
    service, store, provider, source, old, event_id = setup
    original = deepcopy(store.events[event_id].updated_by[-1])
    corrected = replace(confirmation, content="虚构工单更正：刚才测试对象错了，实际上一直没恢复。")
    source.messages[-1] = corrected
    provider.mapping[corrected.message_id] = candidate(corrected, "update", event_id, [old],
        changes=[change(corrected, kind="correction", replaces=confirmation.content)],
        continuity=boundary("retract_recovery", corrected))
    result = service.run_window(UID, window(3))
    event = store.events[event_id]
    assert result.updated == [event_id] and event.recovery is None
    assert event.updated_by[-2] == original
    assert original["snapshot"]["recovery"]["evidence"][0]["digest"] == confirmation.digest
    assert event.updated_by[-1]["changes"][0]["evidence"][0]["digest"] == corrected.digest


def test_competing_candidate_does_not_force_recurrence(tmp_path):
    setup, _ = recovered(tmp_path)
    service, store, provider, source, old, event_id = setup
    new = message("ambiguous", 3, "虚构工单上传又失败了，暂不确定是不是前次的问题。")
    source.messages.append(new)
    provider.mapping[new.message_id] = candidate(new, evidence=[old], changes=[change(new)],
        possible_event_ids=[event_id], continuity=boundary("recurrence", new, event_id))
    before = deepcopy(store.events)
    result = service.run_window(UID, window(3))
    assert result.review and not result.created and store.events == before


def test_recurrence_transaction_failure_leaves_no_child_or_receipt(tmp_path, monkeypatch):
    setup, _ = recovered(tmp_path)
    store = setup[1]
    before = deepcopy((store.events, store.messages, store.processed, store.attributions))
    def failed(*args):
        raise RuntimeError("虚构事务保存失败")
    monkeypatch.setattr(store, "record_attributions", failed)
    with pytest.raises(RuntimeError):
        recur(setup, message("again", 3, "虚构工单 CASE-SUM-42 恢复后今天再次失败。"))
    assert (store.events, store.messages, store.processed, store.attributions) == before


def test_recurrence_snapshot_failure_retry_does_not_duplicate_child(tmp_path, monkeypatch):
    setup, _ = recovered(tmp_path)
    service, store = setup[:2]
    original = service.state.snapshot
    def failed(*args):
        raise OSError("虚构快照失败")
    monkeypatch.setattr(service.state, "snapshot", failed)
    with pytest.raises(OSError):
        recur(setup, message("again", 3, "虚构工单 CASE-SUM-42 恢复后今天再次失败。"))
    saved = deepcopy(store.events)
    assert len(saved) == 2
    monkeypatch.setattr(service.state, "snapshot", original)
    result = service.run_window(UID, window(3))
    assert result.processed == 0 and not result.created and store.events == saved
