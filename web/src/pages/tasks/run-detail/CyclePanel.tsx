import {
  ArrowUpRight,
  Check,
  CircleDot,
  FileClock,
  Inbox,
  MessagesSquare,
} from 'lucide-react';
import { useState } from 'react';
import { EventDetail } from '@/components/EventDetail';
import { EventType } from '@/components/EventType';
import { formatTime } from '@/lib/utils';

/** 从本轮保存的事件快照展示变化，不用当前最终事件覆盖历史证据。 */
export function CyclePanel({
  cycles,
  selected,
  onSelect,
}: {
  cycles: API.CycleResponse[];
  selected: number;
  onSelect: (index: number) => void;
}) {
  const cycle = cycles[selected];
  const [event, setEvent] = useState<API.EventResponse>();
  if (!cycle)
    return (
      <div className="cycle-waiting">
        <FileClock size={24} strokeWidth={1.4} />
        <p>第一轮完成后，进展会显示在这里。</p>
      </div>
    );
  const changes = [
    ...cycle.created.map((id) => ({ id, kind: 'created' })),
    ...cycle.updated.map((id) => ({ id, kind: 'updated' })),
  ];
  return (
    <>
      <div className="cycle-columns">
        <section className="cycle-list" aria-label="处理周期">
          <div className="section-label">
            逐轮进展<span>{cycles.length} 轮已完成</span>
          </div>
          <div className="cycle-list-scroll">
            {cycles.map((item, index) => (
              <button
                type="button"
                key={`${item.window.start}-${item.window.end}`}
                className={`cycle-item ${index === selected ? 'selected' : ''}`}
                aria-pressed={index === selected}
                onClick={() => {
                  setEvent(undefined);
                  onSelect(index);
                }}
              >
                <span className="cycle-marker">
                  <Check size={11} />
                </span>
                <span>
                  <strong>{formatTime(item.window.start)}</strong>
                  <small>
                    {item.skipped_empty
                      ? '无待处理消息 · 已跳过'
                      : item.counts_complete === false
                        ? '消息数不完整'
                        : `${item.processed} 条消息`}
                    <span>·</span>
                    {item.created.length + item.updated.length
                      ? `${item.created.length} 新建 / ${item.updated.length} 更新`
                      : '无事件变化'}
                  </small>
                </span>
                <span className="cycle-number">
                  {String(index + 1).padStart(2, '0')}
                </span>
              </button>
            ))}
          </div>
        </section>
        <section className="cycle-detail" aria-label="本轮变化">
          <div className="cycle-detail-heading">
            <div>
              <span className="section-label">第 {selected + 1} 轮</span>
              <h3>
                {formatTime(cycle.window.start)}
                <span>—</span>
                {formatTime(cycle.window.end)}
              </h3>
            </div>
            <span className="badge">
              <Check size={11} />
              已完成
            </span>
          </div>
          {cycle.recovered_from_history && (
            <div className="notice compact-notice">
              包含中断后恢复的已提交变化。
            </div>
          )}
          {cycle.counts_complete === false && (
            <div className="notice notice-warning compact-notice">
              事件变化已恢复，部分消息计数无法还原。
            </div>
          )}
          <div className="cycle-counts">
            <span>
              <MessagesSquare size={13} />
              {cycle.counts_complete === false
                ? `已知处理 ${cycle.processed} 条`
                : `处理 ${cycle.processed} 条`}
            </span>
            <span>
              <i className="count-dot new" />
              新建 {cycle.created.length}
            </span>
            <span>
              <i className="count-dot update" />
              更新 {cycle.updated.length}
            </span>
            <span>
              <i className="count-dot review" />
              待核对 {cycle.review.length}
            </span>
          </div>
          {changes.length ? (
            <div className="cycle-events">
              {changes.map((change) => {
                const item = cycle.events.find(
                  (candidate) => candidate.event_id === change.id,
                );
                return (
                  item && (
                    <button
                      key={`${change.kind}-${change.id}`}
                      type="button"
                      className="cycle-event"
                      onClick={() => setEvent(item)}
                    >
                      <EventType type={item.type} compact />
                      <span>
                        <strong>{item.summary}</strong>
                        <small>
                          {(item.types?.length ? item.types : [item.type]).join(
                            '、',
                          )}
                          <span>·</span>
                          {item.evidence.length} 条证据
                        </small>
                      </span>
                      <span className={`change-label ${change.kind}`}>
                        {change.kind === 'created' ? '新建' : '更新'}
                      </span>
                      <ArrowUpRight size={14} />
                    </button>
                  )
                );
              })}
            </div>
          ) : (
            <div className="cycle-empty">
              <Inbox size={24} strokeWidth={1.4} />
              <strong>
                {cycle.processed
                  ? '本轮没有新增或更新的事件'
                  : '这个时段没有待处理的消息'}
              </strong>
              <p>
                {cycle.review.length
                  ? '本轮保留待核对项，详见下方。'
                  : '本轮已完成，没有保存新的事件变化。'}
              </p>
            </div>
          )}
          {cycle.review.length > 0 && (
            <div className="review-items">
              <h4>
                <CircleDot size={14} />
                需要核对
              </h4>
              {cycle.review.map((item) => (
                <div
                  key={`${item.type}-${item.message_ids.join(',')}-${item.reason}`}
                >
                  <strong>{item.type}</strong>
                  <p>{item.reason}</p>
                  <small>涉及 {item.message_ids.length} 条消息</small>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
      <EventDetail
        event={event}
        open={!!event}
        onClose={() => setEvent(undefined)}
        historical
      />
    </>
  );
}
