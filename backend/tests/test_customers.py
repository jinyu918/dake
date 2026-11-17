"""客户接入使用合成群和资料，验证身份、失败及任务生命周期。"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from offline_http import offline_only

from dake.config import Settings
from dake.domain.models import Window
from dake.ingestion.client import ChatRecordsError
from dake.ingestion.directory import DirectoryClient
from dake.services.customers import CustomerSyncService, discover_groups, prepare_sync
from dake.jobs.runner import RunRunner
from test_ingestion import Opener, Response, record, UID
from test_ingestion_windows import START, END, window_payload
from test_runs import MemoryRuns


def test_directory_get_and_search_use_official_protocol():
    client = DirectoryClient()
    client._opener = Opener(Response({"code": 0, "data": {"uid": UID, "level": "L7", "rank": "synthetic"}}),
        Response({"code": 0, "data": {"uid": UID, "tam": "合成负责人", "tam_b": None}}),
        Response({"code": 0, "keyword": "合成群", "total": 1, "groups": [{"uid": UID, "group_number": "synthetic-group"}]}))
    assert client.level(UID)["level"] == "L7"
    assert client.tam(UID)["tam_b"] is None
    assert client.search("合成群") == [{"uid": UID, "group_id": "synthetic-group"}]
    assert [r.method for r in client._opener.calls] == ["GET", "GET", "POST"]
    assert client._opener.calls[0].full_url.endswith("/level?uid=fixture-customer")


@pytest.mark.parametrize("data", [{"uid": "other", "level": "L7", "rank": "x"},
    {"uid": UID, "level": "L11", "rank": "x"}, {"uid": UID, "rank": "x"}])
def test_directory_rejects_wrong_uid_unknown_level_and_missing_fields(data):
    client = DirectoryClient()
    client._opener = Opener(Response({"code": 0, "data": data}))
    with pytest.raises(ChatRecordsError): client.level(UID)


def test_search_incomplete_response_not_used():
    client = DirectoryClient()
    client._opener = Opener(Response({"code": 0, "keyword": "合成", "total": 1, "groups": []}))
    with pytest.raises(ChatRecordsError): client.search("合成")


def test_discovery_keeps_unknown_and_conflicting_groups_for_review():
    raw = window_payload((UID, [record("1")]), ("second", [record("2", group="group-b")]))
    raw["groups"][0]["uid"] = ""
    raw["groups"][1]["records"][0]["uid"] = "third"
    groups, count = discover_groups(raw, Window(START, END))
    assert count == 2
    assert all(g["status"] == "review" and g["uid"] is None for g in groups)
    assert groups[1]["candidate_uids"] == ["second", "third"]


def test_discovery_filters_end_minute_and_checks_counts():
    raw = window_payload((UID, [record(time="2026-11-08 09:15:01")]))
    assert discover_groups(raw, Window(START, END)) == ([], 0)
    raw["total"] += 1
    with pytest.raises(ValueError): discover_groups(raw, Window(START, END))


class Chats:
    """按窗口回显合成消息，不联网。"""
    def __init__(self, missing=False):
        self.missing = missing

    def fetch_window(self, start, end):
        raw = window_payload((UID, [record(time=start.strftime("%Y-%m-%d %H:%M:%S"))]), start=start, end=end)
        if self.missing:
            raw["groups"][0]["uid"] = ""
            raw["groups"][0]["records"][0]["uid"] = ""
        return raw


class Directory:
    """可控制资料失败和搜索归属的合成客户端。"""
    def __init__(self, *, fail=False, exact=True):
        self.fail, self.exact, self.calls = fail, exact, []

    def level(self, uid):
        self.calls.append(("level", uid))
        if self.fail: raise ChatRecordsError("合成失败")
        return {"level": "L6", "rank": "合成"}

    def tam(self, uid):
        self.calls.append(("tam", uid))
        return {"tam": "合成人员", "tam_b": None}

    def search(self, keyword):
        return [{"uid": UID, "group_id": "group-a" if self.exact else "unrelated-group"}]


class Saved:
    """只记录业务参数，生命周期测试委托既有内存任务仓库。"""
    def __init__(self, runs=None):
        self.calls, self.runs = [], runs

    def save_window(self, run_id, runner_id, window, groups, profiles, stats):
        self.calls.append(deepcopy((groups, profiles)))
        cycle = {**stats, "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
                 "new_customers": len(profiles), "refreshed_customers": 0, "new_groups": len(groups),
                 "review_groups": sum(g["status"] == "review" for g in groups)}
        if self.runs: self.runs.complete_window(run_id, runner_id, cycle)
        return cycle


@pytest.mark.parametrize("exact", [True, False])
def test_search_only_binds_identical_group_id(tmp_path, exact):
    saved = Saved()
    service = CustomerSyncService(replace(Settings(), data_dir=tmp_path), saved, Chats(True), Directory(exact=exact))
    service.sync_window(Window(START, END), "synthetic-run", "worker", 0)
    group = saved.calls[0][0][0]
    assert (group["status"] == "confirmed") is exact
    assert (group["uid"] == UID) is exact
    assert bool(saved.calls[0][1]) is exact


def test_profiles_only_requested_once_per_run_and_failures_preserved(tmp_path):
    saved, directory = Saved(), Directory(fail=True)
    service = CustomerSyncService(replace(Settings(), data_dir=tmp_path), saved, Chats(), directory)
    first = service.sync_window(Window(START, END), "synthetic-run", "worker", 0)
    service.sync_window(Window(END, END + timedelta(minutes=15)), "synthetic-run", "worker", 1)
    assert first["profile_errors"] == 1
    assert directory.calls == [("level", UID), ("tam", UID)]
    assert saved.calls[0][1][UID]["level"]["error"]
    assert saved.calls[1][1] == {}


@pytest.mark.parametrize("fail", [False, True])
def test_customer_runner_uses_persistent_queue_without_analysis(tmp_path, fail):
    settings, runs = replace(Settings(), data_dir=tmp_path), MemoryRuns()
    def factory(config):
        return CustomerSyncService(config, Saved(runs), Chats(), Directory(fail=fail))
    def forbidden(*args, **kwargs):
        pytest.fail("客户同步不得装配分析服务")
    runner = RunRunner(settings, runs, service_factory=forbidden, customer_factory=factory)
    request, total = prepare_sync({"start": START.isoformat(), "end": END.isoformat()})
    run = runs.create(request, total)
    runner.execute(runs.claim_next(runner.runner_id))
    result = runs.items[run["run_id"]]
    assert result["status"] == ("failed" if fail else "succeeded")
    assert result["completed_windows"] == 1


@pytest.mark.parametrize("values", [{"start": "bad", "end": "2026-11-08"},
    {"start": "2026-11-08", "end": "2026-11-10"}, {"start": END.isoformat(), "end": START.isoformat()}])
def test_sync_range_is_bounded(values):
    with pytest.raises(ValueError): prepare_sync(values)


def test_customer_http_contract_and_analysis_run_isolation(tmp_path, offline_only):
    from offline_http import TestClient
    from dake.api.app_factory import create_app
    from test_api import TestRuns, TestQueries, WakeOnly

    calls = []
    class Customers:
        def list_customers(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"items": [], "total": 0}
        def list_groups(self, *args, **kwargs): return {"items": [], "total": 0}
    settings = replace(Settings(), data_dir=tmp_path)
    runs, worker = TestRuns(), WakeOnly()
    app = create_app(settings, run_repository=runs, runner=worker, query_service=TestQueries(settings), customer_repository=Customers(), start_worker=False)
    with TestClient(app) as client:
        response = client.post("/api/v1/customers/syncs", json={"start": START.isoformat(), "end": END.isoformat()})
        assert response.status_code == 202
        rid = response.json()["run_id"]
        assert worker.notifications == 1
        assert client.get("/api/v1/customers/syncs/" + rid).json()["status"] == "queued"
        assert client.get("/api/v1/customers/syncs").json()["total"] == 1
        assert client.get("/api/v1/runs").json()["total"] == 0
        assert client.get("/api/v1/runs/" + rid).status_code == 404
        assert client.post("/api/v1/customers/syncs", json={"start": START.isoformat(), "end": END.isoformat()}).status_code == 409
        assert client.get("/api/v1/customers?segment=large&offset=2&limit=1&keyword=synthetic").status_code == 200
        assert calls[-1] == ((2, 1, "synthetic"), {"segment": "large"})
        for segment in ("all", "super_large", "non_large", "pending"):
            assert client.get("/api/v1/customers?segment=" + segment).status_code == 200
            assert calls[-1][1]["segment"] == segment
        assert client.get("/api/v1/customers?segment=invalid").status_code == 422
        assert client.get("/api/v1/customers?limit=101").status_code == 422
        assert client.get("/api/v1/customers/groups?uid=../bad").status_code == 400


def test_customer_sync_cycle_uses_configuration():
    request, total = prepare_sync({"start": START.isoformat(), "end": END.isoformat()}, timedelta(minutes=5))
    assert total == 3 and request["interval"] == "5m"
    with pytest.raises(ValueError):
        prepare_sync({"start": START.isoformat(), "end": END.isoformat()}, timedelta(0))
