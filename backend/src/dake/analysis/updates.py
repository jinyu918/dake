"""更新稳定事件身份、融合分类/状态/优先级确定性规则，并恢复历史窗口快照。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from ..domain.manual_controls import (
    compose_review_flag,
    is_locked,
    record_lock_conflict,
    resolve_review_items,
    upsert_review_item,
)
from ..domain.models import Event, PENDING_TYPE, TZ, Window, parse_time
from ..domain.priority_policy import PRIORITY_EVIDENCE_FIELDS, evaluate_priority_proposal
from ..domain.status_policy import evaluate_status_proposal, normalize_legacy_status
from ..services.sender_identity import confirmed_sender_role
from .summary_changes import explicitly_corrects_typed_attribute, prepare_summary, restrict_fields
from .validation import candidate_times


def _bound_fact(fact: dict, available: dict) -> dict:
    value = deepcopy(fact)
    refs = list(value.get("evidence_refs", []))
    seen = {(ref.get("source"), ref.get("message_id"), ref.get("digest"), ref.get("quote")) for ref in refs}
    for key in value.get("evidence_ids", []):
        message = available.get(key)
        if message is None:
            continue
        reference = dict(
            source=message.source,
            message_id=message.message_id,
            id=message.message_id,
            digest=message.digest,
            conversation_id=message.conversation_id,
            send_time=message.sent_at.isoformat(),
            quote=value.get("quote"),
        )
        signature = (reference["source"], reference["message_id"], reference["digest"], reference["quote"])
        if signature not in seen:
            refs.append(reference)
            seen.add(signature)
    if refs:
        value["evidence_refs"] = refs
    return value


def bind_candidate_evidence(candidate, available):
    """将输入消息版本（含 digest 与发送时间）绑定到分类、属性及状态/优先级提案。"""
    proposal = candidate.priority_proposal
    if proposal is not None:
        proposal = _bound_fact(proposal, available)
        for field in PRIORITY_EVIDENCE_FIELDS:
            if fact := proposal.get(field):
                bound = _bound_fact(fact, available)
                for ref in bound.get("evidence_refs", []):
                    message = available.get(f"{ref['source']}:{ref['message_id']}")
                    if message is not None:
                        ref.update(uid=message.uid, sender_id=message.sender_id)
                proposal[field] = bound
    return replace(
        candidate,
        type_evidence={
            label: [_bound_fact(fact, available) for fact in facts]
            for label, facts in candidate.type_evidence.items()
        },
        removed_types={
            label: [_bound_fact(fact, available) for fact in facts]
            for label, facts in candidate.removed_types.items()
        },
        typed_attributes={
            label: {name: _bound_fact(fact, available) for name, fact in facts.items()}
            for label, facts in candidate.typed_attributes.items()
        },
        other_reason=_bound_fact(candidate.other_reason, available) if candidate.other_reason else None,
        status_proposal=_bound_fact(candidate.status_proposal, available) if candidate.status_proposal else None,
        priority_proposal=proposal,
    )


def _fact_proofs(facts, event):
    versions = {}
    for ref in event.evidence:
        versions.setdefault((ref["source"], ref["message_id"]), []).append(ref)
    proofs = set()
    for fact in facts:
        quote = fact.get("quote")
        refs = fact.get("evidence_refs", [])
        if refs:
            proofs.update(
                (ref.get("source"), ref.get("message_id", ref.get("id")), ref.get("digest"), ref.get("send_time"), ref.get("quote", quote))
                for ref in refs
            )
            continue
        for key in fact.get("evidence_ids", []):
            source, separator, message_id = key.partition(":")
            matched = versions.get((source, message_id), []) if separator else []
            if matched:
                proofs.update((source, message_id, ref.get("digest"), ref.get("send_time"), quote) for ref in matched)
            else:
                proofs.add((key, None, None, None, quote))
    return proofs


def _new_evidence(facts, old_facts, event) -> bool:
    return bool(_fact_proofs(facts, event) - _fact_proofs(old_facts, event))


def _merge_records(existing: list[dict], incoming: list[dict]) -> list[dict]:
    result = deepcopy(existing)
    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in result}
    for item in incoming:
        signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if signature not in seen:
            result.append(deepcopy(item))
            seen.add(signature)
    return result


import json  # noqa: E402


def has_structured_change(candidate, event, available=None, *, customer_profile=None) -> bool:
    """判断本轮分类、专属属性、状态提案或优先级信号是否足以单独构成有效修订。"""
    if event is None:
        return False
    if available is not None:
        candidate = bind_candidate_evidence(candidate, available)
    current_labels = event.types or ([] if event.type == PENDING_TYPE else [event.type])
    added = set(candidate.label_types()) - set(current_labels)
    type_evidence = any(
        _new_evidence(facts, event.type_evidence.get(label, []), event)
        for label, facts in candidate.type_evidence.items()
    )
    attributes = any(
        event.typed_attributes.get(label, {}).get(name, {}).get("value") != fact.get("value")
        or _new_evidence([fact], [event.typed_attributes.get(label, {}).get(name, {})], event)
        for label, facts in candidate.typed_attributes.items()
        for name, fact in facts.items()
    )
    priority_refresh = False
    if candidate.priority_proposal is None and customer_profile is not None and event.base_priority != "待评估":
        refreshed = evaluate_priority_proposal(
            None,
            current_priority=event.priority,
            current_base_priority=event.base_priority,
            current_detail=event.priority_detail,
            customer_profile=customer_profile,
        )
        priority_refresh = bool(refreshed["adopted"])
    return bool(
        added
        or candidate.removed_types
        or type_evidence
        or attributes
        or candidate.classification_conflicts
        or candidate.status_proposal is not None
        or candidate.priority_proposal is not None
        or priority_refresh
    )


has_classification_change = has_structured_change


def _collect_supporting_keys(candidate, changes, available=None) -> set[str]:
    """收集摘要变化、分类标签、属性及状态/优先级提案所引用的全部消息键，避免丢弃支撑证据。"""
    raw = {f"{ref['source']}:{ref['message_id']}" for change in changes for ref in change["evidence"]}
    for facts in candidate.type_evidence.values():
        for fact in facts:
            raw.update(fact.get("evidence_ids", []))
    for facts in candidate.removed_types.values():
        for fact in facts:
            raw.update(fact.get("evidence_ids", []))
    for attrs in candidate.typed_attributes.values():
        for fact in attrs.values():
            raw.update(fact.get("evidence_ids", []))
    for single in (candidate.other_reason, candidate.status_proposal, candidate.priority_proposal):
        if single:
            raw.update(single.get("evidence_ids", []))
    for field in PRIORITY_EVIDENCE_FIELDS:
        fact = (candidate.priority_proposal or {}).get(field)
        if fact:
            raw.update(fact["evidence_ids"])
    if available is None:
        return raw
    return {available[key].key if key in available else key for key in raw}


def apply_candidate(
    candidate,
    event,
    available,
    uid,
    stream,
    window: Window,
    settings,
    new_keys=None,
    *,
    continuity=None,
    field_corrections: dict[int, set[str]] | None = None,
    customer_profile: dict | None = None,
    sender_roles: dict | None = None,
):
    """创建或更新稳定 ID，融合分类、状态、优先级与人工锁定规则；无变化时返回 None。"""
    candidate = bind_candidate_evidence(candidate, available)
    start, end = candidate_times(candidate, available)
    _, new_end = candidate_times(candidate, available, new_keys)
    created = event is None
    previous = event.to_dict() if event is not None else None
    old_end_time = event.end_time if event is not None else None
    changes = None
    summary = candidate.summary
    structured_change = has_structured_change(candidate, event, customer_profile=customer_profile)

    if candidate.changes is not None:
        summary, changes = prepare_summary(
            candidate,
            event,
            available,
            historical=(continuity or {}).get("kind") == "historical",
            keep_facts=bool(
                continuity
                and (
                    continuity["kind"] == "retract_recovery"
                    or (continuity["kind"] == "confirm_recovery" and event is not None and event.recovery is None)
                )
            ),
            field_corrections=field_corrections,
        )
        if not changes and not created and not structured_change:
            return None
        if changes:
            candidate = restrict_fields(candidate, event, changes, available)
        start, end = candidate_times(candidate, available)
        _, new_end = candidate_times(candidate, available, new_keys)

    if created:
        event = Event(
            event_id=str(uuid4()),
            uid=uid,
            stream=stream,
            type=candidate.type,
            topic_key=candidate.topic_key,
            start_time=start,
            end_time=end,
            status=candidate.status or settings.default_status,
            severity=candidate.severity or settings.default_severity,
            summary=summary,
            created_on=window.start,
            types=candidate.label_types(),
            type_evidence=deepcopy(candidate.type_evidence),
            classification_evidence_status="已记录" if candidate.type_evidence else "未记录",
            classification_status=candidate.classification_status,
            other_reason=deepcopy(candidate.other_reason),
            removed_types=deepcopy(candidate.removed_types),
            typed_attributes=deepcopy(candidate.typed_attributes),
        )
    else:
        event = deepcopy(event)

    preserved_external_flags = []
    if not created and event.review_flag and not event.review_items:
        preserved_external_flags.append(event.review_flag)

    current_progress = changes is None or any(c["effect"] in {"progress", "correction"} for c in changes)
    current_progress = current_progress or structured_change
    not_late = created or (old_end_time is not None and new_end >= old_end_time)

    if created or (not_late and current_progress):
        event.summary = summary
        event.extensions.update(candidate.extensions)

        # 1. 跟进人更新与人工锁保护
        if candidate.assignees:
            if not created and is_locked(event, "assignees") and candidate.assignees != event.assignees:
                record_lock_conflict(
                    event,
                    "assignees",
                    candidate.assignees,
                    f"锁定跟进人收到新认领/指派建议（{'、'.join(candidate.assignees)}），待核对",
                )
            else:
                event.assignees = list(candidate.assignees)

        # 2. 多标签分类与专属属性更新（含人工锁保护）
        removed_labels = set(candidate.removed_types)
        active_labels = set(candidate.label_types())
        proposed_types = list(event.types)
        if candidate.types is not None:
            proposed_types = list(dict.fromkeys([*proposed_types, *candidate.label_types()]))
        if removed_labels - active_labels:
            proposed_types = [label for label in proposed_types if label not in (removed_labels - active_labels)]

        if not created and is_locked(event, "types") and proposed_types != event.types:
            proof_refs = [
                ref
                for facts in [*candidate.type_evidence.values(), *candidate.removed_types.values()]
                for fact in facts
                for ref in fact.get("evidence_refs", [])
            ]
            record_lock_conflict(
                event,
                "types",
                proposed_types,
                f"锁定分类标签（{'、'.join(event.types) or PENDING_TYPE}）收到新标签建议（{'、'.join(proposed_types) or PENDING_TYPE}），待核对",
                proof_refs,
            )
        else:
            event.types = proposed_types
            for label, facts in candidate.removed_types.items():
                event.removed_types[label] = _merge_records(event.removed_types.get(label, []), facts)
            removed_attribute_names = set()
            for label in removed_labels - active_labels:
                event.type_evidence.pop(label, None)
                removed_attribute_names.update(event.typed_attributes.pop(label, {}))
            for label, facts in candidate.type_evidence.items():
                if label in event.types:
                    event.type_evidence[label] = _merge_records(event.type_evidence.get(label, []), facts)
            for label, values in candidate.typed_attributes.items():
                if label not in event.types:
                    continue
                current = event.typed_attributes.setdefault(label, {})
                for name, fact in values.items():
                    previous_fact = current.get(name)
                    if previous_fact is not None and previous_fact.get("value") == fact.get("value"):
                        fact = deepcopy(fact)
                        fact["evidence_ids"] = list(dict.fromkeys([
                            *previous_fact.get("evidence_ids", []), *fact.get("evidence_ids", []),
                        ]))
                        fact["evidence_refs"] = _merge_records(
                            previous_fact.get("evidence_refs", []),
                            fact.get("evidence_refs", []),
                        )
                    current[name] = deepcopy(fact)
                    event.attributes[name] = fact["value"]
            event.attributes.update(candidate.attributes)
            for name in removed_attribute_names:
                if name not in candidate.attributes and not any(
                    name in values for values in event.typed_attributes.values()
                ):
                    event.attributes.pop(name, None)
            if candidate.types is not None or created:
                event.type = (
                    candidate.type
                    if candidate.type in event.types
                    else (event.types[0] if event.types else PENDING_TYPE)
                )
                event.classification_status = "已分类" if event.types else candidate.classification_status
            event.classification_evidence_status = "已记录" if event.type_evidence else "未记录"
            if candidate.other_reason is not None and "其他" in event.types:
                event.other_reason = deepcopy(candidate.other_reason)
            elif "其他" not in event.types:
                event.other_reason = None

        if candidate.classification_conflicts:
            upsert_review_item(
                event,
                kind="attribute_conflict",
                field="typed_attributes",
                summary="字段冲突待核对：" + "、".join(candidate.classification_conflicts),
                proposed=candidate.classification_conflicts,
            )
        elif previous is not None:
            resolved = {
                f"{label}.{name}"
                for label, attrs in candidate.typed_attributes.items()
                for name, fact in attrs.items()
                if (
                    previous.get("typed_attributes", {}).get(label, {}).get(name) is not None
                    and explicitly_corrects_typed_attribute(candidate, fact)
                )
            }
            if resolved:
                for item in event.review_items:
                    if item.get("kind") == "attribute_conflict" and item.get("status", "open") == "open":
                        remaining = [c for c in (item.get("proposed") or []) if c not in resolved]
                        if remaining:
                            item["proposed"] = remaining
                            item["summary"] = "字段冲突待核对：" + "、".join(remaining)
                        else:
                            item["status"] = "resolved"

    # 3. 客户恢复与复发边界先更新，以便状态策略校验是否具备可信客户恢复事实。
    if continuity:
        if continuity["kind"] == "confirm_recovery":
            event.recovery = deepcopy(continuity["recovery"])
        elif continuity["kind"] == "retract_recovery":
            event.recovery = None
            event.closed_at = None
            if not is_locked(event, "status"):
                event.status = candidate.status or settings.default_status
                if event.lifecycle_status == "已解决" and candidate.status_proposal is None:
                    event.lifecycle_status = "跟进中"
                    event.waiting_reason = None
                    event.resolution = None
        elif continuity["kind"] == "recurrence":
            event.recurrence_of = deepcopy(continuity["recurrence_of"])

    # 4. 生命周期状态确定性评估与人工锁保护
    if candidate.status_proposal is not None:
        sp = candidate.status_proposal
        sp_keys = [key for key in sp.get("evidence_ids", []) if key in available]
        sp_late = bool(not created and old_end_time is not None and sp_keys and max(available[k].sent_at for k in sp_keys) < old_end_time)
        sp_roles = {
            role
            for key in sp_keys
            if (role := confirmed_sender_role(available[key].to_dict(), sender_roles or {})) not in (None, "unknown")
        }
        status_eval = evaluate_status_proposal(
            sp,
            current_status=event.lifecycle_status,
            current_waiting_reason=event.waiting_reason,
            current_resolution=event.resolution,
            active_labels=event.types,
            classification_status=event.classification_status,
            has_customer_recovery=event.recovery is not None,
            evidence_roles=sp_roles,
            evidence_refs=sp.get("evidence_refs", []),
            late=sp_late,
            created=created,
        )
        for rev_item in status_eval["review_items"]:
            upsert_review_item(
                event,
                kind=rev_item["kind"],
                field=rev_item["field"],
                summary=rev_item["summary"],
                proposed=rev_item.get("proposed"),
                evidence_refs=rev_item.get("evidence_refs"),
            )
        if status_eval["adopted"]:
            proposed_tuple = (
                status_eval["lifecycle_status"],
                status_eval["waiting_reason"],
                status_eval["resolution"],
            )
            current_tuple = (event.lifecycle_status, event.waiting_reason, event.resolution)
            if not created and is_locked(event, "status") and proposed_tuple != current_tuple:
                record_lock_conflict(
                    event,
                    "status",
                    dict(
                        status=status_eval["lifecycle_status"],
                        waiting_reason=status_eval["waiting_reason"],
                        resolution=status_eval["resolution"],
                    ),
                    f"锁定状态（{event.lifecycle_status}）收到新状态证据（{status_eval['lifecycle_status']}），待核对",
                    sp.get("evidence_refs", []),
                )
            else:
                event.lifecycle_status = status_eval["lifecycle_status"]
                event.waiting_reason = status_eval["waiting_reason"]
                event.resolution = status_eval["resolution"]
                event.status_detail = status_eval["detail"]
                event.status = event.lifecycle_status
                if not status_eval["review_items"]:
                    resolve_review_items(
                        event,
                        field="status",
                        kinds=("pending_classification_completion", "unverified_recovery_actor"),
                    )
    elif (created or (not_late and current_progress)) and candidate.status:
        if not created and is_locked(event, "status") and candidate.status != event.status:
            record_lock_conflict(
                event,
                "status",
                dict(status=candidate.status),
                f"锁定状态（{event.status}）收到新状态建议（{candidate.status}），待核对",
            )
        else:
            event.status = candidate.status
            mapped_status, _ = normalize_legacy_status(event.status)
            if mapped_status != "状态未明":
                event.lifecycle_status = mapped_status

    # 5. 业务优先级确定性评估（Impact × Urgency + L9/L10 提档封顶）与人工锁保护
    if candidate.priority_proposal is not None or (customer_profile is not None and event.base_priority != "待评估"):
        pp = candidate.priority_proposal
        pp_keys = [key for key in (pp or {}).get("evidence_ids", []) if key in available]
        pp_late = bool(not created and old_end_time is not None and pp_keys and max(available[k].sent_at for k in pp_keys) < old_end_time)
        prio_eval = evaluate_priority_proposal(
            pp,
            current_priority=event.priority,
            current_base_priority=event.base_priority,
            current_detail=event.priority_detail,
            customer_profile=customer_profile,
            evidence_refs=(pp or {}).get("evidence_refs", []),
            late=pp_late,
            created=created,
        )
        if prio_eval["adopted"]:
            if not created and is_locked(event, "priority") and prio_eval["priority"] != event.priority:
                record_lock_conflict(
                    event,
                    "priority",
                    dict(priority=prio_eval["priority"], base_priority=prio_eval["base_priority"]),
                    f"锁定优先级（{event.priority}）收到新评估结果（{prio_eval['priority']}），待核对",
                    (pp or {}).get("evidence_refs", []),
                )
            else:
                event.priority = prio_eval["priority"]
                event.base_priority = prio_eval["base_priority"]
                event.priority_detail = prio_eval["detail"]
                if pp is not None or event.severity in {"P0", "P1", "P2", "P3", "待评估"}:
                    event.severity = event.priority
    elif (created or (not_late and current_progress)) and candidate.severity:
        if not created and is_locked(event, "priority") and candidate.severity != event.severity:
            record_lock_conflict(
                event,
                "priority",
                dict(priority=candidate.severity),
                f"锁定优先级收到新等级建议（{candidate.severity}），待核对",
            )
        else:
            event.severity = candidate.severity

    # 6. 维护显式配置的关闭宽限锚点（默认 closed_statuses 为空，已解决仍允许重开）。
    if created or (not_late and current_progress):
        if event.status in settings.closed_statuses or event.lifecycle_status in settings.closed_statuses:
            if event.closed_at is None:
                event.closed_at = end
        else:
            event.closed_at = None

    if event.review_items or preserved_external_flags:
        event.review_flag = compose_review_flag(event.review_items, extra_flags=preserved_external_flags)
    else:
        event.review_flag = None

    event.start_time = min(event.start_time, start)
    event.end_time = max(event.end_time, end)
    known = {(e["source"], e["message_id"], e.get("digest")) for e in event.evidence}
    added = []
    for key in candidate.evidence_ids:
        message = available[key]
        identity = message.source, message.message_id, message.digest
        if identity not in known:
            entry = dict(
                source=message.source,
                message_id=message.message_id,
                send_time=message.sent_at.isoformat(),
                excerpt=message.content[:160],
                digest=message.digest,
            )
            if message.conversation_id:
                entry["conversation_id"] = message.conversation_id
            event.evidence.append(entry)
            known.add(identity)
            added.append(message.message_id)

    snapshot = event.to_dict()
    snapshot.pop("updated_by")
    if previous is not None:
        prev_compare = dict(previous)
        prev_compare.pop("updated_by", None)
        prev_compare.pop("version", None)
        curr_compare = dict(snapshot)
        curr_compare.pop("version", None)
        if curr_compare == prev_compare:
            return None

    event.version = max(event.version, len(event.updated_by)) + 1
    snapshot["version"] = event.version
    revision = dict(
        action="created" if created else "updated",
        window_start=window.start.isoformat(),
        window_end=window.end.isoformat(),
        message_ids=added,
        snapshot=snapshot,
    )
    if changes is not None:
        tracked_keys = (
            "summary",
            "status",
            "lifecycle_status",
            "waiting_reason",
            "resolution",
            "severity",
            "priority",
            "base_priority",
            "assignees",
            "attributes",
            "types",
            "type_evidence",
            "removed_types",
            "typed_attributes",
            "classification_evidence_status",
            "classification_status",
            "other_reason",
            "review_flag",
            "closed_at",
            "recovery",
            "recurrence_of",
        )
        fields = [key for key in tracked_keys if previous is None or previous.get(key) != snapshot.get(key)]
        effects = {change["effect"] for change in changes}
        kind = (
            "created"
            if created
            else "correction"
            if "correction" in effects or candidate.removed_types
            else "progress"
            if "progress" in effects
            else "confirmation"
            if "confirmation" in effects
            else "evidence_only"
        )
        if structured_change and not effects and not created:
            kind = "correction" if candidate.removed_types else "classification"
        if continuity and continuity["kind"] == "retract_recovery":
            kind = "correction"
        summary_line = "；".join(dict.fromkeys(c["text"] for c in changes))
        if not summary_line:
            summary_line = f"更新{'、'.join(fields[:3])}" if fields else "补充分类或状态依据"
        revision.update(
            revision_no=len(event.updated_by) + 1,
            recorded_at=datetime.now(TZ).isoformat(),
            change_kind=kind,
            change_summary=summary_line,
            summary_changed=created or previous["summary"] != summary,
            changed_fields=fields,
            changes=changes,
        )
    event.updated_by.append(revision)
    return event


def snapshot_as_of(event: Event, end: datetime) -> dict | None:
    """恢复指定窗口结束时的自动处理快照，不把后续人工修改回填为历史窗口自动结果。"""
    revisions = [r for r in event.updated_by if r.get("source") != "manual" and parse_time(r["window_end"]) <= end]
    if not revisions:
        return None
    snapshot = deepcopy(revisions[-1]["snapshot"])
    snapshot["updated_by"] = [{k: v for k, v in r.items() if k != "snapshot"} for r in revisions]
    return snapshot
