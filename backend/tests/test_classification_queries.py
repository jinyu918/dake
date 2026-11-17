"""从正式查询到 HTTP 响应验证分类筛选与旧历史，数据全部自行构造。"""

from copy import deepcopy
from dataclasses import replace

import pytest
from offline_http import TestClient, offline_only

from classification_preview import RUN_ID, STREAM, UID, application


@pytest.fixture
def api(tmp_path):
    app, store = application(tmp_path)
    with TestClient(app) as client:
        yield client, store


@pytest.mark.parametrize("label,expected", [
    (None, {"multi", "other", "pending", "legacy", "empty"}),
    ("资源需求", {"multi", "other"}), ("商务需求", {"multi"}),
    ("其他", {"other"}), ("待分类", {"pending", "empty"}),
    ("排障", {"legacy"}), ("商机", set()), ("流失", set()), ("功能需求", set()),
])
def test_current_labels_filter_once_and_exclude_revoked_labels(api, label, expected):
    client, store = api
    before = deepcopy(store.events)
    response = client.get("/api/v1/events", params={"uid": UID, "stream": STREAM, **({"type": label} if label else {})})
    assert response.status_code == 200, response.text
    assert {item["event_id"] for item in response.json()["items"]} == expected
    assert response.json()["total"] == len(expected)
    assert store.events == before


def test_detail_preserves_label_attribute_and_revocation_evidence(api):
    client, _ = api
    result = client.get("/api/v1/events/multi", params={"uid": UID, "stream": STREAM})
    assert result.status_code == 200
    item = result.json()
    assert item["type_evidence"]["资源需求"][0]["evidence_ids"] == ["synthetic:resource"]
    assert item["type_evidence"]["商务需求"][0]["evidence_ids"] == ["synthetic:business"]
    assert item["typed_attributes"]["资源需求"]["目标总量与增减量"]["quote"] == "请将合成环境配额提高到2000次。"
    assert item["typed_attributes"]["商务需求"]["价格与金额口径"]["value"] is None
    assert item["removed_types"]["商机"][0]["evidence_ids"] == ["synthetic:correction"]
    assert item["review_flag"] == "报价币种待核实"
    assert item["updated_by"][0]["snapshot"]["removed_types"] == {}


def test_classification_only_revision_is_returned_by_event_and_cycle_queries(api, tmp_path):
    from test_classification_replay import day_window, msg, output_candidate, replay

    client, store = api
    first = replace(msg("first", 1, "虚构A下载失败，请排查"), uid=UID)
    second = replace(msg("second", 2, "虚构A下载仍失败，排障标签继续适用"), uid=UID)
    service, replay_store, provider, _ = replay(tmp_path / "replay", [first, second])
    provider.name = STREAM
    provider.mapping[first.message_id] = output_candidate(first)
    created = service.run_window(UID, day_window(1))
    event_id = created.created[0]
    provider.mapping[second.message_id] = output_candidate(
        second, action="update", event_id=event_id, old_message=first,
        summary=replay_store.events[event_id].summary, changes=[])
    updated = service.run_window(UID, day_window(2))
    assert replay_store.events[event_id].updated_by[-1]["change_kind"] == "classification"
    store.events = [replay_store.events[event_id].to_dict()]
    store.messages = [first, second]
    run = client.app.state.services.runs.run
    run["cycles"] = [created.to_dict(), updated.to_dict()]

    listed = client.get("/api/v1/events", params={"uid": UID, "stream": STREAM})
    detail = client.get(f"/api/v1/events/{event_id}", params={"uid": UID, "stream": STREAM})
    cycle = client.get(f"/api/v1/runs/{RUN_ID}", params={"uid": UID, "stream": STREAM})
    changes = client.get(f"/api/v1/events/{event_id}/changes", params={"uid": UID, "stream": STREAM})

    assert listed.status_code == detail.status_code == cycle.status_code == changes.status_code == 200
    assert listed.json()["items"][0]["updated_by"][-1]["change_kind"] == "classification"
    assert detail.json()["updated_by"][-1]["change_kind"] == "classification"
    assert cycle.json()["cycles"][1]["events"][0]["updated_by"][-1]["change_kind"] == "classification"
    assert changes.json()["revisions"][-1]["change_kind"] == "classification"


def test_legacy_and_null_classification_remain_unrecorded_in_current_and_cycle_details(api):
    client, _ = api
    for key, labels, status in (("legacy", ["排障"], "已分类"), ("empty", [], "待分类")):
        response = client.get(f"/api/v1/events/{key}", params={"uid": UID, "stream": STREAM})
        assert response.status_code == 200, response.text
        item = response.json()
        assert item["types"] == labels and item["classification_status"] == status
        assert item["type_evidence"] == {} and item["classification_evidence_status"] == "未记录"
        assert item["typed_attributes"] == {} and item["removed_types"] == {}
    response = client.get(f"/api/v1/runs/{RUN_ID}", params={"uid": UID, "stream": STREAM})
    assert response.status_code == 200, response.text
    old, current = response.json()["cycles"]
    assert "商机" in old["events"][0]["types"]
    assert "商机" not in current["events"][0]["types"]
    assert current["events"][0]["removed_types"]["商机"][0]["quote"] == "之前采购意向说错，本次没有采购计划。"
    assert old["events"][3]["type_evidence"] == {}
    assert old["events"][4]["classification_status"] == "待分类"
    assert old["events"][4]["types"] == []


def test_filter_uses_current_label_set_over_stale_primary(api):
    client, store = api
    store.events = [store.events[0]]
    store.events[0]["type"] = "商机"
    result = client.get("/api/v1/events", params={"uid": UID, "stream": STREAM, "type": "商机"})
    assert result.status_code == 200
    assert result.json()["total"] == 0


def test_legacy_attribute_evidence_is_preserved_in_current_and_cycle_details(api):
    client, store = api
    legacy = {"症状与对照表现": {"value": "合成旧症状", "quote": "合成旧症状依据",
                                  "evidence_ids": ["synthetic:legacy"]}}
    store.events[3]["attribute_evidence"] = deepcopy(legacy)
    client.app.state.services.runs.run["cycles"][0]["events"][3]["attribute_evidence"] = deepcopy(legacy)
    current = client.get("/api/v1/events/legacy", params={"uid": UID, "stream": STREAM}).json()
    snapshot = client.get(f"/api/v1/runs/{RUN_ID}", params={"uid": UID, "stream": STREAM}).json()["cycles"][0]["events"][3]
    for item in (current, snapshot):
        assert item["typed_attributes"] == {"排障": legacy}
        assert item["type_evidence"] == {} and item["classification_evidence_status"] == "未记录"


def test_invalid_filter_and_foreign_scope_do_not_return_records(api):
    client, _ = api
    assert client.get("/api/v1/events", params={"type": "随意类型"}).status_code in (400, 422)
    assert client.get("/api/v1/events", params={"uid": "synthetic-other", "stream": STREAM}).json()["total"] == 0
    assert client.get("/api/v1/events/multi", params={"uid": "synthetic-other", "stream": STREAM}).status_code == 404
    assert client.get("/api/v1/events", params={"uid": UID, "stream": "synthetic-other"}).status_code == 400


def test_openapi_exposes_filter_and_classification_fields(api):
    client, _ = api
    schema = client.get("/openapi.json").json()
    parameter = next(p for p in schema["paths"]["/api/v1/events"]["get"]["parameters"] if p["name"] == "type")
    assert parameter["schema"]["anyOf"][0]["type"] == "string"
    assert parameter["required"] is False
    for name in ("EventResponse", "EventSnapshot"):
        assert {"types", "type_evidence", "removed_types", "typed_attributes", "classification_status"} <= set(schema["components"]["schemas"][name]["required"])
