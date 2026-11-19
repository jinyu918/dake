"""核心管理字段（分类、状态、优先级、跟进人）的原子人工纠偏与锁定服务。"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json

from ..analysis.providers.xhub import configured_stream
from .item_priority import refresh_item_priority
from ..domain.alert_policy import ALERT_EVENT_TYPE
from ..domain.item_clock import TERMINAL, advance_clock
from ..domain.manual_controls import (
    ManualDecisionRequest,
    ManualFieldAction,
    compose_review_flag,
    resolve_review_items,
    set_field_lock,
    validate_manual_value,
)
from ..domain.models import EVENT_LABELS, PENDING_TYPE, TZ, parse_time
from ..domain.priority_policy import PRIORITIES, clear_legacy_timeout_upgrade
from ..domain.status_policy import validate_status_shape
from ..ingestion.client import validate_uid
from ..repositories.mysql import MySQLStore
from .enrich import enrich_event, enrich_events
from .sender_identity import load_sender_roles
from .sender_kinds import load_sender_kinds


class ManualDecisionNotFound(LookupError):
    """目标事件或人工操作编号在指定客户及分析流下不存在。"""


class ManualDecisionConflict(RuntimeError):
    """事件乐观锁版本不匹配，或同一操作编号被不同请求内容复用。"""


def _request_digest(request: ManualDecisionRequest) -> str:
    """对业务裁定内容计算确定性摘要，用于相同 operation_id 的幂等核验。"""
    payload = dict(
        uid=request.uid,
        stream=request.stream,
        expected_version=request.expected_version,
        operator=request.operator.strip(),
        reason=request.reason.strip(),
        actions=[item.model_dump() for item in request.actions],
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _current_field_value(event, field_name: str):
    if field_name == "types":
        return list(event.types)
    if field_name == "status":
        return dict(
            status=event.lifecycle_status,
            waiting_reason=event.waiting_reason,
            resolution=event.resolution,
        )
    if field_name == "priority":
        return event.priority
    if field_name == "assignees":
        return list(event.assignees)
    raise ValueError(f"不支持的受控字段：{field_name}")


class ManualDecisionService:
    """在客户级数据库事务内执行核心字段覆盖、加解锁及冲突消解。"""

    def __init__(self, settings, store_factory=MySQLStore):
        self.settings = settings
        self.store_factory = store_factory

    @contextmanager
    def connection(self):
        store = self.store_factory(**self.settings.store_kwargs())
        try:
            yield store
        finally:
            store.close()

    def scope(self, uid: str | None, stream: str | None) -> tuple[str, str]:
        """校验客户标识与当前分析流，防止跨客户或跨流越权裁定。"""
        uid = uid or self.settings.uid
        validate_uid(uid)
        expected = configured_stream(self.settings)
        if stream is not None and stream != expected:
            raise ValueError("客户与分析流不匹配")
        return uid, expected

    def _enrich(self, store, uid: str, item: dict) -> dict:
        roles = load_sender_roles(self.settings.data_dir)
        kinds = load_sender_kinds(self.settings.data_dir)
        if hasattr(store, "_execute"):
            return enrich_events(store, uid, [item], roles, kinds)[0]
        messages = {
            (m.source, m.message_id): m.to_dict()
            for m in getattr(store, "messages", {}).values()
            if m.uid == uid
        }
        return enrich_event(item, messages, roles, kinds)

    def operation(self, event_id: str, operation_id: str, uid: str | None = None, stream: str | None = None) -> dict:
        """按操作编号查询已提交的人工裁定收据及当前事件视图，用于提交结果不明时核实。"""
        uid, stream = self.scope(uid, stream)
        with self.connection() as store:
            event = next((e for e in store.list_events(uid, stream) if e.event_id == event_id), None)
            if event is None:
                raise ManualDecisionNotFound("该客户下未找到事件")
            op = next((item for item in event.manual_operations if item.get("operation_id") == operation_id), None)
            if op is None:
                raise ManualDecisionNotFound("未找到对应人工裁定记录")
            enriched = self._enrich(store, uid, event.to_dict())
        return dict(event=enriched, operation=deepcopy(op), idempotent_replay=False)

    def submit(self, event_id: str, request: ManualDecisionRequest) -> dict:
        """原子应用人工裁定、记录审计修订并推进事件版本号。"""
        uid, stream = self.scope(request.uid, request.stream)
        digest = _request_digest(request)
        operator = request.operator.strip()
        reason = request.reason.strip()
        decided_at = datetime.now(TZ).isoformat()

        with self.connection() as store:
            with store.transaction(uid):
                event = next((e for e in store.list_events(uid, stream) if e.event_id == event_id), None)
                if event is None:
                    raise ManualDecisionNotFound("该客户下未找到事件")

                existing_op = next(
                    (item for item in event.manual_operations if item.get("operation_id") == request.operation_id),
                    None,
                )
                if existing_op is not None:
                    if existing_op.get("request_digest") != digest:
                        raise ManualDecisionConflict("同一操作编号已用于不同裁定内容")
                    enriched = self._enrich(store, uid, event.to_dict())
                    return dict(event=enriched, operation=deepcopy(existing_op), idempotent_replay=True)

                if event.version != request.expected_version:
                    raise ManualDecisionConflict(f"事件版本已变化（当前版本 {event.version}），请刷新后重试")

                preserved_external_flags = []
                if event.review_flag and not event.review_items:
                    preserved_external_flags.append(event.review_flag)

                clear_legacy_timeout_upgrade(event)
                previous = event.to_dict()
                for act in request.actions:
                    if event.type == ALERT_EVENT_TYPE and act.field == "types":
                        raise ValueError("机器人告警类型由告警证据确定")
                    self._apply_action(event, act, operator=operator, reason=reason, decided_at=decided_at)
                    if act.field == "priority" and act.action == "unlock":
                        refresh_item_priority(event, store.customer_profile)

                reopened = previous["lifecycle_status"] in TERMINAL and event.lifecycle_status not in TERMINAL
                if reopened:
                    event.priority_detail = dict(event.priority_detail or {}, handoff_wait={"started_at": decided_at})
                if clock := (event.priority_detail or {}).get("clock"):
                    if reopened or event.lifecycle_status in TERMINAL:
                        updated_clock = advance_clock(clock, start=parse_time(decided_at), reopened=reopened,
                                                      terminal=event.lifecycle_status in TERMINAL)
                        event.priority_detail["clock"] = dict(updated_clock, calendar=clock.get("calendar"))
                        refresh_item_priority(event, store.customer_profile)

                if event.review_items or preserved_external_flags:
                    event.review_flag = compose_review_flag(event.review_items, extra_flags=preserved_external_flags)
                else:
                    event.review_flag = None

                event.version = max(event.version, len(event.updated_by)) + 1
                op_record = dict(
                    operation_id=request.operation_id,
                    request_digest=digest,
                    operator=operator,
                    reason=reason,
                    decided_at=decided_at,
                    version=event.version,
                    actions=[item.model_dump() for item in request.actions],
                )
                event.manual_operations.append(op_record)

                snapshot = event.to_dict()
                snapshot.pop("updated_by", None)
                tracked_keys = (
                    "type",
                    "types",
                    "classification_status",
                    "typed_attributes",
                    "attributes",
                    "other_reason",
                    "status",
                    "lifecycle_status",
                    "waiting_reason",
                    "resolution",
                    "severity",
                    "priority",
                    "base_priority",
                    "priority_detail",
                    "assignees",
                    "review_flag",
                    "manual_controls",
                )
                changed_fields = [key for key in tracked_keys if previous.get(key) != snapshot.get(key)]

                # 复用事件最近一次自动窗口边界，既满足 HistoryRetriever 单调性又不污染历史周期快照。
                if event.updated_by:
                    win_start = event.updated_by[-1]["window_start"]
                    win_end = event.updated_by[-1]["window_end"]
                else:
                    win_start = event.created_on.isoformat()
                    win_end = (event.end_time + timedelta(microseconds=1)).isoformat()

                revision = dict(
                    action="updated",
                    source="manual",
                    window_start=win_start,
                    window_end=win_end,
                    message_ids=[],
                    snapshot=snapshot,
                    revision_no=len(event.updated_by) + 1,
                    recorded_at=decided_at,
                    change_kind="manual",
                    change_summary=f"人工裁定（{operator}）：{reason}",
                    summary_changed=False,
                    changed_fields=changed_fields,
                    changes=[],
                    manual_operation=deepcopy(op_record),
                )
                event.updated_by.append(revision)
                store.save_event(event)
                enriched = self._enrich(store, uid, event.to_dict())
                return dict(event=enriched, operation=deepcopy(op_record), idempotent_replay=False)

    def _apply_action(
        self,
        event,
        act: ManualFieldAction,
        *,
        operator: str,
        reason: str,
        decided_at: str,
    ) -> None:
        if act.action == "set":
            validate_manual_value(act, EVENT_LABELS)
            locked = True if act.locked is None else bool(act.locked)
            adopted = self._set_value(event, act, operator=operator, reason=reason, decided_at=decided_at)
            set_field_lock(
                event,
                act.field,
                locked,
                operator=operator,
                reason=reason,
                decided_at=decided_at,
                adopted_value=adopted,
            )
            resolve_review_items(event, field=act.field)
            return

        if act.action == "lock":
            adopted = _current_field_value(event, act.field)
            set_field_lock(
                event,
                act.field,
                True,
                operator=operator,
                reason=reason,
                decided_at=decided_at,
                adopted_value=adopted,
            )
            resolve_review_items(event, field=act.field, kinds=("locked_field_conflict",))
            return

        if act.action == "unlock":
            set_field_lock(
                event,
                act.field,
                False,
                operator=operator,
                reason=reason,
                decided_at=decided_at,
            )
            resolve_review_items(event, field=act.field, kinds=("locked_field_conflict",))
            return

        control = (event.manual_controls or {}).get(act.field) or {}
        conflict = control.get("pending_conflict")
        if not conflict:
            raise ValueError(f"字段 {act.field} 当前没有待处理的锁定冲突")

        if act.action == "dismiss_conflict":
            set_field_lock(
                event,
                act.field,
                True,
                operator=operator,
                reason=reason,
                decided_at=decided_at,
                adopted_value=_current_field_value(event, act.field),
            )
            resolve_review_items(event, field=act.field, kinds=("locked_field_conflict",))
            return

        if act.action == "adopt_conflict":
            proposed = conflict.get("proposed_value")
            adopted = self._apply_proposed_conflict(
                event, act.field, proposed, operator=operator, reason=reason, decided_at=decided_at
            )
            locked = True if act.locked is None else bool(act.locked)
            set_field_lock(
                event,
                act.field,
                locked,
                operator=operator,
                reason=reason,
                decided_at=decided_at,
                adopted_value=adopted,
            )
            resolve_review_items(event, field=act.field)

    def _set_value(
        self,
        event,
        act: ManualFieldAction,
        *,
        operator: str,
        reason: str,
        decided_at: str,
    ):
        if act.field == "types":
            new_types = list(act.types or [])
            removed_labels = set(event.types) - set(new_types)
            removed_attr_names = set()
            for label in removed_labels:
                event.type_evidence.pop(label, None)
                removed_attr_names.update(event.typed_attributes.pop(label, {}))
            event.types = new_types
            for name in removed_attr_names:
                if not any(name in attrs for attrs in event.typed_attributes.values()):
                    event.attributes.pop(name, None)
            event.type = new_types[0] if new_types else PENDING_TYPE
            event.classification_status = "已分类" if new_types else "待分类"
            if "其他" not in new_types:
                event.other_reason = None
            event.classification_evidence_status = "已记录" if event.type_evidence else "未记录"
            if new_types:
                resolve_review_items(event, field="status", kinds=("pending_classification_completion",))
            return list(new_types)

        if act.field == "status":
            event.lifecycle_status = act.status
            event.waiting_reason = act.waiting_reason
            event.resolution = act.resolution
            event.status = act.status
            event.status_detail = dict(
                source="manual",
                reason=reason,
                operator=operator,
                decided_at=decided_at,
            )
            if event.status in self.settings.closed_statuses or event.lifecycle_status in self.settings.closed_statuses:
                if event.closed_at is None:
                    event.closed_at = event.end_time
            else:
                event.closed_at = None
            return dict(status=act.status, waiting_reason=act.waiting_reason, resolution=act.resolution)

        if act.field == "priority":
            event.priority = act.priority
            event.severity = act.priority
            detail = deepcopy(event.priority_detail or {})
            detail.update(
                priority=act.priority,
                source="manual",
                manual_reason=reason,
                operator=operator,
                decided_at=decided_at,
            )
            event.priority_detail = detail
            return act.priority

        if act.field == "assignees":
            cleaned = list(dict.fromkeys(item.strip() for item in (act.assignees or []) if item.strip()))
            event.assignees = cleaned
            return list(cleaned)

        raise ValueError(f"不支持的受控字段：{act.field}")

    def _apply_proposed_conflict(
        self,
        event,
        field_name: str,
        proposed,
        *,
        operator: str,
        reason: str,
        decided_at: str,
    ):
        if field_name == "types":
            if not isinstance(proposed, list) or any(t not in EVENT_LABELS for t in proposed):
                raise ValueError("冲突候选分类标签无效")
            synth = ManualFieldAction(field="types", action="set", types=list(proposed))
            return self._set_value(event, synth, operator=operator, reason=reason, decided_at=decided_at)

        if field_name == "status":
            if isinstance(proposed, dict):
                st = proposed.get("status")
                wr = proposed.get("waiting_reason")
                res = proposed.get("resolution")
            else:
                st, wr, res = str(proposed), None, None
            validate_status_shape(st, wr, res)
            synth = ManualFieldAction(field="status", action="set", status=st, waiting_reason=wr, resolution=res)
            return self._set_value(event, synth, operator=operator, reason=reason, decided_at=decided_at)

        if field_name == "priority":
            prio = proposed.get("priority") if isinstance(proposed, dict) else proposed
            base_prio = proposed.get("base_priority") if isinstance(proposed, dict) else None
            if prio not in PRIORITIES:
                raise ValueError("冲突候选优先级无效")
            if base_prio in PRIORITIES:
                event.base_priority = base_prio
            synth = ManualFieldAction(field="priority", action="set", priority=prio)
            return self._set_value(event, synth, operator=operator, reason=reason, decided_at=decided_at)

        if field_name == "assignees":
            if not isinstance(proposed, list):
                raise ValueError("冲突候选跟进人格式无效")
            synth = ManualFieldAction(field="assignees", action="set", assignees=[str(x) for x in proposed])
            return self._set_value(event, synth, operator=operator, reason=reason, decided_at=decided_at)

        raise ValueError(f"不支持的受控字段：{field_name}")
