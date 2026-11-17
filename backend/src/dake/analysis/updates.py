"""更新稳定事件身份、追加证据与修订，并恢复历史窗口快照。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from uuid import uuid4
from ..domain.models import Event, TZ, Window, parse_time
from .validation import candidate_times
from .summary_changes import explicitly_corrects_typed_attribute, prepare_summary, restrict_fields
from .field_changes import (apply_label_changes, prepare_automatic_fields, protect_pending_summary,
                            project_fields, synchronize_summary)
from .relations import apply_relations, review_proofs


def has_classification_change(candidate, event, available=None):
    """判断有直接字段依据的标签或专属属性是否需要单独形成修订。"""
    if event is None:
        return False
    if available is not None:
        candidate = bind_candidate_evidence(candidate, available)
    current_labels = event.types or ([] if event.type == "待分类" else [event.type])
    added = set(candidate.label_types()) - set(current_labels)
    type_evidence = any(_new_evidence(facts, event.type_evidence.get(label, []), event)
                        for label, facts in candidate.type_evidence.items())
    attributes = any(
        event.typed_attributes.get(label, {}).get(name, {}).get("value") != fact.get("value")
        or _new_evidence([fact], [event.typed_attributes.get(label, {}).get(name, {})], event)
        for label, facts in candidate.typed_attributes.items() for name, fact in facts.items())
    return bool(added or candidate.removed_types or type_evidence or attributes
                or candidate.classification_conflicts or candidate.field_updates or candidate.relations)


def _bound_fact(fact, available):
    value = deepcopy(fact)
    refs = list(value.get("evidence_refs", []))
    seen = {(ref.get("source"), ref.get("message_id"), ref.get("digest"), ref.get("quote")) for ref in refs}
    for key in value.get("evidence_ids", []):
        message = available.get(key)
        if message is None:
            continue
        reference = dict(source=message.source, message_id=message.message_id, id=message.message_id,
                         digest=message.digest, conversation_id=message.conversation_id,
                         send_time=message.sent_at.isoformat(), quote=value.get("quote"))
        signature = (reference["source"], reference["message_id"], reference["digest"], reference["quote"])
        if signature not in seen:
            refs.append(reference)
            seen.add(signature)
    if refs:
        value["evidence_refs"] = refs
    return value


def bind_candidate_evidence(candidate, available):
    return replace(candidate,
        type_evidence={label: [_bound_fact(fact, available) for fact in facts]
                       for label, facts in candidate.type_evidence.items()},
        removed_types={label: [_bound_fact(fact, available) for fact in facts]
                       for label, facts in candidate.removed_types.items()},
        typed_attributes={label: {name: _bound_fact(fact, available) for name, fact in facts.items()}
                          for label, facts in candidate.typed_attributes.items()},
        other_reason=_bound_fact(candidate.other_reason, available) if candidate.other_reason else None)


def _fact_proofs(facts, event):
    versions = {}
    for ref in event.evidence:
        versions.setdefault((ref["source"], ref["message_id"]), []).append(ref)
    proofs = set()
    for fact in facts:
        quote = fact.get("quote")
        refs = fact.get("evidence_refs", [])
        if refs:
            proofs.update((ref.get("source"), ref.get("message_id", ref.get("id")),
                           ref.get("digest"), ref.get("send_time"), ref.get("quote", quote)) for ref in refs)
            continue
        for key in fact.get("evidence_ids", []):
            source, separator, message_id = key.partition(":")
            refs = versions.get((source, message_id), []) if separator else []
            if refs:
                proofs.update((source, message_id, ref.get("digest"), ref.get("send_time"), quote) for ref in refs)
            else:
                proofs.add((key, None, None, None, quote))
    return proofs


def _new_evidence(facts, old_facts, event):
    return bool(_fact_proofs(facts, event) - _fact_proofs(old_facts, event))


def _merge_records(existing, incoming):
    result = deepcopy(existing)
    seen = {repr(sorted(item.items())) for item in result}
    for item in incoming:
        signature = repr(sorted(item.items()))
        if signature not in seen:
            result.append(deepcopy(item))
            seen.add(signature)
    return result


def apply_candidate(candidate, event, available, uid, stream, window: Window, settings, new_keys=None, *, continuity=None,
                    field_corrections: dict[int, set[str]] | None = None):
    """创建或更新稳定 ID，追加可核对证据；完全无变化时返回空值避免空修订。"""
    candidate = bind_candidate_evidence(candidate, available)
    start, end = candidate_times(candidate, available)
    _, new_end = candidate_times(candidate, available, new_keys)
    created = event is None
    previous = event.to_dict() if event is not None else None
    working = deepcopy(event) if event is not None else None
    if working is not None:
        apply_label_changes(working, candidate, available)
        prepare_automatic_fields(working, candidate, available, settings)
        if candidate.changes is not None:
            candidate = replace(candidate, changes=protect_pending_summary(working, candidate))
    changes = None
    summary = candidate.summary
    classification_change = has_classification_change(candidate, event)
    classification_change = classification_change or (working is not None and (
        working.field_states != event.field_states or working.field_issues != event.field_issues))
    if candidate.changes is not None:
        summary, changes = prepare_summary(candidate, event, available,
            historical=(continuity or {}).get("kind") == "historical",
            keep_facts=bool(continuity and (continuity["kind"] == "retract_recovery" or
                (continuity["kind"] in {"confirm_recovery", "verify_recovery"} and event.recovery is None))),
            field_corrections=field_corrections)
        if not changes and not created and not classification_change:
            return None
        if changes:
            used = {f"{ref['source']}:{ref['message_id']}" for change in changes for ref in change["evidence"]}
            used.update(ref["message_id"] for update in candidate.field_updates or [] for ref in update["evidence"])
            candidate = replace(candidate, evidence_ids=[key for key in candidate.evidence_ids if key in used])
            candidate = restrict_fields(candidate, event, changes, available)
        start, end = candidate_times(candidate, available)
        _, new_end = candidate_times(candidate, available, new_keys)
    if created:
        event = Event(
            event_id=str(uuid4()), uid=uid, stream=stream, type=candidate.type,
            topic_key=candidate.topic_key, start_time=start, end_time=end,
            status=candidate.status or settings.default_status,
            severity=candidate.severity or settings.default_severity,
            summary=summary, created_on=window.start,
            types=candidate.label_types(), type_evidence=deepcopy(candidate.type_evidence),
            classification_evidence_status="已记录" if candidate.type_evidence else "未记录",
            classification_status=candidate.classification_status,
            other_reason=deepcopy(candidate.other_reason),
            removed_types=deepcopy(candidate.removed_types),
            typed_attributes=deepcopy(candidate.typed_attributes),
        )
    else:
        event = working
    if created and candidate.field_updates is not None:
        prepare_automatic_fields(event, candidate, available, settings, created=True)
    # 迟到的旧消息只补证据和时间，不允许把较新的概述或状态倒退。
    current_progress = changes is None or any(c["effect"] in {"progress", "correction"} for c in changes)
    current_progress = current_progress or classification_change
    if created or (new_end >= event.end_time and current_progress):
        event.summary = summary
        event.status = candidate.status or event.status
        event.severity = candidate.severity or event.severity
        event.assignees = candidate.assignees or event.assignees
        event.extensions.update(candidate.extensions)
        if candidate.types is not None:
            event.types = list(dict.fromkeys([*event.types, *candidate.label_types()]))
        removed_labels = set(candidate.removed_types)
        active_labels = set(candidate.label_types())
        for label, facts in candidate.removed_types.items():
            event.removed_types[label] = _merge_records(event.removed_types.get(label, []), facts)
        removed_attribute_names = set()
        for label in removed_labels:
            event.type_evidence.pop(label, None)
            removed_attribute_names.update(event.typed_attributes.pop(label, {}))
        for label, facts in candidate.type_evidence.items():
            event.type_evidence[label] = _merge_records(event.type_evidence.get(label, []), facts)
        if removed_labels - active_labels:
            event.types = [label for label in event.types if label not in removed_labels - active_labels]
        for label, values in candidate.typed_attributes.items():
            current = event.typed_attributes.setdefault(label, {})
            for name, fact in values.items():
                previous_fact = current.get(name)
                if previous_fact is not None and previous_fact.get("value") == fact.get("value"):
                    fact = deepcopy(fact)
                    fact["evidence_ids"] = list(dict.fromkeys([
                        *previous_fact.get("evidence_ids", []), *fact.get("evidence_ids", [])]))
                    fact["evidence_refs"] = _merge_records(previous_fact.get("evidence_refs", []),
                                                           fact.get("evidence_refs", []))
                current[name] = deepcopy(fact)
                event.attributes[name] = fact["value"]
        event.attributes.update(candidate.attributes)
        for name in removed_attribute_names:
            if name not in candidate.attributes and not any(name in values for values in event.typed_attributes.values()):
                event.attributes.pop(name, None)
        if candidate.types is not None:
            event.type = candidate.type if candidate.type in event.types else (event.types[0] if event.types else "待分类")
            event.classification_status = "已分类" if event.types else candidate.classification_status
        event.classification_evidence_status = "已记录" if event.type_evidence else "未记录"
        if candidate.other_reason is not None:
            event.other_reason = deepcopy(candidate.other_reason)
        if candidate.classification_conflicts:
            event.review_flag = "字段冲突待核实：" + "、".join(candidate.classification_conflicts)
        elif previous is not None and event.review_flag and event.review_flag.startswith("字段冲突待核实："):
            resolved = {
                f"{label}.{name}"
                for label, attrs in candidate.typed_attributes.items()
                for name, fact in attrs.items()
                if (previous.get("typed_attributes", {}).get(label, {}).get(name) is not None
                    and explicitly_corrects_typed_attribute(candidate, fact))
            }
            if resolved:
                prefix = "字段冲突待核实："
                pending = [name for name in event.review_flag[len(prefix):].split("、")
                           if name and name not in resolved]
                event.review_flag = prefix + "、".join(pending) if pending else None
    if continuity:
        if continuity["kind"] in {"confirm_recovery", "verify_recovery"}:
            event.recovery = deepcopy(continuity["recovery"])
        elif continuity["kind"] == "retract_recovery":
            event.recovery = None
            event.closed_at = None
            event.status = candidate.status or settings.default_status
        elif continuity["kind"] == "recurrence":
            event.recurrence_of = deepcopy(continuity["recurrence_of"])
    event.start_time = min(event.start_time, start)
    event.end_time = max(event.end_time, end)
    synchronize_summary(event, candidate, changes, available)
    project_fields(event, settings.closed_statuses)
    apply_relations(event, candidate, available)
    known = {(e["source"], e["message_id"], e.get("digest")) for e in event.evidence}
    added = []
    for key in candidate.evidence_ids:
        message = available[key]
        identity = message.source, message.message_id, message.digest
        if identity not in known:
            event.evidence.append(dict(source=message.source, message_id=message.message_id,
                                       send_time=message.sent_at.isoformat(), excerpt=message.content[:160],
                                       digest=message.digest))
            known.add(identity)
            added.append(message.message_id)
    review_proofs(event.evidence, available)
    snapshot = event.to_dict()
    snapshot.pop("updated_by")
    if previous is not None:
        previous.pop("updated_by")
        if snapshot == previous:
            return None
    event.version = max(event.version, len(event.updated_by)) + 1
    snapshot["version"] = event.version
    revision = dict(
        action="created" if created else "updated", window_start=window.start.isoformat(),
        window_end=window.end.isoformat(), message_ids=added, snapshot=snapshot,
    )
    if changes is not None:
        fields = [key for key in ("summary", "status", "severity", "assignees", "attributes", "types", "type_evidence",
                                  "removed_types", "typed_attributes", "classification_evidence_status",
                                  "classification_status", "other_reason", "review_flag", "closed_at", "recovery", "recurrence_of",
                                  "field_states", "field_issues", "relations")
                  if previous is None or previous[key] != snapshot[key]]
        effects = {change["effect"] for change in changes}
        kind = ("created" if created else "correction" if "correction" in effects or candidate.removed_types else
                "progress" if "progress" in effects else "confirmation" if "confirmation" in effects else "evidence_only")
        if classification_change and not effects and not created:
            kind = "correction" if candidate.removed_types else "classification"
        if continuity and continuity["kind"] == "retract_recovery":
            kind = "correction"
        revision.update(revision_no=len(event.updated_by) + 1, recorded_at=datetime.now(TZ).isoformat(),
                        change_kind=kind, change_summary="；".join(dict.fromkeys(c["text"] for c in changes)),
                        summary_changed=created or previous["summary"] != summary,
                        changed_fields=fields, changes=changes)
    event.updated_by.append(revision)
    return event


def snapshot_as_of(event: Event, end: datetime) -> dict | None:
    """恢复指定窗口结束时的状态，重放旧周期时不显示未来更新。"""
    revisions = [r for r in event.updated_by if parse_time(r["window_end"]) <= end]
    if not revisions:
        return None
    snapshot = deepcopy(revisions[-1]["snapshot"])
    snapshot["updated_by"] = [{k: v for k, v in r.items() if k != "snapshot"} for r in revisions]
    return snapshot


def snapshot_for_cycle(event: Event, end: datetime) -> dict | None:
    return snapshot_as_of(event, end)
