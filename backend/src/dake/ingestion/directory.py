"""官方客户资料与群搜索客户端，仅返回校验后的资料，不参与客户准入。"""

from urllib.parse import urlencode

from .client import ChatRecordsClient, ChatRecordsError, validate_uid
from ..domain.customer_policy import KNOWN_LEVELS

BASE = "https://ts.qiniu.io/api/mars_alert/qq/"


def text_field(value, maximum=256):
    """将明确空值保留为未知，拒绝结构异常和超长值。"""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ChatRecordsError("客户资料字段格式无效")
    return value.strip() or None


class DirectoryClient(ChatRecordsClient):
    """地址固定为已提供的三个官方资料接口，沿用有界重试。"""

    def _data(self, endpoint, uid):
        validate_uid(uid)
        payload = self._request(BASE + endpoint + "?" + urlencode({"uid": uid}), "GET")
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("uid") != uid:
            raise ChatRecordsError("客户资料归属与请求不一致")
        return data

    def level(self, uid):
        """查询月销等级，rank 只保留原值，不据此决定准入。"""
        data = self._data("level", uid)
        if "level" not in data or "rank" not in data:
            raise ChatRecordsError("客户等级响应字段缺失")
        level = text_field(data["level"], 8)
        if level is not None and level not in KNOWN_LEVELS:
            raise ChatRecordsError("客户等级不在已公布范围内")
        return {"level": level, "rank": text_field(data["rank"], 64)}

    def tam(self, uid):
        """保留负责人和备用负责人字段，缺失值不编造。"""
        data = self._data("tam", uid)
        if "tam" not in data or "tam_b" not in data:
            raise ChatRecordsError("客户负责人响应字段缺失")
        return {"tam": text_field(data["tam"]), "tam_b": text_field(data["tam_b"])}

    def search(self, keyword):
        """查询相关群，完整校验条数及身份；仅名称相似不能证明归属。"""
        if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 128:
            raise ValueError("群搜索词须为 1 至 128 个字符")
        payload = self._request(BASE + "searchGroups", "POST", {"keyword": keyword})
        groups = payload.get("groups")
        if (not isinstance(groups, list) or type(payload.get("total")) is not int
                or payload["total"] != len(groups) or payload.get("keyword") != keyword):
            raise ChatRecordsError("群搜索响应不完整或与请求不一致")
        result = []
        for group in groups:
            if not isinstance(group, dict):
                raise ChatRecordsError("群搜索记录无效")
            gid = text_field(group.get("group_number"), 128)
            if not gid:
                raise ChatRecordsError("群搜索记录缺少群标识")
            uid = text_field(group.get("uid"), 128)
            if uid:
                try:
                    validate_uid(uid)
                except ValueError:
                    raise ChatRecordsError("群搜索客户标识无效") from None
            result.append({"group_id": gid, "uid": uid})
        return result
