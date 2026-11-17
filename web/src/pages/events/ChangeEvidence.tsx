import { FieldFacts } from '@/components/FieldDecisions';
import { formatTime } from '@/lib/utils';
import { evidenceKey, revisionEvidence } from './revisionEvidence';

function recoveryLabel(value: API.RecoveryConfirmation | null | undefined) {
  if (!value) return '无恢复记录';
  if (value.review_status === 'pending') return '恢复依据待核实';
  return value.identity_basis === 'confirmed_support_account'
    ? `支持方核验：${value.scope || '范围未记录'}，客户验收未确认`
    : '客户已确认';
}

/** 每条引文只展示保存当时的来源和版本，不拿现有聊天替换。 */
export function Proof({
  item,
  showRole = false,
}: {
  item: API.RecordedMessageRef;
  showRole?: boolean;
}) {
  return (
    <blockquote className="change-proof">
      {showRole ? (
        <small>
          {item.role === 'new'
            ? '本轮消息引用'
            : item.role === 'history'
              ? '历史背景引用'
              : item.role === 'auxiliary'
                ? '辅助背景引用'
                : '输入身份未记录'}
        </small>
      ) : null}
      <p>{item.quote ?? '引文未记录'}</p>
      <small>
        {item.send_time ? formatTime(item.send_time) : '时间未记录'} · 群{' '}
        {item.conversation_id || '未记录'}
      </small>
      <small>
        来源 {item.source || '未记录'} · 消息 {item.message_id || '未记录'}
      </small>
      <details>
        <summary>消息版本</summary>
        <code>{item.digest || '未记录'}</code>
      </details>
    </blockquote>
  );
}

export function Proofs({
  items,
  showRole = false,
}: {
  items: API.RecordedMessageRef[];
  showRole?: boolean;
}) {
  const unique = [
    ...new Map(items.map((item) => [JSON.stringify(item), item])).entries(),
  ];
  return (
    <>
      {unique.map(([key, item]) => (
        <Proof key={key} item={item} showRole={showRole} />
      ))}
    </>
  );
}

/** 已保存快照的可核对差异；缺前版时明确未知，不借当前状态补全。 */
export function VersionDifference({
  revision,
  previous,
  showSummary = true,
  showEvidence = true,
}: {
  revision: API.EventRevision;
  previous?: API.EventSnapshot | null;
  showSummary?: boolean;
  showEvidence?: boolean;
}) {
  const after = revision.snapshot;
  const facts = revisionEvidence(revision, previous, []);
  const initial = revision.action === 'created';
  const refs = revision.changes.flatMap((change) => change.evidence);
  const beforeLabel = initial ? '首次创建，无前版' : '未记录';
  return (
    <div className="change-version">
      <p>
        记录时间：
        {revision.recorded_at ? formatTime(revision.recorded_at) : '未记录'} ·
        提交时间：未记录
      </p>
      {revision.source === 'manual' && revision.manual_operation ? (
        <p>
          人工裁定 · 操作人（本地填写）：{revision.manual_operation.operator} ·
          理由：{revision.manual_operation.reason}
        </p>
      ) : null}
      <dl className="change-diff">
        {showSummary ? (
          <>
            <dt>原摘要</dt>
            <dd>{previous?.summary ?? beforeLabel}</dd>
            <dt>本版摘要</dt>
            <dd>{after?.summary ?? '未记录'}</dd>
          </>
        ) : null}
        <dt>状态</dt>
        <dd>
          {previous && after && previous.status === after.status
            ? `${after.status}（未变化）`
            : `${previous?.status ?? beforeLabel} → ${after?.status ?? '未记录'}`}
        </dd>
      </dl>
      {revision.summary_changed === false ? <p>摘要未变化</p> : null}
      <p>
        {facts.complete
          ? `新增 ${facts.added} 条消息证据，更新 ${facts.revised} 条已有消息的证据版本`
          : '证据差异记录不完整，不能确定全部新增或版本变化'}
      </p>
      {showEvidence ? (
        <>
          {facts.entries.map((item) => {
            const priorVersions = (previous?.evidence || []).filter(
              (old) =>
                old.source === item.source &&
                old.message_id === item.message_id &&
                old.digest !== item.digest,
            );
            const ref = refs.find(
              (r) =>
                r.source === item.source &&
                r.message_id === item.message_id &&
                r.digest === item.digest,
            );
            return (
              <div key={evidenceKey(item)}>
                {priorVersions.length ? (
                  <details>
                    <summary>此前保存的证据版本</summary>
                    <Proofs
                      items={priorVersions.map((old) => ({
                        source: old.source,
                        message_id: old.message_id,
                        digest: old.digest,
                        conversation_id: old.conversation_id,
                        send_time: old.send_time,
                        quote: old.excerpt,
                        role: 'history',
                      }))}
                    />
                  </details>
                ) : null}
                <p>本版新增的证据{priorVersions.length ? '版本' : ''}</p>
                <Proof
                  item={{
                    source: item.source,
                    message_id: item.message_id,
                    digest: item.digest,
                    conversation_id:
                      ref?.conversation_id || item.conversation_id,
                    send_time: item.send_time,
                    quote: item.excerpt,
                    role: 'new',
                  }}
                />
              </div>
            );
          })}
          <details>
            <summary>该版记录的变化依据</summary>
            {revision.source === 'manual' ? (
              <p>本版来自人工裁定；参考依据与采用决定见字段记录。</p>
            ) : revision.changes.length ? (
              revision.changes.map((change) => (
                <div key={JSON.stringify(change)}>
                  <p>
                    {change.text}
                    {change.effect === 'evidence_only' ? '（仅补证据）' : ''}
                  </p>
                  {change.replaces ? (
                    <p>替换原片段：{change.replaces}</p>
                  ) : null}
                  <Proofs
                    items={change.evidence.map((ref) => ({
                      ...ref,
                      role: 'new',
                    }))}
                  />
                </div>
              ))
            ) : (
              <p>未记录</p>
            )}
            {!(after && Object.keys(after.field_states).length) ? (
              <p>
                单个状态字段与引文的精确对应未单独记录；以上为本版保存的依据。
              </p>
            ) : null}
          </details>
        </>
      ) : null}
      {after && Object.keys(after.field_states).length ? (
        <FieldFacts event={after} />
      ) : null}
      <details>
        <summary>客户恢复与复发关系</summary>
        <p>
          恢复确认：
          {previous ? recoveryLabel(previous.recovery) : beforeLabel} →{' '}
          {after ? recoveryLabel(after.recovery) : '未记录'}
        </p>
        {previous?.recovery &&
        JSON.stringify(previous.recovery) !==
          JSON.stringify(after?.recovery) ? (
          <>
            <p>原确认依据</p>
            <Proofs
              items={previous.recovery.evidence.map((ref) => ({
                ...ref,
                role: 'history',
              }))}
            />
          </>
        ) : null}
        {after?.recovery ? (
          <>
            <p>本版确认依据</p>
            <Proofs
              items={after.recovery.evidence.map((ref) => ({
                ...ref,
                role: 'new',
              }))}
            />
          </>
        ) : null}
        <p>
          前次事件：
          {previous
            ? previous.recurrence_of?.event_id || '无关联记录'
            : beforeLabel}{' '}
          → {after ? after.recurrence_of?.event_id || '无关联记录' : '未记录'}
        </p>
        {after?.recurrence_of ? (
          <>
            <p>{after.recurrence_of.reason}</p>
            {after.recurrence_of.review_status === 'pending' ? (
              <p>前后发生轮次待核对：{after.recurrence_of.review_reason}</p>
            ) : null}
            <Proofs
              items={after.recurrence_of.evidence.map((ref) => ({
                ...ref,
                role: 'new',
              }))}
            />
          </>
        ) : null}
      </details>
    </div>
  );
}
