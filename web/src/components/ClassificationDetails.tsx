import { EventType } from './EventType';
import { FieldFacts } from './FieldDecisions';

type EvidenceFact = {
  evidence_ids?: string[];
  quote?: string;
  value?: unknown;
  evidence_status?: string;
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function facts(value: unknown): EvidenceFact[] {
  const entries = Array.isArray(value) ? value : [value];
  return entries
    .filter((item) => item && typeof item === 'object' && !Array.isArray(item))
    .map((item) => {
      const fact = record(item);
      return {
        evidence_ids: Array.isArray(fact.evidence_ids)
          ? fact.evidence_ids.filter(
              (id): id is string => typeof id === 'string',
            )
          : [],
        quote: typeof fact.quote === 'string' ? fact.quote : undefined,
        value: fact.value,
        evidence_status:
          typeof fact.evidence_status === 'string'
            ? fact.evidence_status
            : undefined,
      };
    });
}

function MessageRefs({ ids }: { ids: string[] }) {
  if (!ids.length) return null;
  return (
    <span className="classification-message-refs">
      {ids.map((id) => {
        const messageId = id.replace(/^[^:]+:/, '');
        return <span key={id}>消息 {messageId}</span>;
      })}
    </span>
  );
}

function FactEvidence({ fact }: { fact: EvidenceFact }) {
  return (
    <div className="classification-fact-evidence">
      {fact.evidence_status === 'superseded' ? (
        <p className="notice-warning">
          旧依据已修订，仅供历史核对，采用结论待复核。
        </p>
      ) : fact.evidence_status === 'partially_superseded' ? (
        <p>部分旧依据已修订，仍保留有效的独立依据。</p>
      ) : null}
      {fact.quote ? <blockquote>{fact.quote}</blockquote> : null}
      <MessageRefs ids={fact.evidence_ids || []} />
      {!fact.quote && !fact.evidence_ids?.length ? (
        <span className="classification-no-evidence">依据未记录</span>
      ) : null}
    </div>
  );
}

/** 展示多标签、逐标签依据及按类型归属的属性和证据。 */
export function ClassificationDetails({ event }: { event: API.EventResponse }) {
  const labels = event.types?.length
    ? event.types
    : event.type === '待分类'
      ? []
      : [event.type];
  const typeEvidence = record(event.type_evidence);
  const removedTypes = record(event.removed_types);
  const typedAttributes = record(event.typed_attributes);
  const otherReason = facts(event.other_reason)[0];

  return (
    <section className="classification-details" aria-label="分类与属性依据">
      <h3>分类与属性</h3>
      <FieldFacts event={event} />
      {event.classification_status === '待分类' ? (
        <p className="classification-status">
          待分类：已有事项依据，类型仍待确认。
        </p>
      ) : null}
      {event.classification_evidence_status !== '已记录' && labels.length ? (
        <p className="classification-status">历史记录未保存逐标签依据。</p>
      ) : null}
      <div className="classification-labels">
        {labels.map((label) => (
          <EventType key={label} type={label} />
        ))}
        {labels.length === 0 ? <span>尚未确认类型</span> : null}
      </div>
      {labels.map((label) => {
        const attributes = record(typedAttributes[label]);
        const labelFacts = facts(typeEvidence[label]);
        return (
          <section className="classification-label-detail" key={label}>
            <h4>{label}依据</h4>
            {labelFacts.length ? (
              labelFacts.map((fact) => (
                <FactEvidence
                  key={`${label}-${fact.evidence_ids?.join(',') || fact.quote}`}
                  fact={fact}
                />
              ))
            ) : (
              <p className="classification-no-evidence">标签依据未记录</p>
            )}
            {Object.entries(attributes).length ? (
              <dl className="classification-attributes">
                {Object.entries(attributes).map(([name, rawFact]) => {
                  const fact = facts(rawFact)[0] || {};
                  return (
                    <div key={name}>
                      <dt>{name}</dt>
                      <dd>
                        {String(fact.value ?? '未知')}
                        <FactEvidence fact={fact} />
                      </dd>
                    </div>
                  );
                })}
              </dl>
            ) : null}
          </section>
        );
      })}
      {labels.includes('其他') ? (
        <section className="classification-label-detail">
          <h4>其他类型说明</h4>
          <p>{String(otherReason?.value ?? '业务理由未记录')}</p>
          <FactEvidence fact={otherReason || {}} />
        </section>
      ) : null}
      {Object.entries(removedTypes).length ? (
        <section className="classification-label-detail">
          <h4>已撤销标签</h4>
          {Object.entries(removedTypes).map(([label, value]) => (
            <div className="classification-removed-type" key={label}>
              <span>{label}</span>
              {(facts(value).length ? facts(value) : [{}]).map((fact) => (
                <FactEvidence
                  key={`${label}-${fact.evidence_ids?.join(',') || fact.quote}`}
                  fact={fact}
                />
              ))}
            </div>
          ))}
        </section>
      ) : null}
    </section>
  );
}
