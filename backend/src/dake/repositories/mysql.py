"""本地 MySQL 存储：按客户串行写入，原子保存事件和消息处理标记。

业务数据只使用消息、事件两张表。每个分析版本的消息摘要与事件变化在同一
事务提交。提交应答不明须先核实执行收据，不能自动重试。正文只进入本地数据库持久化目录，异常
信息不会包含 SQL 参数、聊天内容或数据库凭据。
"""

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import re
from typing import Iterable, Iterator

import pymysql
from pymysql.cursors import DictCursor

from ..domain.models import Event, Message, TZ, message_order, parse_time


class StorageError(RuntimeError):
    """经过脱敏、可直接用于命令行提示的数据库错误。"""


class TransactionOutcomeUnknown(StorageError):
    """提交或回滚未得到确认；不得把连接异常当成可以安全重跑。"""


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decoded(value: str | bytes | dict) -> dict:
    return value if isinstance(value, dict) else json.loads(value)


def _sql_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("数据库查询时间必须带时区")
    return value.astimezone(TZ).replace(tzinfo=None)


class MySQLStore:
    """由一个业务实例顺序使用的数据库连接。

    写入必须位于 ``transaction(uid)`` 中。并发工作实例各自建立连接，同一
    客户共用数据库命名锁，即便使用不同分析版本也不能同时修改该客户数据。
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ):
        """连接本项目本地 MySQL；主动拒绝远程地址，防止误连 mars。"""
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("一期数据库仅允许连接本机，禁止使用 mars 或远程数据库")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("数据库端口无效")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", database):
            raise ValueError("数据库名称无效")
        if not user or not password:
            raise ValueError("请在本地 .env 配置数据库用户和密码")
        self.database = database
        self._transaction_uid: str | None = None
        self._customer_uid: str | None = None
        self._closed = False
        self.suppress_revision = False
        self._pipeline_table: bool | None = None
        try:
            self._conn = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                charset="utf8mb4",
                cursorclass=DictCursor,
                autocommit=True,
                connect_timeout=10,
                read_timeout=30,
                write_timeout=30,
            )
        except pymysql.MySQLError as error:
            raise self._safe_error(error) from None

    @staticmethod
    def _safe_error(error: pymysql.MySQLError) -> StorageError:
        code = error.args[0] if error.args and isinstance(error.args[0], int) else "unknown"
        return StorageError(f"MySQL 操作失败（错误码 {code}）；请检查本地数据库状态与配置")

    def _execute(self, statement: str, args: tuple = ()) -> tuple[list[dict], int]:
        if self._closed:
            raise StorageError("数据库连接已关闭")
        try:
            with self._conn.cursor() as cursor:
                count = cursor.execute(statement, args)
                rows = list(cursor.fetchall()) if cursor.description else []
                return rows, count
        except pymysql.MySQLError as error:
            raise self._safe_error(error) from None

    def init_schema(self) -> None:
        """幂等创建消息和事件表；禁止在业务事务中执行会隐式提交的建表语句。"""
        if self._transaction_uid is not None:
            raise StorageError("建表不能在业务事务中执行")
        self._execute(
            """CREATE TABLE IF NOT EXISTS messages (
                source VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
                message_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
                uid VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
                sent_at DATETIME(6) NOT NULL,
                digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                payload JSON NOT NULL,
                processed_by JSON NOT NULL,
                PRIMARY KEY (source, message_id),
                KEY ix_messages_uid_time (uid, sent_at, message_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"""
        )
        self._execute(
            """CREATE TABLE IF NOT EXISTS events (
                event_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
                uid VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
                stream VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
                type VARCHAR(32) NOT NULL,
                topic_key VARCHAR(512) NULL,
                start_time DATETIME(6) NOT NULL,
                end_time DATETIME(6) NOT NULL,
                status VARCHAR(128) NOT NULL,
                review_flag VARCHAR(128) NULL,
                payload JSON NOT NULL,
                PRIMARY KEY (event_id),
                KEY ix_events_uid_stream_end (uid, stream, end_time)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"""
        )

    def _require_transaction(self, uid: str) -> None:
        if self._transaction_uid != uid:
            raise StorageError("写入必须位于对应客户的 transaction(uid) 中")

    @contextmanager
    def customer_lock(self, uid: str) -> Iterator["MySQLStore"]:
        """执行记录预检与业务事务共用客户锁，内层同客户调用不重复获取。"""
        if not uid or len(uid) > 128:
            raise ValueError("客户 UID 无效")
        if getattr(self, "_customer_uid", None) is not None:
            if self._customer_uid != uid:
                raise StorageError("不能在客户锁内切换客户")
            yield self
            return
        lock_name = "dake:" + hashlib.sha256(f"{self.database}\0{uid}".encode()).hexdigest()[:59]
        rows, _ = self._execute("SELECT GET_LOCK(%s, 10) AS acquired", (lock_name,))
        if not rows or rows[0]["acquired"] != 1:
            raise StorageError("该客户正在处理中，请稍后重试")
        self._customer_uid = uid
        try:
            yield self
        finally:
            self._customer_uid = None
            try:
                self._execute("SELECT RELEASE_LOCK(%s) AS released", (lock_name,))
            except StorageError:
                # 断开连接也会释放命名锁；保留原始事务异常，不让释放锁的错误覆盖它。
                self.close()

    @contextmanager
    def metadata_transaction(self) -> Iterator["MySQLStore"]:
        """调度和执行权使用的短事务，不持有客户命名锁，也不能写消息正文。"""
        if self._transaction_uid is not None:
            raise StorageError("不允许嵌套数据库事务")
        self._transaction_uid = "metadata"
        try:
            self._conn.begin()
            try:
                yield self
            except BaseException:
                try:
                    self._conn.rollback()
                except BaseException:
                    try:
                        self.close()
                    finally:
                        raise TransactionOutcomeUnknown("事务回滚未确认，须核实执行收据，禁止自动重跑") from None
                raise
            try:
                self._conn.commit()
            except BaseException:
                try:
                    self.close()
                finally:
                    raise TransactionOutcomeUnknown("事务提交结果不明，须核实执行收据，禁止自动重跑") from None
        finally:
            self._transaction_uid = None

    @contextmanager
    def transaction(self, uid: str) -> Iterator["MySQLStore"]:
        """业务写入原子提交；提交响应不明时关闭原连接并明确阻止自动重试。"""
        if self._transaction_uid is not None:
            raise StorageError("不允许嵌套数据库事务")
        with self.customer_lock(uid):
            self._transaction_uid = uid
            try:
                self._conn.begin()
                try:
                    # 先锁该客户的执行权行，再锁消息，避免与提交事务交叉等待。
                    self._lock_pipeline(uid)
                    yield self
                except BaseException:
                    try:
                        self._conn.rollback()
                    except BaseException:
                        try:
                            self.close()
                        finally:
                            raise TransactionOutcomeUnknown("事务回滚未确认，须核实执行收据，禁止自动重跑") from None
                    raise
                try:
                    self._conn.commit()
                except BaseException:
                    try:
                        self.close()
                    finally:
                        raise TransactionOutcomeUnknown("事务提交结果不明，须核实执行收据，禁止自动重跑") from None
            except pymysql.MySQLError as error:
                raise self._safe_error(error) from None
            finally:
                self._transaction_uid = None

    def upsert_messages(self, messages: Iterable[Message]) -> int:
        """按来源和消息 ID 幂等保存，返回新增或内容变化的条数。

        正文修订时保留旧处理摘要，由 pending_messages 识别需要重新分析的版本；
        不允许同一消息 ID 从原客户挪到另一客户。
        """
        changed = 0
        for message in messages:
            self._require_transaction(message.uid)
            rows, _ = self._execute(
                "SELECT uid, digest, sent_at FROM messages WHERE source=%s AND message_id=%s FOR UPDATE",
                (message.source, message.message_id),
            )
            previous_sent = None
            if rows:
                if rows[0]["uid"] != message.uid:
                    raise StorageError("同一消息 ID 已属于其他客户，拒绝覆盖")
                if rows[0]["digest"] == message.digest:
                    continue
                previous_sent = rows[0].get("sent_at")
                self._execute(
                    "UPDATE messages SET sent_at=%s, digest=%s, payload=%s WHERE source=%s AND message_id=%s AND uid=%s",
                    (
                        _sql_time(message.sent_at), message.digest, _json(message.to_dict()),
                        message.source, message.message_id, message.uid,
                    ),
                )
            else:
                self._execute(
                    "INSERT INTO messages (source,message_id,uid,sent_at,digest,payload,processed_by) VALUES (%s,%s,%s,%s,%s,%s,JSON_OBJECT())",
                    (
                        message.source, message.message_id, message.uid,
                        _sql_time(message.sent_at), message.digest, _json(message.to_dict()),
                    ),
                )
            changed += 1
            if not self.suppress_revision:
                self._bump_input_revision(message, previous_sent)
        return changed

    def _pipeline_ready(self) -> bool:
        """流水表未迁移时不改原有写入；结果只在本连接缓存。"""
        if not hasattr(self, "_pipeline_table"):
            return False
        if self._pipeline_table is None:
            rows, _ = self._execute("SELECT 1 AS present FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name='uid_pipeline_state'")
            self._pipeline_table = bool(rows)
        return self._pipeline_table

    def _lock_pipeline(self, uid: str) -> None:
        """有执行权行时先锁定它；没有行表示该客户尚未进入独立推进。"""
        if not uid or uid.startswith("task:") or not self._pipeline_ready():
            return
        self._execute("SELECT uid FROM uid_pipeline_state WHERE uid=%s FOR UPDATE", (uid,))

    def _bump_input_revision(self, message: Message, previous_sent) -> None:
        """活动窗口结束前的新消息或时间修订使已冻结输入失效，不在模型事务外另行判断。"""
        if not self._pipeline_ready():
            return
        moments = [message.sent_at]
        if isinstance(previous_sent, datetime):
            if previous_sent.tzinfo is None:
                previous_sent = previous_sent.replace(tzinfo=TZ)
            moments.append(previous_sent)
        for moment in moments:
            self._execute("""UPDATE uid_pipeline_state SET input_revision=input_revision+1, updated_at=%s
                WHERE uid=%s AND active_window_end IS NOT NULL AND %s < active_window_end""",
                (datetime.now(TZ).replace(tzinfo=None), message.uid, _sql_time(moment)))

    @staticmethod
    def _bounds(start: datetime, end: datetime) -> tuple[datetime, datetime]:
        if start >= end:
            raise ValueError("查询窗口必须满足 start < end")
        return _sql_time(start), _sql_time(end)

    def pending_messages(self, uid: str, start: datetime, end: datetime, stream: str) -> list[Message]:
        """按发送时间返回窗口内尚未被该分析版本处理的消息，包含迟到及修订消息。"""
        lower, upper = self._bounds(start, end)
        path = "$." + json.dumps(stream, ensure_ascii=False)
        rows, _ = self._execute(
            """SELECT payload FROM messages
               WHERE uid=%s AND sent_at >= %s AND sent_at < %s
                 AND (JSON_EXTRACT(processed_by,%s) IS NULL
                      OR JSON_UNQUOTE(JSON_EXTRACT(processed_by,%s)) <> digest)""",
            (uid, lower, upper, path, path),
        )
        return sorted((Message.from_dict(_decoded(row["payload"])) for row in rows), key=message_order)

    def context_messages(self, uid: str, start: datetime, end: datetime) -> list[Message]:
        """读取客户在 [start, end) 内的上下文，统一按发送时间与数字消息 ID 排序。"""
        lower, upper = self._bounds(start, end)
        rows, _ = self._execute(
            "SELECT payload FROM messages WHERE uid=%s AND sent_at >= %s AND sent_at < %s",
            (uid, lower, upper),
        )
        return sorted((Message.from_dict(_decoded(row["payload"])) for row in rows), key=message_order)

    def history_messages(self, uid, references, before):
        """按已核验的证据版本取正文；版本不符时不读取后来编辑的 payload。"""
        if not uid or before.tzinfo is None:
            raise ValueError("历史正文读取必须限定客户和时间")
        references = list(references)
        if not references:
            return []
        conditions, args = [], [uid, _sql_time(before)]
        for ref in references:
            conditions.append("(source=%s AND message_id=%s AND digest=%s AND sent_at=%s)")
            args.extend((ref["source"], ref["message_id"], ref["digest"], _sql_time(parse_time(ref["send_time"]))))
        rows, _ = self._execute(
            "SELECT payload FROM messages WHERE uid=%s AND sent_at < %s AND (" +
            " OR ".join(conditions) + ") LIMIT %s", tuple(args + [len(references)]),
        )
        return sorted((Message.from_dict(_decoded(row["payload"])) for row in rows), key=message_order)

    def history_neighbor_heads(self, uid, anchor: Message, before, *, span, limit):
        """只取同群邻域的有限元数据；正文须另经历史证据版本核验后读取。"""
        if (not uid or anchor.uid != uid or not anchor.conversation_id or before.tzinfo is None
                or anchor.sent_at >= before or type(limit) is not int or limit <= 0 or span.total_seconds() <= 0):
            raise ValueError("历史邻域范围或预算无效")
        rows, _ = self._execute(
            "SELECT source,message_id,sent_at,digest FROM messages "
            "WHERE uid=%s AND source=%s AND JSON_UNQUOTE(JSON_EXTRACT(payload,'$.conversation_id'))=%s "
            "AND sent_at >= %s AND sent_at < %s AND message_id<>%s "
            "ORDER BY ABS(TIMESTAMPDIFF(MICROSECOND,sent_at,%s)),sent_at,message_id LIMIT %s",
            (uid, anchor.source, anchor.conversation_id, _sql_time(anchor.sent_at - span),
             _sql_time(min(before, anchor.sent_at + span)), anchor.message_id, _sql_time(anchor.sent_at), limit),
        )
        return [dict(source=row["source"], message_id=row["message_id"], digest=row["digest"],
                     send_time=parse_time(row["sent_at"]).isoformat()) for row in rows]

    def latest_message(self, uid: str, before: datetime, stream: str | None = None) -> Message | None:
        """取得窗口之前最后一条消息；指定分析流时只计入已完成的当前版本。"""
        condition = "uid=%s AND sent_at < %s"
        args = (uid, _sql_time(before))
        if stream is not None:
            condition += " AND JSON_UNQUOTE(JSON_EXTRACT(processed_by,%s)) = digest"
            args += ("$." + json.dumps(stream, ensure_ascii=False),)
        rows, _ = self._execute(
            f"SELECT payload FROM messages WHERE {condition} AND sent_at=("
            f"SELECT MAX(sent_at) FROM messages WHERE {condition})",
            args + args,
        )
        messages = [Message.from_dict(_decoded(row["payload"])) for row in rows]
        # 同秒消息 ID 在数据库中是字符串，交给统一排序函数按数字比较，避免词典序误判。
        return max(messages, key=message_order) if messages else None

    def mark_processed(self, messages: Iterable[Message], stream: str) -> None:
        """标记当前消息版本已处理；须与事件保存共用事务，不能提前推进处理状态。"""
        if not stream or len(stream) > 128:
            raise ValueError("分析流标识无效")
        path = "$." + json.dumps(stream, ensure_ascii=False)
        for message in messages:
            self._require_transaction(message.uid)
            rows, _ = self._execute(
                "SELECT uid,digest FROM messages WHERE source=%s AND message_id=%s FOR UPDATE",
                (message.source, message.message_id),
            )
            if not rows or rows[0]["uid"] != message.uid or rows[0]["digest"] != message.digest:
                raise StorageError("待标记消息已变化或不存在，当前周期须重新处理")
            self._execute(
                "UPDATE messages SET processed_by=JSON_SET(processed_by,%s,%s) WHERE source=%s AND message_id=%s AND uid=%s",
                (path, message.digest, message.source, message.message_id, message.uid),
            )

    def record_attributions(self, messages: Iterable[Message], stream: str, records: list[dict]) -> None:
        """复用消息 JSON 保存当前版本的归属收据；不新增表，不修改事件修订。"""
        if not stream or len(stream) > 128:
            raise ValueError("分析流标识无效")
        path = "$." + json.dumps("attribution:" + stream, ensure_ascii=False)
        for message in messages:
            relevant = [record for record in records if any(
                ref["message_id"] == message.key for ref in record["new_evidence"])]
            if not relevant:
                continue
            self._require_transaction(message.uid)
            _, count = self._execute(
                "UPDATE messages SET processed_by=JSON_SET(processed_by,%s,CAST(%s AS JSON)) "
                "WHERE source=%s AND message_id=%s AND uid=%s AND digest=%s",
                (path, _json(dict(digest=message.digest, records=relevant)),
                 message.source, message.message_id, message.uid, message.digest),
            )
            if count != 1:
                raise StorageError("归属收据的消息版本已变化或不存在，当前周期须重新处理")

    def list_events(self, uid: str, stream: str) -> list[Event]:
        """读取一个客户、一个分析版本的事件；观察窗口与关闭宽限由业务层判断。"""
        rows, _ = self._execute(
            "SELECT payload FROM events WHERE uid=%s AND stream=%s ORDER BY start_time,event_id",
            (uid, stream),
        )
        return [Event.from_dict(_decoded(row["payload"])) for row in rows]

    def history_event_heads(self, uid, stream, before, *, limit, since=None, cursor=None, event_ids=None):
        """先按客户和流限定，按索引顺序有界读取元数据，不查询正文或摘要。"""
        if not uid or not stream or type(limit) is not int or limit <= 0:
            raise ValueError("历史检索范围或分页预算无效")
        conditions = ["uid=%s", "stream=%s", "end_time < %s"]
        args = [uid, stream, _sql_time(before)]
        if since is not None:
            conditions.append("end_time >= %s")
            args.append(_sql_time(since))
        if cursor is not None:
            conditions.append("(end_time < %s OR (end_time = %s AND event_id < %s))")
            args.extend((_sql_time(cursor[0]), _sql_time(cursor[0]), cursor[1]))
        if event_ids is not None:
            if not event_ids:
                return []
            conditions.append("event_id IN (" + ",".join("%s" for _ in event_ids) + ")")
            args.extend(event_ids)
        rows, _ = self._execute(
            "SELECT event_id,end_time,JSON_EXTRACT(payload,'$.updated_by[*].window_end') AS revision_ends "
            "FROM events WHERE " + " AND ".join(conditions) +
            " ORDER BY end_time DESC,event_id DESC LIMIT %s", tuple(args + [limit]),
        )
        return [dict(event_id=row["event_id"], end_time=parse_time(row["end_time"]),
                     revision_ends=_decoded(row["revision_ends"]) if row["revision_ends"] else [])
                for row in rows]

    def history_event_snapshots(self, uid, stream, heads):
        """仅读取已核验元数据对应的最后快照；元数据变化则跳过，不回退当前值。"""
        if not uid or not stream:
            raise ValueError("历史检索必须限定客户及分析流")
        if not heads:
            return []
        conditions, args = [], [uid, stream]
        for head in heads:
            conditions.append("(event_id=%s AND JSON_EXTRACT(payload,'$.updated_by[*].window_end')=CAST(%s AS JSON))")
            args.extend((head["event_id"], json.dumps(head["revision_ends"])))
        rows, _ = self._execute(
            "SELECT JSON_EXTRACT(payload,'$.updated_by[last].snapshot') AS snapshot "
            "FROM events WHERE uid=%s AND stream=%s AND (" + " OR ".join(conditions) + ") LIMIT %s",
            tuple(args + [len(heads)]),
        )
        return [Event.from_dict(_decoded(row["snapshot"])) for row in rows if row["snapshot"]]

    def save_event(self, event: Event) -> None:
        """按稳定 event_id 创建或更新事件，保存证据和修订记录，并防止跨客户覆盖。"""
        self._require_transaction(event.uid)
        rows, _ = self._execute(
            "SELECT uid,stream FROM events WHERE event_id=%s FOR UPDATE", (event.event_id,)
        )
        if rows and (rows[0]["uid"] != event.uid or rows[0]["stream"] != event.stream):
            raise StorageError("事件 ID 已属于其他客户或分析流，拒绝覆盖")
        values = (
            event.uid, event.stream, event.type, event.topic_key,
            _sql_time(event.start_time), _sql_time(event.end_time), event.status,
            event.review_flag, _json(event.to_dict()), event.event_id,
        )
        if rows:
            self._execute(
                """UPDATE events SET uid=%s,stream=%s,type=%s,topic_key=%s,start_time=%s,
                   end_time=%s,status=%s,review_flag=%s,payload=%s WHERE event_id=%s""",
                values,
            )
        else:
            self._execute(
                """INSERT INTO events (uid,stream,type,topic_key,start_time,end_time,status,
                   review_flag,payload,event_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                values,
            )

    def close(self) -> None:
        """释放本实例的数据库连接，可重复调用。"""
        if not self._closed:
            self._closed = True
            self._conn.close()
