"""独立保存执行过程；只有提交收据使用业务事务连接，不隐式建表。"""

from contextlib import contextmanager
from datetime import datetime
import json
from uuid import uuid4

from ..domain.models import TZ, json_value
from ..domain.execution_links import event_link_patterns
from .mysql import MySQLStore, StorageError


class AttemptError(StorageError):
    """记录失败或范围不符时停止本批，不泄露记录内容。"""


class AttemptPendingVerification(AttemptError):
    """事务结果尚未核实，禁止自动继续分析。"""


class DeliveryError(AttemptError):
    """业务已提交，文件或任务展示保存失败；只能恢复展示。"""


def encoded(value):
    """统一编码过程数据；禁止传入连接参数或 HTTP 鉴权信息。"""
    return json.dumps(json_value(value), ensure_ascii=False, separators=(",", ":"))


def now():
    """过程记录时间，不代表消息发生或数据库实际提交时刻。"""
    return datetime.now(TZ).isoformat()


class AttemptRepository:
    """独立连接只写阶段；收据由业务连接写入同一数据库。"""

    def __init__(self, settings, store_factory=MySQLStore):
        self.settings, self.store_factory = settings, store_factory

    @contextmanager
    def connection(self):
        store = self.store_factory(**self.settings.store_kwargs())
        try:
            yield store
        finally:
            store.close()

    @staticmethod
    def decode(row):
        value = dict(row)
        for key in ("metadata", "stages", "receipt", "delivery"):
            if isinstance(value.get(key), (str, bytes)):
                value[key] = json.loads(value[key])
        return value

    def get(self, uid, stream, attempt_id):
        """所有读取强制客户和分析范围，不能仅凭执行 ID 查询。"""
        with self.connection() as store:
            rows, _ = store._execute("SELECT * FROM processing_attempts WHERE uid=%s AND stream=%s AND attempt_id=%s",
                                     (uid, stream, attempt_id))
        return self.decode(rows[0]) if rows else None

    def list_for_event_changes(self, uid, stream, event_id, revision_attempt_ids=(), offset=0, limit=20):
        """仅投影当前事件关联执行；先筛关联及重试链，再分页读取大 JSON。"""
        patterns = event_link_patterns(event_id)
        predicate = " OR ".join(f"COALESCE(JSON_CONTAINS({field},CAST(%s AS JSON)),0)" for _, field, _ in patterns)
        args = tuple(encoded(value) for _, _, value in patterns)
        with self.connection() as store:
            present, _ = store._execute("SELECT 1 AS present FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name='processing_attempts'")
            if not present:
                return dict(available=False, items=[], total=0, known_ids=[], related_ids=[])
            columns, _ = store._execute("SELECT column_name AS column_name FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name='processing_attempts' "
                "AND column_name='event_link_hints'")
            accelerated = any(row.get("column_name") == "event_link_hints" for row in columns)
            # JSON 关联表达式可能让排序缓冲携带大正文；仅在内存排序返回的轻量身份。
            if accelerated:
                # 分两次查询，避免 CASE 表达式使不相关行的大阶段正文也被读入。
                heads, _ = store._execute("SELECT attempt_id,retry_of,created_at,"
                    "JSON_CONTAINS(event_link_hints,JSON_QUOTE(%s)) AS related "
                    "FROM processing_attempts WHERE uid=%s AND stream=%s", (event_id, uid, stream))
                hinted = [row["attempt_id"] for row in heads if row["related"]]
                exact = set()
                for start in range(0, len(hinted), 200):
                    ids = hinted[start:start + 200]
                    matches, _ = store._execute("SELECT attempt_id FROM processing_attempts WHERE uid=%s AND stream=%s "
                        "AND attempt_id IN (" + ",".join(["%s"] * len(ids)) + ") AND (" + predicate + ")",
                        (uid, stream, *ids, *args))
                    exact.update(row["attempt_id"] for row in matches)
                for row in heads:
                    row["related"] = row["attempt_id"] in exact
            else:
                heads, _ = store._execute("SELECT attempt_id,retry_of,created_at,(" + predicate + ") AS related "
                    "FROM processing_attempts WHERE uid=%s AND stream=%s", args + (uid, stream))
            heads.sort(key=lambda row: (row["created_at"], row["attempt_id"]), reverse=True)
            known = {r["attempt_id"] for r in heads}
            related = {r["attempt_id"] for r in heads if r["related"] or r["attempt_id"] in revision_attempt_ids}
            children = {}
            for row in heads:
                children.setdefault(row["retry_of"], []).append(row["attempt_id"])
            queue = list(related)
            for parent in queue:
                for child in children.get(parent, []):
                    if child not in related:
                        related.add(child)
                        queue.append(child)
            ids = [r["attempt_id"] for r in heads if r["attempt_id"] in related][offset:offset + limit]
            rows = []
            if ids:
                rows, _ = store._execute("SELECT * FROM processing_attempts WHERE uid=%s AND stream=%s AND attempt_id IN ("
                    + ",".join(["%s"] * len(ids)) + ")", (uid, stream, *ids))
        by_id = {r["attempt_id"]: self.decode(r) for r in rows}
        return dict(available=True, items=[by_id[rid] for rid in ids if rid in by_id], total=len(related),
                    known_ids=list(known), related_ids=list(related))

    def unresolved(self, uid, stream):
        """同一客户的提交未知或模型未知不能靠更换分析流绕过。"""
        with self.connection() as store:
            rows, _ = store._execute("SELECT attempt_id FROM processing_attempts WHERE uid=%s AND ("
                "(stream=%s AND outcome='running') OR outcome IN ('commit_unknown','model_unknown')) LIMIT 1",
                (uid, stream))
        return rows[0]["attempt_id"] if rows else None

    def for_run_window(self, uid, stream, run_id, window_index, window):
        """只定位明确任务中的窗口执行，不将相同日期的任务推定为重试。"""
        with self.connection() as store:
            # 先排序轻量身份，避免模型输入等大 JSON 进入 MySQL 排序缓冲区。
            rows, _ = store._execute("SELECT attempt_id FROM processing_attempts WHERE uid=%s AND stream=%s AND run_id=%s "
                "AND window_index=%s AND window_start=%s AND window_end=%s ORDER BY created_at DESC,attempt_id DESC LIMIT 1",
                (uid, stream, run_id, window_index, window.start.isoformat(), window.end.isoformat()))
        return self.get(uid, stream, rows[0]["attempt_id"]) if rows else None

    def create(self, uid, stream, window, *, run_id=None, window_index=None, retry_of=None, metadata=None):
        """先有持久执行身份再取数；调用方持有客户锁并检查未决执行。"""
        attempt_id = str(uuid4())
        with self.connection() as store:
            store._execute("""INSERT INTO processing_attempts
                (attempt_id,uid,stream,run_id,window_index,window_start,window_end,retry_of,
                 outcome,created_at,metadata,stages) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'running',%s,%s,JSON_ARRAY())""",
                (attempt_id, uid, stream, run_id, window_index, window.start.isoformat(), window.end.isoformat(),
                 retry_of, now(), encoded(metadata or {})))
        return attempt_id

    def stage(self, uid, stream, attempt_id, stage, status, payload):
        """只追加阶段，不覆盖旧记录；失败必须传播以阻止本批提交。"""
        entry = dict(stage=stage, status=status, recorded_at=now(), payload=json_value(payload))
        with self.connection() as store:
            _, changed = store._execute("UPDATE processing_attempts SET stages=JSON_ARRAY_APPEND(stages,'$',CAST(%s AS JSON)) "
                "WHERE uid=%s AND stream=%s AND attempt_id=%s AND outcome='running' AND receipt IS NULL",
                (encoded(entry), uid, stream, attempt_id))
        if changed != 1:
            raise AttemptError("关键过程记录保存失败，本批已停止")

    def write_receipt(self, store, uid, stream, attempt_id, receipt):
        """必须是事件与 processed_by 使用的连接；此后提交前不得再独立写此行。"""
        store._require_transaction(uid)
        _, changed = store._execute("UPDATE processing_attempts SET receipt=CAST(%s AS JSON) "
            "WHERE uid=%s AND stream=%s AND attempt_id=%s AND outcome='running' AND receipt IS NULL",
            (encoded(receipt), uid, stream, attempt_id))
        if changed != 1:
            raise AttemptError("提交收据保存失败，本批不能提交")

    def confirm_commit(self, uid, stream, attempt_id):
        """仅在提交应答成功后确认执行状态；应答丢失时原 running 会继续阻止重跑。"""
        with self.connection() as store:
            _, changed = store._execute("UPDATE processing_attempts SET outcome='committed',finished_at=%s "
                "WHERE uid=%s AND stream=%s AND attempt_id=%s AND receipt IS NOT NULL AND outcome='running'",
                (now(), uid, stream, attempt_id))
        if changed != 1:
            raise DeliveryError("业务已提交，但提交确认记录未保存，须核实后恢复展示")

    def failure(self, uid, stream, attempt_id, *, unknown, error_type, model_unknown=False):
        """保留收据。提交不明和模型结果不明分开记录，普通失败不升格为提交不明。"""
        if unknown and model_unknown:
            raise AttemptError("同一次失败不能同时是提交不明和模型不明")
        outcome = "commit_unknown" if unknown else "model_unknown" if model_unknown else "not_committed"
        guard = ("outcome IN ('running','commit_unknown','committed')" if unknown else
                 "receipt IS NULL AND outcome='running'")
        with self.connection() as store:
            store._execute("UPDATE processing_attempts SET outcome=%s,error_type=%s,finished_at=%s "
                "WHERE uid=%s AND stream=%s AND attempt_id=%s AND " + guard,
                (outcome, error_type, now(), uid, stream, attempt_id))
            if model_unknown or unknown:
                try:
                    store._execute("UPDATE processing_attempts SET model_state=%s, error_code=%s "
                        "WHERE uid=%s AND stream=%s AND attempt_id=%s",
                        ("unknown" if model_unknown else "commit_unknown", outcome, uid, stream, attempt_id))
                except StorageError as error:
                    if "错误码 1054" not in str(error):
                        raise

    def verified(self, uid, stream, attempt_id, *, committed):
        """在客户锁内以收据核实，并持久保存实际核实结果和时间。"""
        outcome = "committed" if committed else "not_committed"
        entry = dict(stage="verification", status="completed", recorded_at=now(), payload=dict(outcome=outcome))
        with self.connection() as store:
            _, changed = store._execute("UPDATE processing_attempts SET outcome=%s,stages=JSON_ARRAY_APPEND(stages,'$',CAST(%s AS JSON)) "
                "WHERE uid=%s AND stream=%s AND attempt_id=%s AND receipt IS " + ("NOT NULL" if committed else "NULL"),
                (outcome, encoded(entry), uid, stream, attempt_id))
        if changed != 1:
            raise AttemptPendingVerification("核实结果未保存，执行仍须核实")

    def delivery(self, uid, stream, attempt_id, destination, status):
        """提交后的展示保存状态与业务结果分离，失败绝不抹除提交事实。"""
        entry = dict(status=status, observed_at=now())
        with self.connection() as store:
            _, changed = store._execute("UPDATE processing_attempts SET delivery=JSON_SET(COALESCE(delivery,JSON_OBJECT()),%s,CAST(%s AS JSON)) "
                "WHERE uid=%s AND stream=%s AND attempt_id=%s AND receipt IS NOT NULL",
                ("$." + destination, encoded(entry), uid, stream, attempt_id))
        if changed != 1:
            raise DeliveryError("业务已提交，展示记录保存未完成；请恢复展示，不要重新分析")
