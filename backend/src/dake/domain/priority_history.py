from collections.abc import Callable
from copy import deepcopy

from .models import Message, message_order, parse_time
from .priority_policy import ITEM_PRIORITY_RULE_VERSION, customer_level_number


def _basis(detail, effective_uid):
    detail = detail or {}
    fields = ("impact", "urgency", "objective_priority", "base_priority", "priority")
    if (detail.get("rule_version") != ITEM_PRIORITY_RULE_VERSION or any(key not in detail for key in fields)
            or not (detail.get("evidence_refs") or (detail.get("impact_evidence") or {}).get("evidence_refs"))):
        return None
    basis = {key: deepcopy(detail[key]) for key in (*fields, "reason", "evidence_refs", "impact_evidence",
                                                    "urgency_evidence", "level_note", "level_state") if key in detail}
    basis["effective_customer_uid"] = detail.get("effective_customer_uid") or effective_uid
    if customer_level_number(detail.get("customer_level"), detail.get("level_state")) >= 0:
        basis["customer_level"] = detail["customer_level"]
    return basis


def initial_priority_basis(event, *, current=False):
    detail = event.priority_detail or {}
    if "initial_basis" in detail:
        return detail["initial_basis"]
    if current:
        return _basis(detail, event.uid)
    for revision in event.updated_by:
        if revision.get("source") in {"directory", "clock", "manual"}:
            continue
        snapshot = revision.get("snapshot") or {}
        if basis := _basis(snapshot.get("priority_detail"), event.uid):
            return basis
    return None


def _message_ref(message, quote):
    return dict(source=message.source, message_id=message.message_id, digest=message.digest,
                conversation_id=message.conversation_id, send_time=message.sent_at.isoformat(), quote=quote)


def _ref_order(ref):
    mid = ref["message_id"]
    return parse_time(ref["send_time"]), (0, int(mid)) if mid.isdigit() else (1, mid), ref["source"]


def update_reply_round(current, candidate, available: dict[str, Message], new_keys,
                       role_for: Callable[[Message], str], uid: str, *, terminal: bool):
    round_state = dict(current or {})
    facts = {}
    candidate_keys = set(candidate.evidence_ids)
    for fact in candidate.reply_evidence:
        key, kind, quote = fact["message_id"], fact["kind"], fact["quote"]
        message = available.get(key)
        required_role = "customer" if kind == "question" else "internal" if kind == "answer" else None
        if (key in candidate_keys and message and message.uid == uid and (new_keys is None or message.key in new_keys)
                and required_role and role_for(message) == required_role
                and quote.strip() and quote in message.content):
            facts[message.key] = (message, kind, quote)

    proposal = candidate.priority_proposal or {}
    latest_question = None
    latest_question_context = {}
    prior_questions = {}
    if round_state and not round_state.get("closed_at"):
        prior_questions = {f"{ref['source']}:{ref['message_id']}": ref
                           for ref in (round_state["first"], round_state["latest"])}
    for message, kind, quote in sorted(facts.values(), key=lambda fact: message_order(fact[0])):
        watermark = round_state.get("last_action")
        if (watermark and message_order(message) <= _ref_order(watermark)) or (
            round_state.get("closed_at") and message.sent_at < parse_time(round_state["closed_at"])
        ):
            continue
        ref = _message_ref(message, quote)
        if kind == "question":
            if not round_state or round_state.get("closed_at"):
                round_state = dict(started_at=ref["send_time"], first=ref)
                prior_questions = {}
            round_state.update(latest=ref, last_action=ref)
            latest_question = message
            latest_question_context = dict(prior_questions)
            prior_questions[message.key] = ref
        elif round_state and not round_state.get("closed_at"):
            round_state.update(closed_at=ref["send_time"], last_action=ref)

    if terminal and round_state and not round_state.get("closed_at"):
        round_state["closed_at"] = round_state["latest"]["send_time"]
    trigger = None
    if latest_question and not terminal:
        for kind in ("escalated_emotion", "strong_followup"):
            fact = proposal.get(kind) or {}
            quote = fact.get("quote")
            text = "".join(char for char in (quote or "") if char.isalnum())
            if (latest_question.key not in fact.get("evidence_ids", []) or not text
                    or text in {"尽快", "请尽快", "麻烦尽快", "请尽快处理", "麻烦尽快处理"}
                    or quote not in latest_question.content):
                continue
            previous = fact.get("previous_message_id")
            earlier = latest_question_context.get(previous) if previous else None
            if previous and (not earlier or _ref_order(earlier) >= message_order(latest_question)):
                continue
            ref = _message_ref(latest_question, quote)
            refs = [ref] if earlier is None else [ref, earlier]
            trigger = dict(kind=kind, occurred_at=ref["send_time"], quote=quote, evidence_refs=refs)
            break
    return round_state or None, trigger
