from copy import deepcopy
from datetime import datetime

from ..domain.models import TZ
from .field_changes import field_state, project_fields, review_message_versions
from .relations import review_proofs, review_recovery, review_relations


def review_summary(event, available):
    marker = "概述依据已修订，当前表述待核对"
    changes = [change for revision in event.updated_by for change in revision.get("changes", [])
               if change.get("text") and change["text"] in event.summary]
    stale = []
    valid = set()
    superseded = {(proof["source"], proof["message_id"], proof.get("digest")) for proof in event.evidence
                  if proof.get("evidence_status") == "superseded"}
    for change in changes:
        proofs = deepcopy(change["evidence"])
        for proof in proofs:
            if (proof["source"], proof["message_id"], proof.get("digest")) in superseded:
                proof["evidence_status"] = "superseded"
        if review_proofs(proofs, available):
            valid.add(change["text"])
        else:
            stale.append(change["text"])
    flags = [value for value in (event.review_flag or "").split("；") if value and value != marker]
    if any(text not in valid for text in stale):
        flags.append(marker)
    event.review_flag = "；".join(flags) or None


def review_revised_evidence(events, messages, *, store, window, result, settings):
    available = {message.key: message for message in messages}
    current = {event.event_id: event for event in events}
    writable = [event for event in events if not event.corrected_into]
    before = {key: deepcopy(event.to_dict()) for key, event in current.items()}
    for event in writable:
        review_proofs(event.evidence, available)
        identity_pending = False
        superseded = {(proof["source"], proof["message_id"], proof.get("digest")) for proof in event.evidence
                      if proof.get("evidence_status") == "superseded"}
        for operation in event.identity_corrections:
            proofs = deepcopy(operation["evidence"])
            for proof in proofs:
                if (proof["source"], proof["message_id"], proof.get("digest")) in superseded:
                    proof["evidence_status"] = "superseded"
            identity_pending |= not review_proofs(proofs, available)
        marker = "同轮身份纠正依据已修订，人工决定待复核"
        flags = [value for value in (event.review_flag or "").split("；") if value and value != marker]
        if identity_pending:
            flags.append(marker)
        event.review_flag = "；".join(flags) or None
        for label, facts in event.type_evidence.items():
            changed = any((message := available.get(f"{proof['source']}:{proof['message_id']}")) is not None
                and proof.get("digest") and message.digest != proof["digest"]
                for fact in facts for proof in fact.get("evidence_refs", []))
            if changed:
                field_state(event, dict(kind="label", name=label, type_label=None))
            for fact in facts:
                valid = review_proofs(fact.get("evidence_refs", []), available)
                if any(proof.get("evidence_status") == "superseded" for proof in fact.get("evidence_refs", [])):
                    fact["evidence_status"] = "partially_superseded" if valid else "superseded"
                elif fact.get("evidence_status") in {"superseded", "partially_superseded"}:
                    fact.pop("evidence_status")
        review_message_versions(event, available)
        project_fields(event, settings.closed_statuses)
        review_recovery(event, available)
        review_summary(event, available)
    for event in writable:
        review_relations(event, current, available)
    for event in writable:
        old = before[event.event_id]
        value = event.to_dict()
        changed = [key for key in value if key not in {"version", "updated_by"} and value[key] != old[key]]
        if not changed:
            continue
        event.version = max(event.version, len(event.updated_by)) + 1
        snapshot = event.to_dict()
        snapshot.pop("updated_by")
        event.updated_by.append(dict(action="updated", window_start=window.start.isoformat(),
            window_end=window.end.isoformat(), message_ids=[], snapshot=snapshot,
            revision_no=len(event.updated_by) + 1, recorded_at=datetime.now(TZ).isoformat(),
            change_kind="classification", change_summary="消息依据修订复核", summary_changed=False,
            changed_fields=changed, changes=[]))
        if result.attempt_id is not None:
            event.updated_by[-1].update(attempt_id=result.attempt_id, proposal_ids=[])
        store.save_event(event)
        if event.event_id not in result.created and event.event_id not in result.updated:
            result.updated.append(event.event_id)
    return events
