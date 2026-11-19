import { useEffect, useState } from 'react';

const HOUR = 3600000;
const MINUTE = 60000;
const thresholds = [
  { hours: 3, tone: 'pale-yellow' },
  { hours: 6, tone: 'yellow' },
  { hours: 12, tone: 'orange' },
  { hours: 24, tone: 'red' },
];

type HandoffEvent = Pick<
  API.EventResponse,
  | 'start_time'
  | 'status'
  | 'lifecycle_status'
  | 'closed_at'
  | 'priority_detail'
  | 'status_detail'
  | 'updated_by'
>;

function stateEvidenceTime(detail: unknown): number {
  if (!detail || typeof detail !== 'object') return Number.NaN;
  if ('source' in detail && detail.source === 'manual') {
    return 'decided_at' in detail && typeof detail.decided_at === 'string'
      ? Date.parse(detail.decided_at)
      : Number.NaN;
  }
  if (
    !('status' in detail) ||
    detail.status !== '等待中' ||
    !('evidence_refs' in detail) ||
    !Array.isArray(detail.evidence_refs)
  ) {
    return Number.NaN;
  }
  const times = detail.evidence_refs
    .map((ref: { send_time?: unknown } | null) =>
      typeof ref?.send_time === 'string' ? Date.parse(ref.send_time) : NaN,
    )
    .filter(Number.isFinite);
  return times.length ? Math.max(...times) : Number.NaN;
}

function waitingStart(event: HandoffEvent): number {
  let entry: API.EventRevision | undefined;
  let priorEvidence = Number.NaN;
  for (const revision of event.updated_by.toReversed()) {
    if (revision.snapshot) {
      if (revision.snapshot.lifecycle_status !== '等待中') {
        priorEvidence = stateEvidenceTime(revision.snapshot.status_detail);
        break;
      }
      entry = revision;
    } else if (
      revision.changed_fields.includes('lifecycle_status') ||
      revision.action === 'created'
    ) {
      entry = revision;
      break;
    }
  }

  if (entry) {
    const windowEnd = Date.parse(entry.window_end);
    const recordedAt = Date.parse(entry.recorded_at ?? '');
    if (entry.source === 'manual' && Number.isFinite(recordedAt))
      return recordedAt;
    const evidence = stateEvidenceTime(
      entry.snapshot ? entry.snapshot.status_detail : event.status_detail,
    );
    if (
      Number.isFinite(evidence) &&
      evidence !== priorEvidence &&
      (entry.snapshot || evidence <= windowEnd)
    ) {
      return evidence;
    }
    if (Number.isFinite(windowEnd)) return windowEnd;
  } else {
    const evidence = stateEvidenceTime(event.status_detail);
    if (Number.isFinite(evidence) && evidence !== priorEvidence)
      return evidence;
  }
  return Date.parse(event.start_time);
}

function waitStart(event: HandoffEvent): number {
  const lifecycle: string = event.lifecycle_status;
  const status =
    !lifecycle || (lifecycle === '状态未明' && event.status === '待处理')
      ? event.status
      : lifecycle;
  if (status === '等待中' && !event.closed_at) return waitingStart(event);
  if (status !== '待处理' && status !== '状态未明') return Number.NaN;

  const wait = event.priority_detail?.handoff_wait;
  const clock = event.priority_detail?.clock;
  if (
    event.closed_at ||
    wait?.accepted_at ||
    clock?.phase === 'progress' ||
    clock?.phase === 'stopped'
  )
    return Number.NaN;

  const startedAt =
    wait?.started_at || (clock?.phase === 'handoff' && clock.started_at);
  return Date.parse(startedAt || (status === '待处理' ? event.start_time : ''));
}

function AgeBadge({ start, kind }: { start: number; kind: '处理' | '回复' }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!Number.isFinite(start)) return;
    let timer: number | undefined;
    function update() {
      const current = Date.now();
      setNow(current);
      const next = thresholds.find(
        ({ hours }) => current <= start + hours * HOUR,
      );
      if (!next) return;
      const crossing = start + next.hours * HOUR - current + 1;
      const minute = MINUTE - (Math.max(0, current - start) % MINUTE);
      timer = window.setTimeout(
        update,
        kind === '回复' && next.hours === 3
          ? Math.min(crossing, minute)
          : Math.min(2147483647, crossing),
      );
    }
    update();
    return () => window.clearTimeout(timer);
  }, [start, kind]);

  if (!Number.isFinite(start)) return null;
  const elapsed = Math.max(0, now - start);
  const band = thresholds.filter(({ hours }) => elapsed > hours * HOUR).at(-1);
  if (band) {
    return (
      <span className={`handoff-age ${band.tone}`}>
        超 {band.hours} 小时未{kind}
      </span>
    );
  }
  if (kind === '处理') return null;
  const minutes = Math.floor(elapsed / MINUTE);
  const hours = Math.floor(minutes / 60);
  return (
    <span className="handoff-age">
      待回复 {hours ? `${hours} 小时 ` : ''}
      {minutes % 60} 分钟
    </span>
  );
}

export function HandoffAgeBadge({ event }: { event: HandoffEvent }) {
  const reply = event.priority_detail?.reply_round;
  const activeReply =
    reply &&
    !reply.closed_at &&
    !event.closed_at &&
    event.lifecycle_status !== '已解决' &&
    event.lifecycle_status !== '已取消';
  return (
    <>
      <AgeBadge start={waitStart(event)} kind="处理" />
      {activeReply && (
        <AgeBadge start={Date.parse(reply.started_at)} kind="回复" />
      )}
    </>
  );
}
