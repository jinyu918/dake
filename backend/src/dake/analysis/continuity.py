"""客户确认恢复及前后两次发生的边界；不定义官方状态或自动关闭工单。"""

from copy import deepcopy
from typing import Literal

from pydantic import Field

from ..domain.models import message_order, parse_time
from .contract import ContractError, StrictObject
from .summary_changes import ChangeReference


CONTINUITY_POLICY = """
八、可信范围恢复与再次发生
仍只用 new/update/duplicate/review。每个候选增加 continuity，无恢复边界相关事实时为 null。
非空结构为 {kind,previous_event_id,evidence:[{message_id,quote}],scope?}；这些是归属依据，不是事件状态。
confirm_recovery：update 原事件。只有 confirmed_sender_role=customer 且 sender_kind 不是 bot 的
本轮消息中，客户本人明确确认这个问题已经恢复才有效。内部修复、内部复测、转述客户已恢复、
自动通知、收到、谢谢、预计恢复都不算。身份未确认时保留事实并 review，不能猜客户身份。
恢复证据放入本候选 changes，时间保存确认消息的 send_time，不猜实际恢复时刻，也不自动关闭工单。
verify_recovery：update 原事件。仅已确认账号为 internal 的支持方直接核验可用；scope 必须是引文中
可核对的测试对象与环境范围，不能把修复上线、预测、转述或未测范围当作恢复，也不能冒充客户验收。
recurrence：new，previous_event_id 指向本次已有候选中的前次事件；referenced_event_id=null。
必须有可信的相应范围恢复事实，以及本轮明确说明同一对象或问题在恢复后新一次发生的原文。
前次为支持方核验时，recurrence.scope 必须与已核验范围完全相同且出现在本轮引文中；未测范围不能外推。
只有关键词相同、隔了几天、又、还是不够；没有恢复记录也不能默认一直未解决或直接合并。
若新消息明确回顾“之前确已恢复，现在再次发生”，可分别输出 confirm_recovery 的 update 和
recurrence 的 new，两者引用同一消息中各自的原文，更新原事件仍须旧事件依据。
同批恢复与再次发生必须拆成候选，不得把两次发生的证据合成一个 update。
historical：update，本轮明确回顾恢复前的情况，只给原事件补证据，不修改其当前结论。
retract_recovery：update，客户明确纠正先前恢复结论，实际一直没好；保留旧版本，撤销恢复记录。
更正摘要中已有恢复片段时使用 correction；摘要未包含该结论时追加有依据的更正事实。
uncertain：review，恢复、指代或发生先后无法核对时使用；changes=[]，possible_event_ids 保存备选。
除 recurrence 外 previous_event_id=null。所有 continuity.evidence 只能引用本轮直接证据；
明确动作还须在 changes 的引文中体现。retract_recovery 同样必须由已确认身份的客户明确说明。
已恢复事件的新进展必须说明上述边界；纯复述仍可 duplicate。不同故障可独立 new，不能只因同产品
就标成复发。所有归属仍需新旧证据支持，不使用未来结论；历史证据不足时 review。
"""


class ContinuityFact(StrictObject):
    """一次边界判断，父事件仅用于复发的新建决定。"""

    kind: Literal["confirm_recovery", "verify_recovery", "retract_recovery", "recurrence", "historical", "uncertain"]
    previous_event_id: str | None = Field(min_length=1)
    evidence: list[ChangeReference] = Field(min_length=1)
    scope: str | None = Field(default=None, min_length=1)


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
        if fact.scope is not None and (not fact.scope.strip() or fact.scope not in ref.quote):
            raise ContractError("恢复范围必须来自本轮直接引文")
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
        support = recovery["identity_basis"] == "confirmed_support_account"
        return (bool(refs) and recovery["identity_basis"] in {"confirmed_account", "confirmed_support_account"}
                and (not support or bool(recovery.get("scope")))
                and parse_time(recovery["confirmed_at"]) == max(parse_time(ref["send_time"]) for ref in refs)
                and parse_time(recovery["confirmed_at"]) <= event.end_time
                and all(ref["sender_role"] == ("internal" if support else "customer")
                        and (not support or recovery["scope"] in ref["quote"])
                        and ref["sender_id"] and ref["quote"] and ref["digest"]
                        and parse_time(ref["send_time"]) < before
                        and (not require_evidence or
                             (ref["source"], ref["message_id"], ref["digest"], ref["send_time"]) in known) for ref in refs))
    except (KeyError, TypeError, ValueError):
        return False


def _proof_order(ref):
    """已保存依据与消息采用同一发送时间、数字 ID、来源排序规则。"""
    mid = ref["message_id"]
    return parse_time(ref["send_time"]), (0, int(mid)) if mid.isdigit() else (1, mid), ref["source"]


def resolve_continuity(candidate, record, event, *, available, customer_keys, closed_statuses, support_keys=()):
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
            return review("旧事件已有恢复记录，本轮与恢复的范围和先后关系不明确")
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
    if kind in {"confirm_recovery", "verify_recovery", "retract_recovery"}:
        support = kind == "verify_recovery"
        if support and not fact.get("scope"):
            return review("支持方恢复核验缺少可核对的对象与环境范围")
        if any(ref["message_id"] not in (support_keys if support else customer_keys) for ref in fact["evidence"]):
            if support:
                return review("支持方恢复核验须有已确认的支持账号身份")
            return review("只有已确认身份的客户明确确认或纠正，才能改变恢复记录")
        edited_recovery = kind == "retract_recovery" and event.recovery and any(
            (proof["source"], proof["message_id"]) == (ref["source"], ref["message_id"])
            and proof["digest"] != ref["digest"] for proof in event.recovery["evidence"] for ref in refs)
        if max(map(_proof_order, refs)) < max(map(_proof_order, event.evidence)) and not edited_recovery:
            return review("恢复或纠正依据早于已有较新反馈，无法确定当前恢复边界")
        if kind == "retract_recovery" and event.recovery is None:
            return review("没有可撤销的客户恢复记录，原结论需要核对")
        for ref in refs:
            ref["sender_role"] = "internal" if support else "customer"
        recovery = dict(confirmed_at=latest.isoformat(), evidence=refs,
                        identity_basis="confirmed_support_account" if support else "confirmed_account",
                        policy_version=record["policy_version"], review_status="confirmed")
        if fact.get("scope"):
            recovery["scope"] = fact["scope"]
        if kind in {"confirm_recovery", "verify_recovery"} and event.recovery:
            if (event.recovery["identity_basis"], event.recovery.get("scope")) != (recovery["identity_basis"], recovery.get("scope")):
                return review("恢复核验的身份或范围发生变化，须核对原边界")
            recovery = deepcopy(event.recovery)
            support = recovery.setdefault("supporting_evidence", [])
            support.extend(ref for ref in refs if ref not in recovery["evidence"] and ref not in support)
        if kind == "retract_recovery" and edited_recovery and any(
                proof.get("evidence_status") != "superseded" and not any((proof["source"], proof["message_id"]) == (ref["source"], ref["message_id"])
                        for ref in refs) for proof in event.recovery.get("supporting_evidence", [])):
            return review("原恢复另有独立确认，须核对本次更正范围")
        return dict(kind=kind, recovery=recovery, evidence=refs)
    if event.recovery is None:
        return review("缺少可信的相应范围恢复依据，不能确定前后两次发生")
    if kind == "recurrence":
        if event.recovery.get("scope") and fact.get("scope") != event.recovery["scope"]:
            return review("本次发生范围与已核验恢复范围不一致或未明确，不能确认复发")
        if event.recovery.get("review_status") == "pending":
            return review("前次恢复依据待核实，不能确认复发")
        if min(message_order(available[key]) for key in candidate.evidence_ids) < max(map(_proof_order, event.recovery["evidence"])):
            return review("再次发生的依据早于恢复确认，不能用后来的结论认定复发")
        return dict(kind=kind, recurrence_of=dict(event_id=event.event_id, reason=record["reason"],
                    recovery=deepcopy(event.recovery), evidence=refs, policy_version=record["policy_version"],
                    review_status="confirmed", review_reason=None))
    return dict(kind="historical", evidence=refs)
