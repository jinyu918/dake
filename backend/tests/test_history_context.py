"""旧聊天补取的离线验收；所有客户、群、消息、事件和模型响应均为自行构造。"""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
import json
import socket
from uuid import UUID

import httpx
import pytest

from dake.analysis.attribution import AttributionOutput
from dake.analysis.contract import ContractError, parse_output
from dake.analysis.providers.xhub import XHubConfig, XHubError, XHubProvider
from dake.config import HistoryContextSettings, HistoryRetrievalSettings, Settings
from dake.domain.alert_policy import ALERT_SCHEMA
from dake.domain.models import Candidate, Event, Message, Window, parse_time
from dake.jobs.runner import prepare_request
from dake.repositories.mysql import MySQLStore
from dake.services.history_context import HistoryContextService
from dake.services.history_retrieval import HistoryRetriever
from dake.services.processing import IncrementalService
from fakes import FakeProvider, FakeSource, MemoryStore


UID, STREAM = "synthetic-context-customer", "synthetic-context-stream"


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    """禁止默认配置及真实连接，不能因测试装配失误读取真实数据。"""
    def forbidden(*args, **kwargs):
        raise RuntimeError("旧聊天测试禁止环境文件、网络或数据库连接")

    monkeypatch.setattr(Settings, "load", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("pymysql.connect", forbidden)
    monkeypatch.setattr("dake.config.dotenv_values", forbidden)
    monkeypatch.setattr("dake.analysis.providers.xhub.dotenv_values", forbidden)


def window(day):
    start = parse_time(f"2036-05-{day:02d}")
    return Window(start, start + timedelta(days=1))


def message(mid="101", day=1, content="虚构上传失败，正在排查", **changes):
    return replace(Message(UID, mid, window(day).start + timedelta(hours=9), content,
                           source="official_chat", conversation_id="synthetic-room-a", sender_id="synthetic-person"),
                   **changes)


def event(number=1, messages=None, **changes):
    messages = messages or [message()]
    start, end = min(m.sent_at for m in messages), max(m.sent_at for m in messages)
    value = Event(str(UUID(int=number)), UID, STREAM, "排障", "虚构上传", start, end,
                  "未知", "未知", "虚构上传失败", start.replace(hour=0), evidence=[
                      dict(source=m.source, message_id=m.message_id, send_time=m.sent_at.isoformat(),
                           digest=m.digest, excerpt=m.content[:160]) for m in messages])
    value = replace(value, **changes)
    snapshot = value.to_dict()
    snapshot.pop("updated_by")
    stop = end.replace(hour=0, minute=0, second=0) + timedelta(days=1)
    value.updated_by = [dict(window_start=start.replace(hour=0).isoformat(), window_end=stop.isoformat(),
                             message_ids=[m.message_id for m in messages], snapshot=snapshot)]
    return value


class ObservedStore(MemoryStore):
    """记录请求的消息版本及邻域；禁止补取退回全客户消息读取。"""

    def __init__(self, messages=(), events=()):
        super().__init__()
        self.messages = {m.key: deepcopy(m) for m in messages}
        self.events = {e.event_id: deepcopy(e) for e in events}
        self.message_reads, self.neighbor_reads = [], []

    def history_messages(self, uid, refs, before):
        self.message_reads.append((uid, deepcopy(refs), before))
        return super().history_messages(uid, refs, before)

    def history_neighbor_heads(self, uid, anchor, before, **kwargs):
        self.neighbor_reads.append((uid, anchor.key, before, kwargs))
        return super().history_neighbor_heads(uid, anchor, before, **kwargs)

    def context_messages(self, *args):
        raise AssertionError("按需补取不能读取全客户时间范围正文")


def retrieve(store, *, new=None, context=(), limits=None, retrieval_limits=None):
    new = new or message("new", 10, "虚构上传问题仍未解决")
    history = HistoryRetriever(store, retrieval_limits or HistoryRetrievalSettings()).retrieve(UID, STREAM, [new], window(10).end)
    result = HistoryContextService(store, limits or HistoryContextSettings()).retrieve(
        history, [new], list(context), store.list_events(UID, STREAM), {})
    return result, history, new


def text(result):
    return "".join(fragment["text"] for item in result.items for fragment in item["fragments"])


def test_ten_day_reference_loads_only_event_evidence_without_mutation():
    old = message()
    unrelated = [message(str(i), i, "虚构无关聊天") for i in range(2, 10)]
    store = ObservedStore([old, *unrelated], [event(messages=[old])])
    before = deepcopy((store.messages, store.events, store.processed))
    result, _, new = retrieve(store)
    assert [item["id"] for item in result.items] == [old.key]
    assert text(result) == old.content and new.content not in text(result)
    assert result.items[0]["conversation_id"] == old.conversation_id
    assert result.items[0]["digest"] == old.digest
    assert not store.neighbor_reads
    assert [[ref["message_id"] for ref in refs] for _, refs, _ in store.message_reads] == [[old.message_id]]
    assert (store.messages, store.events, store.processed) == before


def test_private_references_keep_opening_and_latest_evidence_under_existing_cap():
    old = [message(str(i), i) for i in range(1, 10)]
    store = ObservedStore(old, [event(messages=old)])
    result, history, _ = retrieve(store, retrieval_limits=HistoryRetrievalSettings(evidence_limit=2))
    assert {item["id"] for item in result.items} == {old[0].key, old[-1].key}
    assert history.candidates[0].evidence_limited
    assert "candidate_retrieval_incomplete" in result.reasons
    assert "excerpt" not in json.dumps(history.candidates[0].to_input())


def test_neighbor_is_same_group_reading_background_with_safe_version_basis():
    old = message(content="虚构上传失败，这个需要继续排查")
    neighbor = message("102", content="虚构排查细节", sent_at=old.sent_at + timedelta(minutes=1))
    other_group = replace(neighbor, message_id="103", conversation_id="synthetic-room-b")
    other_customer = replace(neighbor, uid="synthetic-other", message_id="104")
    proof = event(2, [neighbor], topic_key="独立事项", summary="另一件事项")
    store = ObservedStore([old, neighbor, other_group, other_customer], [event(messages=[old]), proof])
    result, history, _ = retrieve(store)
    assert len(history.candidates) == 1
    assert {item["id"] for item in result.items} == {old.key, neighbor.key}
    entry = next(item for item in result.items if item["id"] == neighbor.key)
    assert entry["links"] == [dict(event_id=event().event_id, kind="neighbor", anchor_key=old.key)]
    assert entry["availability"] == "full"


@pytest.mark.parametrize("proof_kind", ["none", "other_stream", "future_revision", "edited"])
def test_unverifiable_neighbors_never_load_current_body(proof_kind):
    old = message(content="虚构上传失败，这个需要排查")
    neighbor = message("102", content="不得泄漏的虚构后改正文", sent_at=old.sent_at + timedelta(minutes=1))
    proof = event(2, [neighbor], topic_key="独立事项", summary="独立事项")
    if proof_kind == "other_stream":
        proof = event(2, [neighbor], stream="synthetic-other-stream", topic_key="独立事项", summary="独立事项")
    if proof_kind == "future_revision":
        proof.updated_by.append(dict(window_end=window(11).end.isoformat(), snapshot=proof.to_dict()))
    if proof_kind == "edited":
        proof.updated_by[-1]["snapshot"]["evidence"][0]["digest"] = "older-version"
    store = ObservedStore([old, neighbor], [event(messages=[old])] + ([] if proof_kind == "none" else [proof]))
    store.processed[(neighbor.key, STREAM)] = neighbor.digest
    result, _, _ = retrieve(store)
    assert neighbor.content not in text(result)
    assert "neighbor_version_unavailable" in result.reasons
    assert all(ref["message_id"] != neighbor.message_id for _, refs, _ in store.message_reads for ref in refs)


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_edited_evidence_uses_only_saved_excerpt(missing):
    old = message(content="旧版本的虚构报告")
    changed = replace(old, content="后来编辑的虚构结论", conversation_id="later-room")
    store = ObservedStore([] if missing else [changed], [event(messages=[old])])
    result, _, _ = retrieve(store)
    assert text(result) == old.content
    assert result.items[0]["availability"] == "excerpt_only"
    assert result.items[0]["conversation_id"] is None
    assert result.items[0]["original_chars"] is None
    assert "evidence_version_unavailable" in result.reasons
    assert not store.neighbor_reads


def test_multiple_candidates_share_body_but_keep_separate_links():
    old = message()
    store = ObservedStore([old], [event(1, [old]), event(2, [old])])
    result, history, _ = retrieve(store)
    assert len(history.candidates) == 2 and len(result.items) == 1
    assert {link["event_id"] for link in result.items[0]["links"]} == {e.event.event_id for e in history.candidates}
    assert not any(key in result.to_input() for key in ("decision", "summary", "status"))


def test_candidate_round_robin_keeps_alternatives_when_message_budget_is_small():
    first = [message(f"a{i}", 1) for i in range(4)]
    second = message("b", 2)
    store = ObservedStore([*first, second], [event(1, first), event(2, [second])])
    result, _, _ = retrieve(store, limits=HistoryContextSettings(max_messages=2))
    assert len(result.items) == 2
    assert {link["event_id"] for item in result.items for link in item["links"]} == {event(1).event_id, event(2).event_id}
    assert result.omitted and "message_budget" in result.reasons


def test_long_message_keeps_offsets_and_total_serialized_budget():
    old = message(content="虚构长内容" * 2000 + "虚构末尾说明")
    store = ObservedStore([old], [event(messages=[old])])
    limits = HistoryContextSettings(message_chars=40, max_chars=1024)
    result, _, _ = retrieve(store, limits=limits)
    assert result.items and result.chars() <= limits.max_chars
    item = result.items[0]
    assert item["truncated"] and sum(len(f["text"]) for f in item["fragments"]) == 40
    assert all(old.content[f["start"]:f["end"]] == f["text"] for f in item["fragments"])
    assert item["fragments"][-1]["end"] == len(old.content)
    assert store.messages[old.key].digest == old.digest


def test_existing_input_is_referenced_without_repeating_its_body():
    old = message()
    result, _, _ = retrieve(ObservedStore([old], [event(messages=[old])]), context=[old])
    assert result.items[0]["availability"] == "already_in_input"
    assert result.items[0]["input_role"] == "context_messages"
    assert not text(result)


def test_new_edit_and_historical_version_remain_separate():
    old = message()
    changed = replace(old, content="虚构上传的新修订")
    result, _, new = retrieve(ObservedStore([changed], [event(messages=[old])]), new=changed)
    assert result.input_versions == ((changed.key, changed.digest),)
    assert result.items[0]["digest"] == old.digest != new.digest
    assert text(result) == old.content


def test_zero_candidates_does_not_read_messages_or_decide_new():
    store = ObservedStore([message()])
    result, _, _ = retrieve(store)
    assert result.items == [] and result.reasons == {"no_candidate"}
    assert not store.message_reads and not store.neighbor_reads


@pytest.mark.parametrize("violation", ["customer", "stream", "stale_batch", "future_context"])
def test_scope_and_input_binding_are_checked_before_reading(violation):
    store = ObservedStore([message()], [event()])
    _, history, new = retrieve(store)
    store.message_reads.clear()
    context, events = [], store.list_events(UID, STREAM)
    if violation == "customer":
        new = replace(new, uid="synthetic-other")
    elif violation == "stream":
        events[0].stream = "synthetic-other-stream"
    elif violation == "stale_batch":
        new = replace(new, content="改变过的虚构输入")
    else:
        context = [message("future", 11)]
    with pytest.raises(ValueError):
        HistoryContextService(store, HistoryContextSettings()).retrieve(history, [new], context, events, {})
    assert not store.message_reads


def test_future_event_conclusion_never_enters_context_reads():
    old = event()
    old.updated_by.append(dict(window_end=window(11).end.isoformat(), snapshot=old.to_dict()))
    store = ObservedStore([message()], [old])
    result, history, _ = retrieve(store)
    assert history.unsafe_skipped == 1 and not result.items and not store.message_reads


def test_neighbor_upper_bound_excludes_next_window_message():
    old = message(day=10, content="虚构上传，这个需要排查", sent_at=window(10).end - timedelta(minutes=1))
    future = message("future", day=11, sent_at=window(10).end)
    store = ObservedStore([old, future], [event(messages=[old])])
    result, _, _ = retrieve(store)
    assert future.key not in {item["id"] for item in result.items}


def test_sql_reads_are_scoped_versioned_and_bounded_without_connecting():
    store = object.__new__(MySQLStore)
    calls = []
    store._execute = lambda sql, args: (calls.append((sql, args)) or ([], 0))
    old = message()
    ref = event(messages=[old]).evidence[0]
    store.history_messages(UID, [ref], window(10).end)
    store.history_neighbor_heads(UID, old, window(10).end, span=timedelta(minutes=10), limit=3)
    sql, args = calls[0]
    assert "uid=%s" in sql and "digest=%s" in sql and "sent_at < %s" in sql and "LIMIT %s" in sql
    assert args[0] == UID and old.digest in args and args[-1] == 1
    sql, args = calls[1]
    assert "SELECT source,message_id,sent_at,digest" in sql and "SELECT payload" not in sql
    assert "conversation_id" in sql and "source=%s" in sql and args[-1] == 3
    assert args[:3] == (UID, old.source, old.conversation_id)


class ContextProvider(FakeProvider):
    """只记录新增的背景通道，输出仍由本轮合成消息决定。"""

    def __init__(self, mapping=None):
        super().__init__(mapping, STREAM)
        self.backgrounds = []

    def configure_history_context(self, result):
        self.backgrounds.append(deepcopy(result))


def build_service(tmp_path):
    old = message()
    settings = Settings(uid=UID, start=window(1).start, end=window(12).end, data_dir=tmp_path)
    provider = ContextProvider({old.message_id: Candidate("排障", "虚构上传", "虚构上传失败", [old.key])})
    store, source = MemoryStore(), FakeSource([old])
    service = IncrementalService(store, source, provider, settings)
    service.run_window(UID, window(1))
    return service, store, source, provider, old


def test_pipeline_keeps_old_chat_read_only_and_repeated_batch_is_idempotent(tmp_path):
    service, store, source, provider, old = build_service(tmp_path)
    before = deepcopy(store.events)
    new = message("new", 10)
    source.messages.append(new)
    result = service.run_window(UID, window(10))
    assert result.processed == 1 and result.created == result.updated == []
    assert store.events == before and store.processed[(old.key, STREAM)] == old.digest
    assert provider.calls[-1][0] == [new] and provider.calls[-1][1] == []
    assert old.content in text(provider.backgrounds[-1])
    calls = len(provider.backgrounds)
    assert service.run_window(UID, window(10)).processed == 0
    assert len(provider.backgrounds) == calls


@pytest.mark.parametrize("failure", ["read", "history_as_writable_evidence"])
def test_failed_read_or_promoting_background_rolls_back(tmp_path, monkeypatch, failure):
    service, store, source, provider, old = build_service(tmp_path)
    before = deepcopy((store.messages, store.events, store.processed))
    watermark = deepcopy(service.state.read(UID, STREAM))
    new = message("new", 10)
    source.messages.append(new)
    if failure == "read":
        def failed(*args):
            raise RuntimeError("虚构读取失败")
        monkeypatch.setattr(store, "history_messages", failed)
    else:
        provider.mapping[new.message_id] = Candidate("排障", "虚构上传", "虚构更新", [new.key, old.key])
    with pytest.raises((ValueError, RuntimeError)):
        service.run_window(UID, window(10))
    assert (store.messages, store.events, store.processed) == before
    assert service.state.read(UID, STREAM) == watermark


def pure_provider(settings):
    """只构造输入，不创建 HTTP 客户端，不读取密钥。"""
    provider = object.__new__(XHubProvider)
    provider.settings, provider.name = replace(settings, provider="xhub"), STREAM
    provider.attribution_model = AttributionOutput
    provider.sender_kinds, provider.history_retrieval, provider.history_context = {}, None, None
    return provider


def test_provider_has_separate_background_and_contract_cannot_promote_it(tmp_path):
    old = message()
    result, history, new = retrieve(ObservedStore([old], [event(messages=[old])]))
    provider = pure_provider(Settings(uid=UID, data_dir=tmp_path))
    provider.configure_history_retrieval(history)
    provider.configure_history_context(result)
    payload = provider._input([new], [], history.events)
    assert payload["history_context"] == result.to_input()
    assert payload["context_messages"] == []
    candidate = dict(type="排障", topic_key="虚构上传", summary="虚构进展", evidence_ids=[new.key, old.key],
                     status=None, severity=None, assignees=[], attributes=[], extensions=[], decision="update",
                     referenced_event_id=history.events[0].event_id, decision_reason="虚构引用")
    with pytest.raises(ContractError):
        parse_output(json.dumps(dict(schema_version="1.0", candidates=[candidate])), [new], [], history.events)
    candidate["evidence_ids"] = [new.key]
    assert parse_output(json.dumps(dict(schema_version="1.0", candidates=[candidate])), [new], [], history.events)


def test_provider_rejects_stale_or_cross_stream_background(tmp_path):
    result, history, new = retrieve(ObservedStore([message()], [event()]))
    provider = pure_provider(Settings(uid=UID, data_dir=tmp_path))
    with pytest.raises(XHubError):
        provider.configure_history_context(replace(result, stream="synthetic-other"))
    provider.configure_history_retrieval(history)
    provider.configure_history_context(result)
    with pytest.raises(XHubError):
        provider._input([new], [message("extra", 2)], history.events)
    provider.configure_history_retrieval(history)
    assert provider.history_context is None


@pytest.mark.parametrize("violation", ["future_message", "other_source", "other_event"])
def test_provider_rechecks_background_time_source_and_links(tmp_path, violation):
    result, history, new = retrieve(ObservedStore([message()], [event()]))
    if violation == "future_message":
        result.items[0]["send_time"] = window(11).start.isoformat()
    elif violation == "other_source":
        result.items[0]["source"] = "synthetic-other-source"
    else:
        result.items[0]["links"][0]["event_id"] = str(UUID(int=999))
    provider = pure_provider(Settings(uid=UID, data_dir=tmp_path))
    provider.configure_history_retrieval(history)
    provider.configure_history_context(result)
    with pytest.raises(XHubError):
        provider._input([new], [], history.events)


def test_formal_provider_receives_background_through_shared_service_with_mock_http(tmp_path):
    settings = Settings(uid=UID, start=window(10).start, end=window(11).end, data_dir=tmp_path, provider="xhub")
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(dict(schema_version="4.0", candidates=[]))}}]})

    provider = XHubProvider(XHubConfig(model="synthetic-model", api_key="synthetic-placeholder"), settings,
                            transport=httpx.MockTransport(respond))
    old, new = message(), message("new", 10)
    store = MemoryStore()
    store.messages[old.key] = old
    old_event = event(messages=[old], stream=provider.name)
    store.events[old_event.event_id] = deepcopy(old_event)
    service = IncrementalService(store, FakeSource([new]), provider, settings)
    try:
        result = service.run_window(UID, window(10))
        payload = json.loads(calls[0]["messages"][1]["content"])
        assert payload["history_context"]["items"][0]["id"] == old.key
        assert payload["context_messages"] == []
        assert [item["id"] for item in payload["new_messages"]] == [new.key]
        assert result.processed == 1 and result.created == result.updated == []
        assert store.events[old_event.event_id] == old_event
        assert (old.key, provider.name) not in store.processed
    finally:
        provider.close()


@pytest.mark.parametrize("cite_alert", [False, True])
def test_mixed_window_structured_alerts_stay_out_of_xhub(tmp_path, monkeypatch, cite_alert):
    settings = Settings(uid=UID, data_dir=tmp_path, provider="xhub", synthetic_alert_source="local-fixture",
                        start=window(1).start, end=window(11).end)
    old = message(content="虚构上传失败，这个需要排查")
    new = message("new", 10, "虚构上传仍然失败")

    def alert(mid, sent_at, kind):
        return message(mid, content=json.dumps(dict(schema=ALERT_SCHEMA, source="local-fixture", alert_id=mid,
                       round_id="fixture", kind=kind, severity="warning")),
                       sender_id="synthetic-bot", sent_at=sent_at)

    cited = alert("old-cited-alert", old.sent_at + timedelta(minutes=1), "trigger")
    neighbor = alert("old-neighbor-alert", old.sent_at + timedelta(minutes=2), "recovery")
    previous = alert("recent-recovery", new.sent_at - timedelta(days=1), "recovery")
    incoming = alert("mixed-recovery", new.sent_at, "recovery")
    calls = []

    def respond(req):
        body = json.loads(req.content)
        calls.append(json.loads(body["messages"][1]["content"]))
        from test_attribution import model_output

        output = model_output(new, decision="update", referenced_event_id=original.event_id,
                              basis=[dict(message_id=incoming.key, quote=incoming.content)],
                              evidence_ids=[new.key, incoming.key]) if cite_alert else json.dumps(
                                  dict(schema_version="4.0", candidates=[]))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": output}}]})

    provider = XHubProvider(XHubConfig(model="synthetic-model", api_key="synthetic-placeholder"), settings,
                            transport=httpx.MockTransport(respond))
    original = event(messages=[old, cited], stream=provider.name)
    proof = event(2, [neighbor], stream=provider.name, topic_key="独立事项", summary="另一件事项")
    store = MemoryStore()
    delivered = []
    monkeypatch.setattr(store, "save_alert_delivery", lambda delivery, _: delivered.append(delivery), raising=False)
    monkeypatch.setattr("dake.services.processing.load_sender_kinds", lambda _: {
        (UID, incoming.source, incoming.sender_id, incoming.conversation_id): "bot"})
    store.messages.update({m.key: m for m in (old, cited, neighbor, previous)})
    for m in (old, cited, neighbor, previous):
        store.processed[(m.key, provider.name)] = m.digest
    store.events.update({e.event_id: deepcopy(e) for e in (original, proof)})
    service = IncrementalService(store, FakeSource([new, incoming]), provider, settings)
    try:
        if cite_alert:
            with pytest.raises(XHubError, match="证据|引文|引用"):
                service.run_window(UID, window(10))
            assert not delivered
        else:
            result = service.run_window(UID, window(10))
            assert result.processed == 2 and result.updated == []
            assert [d["message_id"] for d in delivered] == [incoming.key]
        payload = calls[0]
        assert [m["id"] for m in payload["new_messages"]] == [new.key]
        assert payload["context_messages"] == []
        assert [m["id"] for m in payload["history_context"]["items"]] == [old.key]
        assert ALERT_SCHEMA not in json.dumps(payload)
        assert store.events[original.event_id] == original
    finally:
        provider.close()


def test_confirmed_bot_context_retains_identity_without_becoming_new_input():
    old = message(sender_id="synthetic-bot")
    store = ObservedStore([old], [event(messages=[old])])
    _, history, new = retrieve(store)
    kinds = {(UID, old.source, old.sender_id, old.conversation_id): "bot"}
    result = HistoryContextService(store, HistoryContextSettings()).retrieve(
        history, [new], [], store.list_events(UID, STREAM), kinds)
    assert result.items[0]["sender_kind"] == "bot"
    assert result.input_versions == ((new.key, new.digest),)
    assert not store.processed


def test_pure_bot_batch_does_not_invoke_context_service(tmp_path, monkeypatch):
    service, store, source, provider, _ = build_service(tmp_path)
    notice = message("notice", 10, sender_id="synthetic-bot")
    source.messages.append(notice)
    monkeypatch.setattr("dake.services.processing.load_sender_kinds", lambda _: {
        (UID, notice.source, notice.sender_id, notice.conversation_id): "bot"})
    calls = len(provider.backgrounds)
    result = service.run_window(UID, window(10))
    assert result.processed == 1 and result.created == result.updated == []
    assert len(provider.backgrounds) == calls


def test_total_input_budget_fails_before_mock_request_without_dropping_new_text(tmp_path):
    settings = Settings(uid=UID, data_dir=tmp_path, provider="xhub")
    calls = []
    provider = XHubProvider(XHubConfig(model="synthetic-model", api_key="synthetic-placeholder"), settings,
                            transport=httpx.MockTransport(lambda req: calls.append(req)))
    oversized = message(content="虚构新消息" * settings.history_context.max_input_chars)
    try:
        with pytest.raises(XHubError, match="总输入"):
            provider.extract([oversized], [], [])
        assert not calls
    finally:
        provider.close()


def test_task_freezes_context_limits_and_keeps_analysis_stream(tmp_path):
    from test_runs import execute_next, run_fixture

    settings, repository, runner, _, _, _, _, _, seen = run_fixture(tmp_path, days=1)
    selected = replace(settings, history_context=HistoryContextSettings(max_messages=5))
    request, total = prepare_request(selected, {"interval": "1d"})
    assert request["history_context"] == asdict(selected.history_context)
    runner.settings = replace(settings, history_context=HistoryContextSettings(max_messages=9))
    run = execute_next(repository, runner, request, total)
    assert run["status"] == "succeeded" and seen[-1].history_context.max_messages == 5
    assert request["analysis_stream"] == prepare_request(settings, {"interval": "1d"})[0]["analysis_stream"]


@pytest.mark.parametrize("values", [{"max_messages": 0}, {"neighbor_messages": -1}, {"message_chars": 1},
                                    {"max_chars": 1023}, {"max_input_chars": 100}, {"neighbor_minutes": True}])
def test_invalid_context_resource_limits_are_rejected(values):
    with pytest.raises(ValueError):
        HistoryContextSettings(**values)
