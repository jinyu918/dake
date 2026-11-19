"""在现有客户事务内保存机器人告警事件。"""

from hashlib import sha256
import json

from ..domain.alert_policy import ALERT_EVENT_TYPE, append_alert_delivery, parse_synthetic_alert
from ..domain.manual_controls import compose_review_flag, is_locked, upsert_review_item
from ..domain.models import Event, parse_time
from .sender_kinds import sender_kind


def append_alert_event(current, message, delivery, stream, window, profile_reader, reminder_minutes, attempt_id=None):
    evidence = dict(source=message.source, message_id=message.message_id, digest=message.digest,
                    send_time=message.sent_at.isoformat(), excerpt=message.content[:160],
                    conversation_id=message.conversation_id)
    if current is not None:
        previous = [item for item in current.evidence if (item["source"], item["message_id"])
                    == (message.source, message.message_id)]
        if previous:
            if any(item.get("digest") == message.digest for item in previous):
                return None
            event = Event.from_dict(current.to_dict())
            upsert_review_item(event, kind="alert_source_revision", field=message.key,
                               summary="机器人告警原消息已更正，轮次状态待核对",
                               evidence_refs=[previous[-1], evidence])
            event.review_flag = compose_review_flag(event.review_items)
            return _record_alert_evidence(event, evidence, window, attempt_id)
    alert = append_alert_delivery((current.extensions.get("robot_alert") if current else None), delivery,
                                  profile_reader, reminder_minutes)
    active = alert["state"] == "告警中"
    recovered = alert["state"] == "已恢复"
    lifecycle = "跟进中" if active else "已解决" if recovered else "状态未明"
    priority = "待评估" if alert["priority"] == "待核实" else alert["priority"]
    if current is None:
        identity = [message.uid, stream, *(delivery[key] for key in ("source", "alert_id", "round_id"))]
        event_id = "alert-" + sha256(json.dumps(identity).encode()).hexdigest()[:48]
        current = Event(event_id, message.uid, stream, ALERT_EVENT_TYPE, delivery["alert_id"],
                        message.sent_at, message.sent_at, lifecycle, priority, "", message.sent_at)
    event = Event.from_dict(current.to_dict())
    event.extensions["robot_alert"] = alert
    event.start_time = min(event.start_time, message.sent_at)
    event.end_time = max(event.end_time, message.sent_at)
    if not is_locked(event, "status"):
        event.status = event.lifecycle_status = lifecycle
        event.closed_at = parse_time(alert["recovered_at"]) if recovered else None
        event.resolution = None
    event.base_priority = priority
    if not is_locked(event, "priority"):
        event.priority = event.severity = priority
    event.summary = f"{delivery['alert_id']} · {delivery['round_id']} · {alert['state']}"
    return _record_alert_evidence(event, evidence, window, attempt_id)


def _record_alert_evidence(event, evidence, window, attempt_id):
    event.evidence.append(evidence)
    event.version += 1
    snapshot = event.to_dict()
    snapshot.pop("updated_by")
    revision = dict(action="created" if event.version == 1 else "updated", window_start=window.start.isoformat(),
                    window_end=window.end.isoformat(), message_ids=[evidence["message_id"]],
                    revision_no=event.version, snapshot=snapshot)
    if attempt_id:
        revision["attempt_id"] = attempt_id
    event.updated_by.append(revision)
    return event


def legacy_trigger_evidence(store, recovery, delivery, kinds, synthetic_source):
    reader = getattr(store, "legacy_alert_messages", None)
    if reader is None:
        return
    matches = []
    for message, source in reader(recovery, delivery):
        if message.uid != recovery.uid or message.source != recovery.source or message.sender_id != recovery.sender_id:
            continue
        if sender_kind(message.to_dict(), kinds) != "bot":
            continue
        if delivery["source"] == synthetic_source and source != synthetic_source:
            continue
        old = parse_synthetic_alert(message, source)
        if (old and old["kind"] == "trigger" and message.sent_at <= recovery.sent_at
                and (old["alert_id"], old["round_id"]) == (delivery["alert_id"], delivery["round_id"])):
            matches.append((message, old))
    if len({old["source"] for _, old in matches}) == 1:
        for message, old in matches:
            yield message, dict(old, source=delivery["source"])
