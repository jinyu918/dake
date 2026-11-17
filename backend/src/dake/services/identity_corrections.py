from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json

from ..analysis.identity_fields import import_fields
from ..analysis.relations import review_relations
from ..domain.identity import SameRoundCorrectionRequest
from ..domain.models import TZ, parse_time
from .field_decisions import FieldDecisionConflict


def version(event):
    return max(event.version, len(event.updated_by))


def proof_key(proof):
    return proof["source"], proof["message_id"], proof.get("digest")


def _append_unique(existing, incoming):
    for value in incoming:
        if value not in existing:
            existing.append(deepcopy(value))


def _revision(event, before, operation, now):
    event.version = version(event) + 1
    event.identity_corrections.append(deepcopy(operation))
    snapshot = event.to_dict()
    snapshot.pop("updated_by")
    anchor = event.updated_by[-1] if event.updated_by else {}
    event.updated_by.append(dict(action="updated", source="manual", recorded_at=now,
        revision_no=len(event.updated_by) + 1, window_start=anchor.get("window_start", event.created_on.isoformat()),
        window_end=anchor.get("window_end", event.end_time.isoformat()), message_ids=[], snapshot=snapshot,
        change_kind="manual", change_summary=operation["reason"], manual_operation=deepcopy(operation),
        summary_changed=before["summary"] != event.summary,
        changed_fields=[key for key in snapshot if snapshot[key] != before.get(key)], changes=[]))


def _validate_pair(duplicate, retained, events, request, store):
    if duplicate.event_id == retained.event_id:
        raise ValueError("不能将事项纠正到自身")
    if (duplicate.uid, duplicate.stream) != (retained.uid, retained.stream):
        raise ValueError("同轮纠正仅限同客户同分析范围")
    if duplicate.corrected_into or retained.corrected_into or any(
            event.corrected_into == duplicate.event_id for event in events):
        raise ValueError("已纠正别名不能再次并入或作为保留事项，不能形成链或循环")
    if (version(duplicate), version(retained)) != (request.expected_version, request.retained_expected_version):
        raise FieldDecisionConflict("两侧事项已更新，请刷新核对")
    relation = duplicate.recurrence_of
    if not relation or relation["event_id"] != retained.event_id or relation.get("review_status") == "retracted":
        raise ValueError("本入口只纠正已记录为该原事项复发的同轮误拆；独立事项不能并入")
    if retained.recovery is not None or duplicate.recovery is not None:
        raise ValueError("仍有恢复或独立轮次事实，须先按依据核对相应恢复判断")
    proofs = [proof.model_dump() for proof in request.evidence]
    end = max(parse_time(proof["send_time"]) for proof in proofs) + timedelta(microseconds=1)
    messages = {message.key: message for message in store.history_messages(request.uid, proofs, end)}
    corrections = [change for event in (retained, duplicate) for revision in event.updated_by
                   for change in revision.get("changes", []) if change.get("effect", change.get("kind")) == "correction"]
    for proof in proofs:
        message = messages.get(f"{proof['source']}:{proof['message_id']}")
        if (message is None or message.digest != proof["digest"] or message.sent_at.isoformat() != proof["send_time"]
                or proof["quote"] not in message.content or request.scope not in proof["quote"]):
            raise ValueError("同轮纠正依据版本、范围或直接引文无效，请重新核对")
        if not any(proof_key(proof) == proof_key(old) and proof["quote"] in change["text"]
                   for change in corrections for old in change["evidence"]):
            raise ValueError("须引用已记录的恢复更正事实，不能凭相同关键词或真实复发直接并入")
        if parse_time(proof["send_time"]) < duplicate.created_on:
            raise ValueError("更正依据早于误拆轮次，不能否定后来的独立发生")
    for event in (retained, duplicate):
        sources = store.history_messages(request.uid, event.evidence,
            max(event.end_time, end) + timedelta(microseconds=1))
        expected = {(proof["source"], proof["message_id"], proof.get("digest"), proof["send_time"])
                    for proof in event.evidence}
        if not any((message.source, message.message_id, message.digest, message.sent_at.isoformat()) in expected
                   and request.scope in message.content for message in sources):
            raise ValueError("同轮范围须能在两侧已记录对象的有效消息版本中核对")
    return proofs


class IdentityCorrectionService:
    def __init__(self, queries, clock=lambda: datetime.now(TZ)):
        self.queries, self.clock = queries, clock

    def submit(self, event_id, raw):
        request = SameRoundCorrectionRequest.model_validate(raw)
        uid, stream = self.queries.scope(request.uid, request.stream)
        signature = hashlib.sha256(json.dumps(dict(event_id=event_id, request=request.model_dump()),
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.queries.connection() as store:
            with store.transaction(uid):
                events = store.list_events(uid, stream)
                operations = [operation for event in events for operation in event.identity_corrections
                              if operation["operation_id"] == request.operation_id]
                if operations:
                    if any(operation["request_digest"] != signature for operation in operations):
                        raise FieldDecisionConflict("操作编号已用于不同请求")
                    return deepcopy(operations[0])
                by_id = {event.event_id: event for event in events}
                duplicate, retained = by_id.get(event_id), by_id.get(request.retained_event_id)
                if duplicate is None or retained is None:
                    return None
                proofs = _validate_pair(duplicate, retained, events, request, store)
                before = {event.event_id: event.to_dict() for event in (retained, duplicate)}
                retained, duplicate = deepcopy(retained), deepcopy(duplicate)
                now = self.clock().isoformat()
                import_fields(retained, duplicate, request, now, self.queries.settings)
                for proof in retained.evidence:
                    proof.setdefault("origin_event_id", retained.event_id)
                _append_unique(retained.evidence, [dict(proof, origin_event_id=duplicate.event_id) for proof in duplicate.evidence])
                for name in ("type_evidence", "removed_types"):
                    for label, values in getattr(duplicate, name).items():
                        tagged = [dict(value, origin_event_id=duplicate.event_id) for value in values]
                        _append_unique(getattr(retained, name).setdefault(label, []), tagged)
                retained.start_time = min(retained.start_time, duplicate.start_time)
                retained.end_time = max(retained.end_time, duplicate.end_time)
                for relation in duplicate.relations:
                    imported = deepcopy(relation)
                    imported["origin_event_id"] = duplicate.event_id
                    if imported["event_id"] == retained.event_id and imported["review_status"] != "retracted":
                        imported.update(review_status="pending", review_reason="身份纠正后关系指向自身，须核对")
                    _append_unique(retained.relations, [imported])
                duplicate.corrected_into = retained.event_id
                duplicate.recurrence_of.update(review_status="retracted", review_reason=request.reason,
                    retraction_evidence=proofs, retracted_by=request.operation_id)
                current = dict(by_id, **{retained.event_id: retained, duplicate.event_id: duplicate})
                review_relations(retained, current, {})
                review_relations(duplicate, current, {})
                operation = dict(operation_id=request.operation_id, request_digest=signature, outcome="committed",
                    duplicate_event_id=duplicate.event_id, retained_event_id=retained.event_id, uid=uid, stream=stream,
                    before_versions={identity: value["version"] for identity, value in before.items()},
                    after_versions={event.event_id: version(event) + 1 for event in (retained, duplicate)},
                    operator=request.operator, reason=request.reason, scope=request.scope, decided_at=now,
                    effective_after=max(revision["window_end"] for event in (retained, duplicate) for revision in event.updated_by),
                    evidence=proofs, resolutions=[value.model_dump() for value in request.resolutions])
                for event in (retained, duplicate):
                    _revision(event, before[event.event_id], operation, now)
                    store.save_event(event)
        return operation

    def operation(self, event_id, operation_id, uid, stream):
        uid, stream = self.queries.scope(uid, stream)
        with self.queries.connection() as store:
            with store.customer_lock(uid):
                event = next((value for value in store.list_events(uid, stream) if value.event_id == event_id), None)
                if event is None:
                    return None
                operation = next((value for value in event.identity_corrections
                    if value["operation_id"] == operation_id and value["duplicate_event_id"] == event_id), None)
                return deepcopy(operation) if operation else dict(outcome="not_committed", operation_id=operation_id)
