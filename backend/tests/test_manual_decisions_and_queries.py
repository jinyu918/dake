"""验证人工纠偏与锁定服务、多维筛选排序查询及 OpenAPI 契约一致性。"""

from dataclasses import replace
from datetime import timedelta

import pytest

from dake.api.app_factory import create_app
from dake.api.schemas import EventResponse, ManualDecisionResponse
from dake.config import Settings
from dake.domain.manual_controls import ManualDecisionRequest, ManualFieldAction
from dake.domain.models import Candidate, Event, Message, Window, parse_time
from dake.services.manual_decisions import (
    ManualDecisionConflict,
    ManualDecisionNotFound,
    ManualDecisionService,
)
from dake.services.processing import IncrementalService
from dake.services.queries import QueryService
from fakes import FakeProvider, FakeSource, MemoryStore


UID = "1383603926"


class AttributionProvider(FakeProvider):
    """支持归属协议与历史上下文注入的内存 Provider。"""

    def configure_history_context(self, background):
        self.background = background


class BoundStoreFactory:
    """将单实例 MemoryStore 包装为可重复调用但不销毁状态的连接工厂。"""

    def __init__(self, store: MemoryStore):
        self.store = store

    def __call__(self, **_kwargs):
        return self.store


def _msg(mid: str, at: str, content: str) -> Message:
    return Message(uid=UID, message_id=mid, sent_at=parse_time(at), content=content, conversation_id="grp-1")


def _win(at: str) -> Window:
    start = parse_time(at)
    return Window(start, start + timedelta(minutes=15))


def test_manual_decision_concurrency_idempotency_conflict_and_cross_window_retrieval(tmp_path):
    """人工裁定提交后，下一窗口仍能通过 HistoryRetriever 认回同一事件并遵守人工锁。"""
    m1 = _msg("m1", "2026-11-10 09:00:00", "合成消息：客户咨询存储网关偶发延迟，具体范围待确认。")
    m2 = _msg("m2", "2026-11-10 09:20:00", "合成消息：存储网关延迟已定位为带宽打满，支撑建议扩容并继续观察。")

    provider = AttributionProvider(name="stub-v1")
    settings = replace(Settings(), data_dir=tmp_path, uid=UID, provider="stub")
    store = MemoryStore()
    source = FakeSource([m1])
    pipeline = IncrementalService(store, source, provider, settings)

    provider.mapping["m1"] = Candidate(
        type="待分类",
        types=[],
        classification_status="待分类",
        topic_key="gateway:latency",
        summary="客户咨询存储网关偶发延迟",
        evidence_ids=[m1.key],
        decision="new",
        decision_reason="首次反馈存储网关延迟",
        basis=[{"message_id": m1.key, "quote": m1.content}],
        changes=[
            {
                "kind": "progress",
                "text": "客户咨询存储网关偶发延迟",
                "replaces": None,
                "evidence": [{"message_id": m1.key, "quote": m1.content}],
            }
        ],
    )
    r1 = pipeline.run_window(UID, _win("2026-11-10 09:00:00"))
    event_id = r1.created[0]
    w1_snapshot = r1.events[0]
    assert store.events[event_id].version == 1

    manual = ManualDecisionService(settings, store_factory=BoundStoreFactory(store))
    req = ManualDecisionRequest(
        uid=UID,
        stream="stub-v1",
        expected_version=1,
        operation_id="op-set-initial-001",
        operator="duty-alice",
        reason="人工核实属排障事项并锁定等待客户抓包",
        actions=[
            ManualFieldAction(field="types", action="set", types=["排障"]),
            ManualFieldAction(field="status", action="set", status="等待中", waiting_reason="等待客户"),
            ManualFieldAction(field="priority", action="set", priority="P2"),
            ManualFieldAction(field="assignees", action="set", assignees=["duty-alice"]),
        ],
    )
    submitted = manual.submit(event_id, req)
    validated = ManualDecisionResponse.model_validate(submitted)
    assert validated.idempotent_replay is False
    assert validated.event.version == 2
    assert validated.event.types == ["排障"]
    assert validated.event.lifecycle_status == "等待中"
    assert validated.event.waiting_reason == "等待客户"
    assert validated.event.priority == "P2"
    assert validated.event.assignees == ["duty-alice"]
    assert validated.event.manual_controls["status"].locked is True

    # 相同 operation_id 重试幂等返回，不重复递增版本或追加修订。
    replayed = manual.submit(event_id, req)
    assert replayed["idempotent_replay"] is True
    assert store.events[event_id].version == 2
    assert len(store.events[event_id].updated_by) == 2

    # 按操作编号核实收据成功；不存在的操作编号报 ManualDecisionNotFound。
    queried = manual.operation(event_id, "op-set-initial-001", uid=UID, stream="stub-v1")
    assert queried["operation"]["version"] == 2
    with pytest.raises(ManualDecisionNotFound):
        manual.operation(event_id, "op-missing", uid=UID, stream="stub-v1")

    # 相同 operation_id 但篡改内容 -> 409 ManualDecisionConflict
    with pytest.raises(ManualDecisionConflict):
        manual.submit(event_id, req.model_copy(update={"reason": "不同裁定内容"}))

    # 过期 expected_version -> 409 ManualDecisionConflict
    with pytest.raises(ManualDecisionConflict):
        manual.submit(
            event_id,
            ManualDecisionRequest(
                uid=UID,
                stream="stub-v1",
                expected_version=1,
                operation_id="op-stale-002",
                operator="duty-bob",
                reason="旧版本并发写入",
                actions=[ManualFieldAction(field="priority", action="unlock")],
            ),
        )

    # 第二窗口增量运行：验证 HistoryRetriever 能正常认回被人工修改过的事件，且自动结果不覆盖人工锁定的状态/分类！
    source.messages.append(m2)
    provider.mapping["m2"] = Candidate(
        type="排障",
        types=["排障", "资源需求"],
        classification_status="已分类",
        type_evidence={
            "排障": [{"evidence_ids": [m2.key], "quote": "延迟已定位为带宽打满"}],
            "资源需求": [{"evidence_ids": [m2.key], "quote": "支撑建议扩容"}],
        },
        topic_key="gateway:latency",
        summary="存储网关延迟已定位为带宽打满",
        evidence_ids=[m2.key],
        decision="update",
        referenced_event_id=event_id,
        decision_reason="延续同一存储网关延迟排查",
        basis=[
            {"message_id": m1.key, "quote": m1.content},
            {"message_id": m2.key, "quote": m2.content},
        ],
        changes=[
            {
                "kind": "progress",
                "text": "定位为带宽打满并建议扩容",
                "replaces": None,
                "evidence": [{"message_id": m2.key, "quote": m2.content}],
            }
        ],
        status_proposal={
            "status": "跟进中",
            "waiting_reason": None,
            "resolution": None,
            "evidence_ids": [m2.key],
            "quote": "继续观察",
            "reason": "支撑继续跟进观察",
        },
    )
    r2 = pipeline.run_window(UID, _win("2026-11-10 09:15:00"))
    assert r2.updated == [event_id]
    ev_after_w2 = store.events[event_id]
    assert ev_after_w2.version == 3
    # 锁定的分类和状态保持人工值，同时挂出锁定冲突待核对
    assert ev_after_w2.types == ["排障"]
    assert ev_after_w2.lifecycle_status == "等待中"
    assert ev_after_w2.manual_controls["types"]["pending_conflict"]["proposed_value"] == ["排障", "资源需求"]
    assert ev_after_w2.manual_controls["status"]["pending_conflict"]["proposed_value"]["status"] == "跟进中"
    assert ev_after_w2.review_flag and len(ev_after_w2.review_flag) <= 120

    # 重放第一窗口历史快照时，不得混入后续人工裁定或第二窗口进展。
    replay_w1 = pipeline.run_window(UID, _win("2026-11-10 09:00:00"))
    assert replay_w1.events[0]["types"] == w1_snapshot["types"] == []

    # 人工采纳 types 冲突、驳回 status 冲突、解锁 priority：待核对提示应全部清零。
    resolved = manual.submit(
        event_id,
        ManualDecisionRequest(
            uid=UID,
            stream="stub-v1",
            expected_version=3,
            operation_id="op-resolve-003",
            operator="duty-alice",
            reason="采纳扩容双标签，维持等待客户抓包状态并解锁优先级",
            actions=[
                ManualFieldAction(field="types", action="adopt_conflict", locked=False),
                ManualFieldAction(field="status", action="dismiss_conflict"),
                ManualFieldAction(field="priority", action="unlock"),
            ],
        ),
    )
    ev_final = EventResponse.model_validate(resolved["event"])
    assert ev_final.types == ["排障", "资源需求"]
    assert ev_final.manual_controls["types"].locked is False
    assert ev_final.lifecycle_status == "等待中"
    assert ev_final.manual_controls["status"].pending_conflict is None
    assert ev_final.manual_controls["priority"].locked is False
    assert ev_final.review_flag is None


def test_query_service_filters_multilabel_status_priority_and_sorts_by_business_urgency(tmp_path):
    """验证 QueryService 支持多标签、状态、优先级过滤，以及按未完结 + P0~P3 + L10/L9 + 时间排序。"""
    store = MemoryStore()
    store._execute = lambda *_args, **_kwargs: ([], 0)
    store.profiles[UID] = {"level": "L10", "level_state": "fresh"}
    settings = replace(Settings(), data_dir=tmp_path, uid=UID, provider="stub")

    t1 = parse_time("2026-11-10 09:00:00")
    t2 = parse_time("2026-11-10 10:00:00")
    t3 = parse_time("2026-11-10 11:00:00")
    events = [
        Event(
            event_id="ev-resolved-p0",
            uid=UID,
            stream="stub-v1",
            type="排障",
            types=["排障"],
            topic_key="k1",
            start_time=t1,
            end_time=t3,
            status="已解决",
            lifecycle_status="已解决",
            resolution="客户确认恢复",
            severity="P0",
            priority="P0",
            base_priority="P0",
            summary="已解决故障",
            created_on=t1,
        ),
        Event(
            event_id="ev-active-p2-boosted",
            uid=UID,
            stream="stub-v1",
            type="商机",
            types=["商机", "商务需求"],
            topic_key="k2",
            start_time=t1,
            end_time=t1,
            status="等待中",
            lifecycle_status="等待中",
            waiting_reason="等待内部支撑",
            severity="P2",
            priority="P2",
            base_priority="P2",
            summary="双标签商务商机",
            created_on=t1,
        ),
        Event(
            event_id="ev-active-p0",
            uid=UID,
            stream="stub-v1",
            type="排障",
            types=["排障"],
            topic_key="k3",
            start_time=t2,
            end_time=t2,
            status="跟进中",
            lifecycle_status="跟进中",
            severity="P0",
            priority="P0",
            base_priority="P0",
            summary="未解决核心中断",
            created_on=t2,
        ),
    ]
    with store.transaction(UID):
        for ev in events:
            store.save_event(ev)

    queries = QueryService(settings, store_factory=BoundStoreFactory(store))
    listed = queries.events(UID, "stub-v1")
    # ev-active-p2-boosted 因 L10 fresh 自动展示为 P1；排序应为：未解决 P0 -> 未解决 P1 -> 已解决 P0
    assert [item["event_id"] for item in listed["items"]] == [
        "ev-active-p0",
        "ev-active-p2-boosted",
        "ev-resolved-p0",
    ]
    assert listed["items"][1]["priority"] == "P1"

    # 按第二标签「商务需求」筛选应命中双标签事项
    by_biz = queries.events(UID, "stub-v1", event_type="商务需求")
    assert [item["event_id"] for item in by_biz["items"]] == ["ev-active-p2-boosted"]

    # 按生命周期状态与提升后的优先级筛选
    by_status = queries.events(UID, "stub-v1", status="等待中", priority="P1")
    assert [item["event_id"] for item in by_status["items"]] == ["ev-active-p2-boosted"]

    with pytest.raises(ValueError):
        queries.events(UID, "stub-v1", status="未知非法状态")
    with pytest.raises(ValueError):
        queries.events(UID, "stub-v1", priority="P9")


def test_openapi_includes_manual_decisions_and_preserves_request_optionality(tmp_path):
    """验证 OpenAPI 导出包含人工裁定端点与新字段，且请求可选字段不被强制标记为 required。"""
    settings = replace(Settings(), data_dir=tmp_path, uid=UID)
    app = create_app(settings, start_worker=False)
    schema = app.openapi()
    operations = {item["operationId"] for path in schema["paths"].values() for item in path.values()}
    assert {"submit_manual_decision", "get_manual_decision"} <= operations
    models = schema["components"]["schemas"]
    assert {
        "types",
        "typed_attributes",
        "lifecycle_status",
        "waiting_reason",
        "resolution",
        "priority",
        "base_priority",
        "version",
        "manual_controls",
    } <= set(models["EventResponse"]["required"])
    action_model = models["ManualFieldAction"]
    assert set(action_model["required"]) == {"field", "action"}


def test_http_routes_same_origin_conflict_and_filter_contracts(tmp_path):
    """通过真实 FastAPI ASGI 栈验证人工裁定 HTTP 状态码（200/400/403/404/409/415/422）与列表筛选参数。"""
    import asyncio
    import httpx

    store = MemoryStore()
    store._execute = lambda *_args, **_kwargs: ([], 0)
    t1 = parse_time("2026-11-10 09:00:00")
    ev = Event(
        event_id="http-ev-1",
        uid=UID,
        stream="stub-v1",
        type="排障",
        types=["排障"],
        topic_key="http:k1",
        start_time=t1,
        end_time=t1,
        status="跟进中",
        lifecycle_status="跟进中",
        severity="P2",
        priority="P2",
        base_priority="P2",
        summary="HTTP 路由契约验证事件",
        created_on=t1,
    )
    with store.transaction(UID):
        store.save_event(ev)

    settings = replace(Settings(), data_dir=tmp_path, uid=UID, provider="stub")
    factory = BoundStoreFactory(store)
    app = create_app(
        settings,
        store_factory=factory,
        query_service=QueryService(settings, store_factory=factory),
        manual_decision_service=ManualDecisionService(settings, store_factory=factory),
        start_worker=False,
    )

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            # 1. 非 JSON Content-Type 拒绝 (415)
            r415 = await client.post(
                "/api/v1/events/http-ev-1/manual-decisions",
                content="{}",
                headers={"content-type": "text/plain"},
            )
            assert r415.status_code == 415

            # 2. 跨源 Origin 拒绝 (403)
            r403 = await client.post(
                "/api/v1/events/http-ev-1/manual-decisions",
                json={},
                headers={"origin": "https://evil.example"},
            )
            assert r403.status_code == 403

            # 3. 同源有效提交 -> 200（直接构造且无 updated_by 的旧事件初始 version 为 0）
            payload = {
                "uid": UID,
                "stream": "stub-v1",
                "expected_version": 0,
                "operation_id": "http-op-1",
                "operator": "duty-http",
                "reason": "HTTP 测试升级为 P1 并切等待客户",
                "actions": [
                    {"field": "priority", "action": "set", "priority": "P1"},
                    {
                        "field": "status",
                        "action": "set",
                        "status": "等待中",
                        "waiting_reason": "等待客户",
                    },
                ],
            }
            r200 = await client.post(
                "/api/v1/events/http-ev-1/manual-decisions",
                json=payload,
                headers={"origin": "http://127.0.0.1:8000"},
            )
            assert r200.status_code == 200
            body = r200.json()
            assert body["event"]["version"] == 1
            assert body["event"]["priority"] == "P1"
            assert body["event"]["lifecycle_status"] == "等待中"

            # 4. 旧版本并发冲突 -> 409
            r409 = await client.post(
                "/api/v1/events/http-ev-1/manual-decisions",
                json={**payload, "operation_id": "http-op-stale"},
            )
            assert r409.status_code == 409

            # 5. 查询已提交操作 -> 200；不存在操作 -> 404
            r_get = await client.get(
                "/api/v1/events/http-ev-1/manual-decisions/http-op-1",
                params={"uid": UID, "stream": "stub-v1"},
            )
            assert r_get.status_code == 200 and r_get.json()["operation"]["version"] == 1
            r404 = await client.get(
                "/api/v1/events/http-ev-1/manual-decisions/http-op-missing",
                params={"uid": UID, "stream": "stub-v1"},
            )
            assert r404.status_code == 404

            # 6. 列表筛选参数 -> 200 与非法枚举 -> 400
            r_list = await client.get(
                "/api/v1/events",
                params={"uid": UID, "stream": "stub-v1", "status": "等待中", "priority": "P1"},
            )
            assert r_list.status_code == 200 and r_list.json()["total"] == 1
            r400 = await client.get(
                "/api/v1/events",
                params={"uid": UID, "stream": "stub-v1", "priority": "INVALID"},
            )
            assert r400.status_code == 400

    asyncio.run(exercise())


@pytest.mark.mysql
def test_mysql_persistence_roundtrip_manual_lock_and_review_flag_cap(tmp_path):
    """实库验证 MySQL 8.4 存取多标签、超长待核对截断、人工裁定乐观锁与 JSON_EXTRACT 历史检索快照。"""
    from test_store import mysql_store as mysql_fixture

    gen = mysql_fixture.__wrapped__()
    store, uid, _other, options = next(gen)

    try:
        settings = replace(
            Settings(),
            data_dir=tmp_path,
            uid=uid,
            provider="stub",
            mysql_host=options["host"],
            mysql_port=options["port"],
            mysql_user=options["user"],
            mysql_password=options["password"],
            mysql_database=options["database"],
        )
        m1 = Message(
            uid=uid,
            message_id="mysql-m1",
            sent_at=parse_time("2026-11-10 09:05:00"),
            content="合成实库消息：客户反馈对象存储上传 503，同时询问扩容 100TB 存储包报价。",
            conversation_id="grp-mysql",
        )
        provider = AttributionProvider(name="stub-v1")
        provider.mapping["mysql-m1"] = Candidate(
            type="排障",
            types=["排障", "商机", "商务需求"],
            classification_status="已分类",
            type_evidence={
                "排障": [{"evidence_ids": [m1.key], "quote": "对象存储上传 503"}],
                "商机": [{"evidence_ids": [m1.key], "quote": "扩容 100TB 存储包"}],
                "商务需求": [{"evidence_ids": [m1.key], "quote": "存储包报价"}],
            },
            classification_conflicts=[
                "排障.发生及影响范围存在多处描述待核对",
                "商机.计划规模存在两版数字待核对",
                "商务需求.价格与金额口径存在含税与未税差异待核对",
            ],
            topic_key="kodo:upload-and-quote",
            summary="客户反馈对象存储上传 503 并咨询 100TB 扩容报价",
            evidence_ids=[m1.key],
            decision="new",
            decision_reason="首次出现该实库合成事项",
            basis=[{"message_id": m1.key, "quote": m1.content}],
            changes=[
                {
                    "kind": "progress",
                    "text": "客户反馈对象存储上传 503 并咨询 100TB 扩容报价",
                    "replaces": None,
                    "evidence": [{"message_id": m1.key, "quote": m1.content}],
                }
            ],
            status_proposal={
                "status": "跟进中",
                "waiting_reason": None,
                "resolution": None,
                "evidence_ids": [m1.key],
                "quote": "上传 503",
                "reason": "排障与报价同步跟进中",
            },
            priority_proposal={
                "impact": "high",
                "urgency": "soon",
                "evidence_ids": [m1.key],
                "quote": "上传 503",
                "reason": "核心上传受阻",
            },
        )
        pipeline = IncrementalService(store, FakeSource([m1]), provider, settings)
        r1 = pipeline.run_window(uid, _win("2026-11-10 09:00:00"))
        event_id = r1.created[0]

        # 验证超长多条待核对项被安全截断并成功写入 MySQL VARCHAR(128) 列
        loaded = store.list_events(uid, "stub-v1")
        assert len(loaded) == 1
        assert loaded[0].review_flag is not None and len(loaded[0].review_flag) <= 120

        # 验证实库人工裁定事务与 JSON_EXTRACT 历史快照读取兼容
        manual = ManualDecisionService(settings)
        res = manual.submit(
            event_id,
            ManualDecisionRequest(
                uid=uid,
                stream="stub-v1",
                expected_version=1,
                operation_id="mysql-op-001",
                operator="mysql-tester",
                reason="实库人工锁定等待内部支撑",
                actions=[
                    ManualFieldAction(
                        field="status",
                        action="set",
                        status="等待中",
                        waiting_reason="等待内部支撑",
                    )
                ],
            ),
        )
        assert res["event"]["version"] == 2
        assert res["event"]["lifecycle_status"] == "等待中"

        heads = store.history_event_heads(uid, "stub-v1", parse_time("2026-11-10 09:30:00"), limit=10)
        assert len(heads) == 1
        snaps = store.history_event_snapshots(uid, "stub-v1", heads)
        assert len(snaps) == 1 and snaps[0].lifecycle_status == "等待中"

        queries = QueryService(settings)
        filtered = queries.events(uid, "stub-v1", event_type="商务需求", status="等待中", priority="P1")
        assert [item["event_id"] for item in filtered["items"]] == [event_id]
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_saved_emotion_priority_survives_listing_filter_and_detail(tmp_path):
    store = MemoryStore()
    store._execute = lambda *_args, **_kwargs: ([], 0)
    store.profiles[UID] = dict(level="L3", level_state="fresh")
    settings = replace(Settings(), data_dir=tmp_path, uid=UID, provider="stub")
    start = parse_time("2026-11-10 09:00:00")
    event = Event(event_id="synthetic-emotion-p1", uid=UID, stream="stub-v1", type="排障", topic_key="synthetic",
                  start_time=start, end_time=start, created_on=start, summary="合成局部降级及情绪升级",
                  status="跟进中", severity="P1", priority="P1", base_priority="P2",
                  priority_detail=dict(rule_version="item-priority-v1", priority="P1", pre_timer_priority="P1",
                                       customer_priority="P2", customer_level="L3", level_state="fresh"))
    with store.transaction(UID):
        store.save_event(event)
    queries = QueryService(settings, store_factory=BoundStoreFactory(store))
    assert queries.events(UID, "stub-v1")["items"][0]["priority"] == "P1"
    assert queries.events(UID, "stub-v1", priority="P2")["items"] == []
    item, = queries.events(UID, "stub-v1", priority="P1")["items"]
    assert item == queries.event(event.event_id, UID, "stub-v1")


@pytest.mark.parametrize("enabled", [False, True])
def test_alert_preview_http_returns_only_preview_fields_in_requested_scope(tmp_path, monkeypatch, enabled):
    import asyncio
    import httpx

    settings = replace(Settings(), data_dir=tmp_path, uid=UID,
                       synthetic_alert_source="local-fixture" if enabled else None)
    store = MemoryStore()
    calls = []

    def previews(source, uid, limit):
        calls.append((source, uid, limit))
        return [dict(source=source, alert_id="fixture", round_id="r1", state="已恢复", priority="P1",
                     queue="值班或 TAM", pending=False, members=[uid], affected_uids=[uid], reminders=[])]

    monkeypatch.setattr(store, "list_alert_previews", previews, raising=False)
    app = create_app(settings, store_factory=BoundStoreFactory(store), start_worker=False)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/api/v1/alert-previews", params=dict(uid=UID, limit=3))
            assert response.status_code == 200
            items = response.json()["items"]
            if enabled:
                row, = items
                assert row["state"] == "已恢复" and row["priority"] == "P1"
                assert not {"members", "affected_uids"}.intersection(row)
            else:
                assert items == []
            assert (await client.get("/api/v1/alert-previews", params=dict(limit=201))).status_code == 422

    asyncio.run(exercise())
    assert calls == ([("local-fixture", UID, 3)] if enabled else [])
