"""历史候选的离线验收；客户、群、消息和事项全部为本文件新造的虚构数据。"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import socket
from uuid import UUID

import pytest

from dake.analysis.attribution import AttributionOutput
from dake.analysis.contract import ContractError, parse_output
from dake.analysis.providers.xhub import XHubError, XHubProvider
from dake.config import HistoryRetrievalSettings, Settings
from dake.domain.models import Candidate, Event, Message, Window, parse_time
from dake.repositories.mysql import MySQLStore
from dake.services.history_retrieval import HistoryRetriever
from dake.services.processing import IncrementalService
from fakes import FakeProvider, FakeSource, MemoryStore


UID = "synthetic-retrieval-customer"
STREAM = "synthetic-retrieval-stream-v1"
SOURCE = "synthetic_retrieval"


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    """所有用例显式注入配置；即使误走真实连接路径也立即失败。"""
    def forbidden(*args, **kwargs):
        raise RuntimeError("离线检索验收禁止默认配置、网络或数据库连接")

    monkeypatch.setattr(Settings, "load", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr("pymysql.connect", forbidden)
    monkeypatch.setattr("dotenv.dotenv_values", forbidden)
    monkeypatch.setattr("dake.config.dotenv_values", forbidden)
    monkeypatch.setattr("dake.analysis.providers.xhub.dotenv_values", forbidden)


def window(day):
    start = parse_time(f"2032-05-{day:02d}")
    return Window(start, start + timedelta(days=1))


def message(mid="new-human", day=12, content="星舟网关仍然超时", **changes):
    return replace(Message(UID, mid, window(day).start + timedelta(hours=9), content,
                           source=SOURCE, conversation_id="synthetic-retrieval-room",
                           sender_id="synthetic-retrieval-person"), **changes)


def event(number=1, day=1, **changes):
    item = message(f"old-evidence-{number}", day)
    value = Event(str(UUID(int=number)), UID, STREAM, "排障", "星舟网关/连接超时",
                  item.sent_at, item.sent_at, "虚构待排查", "未知", "星舟网关连接超时", window(day).start,
                  evidence=[dict(source=item.source, message_id=item.message_id,
                                 send_time=item.sent_at.isoformat(), excerpt="虚构证据不应进入候选", digest=item.digest)])
    value = replace(value, **changes)
    snapshot = value.to_dict()
    snapshot.pop("updated_by")
    value.updated_by = [dict(action="created", window_start=window(day).start.isoformat(),
                             window_end=window(day).end.isoformat(), message_ids=[item.message_id], snapshot=snapshot)]
    return value


class ObservedStore(MemoryStore):
    """记录有界查询，不允许检索器偷用全客户事件读取或历史消息回取。"""

    def __init__(self, *events):
        super().__init__()
        self.events = {item.event_id: deepcopy(item) for item in events}
        self.head_calls, self.snapshot_calls = [], []

    def history_event_heads(self, uid, stream, before, **kwargs):
        self.head_calls.append((uid, stream, before, deepcopy(kwargs)))
        return super().history_event_heads(uid, stream, before, **kwargs)

    def history_event_snapshots(self, uid, stream, heads):
        self.snapshot_calls.append((uid, stream, deepcopy(heads)))
        return super().history_event_snapshots(uid, stream, heads)

    def list_events(self, *args):
        raise AssertionError("候选检索不能通过全量事件列表取候选")

    def context_messages(self, *args):
        raise AssertionError("候选检索不能回取旧聊天")


def retrieve(store, item=None, limits=None, *, before=None):
    item = item or message()
    return HistoryRetriever(store, limits or HistoryRetrievalSettings()).retrieve(
        UID, STREAM, [item], before or window(12).end)


def build(tmp_path, messages, mapping=None):
    settings = Settings(uid=UID, data_dir=tmp_path, start=window(1).start, end=window(15).end,
                        interval=timedelta(days=1), provider="stub")
    store, source, provider = MemoryStore(), FakeSource(messages), FakeProvider(mapping, STREAM)
    return IncrementalService(store, source, provider, settings), store, source, provider


@pytest.mark.parametrize("distance", [3, 10])
def test_sequential_batches_retrieve_three_and_ten_day_old_events(tmp_path, distance):
    old, new = message("opening", 1), message("follow", 1 + distance)
    service, store, _, provider = build(tmp_path, [old, new], {
        old.message_id: Candidate("排障", "星舟网关/连接超时", "星舟网关连接超时", [old.key]),
    })
    first = service.run_window(UID, window(1))
    original = deepcopy(store.events)
    service.run_window(UID, window(1 + distance))
    new_input, context, historical = provider.calls[-1]
    assert new_input == [new]
    assert historical[0].event_id == first.created[0]
    assert historical[0].end_time == old.sent_at
    assert not historical[0].updated_by
    assert all("excerpt" not in item for item in historical[0].evidence)
    if distance == 10:
        assert context == []
    assert store.events == original


def test_zero_and_multiple_hits_do_not_decide_or_write():
    store = ObservedStore(event(1, 1), event(2, 9))
    original = deepcopy(store.events)
    none = retrieve(store, message(content="橙鹿票据核验"))
    assert none.candidates == [] and not none.metadata()["incomplete"]
    many = retrieve(store)
    assert len(many.candidates) == 2
    assert many.candidates[0].event.event_id == str(UUID(int=2))
    assert all(any(reason.startswith("keyword:") for reason in c.hit_reasons) for c in many.candidates)
    assert store.events == original and not store.processed and not store.messages


def test_explicit_event_id_has_a_separate_bounded_lookup():
    old = event(1, 1)
    store = ObservedStore(old, *[event(number, 3) for number in range(2, 8)])
    limits = HistoryRetrievalSettings(page_size=1, recent_pages=1, older_pages=1)
    result = retrieve(store, message(content=f"event_id={old.event_id}"), limits)
    assert [candidate.event.event_id for candidate in result.candidates] == [old.event_id]
    assert "exact_event_id" in result.candidates[0].hit_reasons
    assert result.scanned == 2 and result.scan_limited
    assert store.head_calls[0][3]["event_ids"] == [old.event_id]
    assert store.head_calls[0][3]["limit"] == limits.exact_limit


def test_full_message_reference_is_exact_only_inside_scanned_evidence():
    old = event(1, 1)
    other = event(2, 1)
    other.evidence[0].update(source="synthetic_other", message_id=old.evidence[0]["message_id"])
    other.updated_by[0]["snapshot"]["evidence"] = deepcopy(other.evidence)
    store = ObservedStore(old, other)
    result = retrieve(store, message(content=f"message_id={SOURCE}:old-evidence-1"))
    assert [c.event.event_id for c in result.candidates] == [old.event_id]
    assert "exact_message_key" in result.candidates[0].hit_reasons
    assert retrieve(store, message(content="123456789")).candidates == []


def test_request_number_is_only_a_keyword_not_an_authoritative_identifier():
    store = ObservedStore(event(1, 1, summary="星舟网关 虚构请求 TICKET-A"),
                          event(2, 2, summary="星舟网关 虚构请求 TICKET-B"))
    result = retrieve(store, message(content="星舟网关 request_id=TICKET-A"))
    assert len(result.candidates) == 2
    assert not any(reason.startswith("exact_") for c in result.candidates for reason in c.hit_reasons)


def test_customer_and_stream_are_filtered_before_snapshot_reads():
    mine = event(1, 1)
    store = ObservedStore(mine, event(2, 1, uid="synthetic-other-customer"),
                          event(3, 1, stream="synthetic-retrieval-stream-v2"))
    result = retrieve(store, message(content=f"星舟网关 event_id={str(UUID(int=3))}"))
    assert [c.event.event_id for c in result.candidates] == [mine.event_id]
    assert all(call[:2] == (UID, STREAM) for call in store.head_calls + store.snapshot_calls)
    assert {h["event_id"] for _, _, heads in store.snapshot_calls for h in heads} == {mine.event_id}


def test_closed_old_candidate_is_not_excluded():
    old = event(1, 1, status="虚构已关闭", closed_at=window(1).start + timedelta(hours=10))
    result = retrieve(ObservedStore(old))
    assert result.candidates[0].event.closed_at == old.closed_at
    assert result.candidates[0].event.status == old.status
    assert "older" in result.candidates[0].hit_reasons


def test_future_revision_and_missing_history_are_rejected_before_snapshot_read():
    future = event(1, 1)
    future.summary = "未来才知道的虚构修复结论"
    future.updated_by.append(dict(window_end=window(14).end.isoformat(), snapshot=future.to_dict()))
    missing = event(2, 1, updated_by=[])
    missing.updated_by = []
    store = ObservedStore(future, missing, event(3, 14))
    result = retrieve(store)
    assert result.candidates == [] and result.unsafe_skipped == 2
    assert store.snapshot_calls == [] and result.metadata()["incomplete"]


def test_current_projection_is_not_used_as_a_historical_summary():
    old = event(1, 1)
    old.summary = "未来投影中的橙鹿票据"
    store = ObservedStore(old)
    result = retrieve(store)
    assert result.candidates[0].event.summary == "星舟网关连接超时"
    assert retrieve(store, message(content="橙鹿票据")).candidates == []


@pytest.mark.parametrize("invalid", ["evidence", "end", "created", "revision"])
def test_invalid_or_future_snapshot_is_not_used_for_keyword_matches(invalid):
    old = event(1, 1)
    snapshot = old.updated_by[0]["snapshot"]
    future = window(13).start.isoformat()
    if invalid == "evidence":
        snapshot["evidence"][0]["send_time"] = future
    elif invalid == "end":
        snapshot["end_time"] = future
    elif invalid == "created":
        snapshot["created_on"] = future
    else:
        old.updated_by[0]["window_end"] = "invalid-time"
    result = retrieve(ObservedStore(old))
    assert result.candidates == [] and result.unsafe_skipped == 1


def test_snapshot_changed_between_reads_is_incomplete_without_fallback(monkeypatch):
    store = ObservedStore(event(1, 1))
    original = store.history_event_snapshots

    def race(uid, stream, heads):
        store.events[str(UUID(int=1))].updated_by.append(dict(window_end=window(14).end.isoformat()))
        return original(uid, stream, heads)

    monkeypatch.setattr(store, "history_event_snapshots", race)
    result = retrieve(store)
    assert not result.candidates and result.snapshot_unavailable == 1 and result.metadata()["incomplete"]


def test_recent_and_older_scan_budgets_are_separate_from_model_budget():
    store = ObservedStore(*[event(i, 10 if i <= 5 else 1) for i in range(1, 11)])
    limits = HistoryRetrievalSettings(page_size=2, recent_pages=1, older_pages=1, max_candidates=1)
    result = retrieve(store, limits=limits)
    assert result.scanned == 4 and result.pages == 2
    assert result.scan_limited and result.context_limited and len(result.candidates) == 1
    assert sum(len(heads) for _, _, heads in store.snapshot_calls) == 4
    assert len(store.head_calls) == 2


def test_keyset_pagination_keeps_distinct_ids_at_the_same_time():
    store = ObservedStore(*[event(i, 10) for i in range(1, 6)])
    result = retrieve(store, limits=HistoryRetrievalSettings(page_size=2, recent_pages=3))
    assert len(result.candidates) == 5 and len({c.event.event_id for c in result.candidates}) == 5
    assert result.scanned == 5 and not result.scan_limited


def test_character_budget_counts_serialized_candidates_and_does_not_change_summaries():
    store = ObservedStore(event(1, 1))
    complete = retrieve(store)
    size = len(json.dumps([c.to_input() for c in complete.candidates], ensure_ascii=False))
    exact = retrieve(store, limits=HistoryRetrievalSettings(max_chars=size))
    assert len(exact.candidates) == 1 and exact.context_chars == size
    too_small = retrieve(store, limits=HistoryRetrievalSettings(max_chars=size - 1))
    assert too_small.candidates == [] and too_small.context_limited
    assert store.events[str(UUID(int=1))].summary == "星舟网关连接超时"


def test_exact_reference_and_evidence_caps_are_reported():
    old = event(1, 1)
    old.evidence.append({**old.evidence[0], "message_id": "another-synthetic-evidence"})
    old.updated_by[0]["snapshot"]["evidence"] = deepcopy(old.evidence)
    content = f"event_id={old.event_id} event_id={str(UUID(int=2))}"
    result = retrieve(ObservedStore(old), message(content=content),
                      HistoryRetrievalSettings(exact_limit=1, evidence_limit=1))
    assert result.clues_limited and result.metadata()["incomplete"]
    assert len(result.candidates[0].evidence_ids) == 1 and result.candidates[0].evidence_limited


def test_referenced_evidence_keeps_its_slot_and_repeated_versions_do_not_expand_it():
    old = event(1, 1)
    reference = {**old.evidence[0], "message_id": "explicit-evidence"}
    old.evidence.extend([reference, {**reference, "digest": "synthetic-revised-digest"}])
    old.updated_by[0]["snapshot"]["evidence"] = deepcopy(old.evidence)
    result = retrieve(ObservedStore(old), message(content=f"message_id={SOURCE}:explicit-evidence"),
                      HistoryRetrievalSettings(evidence_limit=1))
    assert result.candidates[0].evidence_ids == (f"{SOURCE}:explicit-evidence",)
    assert len(result.events[0].evidence) == 1 and result.candidates[0].evidence_limited


def test_oversized_candidate_does_not_hide_a_smaller_candidate():
    oversized = event(1, 10, summary="星舟网关" * 1000)
    smaller = event(2, 9)
    result = retrieve(ObservedStore(oversized, smaller), limits=HistoryRetrievalSettings(max_chars=1000))
    assert [c.event.event_id for c in result.candidates] == [smaller.event_id]
    assert result.context_limited


def test_keyword_cap_is_explicit_and_does_not_claim_full_recall():
    result = retrieve(ObservedStore(event(1, 1)), message(content="橙鹿票据 星舟网关"),
                      HistoryRetrievalSettings(keyword_limit=1))
    assert result.clues_limited and result.metadata()["incomplete"] and not result.candidates


@pytest.mark.parametrize("old_text,new_text,hit", [
    ("星舟网关连接超时", "星舟网关仍然超时", True),
    ("卡死", "无响应", False),
    ("北京大学", "北京大桥", True),
    ("FICTIONAL-GATEWAY timeout", "fictional-gateway retry", True),
])
def test_keyword_behavior_records_chinese_bigram_and_synonym_limits(old_text, new_text, hit):
    store = ObservedStore(event(1, 1, topic_key=old_text, summary=old_text))
    result = retrieve(store, message(content=new_text))
    assert bool(result.candidates) is hit


def test_bot_only_batch_does_not_enter_retrieval_or_provider(tmp_path, monkeypatch):
    item = message("notice", 1, sender_id="synthetic-retrieval-bot")
    path = tmp_path / "config" / "sender_kinds.json"
    path.parent.mkdir()
    path.write_text(json.dumps(dict(accounts=[dict(uid=UID, source=SOURCE, sender_id=item.sender_id,
                                                  kind="bot", basis="本用例虚构配置")])))
    service, store, _, provider = build(tmp_path, [item])

    def forbidden(*args, **kwargs):
        raise AssertionError("纯机器人批次不应触发候选检索")

    monkeypatch.setattr(service.history_retriever, "retrieve", forbidden)
    assert service.run_window(UID, window(1)).processed == 1
    assert service.run_window(UID, window(1)).processed == 0
    assert not provider.calls and not store.events


def test_repeated_context_late_and_changed_messages_keep_consumption_roles(tmp_path):
    old, recent = message("opening", 1), message("recent", 10)
    service, store, source, provider = build(tmp_path, [old, recent], {
        old.message_id: Candidate("排障", "星舟网关/连接超时", "星舟网关连接超时", [old.key]),
    })
    service.run_window(UID, window(1))
    service.run_window(UID, window(10))
    late = message("late", 2)
    source.messages.append(late)
    # 超出回拉范围且未入库的消息仍会漏掉；检索不能替代摄入补漏。
    assert service.run_window(UID, window(11)).processed == 0
    assert late.key not in store.messages
    with store.transaction(UID):
        store.upsert_messages([late])
    assert service.run_window(UID, window(12)).processed == 1
    pending, context, historical = provider.calls[-1]
    assert pending == [late] and context == [recent]
    assert historical and not any(m.key == old.key for m in pending + context)
    calls = len(provider.calls)
    assert service.run_window(UID, window(12)).processed == 0
    assert len(provider.calls) == calls
    changed = replace(recent, content="星舟网关的虚构修订内容")
    source.messages[source.messages.index(recent)] = changed
    assert service.run_window(UID, window(12)).processed == 1
    assert provider.calls[-1][0] == [changed]
    assert service.run_window(UID, window(12)).processed == 0


def pure_xhub(settings):
    """仅复用输入构造函数，不执行构造器、不创建 HTTP 客户端、不配置凭据。"""
    provider = object.__new__(XHubProvider)
    provider.settings = replace(settings, provider="xhub")
    provider.attribution_model = AttributionOutput
    provider.name, provider.sender_kinds, provider.history_retrieval = STREAM, {}, None
    return provider


def test_model_input_keeps_old_candidates_and_contract_rejects_background_evidence(tmp_path):
    old = event(1, 1, closed_at=window(1).end)
    new = message(source="official_chat")
    result = retrieve(ObservedStore(old), new)
    provider = pure_xhub(Settings(uid=UID, data_dir=tmp_path))
    provider.configure_history_retrieval(result)
    payload = provider._input([new], [], result.events)
    assert payload["existing_events"] == [result.candidates[0].to_input()]
    assert payload["history_retrieval"]["context_chars"] <= provider.settings.history_retrieval.max_chars
    assert payload["existing_events"][0]["evidence_ids"] == [f"{SOURCE}:old-evidence-1"]
    assert "excerpt" not in json.dumps(payload, ensure_ascii=False)
    output = dict(schema_version="1.0", candidates=[dict(
        type="排障", topic_key=old.topic_key, summary="虚构新增进展", evidence_ids=[new.key],
        status=None, severity=None, assignees=[], attributes=[], extensions=[], decision="update",
        referenced_event_id=old.event_id, decision_reason="引用只读候选，最终仍须现有匹配器判断")])
    assert parse_output(json.dumps(output), [new], [], result.events)[0].referenced_event_id == old.event_id
    output["candidates"][0]["evidence_ids"].append(f"{SOURCE}:old-evidence-1")
    with pytest.raises(ContractError):
        parse_output(json.dumps(output), [new], [], result.events)


def test_model_input_rejects_cross_stream_or_stale_batch(tmp_path):
    new = message(source="official_chat")
    result = retrieve(ObservedStore(event(1, 1)), new)
    provider = pure_xhub(Settings(uid=UID, data_dir=tmp_path))
    with pytest.raises(XHubError):
        provider.configure_history_retrieval(replace(result, stream="another-analysis-stream"))
    provider.configure_history_retrieval(result)
    with pytest.raises(XHubError):
        provider._input([new], [], [replace(result.events[0], stream="another-analysis-stream")])
    with pytest.raises(XHubError):
        provider._input([replace(new, content="虚构新版本")], [], result.events)
    with pytest.raises(XHubError):
        provider._input([new], [message("future-context", 14, source="official_chat")], result.events)


def test_sql_queries_are_scoped_bounded_and_do_not_search_global_payloads():
    store = object.__new__(MySQLStore)
    calls = []
    old = event(1, 1)

    def execute(statement, args):
        calls.append((statement, args))
        if "AS revision_ends" in statement:
            return [dict(event_id=old.event_id, end_time=old.end_time.replace(tzinfo=None),
                         revision_ends=json.dumps([window(1).end.isoformat()]))], 1
        return [dict(snapshot=json.dumps(old.updated_by[0]["snapshot"]))], 1

    store._execute = execute
    heads = store.history_event_heads(UID, STREAM, window(12).end, limit=2, since=window(1).start,
                                      cursor=(window(10).start, str(UUID(int=5))))
    snapshots = store.history_event_snapshots(UID, STREAM, heads)
    assert snapshots[0].event_id == old.event_id
    for sql, args in calls:
        assert "WHERE uid=%s AND stream=%s" in sql
        assert args[:2] == (UID, STREAM) and "LIMIT %s" in sql
        assert "LIKE" not in sql and "OFFSET" not in sql and "SELECT payload" not in sql
    assert "end_time < %s" in calls[0][0] and "event_id < %s" in calls[0][0]
    assert "$.updated_by[last].snapshot" in calls[1][0] and "CAST(%s AS JSON)" in calls[1][0]


def test_retrieval_failure_rolls_back_without_advancing_analysis(tmp_path, monkeypatch):
    item = message("opening", 1)
    service, store, _, provider = build(tmp_path, [item])

    def fail(*args, **kwargs):
        raise RuntimeError("虚构检索失败")

    monkeypatch.setattr(store, "history_event_heads", fail)
    with pytest.raises(RuntimeError, match="虚构检索失败"):
        service.run_window(UID, window(1))
    assert not store.messages and not store.events and not store.processed
    assert not provider.calls and service.state.read(UID, STREAM) == {}


def test_task_freezes_retrieval_budgets_using_existing_request_mechanism(tmp_path):
    from test_runs import execute_next, run_fixture

    _, repository, runner, _, _, _, request, total, seen = run_fixture(tmp_path, days=1)
    saved = request["history_retrieval"]
    runner.settings = replace(runner.settings, history_retrieval=HistoryRetrievalSettings(max_candidates=1))
    execute_next(repository, runner, request, total)
    assert seen[0].history_retrieval.max_candidates == saved["max_candidates"] == 8


@pytest.mark.parametrize("changes", [{"page_size": 0}, {"older_pages": -1}, {"max_chars": 1}, {"exact_limit": True}])
def test_invalid_resource_budgets_are_rejected(changes):
    with pytest.raises(ValueError):
        HistoryRetrievalSettings(**changes)
