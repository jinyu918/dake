"""来源水位、客户执行权、分析窗口与隔离队列。业务提交必须走调用方已打开的事务。"""

from datetime import datetime
from uuid import uuid4

from ..config import PROJECT_ROOT
from ..domain.models import TZ
from .mysql import MySQLStore, StorageError, _sql_time
from .runs import RunRepository, _encoded


class PipelineError(StorageError):
    """执行权、水位或隔离写入失败；不表示事务提交结果不明。"""


def _naive(value):
    """把上海时间存成无时区的 DATETIME，和消息表一致。库内读出的值已经是该时区。"""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime) and value.tzinfo is None:
        return value
    return _sql_time(value)


def _aware(value):
    if value is None:
        return None
    return value.replace(tzinfo=TZ) if value.tzinfo is None else value


def apply_statements(store, path):
    """逐条执行迁移；重复的列和索引保持已有结构。语句前的注释行不使整段被跳过。"""
    for statement in path.read_text(encoding="utf-8").split(";"):
        lines = [line for line in statement.splitlines() if line.strip() and not line.strip().startswith("--")]
        text = "\n".join(lines).strip()
        if not text:
            continue
        try:
            store._execute(text)
        except StorageError as error:
            detail = str(error)
            if all(code not in detail for code in ("错误码 1060", "错误码 1061", "错误码 1146")):
                raise


class PipelineRepository(RunRepository):
    """跨进程容量、抓取水位和客户执行权；不复制事件匹配。"""

    def init_schema(self):
        with self.connection() as store:
            apply_statements(store, PROJECT_ROOT / "backend/migrations/005_customer_pipelines.sql")
            self._seed_runtime_slots(store)

    def acquire(self, uid, window, owner, stream, *, phase, catchup_target, lease_seconds):
        """占用该客户的活动窗口。租约已过期但仍有归属时拒绝，交给核实而不是直接重跑。"""
        start, end = _naive(window.start), _naive(window.end)
        target = _naive(catchup_target) or end
        with self.connection() as store:
            with store.metadata_transaction():
                rows, _ = store._execute("SELECT * FROM uid_pipeline_state WHERE uid=%s FOR UPDATE", (uid,))
                if not rows:
                    store._execute("""INSERT INTO uid_pipeline_state
                        (uid,active_stream,origin,catchup_target,phase,updated_at)
                        VALUES (%s,%s,%s,%s,%s,NOW(6))""", (uid, stream, start, target, phase or "catchup"))
                    rows, _ = store._execute("SELECT * FROM uid_pipeline_state WHERE uid=%s FOR UPDATE", (uid,))
                row = rows[0]
                if row["block_reason"]:
                    raise PipelineError(row["block_reason"])
                if row["owner"]:
                    raise PipelineError("该客户执行权待核实，租约过期也不会直接换人重跑模型")
                analyzed = row["analyzed_until"]
                if analyzed is not None and _naive(analyzed) != start:
                    if _naive(analyzed) > start:
                        raise PipelineError("已进入实时或已有更晚结果，旧历史不能倒灌主流")
                    raise PipelineError("更早的历史窗口尚未完成，不能跳过")
                if row["phase"] == "live" and row["catchup_target"] is not None and end <= row["catchup_target"] and start < row["catchup_target"]:
                    raise PipelineError("已进入实时，旧历史不能倒灌主流")
                _, changed = store._execute("""UPDATE uid_pipeline_state SET owner=%s, epoch=epoch+1,
                    active_stream=%s, active_window_start=%s, active_window_end=%s,
                    lease_until=DATE_ADD(NOW(6), INTERVAL %s SECOND), heartbeat_at=NOW(6),
                    catchup_target=COALESCE(catchup_target,%s), phase=IF(%s='live','live',phase), updated_at=NOW(6)
                    WHERE uid=%s AND owner IS NULL AND block_reason IS NULL""",
                    (owner, stream, start, end, int(lease_seconds), target, phase or "catchup", uid))
                if changed != 1:
                    raise PipelineError("该客户执行权已变化，本次不能开始")
                current, _ = store._execute("SELECT epoch,input_revision FROM uid_pipeline_state WHERE uid=%s", (uid,))
                window_id = str(uuid4())
                store._execute("""INSERT INTO analysis_windows
                    (window_id,uid,stream,config_digest,window_start,window_end,predecessor_end,status,
                     claimed_epoch,queued_at,started_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'running',%s,NOW(6),NOW(6))
                    ON DUPLICATE KEY UPDATE status='running', claimed_epoch=VALUES(claimed_epoch),
                    started_at=NOW(6), error_code=NULL""",
                    (window_id, uid, stream, "window", start, end, analyzed, current[0]["epoch"]))
        return dict(uid=uid, owner=owner, epoch=int(current[0]["epoch"]), revision=int(current[0]["input_revision"]),
                    start=start, end=end, stream=stream)

    def heartbeat(self, uid, owner, epoch, lease_seconds):
        """只延长仍属于本代次且未过期的租约，不能把过期代次续回去。"""
        with self.connection() as store:
            _, changed = store._execute("""UPDATE uid_pipeline_state
                SET lease_until=DATE_ADD(NOW(6), INTERVAL %s SECOND), heartbeat_at=NOW(6), updated_at=NOW(6)
                WHERE uid=%s AND owner=%s AND epoch=%s AND lease_until>NOW(6)""",
                (int(lease_seconds), uid, owner, epoch))
        return changed == 1

    def fence(self, store, claim, *, incomplete):
        """在业务事务的最后一步原子核对归属、代次、租约、前驱水位和输入版本。"""
        phase_end = claim["end"]
        _, changed = store._execute("""UPDATE uid_pipeline_state SET
            analyzed_until=%s, active_window_start=NULL, active_window_end=NULL, owner=NULL, lease_until=NULL,
            last_served_at=NOW(6), incomplete=GREATEST(incomplete,%s),
            phase=CASE WHEN catchup_target IS NOT NULL AND %s>=catchup_target THEN 'live' ELSE phase END,
            planned_until=GREATEST(COALESCE(planned_until,%s),%s), updated_at=NOW(6)
            WHERE uid=%s AND owner=%s AND epoch=%s AND active_stream=%s AND lease_until>NOW(6)
            AND block_reason IS NULL AND input_revision=%s AND active_window_start<=>%s AND active_window_end<=>%s
            AND analyzed_until<=>%s""",
            (phase_end, 1 if incomplete else 0, phase_end, phase_end, phase_end,
             claim["uid"], claim["owner"], claim["epoch"], claim["stream"], claim["revision"],
             claim["start"], claim["end"], claim["start"] if claim.get("predecessor", claim["start"]) else None))
        # 首窗的前驱水位是空。上面把空水位误写成窗口起点时改为再试一次空前驱。
        if changed != 1:
            _, changed = store._execute("""UPDATE uid_pipeline_state SET
                analyzed_until=%s, active_window_start=NULL, active_window_end=NULL, owner=NULL, lease_until=NULL,
                last_served_at=NOW(6), incomplete=GREATEST(incomplete,%s),
                phase=CASE WHEN catchup_target IS NOT NULL AND %s>=catchup_target THEN 'live' ELSE phase END,
                planned_until=GREATEST(COALESCE(planned_until,%s),%s), updated_at=NOW(6)
                WHERE uid=%s AND owner=%s AND epoch=%s AND active_stream=%s AND lease_until>NOW(6)
                AND block_reason IS NULL AND input_revision=%s AND active_window_start<=>%s AND active_window_end<=>%s
                AND analyzed_until IS NULL""",
                (phase_end, 1 if incomplete else 0, phase_end, phase_end, phase_end,
                 claim["uid"], claim["owner"], claim["epoch"], claim["stream"], claim["revision"],
                 claim["start"], claim["end"]))
        if changed != 1:
            raise PipelineError("执行权、租约或前驱水位已变化，本窗口结果已回滚")
        store._execute("""UPDATE analysis_windows SET status='committed', committed_at=NOW(6), error_code=NULL
            WHERE uid=%s AND stream=%s AND window_start=%s AND window_end=%s AND claimed_epoch=%s""",
            (claim["uid"], claim["stream"], claim["start"], claim["end"], claim["epoch"]))

    def isolate(self, store, records, window):
        """同一事务写入隔离队列；正文不入库。"""
        for record in records:
            store._execute("""INSERT INTO isolated_records
                (isolation_id,uid,source,message_id,digest,send_time,kind,reason,window_start,window_end,payload,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(6))
                ON DUPLICATE KEY UPDATE reason=VALUES(reason)""",
                (str(uuid4()), record.get("uid"), record["source"], record.get("message_id"), record.get("digest"),
                 _naive(record.get("send_time")), record["kind"], record["reason"][:255],
                 _naive(window.start), _naive(window.end),
                 _encoded({"conversation_id": record.get("conversation_id")} if record.get("conversation_id") else {})))

    def release_claim(self, claim):
        """模型尚未调用或已明确失败时释放本代次归属，不推进分析水位。"""
        with self.connection() as store:
            store._execute("""UPDATE uid_pipeline_state SET owner=NULL, lease_until=NULL,
                active_window_start=NULL, active_window_end=NULL, updated_at=NOW(6)
                WHERE uid=%s AND owner=%s AND epoch=%s""",
                (claim["uid"], claim["owner"], claim["epoch"]))

    def block(self, uid, owner, epoch, kind, reason):
        """按客户阻断。换分析流、换任务或重启都不能清掉这个原因。"""
        with self.connection() as store:
            _, changed = store._execute("""UPDATE uid_pipeline_state SET block_kind=%s, block_reason=%s,
                owner=NULL, lease_until=NULL, active_window_start=NULL, active_window_end=NULL, updated_at=NOW(6)
                WHERE uid=%s AND (owner IS NULL OR (owner=%s AND epoch=%s)) AND block_reason IS NULL""",
                (kind, reason[:255], uid, owner, epoch))
            if changed != 1:
                existing, _ = store._execute("SELECT block_kind,block_reason FROM uid_pipeline_state WHERE uid=%s", (uid,))
                if not existing or not existing[0]["block_reason"]:
                    raise PipelineError("客户阻断没有写上，执行仍须核实")

    def publish_source(self, window, *, message_count, isolated_count, complete, reason=None):
        """只有完整落库才推进抓取水位；隔离条数单独记录，不代表分析已覆盖。"""
        if not complete:
            with self.connection() as store:
                store._execute("""INSERT INTO source_state (source,incomplete_reason,updated_at)
                    VALUES ('official_chat',%s,NOW(6)) ON DUPLICATE KEY UPDATE
                    incomplete_reason=VALUES(incomplete_reason), updated_at=NOW(6)""", (reason or "来源不完整",))
            return False
        start, end = _naive(window.start), _naive(window.end)
        with self.connection() as store:
            with store.metadata_transaction():
                store._execute("""INSERT INTO source_state (source,coverage_start,updated_at)
                    VALUES ('official_chat',%s,NOW(6)) ON DUPLICATE KEY UPDATE
                    coverage_start=COALESCE(coverage_start,VALUES(coverage_start)), updated_at=NOW(6)""", (start,))
                rows, _ = store._execute(
                    "SELECT fetched_until FROM source_state WHERE source='official_chat' FOR UPDATE")
                current = _naive(rows[0]["fetched_until"]) if rows else None
                # 只能从空水位或紧邻的前驱向前；已经覆盖到本窗终点时不回退，也不能跨过缺口。
                if current is not None and current != start and current < end:
                    raise PipelineError("抓取水位不能跳过未完成区间")
                if current is None or current == start:
                    _, changed = store._execute("""UPDATE source_state
                        SET fetched_until=%s, incomplete_reason=NULL, updated_at=NOW(6)
                        WHERE source='official_chat' AND (fetched_until IS NULL OR fetched_until=%s)""",
                        (end, start))
                    if changed != 1:
                        raise PipelineError("抓取水位不能跳过未完成区间")
                store._execute("""INSERT INTO source_windows
                    (fetch_id,source,window_start,window_end,status,message_count,isolated_count,created_at,finished_at)
                    VALUES (%s,'official_chat',%s,%s,'complete',%s,%s,NOW(6),NOW(6))
                    ON DUPLICATE KEY UPDATE status='complete', message_count=VALUES(message_count),
                    isolated_count=VALUES(isolated_count), finished_at=NOW(6), error_reason=NULL""",
                    (str(uuid4()), start, end, message_count, isolated_count))
        return True

    def verify_expired(self, attempts):
        """租约到期后先核对回执和模型发送痕迹，不把 running 批量改成失败。"""
        actions = []
        with self.connection() as store:
            rows, _ = store._execute("""SELECT uid,owner,epoch,active_stream,active_window_start,active_window_end,block_reason
                FROM uid_pipeline_state WHERE owner IS NOT NULL AND (lease_until IS NULL OR lease_until<=NOW(6))
                AND IS_FREE_LOCK(CONCAT('dake:work:',COALESCE(owner,'')))=1""")
        for row in rows:
            action = self._verify_one(attempts, row)
            actions.append(action)
        self.recover_interrupted()
        return actions

    def _verify_one(self, attempts, row):
        uid, stream = row["uid"], row["active_stream"]
        with self.connection() as store:
            with store.metadata_transaction():
                locked, _ = store._execute("""SELECT owner,epoch,lease_until>NOW(6) AS live, block_reason
                    FROM uid_pipeline_state WHERE uid=%s FOR UPDATE""", (uid,))
                if not locked or locked[0]["owner"] != row["owner"] or locked[0]["epoch"] != row["epoch"] or locked[0]["live"]:
                    return dict(uid=uid, action="kept")
                attempt = self._latest_attempt(store, uid, stream, row)
                if attempt and attempt.get("receipt") is not None:
                    store._execute("""UPDATE uid_pipeline_state SET owner=NULL, lease_until=NULL, updated_at=NOW(6)
                        WHERE uid=%s AND epoch=%s""", (uid, row["epoch"]))
                    return dict(uid=uid, action="committed_display", attempt_id=attempt["attempt_id"])
                if attempt and attempt["outcome"] in {"commit_unknown", "model_unknown"}:
                    self._persist_block(store, row, attempt["outcome"], "提交或模型结果未核实，禁止换流重跑")
                    return dict(uid=uid, action=attempt["outcome"], attempt_id=attempt["attempt_id"])
                if attempt and self._model_unknown(attempt):
                    store._execute("""UPDATE processing_attempts SET outcome='model_unknown', model_state='unknown',
                        error_code='model_unknown', finished_at=%s
                        WHERE attempt_id=%s AND receipt IS NULL AND outcome='running'""",
                        (datetime.now(TZ).isoformat(), attempt["attempt_id"]))
                    self._persist_block(store, row, "model_unknown", "模型请求可能已发出但结果未知，禁止自动重发")
                    return dict(uid=uid, action="model_unknown", attempt_id=attempt["attempt_id"])
                if attempt and attempt["outcome"] == "running":
                    store._execute("""UPDATE processing_attempts SET outcome='not_committed', error_code='released',
                        finished_at=%s WHERE attempt_id=%s AND receipt IS NULL AND outcome='running'""",
                        (datetime.now(TZ).isoformat(), attempt["attempt_id"]))
                store._execute("""UPDATE uid_pipeline_state SET owner=NULL, lease_until=NULL,
                    active_window_start=NULL, active_window_end=NULL, updated_at=NOW(6)
                    WHERE uid=%s AND epoch=%s AND block_reason IS NULL""", (uid, row["epoch"]))
                return dict(uid=uid, action="requeued")

    @staticmethod
    def _latest_attempt(store, uid, stream, row):
        if not stream or row["active_window_start"] is None:
            return None
        rows, _ = store._execute("""SELECT attempt_id,outcome,receipt,stages,model_state FROM processing_attempts
            WHERE uid=%s AND stream=%s AND window_start=%s AND window_end=%s
            ORDER BY created_at DESC, attempt_id DESC LIMIT 1""",
            (uid, stream, _aware(row["active_window_start"]).isoformat(), _aware(row["active_window_end"]).isoformat()))
        if not rows:
            return None
        attempt = dict(rows[0])
        if isinstance(attempt.get("stages"), (str, bytes)):
            import json
            attempt["stages"] = json.loads(attempt["stages"])
        return attempt

    @staticmethod
    def _model_unknown(attempt):
        """已记录发送意图、却没有明确失败或完成时，不能当作普通网络错误重发。"""
        if attempt.get("model_state") in {"unknown", "maybe_sent"}:
            return True
        for stage in attempt.get("stages") or []:
            if stage.get("stage") == "model_completed":
                return False
            if stage.get("stage") == "model_failed" and stage.get("payload", {}).get("error_type") != "transport_error":
                return False
            if stage.get("stage") == "model_started":
                return True
        return False

    @staticmethod
    def _persist_block(store, row, kind, reason):
        store._execute("""UPDATE uid_pipeline_state SET block_kind=%s, block_reason=%s, owner=NULL, lease_until=NULL,
            active_window_start=NULL, active_window_end=NULL, updated_at=NOW(6)
            WHERE uid=%s AND epoch=%s""", (kind, reason, row["uid"], row["epoch"]))

    def blocked(self, uid):
        with self.connection() as store:
            rows, _ = store._execute("SELECT block_kind,block_reason,phase,analyzed_until,incomplete FROM uid_pipeline_state WHERE uid=%s", (uid,))
        if not rows or not rows[0]["block_reason"]:
            return None
        return dict(rows[0])

    def snapshot(self):
        """任务中心展示用的水位、积压和额度，不包含消息正文。"""
        with self.connection() as store:
            source, _ = store._execute("SELECT fetched_until,incomplete_reason FROM source_state WHERE source='official_chat'")
            limits, _ = store._execute("""SELECT resource, COUNT(*) AS slots,
                SUM(owner IS NOT NULL AND (lease_until>NOW(6)
                    OR IS_USED_LOCK(CONCAT('dake:work:',COALESCE(owner,''))) IS NOT NULL)) AS used,
                SUM(held_reason='live') AS live_slots
                FROM runtime_limits GROUP BY resource""")
            backlog, _ = store._execute("""SELECT COUNT(*) AS waiting,
                TIMESTAMPDIFF(SECOND, MIN(created_at), NOW(6)) AS oldest
                FROM processing_runs WHERE status='queued' AND mode<>'customer_sync'""")
            isolated, _ = store._execute("SELECT COUNT(*) AS total FROM isolated_records")
        return dict(source=source[0] if source else {}, limits=limits, backlog=backlog[0] if backlog else {},
                    isolated=isolated[0]["total"] if isolated else 0)
