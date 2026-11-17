"""四类增量归属的离线验收；只使用虚构消息、内存事务和模拟模型响应。"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import socket

import httpx
import pytest

from dake.analysis.attribution import attribution_version, parse_attribution_output
from dake.analysis.contract import ContractError
from dake.analysis.providers.xhub import XHubConfig, XHubProvider, configured_stream
from dake.config import Settings
from dake.domain.models import Candidate, Event, Message, Window, parse_time
from dake.jobs.runner import prepare_request
from dake.repositories.mysql import MySQLStore, StorageError
from dake.services.processing import IncrementalService
from fakes import FakeProvider, FakeSource, MemoryStore


UID = "synthetic-attribution-customer"
TOPIC = "虚构报告上传"


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    """任何误接真实配置、网络和数据库的操作都立即失败。"""
    def forbidden(*args, **kwargs):
        raise RuntimeError("归属测试只允许显式配置和本地替身")

    monkeypatch.setattr(Settings, "load", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("pymysql.connect", forbidden)
    monkeypatch.setattr("dake.config.dotenv_values", forbidden)
    monkeypatch.setattr("dake.analysis.providers.xhub.dotenv_values", forbidden)


def window(day):
    start = parse_time(f"2038-06-{day:02d}")
    return Window(start, start + timedelta(days=1))


def message(mid, day, content="虚构工单 CASE-SYN-42 报告上传失败", **changes):
    return replace(Message(UID, mid, window(day).start + timedelta(hours=9), content,
                           conversation_id="synthetic-attribution-room", sender_id="synthetic-human"), **changes)


def candidate(new, action="new", old=None, evidence=(), **changes):
    refs = [new, *evidence]
    return replace(Candidate("排障", TOPIC, new.content, [new.key], decision=action,
        referenced_event_id=old, decision_reason="虚构输入明确支持本次决定",
        basis=[dict(message_id=m.key, quote=m.content) for m in refs]), **changes)


class AttributionProvider(FakeProvider):
    """模拟模型的明确决定，并接收实际补取的历史背景。"""

    def configure_history_context(self, background):
        self.background = deepcopy(background)


def build(tmp_path, messages, mapping=None):
    settings = Settings(uid=UID, data_dir=tmp_path, start=window(1).start, end=window(15).end,
                        provider="stub", closed_statuses=("虚构已恢复",))
    store = MemoryStore()
    provider = AttributionProvider(mapping, name="synthetic-attribution-stream")
    source = FakeSource(messages)
    return IncrementalService(store, source, provider, settings), store, provider, source


def opened(tmp_path):
    old = message("opening", 1)
    service, store, provider, source = build(tmp_path, [old], {old.message_id: candidate(old)})
    first = service.run_window(UID, window(1))
    return service, store, provider, source, old, first.created[0]


@pytest.mark.parametrize("same_batch", [False, True])
def test_independent_new_is_not_merged_by_topic(tmp_path, same_batch):
    a = message("a", 1)
    b = message("b", 1 if same_batch else 2, "另一虚构工单 CASE-SYN-99 是独立报告上传故障")
    service, store, provider, _ = build(tmp_path, [a, b], {"a": candidate(a), "b": candidate(b)})
    first = service.run_window(UID, window(1))
    second = first if same_batch else service.run_window(UID, window(2))
    assert len(store.events) == 2 and not second.updated
    assert len(second.created) == (2 if same_batch else 1)
    assert {tuple(e["message_id"] for e in event.evidence) for event in store.events.values()} == {("a",), ("b",)}


@pytest.mark.parametrize("day,renamed", [(2, True), (10, False), (10, True)])
def test_progress_reuses_identity_with_changed_topic_or_long_gap(tmp_path, day, renamed):
    service, store, provider, source, old, event_id = opened(tmp_path)
    follow = message("follow", day, "虚构工单 CASE-SYN-42 今天又试过仍上传失败，清理缓存也无效",
                     conversation_id="synthetic-second-room")
    source.messages.append(follow)
    provider.mapping[follow.message_id] = candidate(follow, "update", event_id, [old],
        topic_key="虚构文件传输" if renamed else TOPIC)
    result = service.run_window(UID, window(day))
    assert result.updated == [event_id] and not result.created and not result.review
    assert store.events[event_id].topic_key == TOPIC
    assert [e["message_id"] for e in store.events[event_id].evidence] == [old.message_id, follow.message_id]
    assert len(store.events[event_id].updated_by) == 2
    record = store.attributions[(follow.key, service.stream)]["records"][0]
    assert [ref["message_id"] for ref in record["new_evidence"]] == [follow.key]
    assert record["basis"][-1]["role"] == "history"
    assert record["basis"][-1]["conversation_id"] == old.conversation_id
    if day == 10:
        assert provider.calls[-1][1] == []
        assert provider.background.items[0]["id"] == old.key


def test_duplicate_keeps_entire_event_and_records_association_once(tmp_path):
    service, store, provider, source, old, event_id = opened(tmp_path)
    before = deepcopy(store.events[event_id])
    ack = message("ack", 2, "虚构工单 CASE-SYN-42 的消息收到了，没有其他反馈")
    source.messages.append(ack)
    provider.mapping[ack.message_id] = candidate(ack, "duplicate", event_id, [old])
    result = service.run_window(UID, window(2))
    assert result.processed == 1 and not result.created and not result.updated and not result.review
    assert store.events[event_id] == before
    receipt = store.attributions[(ack.key, service.stream)]
    assert receipt["digest"] == ack.digest
    assert receipt["records"][0]["event_id"] == event_id and receipt["records"][0]["action"] == "duplicate"
    retry = service.run_window(UID, window(2))
    assert retry.processed == 0 and len(provider.calls) == 2
    assert store.events[event_id] == before and store.attributions[(ack.key, service.stream)] == receipt


def test_review_and_new_in_same_message_do_not_repeat_creation(tmp_path):
    msg = message("mixed", 1, "新虚构工单明确独立，另一个之前的问题对象还不清楚")
    service, store, provider, _ = build(tmp_path, [msg], {"mixed": [candidate(msg), candidate(
        msg, "review", topic_key=None, decision_reason="另一事项指代不明", basis=[])]})
    first = service.run_window(UID, window(1))
    assert len(first.created) == len(first.review) == first.processed == 1
    assert [r["action"] for r in store.attributions[(msg.key, service.stream)]["records"]] == ["new", "review"]
    retry = service.run_window(UID, window(1))
    assert len(store.events) == len(provider.calls) == 1
    assert retry.processed == 0 and not retry.created


def test_multiple_candidates_preserve_review_alternatives(tmp_path):
    a, b = message("a", 1), message("b", 1, "虚构另一对象报告上传失败")
    service, store, provider, source = build(tmp_path, [a, b], {"a": candidate(a), "b": candidate(b)})
    first = service.run_window(UID, window(1))
    before = deepcopy(store.events)
    vague = message("vague", 2, "上次那个虚构报告上传问题还没好")
    source.messages.append(vague)
    provider.mapping["vague"] = candidate(vague, "review", topic_key=None, possible_event_ids=first.created,
                                           decision_reason="两个上传事项无法确定是哪一个")
    result = service.run_window(UID, window(2))
    assert result.processed == 1 and len(result.review) == 1 and store.events == before
    assert result.review[0]["possible_event_ids"] == first.created


@pytest.mark.parametrize("kind", ["no_current_proof", "no_old_proof", "closed", "competing"])
def test_insufficient_or_closed_target_becomes_review(tmp_path, kind):
    service, store, provider, source, old, event_id = opened(tmp_path)
    if kind == "closed":
        event = store.events[event_id]
        event.status, event.closed_at = "虚构已恢复", old.sent_at
        event.updated_by[-1]["snapshot"].update(status=event.status, closed_at=old.sent_at.isoformat())
    before = deepcopy(store.events)
    new = message("follow", 10)
    source.messages.append(new)
    item = candidate(new, "update", event_id, [old])
    if kind == "no_current_proof":
        item.basis = item.basis[1:]
    if kind == "no_old_proof":
        item.basis = item.basis[:1]
    if kind == "competing":
        item.possible_event_ids = [event_id]
    provider.mapping["follow"] = item
    result = service.run_window(UID, window(10))
    assert len(result.review) == 1 and not result.created and not result.updated
    assert result.review[0]["possible_event_ids"] == [event_id]
    assert store.events == before


@pytest.mark.parametrize("kind", ["forged_quote", "hidden_message", "history_as_new", "foreign_target"])
def test_invalid_refs_roll_back_messages_events_and_receipts(tmp_path, kind):
    service, store, provider, source, old, event_id = opened(tmp_path)
    new = message("follow", 2)
    source.messages.append(new)
    item = candidate(new, "update", event_id, [old])
    if kind == "forged_quote":
        item.basis[-1]["quote"] = "从未提供的虚构恢复结论"
    elif kind == "hidden_message":
        item.basis[-1]["message_id"] = "official_chat:missing"
    elif kind == "history_as_new":
        item.evidence_ids.append(old.key)
    else:
        item.referenced_event_id = "other-scope-event"
    provider.mapping["follow"] = item
    before = deepcopy((store.messages, store.events, store.processed, store.attributions))
    with pytest.raises(ValueError):
        service.run_window(UID, window(2))
    assert (store.messages, store.events, store.processed, store.attributions) == before


def test_message_edit_is_new_version_and_late_feedback_keeps_latest_state(tmp_path):
    service, store, provider, source, old, event_id = opened(tmp_path)
    day10 = message("latest", 10, "虚构报告上传已找到原因，等待修复")
    source.messages.append(day10)
    provider.mapping["latest"] = candidate(day10, "update", event_id, [old])
    service.run_window(UID, window(10))
    before = deepcopy(store.events[event_id])
    late = message("late", 9, "虚构工单 CASE-SYN-42 在第九天也曾上传失败")
    source.messages.append(late)
    provider.mapping["late"] = candidate(late, "update", event_id, [old])
    result = service.run_window(UID, window(11))
    assert result.updated == [event_id]
    assert store.events[event_id].summary == before.summary and store.events[event_id].end_time == before.end_time
    edited = replace(late, content=late.content + "，更正为上午")
    source.messages[-1] = edited
    provider.mapping["late"] = candidate(edited, "update", event_id, [old])
    result = service.run_window(UID, window(11))
    assert result.processed == 1 and len(store.events) == 1
    assert store.attributions[(late.key, service.stream)]["digest"] == edited.digest


def test_two_progress_items_for_same_id_have_one_revision(tmp_path):
    service, store, provider, source, old, event_id = opened(tmp_path)
    a, b = message("a", 2), message("b", 2, "虚构上传新增排查：其他客户端正常")
    source.messages += [a, b]
    provider.mapping.update(a=candidate(a, "update", event_id, [old]), b=candidate(b, "update", event_id, [old]))
    result = service.run_window(UID, window(2))
    assert result.updated == [event_id] and len(store.events[event_id].updated_by) == 2
    assert {ref["message_id"] for ref in store.events[event_id].evidence} == {old.message_id, "a", "b"}
    assert len(store.attributions) == 3


def test_receipt_failure_rolls_back_event_and_processing_marker(tmp_path, monkeypatch):
    msg = message("new", 1)
    service, store, _, _ = build(tmp_path, [msg], {"new": candidate(msg)})
    def failed(*args):
        raise RuntimeError("虚构收据保存失败")
    monkeypatch.setattr(store, "record_attributions", failed)
    with pytest.raises(RuntimeError):
        service.run_window(UID, window(1))
    assert not store.events and not store.messages and not store.processed and not store.attributions


def test_snapshot_failure_retry_keeps_committed_receipt(tmp_path, monkeypatch):
    msg = message("new", 1)
    service, store, provider, _ = build(tmp_path, [msg], {"new": candidate(msg)})
    save = service.state.snapshot
    def failed(*args):
        raise OSError("虚构快照写入失败")
    monkeypatch.setattr(service.state, "snapshot", failed)
    with pytest.raises(OSError):
        service.run_window(UID, window(1))
    assert len(store.events) == len(store.attributions) == 1
    monkeypatch.setattr(service.state, "snapshot", save)
    result = service.run_window(UID, window(1))
    assert result.processed == 0 and len(provider.calls) == 1 and len(store.events) == 1


def model_output(msg, **changes):
    """完整的虚构模型协议，不依赖已有聊天夹具。"""
    value = dict(type="排障", topic_key=TOPIC, summary=msg.content, evidence_ids=[msg.key], status=None,
                 severity=None, assignees=[], attributes=[], extensions=[], decision="new",
                 referenced_event_id=None, decision_reason="新消息明确描述独立事项",
                 type_labels=[dict(type="排障", evidence_ids=[msg.key], quote=msg.content)],
                 removed_types=[], typed_attributes=[], classification_status="已分类", other_reason=None,
                 basis=[dict(message_id=msg.key, quote=msg.content)], possible_event_ids=[], continuity=None,
                 field_updates=[], changes=[] if changes.get("decision") in {"duplicate", "review"} else [dict(
                     kind="progress", text=msg.content, evidence=[dict(message_id=msg.key, quote=msg.content)], replaces=None)])
    value.update(changes)
    return json.dumps(dict(schema_version="5.0", candidates=[value]), ensure_ascii=False)


def test_formal_xhub_uses_four_way_contract_without_changing_stream(tmp_path):
    msg = message("new", 1)
    settings = Settings(uid=UID, data_dir=tmp_path, start=window(1).start, end=window(2).end, provider="xhub")
    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": model_output(msg)}}]})
    provider = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-placeholder", max_tokens=8192),
                            settings, transport=httpx.MockTransport(respond))
    try:
        before_stream = provider.name
        service = IncrementalService(MemoryStore(), FakeSource([msg]), provider, settings)
        result = service.run_window(UID, window(1))
        assert len(result.created) == len(calls) == 1
        assert provider.name == before_stream == configured_stream(settings)
        payload = json.loads(calls[0]["messages"][1]["content"])
        assert payload["attribution_policy"]["version"] == attribution_version()
        assert calls[0]["response_format"]["json_schema"]["schema"]["properties"]["schema_version"]["const"] == "5.0"
        request, _ = prepare_request(settings, {})
        assert request["attribution_version"] == attribution_version()
        assert result.filter_audit[0]["action"] == "new"
    finally:
        provider.close()


def test_formal_xhub_repairs_legacy_output_instead_of_using_legacy_field_rules(tmp_path):
    msg = message("new", 1)
    settings = Settings(uid=UID, data_dir=tmp_path, start=window(1).start, end=window(2).end, provider="xhub")
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        payload = json.loads(model_output(msg))
        if len(calls) == 1:
            payload["schema_version"] = "4.0"
            payload["candidates"][0].pop("field_updates")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]})

    provider = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-placeholder", max_tokens=settings.llm_max_tokens), settings,
                            transport=httpx.MockTransport(respond))
    try:
        result = IncrementalService(MemoryStore(), FakeSource([msg]), provider, settings).run_window(UID, window(1))
        assert len(result.created) == 1 and len(calls) == 2
        assert "schema_version 5.0" in calls[-1]["messages"][-1]["content"]
        assert provider.name == configured_stream(settings)
    finally:
        provider.close()


@pytest.mark.parametrize("count", [0, 1])
def test_mysql_receipt_uses_scoped_version_check_without_database(count):
    store = object.__new__(MySQLStore)
    store._transaction_uid = UID
    calls = []
    def execute(sql, args=()):
        calls.append((sql, args))
        return [], count
    store._execute = execute
    msg = message("new", 1)
    records = [dict(action="review", new_evidence=[dict(message_id=msg.key)])]
    if count:
        store.record_attributions([msg], "synthetic-stream", records)
    else:
        with pytest.raises(StorageError):
            store.record_attributions([msg], "synthetic-stream", records)
    sql, args = calls[0]
    assert "uid=%s AND digest=%s" in sql and "processed_by=JSON_SET" in sql
    assert args[2:] == (msg.source, msg.message_id, UID, msg.digest)
    assert json.loads(args[1])["records"] == records


@pytest.mark.parametrize("action", ["review", "new", "empty"])
def test_no_candidates_does_not_force_creation(tmp_path, action):
    msg = message("unmatched", 1, "之前那个问题还没好" if action == "review" else "虚构独立新诉求")
    mapping = [] if action == "empty" else candidate(msg, action, topic_key=None if action == "review" else TOPIC)
    service, store, _, _ = build(tmp_path, [msg], {msg.message_id: mapping})
    result = service.run_window(UID, window(1))
    assert len(result.created) == int(action == "new")
    assert len(result.review) == int(action == "review")
    assert result.processed == 1


def test_future_edit_cannot_prove_old_event_version(tmp_path):
    service, store, provider, source, old, event_id = opened(tmp_path)
    edited = replace(old, content=old.content + "，这是之后才编辑的内容")
    source.messages[0] = edited
    store.messages[old.key] = edited
    store.processed[(old.key, service.stream)] = edited.digest
    new = message("follow", 2)
    source.messages.append(new)
    provider.mapping["follow"] = candidate(new, "update", event_id, [old])
    before = deepcopy(store.events)
    result = service.run_window(UID, window(2))
    assert len(result.review) == 1 and store.events == before


def test_same_batch_after_close_is_review_without_reopening(tmp_path):
    service, store, provider, source, old, event_id = opened(tmp_path)
    closed = message("closed", 2, "虚构工单已恢复")
    again = replace(message("again", 2, "虚构工单下午再次失败"), sent_at=closed.sent_at + timedelta(hours=3))
    source.messages += [closed, again]
    provider.mapping.update(closed=candidate(closed, "update", event_id, [old], status="虚构已恢复"),
                            again=candidate(again, "update", event_id, [old]))
    result = service.run_window(UID, window(2))
    assert result.updated == [event_id] and len(result.review) == 1 and not result.created
    assert store.events[event_id].closed_at == closed.sent_at
    assert {ref["message_id"] for ref in store.events[event_id].evidence} == {old.message_id, closed.message_id}


def test_bot_is_only_auxiliary_when_human_has_new_facts(tmp_path, monkeypatch):
    bot = message("bot", 1, "虚构监控告警", sender_id="synthetic-bot")
    human = message("human", 1, "虚构上传确实失败，请排查")
    service, store, provider, _ = build(tmp_path, [bot, human], {human.message_id: candidate(
        human, evidence_ids=[human.key, bot.key])})
    monkeypatch.setattr("dake.services.processing.load_sender_kinds", lambda _: {
        (UID, bot.source, bot.sender_id, bot.conversation_id): "bot"})
    result = service.run_window(UID, window(1))
    assert result.processed == 2 and len(result.created) == 1
    assert [m.key for m in provider.calls[0][0]] == [human.key]
    assert [m.key for m in provider.calls[0][1]] == [bot.key]
    assert store.attributions[(bot.key, service.stream)]["records"][0]["event_id"] == result.created[0]


def test_queued_task_policy_change_does_not_change_analysis_scope(tmp_path, monkeypatch):
    from test_runs import execute_next, run_fixture

    settings, repository, runner, _, _, _, _, total, seen = run_fixture(tmp_path, days=1)
    selected = replace(settings, provider="xhub")
    request, total = prepare_request(selected, {"interval": "1d"})
    stream = configured_stream(selected)
    monkeypatch.setattr("dake.analysis.attribution.ATTRIBUTION_POLICY", "虚构策略新版本")
    assert configured_stream(selected) == stream
    runner.settings = selected
    run = execute_next(repository, runner, request, total)
    assert run["status"] == "failed" and run["completed_windows"] == 0 and not seen
    assert "归属策略" in run["error"]


def test_formal_model_update_uses_fetched_old_quote_and_new_writable_evidence(tmp_path):
    old, new = message("opening", 1), message("follow", 10, "虚构工单 CASE-SYN-42 今天再试仍上传失败")
    settings = Settings(uid=UID, data_dir=tmp_path, start=window(1).start, end=window(10).end, provider="xhub")
    calls = []
    def respond(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        if len(calls) == 1:
            content = model_output(old)
        else:
            target = payload["existing_events"][0]["event_id"]
            content = model_output(new, decision="update", referenced_event_id=target, topic_key="虚构文件传输",
                basis=[dict(message_id=m.key, quote=m.content) for m in [new, old]])
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})
    provider = XHubProvider(XHubConfig(model="synthetic-model", api_key="synthetic-placeholder"), settings,
                            transport=httpx.MockTransport(respond))
    store = MemoryStore()
    service = IncrementalService(store, FakeSource([old, new]), provider, settings)
    try:
        first = service.run_window(UID, window(1))
        second = service.run_window(UID, window(10))
        assert second.updated == first.created and not second.created and len(calls) == 2
        assert calls[-1]["context_messages"] == []
        assert calls[-1]["history_context"]["items"][0]["id"] == old.key
        record = second.filter_audit[0]
        assert [ref["message_id"] for ref in record["new_evidence"]] == [new.key]
        assert record["basis"][-1]["role"] == "history"
    finally:
        provider.close()


def test_formal_schema_requires_evidence_and_rejects_invented_target():
    msg = message("new", 1)
    with pytest.raises(ContractError):
        parse_attribution_output(model_output(msg, decision="duplicate", referenced_event_id="invented"), [msg], [], [])
    assert parse_attribution_output(model_output(msg), [msg], [], [])[0].decision == "new"


def test_formal_new_event_saves_typed_attribute_and_revision_snapshot(tmp_path):
    msg = message("resource-new", 1, "请将虚构环境配额提高到2000次/分钟")
    settings = Settings(uid=UID, data_dir=tmp_path, start=window(1).start, end=window(2).end, provider="xhub")
    fact = dict(type_label="资源需求", name="目标总量与增减量", value="2000次/分钟",
                evidence_ids=[msg.key], quote=msg.content)
    content = model_output(msg, type="资源需求", type_labels=[
        dict(type="资源需求", evidence_ids=[msg.key], quote=msg.content)],
        field_updates=[dict(target=dict(kind="attribute", type_label="资源需求", name=fact["name"]),
            ref="opening-quantity", value=fact["value"], stage="目标总量", scope="虚构环境", business_time=None,
            time_kind="unknown", intent="supplement", replaces=[], reason="合成资源申请直接依据",
            evidence=[dict(message_id=msg.key, quote=msg.content)])])
    def respond(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    provider = XHubProvider(
        XHubConfig(model=settings.llm_model, api_key="synthetic-placeholder", max_tokens=8192),
        settings,
        transport=httpx.MockTransport(respond),
    )
    store = MemoryStore()
    service = IncrementalService(store, FakeSource([msg]), provider, settings)
    try:
        result = service.run_window(UID, window(1))

        assert len(result.created) == 1 and not result.updated and not result.review
        event = store.events[result.created[0]]
        expected = {"value": fact["value"], "evidence_ids": fact["evidence_ids"], "quote": fact["quote"],
                    "name": fact["name"], "type_label": fact["type_label"], "value_state": "value"}
        expected["evidence_refs"] = [dict(source=msg.source, message_id=msg.message_id,
            digest=msg.digest, conversation_id=msg.conversation_id, send_time=msg.sent_at.isoformat(),
            quote=msg.content)]
        assert event.typed_attributes["资源需求"]["目标总量与增减量"] == expected
        assert event.attributes["目标总量与增减量"] == fact["value"]
        assert event.evidence[0]["message_id"] == msg.message_id
        snapshot = event.updated_by[0]["snapshot"]
        assert snapshot["typed_attributes"]["资源需求"]["目标总量与增减量"] == expected
        assert snapshot["evidence"][0]["message_id"] == msg.message_id
    finally:
        provider.close()


def test_formal_labels_require_independent_evidence_and_support_pending_classification():
    from dake.analysis.attribution import AttributionOutput

    msg = message("classification", 1)
    labels = [dict(type="排障", evidence_ids=[msg.key], quote=msg.content),
              dict(type="资源需求", evidence_ids=[msg.key], quote=msg.content)]
    parsed = parse_attribution_output(model_output(msg, type_labels=labels), [msg], [], [])[0]
    assert parsed.label_types() == ["排障", "资源需求"]
    assert set(parsed.type_evidence) == {"排障", "资源需求"}

    missing = json.loads(model_output(msg))
    del missing["candidates"][0]["type_labels"]
    with pytest.raises(ContractError):
        parse_attribution_output(json.dumps(missing, ensure_ascii=False), [msg], [], [])

    pending = json.loads(model_output(msg, type="待分类", classification_status="待分类", type_labels=[]))
    assert parse_attribution_output(json.dumps(pending, ensure_ascii=False), [msg], [], [])[0].types == []

    duplicate_retraction = json.loads(model_output(msg, decision="duplicate", type_labels=[
        dict(type="排障", evidence_ids=[msg.key], quote=msg.content)], removed_types=[]))
    duplicate_retraction["candidates"][0]["removed_types"] = [
        dict(type="排障", evidence_ids=[msg.key], quote=msg.content)]
    duplicate_retraction["candidates"][0]["referenced_event_id"] = "old-event"
    with pytest.raises(ContractError):
        parse_attribution_output(json.dumps(duplicate_retraction, ensure_ascii=False), [msg], [], [
            Event.from_dict(dict(event_id="old-event", uid=UID, stream="synthetic-attribution-stream",
                type="排障", types=["排障"], topic_key=TOPIC, start_time=msg.sent_at.isoformat(),
                end_time=msg.sent_at.isoformat(), created_on=msg.sent_at.isoformat(), status="未知",
                severity="未知", summary=msg.content, evidence=[], updated_by=[]))])


def test_label_addition_and_retraction_preserve_event_identity_and_typed_attributes(tmp_path):
    service, store, provider, source, old, event_id = opened(tmp_path)
    added = message("resource", 2, "虚构工单 CASE-SYN-42 报告上传失败，同时申请把配额增加到1200")
    source.messages.append(added)
    fact = dict(value="1200", evidence_ids=[added.key], quote=added.content)
    provider.mapping[added.message_id] = candidate(added, "update", event_id, [old],
        types=["排障", "资源需求"],
        type_evidence={label: [dict(evidence_ids=[added.key], quote=added.content)]
                       for label in ("排障", "资源需求")},
        typed_attributes={"资源需求": {"目标总量与增减量": fact}})
    result = service.run_window(UID, window(2))
    assert result.updated == [event_id] and not result.created
    event = store.events[event_id]
    assert event.event_id == event_id and event.types == ["排障", "资源需求"]
    expected_fact = fact | {"evidence_refs": [dict(source=added.source, message_id=added.message_id,
        id=added.message_id, digest=added.digest, conversation_id=added.conversation_id,
        send_time=added.sent_at.isoformat(), quote=added.content)]}
    assert event.typed_attributes["资源需求"]["目标总量与增减量"] == expected_fact
    assert event.attributes["目标总量与增减量"] == "1200"

    unchanged = message("unchanged", 3, "虚构工单 CASE-SYN-42 的排障继续处理，没有更新资源申请")
    source.messages.append(unchanged)
    provider.mapping[unchanged.message_id] = candidate(unchanged, "update", event_id, [added],
        types=["排障"],
        type_evidence={"排障": [dict(evidence_ids=[unchanged.key], quote=unchanged.content)]})
    result = service.run_window(UID, window(3))
    assert result.updated == [event_id] and store.events[event_id].types == ["排障", "资源需求"]
    assert store.events[event_id].typed_attributes["资源需求"]["目标总量与增减量"] == expected_fact

    corrected = message("correction", 4, "更正：这次没有配额申请，先前增加配额的说法不适用")
    source.messages.append(corrected)
    provider.mapping[corrected.message_id] = candidate(corrected, "update", event_id, [unchanged],
        types=["排障"],
        type_evidence={"排障": [dict(evidence_ids=[corrected.key], quote=corrected.content)]},
        removed_types={"资源需求": [dict(evidence_ids=[corrected.key], quote=corrected.content)]})
    result = service.run_window(UID, window(4))
    assert result.updated == [event_id] and not result.created
    event = store.events[event_id]
    assert event.event_id == event_id and event.types == ["排障"]
    assert "资源需求" not in event.typed_attributes and "目标总量与增减量" not in event.attributes
    assert event.removed_types["资源需求"]


def test_grouping_late_and_current_feedback_keeps_newer_state(tmp_path):
    service, store, provider, source, old, event_id = opened(tmp_path)
    latest = message("latest", 10, "虚构工单正在验证方案")
    source.messages.append(latest)
    provider.mapping["latest"] = candidate(latest, "update", event_id, [old], status="虚构验证中")
    service.run_window(UID, window(10))
    late, current = message("late", 9, "虚构工单当时待排查"), message("current", 11, "虚构工单增加测试记录")
    source.messages += [late, current]
    provider.mapping.update(late=candidate(late, "update", event_id, [old], status="虚构待排查"),
                            current=candidate(current, "update", event_id, [old]))
    result = service.run_window(UID, window(11))
    assert result.updated == [event_id] and store.events[event_id].status == "虚构验证中"
    assert store.events[event_id].summary == current.content
