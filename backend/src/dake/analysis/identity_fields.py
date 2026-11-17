from copy import deepcopy
import hashlib

from .field_changes import active_facts, field_key, field_state, issue, project_fields


def mapped_id(retained_id, origin_event_id, identity):
    return "import-" + hashlib.sha256(f"{retained_id}\0{origin_event_id}\0{identity}".encode()).hexdigest()[:32]


def conflicts(left, right, target):
    same_slot = target["kind"] != "attribute" or all(
        left[name] == right[name] for name in ("stage", "scope", "business_time", "time_kind"))
    return same_slot and (left["value"], left["value_state"]) != (right["value"], right["value_state"])


def prepare_states(event, settings):
    targets = [dict(kind="common", name=name, type_label=None)
               for name in ("summary", "status", "severity", "assignees", "other_reason")]
    targets += [dict(kind="label", name=label, type_label=None) for label in set(event.types) | set(event.removed_types)]
    targets += [dict(kind="attribute", name=name, type_label=label)
                for label, attributes in event.typed_attributes.items() for name in attributes]
    for target in targets:
        field_state(event, target, default_value={"status": settings.default_status,
            "severity": settings.default_severity}.get(target["name"]))
    for state in event.field_states.values():
        for fact in state["facts"]:
            fact.setdefault("origin_event_id", event.event_id)
            fact.setdefault("origin_fact_id", fact["id"])


def import_fields(retained, duplicate, request, now, settings):
    prepare_states(retained, settings)
    prepare_states(duplicate, settings)
    mapping = {fact["id"]: mapped_id(retained.event_id, fact["origin_event_id"], fact["origin_fact_id"])
               for state in duplicate.field_states.values() for fact in state["facts"]}
    resolutions = {field_key(value.target.model_dump()): value for value in request.resolutions}
    if len(resolutions) != len(request.resolutions):
        raise ValueError("同一字段不得重复处置")
    source_locks = {}
    for key, old in duplicate.field_states.items():
        state = retained.field_states.get(key)
        if state is None:
            state = dict(target=deepcopy(old["target"]), facts=[], adopted_ids=[], locked_ids=[],
                         locked=False, decision=None, archived=old["archived"])
            retained.field_states[key] = state
        source_locks[key] = [mapping[identity] for identity in old["locked_ids"]]
        if old.get("decision") or old["locked_ids"]:
            state.setdefault("inherited_decisions", []).append(dict(origin_event_id=duplicate.event_id,
                decision=deepcopy(old.get("decision")), adopted_ids=[mapping[value] for value in old["adopted_ids"]],
                locked_ids=source_locks[key], archived=old["archived"]))
        for original in old["facts"]:
            fact = deepcopy(original)
            fact["id"] = mapping[original["id"]]
            fact["replaces"] = [mapping.get(value, value) for value in original["replaces"]]
            state["facts"].append(fact)
            if original["id"] not in old["adopted_ids"]:
                continue
            if state["archived"] or old["archived"]:
                fact["status"] = "archived"
                continue
            incompatible = [value for value in active_facts(state) if conflicts(value, fact, state["target"])]
            if incompatible or fact.get("evidence_status") == "superseded":
                fact["status"] = "suggested"
                issue(retained, state, "同轮纠正带入的事实待核对", fact["id"], [value["id"] for value in incompatible])
            else:
                state["adopted_ids"].append(fact["id"])
                state["locked_ids"].extend([fact["id"]] if original["id"] in old["locked_ids"] else [])
                state["locked"] = bool(state["locked_ids"])
    for problem in duplicate.field_issues:
        copied = deepcopy(problem)
        copied.update(id=mapped_id(retained.event_id, duplicate.event_id, problem["id"]),
                      origin_event_id=duplicate.event_id, origin_issue_id=problem["id"])
        copied["fact_id"] = mapping.get(problem["fact_id"], problem["fact_id"])
        copied["affected_ids"] = [mapping.get(value, value) for value in problem["affected_ids"]]
        retained.field_issues.append(copied)
    for key, state in retained.field_states.items():
        resolution = resolutions.pop(key, None)
        all_locks = set(state["locked_ids"]) | set(source_locks.get(key, []))
        by_id = {fact["id"]: fact for fact in state["facts"]}
        effective = [by_id[identity] for identity in all_locks]
        clash = any(conflicts(left, right, state["target"]) for left in effective for right in active_facts(state))
        recovery_lock = bool(all_locks and state["target"]["kind"] == "attribute"
                             and state["target"]["type_label"] == "排障" and state["target"]["name"] == "恢复事实")
        blocked_lock = bool(all_locks - set(state["adopted_ids"])) or clash or recovery_lock
        if blocked_lock and resolution is None:
            raise ValueError("涉及锁定冲突或恢复结论，须逐字段明确处置及核对锁定事实")
        if resolution is None:
            continue
        origins = {(fact["origin_event_id"], fact["origin_fact_id"]): fact for fact in state["facts"]}
        selected = []
        for origin in resolution.adopt:
            fact = origins.get((origin.event_id, origin.fact_id))
            if fact is None or fact["status"] in {"rejected", "corrected", "historical", "archived"} or fact["evidence_status"] == "superseded":
                raise ValueError("处置只能采用该字段仍有效的当前事实或待复核建议")
            if fact not in selected:
                selected.append(fact)
        if any(conflicts(left, right, state["target"]) for left in selected for right in selected):
            raise ValueError("不能同时采用同一事实的互斥值")
        expected = {(by_id[value]["origin_event_id"], by_id[value]["origin_fact_id"]) for value in all_locks}
        acknowledged = {(value.event_id, value.fact_id) for value in resolution.acknowledged_locks}
        if expected != acknowledged:
            raise ValueError("须逐项核对本字段两侧全部受影响锁定，不能批量解锁")
        if state["archived"]:
            raise ValueError("已归档字段不能通过身份纠正重新采用")
        selected_ids = [value["id"] for value in selected]
        for fact in active_facts(state):
            if fact["id"] not in selected_ids:
                fact["status"] = "historical"
        for fact in selected:
            fact["status"] = "adopted"
        state.update(adopted_ids=selected_ids, locked=resolution.locked,
                     locked_ids=selected_ids.copy() if resolution.locked else [], decision=dict(
                         operation_id=request.operation_id, action="same_round_correction", operator=request.operator,
                         reason=resolution.reason, decided_at=now, adopted_ids=selected_ids, locked=resolution.locked))
        for problem in retained.field_issues:
            if problem["field_key"] == key and problem["status"] == "open" and problem["reason"] == "同轮纠正带入的事实待核对":
                problem.update(status="resolved", resolution=deepcopy(state["decision"]))
        for fact in state["facts"]:
            if fact["status"] == "suggested" and fact["id"] not in selected_ids and any(
                    conflicts(fact, value, state["target"]) for value in selected):
                fact["status"] = "rejected"
    if resolutions:
        raise ValueError("处置字段不存在")
    for state in retained.field_states.values():
        label = state["target"].get("type_label")
        if label and any(
                value["target"]["kind"] == "label" and value["target"]["name"] == label
                and any(fact["value"] is False for fact in active_facts(value))
                for value in retained.field_states.values()):
            if state["locked_ids"]:
                raise ValueError("撤销标签涉及锁定属性，须先用字段裁定核对")
            state["archived"] = True
    project_fields(retained, settings.closed_statuses)
