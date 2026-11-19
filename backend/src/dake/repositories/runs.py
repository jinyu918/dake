"""持久化处理任务与周期进度；业务事件仍由既有 MySQL 仓库维护。"""

from contextlib import contextmanager, nullcontext
from datetime import datetime
import hashlib
import json
from uuid import uuid4

from ..config import API_MAX_PENDING_RUNS, API_RUN_LIST_LIMIT
from ..domain.models import TZ, json_value
from .mysql import MySQLStore, StorageError

# 周期正文可能很大，列表与恢复查找不把它带进数据库排序缓冲区。
SUMMARY_COLUMNS = ("run_id,uid,mode,status,request,total_windows,completed_windows,current_window,"
                   "created_at,started_at,finished_at,error")
CHANGE_RUN_PAGE_SIZE = 20

# 旧任务只能用已保存周期证明范围；显式记录过范围时不借其他周期越界。
RUN_SCOPE_CONDITION = """uid=%s AND (
    JSON_UNQUOTE(JSON_EXTRACT(request,'$.analysis_stream'))=%s
    OR (COALESCE(JSON_TYPE(JSON_EXTRACT(request,'$.analysis_stream')),'NULL')='NULL'
        AND JSON_CONTAINS(JSON_EXTRACT(cycles,'$[*].stream'),JSON_QUOTE(%s))))"""


def _scope_args(uid, stream):
    """进度查询与变化面板共用同一范围条件。"""
    return (uid, stream, stream)


class RunConflict(ValueError):
    """客户已有未结束任务，或等待队列达到本地运行上限。"""


def _decode(value, default=None):
    return default if value is None else (json.loads(value) if isinstance(value, (str, bytes)) else value)


def _encoded(value):
    return json.dumps(json_value(value), ensure_ascii=False, separators=(",", ":"))


def _now():
    return datetime.now(TZ).replace(tzinfo=None)


class RunRepository:
    """每次操作单独建立连接，避免 API 线程与后台线程共享 MySQL 连接。"""

    def __init__(self, settings, store_factory=MySQLStore):
        self.settings = settings
        self.store_factory = store_factory
        suffix = hashlib.sha256(settings.mysql_database.encode()).hexdigest()[:32]
        self.worker_lock = "dake:runner:" + suffix
        self.submit_lock = "dake:submit:" + suffix

    @contextmanager
    def connection(self):
        """限定一次数据库操作的连接生命周期，异常时也释放资源。"""
        store = self.store_factory(**self.settings.store_kwargs())
        try:
            yield store
        finally:
            store.close()

    def init_schema(self):
        """只新增任务表，不删除或重建现有消息、事件数据。"""
        with self.connection() as store:
            store.init_schema()
            store._execute("""CREATE TABLE IF NOT EXISTS processing_runs (
                run_id VARCHAR(36) COLLATE utf8mb4_bin PRIMARY KEY,
                uid VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
                mode VARCHAR(16) NOT NULL,
                status VARCHAR(16) NOT NULL,
                request JSON NOT NULL,
                total_windows INT NOT NULL,
                completed_windows INT NOT NULL DEFAULT 0,
                current_window JSON NULL,
                current_baseline JSON NULL,
                runner_id VARCHAR(36) NULL,
                recovered_by VARCHAR(36) NULL,
                created_at DATETIME(6) NOT NULL,
                started_at DATETIME(6) NULL,
                finished_at DATETIME(6) NULL,
                error VARCHAR(512) NULL,
                cycles JSON NOT NULL,
                KEY ix_runs_status_created (status,created_at),
                KEY ix_runs_uid_created (uid,created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""")
            from .pipeline import apply_statements
            from ..config import PROJECT_ROOT
            apply_statements(store, PROJECT_ROOT / "backend/migrations/005_customer_pipelines.sql")
            self._seed_runtime_slots(store)

    def _seed_runtime_slots(self, store):
        """按配置补齐额度行；不抢走仍在有效租约中的名额。"""
        slots = getattr(self.settings, "analysis_slots", 2)
        reserved = getattr(self.settings, "live_reserved", 1)
        for index in range(slots):
            role = "live" if index < reserved else "general"
            store._execute("""INSERT INTO runtime_limits (resource,slot,held_reason,epoch)
                VALUES ('analysis',%s,%s,0) ON DUPLICATE KEY UPDATE
                held_reason=IF(owner IS NULL OR ((lease_until IS NULL OR lease_until<=NOW(6))
                    AND IS_FREE_LOCK(CONCAT('dake:work:',COALESCE(owner,'')))=1),
                    VALUES(held_reason), held_reason)""",
                (index, role))
        store._execute("""INSERT INTO runtime_limits (resource,slot,held_reason,epoch)
            VALUES ('fetch',0,'general',0) ON DUPLICATE KEY UPDATE slot=slot""")

    @staticmethod
    def _record(row, *, include_cycles=True, internal=False):
        result = dict(row)
        for name in ("request", "current_window", "current_baseline", "cycles"):
            result[name] = _decode(result.get(name), [] if name == "cycles" else None)
        for name in ("created_at", "started_at", "finished_at"):
            value = result.get(name)
            result[name] = value.replace(tzinfo=TZ).isoformat() if value else None
        if not include_cycles:
            result["cycles"] = []
        if not internal:
            for name in ("current_baseline", "runner_id", "recovered_by"):
                result.pop(name, None)
        return result

    def create(self, request: dict, total_windows: int, *, run_id=None) -> dict:
        """在跨进程提交锁内检查同客户冲突与队列上限，再保存待执行任务。"""
        run_id = run_id or str(uuid4())
        with self.connection() as store:
            rows, _ = store._execute("SELECT GET_LOCK(%s,10) AS ok", (self.submit_lock,))
            if not rows or rows[0]["ok"] != 1:
                raise RunConflict("任务提交繁忙，请稍后重试")
            try:
                existing, _ = store._execute("SELECT * FROM processing_runs WHERE run_id=%s", (run_id,))
                if existing:
                    if existing[0]["uid"] != request["uid"] or existing[0]["mode"] != request["mode"]:
                        raise RunConflict("子任务身份冲突")
                    return self._record(existing[0])
                rows, _ = store._execute("SELECT uid FROM processing_runs WHERE status IN ('queued','running')")
                if any(row["uid"] == request["uid"] for row in rows):
                    raise RunConflict("该客户已有等待或执行中的任务")
                if len(rows) >= API_MAX_PENDING_RUNS:
                    raise RunConflict("等待队列已满，请等待已有任务结束")
                store._execute("""INSERT INTO processing_runs
                    (run_id,uid,mode,status,request,total_windows,created_at,cycles)
                    VALUES (%s,%s,%s,'queued',%s,%s,%s,JSON_ARRAY())""",
                    (run_id, request["uid"], request["mode"], _encoded(request), total_windows, _now()))
            finally:
                store._execute("SELECT RELEASE_LOCK(%s)", (self.submit_lock,))
        return self.get(run_id)

    def get(self, run_id: str, *, internal=False, uid=None, stream=None) -> dict | None:
        """取得完整任务及周期结果；内部字段只供中断恢复使用。"""
        where, args = "run_id=%s", (run_id,)
        if stream is not None:
            if uid is None:
                raise ValueError("查询分析范围时必须指定客户")
            where += " AND " + RUN_SCOPE_CONDITION
            args += _scope_args(uid, stream)
        elif uid is not None:
            where += " AND uid=%s"
            args += (uid,)
        with self.connection() as store:
            rows, _ = store._execute("SELECT * FROM processing_runs WHERE " + where, args)
        return self._record(rows[0], internal=internal) if rows else None

    def progress_rows(self, run_ids, *, include_cycles=False):
        """分批读取同一执行引用的进度，省去逐窗口连接及请求、快照等正文。"""
        ids = list(dict.fromkeys(run_ids))
        result = {}
        if not ids:
            return result
        columns = "run_id,status,created_at,completed_windows,total_windows"
        if include_cycles:
            # 只传回统计所需的字段，保留旧周期中缺失字段及空数组的语义。
            columns += """, (SELECT JSON_ARRAYAGG(JSON_OBJECT(
                'processed', c.processed, 'created', COALESCE(c.created, JSON_ARRAY()),
                'updated', COALESCE(c.updated, JSON_ARRAY()), 'filter_audit', COALESCE(c.audit, JSON_ARRAY())))
                FROM JSON_TABLE(cycles, '$[*]' COLUMNS(
                    processed INT PATH '$.processed' DEFAULT '0' ON EMPTY,
                    created JSON PATH '$.created', updated JSON PATH '$.updated',
                    audit JSON PATH '$.filter_audit')) AS c) AS cycles"""
        with self.connection() as store:
            for start in range(0, len(ids), 400):
                batch = ids[start:start + 400]
                placeholders = ",".join(["%s"] * len(batch))
                rows, _ = store._execute(
                    f"SELECT {columns} FROM processing_runs WHERE run_id IN ({placeholders})", tuple(batch))
                for row in rows:
                    if include_cycles:
                        row["cycles"] = _decode(row["cycles"], [])
                    result[row["run_id"]] = row
        return result

    def list(self, uid=None, mode=None, stream=None) -> dict:
        """列表省略周期正文，只显示任务摘要和进度。"""
        conditions, args = [], []
        if stream is not None:
            if uid is None:
                raise ValueError("查询分析范围时必须指定客户")
            conditions.append(RUN_SCOPE_CONDITION)
            args.extend(_scope_args(uid, stream))
        elif uid is not None:
            conditions.append("uid=%s")
            args.append(uid)
        if mode is not None:
            conditions.append("mode=%s")
            args.append(mode)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connection() as store:
            rows, _ = store._execute("SELECT " + SUMMARY_COLUMNS + " FROM processing_runs" + where + " ORDER BY created_at DESC LIMIT %s", tuple(args) + (API_RUN_LIST_LIMIT,))
            counts, _ = store._execute("SELECT COUNT(*) AS total FROM processing_runs" + where, tuple(args))
        return {"items": [self._record(row, include_cycles=False) for row in rows], "total": counts[0]["total"]}

    def list_for_event_changes(self, uid, stream, offset=0, event_id=None):
        """按范围分页取既有记录；先过滤再分页，不把其他客户周期带入面板。"""
        if event_id is not None:
            return self._event_change_cycles(uid, stream, event_id, offset)
        where = RUN_SCOPE_CONDITION
        args = _scope_args(uid, stream)
        with self.connection() as store:
            selected, _ = store._execute("SELECT run_id FROM processing_runs WHERE "
                + where + " ORDER BY created_at DESC,run_id DESC LIMIT %s OFFSET %s",
                args + (CHANGE_RUN_PAGE_SIZE, offset))
            counts, _ = store._execute("SELECT COUNT(*) AS total FROM processing_runs WHERE " + where, args)
            # 先只排序 ID，再取这页正文，避免大周期 JSON 进入 MySQL 排序缓冲区。
            ids = [row["run_id"] for row in selected]
            rows = []
            if ids:
                rows, _ = store._execute("SELECT " + SUMMARY_COLUMNS + ",cycles,recovered_by FROM processing_runs WHERE "
                    + where + " AND run_id IN (" + ",".join(["%s"] * len(ids)) + ")", args + tuple(ids))
        by_id = {row["run_id"]: row for row in rows}
        return {"items": [self._record(by_id[rid], internal=True) for rid in ids if rid in by_id], "total": counts[0]["total"]}

    def _event_change_cycles(self, uid, stream, event_id, offset):
        """只展开周期身份、写入列表与归属依据，避免扫描和传输全量事件快照。"""
        projection = """ FROM processing_runs AS r JOIN JSON_TABLE(r.cycles,'$[*]' COLUMNS(
            ordinal FOR ORDINALITY,
            cycle_uid VARCHAR(128) COLLATE utf8mb4_bin PATH '$.uid',
            cycle_stream VARCHAR(128) COLLATE utf8mb4_bin PATH '$.stream',
            cycle_window JSON PATH '$.window',
            attempt_id VARCHAR(36) PATH '$.attempt_id',
            created JSON PATH '$.created', updated JSON PATH '$.updated',
            filter_audit JSON PATH '$.filter_audit',
            processed INT PATH '$.processed', counts_complete BOOL PATH '$.counts_complete',
            recovered_from_history BOOL PATH '$.recovered_from_history'
        )) AS c ON TRUE WHERE r.uid=%s AND c.cycle_uid=%s AND c.cycle_stream=%s
        AND (COALESCE(JSON_TYPE(JSON_EXTRACT(r.request,'$.analysis_stream')),'NULL')='NULL'
             OR JSON_UNQUOTE(JSON_EXTRACT(r.request,'$.analysis_stream'))=%s)
        AND JSON_CONTAINS(JSON_EXTRACT(r.cycles,'$[*].created','$[*].updated',
            '$[*].filter_audit[*].event_id','$[*].filter_audit[*].possible_event_ids'),JSON_QUOTE(%s))
        AND (JSON_CONTAINS(c.created,JSON_QUOTE(%s)) OR JSON_CONTAINS(c.updated,JSON_QUOTE(%s))
             OR EXISTS (SELECT 1 FROM JSON_TABLE(c.filter_audit,'$[*]' COLUMNS(
                 kind VARCHAR(64) COLLATE utf8mb4_bin PATH '$.kind',
                 event_id VARCHAR(64) COLLATE utf8mb4_bin PATH '$.event_id',
                 possible_event_ids JSON PATH '$.possible_event_ids'
             )) AS a WHERE a.kind='event_attribution'
                 AND (a.event_id=%s OR JSON_CONTAINS(a.possible_event_ids,JSON_QUOTE(%s)))))"""
        args = (uid, uid, stream, stream, event_id, event_id, event_id, event_id, event_id)
        with self.connection() as store:
            heads, _ = store._execute("SELECT DISTINCT r.run_id,r.created_at" + projection
                + " ORDER BY r.created_at DESC,r.run_id DESC", args)
            ids = [r["run_id"] for r in heads[offset:offset + CHANGE_RUN_PAGE_SIZE]]
            if not ids:
                return dict(items=[], total=len(heads))
            columns = ",".join("r." + column for column in SUMMARY_COLUMNS.split(","))
            rows, _ = store._execute("SELECT " + columns + ",r.recovered_by,c.*" + projection
                + " AND r.run_id IN (" + ",".join(["%s"] * len(ids)) + ") ORDER BY c.ordinal", args + tuple(ids))
        by_id = {}
        for row in rows:
            if row["run_id"] not in by_id:
                by_id[row["run_id"]] = self._record(row, internal=True)
            by_id[row["run_id"]]["cycles"].append(dict(
                uid=row["cycle_uid"], stream=row["cycle_stream"], window=_decode(row["cycle_window"]),
                _cycle_index=row["ordinal"] - 1, attempt_id=row["attempt_id"],
                created=_decode(row["created"], []), updated=_decode(row["updated"], []),
                filter_audit=_decode(row["filter_audit"], []), processed=row["processed"],
                counts_complete=row["counts_complete"] if row["counts_complete"] is not None else True,
                recovered_from_history=bool(row["recovered_from_history"])))
        return dict(items=[by_id[rid] for rid in ids], total=len(heads))

    def recover_interrupted(self):
        """核对失去工人的运行，包括尚未取窗口和已经释放客户执行权的两个间隙。"""
        orphan = """r.status='running' AND IS_FREE_LOCK(CONCAT('dake:work:',COALESCE(r.runner_id,'')))=1
            AND NOT EXISTS (SELECT 1 FROM runtime_limits l WHERE l.owner=r.runner_id AND l.lease_until>NOW(6))
            AND NOT EXISTS (SELECT 1 FROM uid_pipeline_state p WHERE p.uid=r.uid AND p.owner IS NOT NULL)"""
        with self.connection() as store:
            ids, _ = store._execute("SELECT r.run_id FROM processing_runs r WHERE " + orphan)
            for item in ids:
                with store.metadata_transaction():
                    rows, _ = store._execute("""SELECT r.run_id,r.uid,r.mode,r.completed_windows,r.total_windows,
                        r.current_window IS NOT NULL AS in_window, r.request->>'$.analysis_stream' AS stream,
                        r.request->>'$.strict_window' AS strict_window
                        FROM processing_runs r WHERE r.run_id=%s AND """ + orphan + " FOR UPDATE", (item["run_id"],))
                    if not rows:
                        continue
                    run = rows[0]
                    # 旧版非严格窗口在模型调用期间持有客户锁，耗时超过名额租约也仍在工作。
                    free, _ = store._execute("SELECT IS_FREE_LOCK(%s) AS ok", (store.customer_lock_name(run["uid"]),))
                    if free[0]["ok"] != 1:
                        continue
                    status, error = self._interrupted_result(store, run)
                    store._execute("""UPDATE processing_runs SET status=%s,error=%s,runner_id=NULL,
                        started_at=IF(%s='queued',NULL,started_at),finished_at=IF(%s='queued',NULL,NOW(6))
                        WHERE run_id=%s""", (status, error, status, status, run["run_id"]))

    @staticmethod
    def _interrupted_result(store, run):
        """仅从已提交回执或已发布的来源窗口确认完成；未决窗口留下原因供核实。"""
        complete = run["completed_windows"]
        if complete:
            if run["mode"] == "customer_sync":
                rows, _ = store._execute("""SELECT COUNT(DISTINCT IF(w.fetch_id IS NULL,NULL,c.idx)) AS confirmed,
                    SUM(COALESCE(c.profiles,0)+COALESCE(c.lookups,0)) AS errors FROM processing_runs r
                    JOIN JSON_TABLE(r.cycles,'$[*]' COLUMNS(
                        idx FOR ORDINALITY, start_at VARCHAR(40) PATH '$.window.start', end_at VARCHAR(40) PATH '$.window.end',
                        profiles INT PATH '$.profile_errors', lookups INT PATH '$.lookup_errors')) c ON TRUE
                    LEFT JOIN source_windows w ON w.source='official_chat' AND w.status='complete'
                        AND w.window_start=CAST(SUBSTRING_INDEX(c.start_at,'+',1) AS DATETIME(6))
                        AND w.window_end=CAST(SUBSTRING_INDEX(c.end_at,'+',1) AS DATETIME(6)) WHERE r.run_id=%s""", (run["run_id"],))
            else:
                rows, _ = store._execute("""SELECT COUNT(a.attempt_id) AS confirmed FROM processing_runs r
                    JOIN JSON_TABLE(r.cycles,'$[*]' COLUMNS(idx FOR ORDINALITY,
                        attempt_id VARCHAR(36) PATH '$.attempt_id', start_at VARCHAR(40) PATH '$.window.start',
                        end_at VARCHAR(40) PATH '$.window.end')) c ON TRUE
                    JOIN processing_attempts a ON a.attempt_id=c.attempt_id AND a.uid=r.uid
                        AND a.run_id=r.run_id AND a.window_index=c.idx-1 AND a.receipt IS NOT NULL
                        AND a.window_start=c.start_at AND a.window_end=c.end_at
                        AND (r.request->>'$.analysis_stream' IS NULL OR a.stream=r.request->>'$.analysis_stream')
                    WHERE r.run_id=%s""", (run["run_id"],))
            if rows[0]["confirmed"] != complete:
                return "failed", "执行中断，已处理窗口的提交结果未确认，请核实"
            if complete == run["total_windows"] and not run["in_window"]:
                if rows[0].get("errors"):
                    return "failed", "部分资料或群搜索查询失败，已保存有效结果"
                return "succeeded", None
        attempts, _ = store._execute("""SELECT (run_id=%s AND window_index>=%s AND receipt IS NOT NULL) AS delivered,
                outcome,error_code FROM processing_attempts
            WHERE uid=%s AND ((run_id=%s AND window_index>=%s)
                OR outcome IN ('commit_unknown','model_unknown') OR (stream=%s AND outcome='running'))""",
            (run["run_id"], complete, run["uid"], run["run_id"], complete, run["stream"]))
        if any(a["delivered"] for a in attempts):
            return "failed", "窗口业务已提交，执行中断，请按回执恢复展示"
        blocked, _ = store._execute("SELECT block_reason FROM uid_pipeline_state WHERE uid=%s", (run["uid"],))
        unresolved = any(a["outcome"] != "not_committed" or a["error_code"] != "released" for a in attempts)
        if unresolved or (blocked and blocked[0]["block_reason"]) or (run["in_window"] and run["strict_window"] != "true"):
            return "failed", "执行中断，当前窗口的结果未确认，请核实后再继续"
        return "queued", None

    @contextmanager
    def claimed_work(self, *, source):
        """一次领取独占一个身份和连接；工人退出或断线时 MySQL 自动释放执行锁。"""
        owner = str(uuid4())
        with self.connection() as keeper:
            run = self._claim(owner, source=source, keeper=keeper)
            try:
                yield run, keeper
            finally:
                if run is not None:
                    self.release_slot(owner, source=source, slot=run["_capacity_slot"])

    def working_ids(self, run_ids):
        """只统计这批任务实际占用的名额，兼容旧工人的短租约。"""
        ids = tuple(set(run_ids))
        if not ids:
            return set()
        with self.connection() as store:
            rows, _ = store._execute("""SELECT DISTINCT r.run_id
                FROM runtime_limits l JOIN processing_runs r ON r.runner_id=l.owner
                AND l.resource=IF(r.mode='customer_sync','fetch','analysis')
                WHERE r.status='running' AND (l.lease_until>NOW(6)
                    OR IS_USED_LOCK(CONCAT('dake:work:',COALESCE(l.owner,''))) IS NOT NULL)
                AND r.run_id IN (""" + ",".join(["%s"] * len(ids)) + ")", ids)
        return {row["run_id"] for row in rows}

    def claim_source(self, runner_id: str) -> dict | None:
        """领取一个全局抓取窗口；不按客户重复取数，也不占用分析名额。"""
        return self._claim(runner_id, source=True)

    def claim_fair(self, runner_id: str) -> dict | None:
        """空闲分析名额领取可运行客户的最早窗口；实时预留名额不借给历史追赶。"""
        return self._claim(runner_id, source=False)

    def _claim(self, runner_id: str, *, source: bool, keeper=None) -> dict | None:
        lease = int(getattr(self.settings, "lease_seconds", 30))
        slots = int(getattr(self.settings, "analysis_slots", 2))
        chosen = None
        held = None
        for attempt in range(3):
            try:
                with nullcontext(keeper) if keeper is not None else self.connection() as store:
                    with store.metadata_transaction():
                        resource = "fetch" if source else "analysis"
                        limit = 1 if source else slots
                        free, _ = store._execute("""SELECT slot, held_reason FROM runtime_limits
                            WHERE resource=%s AND slot<%s AND (owner IS NULL OR
                                ((lease_until IS NULL OR lease_until<=NOW(6))
                                    AND IS_FREE_LOCK(CONCAT('dake:work:',COALESCE(owner,'')))=1))
                            ORDER BY slot FOR UPDATE SKIP LOCKED""", (resource, limit))
                        if not free:
                            return None
                        if source:
                            rows, _ = store._execute("""SELECT run_id, uid, 'live' AS phase FROM processing_runs
                                WHERE status='queued' AND mode='customer_sync' ORDER BY created_at, run_id LIMIT 8""")
                        else:
                            rows, _ = store._execute("""SELECT r.run_id, r.uid,
                                COALESCE(JSON_UNQUOTE(JSON_EXTRACT(r.request,'$.phase')),'live') AS phase
                                FROM processing_runs r
                                LEFT JOIN uid_pipeline_state p ON p.uid=r.uid
                                WHERE r.status='queued' AND r.mode<>'customer_sync'
                                AND (p.uid IS NULL OR (p.block_reason IS NULL AND p.owner IS NULL))
                                AND NOT EXISTS (SELECT 1 FROM processing_runs a WHERE a.uid=r.uid AND a.status='running'
                                    AND a.mode<>'customer_sync')
                                ORDER BY p.last_served_at IS NULL DESC, p.last_served_at, r.created_at, r.run_id LIMIT 32""")
                        chosen = None
                        slot = None
                        for row in rows:
                            phase = row.get("phase") or "live"
                            slot = next((item for item in free if source or item["held_reason"] != "live" or phase == "live"), None)
                            if slot is None:
                                continue
                            _, changed = store._execute("""UPDATE processing_runs SET status='running', runner_id=%s, started_at=%s
                                WHERE run_id=%s AND status='queued'""", (runner_id, _now(), row["run_id"]))
                            if changed == 1:
                                chosen = row["run_id"]
                                break
                        if chosen is None:
                            return None
                        if keeper is not None:
                            locked, _ = store._execute("SELECT GET_LOCK(CONCAT('dake:work:',%s),0) AS ok", (runner_id,))
                            if locked[0]["ok"] != 1:
                                raise RunConflict("本次领取未取得执行锁")
                        store._execute("""UPDATE runtime_limits SET owner=%s, epoch=epoch+1,
                            lease_until=DATE_ADD(NOW(6), INTERVAL %s SECOND)
                            WHERE resource=%s AND slot=%s""", (runner_id, lease, resource, slot["slot"]))
                        held = slot["slot"]
                break
            except StorageError as error:
                if any(code in str(error) for code in ("1213", "1205")) and attempt < 2:
                    import time
                    time.sleep(0.02 * (attempt + 1))
                    continue
                raise
        claimed = self.get(chosen, internal=True)
        if claimed is not None:
            claimed["_capacity_slot"] = held
            claimed["_capacity_source"] = source
        return claimed

    def release_slot(self, runner_id: str, *, source: bool, slot: int):
        """只归还本次领取的名额，避免同一进程的其他客户被一起放开。"""
        resource = "fetch" if source else "analysis"
        with self.connection() as store:
            store._execute("""UPDATE runtime_limits SET owner=NULL, lease_until=NULL
                WHERE resource=%s AND slot=%s AND owner=%s""", (resource, slot, runner_id))

    def claim_next(self, runner_id: str) -> dict | None:
        """兼容单线程测试的最早任务领取。生产分析循环使用 claim_fair。"""
        with self.connection() as store:
            rows, _ = store._execute("SELECT run_id FROM processing_runs WHERE status='queued' ORDER BY created_at,run_id LIMIT 1")
            if not rows:
                return None
            run_id = rows[0]["run_id"]
            _, changed = store._execute("""UPDATE processing_runs SET status='running',runner_id=%s,
                started_at=%s WHERE run_id=%s AND status='queued'""", (runner_id, _now(), run_id))
        return self.get(run_id, internal=True) if changed else None

    def begin_window(self, run_id: str, runner_id: str, window: dict, baseline: dict):
        """执行前保存窗口和已有修订数，崩溃后能区分新增提交与早先历史。"""
        with self.connection() as store:
            _, changed = store._execute("""UPDATE processing_runs SET current_window=%s,current_baseline=%s
                WHERE run_id=%s AND status='running' AND runner_id=%s""",
                (_encoded(window), _encoded(baseline), run_id, runner_id))
            if not changed:
                # MySQL 对完全相同的窗口和基线返回零行变化；这不等于执行权丢失。
                owned, _ = store._execute("SELECT run_id FROM processing_runs "
                    "WHERE run_id=%s AND status='running' AND runner_id=%s", (run_id, runner_id))
                changed = bool(owned)
        if not changed:
            raise StorageError("任务执行权已变化，已停止当前执行")

    def complete_window(self, run_id: str, runner_id: str, cycle: dict):
        """一次 SQL 原子追加周期与进度，失败不会抹掉此前已保存的周期。"""
        with self.connection() as store:
            _, changed = store._execute("""UPDATE processing_runs SET
                cycles=JSON_ARRAY_APPEND(cycles,'$',CAST(%s AS JSON)),
                completed_windows=completed_windows+1,current_window=NULL,current_baseline=NULL
                WHERE run_id=%s AND status='running' AND runner_id=%s""",
                (_encoded(cycle), run_id, runner_id))
        if not changed:
            raise StorageError("任务执行权已变化，已停止保存进度")

    def has_interrupted_range(self, uid, start, end):
        """存在旧的中断窗口时保留原收据恢复路径，不把未展示结果当空窗口跳过。"""
        with self.connection() as store:
            rows, _ = store._execute("SELECT run_id FROM processing_runs WHERE uid=%s AND status='failed' "
                "AND current_window IS NOT NULL AND JSON_UNQUOTE(JSON_EXTRACT(current_window,'$.start')) < %s "
                "AND JSON_UNQUOTE(JSON_EXTRACT(current_window,'$.end')) > %s LIMIT 1", (uid, end, start))
        return bool(rows)

    def complete_empty_windows(self, run_id, runner_id, start_index, cycles):
        """原子写入空窗口检查结果；重复提交或执行权变化时不重复推进。"""
        if not cycles:
            return
        with self.connection() as store:
            _, changed = store._execute("UPDATE processing_runs SET cycles=JSON_MERGE_PRESERVE(cycles,CAST(%s AS JSON)), "
                "completed_windows=completed_windows+%s,current_window=NULL,current_baseline=NULL "
                "WHERE run_id=%s AND status='running' AND runner_id=%s AND completed_windows=%s",
                (_encoded(cycles), len(cycles), run_id, runner_id, start_index))
        if not changed:
            raise StorageError("空窗口进度或执行权已变化，停止重复推进")

    def cancel_queued(self, run_id: str) -> None:
        """暂停时放弃尚未开始的抓取，不把它记成来源失败。"""
        with self.connection() as store:
            store._execute("""UPDATE processing_runs SET status='failed',finished_at=%s,error=%s
                WHERE run_id=%s AND status='queued'""", (_now(), "已暂停，未开始的窗口不再执行", run_id))

    def finish(self, run_id: str, runner_id: str, *, error: str | None = None):
        """成功或失败都写明确终态；错误必须由调用方提供固定的脱敏文本。"""
        with self.connection() as store:
            store._execute("""UPDATE processing_runs SET status=%s,finished_at=%s,error=%s
                WHERE run_id=%s AND status='running' AND runner_id=%s""",
                ("failed" if error else "succeeded", _now(), error, run_id, runner_id))

    def interrupted_window(self, uid: str, mode: str, window: dict):
        """寻找尚未恢复的中断窗口，不把普通重复回放包装成新的事件变化。"""
        with self.connection() as store:
            rows, _ = store._execute("SELECT " + SUMMARY_COLUMNS + """,current_baseline FROM processing_runs WHERE uid=%s AND mode=%s
                AND status='failed' AND recovered_by IS NULL AND current_window IS NOT NULL
                ORDER BY finished_at DESC""", (uid, mode))
        for row in rows:
            item = self._record(row, internal=True)
            if item["current_window"] == window:
                return item
        return None

    def mark_recovered(self, failed_run_id: str, new_run_id: str):
        """只有恢复周期已持久化后才标记旧任务，后续普通重跑仍为零增量。"""
        with self.connection() as store:
            store._execute("UPDATE processing_runs SET recovered_by=%s WHERE run_id=%s AND recovered_by IS NULL",
                           (new_run_id, failed_run_id))
