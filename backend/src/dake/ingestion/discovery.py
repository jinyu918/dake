"""客户发现来源适配：校验跨客户响应并提取群归属，不访问资料或写业务库。"""

from datetime import datetime, timedelta

from ..domain.models import TZ
from .client import validate_uid, window_parameters
from .directory import text_field


def _uid(value):
    """缺失归属返回空，非空非法标识停止摄入。"""
    if value in (None, ""):
        return None
    validate_uid(value)
    return value


def discover_groups(payload, window):
    """只读取归属所需字段，校验响应完整性；结束分钟不提前发现到本窗口。"""
    params = window_parameters(window.start, window.end)
    start = datetime.strptime(params["start"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    end = datetime.strptime(params["end"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ) + timedelta(minutes=1)
    if (not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0
            or payload.get("start") != start.strftime("%Y-%m-%d %H:%M:%S")
            or payload.get("end") != end.strftime("%Y-%m-%d %H:%M:%S")
            or payload.get("uid") is not None):
        raise ValueError("跨客户响应范围不一致")
    groups = payload.get("groups")
    if not isinstance(groups, list) or type(payload.get("total")) is not int or payload["total"] < 0:
        raise ValueError("跨客户响应不完整")
    received, messages, found = 0, {}, {}
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("群记录无效")
        gid = text_field(group.get("group_number"), 128)
        name = text_field(group.get("group_name"), 512) or ""
        rows = group.get("records")
        if not gid or not isinstance(rows, list) or type(group.get("total")) is not int or group["total"] != len(rows):
            raise ValueError("群记录不完整")
        received += len(rows)
        owner = _uid(group.get("uid"))
        for row in rows:
            if not isinstance(row, dict) or row.get("group_number") != gid:
                raise ValueError("消息群归属不一致")
            mid = row.get("id")
            if not isinstance(mid, str) or not mid.isascii() or not mid.isdigit():
                raise ValueError("消息标识无效")
            sent = row.get("send_time")
            if not isinstance(sent, str):
                raise ValueError("消息时间无效")
            sent = datetime.strptime(sent, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
            if not start <= sent < end:
                raise ValueError("消息超出接口范围")
            record_uid = _uid(row.get("uid"))
            identity = (gid, owner, record_uid, sent)
            if mid in messages and messages[mid] != identity:
                raise ValueError("消息身份冲突")
            messages[mid] = identity
            if not window.start <= sent < window.end:
                continue
            current = found.setdefault(gid, {"source": "official_chat", "group_id": gid, "group_name": name,
                                            "owners": set(), "missing": False})
            current["owners"].update(value for value in (owner, record_uid) if value)
            current["missing"] |= not owner or not record_uid
    if received != payload["total"]:
        raise ValueError("响应条数不一致")
    result = []
    for group in found.values():
        candidates = sorted(group.pop("owners"))
        missing = group.pop("missing")
        confirmed = len(candidates) == 1 and not missing
        result.append({**group, "uid": candidates[0] if confirmed else None,
                       "status": "confirmed" if confirmed else "review",
                       "reason": "接口群与消息归属一致" if confirmed else "群与消息归属冲突" if len(candidates) > 1 else "客户 UID 缺失，待补查",
                       "candidate_uids": candidates})
    count = sum(window.start <= row[3] < window.end for row in messages.values())
    return result, count
