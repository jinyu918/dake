"""用原回归场景验收明确归属；模型决定与消息均为虚构，不代表真实语义准确率。"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import socket

import pytest

from dake.analysis.attribution import parse_attribution_output
from dake.config import Settings
from dake.domain.models import Candidate, Message, Window, parse_time
from dake.services.processing import IncrementalService
from fakes import FakeProvider, FakeSource, MemoryStore


UID = "synthetic-gap-regression-customer"
TOPIC = "synthetic-device:connection-timeout"
OPEN_STATUS = "合成未解决"
CLOSED_STATUS = "合成已结束"


class KnownBusinessGap(AssertionError):
    """保留原基线的业务差异断言；现在不再标记为预期失败。"""


def expect_business_result(actual, expected):
    """指出原基线中的业务期望差异，不屏蔽普通断言或运行异常。"""
    if actual != expected:
        raise KnownBusinessGap(f"业务期望：{expected!r}；当前结果：{actual!r}")


@pytest.fixture(autouse=True)
def local_dependencies_only(monkeypatch):
    """新增用例禁止读取默认配置、解析网络地址或建立真实连接。"""
    def forbidden(*args, **kwargs):
        raise RuntimeError("回归缺口测试只能使用显式配置与本地替身")

    monkeypatch.setattr(Settings, "load", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


def day_window(day):
    start = parse_time(f"2031-04-{day:02d}")
    return Window(start, start + timedelta(days=1))


def message(mid, day, content):
    return Message(
        uid=UID, message_id=mid, sent_at=day_window(day).start + timedelta(hours=9),
        content=content, source="official_chat",
        conversation_id="synthetic-gap-room", sender_id="synthetic-gap-person",
    )


@pytest.fixture
def opening():
    return message("gap-opening", 1, "虚构工单 CASE-SYNTH-42 的样机连接超时，尚未解决。")


def candidate(item, **changes):
    return replace(Candidate("排障", TOPIC, item.content, [item.key], status=OPEN_STATUS,
        decision="new", decision_reason="虚构证据明确支持本次归属",
        basis=[dict(message_id=item.key, quote=item.content)]), **changes)


class ExplicitProvider(FakeProvider):
    """原回归场景改用明确归属，并接收正式服务补取的只读背景。"""

    def configure_history_context(self, result):
        self.background = result


def build(tmp_path, messages, mapping):
    settings = Settings(
        uid=UID, data_dir=tmp_path, start=day_window(1).start, end=day_window(10).end,
        interval=timedelta(days=1), provider="stub", match_window=timedelta(days=7),
        closed_statuses=(CLOSED_STATUS,),
    )
    store, provider = MemoryStore(), ExplicitProvider(mapping, name="synthetic-gap-regression-v1")
    service = IncrementalService(store, FakeSource(messages), provider, settings)
    return service, store, provider


def test_explicit_new_decision_keeps_same_topic_incidents_separate(tmp_path, opening):
    follow = message("gap-independent", 2, "虚构工单 CASE-SYNTH-99 是另一次独立超时，请单独记录。")
    service, store, provider = build(tmp_path, [opening, follow], {opening.message_id: candidate(opening)})
    first = service.run_window(UID, day_window(1))
    assert len(first.created) == 1
    original = deepcopy(store.events[first.created[0]])

    # 经过真实合约解析传递 new，避免在不存在的候选字段上模拟模型决策。
    output = {"schema_version": "4.0", "candidates": [{
        "type": "排障", "topic_key": TOPIC, "summary": follow.content,
        "evidence_ids": [follow.key], "status": None, "severity": None,
        "assignees": [], "attributes": [], "extensions": [], "decision": "new",
        "referenced_event_id": None, "decision_reason": "虚构工单不同，明确要求独立记录。",
        "type_labels": [{"type": "排障", "evidence_ids": [follow.key], "quote": follow.content}],
        "removed_types": [], "typed_attributes": [], "classification_status": "已分类", "other_reason": None,
        "basis": [{"message_id": follow.key, "quote": follow.content}], "possible_event_ids": [], "continuity": None,
        "changes": [{"kind": "progress", "text": follow.content, "replaces": None,
                     "evidence": [{"message_id": follow.key, "quote": follow.content}]}],
    }]}
    parsed = parse_attribution_output(json.dumps(output, ensure_ascii=False), [follow], [opening], [original])
    assert parsed[0].extensions["extraction_v1"]["decision"] == "new"
    provider.mapping[follow.message_id] = parsed
    result = service.run_window(UID, day_window(2))
    assert result.processed == 1 and not result.review
    assert provider.calls[-1][2][0].event_id == original.event_id

    expect_business_result((len(result.created), result.updated), (1, []))
    assert result.created[0] != original.event_id
    assert store.events[original.event_id] == original
    assert len(store.list_events(UID, service.stream)) == 2
    assert [item["message_id"] for item in store.events[result.created[0]].evidence] == [follow.message_id]


def test_same_incident_with_renamed_topic_keeps_event_id(tmp_path, opening):
    follow = message("gap-renamed-topic", 2, "虚构工单 CASE-SYNTH-42 仍是同一连接问题，仅换一种名称描述。")
    renamed = candidate(follow, topic_key="synthetic-device:request-timeout",
                        decision="update", basis=[dict(message_id=m.key, quote=m.content) for m in [follow, opening]])
    service, store, provider = build(tmp_path, [opening, follow], {
        opening.message_id: candidate(opening), follow.message_id: renamed,
    })
    first = service.run_window(UID, day_window(1))
    assert len(first.created) == 1
    renamed.referenced_event_id = first.created[0]
    result = service.run_window(UID, day_window(2))
    new, context, events = provider.calls[-1]
    assert [item.key for item in new] == [follow.key]
    assert [item.key for item in context] == [opening.key]
    assert events[0].event_id == first.created[0] and events[0].topic_key != renamed.topic_key

    expect_business_result((result.created, result.updated, len(store.events)), ([], first.created, 1))
    assert not result.review and result.processed == 1
    assert {item["message_id"] for item in store.events[first.created[0]].evidence} == {
        opening.message_id, follow.message_id,
    }


def test_unresolved_incident_after_seven_days_keeps_event_id(tmp_path, opening):
    follow = message("gap-still-unresolved", 9, "虚构工单 CASE-SYNTH-42 持续至今仍未解决，并非新问题。")
    service, store, provider = build(tmp_path, [opening, follow], {
        opening.message_id: candidate(opening), follow.message_id: candidate(follow),
    })
    first = service.run_window(UID, day_window(1))
    assert len(first.created) == 1
    provider.mapping[follow.message_id] = candidate(follow, decision="update", referenced_event_id=first.created[0],
        basis=[dict(message_id=m.key, quote=m.content) for m in [follow, opening]])
    for day in range(2, 9):
        silent = service.run_window(UID, day_window(day))
        assert (silent.processed, silent.created, silent.updated) == (0, [], [])
    assert len(provider.calls) == 1
    result = service.run_window(UID, day_window(9))
    old = provider.calls[-1][2][0]
    assert old.event_id == first.created[0] and old.status == OPEN_STATUS and old.closed_at is None
    assert follow.sent_at - old.end_time > service.settings.match_window == timedelta(days=7)

    expect_business_result((result.created, result.updated, len(store.events)), ([], first.created, 1))
    event = store.events[first.created[0]]
    assert not result.review and event.end_time == follow.sent_at
    assert event.status == OPEN_STATUS and event.closed_at is None
    assert [item["message_id"] for item in event.evidence] == [opening.message_id, follow.message_id]


def test_acknowledgement_without_business_change_keeps_summary(tmp_path, opening):
    follow = message("gap-acknowledgement", 2, "虚构工单 CASE-SYNTH-42 的消息收到，暂无其他反馈。")
    service, store, provider = build(tmp_path, [opening, follow], {
        opening.message_id: candidate(opening),
        follow.message_id: candidate(follow, summary="虚构工单的问题已收到，暂无新增进展。"),
    })
    first = service.run_window(UID, day_window(1))
    assert len(first.created) == 1
    original = deepcopy(store.events[first.created[0]])
    provider.mapping[follow.message_id] = candidate(follow, decision="duplicate", referenced_event_id=first.created[0],
        basis=[dict(message_id=m.key, quote=m.content) for m in [follow, opening]])
    result = service.run_window(UID, day_window(2))
    event = store.events[first.created[0]]
    assert not result.updated and not result.created and not result.review
    assert result.processed == 1 and len(provider.calls) == 2
    assert (event.status, event.severity, event.assignees, event.attributes, event.extensions) == (
        original.status, original.severity, original.assignees, original.attributes, original.extensions,
    )
    # duplicate 只记录归属收据，事件时间、证据、摘要和修订全部保持不变。
    assert event == original
    expect_business_result(event.summary, original.summary)
