"""对已完成同步的来源批量跳过空窗口，不改变有消息窗口的时间边界。"""
from ..domain.models import CycleResult


def empty_prefix(service, uid, remaining):
    """检查累计待处理消息，保留迟到消息及结果待核实的执行。调用方持有客户锁。"""
    if not remaining:
        return []
    if service.attempts is not None and service.attempts.unresolved(uid, service.stream):
        return []
    pending = service.store.pending_messages(uid, service.settings.start, remaining[-1].end, service.stream)
    earliest = min((message.sent_at for message in pending), default=remaining[-1].end)
    cycles = []
    for window in remaining:
        if window.end > earliest:
            break
        cycle = CycleResult(uid, service.stream, window).to_dict()
        cycle.update(skipped_empty=True, counts_complete=True)
        cycles.append(cycle)
    return cycles
