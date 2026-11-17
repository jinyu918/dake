"""官方聊天适配与私有原始缓存；只转换消息，不负责事件分类。"""

from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from dake.ingestion.client import ChatRecordsClient, validate_dates, validate_uid, window_parameters
from dake.domain.models import Message, TZ, message_order


class AdapterError(ValueError):
    """输入畸形或不完整时停止摄入；错误文本不包含来源字段值。"""


def _text(value, field: str, *, required: bool = False) -> str:
    if not isinstance(value, str) or (required and not value):
        raise AdapterError(f"响应字段 {field} 必须是有效字符串")
    return value


def _count(value) -> int:
    if type(value) is not int or value < 0:
        raise AdapterError("响应 total 必须是非负整数")
    return value


class ChatRecordsAdapter:
    """校验返回完整性，把分群记录转成按发送时间全局排序的统一消息。"""

    def normalize(self, payload: dict, uid: str, start: date, end: date) -> list[Message]:
        """验证客户、日期、条数和消息身份；同 ID 内容冲突时禁止静默覆盖。"""
        validate_uid(uid)
        validate_dates(start, end)
        return self._normalize(payload, uid, start.isoformat(), end.isoformat(),
                               datetime.combine(start, datetime.min.time(), TZ),
                               datetime.combine(end + timedelta(days=1), datetime.min.time(), TZ),
                               require_uids=False)

    def normalize_window(self, payload: dict, start: datetime, end: datetime,
                         uid: str | None = None) -> list[Message]:
        """先完整校验跨客户响应，再按业务左闭右开窗口筛选；不猜测缺失归属。"""
        parameters = window_parameters(start, end, uid)
        request_start = datetime.strptime(parameters["start"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        request_end = datetime.strptime(parameters["end"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        # 分钟接口回显秒级范围，结束回显为请求结束分钟的下一分钟。
        response_end = request_end + timedelta(minutes=1)
        messages = self._normalize(payload, uid, request_start.strftime("%Y-%m-%d %H:%M:%S"),
                                   response_end.strftime("%Y-%m-%d %H:%M:%S"),
                                   request_start, response_end, require_uids=True)
        return [message for message in messages if start <= message.sent_at < end]

    def _normalize(self, payload: dict, uid: str | None, start: str, end: str,
                   lower: datetime, upper: datetime, *, require_uids: bool) -> list[Message]:
        """共用完整性、身份、消息转换及冲突检查，未知客户暂不进入统一消息。"""
        if not isinstance(payload, dict):
            raise AdapterError("响应顶层必须是对象")
        if type(payload.get("code")) is not int or payload["code"] != 0:
            raise AdapterError("响应业务状态不是成功")
        start_values = {start} if require_uids else {start, lower.strftime("%Y-%m-%d %H:%M:%S")}
        end_values = {end} if require_uids else {end, upper.strftime("%Y-%m-%d %H:%M:%S")}
        if payload.get("start") not in start_values or payload.get("end") not in end_values:
            raise AdapterError("响应时间范围与请求不一致")
        if ((not require_uids and payload.get("uid") != uid)
                or ("uid" in payload and payload["uid"] != uid)):
            raise AdapterError("响应客户或日期范围与请求不一致")
        groups = payload.get("groups")
        if not isinstance(groups, list):
            raise AdapterError("响应 groups 必须是列表")
        expected = _count(payload.get("total"))
        received = 0
        unique: dict[str, Message] = {}
        group_owners: dict[str, str] = {}
        for group in groups:
            if not isinstance(group, dict):
                raise AdapterError("群记录必须是对象")
            group_uid = group.get("uid") if require_uids else group.get("uid", uid)
            try:
                validate_uid(group_uid)
            except ValueError:
                raise AdapterError("群客户 UID 缺失或无效，归属待确认") from None
            if uid is not None and group_uid != uid:
                raise AdapterError("群客户 UID 与请求不一致")
            conversation_id = _text(group.get("group_number"), "group_number", required=True)
            if conversation_id in group_owners and group_owners[conversation_id] != group_uid:
                raise AdapterError("同一群出现不同客户 UID，归属待确认")
            group_owners[conversation_id] = group_uid
            conversation_name = _text(group.get("group_name", ""), "group_name")
            records = group.get("records")
            if not isinstance(records, list):
                raise AdapterError("响应 records 必须是列表")
            if _count(group.get("total")) != len(records):
                raise AdapterError("群 total 与实收条数不一致；分页或截断规则待确认")
            received += len(records)
            for record in records:
                if not isinstance(record, dict):
                    raise AdapterError("消息记录必须是对象")
                record_uid = record.get("uid") if require_uids else record.get("uid", group_uid)
                if record_uid != group_uid:
                    raise AdapterError("消息客户 UID 缺失或与群归属不一致，归属待确认")
                message_id = _text(record.get("id"), "id", required=True)
                if not re.fullmatch(r"[0-9]+", message_id):
                    raise AdapterError("消息 id 必须是数字字符串")
                if record.get("group_number") != conversation_id:
                    raise AdapterError("消息所属群与响应分组不一致")
                value = _text(record.get("send_time"), "send_time", required=True)
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
                        raise ValueError()
                    sent_at = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
                except ValueError:
                    raise AdapterError("消息 send_time 格式无效") from None
                if not lower <= sent_at < upper:
                    raise AdapterError("消息发送时间超出请求范围")
                content = _text(record.get("content"), "content")
                internal_hint = record.get("is_inner")
                if internal_hint is not None and (type(internal_hint) is not int or internal_hint not in (0, 1)):
                    raise AdapterError("is_inner 只能保留为 0、1 或未知提示值")
                display_name = _text(record.get("display_name", ""), "display_name")
                nick_name = _text(record.get("nick_name", ""), "nick_name")
                message = Message(
                    uid=group_uid,
                    message_id=message_id,
                    sent_at=sent_at,
                    content=content,
                    source="official_chat",
                    conversation_id=conversation_id,
                    conversation_name=conversation_name,
                    sender_id=_text(record.get("sender_number", ""), "sender_number"),
                    sender_name=display_name or nick_name,
                    internal_hint=internal_hint,
                    markers=tuple(marker for marker in ("[图片]", "[文件]") if marker in content),
                )
                old = unique.get(message_id)
                if old is not None and old != message:
                    raise AdapterError("同一消息 id 出现冲突记录，已停止摄入")
                unique[message_id] = message
        if received != expected:
            raise AdapterError("顶层 total 与实收条数不一致；分页或截断规则待确认")
        return sorted(unique.values(), key=message_order)


class OfficialChatSource:
    """在线刷新请求窗口以补齐迟到消息；离线读取窗口或兼容日期缓存。"""

    def __init__(self, client: ChatRecordsClient | None, data_dir: Path | str, offline: bool = False):
        self.client = client
        self.data_dir = Path(data_dir)
        self.offline = offline
        self.adapter = ChatRecordsAdapter()

    def cache_path(self, uid: str, day: date) -> Path:
        """原始响应只写入调用方指定的数据目录，拒绝路径穿越和外部软链接。"""
        validate_uid(uid)
        validate_dates(day, day)
        root = self.data_dir.resolve()
        target = root / "raw" / uid / f"{day.isoformat()}.json"
        if not target.resolve().is_relative_to(root):
            raise ValueError("原始缓存路径必须位于本地数据目录")
        return target

    def load_day(self, uid: str, day: date) -> list[Message]:
        """提供当天统一消息，当前处理周期的时间过滤由业务服务负责。"""
        path = self.cache_path(uid, day)
        return self._load(path, lambda: self.client.fetch(uid, day, day),
                          lambda payload: self.adapter.normalize(payload, uid, day, day))

    def window_cache_path(self, start: datetime, end: datetime, uid: str | None = None) -> Path:
        """以来源、分钟范围和客户筛选组成缓存身份，跨客户与单客户互不覆盖。"""
        parameters = window_parameters(start, end, uid)
        identity = json.dumps(parameters, sort_keys=True).encode("utf-8")
        token = hashlib.sha256(identity).hexdigest()
        root = self.data_dir.resolve()
        target = root / "raw" / "official_chat" / "windows" / f"{token}.json"
        if not target.resolve().is_relative_to(root):
            raise ValueError("原始缓存路径必须位于本地数据目录")
        return target

    def load_window(self, uid: str | None, start: datetime, end: datetime) -> list[Message]:
        """在线按分钟读取统一消息；离线优先用窗口缓存，兼容已有单客户日期缓存。"""
        path = self.window_cache_path(start, end, uid)
        if self.offline and not path.exists() and uid is not None:
            messages = {}
            day = start.astimezone(TZ).date()
            last_day = (end.astimezone(TZ) - timedelta(microseconds=1)).date()
            while day <= last_day:
                for message in self.load_day(uid, day):
                    if start <= message.sent_at < end:
                        previous = messages.get(message.key)
                        if previous is not None and previous != message:
                            raise AdapterError("日期缓存中的同一消息出现冲突记录，已停止摄入")
                        messages[message.key] = message
                day += timedelta(days=1)
            return sorted(messages.values(), key=message_order)
        return self._load(path, lambda: self.client.fetch_window(start, end, uid),
                          lambda payload: self.adapter.normalize_window(payload, start, end, uid))

    def _load(self, path: Path, fetch, normalize) -> list[Message]:
        """先校验再原子保存响应，失败或离线读取均不破坏上次有效缓存。"""
        if self.offline:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise AdapterError("该请求没有本地原始缓存，请先在线取数") from None
            except (ValueError, UnicodeError):
                raise AdapterError("本地原始缓存不是有效 JSON") from None
        else:
            if self.client is None:
                raise ValueError("在线取数需要官方客户端")
            payload = fetch()
        messages = normalize(payload)
        if not self.offline:
            # 先完成适配校验再原子替换缓存，失败响应不破坏之前的可用副本。
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(prefix=".chat-", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return messages
