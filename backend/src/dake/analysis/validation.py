"""验证候选与证据来源，计算新增证据时间边界。"""

from datetime import datetime
from ..domain.models import Candidate, EVENT_LABELS, PENDING_TYPE, TYPE_ATTRIBUTE_NAMES, Message


def evidence_map(messages: list[Message]) -> dict[str, Message]:
    """建立可回查证据索引；跨来源重号时只接受带来源的完整消息键。"""
    result = {m.key: m for m in messages}
    by_id: dict[str, list[Message]] = {}
    for message in messages:
        by_id.setdefault(message.message_id, []).append(message)
    for key, items in by_id.items():
        if len({item.key for item in items}) == 1:
            result[key] = items[-1]
    return result


def validate_candidates(candidates, new_messages, context) -> dict[str, Message]:
    """拒绝非官方主类型、无来源证据和纯历史重复候选，失败时整批回滚。"""
    available = evidence_map(context + new_messages)
    new_keys = {m.key for m in new_messages}
    for item in candidates:
        if not isinstance(item, Candidate) or item.type not in (*EVENT_LABELS, PENDING_TYPE):
            raise ValueError("抽取结果的主类型不在允许的六类、其他或待分类中")
        labels = item.label_types()
        if item.classification_status == "待分类":
            if item.type != PENDING_TYPE or labels:
                raise ValueError("待分类候选不能带已采用类型标签")
        elif item.type == PENDING_TYPE:
            raise ValueError("待分类候选必须显式标记分类状态")
        elif item.types is not None and item.type not in labels:
            raise ValueError("主类型必须包含在当前标签集合中")
        if item.type == "其他" or "其他" in labels:
            if not item.other_reason:
                raise ValueError("其他标签必须有排除六类的理由")
        if not isinstance(item.summary, str) or not item.summary.strip():
            raise ValueError("抽取结果缺少内容概述")
        if not isinstance(item.evidence_ids, list) or not item.evidence_ids:
            raise ValueError("抽取结果必须提供证据消息 ID")
        if any(not isinstance(key, str) or key not in available for key in item.evidence_ids):
            raise ValueError("抽取结果引用了输入中不存在的证据")
        if not any(available[key].key in new_keys for key in item.evidence_ids):
            raise ValueError("候选事件必须至少由一条本次新增消息支持")
        if item.topic_key is not None and not isinstance(item.topic_key, str):
            raise ValueError("主题标识必须是字符串或空值")
        if any(value is not None and not isinstance(value, str) for value in (item.status, item.severity)):
            raise ValueError("状态和等级必须是字符串或空值")
        if not isinstance(item.assignees, list) or any(not isinstance(v, str) for v in item.assignees):
            raise ValueError("跟进人字段须为字符串列表")
        if not isinstance(item.attributes, dict) or not isinstance(item.extensions, dict):
            raise ValueError("专属属性和扩展字段须为对象")
        for label, facts in item.type_evidence.items():
            if label not in labels:
                raise ValueError("类型证据不能指向未采用的标签")
            for fact in facts:
                refs = fact.get("evidence_ids", []) if isinstance(fact, dict) else []
                quote = fact.get("quote") if isinstance(fact, dict) else None
                if (not refs or any(ref not in available for ref in refs)
                        or not isinstance(quote, str)
                        or not any(quote in available[ref].content for ref in refs)):
                    raise ValueError("类型标签证据必须能在输入消息中核对")
        for label, fact_map in item.typed_attributes.items():
            if label not in labels or not isinstance(fact_map, dict):
                raise ValueError("类型专属属性必须对应已采用标签")
            for name, fact in fact_map.items():
                if not isinstance(name, str) or not name or not isinstance(fact, dict):
                    raise ValueError("类型专属属性结构无效")
                if name not in TYPE_ATTRIBUTE_NAMES.get(label, ()):
                    raise ValueError("类型专属属性名称不属于该类型字典")
                refs = fact.get("evidence_ids", [])
                quote = fact.get("quote")
                value = fact.get("value")
                if (not refs or any(ref not in available for ref in refs) or not isinstance(quote, str)
                        or not isinstance(value, str) or value not in quote
                        or not any(quote in available[ref].content for ref in refs)):
                    raise ValueError("类型专属属性值必须有可核对的直接证据")
        for label, facts in item.removed_types.items():
            if label in labels:
                raise ValueError("同一类型不能同时采用和撤销")
            for fact in facts:
                refs = fact.get("evidence_ids", []) if isinstance(fact, dict) else []
                quote = fact.get("quote") if isinstance(fact, dict) else None
                if (not refs or any(ref not in available for ref in refs)
                        or not isinstance(quote, str)
                        or not any(quote in available[ref].content for ref in refs)):
                    raise ValueError("标签撤销证据必须能在输入消息中核对")
    return available


def candidate_times(candidate: Candidate, available: dict, new_keys=None) -> tuple[datetime, datetime]:
    """匹配时以新增证据计时，防止旧上下文拉长区间而绕过认回窗口。"""
    times = [available[key].sent_at for key in candidate.evidence_ids
             if new_keys is None or available[key].key in new_keys]
    return min(times), max(times)
