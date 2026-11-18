import json
from datetime import timedelta

from dake.domain.alert_policy import ALERT_SCHEMA, append_alert_delivery, parse_synthetic_alert
from dake.domain.models import Message, parse_time


START = parse_time("2038-06-01T09:00:00+08:00")


def delivery(mid, uid, minute, *, kind="trigger", round_id="shared", affected=True):
    payload = dict(schema=ALERT_SCHEMA, source="local-fixture", alert_id="storage-rule", round_id=round_id,
                   kind=kind, severity="warning", affected_uid=uid if affected else None)
    msg = Message(uid, mid, START + timedelta(minutes=minute), json.dumps(payload), sender_id="fixture-bot")
    return parse_synthetic_alert(msg, "local-fixture")


def test_round_isolation_levels_late_recovery_and_recurrence():
    profiles = {"low": dict(level="L4", level_state="fresh"), "high": dict(level="L10", level_state="fresh")}
    low = append_alert_delivery(None, delivery("low1", "low", 0, round_id="low-round"), profiles.get, 15)
    high = append_alert_delivery(None, delivery("high1", "high", 0, round_id="high-round"), profiles.get, 15)
    assert (low["priority"], high["priority"]) == ("P3", "P1")
    mixed = append_alert_delivery(None, delivery("mix1", "low", 0), profiles.get, 15)
    mixed = append_alert_delivery(mixed, delivery("mix2", "high", 1), profiles.get, 15)
    mixed = append_alert_delivery(mixed, delivery("mix3", "high", 2), profiles.get, 15)
    assert mixed["priority"] == "P1"
    assert [reminder["priority"] for reminder in mixed["reminders"]] == ["P3", "P1"]
    mixed = append_alert_delivery(mixed, delivery("mix4", "high", 16), profiles.get, 15)
    assert mixed["reminders"][-1]["kind"] == "重复提醒"
    recovered = append_alert_delivery(mixed, delivery("mix5", "high", 17, kind="recovery"), profiles.get, 15)
    assert recovered["state"] == "已恢复" and high["state"] == "告警中"
    recurrence = append_alert_delivery(None, delivery("mix6", "high", 18, round_id="new-round"), profiles.get)
    assert recurrence["state"] == "告警中"
    late = append_alert_delivery(None, delivery("late1", "unknown", 30, kind="recovery"), profiles.get)
    assert late["state"] == "待匹配恢复" and late["queue"] == "内部兜底"
    late = append_alert_delivery(late, delivery("late2", "unknown", 29, affected=False), profiles.get)
    assert late["state"] == "已恢复" and late["pending"]
