import { formatMetricNumber, productDisplayName } from '@/lib/productLabels';
import { formatTime } from '@/lib/utils';
import { evidenceKey } from '@/pages/events/revisionEvidence';
import { priorityClock } from './EventBadges';

type EvidenceRef = Pick<
  API.EvidenceResponse,
  'message_id' | 'source' | 'digest'
>;
type ResolveEvidence = (mid: string, ref?: EvidenceRef) => string | undefined;

type FactRecord = {
  value?: unknown;
  quote?: unknown;
  evidence_ids?: unknown;
  evidence_refs?: unknown;
};

type StructuredEvidenceRecord = {
  field_id?: unknown;
  scope_type?: unknown;
  scope_key?: unknown;
  label?: unknown;
  unit?: unknown;
  normalized_value?: unknown;
  raw_value?: unknown;
  quote?: unknown;
  evidence_ids?: unknown;
};

function messageRefIds(fact: FactRecord): string[] {
  const refs = Array.isArray(fact.evidence_refs) ? fact.evidence_refs : [];
  const fromRefs = refs
    .map((item) =>
      item && typeof item === 'object'
        ? String(
            (item as Record<string, unknown>).message_id ??
              (item as Record<string, unknown>).id ??
              '',
          )
        : '',
    )
    .filter(Boolean);
  if (fromRefs.length) return [...new Set(fromRefs)];
  const raw = Array.isArray(fact.evidence_ids) ? fact.evidence_ids : [];
  return [
    ...new Set(
      raw.map((id) => String(id || '').replace(/^[^:]+:/, '')).filter(Boolean),
    ),
  ];
}

function PriorityEvidence({
  fact,
  resolve,
  onLocate,
}: {
  fact: FactRecord;
  resolve?: ResolveEvidence;
  onLocate?: (key: string) => void;
}) {
  if (!fact) return null;
  const refs: EvidenceRef[] = Array.isArray(fact.evidence_refs)
    ? fact.evidence_refs
    : [];
  return (
    <div>
      {typeof fact.quote === 'string' && <blockquote>{fact.quote}</blockquote>}
      {messageRefIds(fact).map((mid) => {
        const key = resolve?.(
          mid,
          refs.find((ref) => ref?.message_id === mid),
        );
        return key && onLocate ? (
          <button
            key={mid}
            type="button"
            className="evidence-locate"
            onClick={() => onLocate(key)}
          >
            消息 {mid}
          </button>
        ) : (
          <small key={mid}>消息 {mid}</small>
        );
      })}
    </div>
  );
}

const impactLabels: Record<string, string> = {
  critical: '重大事故',
  high: '核心业务受阻',
  medium: '局部业务受影响',
  low: '常规事项',
  unknown: '未确认',
};
const urgencyLabels: Record<string, string> = {
  immediate: '紧急',
  soon: '近期',
  normal: '常规',
  unknown: '未确认',
};

export function ItemPriorityDetails({
  event,
  resolve,
  onLocate,
}: {
  event: API.EventResponse;
  resolve?: ResolveEvidence;
  onLocate?: (key: string) => void;
}) {
  if (event.type === '机器人告警') return null;
  const priority = event.priority_detail ?? {};
  const initial = priority.initial_basis;
  const upgrade = priority.response_upgrade;
  const clock = priorityClock(priority);
  const affected = Array.isArray(priority.affected_customers)
    ? priority.affected_customers
    : [];
  return (
    <section aria-label="优先级依据" className="classification-note">
      <h3>优先级依据</h3>
      {initial ? (
        <div>
          <strong>首次定级</strong>
          <p>
            客观 {initial.objective_priority} →{' '}
            {['L9', 'L10'].includes(initial.customer_level) ? '超大客' : ''}基础{' '}
            {initial.base_priority} → 处理 {initial.priority}
          </p>
          <p>有效客户等级 {initial.customer_level ?? '待确认'}</p>
          {initial.reason && <p>{initial.reason}</p>}
          <p>业务影响 · {impactLabels[initial.impact]}</p>
          <PriorityEvidence
            fact={
              initial.impact_evidence ?? {
                evidence_refs: initial.evidence_refs,
                quote: initial.evidence_refs?.[0]?.quote,
              }
            }
            resolve={resolve}
            onLocate={onLocate}
          />
          <p>客观时限 · {urgencyLabels[initial.urgency]}</p>
          {initial.urgency_evidence && (
            <PriorityEvidence
              fact={initial.urgency_evidence}
              resolve={resolve}
              onLocate={onLocate}
            />
          )}
        </div>
      ) : (
        <p>首次定级依据未留存</p>
      )}
      {upgrade && (
        <div>
          <strong>
            {formatTime(upgrade.occurred_at)} · {upgrade.from_priority} →{' '}
            {upgrade.to_priority} ·{' '}
            {upgrade.kind === 'strong_followup' ? '强催办' : '明显情绪升级'}
          </strong>
          <PriorityEvidence
            fact={upgrade}
            resolve={resolve}
            onLocate={onLocate}
          />
        </div>
      )}
      {event.manual_controls?.priority?.locked ? (
        <p>当前自动建议 {String(priority.pre_timer_priority || '待评估')}</p>
      ) : null}
      {priority.review_required ? <p>业务影响待复核</p> : null}
      {typeof priority.effective_customer_uid === 'string' ? (
        <p>
          采用客户 {priority.effective_customer_uid} ·{' '}
          {String(priority.customer_level ?? '等级待确认')}
        </p>
      ) : null}
      {clock.phase &&
        (clock.phase === 'stopped' ? (
          <p>计时已结束</p>
        ) : (
          <p>
            {clock.phase === 'progress'
              ? '待进展'
              : clock.phase === 'handoff'
                ? '待接手'
                : '计时起点待核实'}
            {typeof clock.deadline === 'string' && !clock.paused_at
              ? ` · 截止 ${formatTime(clock.deadline)}`
              : ''}
            {typeof clock.review_at === 'string' && clock.paused_at
              ? ` · 暂停至复查 ${formatTime(clock.review_at)}`
              : ''}
          </p>
        ))}
      {affected.map((raw) => {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
        const fact = raw as Record<string, unknown>;
        return (
          <div key={String(fact.uid ?? fact.customer)}>
            <strong>
              {String(fact.customer)} ·{' '}
              {fact.status === 'confirmed' ? '已确认影响' : '影响待核实'}
            </strong>
            {typeof fact.quote === 'string' ? (
              <blockquote>{fact.quote}</blockquote>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}

/** 展示可配置产品归属、强类型结构化属性指标、分类标签原文依据及更正记录。 */
export function ClassificationDetails({
  event,
  evidence = [],
  onLocate,
}: {
  event: API.EventResponse;
  evidence?: API.EvidenceResponse[];
  onLocate?: (evidenceKeyValue: string) => void;
}) {
  const typeEvidence = event.type_evidence || {};
  const removedTypes = event.removed_types || {};
  const otherReason = event.other_reason;
  const products = event.products || [];
  const structuredEvidence = (event.structured_evidence ||
    []) as StructuredEvidenceRecord[];

  const hasPriority = event.type !== '机器人告警';
  const hasTypeEvidence = Object.keys(typeEvidence).length > 0;
  const hasRemoved = Object.keys(removedTypes).length > 0;
  const hasOtherReason = Boolean(
    otherReason && typeof otherReason.quote === 'string',
  );
  const hasProducts = products.length > 0;
  const hasStructured = structuredEvidence.length > 0;

  if (
    !hasPriority &&
    !hasTypeEvidence &&
    !hasRemoved &&
    !hasOtherReason &&
    !hasProducts &&
    !hasStructured
  ) {
    return null;
  }

  function resolveEvidence(mid: string, ref?: EvidenceRef) {
    const matched = evidence.find(
      (item) =>
        item.message_id === mid &&
        (!ref?.source || item.source === ref.source) &&
        (!ref?.digest || item.digest === ref.digest),
    );
    return matched && evidenceKey(matched);
  }

  function locateByMessageId(mid: string) {
    const key = resolveEvidence(mid);
    if (key) onLocate?.(key);
  }

  return (
    <section
      className="classification-section"
      aria-label={
        hasProducts || hasStructured ? '产品与结构化属性' : '事项分类与定级'
      }
    >
      <ItemPriorityDetails
        event={event}
        resolve={resolveEvidence}
        onLocate={onLocate}
      />
      {hasProducts || hasStructured ? <h3>产品与结构化属性</h3> : null}

      {hasProducts || hasStructured ? (
        <div className="typed-attr-card mb-3">
          {hasProducts ? (
            <div className="typed-attr-header flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-neutral-500">
                涉及产品：
              </span>
              {products.map((pkey) => (
                <span
                  key={pkey}
                  className="inline-flex items-center rounded-md bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-900 ring-1 ring-amber-200/80 ring-inset"
                >
                  {productDisplayName(pkey)}
                </span>
              ))}
            </div>
          ) : null}
          {hasStructured ? (
            <dl className="typed-attr-list">
              {structuredEvidence.map((item, idx) => {
                const label = String(
                  item.label || item.field_id || `字段${idx}`,
                );
                const unit = typeof item.unit === 'string' ? item.unit : '';
                const formatted = formatMetricNumber(
                  item.normalized_value,
                  unit,
                );
                const quote = typeof item.quote === 'string' ? item.quote : '';
                const mids = messageRefIds({
                  evidence_ids: item.evidence_ids,
                });
                return (
                  <div key={String(item.field_id || idx)}>
                    <dt>{label}</dt>
                    <dd>
                      <span className="font-medium">{formatted}</span>
                      {quote || mids.length ? (
                        <small className="typed-attr-proof">
                          {quote ? `「${quote}」` : ''}
                          {mids.map((mid) =>
                            onLocate &&
                            evidence.some((e) => e.message_id === mid) ? (
                              <button
                                key={mid}
                                type="button"
                                className="evidence-locate"
                                onClick={() => locateByMessageId(mid)}
                              >
                                消息 {mid}
                              </button>
                            ) : (
                              <span key={mid}> 消息 {mid}</span>
                            ),
                          )}
                        </small>
                      ) : null}
                    </dd>
                  </div>
                );
              })}
            </dl>
          ) : null}
        </div>
      ) : null}

      {hasOtherReason && otherReason ? (
        <div className="classification-note">
          <strong>归为「其他」的排除依据：</strong>
          <span>{String(otherReason.value || otherReason.quote || '')}</span>
          {typeof otherReason.quote === 'string' &&
            otherReason.quote !== otherReason.value && (
              <blockquote>{otherReason.quote}</blockquote>
            )}
        </div>
      ) : null}

      {hasTypeEvidence ? (
        <details className="aside-update-facts">
          <summary>查看各标签原文依据</summary>
          <div className="type-evidence-list">
            {Object.entries(typeEvidence).map(([label, facts]) => (
              <div key={label} className="type-evidence-group">
                <strong>{label}</strong>
                {(facts || []).map((rawFact: Record<string, unknown>) => {
                  const fact = (rawFact || {}) as FactRecord;
                  const quote =
                    typeof fact.quote === 'string' ? fact.quote : '';
                  const mids = messageRefIds(fact);
                  return (
                    <blockquote key={`${label}-${mids.join(',')}-${quote}`}>
                      <p>{quote || '未记录引文'}</p>
                      {mids.length ? (
                        <small>消息 {mids.join('、')}</small>
                      ) : null}
                    </blockquote>
                  );
                })}
              </div>
            ))}
          </div>
        </details>
      ) : null}

      {hasRemoved ? (
        <details className="aside-update-facts">
          <summary>
            已更正移除的标签（{Object.keys(removedTypes).join('、')}）
          </summary>
          <div className="type-evidence-list">
            {Object.entries(removedTypes).map(([label, facts]) => (
              <div key={label} className="type-evidence-group">
                <strong>{label}</strong>
                {(facts || []).map((rawFact: Record<string, unknown>) => {
                  const fact = (rawFact || {}) as FactRecord;
                  const quote =
                    typeof fact.quote === 'string' ? fact.quote : '';
                  const mids = messageRefIds(fact);
                  return (
                    <blockquote key={`${label}-${mids.join(',')}-${quote}`}>
                      <p>{quote || '未记录更正引文'}</p>
                      {mids.length ? (
                        <small>消息 {mids.join('、')}</small>
                      ) : null}
                    </blockquote>
                  );
                })}
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </section>
  );
}
