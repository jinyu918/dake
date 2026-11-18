"""客户确认恢复及前后两次发生的边界；不定义官方状态或自动关闭工单。"""

from copy import deepcopy
from typing import Literal

from pydantic import Field

from ..domain.models import message_order, parse_time
from .contract import ContractError, StrictObject
from .summary_changes import ChangeReference


class ContinuityFact(StrictObject):
    """一次边界判断，父事件仅用于复发的新建决定。"""

    kind: Literal["confirm_recovery", "retract_recovery", "recurrence", "historical", "uncertain"]
    previous_event_id: str | None = Field(min_length=1)
    evidence: list[ChangeReference] = Field(min_length=1)


def normalize_uncertain_reference(candidate, existing_events):
    """明确待确认时把已知候选引用放回备选位置，不推断恢复或复发。"""
    fact = candidate.continuity
    if (candidate.decision != "review" or candidate.referenced_event_id is not None
            or not fact or fact.get("kind") != "uncertain"):
        return []
    previous = fact.get("previous_event_id")
    if not previous or not any(e.event_id == previous for e in existing_events):
        return []
    candidate.possible_event_ids = list(dict.fromkeys([*candidate.possible_event_ids, previous]))
    fact["previous_event_id"] = None
    return [dict(field="continuity.previous_event_id", reason="uncertain_reference_to_possible_events",
                 event_id=previous)]


def validate_continuity(candidate, available, eligible_keys):
    """先核对协议和本批引文；身份及恢复先后在按时间执行时再核对。"""
    if candidate.continuity is None:
        return
    try:
        fact = ContinuityFact.model_validate(candidate.continuity)
    except ValueError:
        raise ContractError("恢复或复发依据不符合合约") from None
    expected = "new" if fact.kind == "recurrence" else "review" if fact.kind == "uncertain" else "update"
    if candidate.decision != expected or bool(fact.previous_event_id) != (fact.kind == "recurrence"):
        raise ContractError("恢复边界与归属动作或前次事件引用不一致")
    change_refs = [ref for change in candidate.changes or [] for ref in change["evidence"]]
    for ref in fact.evidence:
        message = available.get(ref.message_id)
        if (ref.message_id not in candidate.evidence_ids or message is None or message.key != ref.message_id
                or not ref.quote.strip() or ref.quote not in message.content):
            raise ContractError("恢复或复发引文必须来自本批直接证据")
        if fact.kind != "uncertain" and not any(
                item["message_id"] == ref.message_id and ref.quote in item["quote"] for item in change_refs):
            raise ContractError("恢复或复发依据必须同时出现在本轮变化事实中")
    if fact.kind == "recurrence" and not any(ref.message_id in eligible_keys for ref in fact.evidence):
        raise ContractError("恢复或复发不能仅由机器人通知推动")


def recovery_is_safe(event, before, *, require_evidence=True):
    """仅信任已随事件快照保存且版本、时间、客户身份均有依据的恢复记录。"""
    recovery = event.recovery
    if recovery is None:
        return True
    try:
        refs = recovery["evidence"]
        known = {(e["source"], e["message_id"], e.get("digest"), e["send_time"]) for e in event.evidence}
        return (bool(refs) and recovery["identity_basis"] == "confirmed_account"
                and parse_time(recovery["confirmed_at"]) == max(parse_time(ref["send_time"]) for ref in refs)
                and parse_time(recovery["confirmed_at"]) <= event.end_time
                and all(ref["sender_role"] == "customer" and ref["sender_id"] and ref["quote"] and ref["digest"]
                        and parse_time(ref["send_time"]) < before
                        and (not require_evidence or
                             (ref["source"], ref["message_id"], ref["digest"], ref["send_time"]) in known) for ref in refs))
    except (KeyError, TypeError, ValueError):
        return False


def _proof_order(ref):
    """已保存依据与消息采用同一发送时间、数字 ID、来源排序规则。"""
    mid = ref["message_id"]
    return parse_time(ref["send_time"]), (0, int(mid)) if mid.isdigit() else (1, mid), ref["source"]


def resolve_continuity(candidate, record, event, *, available, customer_keys, closed_statuses):
    """读取执行到此处的旧事件；不让同批后来的恢复结论解释先前的新发生。"""
    if record["action"] in {"review", "duplicate"}:
        return None
    fact = candidate.continuity

    def review(reason):
        record.update(action="review", event_id=None, reason=reason,
                      possible_event_ids=[event.event_id] if event else record["possible_event_ids"])
        return None

    if fact is None:
        if record["action"] == "update" and candidate.changes != [] and event.recovery:
            # 发送时间早于恢复确认的原始反馈，可确定属于恢复前；晚发的回顾须模型明确引用。
            if max(available[key].sent_at for key in candidate.evidence_ids) < parse_time(event.recovery["confirmed_at"]):
                return dict(kind="historical")
            return review("旧事件已获客户恢复确认，本轮与恢复的先后关系不明确")
        if record["action"] == "update" and candidate.changes != [] and (
                event.closed_at is not None or event.status in closed_statuses):
            return review("旧结束记录缺少可核对的恢复边界，需确认本轮发生情况")
        return None
    refs = []
    for ref in fact["evidence"]:
        message = available[ref["message_id"]]
        value = dict(source=message.source, message_id=message.message_id, digest=message.digest,
                     conversation_id=message.conversation_id, send_time=message.sent_at.isoformat(),
                     sender_id=message.sender_id, quote=ref["quote"])
        if value not in refs:
            refs.append(value)
    kind = fact["kind"]
    latest = max(parse_time(ref["send_time"]) for ref in refs)
    if kind in {"confirm_recovery", "retract_recovery"}:
        if any(ref["message_id"] not in customer_keys for ref in fact["evidence"]):
            return review("只有已确认身份的客户明确确认或纠正，才能改变恢复记录")
        if max(map(_proof_order, refs)) < max(map(_proof_order, event.evidence)):
            return review("恢复或纠正依据早于已有较新反馈，无法确定当前恢复边界")
        if kind == "retract_recovery" and event.recovery is None:
            return review("没有可撤销的客户恢复记录，原结论需要核对")
        for ref in refs:
            ref["sender_role"] = "customer"
        recovery = dict(confirmed_at=latest.isoformat(), evidence=refs, identity_basis="confirmed_account",
                        policy_version=record["policy_version"])
        return dict(kind=kind, recovery=deepcopy(event.recovery or recovery), evidence=refs)
    if event.recovery is None:
        return review("缺少客户明确确认恢复的依据，不能确定前后两次发生")
    if kind == "recurrence":
        if min(message_order(available[key]) for key in candidate.evidence_ids) < max(map(_proof_order, event.recovery["evidence"])):
            return review("再次发生的依据早于恢复确认，不能用后来的结论认定复发")
        return dict(kind=kind, recurrence_of=dict(event_id=event.event_id, reason=record["reason"],
                    recovery=deepcopy(event.recovery), evidence=refs, policy_version=record["policy_version"]))
    return dict(kind="historical", evidence=refs)
