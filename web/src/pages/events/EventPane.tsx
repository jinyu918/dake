import { ClassificationDetails } from '@/components/ClassificationDetails';
import { FieldDecisionEditor } from '@/components/FieldDecisions';
import { SameRoundCorrection } from '@/components/SameRoundCorrection';
import { formatTime } from '@/lib/utils';
import { ChatMessage } from './ChatMessage';
import { ExpandableText } from './ExpandableText';
import {
  conversationLabel,
  eventDisplayTitle,
  formatClock,
  formatDay,
  messageIdentity,
  shanghaiDay,
} from './eventTitle';
import { evidenceKey, messageKey } from './revisionEvidence';

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
          {ref.evidence_status === 'superseded' ? (
            <p className="notice-warning">旧依据已修订，仅供历史核对。</p>
          ) : null}
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
          <summary>
            {recovery.review_status === 'pending'
              ? '恢复依据待核实'
              : recovery.identity_basis === 'confirmed_support_account'
                ? '支持方已核验指定范围恢复，客户验收未确认'
                : '客户已明确确认恢复'}
          </summary>
          {recovery.scope ? <p>核验范围：{recovery.scope}</p> : null}
          <p>确认消息时间：{formatTime(recovery.confirmed_at)}</p>
          <ContinuityEvidence items={recovery.evidence} />
          {recovery.supporting_evidence?.length ? (
            <>
              <p>独立确认依据：</p>
              <ContinuityEvidence items={recovery.supporting_evidence} />
            </>
          ) : null}
        </details>
      ) : null}
      {previous ? (
        <details className="aside-update-facts">
          <summary>
            {previous.review_status === 'retracted'
              ? '误拆复发关系已撤销'
              : previous.review_status === 'pending'
                ? '前后发生轮次待核对'
                : '本次为恢复后再次发生'}
          </summary>
          {previous.review_reason ? <p>{previous.review_reason}</p> : null}
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

function BusinessRelations({ event }: { event: API.EventResponse }) {
  if (!event.relations?.length) return null;
  return (
    <section aria-label="事项关系">
      <h3>事项关系</h3>
      {event.relations.map((relation) => (
        <details key={`${relation.kind}-${relation.event_id}`}>
          <summary>
            {relation.kind === 'caused_by' ? '因果关联' : '方案关联'} ·{' '}
            {relation.review_status === 'pending'
              ? '依据待核实'
              : relation.review_status === 'retracted'
                ? '已撤销'
                : '依据有效'}
          </summary>
          <a
            href={`/events?${new URLSearchParams({ uid: event.uid, stream: event.stream, event: relation.event_id })}`}
          >
            查看关联事项
          </a>
          <p>{relation.reason}</p>
          {relation.review_reason ? <p>{relation.review_reason}</p> : null}
          {[...relation.evidence, ...relation.retraction_evidence].map(
            (proof) => (
              <blockquote key={JSON.stringify(proof)}>
                <p>{proof.quote}</p>
                <small>
                  消息 {proof.message_id} · {formatTime(proof.send_time)}
                </small>
                {proof.evidence_status === 'superseded' ? (
                  <p>旧依据已修订，仅供历史核对。</p>
                ) : null}
              </blockquote>
            ),
          )}
        </details>
      ))}
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
  | { kind: 'message'; item: API.EvidenceResponse };

function threadItems(event: API.EventResponse): ThreadItem[] {
  const evidence = [...(event.evidence || [])].sort(
    (left, right) => Date.parse(left.send_time) - Date.parse(right.send_time),
  );
  const conversations = new Set(
    evidence.map((item) => item.conversation_id).filter(Boolean),
  );
  const splitGroups = conversations.size > 1;
  const items: ThreadItem[] = [];
  let lastGroup: string | undefined;
  let lastDay = '';
  for (const item of evidence) {
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
    items.push({ kind: 'message', item });
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
  targets = [],
  onSaved,
}: {
  event: API.EventResponse;
  targets?: API.FieldTarget[];
  onSaved?: () => void;
}) {
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
          <ExpandableText
            as="h2"
            lines={2}
            className="event-chat-title"
            text={eventDisplayTitle(event)}
            empty="未提供标题"
          />
          {meta.length ? (
            <p className="event-chat-meta">
              {meta.map((item) => (
                <span key={item}>{item}</span>
              ))}
            </p>
          ) : null}
        </header>
        <ol
          className="chat-log inbox-pane-scroll"
          aria-label="聊天记录"
          key={event.event_id}
        >
          {threadItems(event).map((entry) => {
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
                className={`chat-row ${identity.side}`}
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
                    <time dateTime={item.send_time}>
                      {formatClock(item.send_time)}
                    </time>
                  </div>
                  <ChatMessage excerpt={item.excerpt} content={item.content} />
                </div>
              </li>
            );
          })}
        </ol>
      </section>
      <aside className="inbox-aside" aria-label="事件详情">
        <h2 className="event-aside-title">事件详情</h2>
        {event.corrected_into ? (
          <p role="status">
            此事项已纠正，后续跟进见{' '}
            <a
              href={`/events?${new URLSearchParams({ uid: event.uid, stream: event.stream, event: event.corrected_into })}`}
            >
              保留事项
            </a>
            。原身份及历史仍可核对。
          </p>
        ) : null}
        {onSaved && !event.corrected_into ? (
          <FieldDecisionEditor
            event={event}
            targets={targets}
            onSaved={onSaved}
          />
        ) : null}
        {onSaved && !event.corrected_into ? (
          <SameRoundCorrection event={event} onSaved={onSaved} />
        ) : null}
        <div className="event-aside inbox-pane-scroll" key={event.event_id}>
          {event.review_flag ? (
            <div className="notice notice-warning">
              {event.review_flag}，需要进一步核对。
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
          <ClassificationDetails event={event} />
          <section className="event-handling" aria-labelledby="event-handling">
            <h3 id="event-handling">跟进信息</h3>
            <dl>
              <div>
                <dt>跟进人</dt>
                <dd>{assignee || '未分配'}</dd>
              </div>
              <div>
                <dt>状态</dt>
                <dd>{status || '—'}</dd>
              </div>
            </dl>
          </section>
          <RecoveryDetails event={event} />
          <BusinessRelations event={event} />
        </div>
      </aside>
    </>
  );
}
