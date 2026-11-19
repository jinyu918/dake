import {
  ArrowUpRight,
  Clock3,
  FileText,
  History,
  MessagesSquare,
  UserRound,
} from 'lucide-react';
import { HandoffAgeBadge } from '@/components/HandoffAgeBadge';
import { formatTime } from '@/lib/utils';
import { ClassificationDetails } from './ClassificationDetails';
import { PriorityBadge, StatusBadge } from './EventBadges';
import { EventType, eventDisplayLabels } from './EventType';
import { ErrorNotice, LoadingRows } from './Feedback';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from './ui/dialog';

const attributeNames: Record<string, string> = {
  resource_type: '资源类型',
  quantity: '数量',
  region: '地域',
  deadline: '期望时间',
  product: '产品',
  product_line: '产品线',
  impact: '影响范围',
  specification: '规格',
  intent: '意向',
  reason: '原因',
  amount: '金额',
  next_step: '下一步',
};

/** 从当前事件或某一周期的快照展示详情，历史快照不会被最新状态替换。 */
export function EventDetail({
  event,
  open,
  onClose,
  loading = false,
  error,
  retry,
  historical = false,
}: {
  event?: API.EventResponse;
  open: boolean;
  onClose: () => void;
  loading?: boolean;
  error?: string;
  retry?: () => void;
  historical?: boolean;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value) onClose();
      }}
    >
      <DialogContent className="event-drawer">
        <div className="detail-top">
          <DialogTitle>事件详情</DialogTitle>
          <DialogDescription>
            {historical
              ? '查看这一轮结束时保存的事件状态。'
              : '跟进进展、聊天证据和更新记录。'}
          </DialogDescription>
        </div>
        {error && <ErrorNotice message={error} retry={retry} />}
        {loading && !event ? (
          <LoadingRows />
        ) : (
          event && (
            <div className="event-detail-body">
              <div className="detail-badges">
                {eventDisplayLabels(event).map((label) => (
                  <EventType key={label} type={label} />
                ))}
                {historical && <span className="badge">历史快照</span>}
              </div>
              <h2>{event.summary}</h2>
              <div className="event-identifier">
                事件编号 <span>{event.event_id}</span>
              </div>
              {event.review_flag && (
                <div className="notice notice-warning">{event.review_flag}</div>
              )}
              <dl className="detail-metadata">
                <div>
                  <dt>
                    <UserRound size={14} />
                    跟进人员
                  </dt>
                  <dd>
                    {event.assignees.length
                      ? event.assignees.join('、')
                      : '尚未明确'}
                  </dd>
                </div>
                <div>
                  <dt>当前状态</dt>
                  <dd>
                    <StatusBadge
                      status={event.lifecycle_status || event.status}
                      waitingReason={event.waiting_reason}
                      resolution={event.resolution}
                    />
                  </dd>
                </div>
                <div>
                  <dt>优先级</dt>
                  <dd>
                    <PriorityBadge
                      priority={event.priority}
                      basePriority={event.base_priority}
                      detail={event.priority_detail}
                    />
                    <HandoffAgeBadge event={event} />
                  </dd>
                </div>
                <div>
                  <dt>客户</dt>
                  <dd>{event.uid}</dd>
                </div>
                <div>
                  <dt>
                    <Clock3 size={14} />
                    首次出现
                  </dt>
                  <dd>{formatTime(event.start_time)}</dd>
                </div>
                <div>
                  <dt>最近进展</dt>
                  <dd>{formatTime(event.end_time)}</dd>
                </div>
              </dl>
              <ClassificationDetails event={event} />
              {Object.keys(event.attributes).length > 0 && (
                <section className="detail-section">
                  <h3>
                    <FileText size={15} />
                    补充信息
                  </h3>
                  <dl className="attributes">
                    {Object.entries(event.attributes).map(([key, value]) => (
                      <div key={key}>
                        <dt>{attributeNames[key] || key}</dt>
                        <dd>
                          {typeof value === 'object'
                            ? JSON.stringify(value)
                            : String(value ?? '未知')}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </section>
              )}
              <section className="detail-section">
                <h3>
                  <MessagesSquare size={15} />
                  聊天证据<span>{event.evidence.length}</span>
                </h3>
                <div className="evidence-list">
                  {event.evidence.map((evidence) => (
                    <article
                      className="evidence"
                      key={`${evidence.source}-${evidence.message_id}-${evidence.digest}`}
                    >
                      <blockquote>{evidence.excerpt}</blockquote>
                      <div>
                        <time>{formatTime(evidence.send_time)}</time>
                        <span>消息 {evidence.message_id}</span>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
              <section className="detail-section">
                <h3>
                  <History size={15} />
                  更新记录<span>{event.updated_by.length}</span>
                </h3>
                <ol className="timeline">
                  {[...event.updated_by].reverse().map((revision) => (
                    <li
                      key={`${revision.window_start}-${revision.window_end}-${revision.action}-${revision.message_ids.join(',')}`}
                    >
                      <span
                        className={`timeline-point ${revision.action === 'created' ? 'created' : ''}`}
                      />
                      <div className="timeline-heading">
                        <strong>
                          {revision.action === 'created'
                            ? '创建事件'
                            : '更新已有事件'}
                        </strong>
                        <time title="处理区间">
                          {formatTime(revision.window_start)} —{' '}
                          {formatTime(revision.window_end)}
                        </time>
                      </div>
                      <p>
                        {revision.action === 'created'
                          ? '从聊天中建立事件记录'
                          : '补充进展，保留原事件编号'}
                        {revision.message_ids.length > 0 &&
                          ` · ${revision.message_ids.length} 条消息`}
                      </p>
                      {revision.snapshot?.summary && (
                        <p className="timeline-summary">
                          {String(revision.snapshot.summary)}
                        </p>
                      )}
                    </li>
                  ))}
                </ol>
              </section>
              <div className="detail-footer">
                <ArrowUpRight size={13} />
                证据与时间均来自本地保存的记录
              </div>
            </div>
          )
        )}
      </DialogContent>
    </Dialog>
  );
}
