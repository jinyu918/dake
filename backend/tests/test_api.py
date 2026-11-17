"""使用合成响应检验 HTTP 合约、安全边界与任务提交，不触碰真实聊天。"""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from offline_http import TestClient, offline_only

from dake.api.schemas import RunResponse
from dake.config import Settings
TEST_UID = "synthetic-http-customer"
TEST_STREAM = "stub-v1"
from dake.domain.models import EVENT_TYPES, Event, parse_time
from dake.api.app_factory import create_app
from dake.repositories.runs import RunConflict


def event_value():
    """构造独立合成事件，便于验证详情保留证据和历史字段。"""
    moment = parse_time("2026-11-08 09:00:00")
    return Event(event_id="synthetic-event", uid=TEST_UID, stream=TEST_STREAM,
                 type="排障", topic_key="synthetic-topic", start_time=moment, end_time=moment,
                 status="未知", severity="未知", summary="自行构造的测试事件",
                 created_on=moment, evidence=[dict(source="synthetic", message_id="1",
                 send_time=moment.isoformat(), excerpt="自行构造的测试证据")]).to_dict()


class TestQueries:
    __test__ = False

    def __init__(self, settings):
        self.settings = settings

    def config(self):
        return dict(uid=self.settings.uid, start="2026-11-08", end="2026-11-12", interval="15m",
                    context_lookback="3d", match_window="7d", ingest_lookback="2d", closed_grace="7d",
                    event_types=list(EVENT_TYPES), provider="stub", customers=[dict(uid=TEST_UID,
                    label="合成测试客户", message_count=1, event_count=1, stream=TEST_STREAM)])

    def events(self, uid=None, stream=None, event_type=None):
        if uid != TEST_UID or stream != TEST_STREAM:
            return {"items": [], "total": 0}
        item = event_value()
        items = [item] if event_type is None or item["type"] == event_type else []
        return {"items": items, "total": len(items)}

    def event(self, event_id, uid=None, stream=None):
        return next((e for e in self.events(uid, stream)["items"] if e["event_id"] == event_id), None)


class TestRuns:
    __test__ = False

    def __init__(self):
        self.items = {}

    def init_schema(self):
        pass

    @contextmanager
    def connection(self):
        yield self

    def _execute(self, *args):
        return [], 0

    def create(self, request, total_windows):
        if any(run["uid"] == request["uid"] and run["status"] in ("queued", "running") for run in self.items.values()):
            raise RunConflict()
        item = dict(run_id=str(uuid4()), uid=request["uid"], mode=request["mode"], status="queued",
                    request=request, total_windows=total_windows, completed_windows=0, current_window=None,
                    created_at="2026-11-13T10:00:00+08:00", started_at=None, finished_at=None,
                    error=None, cycles=[])
        self.items[item["run_id"]] = deepcopy(item)
        return item

    @staticmethod
    def in_scope(item, uid, stream):
        """内存接口替身只按明确范围或旧周期匹配，不生成任务。"""
        if uid is not None and item["uid"] != uid:
            return False
        if stream is None:
            return True
        recorded = item["request"].get("analysis_stream")
        if recorded is not None:
            return recorded == stream
        return any(cycle.get("uid") == uid and cycle.get("stream") == stream for cycle in item["cycles"])

    def list(self, uid=None, mode=None, stream=None):
        items = [deepcopy(item) for item in self.items.values()
                 if self.in_scope(item, uid, stream) and (mode is None or item["mode"] == mode)]
        return {"items": items, "total": len(items)}

    def get(self, run_id, *, uid=None, stream=None):
        item = self.items.get(run_id)
        return deepcopy(item) if item is not None and self.in_scope(item, uid, stream) else None


class WakeOnly:
    def __init__(self):
        self.notifications = 0

    def notify(self):
        self.notifications += 1


@pytest.fixture
def api(tmp_path):
    settings = replace(Settings(uid="synthetic-http-customer"), data_dir=tmp_path, mysql_password="synthetic-secret-never-return")
    repository, worker = TestRuns(), WakeOnly()
    application = create_app(settings, run_repository=repository, runner=worker,
                             query_service=TestQueries(settings), start_worker=False)
    with TestClient(application) as client:
        yield client, repository, worker, application


def test_openapi_has_named_operations_and_typed_models(api):
    client, _, _, _ = api
    schema = client.get("/openapi.json").json()
    operations = {item["operationId"] for path in schema["paths"].values() for item in path.values()}
    assert operations == {"health", "get_config", "list_events", "get_event", "get_event_changes", "list_runs", "get_run",
                          "submit_field_decision", "get_field_decision", "correct_same_round", "get_same_round_correction",
                          "list_customer_profiles", "list_customer_groups",
                          "create_customer_sync", "list_customer_syncs", "get_customer_sync", "list_tasks", "create_task",
                          "preview_task", "update_task", "task_action", "list_task_executions", "get_task_execution",
                          "list_sender_rules", "search_sender_candidates", "save_sender_rule"}
    assert {"ConfigResponse", "EventResponse", "EventRevision", "EvidenceResponse", "RunResponse", "CycleResponse"} <= set(schema["components"]["schemas"])
    assert "synthetic-secret-never-return" not in client.get("/api/v1/config").text


def test_openapi_response_defaults_are_required_but_request_defaults_remain_optional(api):
    client, _, _, _ = api
    schema = client.get("/openapi.json").json()
    models = schema["components"]["schemas"]
    assert {"assignees", "evidence", "attributes", "typed_attributes", "types", "type_evidence",
            "classification_evidence_status", "extensions", "updated_by", "review_flag", "closed_at"} <= set(models["EventResponse"]["required"])
    assert {"created", "updated", "review", "events", "processed", "ingested", "recovered_from_history", "counts_complete"} <= set(models["CycleResponse"]["required"])
    assert "cycles" in models["RunResponse"]["required"]
    request_schema = schema["paths"]["/api/v1/tasks"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    request_model = models[request_schema["$ref"].rsplit("/", 1)[-1]]
    assert not ({"mode", "uid", "start", "end", "interval", "offline"} & set(request_model.get("required", [])))
    assert client.post("/api/v1/runs", json={}).status_code == 405


def test_old_submission_is_removed(api):
    client, repository, worker, _ = api
    assert client.post("/api/v1/runs", json={}).status_code == 405
    assert not repository.items and worker.notifications == 0


def test_progress_reads_are_scoped_and_never_submit_or_wake_processing(api):
    """虚构跨天进度保留失败及已完成计数，错客户或范围不能读取正文。"""
    client, repository, worker, _ = api
    from dake.jobs.runner import prepare_request
    run = repository.create(*prepare_request(api[3].state.services.settings, {"interval": "1d"}))
    item = repository.items[run["run_id"]]
    item.update(status="failed", completed_windows=1,
                error="虚构校验失败：字段变化缺少依据",
                current_window={"start": "2038-06-02T09:00:00+08:00", "end": "2038-06-02T09:05:00+08:00"})
    item["request"]["analysis_stream"] = "synthetic-scope-a"
    for uid, stream in ((item["uid"], "synthetic-scope-b"), ("synthetic-other-customer", "synthetic-scope-a")):
        other = deepcopy(item)
        other.update(run_id=str(uuid4()), uid=uid)
        other["request"].update(uid=uid, analysis_stream=stream)
        repository.items[other["run_id"]] = other
    scope = {"uid": item["uid"], "stream": "synthetic-scope-a"}
    before = deepcopy(repository.items)
    notifications = worker.notifications
    listed = client.get("/api/v1/runs", params=scope).json()
    assert listed["total"] == 1 and listed["items"][0]["run_id"] == run["run_id"]
    detail = client.get(f"/api/v1/runs/{run['run_id']}", params=scope).json()
    assert detail["status"] == "failed" and detail["completed_windows"] == 1
    assert detail["current_window"] == item["current_window"] and detail["error"] == item["error"]
    for wrong in ({**scope, "uid": "synthetic-other-customer"}, {**scope, "stream": "synthetic-scope-b"}):
        assert client.get(f"/api/v1/runs/{run['run_id']}", params=wrong).status_code == 404
    for path in ("/api/v1/runs", f"/api/v1/runs/{run['run_id']}"):
        assert client.get(path, params={"stream": "synthetic-scope-a"}).status_code == 422
    assert repository.items == before and worker.notifications == notifications


def test_progress_legacy_cycles_do_not_return_foreign_scope_content(api):
    client, repository, _, _ = api
    from dake.jobs.runner import prepare_request
    run = repository.create(*prepare_request(api[3].state.services.settings, {"interval": "1d"}))
    item = repository.items[run["run_id"]]
    item["request"]["analysis_stream"] = None
    cycle = dict(uid=item["uid"], stream="synthetic-scope-a",
                 window={"start": run["request"]["start"], "end": run["request"]["end"]})
    item["cycles"] = [cycle, {**cycle, "stream": "synthetic-foreign-scope"},
                      {**cycle, "uid": "synthetic-foreign-customer"}]
    params = dict(uid=item["uid"], stream="synthetic-scope-a")
    detail = client.get(f"/api/v1/runs/{run['run_id']}", params=params)
    assert detail.status_code == 200
    assert len(detail.json()["cycles"]) == 1
    assert "synthetic-foreign" not in detail.text
    item["request"]["analysis_stream"] = "synthetic-explicit-other"
    assert client.get(f"/api/v1/runs/{run['run_id']}", params=params).status_code == 404


def test_progress_repository_filters_before_list_limit_without_database():
    from dake.repositories.runs import RunRepository

    statements = []

    class RecordingStore:
        def __init__(self, **kwargs):
            pass

        def _execute(self, sql, args):
            statements.append((sql, args))
            return ([{"total": 0}] if "COUNT(*)" in sql else []), 0

        def close(self):
            pass

    repository = RunRepository(Settings(), store_factory=RecordingStore)
    assert repository.list("synthetic-customer", "official", stream="synthetic-scope") == {"items": [], "total": 0}
    assert repository.get("synthetic-run", uid="synthetic-customer", stream="synthetic-scope") is None
    for sql, args in statements:
        assert "uid=%s" in sql and "$.analysis_stream" in sql
        assert "synthetic-customer" in args and "synthetic-scope" in args
        assert "synthetic-customer" not in sql and "synthetic-scope" not in sql
    assert statements[0][0].index("WHERE") < statements[0][0].index("LIMIT")
    assert "cycles" not in statements[0][0].split(" FROM ", 1)[0]
    with pytest.raises(ValueError, match="必须指定客户"):
        repository.list(stream="synthetic-scope")
    with pytest.raises(ValueError, match="必须指定客户"):
        repository.get("synthetic-run", stream="synthetic-scope")


@pytest.mark.parametrize("body", [
    {"mode": "official", "start": "2026-11-12", "end": "2026-11-08"},
    {"mode": "official", "start": "2026-01-01", "end": "2026-11-12"},
    {"mode": "official", "interval": "1s"},
    {"mode": "official", "interval": "999999999999d"},
])
def test_invalid_or_unbounded_runs_are_rejected(api, body):
    client, repository, _, _ = api
    assert client.post("/api/v1/runs", json=body).status_code == 405
    assert not repository.items


def test_event_queries_stay_scoped_and_removed_demo_routes_are_unavailable(api):
    client, repository, worker, _ = api
    params = {"uid": TEST_UID, "stream": TEST_STREAM}
    detail = client.get("/api/v1/events/synthetic-event", params=params)
    assert detail.status_code == 200 and detail.json()["evidence"][0]["message_id"] == "1"
    assert client.get("/api/v1/events/synthetic-event", params={"uid": "another-customer", "stream": TEST_STREAM}).status_code == 404
    assert client.get("/api/v1/demo/history").status_code == 404
    assert client.post("/api/v1/demo/sessions", json={}).status_code == 404
    assert client.post("/api/v1/runs", json={"mode": "demo"}).status_code == 405
    assert not repository.items and worker.notifications == 0


def test_json_and_same_origin_write_boundary_and_private_data(api):
    client, repository, _, _ = api
    assert client.post("/api/v1/runs", content='{}', headers={"content-type": "text/plain"}).status_code == 415
    assert client.post("/api/v1/runs", json={}, headers={"origin": "https://outside.invalid"}).status_code == 403
    assert not repository.items
    assert client.post("/api/v1/runs", json={}, headers={"origin": "http://testserver"}).status_code == 405
    assert client.get("/api/v1/config").headers["cache-control"] == "no-store"
    assert client.get("/data/raw/any.json").status_code == 404
    assert client.get("/health", headers={"host": "outside.invalid"}).status_code == 400


def test_validation_and_unexpected_errors_do_not_echo_request_or_exception(api):
    client, _, _, application = api
    response = client.post("/api/v1/tasks", json={"name": "合成任务", "api_key": "fake-secret-value"})
    assert response.status_code == 422 and "fake-secret-value" not in response.text
    application.state.services.queries.config = lambda: (_ for _ in ()).throw(RuntimeError("fake SQL and secret"))
    response = client.get("/api/v1/config")
    assert response.status_code == 503 and "secret" not in response.text and "SQL" not in response.text


def test_configured_lan_host_keeps_same_origin_boundary(tmp_path):
    settings = replace(Settings(uid="synthetic-http-customer"), data_dir=tmp_path,
                       api_allowed_hosts=("localhost", "127.0.0.1", "192.0.2.10"))
    repository, worker = TestRuns(), WakeOnly()
    application = create_app(settings, run_repository=repository, runner=worker,
                             query_service=TestQueries(settings), start_worker=False)
    with TestClient(application, base_url="http://192.0.2.10:8000") as client:
        assert client.get("/api/v1/config").status_code == 200
        assert client.get("/health", headers={"host": "outside.invalid"}).status_code == 400
        assert client.post("/api/v1/runs", json={},
                           headers={"origin": "http://outside.invalid"}).status_code == 403
        assert not repository.items
        assert client.post("/api/v1/runs", json={},
                           headers={"origin": "http://192.0.2.10:8000"}).status_code == 405
