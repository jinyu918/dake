"""已确认机器人消息的告警证据及轮次规则。"""

from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
import re

from .models import parse_time
from .priority_policy import customer_level_number

ALERT_EVENT_TYPE = "机器人告警"
ALERT_ID = re.compile(r"[A-Za-z0-9._-]{1,96}")
ALERT_SCHEMA = "dake.synthetic-alert-v1"
ROUTES = {"P1": "值班或 TAM", "P2": "客户负责人", "P3": "普通告警聚合", "待核实": "内部兜底"}


def parse_synthetic_alert(message, source):
    try:
        value = json.loads(message.content)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("schema") != ALERT_SCHEMA or value.get("source") != source:
        return None
    for key in ("alert_id", "round_id"):
        if not isinstance(value.get(key), str) or not ALERT_ID.fullmatch(value[key]):
            return None
    if value.get("kind") not in ("trigger", "recovery"):
        return None
    severity = value.get("severity")
    if not isinstance(severity, str) or len(severity) > 32:
        return None
    return dict(source=source, alert_id=value["alert_id"], round_id=value["round_id"], kind=value["kind"],
                original_severity=severity, occurred_at=message.sent_at.isoformat(), message_id=message.key,
                uid=message.uid, confirmed=value.get("affected_uid") == message.uid)


def parse_alert_evidence(message, synthetic_source=None):
    if synthetic_source and (delivery := parse_synthetic_alert(message, synthetic_source)):
        return delivery
    lines = message.content.strip().splitlines()
    if not lines:
        return None
    headline = re.fullmatch(r"(?:【(告警触发|告警恢复)】|(告警触发|告警恢复))", lines[0].strip())
    if headline is None:
        return None
    fields = {}
    for line in lines[1:]:
        field = re.fullmatch(r"(告警ID|轮次ID|告警级别)\s*[:：]\s*(\S+)\s*", line.strip())
        if field is None:
            continue
        key, value = field.groups()
        if key in fields:
            return None
        fields[key] = value
    alert_id, round_id = fields.get("告警ID", ""), fields.get("轮次ID", "")
    severity = fields.get("告警级别", "")
    if not ALERT_ID.fullmatch(alert_id) or not ALERT_ID.fullmatch(round_id) or len(severity) > 32:
        return None
    identity = sha256(json.dumps([message.source, message.sender_id]).encode()).hexdigest()
    return dict(source=f"bot-{identity}", alert_id=alert_id, round_id=round_id,
                kind="trigger" if "触发" in headline[0] else "recovery", original_severity=severity,
                occurred_at=message.sent_at.isoformat(), message_id=message.key, uid=message.uid, confirmed=True)


def append_alert_delivery(current, delivery, profile_reader, reminder_minutes=None):
    result = deepcopy(current or {key: delivery[key] for key in ("source", "alert_id", "round_id")})
    old_priority = result.get("priority")
    old_state = result.get("state")
    occurred = parse_time(delivery["occurred_at"])
    members = result.setdefault("members", [])
    if delivery["uid"] not in members:
        members.append(delivery["uid"])
    affected = result.setdefault("affected_uids", [])
    if delivery["kind"] == "trigger":
        start = result.get("triggered_at")
        if not start or occurred < parse_time(start):
            result["triggered_at"] = delivery["occurred_at"]
            result["original_severity"] = delivery["original_severity"]
        if delivery["confirmed"] and delivery["uid"] not in affected:
            affected.append(delivery["uid"])
    else:
        start = result.get("triggered_at")
        if not start or occurred >= parse_time(start):
            previous = result.get("recovered_at")
            if not previous or occurred > parse_time(previous):
                result["recovered_at"] = delivery["occurred_at"]
        else:
            result["unmatched_recovery"] = True

    triggered, recovered = result.get("triggered_at"), result.get("recovered_at")
    result["state"] = "待匹配恢复" if not triggered else (
        "已恢复" if recovered and parse_time(recovered) >= parse_time(triggered) else "告警中")
    levels = {}
    for uid in affected:
        profile = profile_reader(uid) or {}
        levels[uid] = customer_level_number(profile.get("level"), profile.get("level_state"))
    highest = max(levels.values(), default=-1)
    priority = "P1" if highest >= 9 else "P2" if highest >= 6 else "P3" if highest >= 0 else "待核实"
    result.update(priority=priority, queue=ROUTES[priority], pending=any(levels.get(uid, -1) < 0 for uid in members))
    previews = result.setdefault("reminders", [])
    ranks = {"P1": 0, "P2": 1, "P3": 2, "待核实": 3}
    escalated = old_priority in ranks and ranks[priority] < ranks[old_priority]
    repeated = reminder_minutes and previews and occurred >= parse_time(previews[-1]["at"]) + timedelta(minutes=reminder_minutes)
    if delivery["kind"] == "trigger" and result["state"] == "已恢复":
        historical_first = not previews or (
            previews[0]["kind"] == "历史首次触发" and occurred < parse_time(previews[0]["at"])
        )
        if historical_first:
            previews[:1] = [dict(message_id=delivery["message_id"], at=delivery["occurred_at"], priority=priority,
                                 route=ROUTES[priority], kind="历史首次触发")]
    elif delivery["kind"] == "trigger" and result["state"] == "告警中" and (old_state != "告警中" or escalated or repeated):
        previews.append(dict(message_id=delivery["message_id"], at=delivery["occurred_at"], priority=priority,
                             route=ROUTES[priority], kind="档位提升" if escalated else "重复提醒" if repeated else "首次触发"))
    return result
