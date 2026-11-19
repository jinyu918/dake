"""使用已保存的影响事实和本地目录重评事项。"""

from copy import deepcopy
from datetime import datetime

from ..domain.item_clock import TERMINAL, evaluate_clock
from ..domain.models import TZ, Window, parse_time

from ..domain.manual_controls import is_locked
from ..domain.priority_policy import ITEM_PRIORITY_RULE_VERSION, apply_customer_tier, customer_level_number, response_priority


def refresh_item_priority(event, profile_reader, calendar=None, now=None):
    detail = event.priority_detail or {}
    facts = detail.get("affected_customers", [])
    if event.lifecycle_status in TERMINAL:
        return False
    if not facts and not detail.get("clock") and detail.get("rule_version") != ITEM_PRIORITY_RULE_VERSION:
        return False
    best_uid = event.uid
    best_level, best_state, best_rank = None, "unknown", -1
    uids = dict.fromkeys([event.uid, *(fact["uid"] for fact in facts if fact["status"] == "confirmed")])
    for uid in uids:
        profile = profile_reader(uid) or {}
        level, state = profile.get("level"), profile.get("level_state")
        rank = customer_level_number(level, state)
        if rank > best_rank:
            best_uid, best_level, best_state, best_rank = uid, level, state, rank
    tier = apply_customer_tier(event.base_priority, best_level, best_state)
    customer_priority = tier["priority"]
    suggested = response_priority(customer_priority, detail)
    updated = dict(detail)
    updated.update(tier)
    updated["pre_timer_priority"] = suggested
    if detail.get("clock"):
        clock = evaluate_clock(detail["clock"], updated, calendar, now or datetime.now(TZ))
        updated["clock"] = clock
    adopted = event.priority if is_locked(event, "priority") else suggested
    updated.update(rule_version=ITEM_PRIORITY_RULE_VERSION, effective_customer_uid=best_uid if best_rank >= 0 else None,
                   affected_customers_pending=any(fact["status"] == "pending" for fact in facts),
                   customer_priority=customer_priority, pre_timer_priority=suggested, priority=adopted)
    if updated == detail and adopted == event.priority:
        return False
    if adopted != event.priority or event.severity in {"P0", "P1", "P2", "P3", "待评估"}:
        event.severity = adopted
    event.priority = adopted
    event.priority_detail = updated
    return True


def save_priority_refresh(store, event, window, changed_in_window=False, *, source=None, attempt_id=None):
    if changed_in_window:
        revision = event.updated_by[-1]
        if "changed_fields" in revision:
            revision["changed_fields"] = list(dict.fromkeys([*revision["changed_fields"], "priority", "priority_detail"]))
    else:
        event.version = max(event.version, len(event.updated_by)) + 1
        revision = dict(action="updated", window_start=window.start.isoformat(), window_end=window.end.isoformat(),
                        message_ids=[], revision_no=len(event.updated_by) + 1, change_kind="classification",
                        change_summary="按本地档案重评优先级", changed_fields=["priority", "priority_detail"],
                        recorded_at=datetime.now(TZ).isoformat())
        if source is not None:
            revision["source"] = source
        if attempt_id is not None:
            revision["attempt_id"] = attempt_id
        event.updated_by.append(revision)
    snapshot = event.to_dict()
    snapshot.pop("updated_by")
    revision["snapshot"] = deepcopy(snapshot)
    store.save_event(event)


def refresh_directory_priorities(store, changed_uids):
    count = 0
    for owner, stream in store.item_priority_scopes(changed_uids):
        with store.transaction(owner):
            for event in store.list_events(owner, stream):
                if event.lifecycle_status in {"已解决", "已取消"} or not event.updated_by:
                    continue
                if not refresh_item_priority(event, store.customer_profile):
                    continue
                previous = event.updated_by[-1]
                window = Window(parse_time(previous["window_start"]), parse_time(previous["window_end"]))
                save_priority_refresh(store, event, window, source="directory")
                count += 1
    return count


def check_due_item_priorities(store, calendar, *, now, limit=100):
    changed = 0
    for uid, stream, event_id, version in store.due_priority_events(now, limit):
        with store.transaction(uid):
            event = next((event for event in store.list_events(uid, stream) if event.event_id == event_id), None)
            if event is None or event.version != version or event.lifecycle_status in TERMINAL or not event.updated_by:
                continue
            if refresh_item_priority(event, store.customer_profile, calendar, now):
                prior = event.updated_by[-1]
                window = Window(parse_time(prior["window_start"]), parse_time(prior["window_end"]))
                save_priority_refresh(store, event, window, source="clock")
                changed += 1
    return changed
