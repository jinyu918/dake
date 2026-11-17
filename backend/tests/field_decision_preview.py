"""第五阶段合成验收入口；仅测试内存仓库，复用正式归属、查询及裁定服务。"""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from classification_preview import NoExecutions, SyntheticRuns, SyntheticStore
from dake.api.app_factory import create_app
from dake.analysis.field_changes import field_key
from dake.services.queries import QueryService
from test_classification_replay import UID, day_window, msg, ref
from test_field_decisions import RESOURCE, change, proposal, setup_replay


RUN_ID = "00000000-0000-4000-8000-000000000005"
STREAM = "stub-v1"


class PreviewRuns(SyntheticRuns):
    def list(self, uid=None, mode=None, stream=None):
        items = [deepcopy(self.run)] if uid in (None, UID) and stream in (None, STREAM) else []
        return dict(items=items, total=len(items))

    def get(self, run_id, *, uid=None, stream=None):
        return deepcopy(self.run) if run_id == RUN_ID and uid in (None, UID) and stream in (None, STREAM) else None


def application(directory):
    first = msg("a36-request", 1, "合成验收申请R目标2000，请继续办理")
    anchor = msg("a36-anchor", 1, "合成验收申请R已经受理", hour=8)
    opening = proposal(first, [change(first)])
    opening["evidence_ids"].append(anchor.key)
    opening["changes"].append(dict(kind="progress", text=anchor.content, evidence=[ref(anchor)], replaces=None))
    service, store, provider, source = setup_replay(Path(directory), [first, anchor], {first.message_id: opening})
    provider.name = STREAM
    initial = service.run_window(UID, day_window(1))
    event_id = initial.created[0]
    store._execute = lambda sql, args=(): ([dict(uid=UID)], 0) if sql.startswith("SELECT DISTINCT uid") else SyntheticStore(
        [event.to_dict() for event in store.events.values()], list(store.messages.values()))._execute(sql, args)
    run = dict(run_id=RUN_ID, uid=UID, mode="official", status="succeeded", created_at=day_window(1).end.isoformat(), request=dict(
        mode="official", uid=UID, analysis_stream=STREAM, start=service.settings.start.isoformat(),
        end=service.settings.end.isoformat(), interval="1d", offline=True, context_lookback="20d", match_window="7d",
        closed_grace="7d", ingest_lookback="2d", default_status="未知", default_severity="未知", closed_statuses=[], provider="stub"),
        total_windows=1, completed_windows=1, cycles=[initial.to_dict()])
    runs = PreviewRuns(run, store)
    queries = QueryService(service.settings, store_factory=lambda **kwargs: store, attempts=NoExecutions())
    app = create_app(service.settings, query_service=queries, run_repository=runs, runner=object(), start_worker=False)

    @app.post("/synthetic/revise", include_in_schema=False)
    def revise():
        state = store.events[event_id].field_states[field_key(RESOURCE)]
        identity = state["adopted_ids"][0]
        revised = replace(first, content="合成验收申请R目标200，之前多写一个零；本次办理继续")
        source.messages = [revised, anchor]
        provider.mapping[first.message_id] = proposal(revised,
            [change(revised, value="200", intent="correction", replaces=[identity])],
            action="update", event_id=event_id, old_message=anchor,
            changes=[dict(kind="progress", text="本次办理继续", evidence=[ref(revised, "本次办理继续")], replaces=None)])
        return service.run_window(UID, day_window(2)).to_dict()

    @app.post("/synthetic/reset", include_in_schema=False)
    def reset():
        store.events = {event_id: deepcopy(initial_event)}
        store.messages = deepcopy(initial_messages)
        store.processed = deepcopy(initial_processed)
        store.attributions = deepcopy(initial_attributions)
        source.messages = [first, anchor]
        provider.mapping = {first.message_id: opening}
        return dict(event_id=event_id, uid=UID, stream=STREAM)

    initial_event = deepcopy(store.events[event_id])
    initial_messages = deepcopy(store.messages)
    initial_processed = deepcopy(store.processed)
    initial_attributions = deepcopy(store.attributions)
    return app


def main():
    import argparse
    import uvicorn
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    parser = argparse.ArgumentParser(description="仅自造数据，不加载 .env、数据库或真实模型")
    parser.add_argument("--http-port", required=True, type=int)
    parser.add_argument("--web-dist", required=True, type=Path)
    args = parser.parse_args()
    if args.http_port in (3306, 3307, 8000, 8001) or not 1024 <= args.http_port <= 65535:
        parser.error("须选择独立本地端口")
    dist = args.web_dist.resolve()
    if not (dist / "index.html").is_file():
        parser.error("需要已构建的前端文件")
    with TemporaryDirectory(prefix="dake-field-preview-") as directory:
        app = application(directory)

        @app.middleware("http")
        async def synthetic_boundary(request, call_next):
            if request.method != "GET" and not (
                request.method == "POST" and (request.url.path in {"/synthetic/reset", "/synthetic/revise"}
                    or request.url.path.startswith("/api/v1/events/") and request.url.path.endswith("/field-decisions"))):
                return JSONResponse({"detail": "合成验收仅开放字段裁定与自造消息修订"}, status_code=405)
            return await call_next(request)

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            target = (dist / path).resolve()
            if not target.is_relative_to(dist):
                return JSONResponse({"detail": "无效路径"}, status_code=404)
            if path and target.is_file():
                return FileResponse(target)
            content = (dist / "index.html").read_text(encoding="utf-8")
            return HTMLResponse(content.replace("<body>", '<body><div style="padding:8px;background:#fef3c7">合成验收：全部为自造数据 · 字段裁定可写 · 未调用真实模型</div>'))

        uvicorn.run(app, host="127.0.0.1", port=args.http_port, access_log=False)


if __name__ == "__main__":
    main()
