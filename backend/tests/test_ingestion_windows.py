"""分钟与多客户适配使用合成消息，覆盖窗口隔离及旧日期回放兼容。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from dake.domain.models import TZ, Window
from dake.ingestion.adapter import AdapterError, ChatRecordsAdapter, OfficialChatSource
from dake.ingestion.client import ChatRecordsClient
from dake.services.ingestion import IngestionService
from test_ingestion import DAY, UID, FakeClient, Opener, Response, payload, record


START = datetime(2026, 11, 8, 9, tzinfo=TZ)
END = START + timedelta(minutes=15)


def window_payload(*groups, start=START, end=END, uid=None):
    """构造带两层客户身份的分钟响应。"""
    value = payload(*(records for _, records in groups))
    value.pop("uid")
    value.update(start=start.astimezone(TZ).replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S"),
                 end=(end.astimezone(TZ).replace(second=0, microsecond=0) + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"))
    for group, (customer, _) in zip(value["groups"], groups):
        group["uid"] = customer
        for row in group["records"]:
            row["uid"] = customer
    if uid is not None:
        value["uid"] = uid
    return value


def normalize(value, start=START, end=END, uid=None):
    return ChatRecordsAdapter().normalize_window(value, start, end, uid)


def test_cross_customer_global_sort_and_exact_window_boundary():
    raw = window_payload(
        (UID, [record("1", time="2026-11-08 09:14:59"),
               record("2", time="2026-11-08 09:15:00"),
               record("3", time="2026-11-08 09:15:59")]),
        ("fixture-b", [record("4", group="group-b", time="2026-11-08 09:00:00")]),
    )
    messages = normalize(raw)
    assert [(m.uid, m.message_id) for m in messages] == [("fixture-b", "4"), (UID, "1")]
    assert messages[0].key == "official_chat:4"
    assert messages[0].sent_at != datetime(2026, 11, 8, 11, tzinfo=TZ)


def test_adjacent_window_keeps_end_minute_only_in_next_window():
    rows = [record("1", time="2026-11-08 09:15:00"), record("2", time="2026-11-08 09:15:59")]
    assert normalize(window_payload((UID, deepcopy(rows)))) == []
    next_end = END + timedelta(minutes=15)
    following = window_payload((UID, rows), start=END, end=next_end)
    assert [m.message_id for m in normalize(following, END, next_end)] == ["1", "2"]


def test_seconds_and_other_timezone_are_covered_then_filtered():
    start = START.astimezone(timezone.utc) + timedelta(seconds=20)
    end = start + timedelta(seconds=20)
    raw = window_payload((UID, [record("1", time="2026-11-08 09:00:19"),
                                record("2", time="2026-11-08 09:00:20"),
                                record("3", time="2026-11-08 09:00:40")]), start=start, end=end)
    client = ChatRecordsClient()
    client._opener = Opener(Response(raw))
    assert client.fetch_window(start, end) == raw
    assert json.loads(client._opener.calls[0].data) == {"start": "2026-11-08 09:00", "end": "2026-11-08 09:00"}
    assert [m.message_id for m in normalize(raw, start, end)] == ["2"]


def test_single_customer_minute_request_and_nonempty_response():
    raw = window_payload((UID, [record()]), uid=UID)
    client = ChatRecordsClient()
    client._opener = Opener(Response(raw))
    assert normalize(client.fetch_window(START, END, UID), uid=UID)[0].uid == UID
    assert json.loads(client._opener.calls[0].data)["uid"] == UID
    raw.pop("uid")
    assert normalize(raw, uid=UID)[0].uid == UID
    with pytest.raises(AdapterError):
        normalize(raw, uid="another-customer")


@pytest.mark.parametrize("location", ["group", "record"])
@pytest.mark.parametrize("value", [None, "", "../bad", "other-customer"])
def test_missing_invalid_or_conflicting_uid_is_rejected(location, value):
    raw = window_payload((UID, [record()]))
    target = raw["groups"][0] if location == "group" else raw["groups"][0]["records"][0]
    target["uid"] = value
    with pytest.raises(AdapterError) as error:
        normalize(raw)
    assert "other-customer" not in str(error.value)


def test_date_adapter_checks_optional_uid_fields_too():
    raw = payload()
    raw["groups"][0]["records"][0]["uid"] = "different"
    with pytest.raises(AdapterError):
        ChatRecordsAdapter().normalize(raw, UID, DAY, DAY)


@pytest.mark.parametrize("mutation", ["total", "group_total", "group_id", "time", "top_uid", "echo"])
def test_complete_response_checked_even_for_end_minute(mutation):
    raw = window_payload((UID, [record(time="2026-11-08 09:15:50")]))
    if mutation == "total":
        raw["total"] += 1
    elif mutation == "group_total":
        raw["groups"][0]["total"] += 1
    elif mutation == "group_id":
        raw["groups"][0]["records"][0]["group_number"] = "other"
    elif mutation == "time":
        raw["groups"][0]["records"][0]["send_time"] = "2026-11-08 09:16:00"
    elif mutation == "top_uid":
        raw["uid"] = UID
    else:
        raw["start"] = "incorrect"
    with pytest.raises(AdapterError):
        normalize(raw)


def test_duplicate_id_cannot_cross_customers_or_hide_outside_business_window():
    raw = window_payload((UID, [record()]), ("fixture-b", [record(group="group-b")]))
    with pytest.raises(AdapterError, match="冲突"): normalize(raw)
    raw = window_payload((UID, [record(), record(time="2026-11-08 09:15:50")]))
    with pytest.raises(AdapterError, match="冲突"): normalize(raw)
    row = record()
    assert len(normalize(window_payload((UID, [row, deepcopy(row)])))) == 1


@pytest.mark.parametrize("start,end", [(START.replace(tzinfo=None), END), (START, START), (END, START)])
def test_invalid_window_never_calls_transport(start, end):
    client = ChatRecordsClient()
    client._opener = Opener()
    with pytest.raises(ValueError): client.fetch_window(start, end)
    assert client._opener.calls == []


class WindowClient:
    """模拟分钟接口，记录请求而不访问网络。"""
    def __init__(self, value):
        self.value = value
        self.calls = []

    def fetch_window(self, start, end, uid=None):
        self.calls.append((start, end, uid))
        return deepcopy(self.value)


def test_window_cache_refresh_roundtrip_and_invalid_refresh_preserves_previous(tmp_path):
    client = WindowClient(window_payload((UID, [record()])))
    source = OfficialChatSource(client, tmp_path)
    first = source.load_window(None, START, END)
    assert source.load_window(None, START, END) == first
    assert len(client.calls) == 2
    client.value["total"] += 1
    with pytest.raises(AdapterError): source.load_window(None, START, END)
    assert OfficialChatSource(None, tmp_path, True).load_window(None, START, END) == first
    assert os.stat(source.window_cache_path(START, END)).st_mode & 0o777 == 0o600


def test_window_cache_identity_separates_customers_ranges_and_dates(tmp_path):
    source = OfficialChatSource(None, tmp_path, True)
    paths = {source.window_cache_path(START, END), source.window_cache_path(START, END, UID),
             source.window_cache_path(START, END, "all"),
             source.window_cache_path(START, END + timedelta(minutes=1)), source.cache_path(UID, DAY)}
    assert len(paths) == 5
    assert source.window_cache_path(START, END) == source.window_cache_path(
        START.astimezone(timezone.utc), END.astimezone(timezone.utc))
    with pytest.raises(ValueError): source.window_cache_path(START, END, "../escape")
    (tmp_path / "raw").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="缓存路径"): source.window_cache_path(START, END)


def test_offline_window_supports_old_date_cache_but_not_unknown_cross_customer_scope(tmp_path):
    source = OfficialChatSource(FakeClient(payload()), tmp_path)
    expected = source.load_day(UID, DAY)
    offline = OfficialChatSource(None, tmp_path, True)
    assert offline.load_window(UID, START, END) == expected
    with pytest.raises(AdapterError, match="本地原始缓存"): offline.load_window(None, START, END)
    path = offline.window_cache_path(START, END, UID)
    path.parent.mkdir(parents=True)
    path.write_text("invalid")
    with pytest.raises(AdapterError, match="有效 JSON"): offline.load_window(UID, START, END)


def test_window_service_routes_to_minute_source_and_rejects_other_customers(tmp_path):
    from fakes import MemoryStore

    source = OfficialChatSource(WindowClient(window_payload((UID, [record()]))), tmp_path)
    store = MemoryStore()
    service = IngestionService(store, source)
    assert service.fetch(UID, Window(START, END)) == 1
    assert service.fetch(UID, Window(START, END)) == 0
    assert len(source.client.calls) == 2
    source.client.value = window_payload(("fixture-b", [record()]))
    with pytest.raises(AdapterError): service.fetch(UID, Window(START, END))
    assert len(store.messages) == 1


def test_same_group_cannot_belong_to_multiple_customers():
    raw = window_payload((UID, [record("1")]), ("fixture-b", [record("2")]))
    with pytest.raises(AdapterError, match="同一群"):
        normalize(raw)


def test_minute_response_end_echo_must_be_expanded_by_exactly_one_minute():
    raw = window_payload((UID, [record()]))
    for wrong_end in ("2026-11-08 09:15:00", "2026-11-08 09:17:00"):
        raw["end"] = wrong_end
        with pytest.raises(AdapterError, match="时间范围"):
            normalize(raw)


def test_minute_source_replay_updates_same_event_without_future_messages(tmp_path):
    from dataclasses import replace
    from dake.config import Settings
    from dake.domain.models import Candidate
    from dake.services.processing import IncrementalService
    from dake.services.replay import ReplayService
    from fakes import FakeProvider, MemoryStore

    rows = [record("1"), record("2", time="2026-11-08 09:15:00")]

    class ReplayClient:
        """模拟包含结束分钟的服务端，供两轮增量及重复回放验证。"""
        def fetch_window(self, start, end, uid=None):
            selected = [deepcopy(row) for row in rows
                        if start <= datetime.strptime(row["send_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
                        < end + timedelta(minutes=1)]
            return window_payload((uid, selected), start=start, end=end, uid=uid)

    provider = FakeProvider({row["id"]: Candidate("排障", "synthetic-topic", row["content"], [row["id"]])
                             for row in rows})
    settings = replace(Settings(), uid=UID, start=START, end=END + timedelta(minutes=15), data_dir=tmp_path)
    source = OfficialChatSource(ReplayClient(), tmp_path)
    service = IncrementalService(MemoryStore(), source, provider, settings)
    args = (UID, START, settings.end, timedelta(minutes=15))
    iterator = ReplayService(service).run_range(*args)
    first = next(iterator)
    assert len(first.created) == 1
    assert [m.message_id for m in service.store.messages.values()] == ["1"]
    second = next(iterator)
    assert second.updated == first.created
    assert [[m.message_id for m in call[0]] for call in provider.calls] == [["1"], ["2"]]
    repeated = list(ReplayService(service).run_range(*args))
    assert all(not result.created and not result.updated for result in repeated)
    assert len(provider.calls) == 2
    assert len(service.store.events) == 1


def test_offline_date_cache_crosses_shanghai_midnight(tmp_path):
    raw = payload([record("1", time="2026-11-08 23:59:59")])
    client = FakeClient(raw)
    source = OfficialChatSource(client, tmp_path)
    source.load_day(UID, DAY)
    tomorrow = DAY + timedelta(days=1)
    raw = payload([record("2", time="2026-11-09 00:00:00")])
    raw.update(start=tomorrow.isoformat(), end=tomorrow.isoformat())
    client.value = raw
    source.load_day(UID, tomorrow)
    start = datetime(2026, 11, 8, 23, 59, tzinfo=TZ).astimezone(timezone.utc)
    end = start + timedelta(minutes=2)
    result = OfficialChatSource(None, tmp_path, True).load_window(UID, start, end)
    assert [m.message_id for m in result] == ["1", "2"]


def test_conflicting_message_across_old_date_caches_is_not_silently_overwritten(tmp_path):
    client = FakeClient(payload([record("1", time="2026-11-08 23:59:59")]))
    source = OfficialChatSource(client, tmp_path)
    source.load_day(UID, DAY)
    tomorrow = DAY + timedelta(days=1)
    client.value = payload([record("1", time="2026-11-09 00:00:00")])
    client.value.update(start=tomorrow.isoformat(), end=tomorrow.isoformat())
    source.load_day(UID, tomorrow)
    with pytest.raises(AdapterError, match="冲突"):
        OfficialChatSource(None, tmp_path, True).load_window(
            UID, datetime(2026, 11, 8, 23, 59, tzinfo=TZ), datetime(2026, 11, 9, 0, 1, tzinfo=TZ))
