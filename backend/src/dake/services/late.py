"""迟到、修订与无法归属的分流；不改写已提交事件。"""

from ..domain.models import parse_time


def _later_result(events, moment) -> bool:
    """已有窗口结束时间晚于该消息，说明主流已经往前提交过。"""
    for event in events:
        for revision in getattr(event, "updated_by", []) or []:
            if parse_time(revision["window_end"]) > moment:
                return True
    return False


def classify_pending(pending, window, events, grace, revision_keys=()):
    """把待处理消息分成当前窗、宽限内补齐，以及必须隔离的迟到或修订。

    宽限是现有补漏配置的工程缺省，不是行业保证。重复且摘要未变的消息不会进入 pending。
    """
    revision_keys = set(revision_keys)
    mainstream, backfill, isolated = [], [], []
    for message in pending:
        if message.sent_at >= window.end:
            continue
        later = _later_result(events, message.sent_at)
        revised = message.key in revision_keys
        in_window = window.start <= message.sent_at < window.end
        within_grace = message.sent_at >= window.start - grace
        if revised:
            isolated.append(_item(message, "source_revision", "来源修订已隔离，不改写已有事件，结果可能不完整"))
        elif in_window:
            mainstream.append(message)
        elif within_grace and not later:
            backfill.append(message)
            mainstream.append(message)
        else:
            kind = "source_revision" if revised else "late_message"
            reason = ("来源修订已隔离，主流继续且结果可能不完整" if revised
                      else "迟到消息已隔离，主流继续且结果可能不完整")
            isolated.append(_item(message, kind, reason))
    return mainstream, backfill, isolated


def _item(message, kind, reason):
    """隔离记录只保留可追溯身份，不复制聊天正文。"""
    return dict(uid=message.uid, source=message.source, message_id=message.message_id, digest=message.digest,
                send_time=message.sent_at, kind=kind, reason=reason, conversation_id=message.conversation_id)
