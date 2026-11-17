"""第三阶段共用的自造记录；只在内存中提供正式查询与 HTTP 路由。"""

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from dake.api.app_factory import create_app
from dake.config import Settings
from dake.domain.models import Event, Message, parse_time
from dake.services.queries import QueryService

UID = "synthetic-classification"
STREAM = "stub-v1"
RUN_ID = "00000000-0000-4000-8000-000000000003"
MOMENT = "2038-06-01T09:00:00+08:00"


def records():
    moment = parse_time(MOMENT)
    messages = [Message(UID, key, moment, content, source="synthetic",
                        conversation_id="synthetic-room", conversation_name="合成验收群")
                for key, content in (
                    ("resource", "请将合成环境配额提高到2000次。"),
                    ("business", "请为同次提额提供合成服务报价。"),
                    ("correction", "之前采购意向说错，本次没有采购计划。"),
                    ("other", "请安排合成培训，并开通同场实操所需临时配额，只验收整场培训。"),
                    ("pending", "请继续办理合成申请R的交付，具体交付内容待核实。"),
                    ("legacy", "合成旧事项报障，原记录没有逐标签依据。"),
                    ("purchase", "为合成环境配额增购做购买评估，请提供方案。"))]

    def fact(index, **extra):
        message = messages[index]
        return dict(evidence_ids=[message.key], quote=message.content, **extra)

    def event(key, primary, labels, indexes, **extra):
        value = Event(key, UID, STREAM, primary, key, moment, moment, "待处理", "未知",
                      "合成验收：" + key, moment, types=labels,
                      evidence=[dict(source=m.source, message_id=m.message_id, digest=m.digest,
                                     send_time=MOMENT, excerpt=m.content) for m in
                                (messages[index] for index in indexes)]).to_dict()
        value.update(extra)
        return value

    multi = event("multi", "资源需求", ["资源需求", "商务需求"], [0, 1, 2],
                  type_evidence={"资源需求": [fact(0)], "商务需求": [fact(1)]},
                  classification_evidence_status="已记录", review_flag="报价币种待核实",
                  typed_attributes={
                      "资源需求": {"目标总量与增减量": fact(0, type_label="资源需求", value="2000次")},
                      "商务需求": {"价格与金额口径": fact(1, type_label="商务需求", value=None)}},
                  removed_types={"商机": [fact(2)]})
    other = event("other", "其他", ["其他", "资源需求"], [3],
                  type_evidence={"其他": [fact(3)], "资源需求": [fact(3)]},
                  classification_evidence_status="已记录",
                  other_reason=fact(3, value="额外内容是专项培训，非采购、流失、异常、新能力或商务办理；临时配额只服务于同场实操，无法独立验收。"))
    pending = event("pending", "待分类", [], [4], classification_status="待分类",
                    review_flag="申请R的交付内容待核实")
    legacy = event("legacy", "排障", [], [5])
    for key in ("types", "type_evidence", "removed_types", "typed_attributes",
                "classification_evidence_status", "classification_status"):
        legacy.pop(key)
    empty = event("empty", "待分类", [], [4])
    for key in ("types", "type_evidence", "removed_types", "typed_attributes", "other_reason",
                "classification_evidence_status", "classification_status"):
        empty[key] = None
    old_multi = deepcopy(multi)
    old_multi.update(types=["商机", "资源需求", "商务需求"], removed_types={},
                     evidence=[e for e in multi["evidence"] if e["message_id"] != "correction"] +
                     [dict(source="synthetic", message_id="purchase", digest=messages[6].digest,
                           send_time=MOMENT, excerpt=messages[6].content)],
                     type_evidence={**multi["type_evidence"], "商机": [fact(6)]})
    multi["updated_by"] = [dict(action="created", window_start=MOMENT,
                                window_end="2038-06-01T09:15:00+08:00",
                                message_ids=["resource", "business"], snapshot=old_multi)]
    events = [multi, other, pending, legacy, empty]
    request = dict(mode="official", uid=UID, analysis_stream=STREAM, start=MOMENT,
                   end="2038-06-02T09:00:00+08:00", interval="1d", offline=True,
                   context_lookback="3d", match_window="7d", closed_grace="7d",
                   ingest_lookback="2d", default_status="未知", default_severity="未知",
                   closed_statuses=[], provider="stub")
    cycles = [dict(uid=UID, stream=STREAM,
                   window=dict(start=MOMENT, end="2038-06-01T09:15:00+08:00"),
                   processed=6, created=[e["event_id"] for e in events],
                   events=[old_multi, other, pending, legacy, empty]),
              dict(uid=UID, stream=STREAM,
                   window=dict(start="2038-06-01T09:15:00+08:00", end="2038-06-01T09:30:00+08:00"),
                   processed=1, updated=["multi"], events=[deepcopy(multi)])]
    run = dict(run_id=RUN_ID, uid=UID, mode="official", status="succeeded", request=request,
               total_windows=2, completed_windows=2, created_at=MOMENT, cycles=cycles)
    return events, messages, run


class SyntheticStore:
    def __init__(self, events, messages):
        self.events, self.messages = events, messages

    def list_events(self, uid, stream):
        return [Event.from_dict(e) for e in deepcopy(self.events) if (e["uid"], e["stream"]) == (uid, stream)]

    def _execute(self, sql, args=()):
        if sql.startswith("SELECT DISTINCT uid"):
            return [{"uid": UID}], 0
        if "COUNT(*)" in sql:
            return [{"total": len(self.events) if "FROM events" in sql else len(self.messages)}], 0
        if sql.startswith("SELECT source, message_id, payload FROM messages"):
            return [dict(source=m.source, message_id=m.message_id, payload=m.to_dict())
                    for m in self.messages if m.uid == args[0]], 0
        if sql == "SELECT 1":
            return [], 0
        raise AssertionError("合成仓库不支持该查询")

    def close(self):
        pass


class SyntheticRuns:
    def __init__(self, run, store):
        self.run, self.store = run, store

    def init_schema(self):
        pass

    @contextmanager
    def connection(self):
        yield self.store

    def list(self, uid=None, mode=None, stream=None):
        items = [deepcopy(self.run)] if uid in (None, UID) and stream in (None, STREAM) else []
        return dict(items=items, total=len(items))

    def get(self, run_id, *, uid=None, stream=None):
        return deepcopy(self.run) if run_id == RUN_ID and uid in (None, UID) and stream in (None, STREAM) else None

    def list_for_event_changes(self, uid, stream, offset, *, event_id):
        return self.list(uid, stream=stream)


class NoExecutions:
    def list_for_event_changes(self, *args):
        return dict(items=[], known_ids=[], available=False, total=0)


def application(data_dir):
    events, messages, run = records()
    store = SyntheticStore(events, messages)
    settings = Settings(uid=UID, data_dir=Path(data_dir))
    queries = QueryService(settings, store_factory=lambda **kwargs: store, attempts=NoExecutions())
    app = create_app(settings, query_service=queries, run_repository=SyntheticRuns(run, store),
                     runner=object(), start_worker=False)
    return app, store


def main():
    import argparse
    import uvicorn
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    parser = argparse.ArgumentParser(description="第三阶段自造数据只读验收，不加载 .env 或连接数据库")
    parser.add_argument("--http-port", required=True, type=int)
    parser.add_argument("--web-dist", required=True, type=Path)
    args = parser.parse_args()
    if args.http_port in (3306, 3307, 8000, 8001) or not 1024 <= args.http_port <= 65535:
        parser.error("必须选择独立本地端口")
    dist = args.web_dist.resolve()
    if not (dist / "index.html").is_file():
        parser.error("需要现有前端构建")
    with TemporaryDirectory(prefix="dake-classification-") as directory:
        app, _ = application(directory)

        @app.middleware("http")
        async def read_only(request, call_next):
            if request.method != "GET":
                return JSONResponse({"detail": "合成验收只读"}, status_code=405)
            return await call_next(request)

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            target = (dist / path).resolve()
            if not target.is_relative_to(dist):
                return JSONResponse({"detail": "无效路径"}, status_code=404)
            if path and target.is_file():
                return FileResponse(target)
            content = (dist / "index.html").read_text(encoding="utf-8")
            return HTMLResponse(content.replace("<body>", '<body><div style="padding:8px;background:#fef3c7">合成验收：全部为自造数据 · 只读 · 未调用模型</div>'))

        uvicorn.run(app, host="127.0.0.1", port=args.http_port, access_log=False)


if __name__ == "__main__":
    main()
