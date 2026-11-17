"""结构化字段、外部协议失败及事务回滚验证；模型请求由测试替身接管。"""

from dataclasses import replace
import json

import httpx
import pytest

from dake.analysis.contract import ContractError, parse_output
from dake.analysis.providers.xhub import XHubConfig, XHubError, XHubProvider
from dake.config import Settings
from dake.domain.models import Event, Window, parse_time
from dake.services.processing import IncrementalService
from fakes import FakeSource, MemoryStore

from provider_fakes import UID, message, candidate, output, response


def provider(transport, tmp_path, **config):
    return XHubProvider(XHubConfig(model="synthetic-model", api_key="fake-key-for-test", **config),
                        replace(Settings(), uid=UID, provider="xhub", data_dir=tmp_path),
                        repair_attempts=0, transport=httpx.MockTransport(transport))


def test_grounded_fields_and_unknowns():
    msg = message(text="合成空间 a 已恢复正常，这个问题我来跟进。")
    raw = candidate(status=dict(value="恢复正常", quote="已恢复正常", evidence_ids=[msg.key]),
                    assignees=[dict(value=msg.sender_id, quote="这个问题我来跟进", evidence_ids=[msg.key])])
    result = parse_output(output(raw), [msg], [], [])[0]
    assert result.status == "恢复正常"
    assert result.severity is None
    assert result.assignees == [msg.sender_id]
    assert result.extensions["extraction_v1"]["status"]["quote"] == "已恢复正常"


@pytest.mark.parametrize("changes", [
    {"type": "其他"}, {"evidence_ids": ["forged"]}, {"evidence_ids": ["m1"]},
    {"evidence_ids": [message().key, message().key]}, {"topic_key": None},
    {"summary": " "}, {"event_id": "model-invented"}, {"decision": "new", "referenced_event_id": "old"},
    {"severity": {"value": "P0", "evidence_ids": [message().key], "quote": "合成空间 a 下载失败"}},
    {"status": {"value": "恢复正常", "evidence_ids": [message().key], "quote": "已恢复正常"}},
])
def test_invalid_structure_or_forged_evidence_rejected(changes):
    with pytest.raises(ContractError):
        parse_output(output(candidate(**changes)), [message()], [], [])


def test_history_only_candidate_is_rejected():
    with pytest.raises(ContractError, match="新增证据"):
        parse_output(output(candidate(evidence_ids=[message("old").key])),
                     [message()], [message("old")], [])


def test_update_reference_must_match_customer_type_and_canonical_topic():
    moment = parse_time("2026-11-08T08:00:00+08:00")
    events = [Event(event_id="old", uid=UID, stream="test", type="排障", topic_key="存储/a/下载失败",
                    summary="合成故障", start_time=moment, end_time=moment, created_on=moment,
                    status="未知", severity="未知")]
    raw = candidate(decision="update", referenced_event_id="old")
    assert parse_output(output(raw), [message()], [], events)[0].referenced_event_id == "old"
    for change in ({"referenced_event_id": "invented"}, {"topic_key": "别的主题"}, {"type": "商机"}):
        with pytest.raises(ContractError):
            parse_output(output({**raw, **change}), [message()], [], events)
    events[0].uid = "another-customer"
    with pytest.raises(ContractError, match="同一客户"):
        parse_output(output(raw), [message()], [], events)


def test_ambiguous_candidate_does_not_force_identity():
    result = parse_output(output(candidate(decision="review", topic_key=None)), [message()], [], [])[0]
    assert result.ambiguous and result.referenced_event_id is None


def test_legacy_event_keeps_type_without_inventing_label_evidence():
    moment = parse_time("2026-11-08T08:00:00+08:00")
    event = Event.from_dict(dict(event_id="old", uid=UID, stream="test", type="排障", topic_key="合成主题",
        start_time=moment.isoformat(), end_time=moment.isoformat(), created_on=moment.isoformat(),
        status="未知", severity="未知", summary="合成摘要", attributes={}, evidence=[], updated_by=[]))
    assert event.types == ["排障"]
    assert event.type_evidence == {}
    assert event.classification_evidence_status == "未记录"


@pytest.mark.parametrize("status", [301, 400, 401, 429, 500])
def test_http_failure_is_not_successful_empty_result(tmp_path, status):
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "fake-key-for-test secret body"},
                              headers={"Location": "https://example.invalid"})

    client = provider(reply, tmp_path)
    try:
        with pytest.raises(XHubError) as error:
            client.extract([message()], [], [])
        assert "fake-key-for-test" not in str(error.value)
        assert len(calls) == 1
        assert client.traces[-1]["http_status"] == status
        assert "secret body" not in json.dumps(client.traces)
    finally:
        client.close()


@pytest.mark.parametrize("result", [
    {"choices": []},
    {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": None, "refusal": "refused"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]},
])
def test_incomplete_refusal_or_malformed_output_fails(tmp_path, result):
    client = provider(lambda _: httpx.Response(200, json=result), tmp_path)
    try:
        with pytest.raises(XHubError):
            client.extract([message()], [], [])
        assert not client.traces[-1].get("ok", False)
    finally:
        client.close()


def test_wrong_customer_is_blocked_before_network(tmp_path):
    calls = []
    client = provider(lambda request: calls.append(request), tmp_path)
    try:
        with pytest.raises(XHubError, match="已启用客户"):
            client.extract([replace(message(), uid="real-customer", source="official_chat")], [], [])
        assert calls == []
        assert "fake-key-for-test" not in repr(client.config)
    finally:
        client.close()


def test_timeout_does_not_retry_or_echo_request(tmp_path):
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("fake-key-for-test", request=request)

    client = provider(timeout, tmp_path)
    try:
        with pytest.raises(XHubError, match="超时") as error:
            client.extract([message()], [], [])
        assert len(calls) == 1 and "fake-key-for-test" not in str(error.value)
    finally:
        client.close()


def test_invalid_model_result_rolls_back_message_processing(tmp_path):
    store = MemoryStore()
    client = provider(lambda _: response(output(candidate(evidence_ids=["invented"]))), tmp_path)
    settings = replace(Settings(), uid=UID, data_dir=tmp_path)
    service = IncrementalService(store, FakeSource([message()]), client, settings)
    try:
        with pytest.raises(XHubError):
            service.run_window(UID, Window(parse_time("2026-11-08"), parse_time("2026-11-09")))
        assert not store.events and not store.processed and not store.messages
    finally:
        client.close()
