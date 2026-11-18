"""验证候选与证据来源，计算新增证据时间边界。"""

from datetime import datetime
import re

from ..domain.models import Candidate, EVENT_LABELS, PENDING_TYPE, TYPE_ATTRIBUTE_NAMES, Message
from ..domain.priority_policy import IMPACT_LEVELS, PRIORITY_EVIDENCE_FIELDS, URGENCY_LEVELS
from ..domain.status_policy import validate_status_shape


def evidence_map(messages: list[Message]) -> dict[str, Message]:
    """建立可回查证据索引；跨来源重号时只接受带来源的完整消息键，同键异文拒绝静默覆盖。"""
    available: dict[str, Message] = {}
    by_id: dict[str, list[Message]] = {}
    for message in messages:
        old = available.get(message.key)
        if old is not None and old != message:
            raise ValueError("证据输入存在同键冲突")
        available[message.key] = message
        by_id.setdefault(message.message_id, []).append(message)
    for key, items in by_id.items():
        if key not in available and len({item.key for item in items}) == 1:
            available[key] = items[-1]
    return available


def validate_candidates(candidates, new_messages, context) -> dict[str, Message]:
    """拒绝幻觉类型、空证据、跨出输入的消息引用和纯历史重报。"""
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
        if item.topic_key is not None and not isinstance(item.topic_key, str):
            raise ValueError("主题标识必须是字符串或空值")
        if not isinstance(item.summary, str) or not item.summary.strip():
            raise ValueError("抽取结果缺少内容概述")
        if not isinstance(item.evidence_ids, list) or not item.evidence_ids:
            raise ValueError("抽取结果必须提供证据消息 ID")
        if any(not isinstance(key, str) or key not in available for key in item.evidence_ids):
            raise ValueError("抽取结果引用了输入中不存在的证据")
        if not any(available[key].key in new_keys for key in item.evidence_ids):
            raise ValueError("候选事件必须至少由一条本次新增消息支持")
        for value in (item.status, item.severity):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("状态和等级不能使用空白字符串")
        if not isinstance(item.assignees, list) or any(not isinstance(a, str) or not a.strip() for a in item.assignees):
            raise ValueError("跟进人字段须为字符串列表")
        if not isinstance(item.attributes, dict) or not isinstance(item.extensions, dict):
            raise ValueError("专属属性和扩展字段须为对象")

        for label, facts in item.type_evidence.items():
            if label not in labels:
                raise ValueError("类型证据不能指向未采用的标签")
            for fact in facts:
                refs = fact.get("evidence_ids", []) if isinstance(fact, dict) else []
                quote = fact.get("quote") if isinstance(fact, dict) else None
                if (
                    not refs
                    or any(ref not in available for ref in refs)
                    or not isinstance(quote, str)
                    or not quote.strip()
                    or not any(quote in available[ref].content for ref in refs)
                ):
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
                if (
                    not refs
                    or any(ref not in available for ref in refs)
                    or not isinstance(quote, str)
                    or not isinstance(value, str)
                    or value not in quote
                    or not any(quote in available[ref].content for ref in refs)
                ):
                    raise ValueError("类型专属属性值必须有可核对的直接证据")

        for label, facts in item.removed_types.items():
            if label in labels:
                raise ValueError("同一类型不能同时采用和撤销")
            for fact in facts:
                refs = fact.get("evidence_ids", []) if isinstance(fact, dict) else []
                quote = fact.get("quote") if isinstance(fact, dict) else None
                if (
                    not refs
                    or any(ref not in available for ref in refs)
                    or not isinstance(quote, str)
                    or not quote.strip()
                    or not any(quote in available[ref].content for ref in refs)
                ):
                    raise ValueError("标签撤销证据必须能在输入消息中核对")

        if item.status_proposal is not None:
            sp = item.status_proposal
            validate_status_shape(sp.get("status"), sp.get("waiting_reason"), sp.get("resolution"))
            refs = sp.get("evidence_ids", [])
            quote = sp.get("quote")
            if (
                not refs
                or any(ref not in available for ref in refs)
                or not isinstance(quote, str)
                or not quote.strip()
                or not any(quote in available[ref].content for ref in refs)
            ):
                raise ValueError("状态提案引文必须能在输入消息中核对")

        if item.priority_proposal is not None:
            pp = item.priority_proposal
            if pp.get("impact") not in IMPACT_LEVELS or pp.get("urgency") not in URGENCY_LEVELS:
                raise ValueError("优先级提案信号不在允许档位中")
            refs = pp.get("evidence_ids", [])
            quote = pp.get("quote")
            if any(ref not in available for ref in refs) or not priority_quote_matches(quote, [available[ref] for ref in refs]):
                raise ValueError("优先级提案引文必须能在每条所引用消息中核对")
    return available


def priority_quote_matches(quote: str, messages: list[Message]) -> bool:
    return bool(isinstance(quote, str) and quote.strip() and messages
                and all(quote in message.content for message in messages))


def validate_priority_evidence(candidate, available, *, uid, human_keys, customer_keys):
    proposal = candidate.priority_proposal or {}
    candidate_keys = {available[key].key for key in candidate.evidence_ids}
    facts = [("priority_proposal", proposal or None), *((field, proposal.get(field)) for field in PRIORITY_EVIDENCE_FIELDS)]
    facts.extend(("affected_customer", fact) for fact in proposal.get("affected_customers", []))
    for field, fact in facts:
        if fact is None:
            continue
        is_response = field in PRIORITY_EVIDENCE_FIELDS[2:]
        allowed_keys = candidate_keys & human_keys
        if is_response:
            allowed_keys &= customer_keys
        quote = fact.get("quote")
        refs = fact.get("evidence_ids", [])
        if any(key not in available or available[key].key not in allowed_keys or available[key].uid != uid for key in refs
               ) or not priority_quote_matches(quote, [available[key] for key in refs]):
            raise ValueError("优先级维度证据必须是当前事项的同客户本轮原文，情绪和催办须由已确认客户本人发出")
        if field == "affected_customer":
            customer, target = fact.get("customer"), fact.get("uid")
            impact_quote = fact.get("impact_quote")
            if not isinstance(customer, str) or not customer.strip() or customer not in quote:
                raise ValueError("受影响客户必须在原文中明确出现")
            if target is not None and (not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", target)
                    or not re.search(rf"(?<![A-Za-z0-9_-])UID\s*[:：=]?\s*{re.escape(target)}(?![A-Za-z0-9_-])", quote, re.I)
                    or target == uid):
                raise ValueError("跨客户 UID 必须是原文中完整出现的其他客户标识")
            status = fact.get("status")
            if status not in {"confirmed", "pending"} or (status == "confirmed" and (
                    target is None or not isinstance(impact_quote, str) or not impact_quote.strip()
                    or impact_quote == customer or impact_quote not in quote)):
                raise ValueError("已确认影响需要可回查的客户 UID 和已发生影响原文")
        if is_response:
            text = "".join(char for char in quote if char.isalnum())
            if not text or text in {"尽快", "请尽快", "麻烦尽快", "请尽快处理", "麻烦尽快处理"}:
                raise ValueError("单句普通催促或表情不能用作情绪升级或强催办证据")


def candidate_times(candidate: Candidate, available: dict[str, Message],
                    new_keys: set[str] | None = None) -> tuple[datetime, datetime]:
    """统一用证据的 send_time 计算起止时间；匹配窗口优先对齐新增证据。"""
    messages = [available[key] for key in candidate.evidence_ids
                if new_keys is None or available[key].key in new_keys]
    if not messages:
        messages = [available[key] for key in candidate.evidence_ids]
    times = [m.sent_at for m in messages]
    return min(times), max(times)
