"""所有来源记录均为自行构造的数据，不使用真实群聊作为测试夹具。"""

from copy import deepcopy
from datetime import date
import io
import json
import os
from urllib.error import HTTPError, URLError

import pytest

from dake.ingestion.adapter import AdapterError, ChatRecordsAdapter, OfficialChatSource
from dake.ingestion.client import ChatRecordsClient, ChatRecordsError, _NoRedirect
from dake.domain.models import TZ


DAY = date(2026, 11, 8)
UID = "fixture-customer"


def record(mid="101", *, group="group-a", time="2026-11-08 09:00:00", content="自行构造的测试消息"):
    return {
        "id": mid,
        "group_number": group,
        "group_name": "虚构群",
        "sender_number": "invented-sender",
        "display_name": "虚构联系人",
        "nick_name": "虚构昵称",
        "content": content,
        "created_at": "2026-11-08 11:00:00",
        "send_time": time,
        "is_inner": 0,
    }


def payload(*groups):
    grouped = []
    for records in groups or ([record()],):
        grouped.append({
            "group_number": records[0]["group_number"] if records else "empty-group",
            "group_name": "虚构群",
            "total": len(records),
            "records": records,
        })
    return {"code": 0, "msg": "ok", "uid": UID, "start": DAY.isoformat(), "end": DAY.isoformat(), "total": sum(g["total"] for g in grouped), "groups": grouped}


def normalize(value):
    return ChatRecordsAdapter().normalize(value, UID, DAY, DAY)


def test_multigroup_global_order_uses_send_time_and_retains_numeric_ids():
    messages = normalize(payload(
        [record("0001", time="2026-11-08 12:00:00")],
        [record("20", group="group-b", time="2026-11-08 08:00:00"), record("9", group="group-b", time="2026-11-08 08:00:00")],
    ))
    assert [m.message_id for m in messages] == ["9", "20", "0001"]
    assert messages[0].sent_at.hour == 8
    assert messages[0].sent_at.tzinfo == TZ
    assert messages[0].uid == UID
    assert messages[0].internal_hint == 0


def test_markers_and_missing_sender_name_are_preserved_without_inference():
    value = payload([record(content="说明 [图片] 和 [文件]")])
    value["groups"][0]["records"][0]["display_name"] = ""
    message = normalize(value)[0]
    assert message.markers == ("[图片]", "[文件]")
    assert message.sender_name == value["groups"][0]["records"][0]["nick_name"]


def test_empty_day_is_valid():
    value = payload()
    value.update(total=0, groups=[])
    assert normalize(value) == []


def test_inclusive_date_range_keeps_both_days():
    value = payload([record(time="2026-11-08 00:00:00"), record("102", time="2026-11-09 23:59:59")])
    value["end"] = "2026-11-09"
    assert len(ChatRecordsAdapter().normalize(value, UID, DAY, date(2026, 11, 9))) == 2


def test_official_midnight_range_echo_matches_requested_inclusive_dates():
    value = payload()
    value.update(start="2026-11-08 00:00:00", end="2026-11-09 00:00:00")
    assert len(ChatRecordsAdapter().normalize(value, UID, DAY, DAY)) == 1


@pytest.mark.parametrize("where", ["top", "group"])
def test_incomplete_response_is_rejected(where):
    value = payload()
    (value if where == "top" else value["groups"][0])["total"] += 1
    with pytest.raises(AdapterError, match="total"):
        normalize(value)


@pytest.mark.parametrize("field,value", [("uid", "another"), ("start", "2026-11-07"), ("end", "2026-11-09"), ("code", 1), ("code", False), ("groups", None), ("total", True)])
def test_response_metadata_is_checked(field, value):
    raw = payload()
    raw[field] = value
    with pytest.raises(AdapterError):
        normalize(raw)


@pytest.mark.parametrize("field,value", [
    ("id", 123), ("id", "x"), ("send_time", "bad-time"),
    ("send_time", "2026-11-09 00:00:00"), ("send_time", "2026-11-07 23:59:59"),
    ("content", None), ("group_number", "different-group"), ("is_inner", 2), ("is_inner", True),
])
def test_invalid_records_rejected_without_echoing_input(field, value):
    raw = payload()
    raw["groups"][0]["records"][0][field] = value
    with pytest.raises(AdapterError) as error:
        normalize(raw)
    assert "虚构" not in str(error.value)


def test_duplicate_id_is_idempotent_but_conflicting_record_stops_ingestion():
    row = record()
    assert len(normalize(payload([row, deepcopy(row)]))) == 1
    conflicting = record(content="另一个自行构造的内容")
    with pytest.raises(AdapterError, match="冲突"):
        normalize(payload([row, conflicting]))


class FakeClient:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def fetch(self, uid, start, end):
        self.calls += 1
        return deepcopy(self.value)


def test_online_refresh_and_offline_roundtrip(tmp_path):
    client = FakeClient(payload())
    online = OfficialChatSource(client, tmp_path)
    first = online.load_day(UID, DAY)
    second = online.load_day(UID, DAY)
    assert client.calls == 2
    cached = OfficialChatSource(None, tmp_path, offline=True).load_day(UID, DAY)
    assert first == second == cached
    assert os.stat(online.cache_path(UID, DAY)).st_mode & 0o777 == 0o600


def test_invalid_refresh_keeps_previous_valid_cache(tmp_path):
    client = FakeClient(payload())
    source = OfficialChatSource(client, tmp_path)
    source.load_day(UID, DAY)
    client.value["total"] += 1
    with pytest.raises(AdapterError):
        source.load_day(UID, DAY)
    assert len(OfficialChatSource(None, tmp_path, True).load_day(UID, DAY)) == 1


def test_offline_requires_valid_local_cache(tmp_path):
    source = OfficialChatSource(None, tmp_path, True)
    with pytest.raises(AdapterError, match="本地原始缓存"):
        source.load_day(UID, DAY)
    path = source.cache_path(UID, DAY)
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(AdapterError, match="有效 JSON"):
        source.load_day(UID, DAY)


@pytest.mark.parametrize("uid", ["../escape", "/tmp/escape", "", ".", "a/b", "a\\b"])
def test_cache_uid_cannot_escape_data_directory(tmp_path, uid):
    with pytest.raises(ValueError):
        OfficialChatSource(None, tmp_path, True).cache_path(uid, DAY)


def test_symlink_cache_cannot_escape_data_directory(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "raw").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="缓存路径"):
        OfficialChatSource(None, directory, True).cache_path(UID, DAY)


class Response:
    status = 200

    def __init__(self, value):
        self.raw = value if isinstance(value, bytes) else json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.raw


class Opener:
    def __init__(self, *outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def open(self, request, **kwargs):
        self.calls.append(request)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_transport_posts_only_official_request_fields():
    client = ChatRecordsClient()
    client._opener = Opener(Response(payload()))
    assert client.fetch(UID, DAY, DAY)["total"] == 1
    request = client._opener.calls[0]
    assert request.method == "POST"
    assert json.loads(request.data) == {"uid": UID, "start": "2026-11-08", "end": "2026-11-08"}
    assert request.get_header("Authorization") is None


def test_transient_http_errors_retry_without_echoing_body(monkeypatch):
    monkeypatch.setattr("dake.ingestion.client.time.sleep", lambda _: None)
    client = ChatRecordsClient()
    failure = HTTPError("https://example.invalid", 503, "private-content", {}, io.BytesIO(b"private-content"))
    client._opener = Opener(failure, Response(payload()))
    assert client.fetch(UID, DAY, DAY)["total"] == 1
    assert len(client._opener.calls) == 2


@pytest.mark.parametrize("status", [302, 400, 401, 403, 404])
def test_permanent_http_errors_are_not_retried_or_disclosed(status):
    client = ChatRecordsClient()
    client._opener = Opener(HTTPError("https://example.invalid", status, "private-content", {}, io.BytesIO(b"private-content")))
    with pytest.raises(ChatRecordsError) as error:
        client.fetch(UID, DAY, DAY)
    assert "private-content" not in str(error.value)
    assert len(client._opener.calls) == 1


@pytest.mark.parametrize("value", [b"private-content", {"code": 1, "msg": "private-content"}, [], {"code": False}])
def test_invalid_transport_response_is_redacted(value):
    client = ChatRecordsClient()
    client._opener = Opener(Response(value))
    with pytest.raises(ChatRecordsError) as error:
        client.fetch(UID, DAY, DAY)
    assert "private-content" not in str(error.value)


def test_network_errors_are_redacted_and_bounded(monkeypatch):
    monkeypatch.setattr("dake.ingestion.client.time.sleep", lambda _: None)
    client = ChatRecordsClient(retries=1)
    client._opener = Opener(URLError("private-content"), URLError("private-content"))
    with pytest.raises(ChatRecordsError) as error:
        client.fetch(UID, DAY, DAY)
    assert "private-content" not in str(error.value)
    assert len(client._opener.calls) == 2


def test_no_redirect_handler_and_no_alternative_endpoint():
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid") is None
    with pytest.raises(ValueError):
        ChatRecordsClient("http://127.0.0.1/qq/messages")
