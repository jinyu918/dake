from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from uuid import uuid4

from ..analysis.field_changes import active_facts, field_key, field_state, label_active, project_fields
from ..domain.fields import FieldDecisionRequest
from ..domain.models import TZ


class FieldDecisionConflict(ValueError):
    pass


def _validate_value(target, value):
    if target.kind == "label":
        valid = type(value) is bool
    elif target.kind == "common" and target.name == "assignees":
        valid = isinstance(value, list) and bool(value) and all(isinstance(item, str) and item.strip() for item in value)
    else:
        valid = isinstance(value, str) and bool(value.strip())
    if not valid:
        raise ValueError("裁定值与字段类型不符")


def _archive_dependents(event, label, approved):
    dependents = [state for state in event.field_states.values() if state["target"]["type_label"] == label
                  and not state["archived"]]
    if dependents and not approved:
        raise ValueError("撤销标签须明确归档受影响属性及锁定决定")
    for state in dependents:
        state.update(archived=True, locked=False, locked_ids=[])
        for fact in active_facts(state):
            fact["status"] = "archived"
    event.type_evidence.pop(label, None)
    event.removed_types.setdefault(label, []).append(dict(source="manual", reason="人工撤销，详情见裁定记录"))
    removed = event.typed_attributes.pop(label, {})
    for name in removed:
        event.attributes.pop(name, None)


def apply_decision(event, request, now, settings):
    request = FieldDecisionRequest.model_validate(request)
    signature = hashlib.sha256(json.dumps(request.model_dump(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    operation = next((item for item in event.manual_operations if item["operation_id"] == request.operation_id), None)
    if operation:
        if operation["request_digest"] != signature:
            raise FieldDecisionConflict("操作编号已用于不同请求")
        return event
    version = max(event.version, len(event.updated_by))
    if version != request.expected_version:
        raise FieldDecisionConflict("事项已更新，请刷新后核对本次裁定")
    updated = deepcopy(event)
    before = updated.to_dict()
    before.pop("updated_by")
    defaults = dict(status=settings.default_status, severity=settings.default_severity)
    for action in request.actions:
        if action.action in {"lock", "unlock", "confirm"} and action.issue_ids:
            raise ValueError("确认或切换锁定不能附带清除待复核问题")
        if action.target.kind == "attribute" and not label_active(updated, action.target.type_label):
            raise ValueError("须先采用所属标签，再裁定该标签的属性")
        state = field_state(updated, action.target.model_dump(), default_value=defaults.get(action.target.name))
        if action.action == "unlock":
            state.update(locked=False, locked_ids=[])
        elif action.action == "lock":
            if not state["adopted_ids"]:
                raise ValueError("没有采用事实时不能锁定")
            state.update(locked=True, locked_ids=state["adopted_ids"].copy())
        elif action.action == "confirm":
            if not state["adopted_ids"]:
                raise ValueError("没有采用事实时不能确认")
        elif action.action in {"adopt", "resolve"}:
            if action.action == "adopt":
                fact = next((fact for fact in state["facts"] if fact["id"] == action.fact_id), None)
                if fact is None or fact["status"] != "suggested" or fact.get("evidence_status") == "superseded":
                    raise ValueError("建议不存在、已经处理或依据已修订")
                if action.target.kind == "label" and fact["value"] is False:
                    _archive_dependents(updated, action.target.name, action.archive_dependents)
                affected = set(fact.get("replaces", []))
                affected.update(old["id"] for old in active_facts(state) if all(
                    old[name] == fact[name] for name in ("stage", "scope", "business_time", "time_kind")))
                state["adopted_ids"] = [identity for identity in state["adopted_ids"] if identity not in affected]
                state["adopted_ids"].append(fact["id"])
                for old in state["facts"]:
                    if old["id"] in affected:
                        old["status"] = "historical"
                fact["status"] = "adopted"
                state["archived"] = False
                if state["locked"]:
                    state["locked_ids"] = state["adopted_ids"].copy()
            if not action.issue_ids:
                raise ValueError("采用建议或解决问题须指定所解决的问题编号")
        else:
            value_state = "value" if action.action == "set" else {
                "clear": "cleared", "unknown": "unknown", "not_applicable": "not_applicable"}[action.action]
            value = action.value if value_state == "value" else None
            if value_state == "value":
                _validate_value(action.target, value)
            if action.target.kind == "label":
                if value_state != "value":
                    raise ValueError("标签须明确裁定采用或撤销")
                if value is False:
                    _archive_dependents(updated, action.target.name, action.archive_dependents)
            if not action.stage.strip() or not action.scope.strip():
                raise ValueError("人工事实须说明环节和范围")
            adopted = active_facts(state)
            affected = [old for old in adopted if old["id"] == action.fact_id] if action.fact_id else [
                old for old in adopted if all(old[name] == getattr(action, name)
                    for name in ("stage", "scope", "business_time", "time_kind"))]
            if action.fact_id and not affected:
                raise ValueError("人工更正目标不是本字段当前采用事实")
            if not affected and len(adopted) > 1:
                raise ValueError("多事实字段裁定须明确事实目标或匹配环节和范围")
            if not affected:
                affected = adopted
            affected_ids = {old["id"] for old in affected}
            fact = dict(id="manual-" + uuid4().hex, value=value, value_state=value_state, stage=action.stage,
                scope=action.scope, business_time=action.business_time, time_kind=action.time_kind, intent="correction",
                replaces=list(affected_ids), reason=request.reason,
                evidence=[deepcopy(ref) for old in affected for ref in old["evidence"]], source="manual",
                evidence_status="manual", status="adopted", input_ref=None, operation_id=request.operation_id,
                operator=request.operator, decided_at=now)
            for old in affected:
                old["status"] = "historical"
            state["facts"].append(fact)
            state["adopted_ids"] = [identity for identity in state["adopted_ids"] if identity not in affected_ids] + [fact["id"]]
            state["archived"] = False
            if state["locked"]:
                state["locked_ids"] = [identity for identity in state["locked_ids"] if identity not in affected_ids] + [fact["id"]]
        for identity in action.issue_ids:
            problem = next((item for item in updated.field_issues if item["id"] == identity
                and item["field_key"] == field_key(action.target.model_dump()) and item["status"] == "open"), None)
            if problem is None:
                raise ValueError("问题不存在、已解决或不属于本字段")
            problem.update(status="resolved", resolution=dict(operation_id=request.operation_id, operator=request.operator,
                           reason=request.reason, decided_at=now))
        if action.action == "resolve":
            dismissed = {item.get("fact_id") for item in updated.field_issues if item["id"] in action.issue_ids}
            for fact in state["facts"]:
                if fact["id"] in dismissed and fact["status"] == "suggested" and not any(
                        item.get("fact_id") == fact["id"] and item["status"] == "open" for item in updated.field_issues):
                    fact["status"] = "rejected"
        state["decision"] = dict(operation_id=request.operation_id, action=action.action, operator=request.operator,
            reason=request.reason, decided_at=now, adopted_ids=state["adopted_ids"].copy(), locked=state["locked"])
    project_fields(updated, settings.closed_statuses)
    updated.version = version + 1
    operation = dict(operation_id=request.operation_id, request_digest=signature, operator=request.operator,
        reason=request.reason, decided_at=now, version=updated.version, actions=[action.model_dump() for action in request.actions])
    updated.manual_operations.append(operation)
    snapshot = updated.to_dict()
    snapshot.pop("updated_by")
    anchor = next((revision for revision in reversed(event.updated_by) if revision.get("source") != "manual"), None)
    updated.updated_by.append(dict(action="updated", source="manual", recorded_at=now, manual_operation=operation,
        revision_no=len(event.updated_by) + 1, window_start=anchor["window_start"] if anchor else event.created_on.isoformat(),
        window_end=anchor["window_end"] if anchor else event.end_time.isoformat(), message_ids=[], snapshot=snapshot,
        change_kind="manual", change_summary=request.reason, summary_changed=event.summary != updated.summary,
        changed_fields=[key for key in snapshot if snapshot[key] != before[key]], changes=[]))
    return updated


class FieldDecisionService:
    def __init__(self, queries, clock=lambda: datetime.now(TZ)):
        self.queries, self.clock = queries, clock

    @contextmanager
    def connection(self):
        with self.queries.connection() as store:
            yield store

    def submit(self, event_id, request):
        uid, stream = self.queries.scope(request.uid, request.stream)
        with self.connection() as store:
            with store.transaction(uid):
                event = next((item for item in store.list_events(uid, stream) if item.event_id == event_id), None)
                if event is None:
                    return None
                if event.corrected_into:
                    raise FieldDecisionConflict(f"该事项已纠正，请进入保留事项 {event.corrected_into} 核对后裁定")
                updated = apply_decision(event, request, self.clock().isoformat(), self.queries.settings)
                if updated is not event:
                    store.save_event(updated)
        return updated.to_dict()

    def operation(self, event_id, operation_id, uid, stream):
        uid, stream = self.queries.scope(uid, stream)
        with self.connection() as store:
            event = next((item for item in store.list_events(uid, stream) if item.event_id == event_id), None)
        return next((operation for operation in event.manual_operations if operation["operation_id"] == operation_id), None) if event else None
