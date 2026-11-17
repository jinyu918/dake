from copy import deepcopy
from typing import Literal

from pydantic import Field

from ..domain.models import parse_time
from ..domain.event_identity import canonical_id
from .contract import ContractError, StrictObject
from .summary_changes import ChangeReference


class RelationUpdate(StrictObject):
    kind: Literal["based_on", "caused_by"]
    event_id: str = Field(min_length=1)
    intent: Literal["attach", "retract"]
    reason: str = Field(min_length=1)
    evidence: list[ChangeReference] = Field(min_length=1)


def validate_relations(candidate, available, existing_events, eligible_keys, history_items=()):
    known = {event.event_id: event for event in existing_events}
    targets = set()
    for relation in candidate.relations:
        signature = relation["kind"], relation["event_id"]
        if signature in targets:
            raise ContractError("同一批事项关系不得重复")
        targets.add(signature)
        if (candidate.decision not in {"new", "update"} or relation["event_id"] not in known
                or relation["event_id"] == candidate.referenced_event_id or not relation["reason"].strip()):
            raise ContractError("事项关系须指向本次提供的独立已有事项")
        parent = known[relation["event_id"]]
        pending, visited = ([parent.event_id] if relation["intent"] == "attach" else []), set()
        while pending:
            identity = pending.pop()
            if identity == candidate.referenced_event_id:
                raise ContractError("事项关系不能循环引用")
            if identity in visited or identity not in known:
                continue
            visited.add(identity)
            pending.extend(item["event_id"] for item in known[identity].relations
                           if item["review_status"] != "retracted")
        for proof in relation["evidence"]:
            message = available.get(proof["message_id"])
            if (message is None or message.key not in eligible_keys or message.key not in candidate.evidence_ids
                    or not proof["quote"].strip() or proof["quote"] not in message.content
                    or parent.start_time > message.sent_at):
                raise ContractError("事项关系须有本轮直接引文，不得指向未来事项")
        linked = False
        for proof in candidate.basis:
            message = available.get(proof["message_id"])
            for old in parent.evidence:
                if proof["message_id"] != f"{old['source']}:{old['message_id']}":
                    continue
                linked |= bool(message and message.digest == old.get("digest") and proof["quote"] in message.content)
                linked |= any(item["id"] == proof["message_id"]
                    and (old.get("digest") is None or item.get("digest") == old["digest"])
                    and any(link["event_id"] == parent.event_id and link["kind"] == "event_evidence" for link in item["links"])
                    and any(proof["quote"] in fragment["text"] for fragment in item["fragments"])
                    for item in history_items)
        if not linked:
            raise ContractError("事项关系须同时提供被关联事项的旧依据")


def apply_relations(event, candidate, available):
    for update in candidate.relations:
        previous = next((item for item in event.relations
                         if (item["kind"], item["event_id"]) == (update["kind"], update["event_id"])), None)
        evidence = []
        for proof in update["evidence"]:
            message = available[proof["message_id"]]
            evidence.append(dict(source=message.source, message_id=message.message_id, digest=message.digest,
                send_time=message.sent_at.isoformat(), conversation_id=message.conversation_id, quote=proof["quote"]))
        if update["intent"] == "retract":
            if previous is None:
                raise ContractError("不能撤销不存在的事项关系")
            previous.update(review_status="retracted", review_reason=update["reason"], retraction_evidence=evidence)
        elif previous is None:
            event.relations.append(dict(kind=update["kind"], event_id=update["event_id"], reason=update["reason"],
                evidence=evidence, review_status="confirmed", review_reason=None, retraction_evidence=[]))
        else:
            previous["evidence"].extend(proof for proof in evidence if proof not in previous["evidence"])
            previous.update(review_status="confirmed", review_reason=None)


def review_proofs(proofs, available):
    for proof in proofs:
        message = available.get(f"{proof['source']}:{proof['message_id']}")
        if message is not None and proof.get("digest"):
            if message.digest != proof["digest"]:
                proof["evidence_status"] = "superseded"
            elif proof.get("evidence_status") == "superseded":
                proof.pop("evidence_status")
    return any(proof.get("evidence_status") != "superseded" for proof in proofs)


def review_recovery(event, available):
    if event.recovery:
        recovery = event.recovery
        valid = review_proofs(recovery["evidence"], available)
        support = recovery.get("supporting_evidence", [])
        review_proofs(support, available)
        if not valid:
            remaining = [proof for proof in support if proof.get("evidence_status") != "superseded"]
            if remaining:
                recovery["evidence"] = deepcopy(remaining)
                recovery["confirmed_at"] = max(proof["send_time"] for proof in remaining)
                valid = True
        recovery["review_status"] = "confirmed" if valid else "pending"


def review_relations(event, events, available):
    if event.recurrence_of and event.recurrence_of.get("review_status") != "retracted":
        relation = event.recurrence_of
        resolved = canonical_id(relation["event_id"], events)
        parent = events.get(resolved)
        recovery = parent.recovery if parent else None
        valid = review_proofs(relation["evidence"], available)
        valid = valid and recovery is not None and recovery.get("review_status") != "pending"
        valid = valid and resolved != canonical_id(event.event_id, events) and parse_time(recovery["confirmed_at"]) <= event.start_time
        relation["review_status"] = "confirmed" if valid else "pending"
        relation["review_reason"] = None if valid else "前次恢复或本次发生的直接依据失效，发生轮次待核对"
        if valid:
            relation["recovery"] = deepcopy(recovery)
    for relation in event.relations:
        valid = review_proofs(relation["evidence"], available)
        review_proofs(relation.get("retraction_evidence", []), available)
        if relation["review_status"] != "retracted":
            resolved = canonical_id(relation["event_id"], events)
            valid = valid and resolved is not None and resolved != canonical_id(event.event_id, events)
            relation["review_status"] = "confirmed" if valid else "pending"
            relation["review_reason"] = None if valid else "关系的直接消息依据已修订"
    uncertain = ((event.recovery or {}).get("review_status") == "pending"
                 or (event.recurrence_of or {}).get("review_status") == "pending"
                 or any(item["review_status"] == "pending" for item in event.relations))
    marker = "恢复或事项关系依据待核实"
    flags = [value for value in (event.review_flag or "").split("；") if value and value != marker]
    if uncertain:
        flags.append(marker)
    event.review_flag = "；".join(flags) or None
