from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from classification_preview import NoExecutions, SyntheticStore
from field_decision_preview import PreviewRuns, RUN_ID
from dake.api.app_factory import create_app
from dake.services.queries import QueryService
from test_classification_replay import UID, day_window, msg
from test_identity_corrections import build_split
from final_replay import record


def application(directory):
    roles = {(UID, "synthetic", "synthetic-human"): "customer"}
    with patch("dake.services.processing.load_sender_roles", lambda _: roles):
        case = build_split(Path(directory))
    case.provider.name = "stub-v1"
    for event in case.store.events.values():
        event.stream = "stub-v1"
        for revision in event.updated_by:
            revision["snapshot"]["stream"] = "stub-v1"
    case.store.processed = {(key, "stub-v1"): digest for (key, _), digest in case.store.processed.items()}
    case.store._execute = lambda sql, args=(): ([dict(uid=UID)], 0) if sql.startswith("SELECT DISTINCT uid") else SyntheticStore(
        [event.to_dict() for event in case.store.events.values()], list(case.store.messages.values()))._execute(sql, args)
    run = dict(run_id=RUN_ID, uid=UID, mode="official", status="succeeded", created_at=day_window(1).end.isoformat(),
        request=dict(mode="official", uid=UID, analysis_stream="stub-v1", start=case.service.settings.start.isoformat(),
            end=case.service.settings.end.isoformat(), interval="1d", offline=True, context_lookback="20d", match_window="7d",
            closed_grace="7d", ingest_lookback="20d", default_status="未知", default_severity="未知", closed_statuses=[], provider="stub"),
        total_windows=4, completed_windows=4, cycles=[value.to_dict() for value in case.rounds])
    queries = QueryService(case.service.settings, store_factory=lambda **kwargs: case.store, attempts=NoExecutions())
    app = create_app(case.service.settings, query_service=queries, run_repository=PreviewRuns(run, case.store), runner=object(), start_worker=False)
    original = deepcopy((case.store.events, case.store.messages, case.store.processed, case.store.attributions,
                         case.source.messages, case.provider.mapping))

    @app.post("/synthetic/reset", include_in_schema=False)
    def reset():
        (case.store.events, case.store.messages, case.store.processed, case.store.attributions,
         case.source.messages, case.provider.mapping) = deepcopy(original)
        return dict(event_id=case.duplicate_id, retained_event_id=case.parent_id, uid=UID, stream="stub-v1")

    @app.post("/synthetic/follow", include_in_schema=False)
    def follow():
        message = msg("browser-follow", 5, "A上传同一次故障仍失败，请继续跟进。")
        case.source.messages.append(message)
        case.provider.mapping[message.message_id] = record(message, ["排障"], old=case.event(case.duplicate_id), anchor=case.again_message)
        with patch("dake.services.processing.load_sender_roles", lambda _: roles):
            return case.service.run_window(UID, day_window(5)).to_dict()

    return app


def main():
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="仅合成数据、内存事务及正式人工纠正 HTTP")
    parser.add_argument("--http-port", required=True, type=int)
    parser.add_argument("--web-dist", required=True, type=Path)
    args = parser.parse_args()
    if args.http_port in (3306, 3307, 8000, 8001) or not 1024 <= args.http_port <= 65535:
        parser.error("须选择独立回环端口")
    dist = args.web_dist.resolve()
    if not (dist / "index.html").is_file():
        parser.error("需要已构建前端")
    with TemporaryDirectory(prefix="dake-identity-preview-") as directory:
        app = application(directory)

        @app.middleware("http")
        async def boundary(request, call_next):
            if request.method != "GET" and not (request.method == "POST" and (
                    request.url.path in {"/synthetic/reset", "/synthetic/follow"} or request.url.path.startswith("/api/v1/events/")
                    and request.url.path.endswith(("/same-round-corrections", "/field-decisions")))):
                return JSONResponse({"detail": "合成验收不开放其他写入"}, status_code=405)
            return await call_next(request)

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            target = (dist / path).resolve()
            if not target.is_relative_to(dist):
                return JSONResponse({"detail": "无效路径"}, status_code=404)
            if path and target.is_file():
                return FileResponse(target)
            return HTMLResponse((dist / "index.html").read_text().replace("<body>",
                '<body><div style="padding:8px;background:#fef3c7">合成验收：同轮误拆纠正可写 · 全部自造数据</div>'))

        uvicorn.run(app, host="127.0.0.1", port=args.http_port, access_log=False)


if __name__ == "__main__":
    main()
