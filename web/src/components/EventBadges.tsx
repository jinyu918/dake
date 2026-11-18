import { Crown, Lock } from 'lucide-react';

const PRIORITY_RANKS: Record<string, number> = {
  P0: 0,
  P1: 1,
  P2: 2,
  P3: 3,
  待评估: 4,
};

const PRIORITY_CLASSES: Record<string, string> = {
  P0: 'p0',
  P1: 'p1',
  P2: 'p2',
  P3: 'p3',
  待评估: 'pending',
};

const STATUS_CLASSES: Record<string, string> = {
  跟进中: 'active',
  等待中: 'waiting',
  已解决: 'resolved',
  已取消: 'cancelled',
  状态未明: 'unknown',
};

function customerLevelNumber(level: unknown, state: unknown): number {
  return state === 'fresh' &&
    typeof level === 'string' &&
    /^L(?:10|[0-9])$/.test(level)
    ? Number(level.slice(1))
    : -1;
}

export function priorityClock(detail?: Record<string, unknown> | null) {
  const clock = detail?.clock;
  return clock && typeof clock === 'object'
    ? (clock as Record<string, unknown>)
    : {};
}

export function compareEventsByPriority(
  left: API.EventResponse,
  right: API.EventResponse,
  customerMap?: Map<string, API.CustomerResponse>,
): number {
  function keys(event: API.EventResponse) {
    const detail = event.priority_detail;
    const clock = priorityClock(detail);
    const ended = Number(
      event.lifecycle_status === '已解决' ||
        event.lifecycle_status === '已取消',
    );
    const rank = PRIORITY_RANKS[event.priority || '待评估'] ?? 4;
    const deadline = clock.paused_at ? clock.review_at : clock.deadline;
    const parsed =
      typeof deadline === 'string' ? Date.parse(deadline) : Number.NaN;
    const due = Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
    if (rank === 4) return [ended, rank, Number(!detail?.review_required), due];
    const saved = detail?.rule_version === 'item-priority-v1';
    const profile = saved ? undefined : customerMap?.get(event.uid);
    const level = customerLevelNumber(
      profile?.level || detail?.customer_level,
      profile?.level_state || detail?.level_state,
    );
    const response = detail?.escalated_emotion
      ? 0
      : detail?.strong_followup
        ? 1
        : 2;
    const overdue =
      clock.phase !== 'stopped' && (clock.overdue || clock.review_due);
    return [ended, rank, Number(!overdue), -level, response, due];
  }
  const leftKeys = keys(left);
  const rightKeys = keys(right);
  for (const [index, key] of leftKeys.entries()) {
    if (key !== rightKeys[index]) return key < rightKeys[index] ? -1 : 1;
  }
  return left.event_id === right.event_id
    ? 0
    : left.event_id < right.event_id
      ? -1
      : 1;
}

/** 展示业务优先级徽标及超级大客提档、人工锁定标记。 */
export function PriorityBadge({
  priority,
  basePriority,
  detail,
  locked = false,
  compact = false,
}: {
  priority?: string;
  basePriority?: string;
  detail?: Record<string, unknown> | null;
  locked?: boolean;
  compact?: boolean;
}) {
  const value = priority || '待评估';
  const tone = PRIORITY_CLASSES[value] || 'pending';
  const clock = priorityClock(detail);
  const vipBoosted = Boolean(detail?.vip_boosted);
  const levelNote =
    typeof detail?.level_note === 'string' ? detail.level_note : '';
  return (
    <span
      className={`priority-pill ${tone} ${compact ? 'compact' : ''}`}
      title={levelNote || undefined}
    >
      {vipBoosted && <Crown size={11} />}
      <span>{value}</span>
      {!compact && vipBoosted && basePriority && basePriority !== value && (
        <small className="priority-pill-sub">基 {basePriority}</small>
      )}
      {clock.phase !== 'stopped' && clock.overdue ? <small>超时</small> : null}
      {clock.review_due ? <small>复查到期</small> : null}
      {basePriority === '待评估' && value !== '待评估' ? (
        <small>影响待评估</small>
      ) : null}
      {locked && <Lock size={10} aria-label="已人工锁定" />}
    </span>
  );
}

/** 展示生命周期五主态及等待方或结束结果子原因。 */
export function StatusBadge({
  status,
  waitingReason,
  resolution,
  locked = false,
  compact = false,
}: {
  status?: string;
  waitingReason?: string | null;
  resolution?: string | null;
  locked?: boolean;
  compact?: boolean;
}) {
  const main = status || '状态未明';
  const tone = STATUS_CLASSES[main] || 'unknown';
  const sub =
    main === '等待中'
      ? waitingReason
      : main === '已解决' || main === '已取消'
        ? resolution
        : null;
  return (
    <span className={`lifecycle-pill ${tone} ${compact ? 'compact' : ''}`}>
      <i aria-hidden="true" />
      <span>{main}</span>
      {!compact && sub ? <small>· {sub}</small> : null}
      {locked && <Lock size={10} aria-label="已人工锁定" />}
    </span>
  );
}
