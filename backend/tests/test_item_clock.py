from datetime import date, time, timedelta

import pytest

from dake.domain.item_clock import MINUTES, ServiceCalendar, advance_clock, evaluate_clock
from dake.domain.models import Event, Window, parse_time
from dake.services.item_priority import check_due_item_priorities
from fakes import MemoryStore


DAY = parse_time("2038-06-01T09:00:00+08:00")
CALENDAR = ServiceCalendar(frozenset(range(5)), time(9), time(17))


def detail(priority="P2", level="L8"):
    return dict(pre_timer_priority=priority, customer_level=level, level_state="fresh")


def start_clock(start=DAY):
    return advance_clock(None, start=start, terminal=False)


@pytest.mark.parametrize("priority", MINUTES)
@pytest.mark.parametrize(("level", "band"), [("L10", 0), ("L8", 1), ("L2", 2)])
@pytest.mark.parametrize("phase", ["handoff", "progress"])
def test_all_deadlines_before_and_exactly_due(priority, level, band, phase):
    clock = dict(start_clock(), phase=phase)
    duration = timedelta(minutes=MINUTES[priority][band][phase == "progress"])
    due = DAY + duration if priority == "P0" else CALENDAR.add(DAY, duration)
    before = evaluate_clock(clock, detail(priority, level), CALENDAR, due - timedelta(microseconds=1))
    assert not before["overdue"]
    at_deadline = evaluate_clock(before, detail(priority, level), CALENDAR, due)
    assert at_deadline["overdue"]
    assert at_deadline["next_check"] is None
    assert "timeout_priority" not in at_deadline
    assert evaluate_clock(at_deadline, detail(priority, level), CALENDAR, due + timedelta(hours=1)) == at_deadline


def test_service_holiday_phase_pause_review_and_reopen():
    start = parse_time("2026-11-20T16:00:00+08:00")
    calendar = ServiceCalendar(frozenset(range(5)), time(9), time(17), frozenset({date(2026, 11, 23)}))
    clock = start_clock(start)
    assert evaluate_clock(clock, detail(), calendar, start)["deadline"].startswith("2026-11-24T10:00")
    clock = advance_clock(clock, start=start, terminal=False, actions=[(start, dict(kind="handoff"))])
    paused = start + timedelta(minutes=30)
    review = parse_time("2026-11-24T12:00:00+08:00")
    clock = advance_clock(clock, start=start, terminal=False,
                          actions=[(paused, dict(kind="external_wait", review_at=review.isoformat()))])
    assert not evaluate_clock(clock, detail(), calendar, review)["overdue"]
    assert evaluate_clock(clock, detail(), calendar, review)["review_due"]
    clock = advance_clock(clock, start=start, terminal=False, actions=[(review, dict(kind="resume"))])
    assert evaluate_clock(clock, detail(), calendar, review)["deadline"].startswith("2026-11-24T15:30")
    stopped = advance_clock(clock, start=start, terminal=True)
    assert stopped["phase"] == "stopped" and stopped["next_check"] is None
    assert advance_clock(stopped, start=review, reopened=True, terminal=False)["started_at"] == review.isoformat()


def test_production_fault_ignores_external_pause_and_uses_original_start_on_promotion():
    start = parse_time("2026-11-20T22:00:00+08:00")
    now = start + timedelta(hours=1)
    clock = advance_clock(start_clock(start), start=start, terminal=False,
                          actions=[(start, dict(kind="production_fault")), (start, dict(kind="handoff")),
                                   (now, dict(kind="external_wait"))])
    assert "paused_at" not in clock
    assert not evaluate_clock(clock, detail(), CALENDAR, now)["overdue"]
    assert evaluate_clock(clock, detail("P1", "L9"), CALENDAR, now)["overdue"]


def test_due_scanner_rechecks_version_and_preserves_lock():
    from dake.domain.manual_controls import set_field_lock

    store = MemoryStore()
    store.profiles["synthetic"] = dict(level="L8", level_state="fresh")
    clock = evaluate_clock(start_clock(), detail(), CALENDAR, DAY)
    event = Event(event_id="synthetic-event", uid="synthetic", stream="stub-v1", type="排障", topic_key="case",
                  summary="合成局部故障", status="跟进中", severity="P2", priority="P2", base_priority="P2",
                  start_time=DAY, end_time=DAY, created_on=DAY, version=1,
                  priority_detail=dict(detail(), rule_version="item-priority-v1", clock=clock),
                  updated_by=[dict(window_start=DAY.isoformat(), window_end=(DAY + timedelta(minutes=15)).isoformat())])
    set_field_lock(event, "priority", True, operator="合成操作员", reason="固定处理档", decided_at=DAY.isoformat())
    with store.transaction(event.uid):
        store.save_event(event)
    store.due_priority_events = lambda *_: [(event.uid, event.stream, event.event_id, 0)]
    due = DAY + timedelta(hours=2)
    assert check_due_item_priorities(store, CALENDAR, now=due) == 0
    store.due_priority_events = lambda *_: [(event.uid, event.stream, event.event_id, store.events[event.event_id].version)]
    assert check_due_item_priorities(store, CALENDAR, now=due) == 1
    updated = store.events[event.event_id]
    assert updated.priority == "P2" and updated.priority_detail["clock"]["overdue"]
    assert check_due_item_priorities(store, CALENDAR, now=due) == 0


def test_restore_legacy_timeout_rank_without_overriding_a_manual_lock():
    from types import SimpleNamespace
    from dake.domain.priority_policy import clear_legacy_timeout_upgrade

    def legacy(locked):
        return SimpleNamespace(priority="P1", severity="P1", manual_controls={"priority": {"locked": locked}},
                               priority_detail={"pre_timer_priority": "P2", "clock": {"timeout_priority": "P1"}})

    for locked, expected in [(False, "P2"), (True, "P1")]:
        row = legacy(locked)
        clear_legacy_timeout_upgrade(row)
        assert row.priority == row.severity == row.priority_detail["priority"] == expected
        assert "timeout_priority" not in row.priority_detail["clock"]
