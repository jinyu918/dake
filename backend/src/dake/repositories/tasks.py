"""任务定义及编排状态的持久存储；短事务与独占任务锁保护并发提交。"""

from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

from .runs import RunRepository, RunConflict, _encoded, _decode, _now
from .mysql import _sql_time
from ..domain.models import TZ
from ..config import PROJECT_ROOT


def record(row):
    """统一 JSON 和上海时间序列化，不暴露原始消息。"""
    result = dict(row)
    for key in ("config", "frozen_uids", "state"):
        if key in result:
            result[key] = _decode(result[key])
    for key, value in result.items():
        if isinstance(value, datetime):
            result[key] = value.replace(tzinfo=TZ).isoformat()
    if "enabled" in result:
        result["enabled"] = bool(result["enabled"])
    return result


class TaskRepository(RunRepository):
    """任务中心仅编排既有运行，不复制模型处理或事件更新。"""

    def init_schema(self):
        with self.connection() as store:
            migration = PROJECT_ROOT / "backend/migrations/004_task_center.sql"
            for statement in migration.read_text().split(";"):
                if statement.strip():
                    store._execute(statement)

    @contextmanager
    def locked(self, task_id):
        """多个网页及调度实例的操作在同一个任务锁内顺序提交。"""
        key = "dake:task:" + task_id
        with self.connection() as store:
            rows, _ = store._execute("SELECT GET_LOCK(%s,5) AS ok", (key,))
            if not rows or rows[0]["ok"] != 1:
                raise RunConflict()
            try:
                with store.transaction("task:" + task_id):
                    yield store
            finally:
                store._execute("SELECT RELEASE_LOCK(%s)", (key,))

    def create(self, config, frozen_uids):
        task_id = str(uuid4())
        with self.connection() as store:
            store._execute("INSERT INTO task_definitions (task_id,config,frozen_uids,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s)", (task_id, _encoded(config), _encoded(frozen_uids), _now(), _now()))
        return self.get(task_id)

    def get(self, task_id):
        with self.connection() as store:
            rows, _ = store._execute("SELECT * FROM task_definitions WHERE task_id=%s", (task_id,))
        return record(rows[0]) if rows else None

    def list(self):
        with self.connection() as store:
            rows, _ = store._execute("SELECT * FROM task_definitions ORDER BY created_at DESC")
        return [record(row) for row in rows]

    def update_config(self, task_id, config, version, frozen_uids):
        with self.locked(task_id) as store:
            rows, _ = store._execute("SELECT * FROM task_definitions WHERE task_id=%s", (task_id,))
            if not rows:
                raise ValueError("任务不存在")
            old = record(rows[0])
            if old["version"] != version:
                raise RunConflict("配置已经变化，请刷新后重试")
            # 已启用的持续任务保持游标，修改模式时暂停，防止保存即意外启动。
            enabled = old["enabled"] and config["mode"] == "automatic"
            store._execute("UPDATE task_definitions SET config=%s,frozen_uids=%s,version=version+1,enabled=%s,"
                "updated_at=%s WHERE task_id=%s", (_encoded(config), _encoded(frozen_uids), enabled, _now(), task_id))
        return self.get(task_id)

    def set_enabled(self, task_id, enabled):
        with self.locked(task_id) as store:
            store._execute("UPDATE task_definitions SET enabled=%s,next_run_at=%s,updated_at=%s WHERE task_id=%s",
                (enabled, _now() if enabled else None, _now(), task_id))
        return self.get(task_id)

    def executions(self, task_id=None, *, active=False):
        where, args = ("WHERE task_id=%s", (task_id,)) if task_id else ("", ())
        if active:
            where += (" AND " if where else " WHERE ") + "status IN ('queued','running')"
        with self.connection() as store:
            rows, _ = store._execute("SELECT * FROM task_executions " + where + " ORDER BY created_at DESC LIMIT 100", args)
        return [record(row) for row in rows]

    def execution(self, execution_id):
        with self.connection() as store:
            rows, _ = store._execute("SELECT * FROM task_executions WHERE execution_id=%s", (execution_id,))
        return record(rows[0]) if rows else None

    def start_execution(self, task_id, state, *, automatic=False):
        execution_id = str(uuid4())
        with self.locked(task_id) as store:
            tasks, _ = store._execute("SELECT * FROM task_definitions WHERE task_id=%s", (task_id,))
            if not tasks or (automatic and not tasks[0]["enabled"]):
                return None
            if tasks[0]["version"] != state["version"]:
                raise RunConflict("配置已变化")
            active, _ = store._execute("SELECT execution_id FROM task_executions WHERE task_id=%s "
                "AND status IN ('queued','running')", (task_id,))
            if active:
                raise RunConflict("该任务有未完成的执行")
            store._execute("INSERT INTO task_executions (execution_id,task_id,status,stage,state,created_at) "
                "VALUES (%s,%s,'queued','discovery',%s,%s)", (execution_id, task_id, _encoded(state), _now()))
            store._execute("UPDATE task_definitions SET cursor_at=COALESCE(cursor_at,%s),updated_at=%s WHERE task_id=%s",
                (_sql_time(datetime.fromisoformat(state["start"])), _now(), task_id))
        return self.execution(execution_id)

    def save_execution(self, execution, *, cursor=None, next_at=None):
        terminal = execution["status"] not in ("queued", "running")
        with self.locked(execution["task_id"]) as store:
            store._execute("UPDATE task_executions SET status=%s,stage=%s,state=%s,finished_at=%s WHERE execution_id=%s",
                (execution["status"], execution["stage"], _encoded(execution["state"]), _now() if terminal else None,
                 execution["execution_id"]))
            if terminal:
                store._execute("UPDATE task_definitions SET cursor_at=IF(%s IS NULL,cursor_at,GREATEST(cursor_at,%s)),"
                    "next_run_at=%s,updated_at=%s WHERE task_id=%s", (cursor, cursor, next_at, _now(), execution["task_id"]))
