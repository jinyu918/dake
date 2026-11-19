import { useEffect, useState } from 'react';

const HOUR = 3600000;
const thresholds = [
  { hours: 6, tone: 'yellow' },
  { hours: 12, tone: 'orange' },
  { hours: 24, tone: 'red' },
];

type HandoffEvent = Pick<
  API.EventResponse,
  'start_time' | 'status' | 'lifecycle_status' | 'closed_at' | 'priority_detail'
>;

function waitStart(event: HandoffEvent): number {
  const status = event.lifecycle_status || event.status;
  const wait = event.priority_detail?.handoff_wait;
  const clock = event.priority_detail?.clock;
  if (
    status === '已解决' ||
    status === '已取消' ||
    event.closed_at ||
    wait?.accepted_at ||
    (!wait?.started_at && ['progress', 'stopped'].includes(clock?.phase))
  )
    return Number.NaN;
  return Date.parse(wait?.started_at || clock?.started_at || event.start_time);
}

export function HandoffAgeBadge({ event }: { event: HandoffEvent }) {
  const start = waitStart(event);
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
      if (next) {
        timer = window.setTimeout(
          update,
          Math.min(2147483647, start + next.hours * HOUR - current + 1),
        );
      }
    }
    update();
    return () => window.clearTimeout(timer);
  }, [start]);
  const band = thresholds
    .filter(({ hours }) => now - start > hours * HOUR)
    .at(-1);
  return band ? (
    <span className={`handoff-age ${band.tone}`}>
      超 {band.hours} 小时未处理
    </span>
  ) : null;
}
