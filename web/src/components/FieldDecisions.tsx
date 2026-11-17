const targetName = (target: API.FieldTarget) =>
  target.kind === 'attribute'
    ? `${target.type_label} · ${target.name}`
    : target.name;

/** 显示自动提取的当前字段事实、其证据及尚未消解的冲突。 */
export function FieldFacts({
  event,
}: {
  event: Pick<API.EventSnapshot, 'field_states' | 'field_issues'>;
}) {
  const states = Object.entries(event.field_states || {});
  const issues = (event.field_issues || []).filter(
    (issue) => issue.status === 'open',
  );

  if (!states.length && !issues.length) return null;

  return (
    <section className="field-facts" aria-label="字段事实">
      <h4>字段事实</h4>
      {states.map(([key, rawState]) => {
        const state = rawState as API.FieldState;
        const adopted = new Set(state.adopted_ids);
        return (
          <details key={key}>
            <summary>{targetName(state.target)}</summary>
            {state.facts.map((fact: API.FieldFact) => (
              <article className="field-fact" key={fact.id}>
                <strong>
                  {adopted.has(fact.id) ? '当前采用' : '历史事实'} ·{' '}
                  {fact.value_state === 'value'
                    ? String(fact.value ?? '未知')
                    : fact.value_state === 'cleared'
                      ? '已明确撤销'
                      : fact.value_state === 'not_applicable'
                        ? '不适用'
                        : '未知'}
                </strong>
                <p>
                  {fact.stage} · {fact.scope}
                  {fact.business_time ? ` · ${fact.business_time}` : ''}
                </p>
                {fact.evidence.map((evidence: API.FieldEvidence) => (
                  <blockquote
                    key={`${evidence.source}:${evidence.message_id}:${evidence.digest || ''}:${evidence.quote || ''}`}
                  >
                    {evidence.evidence_status === 'superseded' ? '旧依据：' : ''}
                    {evidence.quote || '引文未记录'}
                  </blockquote>
                ))}
              </article>
            ))}
          </details>
        );
      })}
      {issues.map((issue) => (
        <p className="notice notice-warning" key={issue.id}>
          {targetName(issue.target)}：{issue.reason}
        </p>
      ))}
    </section>
  );
}
