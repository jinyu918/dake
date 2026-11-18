"""事项计时与显式服务日历。"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path

from .models import TZ, parse_time
from .priority_policy import customer_level_number

TERMINAL = {"已解决", "已取消"}
MINUTES = {
    "P0": ((0, 30), (0, 30), (0, 30)),
    "P1": ((30, 60), (45, 120), (60, 120)),
    "P2": ((60, 120), (120, 240), (240, 480)),
    "P3": ((120, 240), (240, 480), (480, 960)),
    "待评估": ((60, 60), (120, 120), (240, 240)),
}
TIMEOUT_PRIORITY = {"P2": "P1", "P3": "P2"}


@dataclass(frozen=True)
class ServiceCalendar:
    weekdays: frozenset[int]
    opens: time
    closes: time
    holidays: frozenset[date] = frozenset()

    def __post_init__(self):
        if not self.weekdays or not self.weekdays <= set(range(7)) or self.opens >= self.closes:
            raise ValueError("服务日历必须配置有效的周服务日与营业时段")
        if self.opens.tzinfo or self.closes.tzinfo:
            raise ValueError("日历时段使用上海本地时间")

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, value):
        return cls(frozenset(value["weekdays"]), time.fromisoformat(value["opens"]),
                   time.fromisoformat(value["closes"]), frozenset(date.fromisoformat(day) for day in value.get("holidays", [])))

    def to_dict(self):
        return dict(weekdays=sorted(self.weekdays), opens=self.opens.isoformat(), closes=self.closes.isoformat(),
                    holidays=sorted(day.isoformat() for day in self.holidays))

    def interval(self, day):
        if day.weekday() in self.weekdays and day not in self.holidays:
            return datetime.combine(day, self.opens, TZ), datetime.combine(day, self.closes, TZ)
        return None

    def add(self, start, remaining):
        cursor = start.astimezone(TZ)
        while remaining > timedelta():
            if span := self.interval(cursor.date()):
                active_from = max(cursor, span[0])
                if active_from + remaining <= span[1]:
                    return active_from + remaining
                remaining -= max(timedelta(), span[1] - active_from)
            cursor = datetime.combine(cursor.date() + timedelta(days=1), time.min, TZ)
        return cursor

    def elapsed(self, start, end):
        day, final = start.astimezone(TZ).date(), end.astimezone(TZ).date()
        elapsed = timedelta()
        while day <= final:
            if span := self.interval(day):
                elapsed += max(timedelta(), min(end, span[1]) - max(start, span[0]))
            day += timedelta(days=1)
        return elapsed


def advance_clock(current, *, start, terminal, reopened=False, actions=()):
    if terminal:
        return dict(current, phase="stopped", deadline=None, next_check=None) if current else None
    clock = dict(current or {})
    if not clock or reopened:
        clock = dict(phase="handoff", started_at=start.isoformat(), last_fact_at=start.isoformat())
    for at, action in actions:
        if at < parse_time(clock["last_fact_at"]):
            continue
        kind = action["kind"]
        if kind == "production_fault":
            clock["production_fault"] = True
            if paused := clock.pop("paused_at", None):
                clock["pauses"] = [*clock.get("pauses", []), [paused, at.isoformat()]]
                clock.pop("review_at", None)
        elif (kind == "handoff" and clock["phase"] == "handoff") or (kind == "progress" and clock["phase"] == "progress"):
            calendar = clock.get("calendar")
            clock = dict(phase="progress", started_at=at.isoformat(), production_fault=bool(clock.get("production_fault")))
            if calendar:
                clock["calendar"] = calendar
        elif kind == "external_wait" and clock["phase"] == "progress" and not clock.get("production_fault"):
            clock.setdefault("paused_at", at.isoformat())
            clock["review_at"] = parse_time(action["review_at"]).isoformat()
        elif kind == "resume" and (paused := clock.pop("paused_at", None)):
            clock["pauses"] = [*clock.get("pauses", []), [paused, at.isoformat()]]
            clock.pop("review_at", None)
        else:
            continue
        clock["last_fact_at"] = at.isoformat()
        clock["last_evidence"] = action.get("evidence_refs", [])
    return clock


def evaluate_clock(clock, detail, calendar, now):
    if not clock or clock["phase"] == "stopped":
        return clock
    if calendar is None and clock.get("calendar"):
        calendar = ServiceCalendar.from_dict(clock["calendar"])
    if calendar is None:
        return clock
    priority = detail["pre_timer_priority"]
    level = customer_level_number(detail.get("customer_level"), detail.get("level_state"))
    band = 0 if level >= 9 else 1 if level >= 6 else 2
    minutes = MINUTES[priority][band][clock["phase"] == "progress"]
    start = parse_time(clock["started_at"])
    natural = priority == "P0" or priority == "P1" and clock.get("production_fault")
    add = (lambda at, duration: at + duration) if natural else calendar.add
    elapsed = (lambda begin, end: end - begin) if natural else calendar.elapsed
    remaining = timedelta(minutes=minutes)
    cursor = start
    for begin, end in clock.get("pauses", []):
        remaining = max(timedelta(), remaining - elapsed(cursor, parse_time(begin)))
        cursor = parse_time(end)
    deadline = add(cursor, remaining)
    paused_at = parse_time(clock["paused_at"]) if clock.get("paused_at") else None
    overdue = bool(clock.get("overdue") or deadline <= (paused_at or now))
    review = parse_time(clock["review_at"]) if paused_at else None
    review_due = bool(review and now >= review)
    next_check = (review if not review_due else None) if paused_at else (deadline if not overdue else None)
    return dict(clock, calendar=calendar.to_dict(), pre_timer_priority=priority, deadline=deadline.isoformat(), overdue=overdue,
                review_due=review_due, next_check=next_check.isoformat() if next_check else None,
                timeout_priority=TIMEOUT_PRIORITY.get(priority, priority) if overdue else None)
