def canonical_id(event_id, events):
    event = events.get(event_id)
    if event is None:
        return None
    target = event.corrected_into
    if target is None:
        return event_id
    retained = events.get(target)
    if retained is None or retained.corrected_into or (retained.uid, retained.stream) != (event.uid, event.stream):
        return None
    return retained.event_id


def followup_events(events):
    return [event for event in events if event.corrected_into is None]


def resolve_candidate(candidate, events):
    by_id = {event.event_id: event for event in events}
    for name in ("referenced_event_id",):
        identity = getattr(candidate, name)
        if identity in by_id and by_id[identity].corrected_into:
            resolved = canonical_id(identity, by_id)
            if resolved is None:
                candidate.decision, candidate.referenced_event_id, candidate.changes = "review", None, []
                candidate.possible_event_ids = []
                candidate.continuity = None
                return
            setattr(candidate, name, resolved)
    candidate.possible_event_ids = list(dict.fromkeys(
        canonical_id(identity, by_id) or identity for identity in candidate.possible_event_ids))
    if candidate.continuity and candidate.continuity.get("previous_event_id"):
        identity = candidate.continuity["previous_event_id"]
        candidate.continuity["previous_event_id"] = canonical_id(identity, by_id) or identity
    for relation in candidate.relations:
        relation["event_id"] = canonical_id(relation["event_id"], by_id) or relation["event_id"]
    protected = [event for event in events if not event.corrected_into and event.identity_corrections
                 and event.topic_key == candidate.topic_key]
    if (candidate.decision == "new" and protected and not candidate.continuity):
        candidate.decision, candidate.changes = "review", []
        candidate.possible_event_ids = [event.event_id for event in protected]
        candidate.decision_reason = "已确认同轮误拆，缺少新的独立发生边界，须核对保留事项"
