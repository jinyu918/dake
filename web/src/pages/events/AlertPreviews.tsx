import { useState } from 'react';
import { useResource } from '@/lib/useResource';
import { formatTime } from '@/lib/utils';
import { api } from '@/services/api';

export function AlertPreviews({ uid }: { uid?: string }) {
  const [opened, setOpened] = useState(false);
  const alerts = useResource(
    opened ? `alerts:${uid || 'all'}` : null,
    (signal) => api.alertPreviews(uid, signal),
  );
  return (
    <details
      className="classification-section"
      onToggle={(event) => setOpened(event.currentTarget.open)}
    >
      <summary>机器人告警预览</summary>
      {alerts.loading ? <p>正在读取告警</p> : null}
      {alerts.error ? <p role="alert">{alerts.error}</p> : null}
      {alerts.data?.items.length === 0 ? <p>当前范围没有告警预览</p> : null}
      <ul>
        {alerts.data?.items.map((item) => (
          <li key={`${item.source}:${item.alert_id}:${item.round_id}`}>
            <strong>
              {item.priority} · {item.state}
            </strong>{' '}
            {item.alert_id} / {item.round_id}
            <p>
              {item.queue}
              {item.pending ? ' · 有客户待核实' : ''}
              {item.triggered_at
                ? ` · 触发 ${formatTime(item.triggered_at)}`
                : ''}
              {item.recovered_at
                ? ` · 恢复 ${formatTime(item.recovered_at)}`
                : ''}
            </p>
            {item.reminders.map((reminder) => (
              <p key={reminder.message_id}>
                {reminder.kind} · {reminder.priority} · {reminder.route} ·{' '}
                {formatTime(reminder.at)}
              </p>
            ))}
          </li>
        ))}
      </ul>
    </details>
  );
}
