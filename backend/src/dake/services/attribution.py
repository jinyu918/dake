"""执行四类归属、多标签合并和客户恢复边界，复用事件修订及消息事务。"""

from copy import deepcopy
from dataclasses import asdict, replace
import json

from ..analysis.attribution import decide
from ..analysis.continuity import resolve_continuity, validate_continuity
from ..analysis.contract import ContractError
from ..analysis.summary_changes import (
    explicitly_corrects_typed_attribute,
    field_only_corrections,
    prepare_summary,
    validate_changes,
)
from ..analysis.updates import apply_candidate, has_structured_change
from ..analysis.validation import candidate_times, validate_priority_evidence
from ..config import REVIEW
from ..domain.models import message_order
from ..domain.priority_policy import merge_priority_proposals


class _ValidationStore:
    """预校验的空写入目标，不接触数据库、记录器或文件。"""

    def save_event(self, event):
        pass

    def record_attributions(self, pending, stream, records):
        pass


def preflight_attributions(candidates, **kwargs):
    """在隔离副本上复用完整归属逻辑，让提交前的合约错误进入已有纠正机会。"""
    apply_attributions(deepcopy(candidates), store=_ValidationStore(), observe=lambda *a, **k: None,
                       **deepcopy(kwargs))


def _proposal_written(candidate, revision, available):
    """只为已保存的具体事实或结构化字段变化关联建议，同一消息里的无关候选不能冒领修订。"""
    if candidate.changes is None:
        return True
    for proposed in candidate.changes:
        refs = {
            (
                available[ref["message_id"]].source,
                available[ref["message_id"]].message_id,
                available[ref["message_id"]].digest,
                ref["quote"],
            )
            for ref in proposed["evidence"]
        }
        for written in revision.get("changes", []):
            if (
                (proposed["kind"], proposed["text"].strip(), proposed["replaces"])
                == (written["kind"], written["text"], written["replaces"])
                and refs.intersection(
                    (ref["source"], ref["message_id"], ref["digest"], ref["quote"])
                    for ref in written["evidence"]
                )
            ):
                return True
    if not candidate.changes and revision.get("change_kind") in {"classification", "correction"}:
        candidate_mids = {available[key].message_id for key in candidate.evidence_ids if key in available}
        return bool(candidate_mids.intersection(revision.get("message_ids", [])) or revision.get("changed_fields"))
    return False


def _merge_records(existing, incoming):
    result = deepcopy(existing)
    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in result}
    for item in incoming:
        signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if signature not in seen:
            result.append(deepcopy(item))
            seen.add(signature)
    return result


def _merge_extraction(left, right):
    if right is None:
        return deepcopy(left)
    if left is None:
        return deepcopy(right)
    result = deepcopy(left)
    for key, value in right.items():
        if key not in {"assignees", "attributes", "extensions", "typed_attributes"}:
            if value is not None:
                result[key] = deepcopy(value)
            continue
        identity = (
            (lambda item: (item.get("type_label"), item["name"]))
            if key == "typed_attributes"
            else (lambda item: item["value"] if key == "assignees" else item["name"])
        )
        items = {identity(item): deepcopy(item) for item in result.get(key, [])}
        items.update({identity(item): deepcopy(item) for item in value})
        result[key] = list(items.values())
    return result


def _combine(candidates, old, available, new_keys):
    """按消息时间合并同一事项的多个进展，保留直接字段来源并标记未解属性冲突。"""
    combined = None
    labels, type_evidence, removed_types, typed_attributes = [], {}, {}, {}
    field_values, conflicts, generic_values = {}, set(), {}
    previous_attributes = deepcopy(old.typed_attributes)
    field_corrections = {}
    attributes = {}
    progress_attributes = {"审批、交付与可用验证", "上线与需求验证结果", "处置与验证结果"}
    for label, facts in old.typed_attributes.items():
        for name, fact in facts.items():
            if name == "目标总量与增减量":
                field_values[(label, name)] = {fact.get("value")}

    for index, candidate in enumerate(candidates):
        if candidate_times(candidate, available, new_keys)[1] < old.end_time:
            candidate = replace(
                candidate,
                status=None,
                severity=None,
                assignees=[],
                attributes={},
                typed_attributes={},
                extensions={},
                status_proposal=None,
                priority_proposal=None,
            )
        change_offset = len(combined.changes or []) if combined is not None else 0
        if index == 0:
            combined = deepcopy(candidate)
        else:
            combined.evidence_ids = list(dict.fromkeys([*combined.evidence_ids, *candidate.evidence_ids]))
            combined.summary = candidate.summary
            combined.status = candidate.status or combined.status
            combined.severity = candidate.severity or combined.severity
            combined.assignees = candidate.assignees or combined.assignees
            combined.status_proposal = candidate.status_proposal or combined.status_proposal
            combined.priority_proposal = merge_priority_proposals(
                combined.priority_proposal, candidate.priority_proposal
            )
            combined.other_reason = candidate.other_reason or combined.other_reason
            if (combined.changes is None) != (candidate.changes is None):
                raise ValueError("同一事件不能混用有变化依据和旧版摘要候选")
            if candidate.changes is not None:
                combined.changes.extend(deepcopy(candidate.changes))

        for label, facts in candidate.removed_types.items():
            labels = [value for value in labels if value != label]
            type_evidence.pop(label, None)
            typed_attributes.pop(label, None)
            previous_attributes.pop(label, None)
            field_values = {key: values for key, values in field_values.items() if key[0] != label}
            removed_types[label] = _merge_records(removed_types.get(label, []), facts)
        for label in candidate.label_types():
            if label not in labels:
                labels.append(label)
        for label, facts in candidate.type_evidence.items():
            type_evidence[label] = _merge_records(type_evidence.get(label, []), facts)
        field_corrections.update({
            change_offset + offset: values
            for offset, values in field_only_corrections(candidate, previous_attributes, old.summary).items()
        })
        for label, facts in candidate.typed_attributes.items():
            current = typed_attributes.setdefault(label, {})
            for name, fact in facts.items():
                field_key = label, name
                conflict_name = f"{label}.{name}"
                values = field_values.setdefault(field_key, set())
                if name in progress_attributes or explicitly_corrects_typed_attribute(candidate, fact):
                    values.clear()
                    conflicts.discard(conflict_name)
                values.add(fact.get("value"))
                if len(values) > 1:
                    conflicts.add(conflict_name)
                current[name] = deepcopy(fact)
                previous_attributes.setdefault(label, {})[name] = deepcopy(fact)
        for name, value in candidate.attributes.items():
            generic_values.setdefault(name, set()).add(value)
            attributes[name] = value
        left_extraction = combined.extensions.get("extraction_v1")
        right_extraction = candidate.extensions.get("extraction_v1")
        extensions = deepcopy(combined.extensions)
        extensions.update({key: deepcopy(value) for key, value in candidate.extensions.items() if key != "extraction_v1"})
        extraction = _merge_extraction(left_extraction, right_extraction)
        if extraction is not None:
            extensions["extraction_v1"] = extraction
        combined.extensions = extensions

    for label, facts in typed_attributes.items():
        for name, fact in facts.items():
            generic_values.setdefault(name, set()).add(fact.get("value"))
    conflicting_names = {name for label, name in field_values if f"{label}.{name}" in conflicts}
    conflicting_names.update(name for name, values in generic_values.items() if len(values) > 1)
    for label, name in [(label, name) for label, facts in typed_attributes.items() for name in facts]:
        if f"{label}.{name}" in conflicts:
            typed_attributes[label].pop(name, None)
            if not typed_attributes[label]:
                typed_attributes.pop(label)
    combined.types = labels if any(candidate.types is not None for candidate in candidates) else None
    combined.type_evidence = type_evidence
    combined.removed_types = removed_types
    combined.typed_attributes = typed_attributes
    for label, facts in typed_attributes.items():
        for name, fact in facts.items():
            if name not in conflicting_names:
                attributes[name] = fact["value"]
    combined.attributes = {name: value for name, value in attributes.items() if name not in conflicting_names}
    combined.classification_conflicts = sorted(conflicts)
    return combined, field_corrections


def apply_attributions(
    candidates,
    *,
    store,
    events,
    visible_events,
    available,
    pending,
    eligible,
    background,
    result,
    settings,
    observe,
    customer_keys=(),
    customer_profile=None,
    sender_roles=None,
    recorder=None,
):
    """先核对整批，再按消息时间执行；恢复与再次发生分别保存，不被批内合并压平。"""
    new_keys = {m.key for m in pending}
    human_keys = {m.key for m in eligible}
    customer_keys = set(customer_keys) & human_keys
    plans = []
    for candidate in candidates:
        if any(key not in new_keys for key in candidate.evidence_ids):
            raise ContractError("明确归属的写入证据只能来自本批新增或变更消息，旧背景须放在 basis")
        try:
            validate_priority_evidence(candidate, available, uid=result.uid, human_keys=human_keys,
                                       customer_keys=customer_keys)
        except ValueError as error:
            raise ContractError(str(error)) from None
        validate_changes(candidate, available, human_keys)
        validate_continuity(candidate, available, human_keys)
        record = decide(
            candidate,
            visible_events=visible_events,
            available=available,
            new_keys=new_keys,
            eligible_keys=human_keys,
            background=background,
            uid=result.uid,
            stream=result.stream,
            before=result.window.end,
            closed_statuses=settings.closed_statuses,
        )
        record.update(
            kind="event_attribution",
            window_start=result.window.start.isoformat(),
            window_end=result.window.end.isoformat(),
            requested_action=candidate.decision,
        )
        if recorder is not None:
            record.update(attempt_id=recorder.attempt_id, proposal_id=recorder.proposals[id(candidate)])
        plans.append((candidate, record))

    def order(pair):
        latest = max((available[key] for key in pair[0].evidence_ids), key=message_order)
        return (*message_order(latest), 0 if (pair[0].continuity or {}).get("kind") == "confirm_recovery" else 1)

    ordered = sorted(plans, key=order)
    groups = []
    for pair in ordered:
        candidate, record = pair
        previous = groups[-1][-1] if groups else None
        if (
            previous
            and record["action"] == previous[1]["action"] == "update"
            and record["event_id"] == previous[1]["event_id"]
            and candidate.continuity is None
            and previous[0].continuity is None
            and candidate.changes != []
            and previous[0].changes != []
            and previous[0].status not in settings.closed_statuses
            and (previous[0].status_proposal or {}).get("status") not in settings.closed_statuses
        ):
            groups[-1].append(pair)
        else:
            groups.append([pair])

    current = {event.event_id: event for event in events}
    for group in groups:
        accepted = []
        old = None
        for candidate, record in group:
            related = record["event_id"] or (candidate.continuity or {}).get("previous_event_id")
            old = current.get(related)
            boundary = resolve_continuity(
                candidate,
                record,
                old,
                available=available,
                customer_keys=customer_keys,
                closed_statuses=settings.closed_statuses,
            )
            if boundary:
                record["continuity"] = boundary
            if record["action"] == "review":
                result.review.append(dict(
                    review_flag=REVIEW,
                    type=candidate.type,
                    message_ids=[available[key].message_id for key in candidate.evidence_ids],
                    possible_event_ids=record["possible_event_ids"],
                    reason=record["reason"],
                ))
            elif record["action"] == "new" or (
                record["action"] == "update"
                and (
                    candidate.changes != []
                    or has_structured_change(candidate, old, available, customer_profile=customer_profile)
                )
            ):
                accepted.append((candidate, record, boundary))

        updated = None
        if accepted:
            first, record, boundary = accepted[0]
            action = record["action"]
            effective = accepted
            if action == "update" and len(accepted) > 1:
                effective = [
                    (c, r, b)
                    for c, r, b in accepted
                    if c.changes is None
                    or b is not None
                    or has_structured_change(c, old, available, customer_profile=customer_profile)
                    or bool(prepare_summary(c, old, available)[1])
                ]
            if effective:
                writable, field_corrections = (
                    _combine([c for c, _, _ in effective], old, available, new_keys)
                    if action == "update"
                    else (first, {})
                )
                updated = apply_candidate(
                    writable,
                    old if action == "update" else None,
                    available,
                    result.uid,
                    result.stream,
                    result.window,
                    settings,
                    new_keys,
                    continuity=boundary,
                    field_corrections=field_corrections,
                    customer_profile=customer_profile,
                    sender_roles=sender_roles,
                )
            if updated is not None:
                if recorder is not None:
                    updated.updated_by[-1].update(
                        attempt_id=recorder.attempt_id,
                        proposal_ids=[
                            r["proposal_id"]
                            for c, r, _ in accepted
                            if _proposal_written(c, updated.updated_by[-1], available)
                        ],
                    )
                if writable.changes is not None:
                    updated.updated_by[-1]["policy_version"] = record["policy_version"]
                store.save_event(updated)
                current[updated.event_id] = updated
                if action == "new":
                    record["event_id"] = updated.event_id
                    result.created.append(updated.event_id)
                elif updated.event_id not in result.updated:
                    result.updated.append(updated.event_id)

        for candidate, record in group:
            action = record["action"]
            if candidate.changes is not None and action in {"new", "update"}:
                revision = updated.updated_by[-1] if updated is not None else None
                written = (
                    {
                        (ref["source"], ref["message_id"], ref["digest"])
                        for change in revision.get("changes", [])
                        for ref in change["evidence"]
                    }
                    if revision
                    else set()
                )
                referenced = {(ref["source"], ref["id"], ref["digest"]) for ref in record["new_evidence"]}
                if candidate.changes:
                    if not written.intersection(referenced):
                        revision = None
                elif revision is None or revision.get("change_kind") not in {"classification", "correction"}:
                    revision = None
                record.update(
                    revision_no=revision.get("revision_no") if revision else None,
                    summary_changed=revision.get("summary_changed", False) if revision else False,
                    change_kind=revision.get("change_kind") if revision else "no_change",
                )
            observe(
                "decision",
                candidate=asdict(candidate),
                action=action,
                event_id=record["event_id"],
                possible_event_ids=record["possible_event_ids"],
                reason=record["reason"],
                **(
                    {
                        "proposal_id": record["proposal_id"],
                        "contributed_to_revision": bool(
                            updated and record["proposal_id"] in updated.updated_by[-1].get("proposal_ids", [])
                        ),
                        "change_scope": "combined_event_group" if len(group) > 1 else "single_proposal",
                    }
                    if recorder is not None
                    else {}
                ),
                before=old.to_dict() if old and action != "new" else None,
                after=updated.to_dict() if updated else old.to_dict() if old else None,
            )

    records = [record for _, record in plans]
    store.record_attributions(pending, result.stream, records)
    result.filter_audit.extend(records)
    return list(current.values())
