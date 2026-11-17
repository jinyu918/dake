from copy import deepcopy
import hashlib
import json
from uuid import uuid4

from ..domain.fields import FieldTarget, FieldUpdate
from .contract import ContractError
from .relations import review_proofs


def field_key(target):
    target = FieldTarget.model_validate(target).model_dump()
    return ":".join(str(target[name] or "") for name in ("kind", "type_label", "name"))


def _proofs(event, fact):
    if fact.get("evidence_refs"):
        return deepcopy(fact["evidence_refs"])
    return [dict(ref, quote=fact.get("quote")) for ref in event.evidence
            if f"{ref['source']}:{ref['message_id']}" in fact.get("evidence_ids", [])]


def field_state(event, target, *, default_value=None):
    target = FieldTarget.model_validate(target).model_dump()
    key = field_key(target)
    if key in event.field_states:
        return event.field_states[key]
    state = dict(target=target, facts=[], adopted_ids=[], locked_ids=[], locked=False, decision=None, archived=False)
    event.field_states[key] = state
    kind, name, label = target["kind"], target["name"], target["type_label"]
    evidence = []
    if kind == "attribute":
        old = event.typed_attributes.get(label, {}).get(name)
        value = old.get("value") if old else None
        evidence = _proofs(event, old) if old else []
    elif kind == "label":
        value = name in (event.types or ([event.type] if event.type != "待分类" else []))
        evidence = [ref for fact in event.type_evidence.get(name, []) for ref in _proofs(event, fact)]
    elif name == "other_reason":
        old = event.other_reason
        value = old.get("value") if old else None
        evidence = _proofs(event, old) if old else []
    else:
        value = getattr(event, name)
        extracted = event.extensions.get("extraction_v1", {}).get(name)
        if name in {"status", "severity"} and value == default_value:
            if not isinstance(extracted, dict) or extracted.get("value") != value:
                for revision in reversed(event.updated_by):
                    snapshot = revision.get("snapshot", {})
                    if snapshot.get(name) != value:
                        break
                    previous = snapshot.get("extensions", {}).get("extraction_v1", {}).get(name)
                    if isinstance(previous, dict) and previous.get("value") == value:
                        extracted = previous
                        break
        if isinstance(extracted, dict):
            evidence = _proofs(event, extracted)
        if name in {"status", "severity"} and value == default_value and not (
                isinstance(extracted, dict) and extracted.get("value") == value):
            value = None
    if value is not None and value != []:
        signature = json.dumps([event.event_id, target, value, evidence], ensure_ascii=False, sort_keys=True)
        identity = "legacy-" + hashlib.sha256(signature.encode()).hexdigest()[:24]
        state["facts"].append(dict(id=identity, value=value, value_state="value", stage="未记录", scope="未记录",
            business_time=None, time_kind="unknown", intent="supplement", replaces=[], reason="历史记录迁移，未补造事实范围",
            evidence=evidence, source="legacy", evidence_status="recorded" if evidence else "unrecorded",
            status="adopted", input_ref=None))
        state["adopted_ids"] = [identity]
    return state


def issue(event, state, reason, fact_id=None, affected_ids=()):
    key = field_key(state["target"])
    existing = next((item for item in event.field_issues if item["status"] == "open"
        and item["field_key"] == key and item["reason"] == reason and item.get("fact_id") == fact_id
        and item.get("affected_ids", []) == list(affected_ids)), None)
    if existing:
        return existing
    record = dict(id=uuid4().hex, field_key=key, target=deepcopy(state["target"]), reason=reason,
                  fact_id=fact_id, affected_ids=list(affected_ids), status="open", resolution=None)
    event.field_issues.append(record)
    return record


def active_facts(state):
    return [fact for fact in state["facts"] if fact["id"] in state["adopted_ids"]]


def label_active(event, label):
    state = event.field_states.get(field_key(dict(kind="label", name=label, type_label=None)))
    adopted = active_facts(state) if state else []
    return adopted[-1]["value"] is True if adopted else label in event.types


def _same_slot(left, right):
    return all(left[name] == right[name] for name in ("stage", "scope", "business_time", "time_kind"))


def _record_proofs(update, available):
    proofs = []
    for ref in update["evidence"]:
        message = available[ref["message_id"]]
        proofs.append(dict(source=message.source, message_id=message.message_id, digest=message.digest,
            conversation_id=message.conversation_id, send_time=message.sent_at.isoformat(), quote=ref["quote"]))
    return proofs


def validate_field_updates(candidate, available, eligible_keys, event=None):
    refs = set()
    for raw in candidate.field_updates or []:
        update = FieldUpdate.model_validate(raw)
        if update.ref in refs:
            raise ContractError("字段变化的批内引用不得重复")
        refs.add(update.ref)
        if not all(value.strip() for value in (update.value, update.stage, update.scope, update.reason)):
            raise ContractError("字段事实和变化理由不能为空白")
        if (update.time_kind == "unknown") != (update.business_time is None):
            raise ContractError("业务时间未知时保持空值，已知时间须区分计划和实际")
        if update.target.kind == "label":
            raise ContractError("自动标签采用及撤销须使用逐标签依据")
        for ref in update.evidence:
            message = available.get(ref.message_id)
            if (ref.message_id not in eligible_keys or ref.message_id not in candidate.evidence_ids
                    or message is None or not ref.quote.strip() or ref.quote not in message.content):
                raise ContractError("字段变化必须有本轮直接连续引文")
        if not any(update.value in ref.quote for ref in update.evidence):
            raise ContractError("字段变化值必须来自该字段直接引文")
        if update.business_time is not None and (not update.business_time.strip()
                or not any(update.business_time in ref.quote for ref in update.evidence)):
            raise ContractError("业务时间须保留直接引文中的时间原话")
        if update.intent == "correction" and not update.replaces:
            raise ContractError("字段更正必须定位被更正事实")
        if update.intent in {"supplement", "confirmation", "conflict"} and update.replaces:
            raise ContractError("补充、确认和冲突不能自行替换旧事实")


def apply_field_update(event, raw, available, *, late=False, batch_refs=None, default_value=None, incoming_labels=()):
    update = FieldUpdate.model_validate(raw).model_dump()
    state = field_state(event, update["target"], default_value=default_value)
    if (update["target"]["kind"] == "attribute" and not label_active(event, update["target"]["type_label"])
            and (update["target"]["type_label"] not in incoming_labels
                 or field_key(dict(kind="label", name=update["target"]["type_label"], type_label=None)) in event.field_states)):
        state["archived"] = True
    evidence = _record_proofs(update, available)
    signature = json.dumps([event.event_id, update, evidence], sort_keys=True, ensure_ascii=False)
    fact_id = "fact-" + hashlib.sha256(signature.encode()).hexdigest()[:24]
    if batch_refs is not None:
        batch_refs[update["ref"]] = fact_id
    if any(fact["id"] == fact_id for fact in state["facts"]):
        return
    targets = []
    for reference in update["replaces"]:
        identity = (batch_refs or {}).get(reference[6:]) if reference.startswith("batch:") else reference
        target = next((fact for fact in state["facts"] if fact["id"] == identity), None)
        if target is None:
            raise ContractError("字段变化目标不存在或不属于该字段")
        targets.append(target)
    if update["intent"] == "correction" and any(
            target["source"] != "legacy" and (target["stage"], target["scope"]) != (update["stage"], update["scope"])
            for target in targets):
        raise ContractError("字段更正须与目标事实的环节和范围一致")
    if update["intent"] == "progress" and targets and any(
            _same_slot(target, update) for target in targets):
        raise ContractError("同一事实不能以正常进展替换，须澄清冲突或明确更正")
    fact = dict(id=fact_id, value=update["value"], value_state="value", stage=update["stage"], scope=update["scope"],
        business_time=update["business_time"], time_kind=update["time_kind"], intent=update["intent"],
        replaces=[target["id"] for target in targets], reason=update["reason"], evidence=evidence,
        source="automatic", evidence_status="current", status="suggested", input_ref=update["ref"])
    state["facts"].append(fact)
    current = active_facts(state)
    same = [old for old in current if _same_slot(old, fact) or old["source"] == "legacy"]
    different = [old for old in same if old["value"] != fact["value"] or old["value_state"] != "value"]
    protected = set(state["locked_ids"])
    replacement_ids = {target["id"] for target in targets}
    blocked = bool(protected.intersection(replacement_ids) or any(old["id"] in protected for old in different))
    blocked = blocked or (state["locked"] and update["target"]["kind"] == "common"
                          and any(old["value"] != fact["value"] for old in current))
    if late and update["intent"] != "correction":
        fact["status"] = "historical"
        return
    if state["archived"]:
        issue(event, state, "已撤销标签下的新事实待复核", fact_id)
        return
    if blocked or update["intent"] == "conflict" or any(old["id"] not in replacement_ids for old in different):
        issue(event, state, "锁定采用值出现新建议" if blocked else "同一事实存在互斥说法", fact_id)
        return
    for old in current:
        if old["id"] in replacement_ids:
            state["adopted_ids"].remove(old["id"])
            old["status"] = "corrected" if update["intent"] == "correction" else "historical"
    if same and not different and not replacement_ids:
        fact["status"] = "confirmed"
        state["adopted_ids"].append(fact_id)
    else:
        state["adopted_ids"].append(fact_id)
        fact["status"] = "adopted"
    if update["intent"] == "correction":
        for problem in event.field_issues:
            if (problem["field_key"] == field_key(state["target"]) and problem["status"] == "open"
                    and problem.get("fact_id") in replacement_ids):
                problem.update(status="resolved", resolution=dict(source="automatic", fact_id=fact_id))


def project_field(event, state):
    target = state["target"]
    kind, name, label = target["kind"], target["name"], target["type_label"]
    adopted = active_facts(state)
    if state["archived"]:
        if kind == "attribute":
            event.typed_attributes.get(label, {}).pop(name, None)
            event.attributes.pop(name, None)
        return
    if not adopted:
        return
    selected = adopted[-1]
    value = selected["value"] if selected["value_state"] == "value" else None
    if kind == "attribute":
        values = list(dict.fromkeys(str(fact["value"]) for fact in adopted if fact["value_state"] == "value"))
        value = "；".join(values) if values else None
        proofs = [ref for fact in adopted for ref in fact["evidence"]
                  if ref.get("evidence_status") != "superseded"]
        event.typed_attributes.setdefault(label, {})[name] = dict(type_label=label, name=name, value=value,
            value_state=selected["value_state"], evidence_refs=deepcopy(proofs),
            evidence_ids=list(dict.fromkeys(f"{ref['source']}:{ref['message_id']}" for ref in proofs)),
            quote=proofs[-1].get("quote") if proofs else None)
        if value is None:
            event.attributes.pop(name, None)
        else:
            event.attributes[name] = value
    elif kind == "label":
        if value is True:
            event.types = list(dict.fromkeys([*event.types, name]))
        else:
            event.types = [item for item in event.types if item != name]
        event.type = event.type if event.type in event.types else (event.types[0] if event.types else "待分类")
        event.classification_status = "已分类" if event.types else "待分类"
    elif name == "assignees":
        event.assignees = [value] if isinstance(value, str) else value or []
    elif name == "other_reason":
        event.other_reason = dict(value=value, source=selected["source"], evidence_refs=selected["evidence"]) if value else None
    else:
        setattr(event, name, value if value is not None else "未知" if selected["value_state"] == "unknown" else "")


def review_message_versions(event, available):
    for state in event.field_states.values():
        for fact in state["facts"]:
            valid = review_proofs(fact["evidence"], available)
            changed = any(proof.get("evidence_status") == "superseded" for proof in fact["evidence"])
            if changed:
                fact["evidence_status"] = "partially_superseded" if valid else "superseded"
            elif fact["evidence_status"] in {"superseded", "partially_superseded"}:
                fact["evidence_status"] = "recorded" if fact["source"] == "legacy" else "current"
        for fact in active_facts(state):
            supported = any(other["evidence_status"] != "superseded" and _same_slot(other, fact)
                and other["value"] == fact["value"] and other["value_state"] == fact["value_state"]
                for other in active_facts(state))
            if fact["evidence_status"] == "superseded" and not supported and not state["archived"]:
                issue(event, state, "采用事实的直接消息依据已修订", affected_ids=[fact["id"]])
        for problem in event.field_issues:
            if (problem["field_key"] != field_key(state["target"]) or problem["status"] != "open"
                    or problem["reason"] != "采用事实的直接消息依据已修订"):
                continue
            unresolved = any(fact["id"] in problem["affected_ids"] and fact["evidence_status"] == "superseded"
                and not any(other["evidence_status"] != "superseded" and _same_slot(other, fact)
                    and other["value"] == fact["value"] for other in active_facts(state))
                for fact in active_facts(state))
            if not unresolved or state["archived"]:
                problem.update(status="resolved", resolution=dict(source="automatic", reason="对应事实已更正或另有独立有效依据"))


def project_fields(event, closed_statuses):
    for state in event.field_states.values():
        project_field(event, state)
    state = event.field_states.get(field_key(dict(kind="common", name="status", type_label=None)))
    adopted = active_facts(state) if state else []
    has_status = not adopted or adopted[-1]["value_state"] == "value"
    # 关闭宽限沿用消息观察锚点，不以人工操作时间推造业务完成时间。
    event.closed_at = (event.closed_at or event.end_time) if has_status and event.status in closed_statuses else None


def protect_labels(event, candidate, available):
    blocked = set()
    changes = [(label, False, facts) for label, facts in candidate.removed_types.items()]
    changes += [(label, True, candidate.type_evidence.get(label, [])) for label in candidate.label_types()
                if label in candidate.removed_types
                or field_key(dict(kind="label", name=label, type_label=None)) in event.field_states]
    for label, value, proofs in changes:
        related = [state for state in event.field_states.values() if state["locked"] and
            ((state["target"]["kind"] == "label" and state["target"]["name"] == label)
             or (not value and state["target"]["type_label"] == label))]
        state = field_state(event, dict(kind="label", name=label, type_label=None))
        if active_facts(state) and active_facts(state)[-1]["value"] is value:
            continue
        identity = uuid4().hex
        state["facts"].append(dict(id=identity, value=value, value_state="value", stage="分类", scope="当前事项",
            business_time=None, time_kind="unknown", intent="correction", replaces=state["adopted_ids"].copy(),
            reason="有直接依据的标签变化", evidence=[dict(source=available[key].source,
                message_id=available[key].message_id, digest=available[key].digest,
                send_time=available[key].sent_at.isoformat(), conversation_id=available[key].conversation_id,
                quote=fact.get("quote")) for fact in proofs for key in fact.get("evidence_ids", [])],
            source="automatic", evidence_status="current",
            status="suggested", input_ref=None))
        if related:
            issue(event, state, "标签变化影响锁定决定", identity,
                  [identity for related_state in related for identity in related_state["locked_ids"]])
            if not value:
                blocked.add(label)
        else:
            for old in active_facts(state):
                old["status"] = "historical"
            state["adopted_ids"] = [identity]
            state["facts"][-1]["status"] = "adopted"
            for dependent in event.field_states.values():
                if dependent["target"]["type_label"] != label:
                    continue
                dependent["archived"] = not value
                if value:
                    for fact in active_facts(dependent):
                        fact["status"] = "historical"
                    dependent["adopted_ids"] = []
    return blocked


def prepare_automatic_fields(event, candidate, available, settings, *, created=False):
    review_message_versions(event, available)
    defaults = dict(status=settings.default_status, severity=settings.default_severity)
    if candidate.field_updates is not None:
        batch_refs = {}
        for update in candidate.field_updates:
            latest = max(available[ref["message_id"]].sent_at for ref in update["evidence"])
            apply_field_update(event, update, available, late=not created and latest < event.end_time,
                               batch_refs=batch_refs, default_value=defaults.get(update["target"]["name"]),
                               incoming_labels=candidate.label_types())
        return
    for state in list(event.field_states.values()):
        target = state["target"]
        name = target["name"]
        if target["kind"] == "attribute":
            supplied = candidate.typed_attributes.get(target["type_label"], {}).get(name)
            if supplied is None:
                supplied = next((fact for fact in candidate.extensions.get("extraction_v1", {}).get("typed_attributes", [])
                    if fact["name"] == name and fact["type_label"] == target["type_label"]), None)
        elif target["kind"] == "common":
            supplied = candidate.extensions.get("extraction_v1", {}).get(name)
        else:
            continue
        if not isinstance(supplied, dict) or not supplied.get("evidence_ids"):
            continue
        value = supplied.get("value")
        if value is None:
            continue
        existing = active_facts(state)
        shape = existing[-1] if existing else dict(stage="未记录", scope="未记录", business_time=None, time_kind="unknown")
        update = dict(target=target, ref="legacy:" + name, value=value,
            **{key: shape[key] for key in ("stage", "scope", "business_time", "time_kind")},
            intent="supplement", replaces=[], reason="旧合约建议仍受字段采用决定约束",
            evidence=[dict(message_id=key, quote=supplied["quote"]) for key in supplied["evidence_ids"]])
        latest = max(available[ref["message_id"]].sent_at for ref in update["evidence"])
        apply_field_update(event, update, available, late=latest < event.end_time, default_value=defaults.get(name))


def protect_pending_summary(event, candidate):
    pending = [(state, fact) for state in event.field_states.values() for fact in state["facts"]
               if fact["status"] == "suggested"]
    protected = []
    for raw in candidate.changes or []:
        change = deepcopy(raw)
        related = any(
            ((isinstance(fact["value"], str) and fact["value"] in change["text"])
             or any(isinstance(old["value"], str) and old["value"] in (change["replaces"] or "")
                    for old in active_facts(state)))
            and any(ref["message_id"] == f"{proof['source']}:{proof['message_id']}"
                and (proof.get("quote") in ref["quote"] or ref["quote"] in (proof.get("quote") or ""))
                for proof in fact["evidence"] for ref in change["evidence"])
            for state, fact in pending)
        if related:
            change.update(text="字段建议待复核：" + change["text"], replaces=None, kind="progress")
        protected.append(change)
    return protected


def synchronize_summary(event, candidate, changes, available):
    target = dict(kind="common", name="summary", type_label=None)
    state = event.field_states.get(field_key(target))
    if state is None or any(update["target"] == target for update in candidate.field_updates or []):
        return
    current = active_facts(state)
    if current and current[-1]["value"] == event.summary:
        return
    evidence = [deepcopy(ref) for change in changes or [] for ref in change["evidence"]]
    if changes is None:
        evidence = _record_proofs(dict(evidence=[dict(message_id=key, quote=available[key].content)
            for key in candidate.evidence_ids]), available)
    signature = json.dumps([event.event_id, event.summary, evidence], ensure_ascii=False, sort_keys=True)
    identity = "summary-" + hashlib.sha256(signature.encode()).hexdigest()[:24]
    if any(fact["id"] == identity for fact in state["facts"]):
        return
    fact = dict(id=identity, value=event.summary, value_state="value", stage="摘要演进", scope="当前事项",
        business_time=None, time_kind="unknown", intent="progress", replaces=state["adopted_ids"].copy(),
        reason="根据本版有依据的局部变化生成摘要", evidence=evidence, source="automatic",
        evidence_status="current", status="suggested" if state["locked"] else "adopted", input_ref=None)
    state["facts"].append(fact)
    if state["locked"]:
        issue(event, state, "摘要变化涉及人工锁定内容，见本版变化依据", identity)
        return
    for old in current:
        old["status"] = "historical"
    state["adopted_ids"] = [identity]
