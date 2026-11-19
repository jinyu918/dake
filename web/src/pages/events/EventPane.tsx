import { AlertTriangle, Crown, Lock, MessagesSquare } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { ClassificationDetails } from '@/components/ClassificationDetails';
import { PriorityBadge, StatusBadge } from '@/components/EventBadges';
import { EventType, eventDisplayLabels } from '@/components/EventType';
import { HandoffAgeBadge } from '@/components/HandoffAgeBadge';
import { ManualDecisionDialog } from '@/components/ManualDecisionDialog';
import { customerLevelNote } from '@/lib/customerTier';
import { formatTime } from '@/lib/utils';
import { api } from '@/services/api';
import { ChatMessage } from './ChatMessage';
import { customerTierBadge } from './CustomerSwitcher';
import { ExpandableText } from './ExpandableText';
import {
  conversationLabel,
  eventDisplayTitle,
  formatClock,
  formatDay,
  messageIdentity,
  revisionHeadline,
  shanghaiDay,
} from './eventTitle';
import { evidenceKey, messageKey, revisionEvidence } from './revisionEvidence';

function present(value: string | null | undefined) {
  return value && value !== '未知' ? value : '';
}

function ContinuityEvidence({ items }: { items: API.ContinuityEvidence[] }) {
  return (
    <>
      {items.map((ref) => (
        <blockquote
          key={`${ref.source}-${ref.message_id}-${ref.digest}-${ref.quote}`}
        >
          <p>{ref.quote}</p>
          <small>
            {formatTime(ref.send_time)} · 群 {ref.conversation_id || '未记录'}
            {' · '}消息 {ref.message_id} · 账号 {ref.sender_id}
          </small>
        </blockquote>
      ))}
    </>
  );
}

function RecoveryDetails({ event }: { event: API.EventResponse }) {
  const { recovery, recurrence_of: previous } = event;
  if (!recovery && !previous) return null;
  const previousQuery = previous
    ? new URLSearchParams({
        uid: event.uid,
        stream: event.stream,
        event: previous.event_id,
      })
    : null;
  return (
    <section aria-labelledby="event-recovery">
      <h3 id="event-recovery">恢复与复发</h3>
      {recovery ? (
        <details className="aside-update-facts">
          <summary>客户已明确确认恢复</summary>
          <p>确认消息时间：{formatTime(recovery.confirmed_at)}</p>
          <ContinuityEvidence items={recovery.evidence} />
        </details>
      ) : null}
      {previous ? (
        <details className="aside-update-facts">
          <summary>本次为恢复后再次发生</summary>
          <p>
            <a href={`/events?${previousQuery}`}>查看前次事件</a>
          </p>
          <p>{previous.reason}</p>
          <p>关联时保存的前次恢复依据：</p>
          <ContinuityEvidence items={previous.recovery.evidence} />
          <p>本次再次发生的依据：</p>
          <ContinuityEvidence items={previous.evidence} />
        </details>
      ) : null}
    </section>
  );
}

function EvidenceTimeRange({ start, end }: { start: string; end: string }) {
  const sameDay = shanghaiDay(start) === shanghaiDay(end);
  return (
    <>
      <time dateTime={start}>
        {formatTime(start, true)} {formatClock(start)}
      </time>
      {start !== end && (
        <>
          {' — '}
          <time dateTime={end}>
            {!sameDay && `${formatTime(end, true)} `}
            {formatClock(end)}
          </time>
        </>
      )}
    </>
  );
}

function GroupTags({
  conversations,
}: {
  conversations: API.ConversationRef[];
}) {
  if (!conversations.length) return <span>—</span>;
  return (
    <ul className="group-tags">
      {conversations.map((item) => {
        const name = conversationLabel(item.name);
        return (
          <li key={item.id || name} className="group-tag" title={name}>
            {name}
          </li>
        );
      })}
    </ul>
  );
}

type ThreadItem =
  | { kind: 'group'; key: string; name: string; platform: string }
  | { kind: 'date'; key: string; day: string; label: string }
  | { kind: 'message'; item: API.EvidenceResponse; contextOnly?: boolean };

function compareMessageOrder(
  left: API.EvidenceResponse,
  right: API.EvidenceResponse,
) {
  const diff = Date.parse(left.send_time) - Date.parse(right.send_time);
  if (diff !== 0) return diff;
  const leftNum = Number(left.message_id);
  const rightNum = Number(right.message_id);
  if (Number.isFinite(leftNum) && Number.isFinite(rightNum)) {
    return leftNum - rightNum;
  }
  return left.message_id.localeCompare(right.message_id);
}

function threadItems(
  event: API.EventResponse,
  contextMessages?: API.ContextMessageResponse[],
): ThreadItem[] {
  const baseEvidence = event.evidence || [];
  const seenKeys = new Set(baseEvidence.map(messageKey));
  const merged: { item: API.EvidenceResponse; contextOnly: boolean }[] =
    baseEvidence.map((item) => ({ item, contextOnly: false }));

  if (contextMessages?.length) {
    for (const ctx of contextMessages) {
      const mKey = messageKey(ctx);
      if (!seenKeys.has(mKey)) {
        seenKeys.add(mKey);
        merged.push({ item: ctx, contextOnly: !ctx.is_evidence });
      }
    }
  }

  merged.sort((left, right) => compareMessageOrder(left.item, right.item));

  const conversations = new Set(
    merged.map(({ item }) => item.conversation_id).filter(Boolean),
  );
  const splitGroups = conversations.size > 1;
  const items: ThreadItem[] = [];
  let lastGroup: string | undefined;
  let lastDay = '';
  for (const { item, contextOnly } of merged) {
    const day = shanghaiDay(item.send_time);
    if (day !== lastDay) {
      items.push({
        kind: 'date',
        key: `day-${day}`,
        day,
        label: formatDay(item.send_time),
      });
      lastDay = day;
      lastGroup = undefined;
    }
    const group = item.conversation_id || '';
    if (splitGroups && group !== lastGroup) {
      items.push({
        kind: 'group',
        key: `group-${group}-${item.message_id}`,
        name: conversationLabel(item.conversation_name),
        platform: item.platform_label || '',
      });
      lastGroup = group;
    }
    items.push({ kind: 'message', item, contextOnly });
  }
  return items;
}

function sourceLine(platforms: string[], groupCount: number) {
  const parts: string[] = [];
  if (platforms.length) parts.push(platforms.join('、'));
  if (groupCount > 0) parts.push(`${groupCount} 个群`);
  return parts;
}

export function EventPane({
  event,
  customer,
  isAllCustomers = false,
  onSelectCustomer,
  onEventUpdated,
}: {
  event: API.EventResponse;
  customer?: API.CustomerResponse;
  isAllCustomers?: boolean;
  onSelectCustomer?: (uid: string) => void;
  onEventUpdated?: (updated: API.EventResponse) => void;
}) {
  const chat = useRef<HTMLOListElement>(null);
  const [focused, setFocused] = useState('');
  const [showContext, setShowContext] = useState(false);
  const [contextData, setContextData] =
    useState<API.EventContextResponse | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState('');

  useEffect(() => {
    if (!showContext) return;
    const controller = new AbortController();
    setContextLoading(true);
    setContextError('');
    api
      .eventContext(event.event_id, event.uid, event.stream, controller.signal)
      .then((res) => {
        if (!controller.signal.aborted) {
          setContextData(res);
          setContextLoading(false);
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          setContextError(
            err instanceof Error ? err.message : '加载前后群消息失败',
          );
          setContextLoading(false);
        }
      });
    return () => controller.abort();
  }, [showContext, event.event_id, event.uid, event.stream]);

  const tierBadge = customerTierBadge(customer);
  const alert =
    event.type === '机器人告警' ? event.extensions?.robot_alert : null;
  const labels = eventDisplayLabels(event);
  const controls = event.manual_controls || {};
  const priorityNote =
    typeof event.priority_detail?.level_note === 'string'
      ? customerLevelNote(event.priority_detail.level_note)
      : typeof event.priority_detail?.reason === 'string'
        ? event.priority_detail.reason
        : '';
  const statusNote =
    typeof event.status_detail?.reason === 'string'
      ? event.status_detail.reason
      : '';
  function locate(key: string) {
    const row = Array.from(
      chat.current?.querySelectorAll<HTMLElement>('[data-evidence-key]') || [],
    ).find((item) => item.dataset.evidenceKey === key);
    if (!row) return;
    setFocused(key);
    row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    row.focus({ preventScroll: true });
  }
  const conversations = event.conversations || [];
  const platforms = event.platforms?.length
    ? event.platforms
    : [
        ...new Set(
          (event.evidence || [])
            .map((item) => item.platform_label)
            .filter((label): label is string => Boolean(label)),
        ),
      ];
  const assignee = (event.assignees || []).filter(Boolean).join('、');
  const status = present(event.status);
  const evidence = event.evidence || [];
  const messageCount = new Set(evidence.map(messageKey)).size;
  const versionCount = new Set(evidence.map(evidenceKey)).size;
  const meta = [
    ...sourceLine(platforms, conversations.length),
    `累计 ${messageCount} 条关联消息`,
    ...(versionCount > messageCount ? [`${versionCount} 个证据版本`] : []),
  ];

  return (
    <>
      <section className="inbox-chat" aria-label="相关聊天">
        <header className="event-chat-header">
          <p className="event-chat-account">
            {customer?.label || `客户 ${event.uid}`}
          </p>
          <ExpandableText
            as="h2"
            lines={2}
            className="event-chat-title"
            text={eventDisplayTitle(event)}
            empty="未提供标题"
          />
          <div className="event-chat-meta-row">
            {meta.length ? (
              <p className="event-chat-meta">
                {meta.map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </p>
            ) : null}
            <button
              type="button"
              className={`chat-context-toggle ${showContext ? 'is-active' : ''}`}
              aria-pressed={showContext}
              onClick={() => setShowContext((prev) => !prev)}
            >
              <MessagesSquare size={12} />
              {contextLoading
                ? '加载上下文…'
                : showContext
                  ? '仅看关联证据'
                  : '显示前后群消息'}
            </button>
          </div>
          {contextError && showContext ? (
            <p className="chat-context-error" role="alert">
              {contextError}
            </p>
          ) : null}
        </header>
        <ol
          ref={chat}
          className="chat-log inbox-pane-scroll"
          aria-label="聊天记录"
          key={event.event_id}
        >
          {threadItems(event, showContext ? contextData?.items : undefined).map(
            (entry) => {
              if (entry.kind === 'group') {
                const text = [entry.platform, entry.name]
                  .filter(Boolean)
                  .join(' · ');
                return (
                  <li key={entry.key}>
                    <p className="chat-group-label">{text}</p>
                  </li>
                );
              }
              if (entry.kind === 'date') {
                return (
                  <li key={entry.key}>
                    <p className="chat-date">
                      <time dateTime={entry.day}>{entry.label}</time>
                    </p>
                  </li>
                );
              }
              const item = entry.item;
              const identity = messageIdentity(
                item.sender_role,
                item.sender_kind,
              );
              return (
                <li
                  key={evidenceKey(item)}
                  data-message-id={item.message_id}
                  data-evidence-key={evidenceKey(item)}
                  tabIndex={-1}
                  className={`chat-row ${identity.side}${entry.contextOnly ? ' is-context-only' : ''}${focused === evidenceKey(item) ? ' evidence-focused' : ''}`}
                >
                  <span className="chat-avatar" aria-hidden="true">
                    {identity.avatar}
                  </span>
                  <div className="chat-message-body">
                    <div className="chat-message-meta">
                      <strong>{item.sender_name || '—'}</strong>
                      {identity.label ? (
                        <span className="chat-role">{identity.label}</span>
                      ) : null}
                      {entry.contextOnly ? (
                        <span className="chat-context-tag">前后上下文</span>
                      ) : null}
                      <time dateTime={item.send_time}>
                        {formatClock(item.send_time)}
                      </time>
                    </div>
                    <ChatMessage
                      excerpt={item.excerpt}
                      content={item.content}
                    />
                  </div>
                </li>
              );
            },
          )}
        </ol>
        <p className="evidence-footer">
          {showContext
            ? `按消息时间排序 · 已包含同群前后 15 分钟原始记录${contextData?.truncated ? '（已按条数上限截断）' : ''}`
            : '按消息时间排序 · 仅展示当前事件关联证据'}
        </p>
      </section>
      <aside className="inbox-aside" aria-label="事件详情">
        <div className="event-aside-head">
          <h2 className="event-aside-title">事件详情</h2>
          {customer?.stream && customer.stream !== event.stream ? (
            <small>历史分析 · 只读</small>
          ) : onEventUpdated ? (
            <ManualDecisionDialog event={event} onUpdated={onEventUpdated} />
          ) : null}
        </div>
        <div className="event-aside inbox-pane-scroll" key={event.event_id}>
          {event.review_flag ? (
            <div className="aside-review-banner" role="status">
              <AlertTriangle size={14} />
              <span>{event.review_flag}</span>
            </div>
          ) : null}
          <dl className="aside-summary">
            <dt>摘要</dt>
            <dd>
              <ExpandableText
                lines={3}
                className="aside-summary-text"
                text={event.summary || ''}
                empty="暂无摘要"
              />
            </dd>
          </dl>
          <dl className="aside-fields">
            <div>
              <dt>
                分类标签
                {controls.types?.locked ? (
                  <Lock size={11} className="inline-lock-icon" />
                ) : null}
              </dt>
              <dd className="aside-type-badges">
                {labels.map((label) => (
                  <EventType key={label} type={label} />
                ))}
              </dd>
            </div>
            <div>
              <dt>优先级</dt>
              <dd className="aside-priority-cell">
                <PriorityBadge
                  priority={event.priority}
                  basePriority={event.base_priority}
                  detail={event.priority_detail}
                  locked={Boolean(controls.priority?.locked)}
                />
                <HandoffAgeBadge event={event} />
                {priorityNote ? (
                  <small className="aside-field-note">{priorityNote}</small>
                ) : null}
              </dd>
            </div>
            {alert && (
              <div>
                <dt>告警轮次</dt>
                <dd>
                  {alert.round_id} · {alert.state}
                </dd>
                <dd>
                  {alert.queue}
                  {alert.pending ? ' · 影响客户待核实' : ''}
                </dd>
                {alert.triggered_at && (
                  <dd>触发 {formatTime(alert.triggered_at)}</dd>
                )}
                {alert.recovered_at && (
                  <dd>恢复 {formatTime(alert.recovered_at)}</dd>
                )}
              </div>
            )}
            <div>
              <dt>客户 UID</dt>
              <dd className="aside-customer-value">
                <span>{event.uid}</span>
                {tierBadge ? (
                  <span className={`customer-tier-pill ${tierBadge.kind}`}>
                    {tierBadge.kind === 'super' && <Crown size={11} />}
                    {tierBadge.short}
                  </span>
                ) : null}
                {isAllCustomers && onSelectCustomer ? (
                  <button
                    type="button"
                    className="aside-focus-customer"
                    onClick={() => onSelectCustomer(event.uid)}
                  >
                    只看该客户
                  </button>
                ) : null}
              </dd>
            </div>
            {customer?.tam ? (
              <div>
                <dt>客户 TAM</dt>
                <dd>
                  {customer.tam}
                  {customer.tam_b ? ` / 备 ${customer.tam_b}` : ''}
                </dd>
              </div>
            ) : null}
            <div>
              <dt>来源</dt>
              <dd>{platforms.length ? platforms.join('、') : '—'}</dd>
            </div>
            <div>
              <dt>群聊</dt>
              <dd>
                <GroupTags conversations={conversations} />
              </dd>
            </div>
            <div>
              <dt>消息时间</dt>
              <dd>
                {event.start_time && event.end_time ? (
                  <EvidenceTimeRange
                    start={event.start_time}
                    end={event.end_time}
                  />
                ) : (
                  '—'
                )}
              </dd>
            </div>
          </dl>
          <section className="event-handling" aria-labelledby="event-handling">
            <h3 id="event-handling">跟进信息</h3>
            <dl>
              <div>
                <dt>
                  跟进人
                  {controls.assignees?.locked ? (
                    <Lock size={11} className="inline-lock-icon" />
                  ) : null}
                </dt>
                <dd>{assignee || '未分配'}</dd>
              </div>
              <div>
                <dt>状态</dt>
                <dd className="aside-status-cell">
                  <StatusBadge
                    status={event.lifecycle_status || status || '状态未明'}
                    waitingReason={event.waiting_reason}
                    resolution={event.resolution}
                    locked={Boolean(controls.status?.locked)}
                  />
                  {statusNote ? (
                    <small className="aside-field-note">{statusNote}</small>
                  ) : null}
                </dd>
              </div>
            </dl>
          </section>
          <ClassificationDetails
            event={event}
            evidence={evidence}
            onLocate={locate}
          />
          <RecoveryDetails event={event} />
          <section className="aside-timeline" aria-label="历次变化">
            <h3>历次变化</h3>
            {event.updated_by.length ? (
              <ol>
                {event.updated_by
                  .map((revision, index) => {
                    const proof = revisionEvidence(
                      revision,
                      event.updated_by[index - 1]?.snapshot,
                      evidence,
                    );
                    const isManual =
                      revision.source === 'manual' ||
                      revision.change_kind === 'manual';
                    return (
                      <li
                        key={`${revision.revision_no ?? index}:${revision.window_start}`}
                      >
                        <strong>
                          {revision.action === 'created'
                            ? '创建事件'
                            : isManual
                              ? '人工裁定'
                              : '更新事件'}
                        </strong>
                        <small>
                          {isManual && revision.recorded_at
                            ? formatTime(revision.recorded_at)
                            : `${formatTime(revision.window_start)} — ${formatTime(revision.window_end)}`}
                        </small>
                        <p>
                          {revision.change_summary ||
                            revisionHeadline(revision) ||
                            '已保存本轮变化'}
                        </p>
                        {proof.locatable.map((key, position) => (
                          <button
                            type="button"
                            key={key}
                            className="evidence-locate"
                            onClick={() => locate(key)}
                          >
                            定位原文
                            {proof.locatable.length > 1
                              ? ` ${position + 1}`
                              : ''}
                          </button>
                        ))}
                      </li>
                    );
                  })
                  .reverse()}
              </ol>
            ) : (
              <p>暂无已保存的变化记录</p>
            )}
          </section>
        </div>
      </aside>
    </>
  );
}
