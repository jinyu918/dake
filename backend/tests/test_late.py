"""迟到与来源修订的分流使用合成消息，不读取真实聊天。"""

from datetime import timedelta
from types import SimpleNamespace

from dake.config import bound
from dake.domain.models import Window
from dake.services.late import classify_pending


def message(mid, at):
    return SimpleNamespace(uid="synthetic-user", source="official_chat", message_id=mid, digest="digest-" + mid,
        sent_at=bound(at), key="official_chat:" + mid, conversation_id="synthetic-group")


def event(end):
    return SimpleNamespace(updated_by=[{"window_end": bound(end).isoformat()}])


def test_ordinary_late_message_is_isolated_without_mixing_into_the_current_window():
    window = Window(bound("2026-11-16T10:15:00+08:00"), bound("2026-11-16T10:30:00+08:00"))
    current = message("current", "2026-11-16T10:20:00+08:00")
    late = message("late", "2026-11-16T10:05:00+08:00")
    mainstream, backfill, isolated = classify_pending(
        [late, current], window, [event("2026-11-16T10:15:00+08:00")], timedelta(days=2))
    assert mainstream == [current] and not backfill
    assert isolated[0]["kind"] == "late_message" and isolated[0]["message_id"] == "late"
    assert "content" not in isolated[0]


def test_grace_backfill_requires_no_later_result_and_revision_never_rewrites():
    window = Window(bound("2026-11-16T10:15:00+08:00"), bound("2026-11-16T10:30:00+08:00"))
    early = message("early", "2026-11-16T10:05:00+08:00")
    revised = message("revised", "2026-11-16T10:20:00+08:00")
    mainstream, backfill, isolated = classify_pending([early], window, [], timedelta(minutes=15))
    assert mainstream == [early] and backfill == [early] and not isolated
    mainstream, _, isolated = classify_pending(
        [revised], window, [], timedelta(days=2), revision_keys={revised.key})
    assert not mainstream and isolated[0]["kind"] == "source_revision"
