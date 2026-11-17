import { useState } from 'react';
import { FieldFacts } from '@/components/FieldDecisions';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeading,
  DialogTrigger,
} from '@/components/ui/dialog';
import { api } from '@/services/api';
import { ApiError } from '@/services/request';

type FieldChoices = {
  target: API.FieldTarget;
  facts: { event: API.EventResponse; fact: API.FieldFact; locked: boolean }[];
};

function key(target: API.FieldTarget) {
  return [target.kind, target.type_label || '', target.name].join(':');
}

function origin(event: API.EventResponse, fact: API.FieldFact): API.FactOrigin {
  return {
    event_id: fact.origin_event_id || event.event_id,
    fact_id: fact.origin_fact_id || fact.id,
  };
}

export function SameRoundCorrection({
  event,
  onSaved,
}: {
  event: API.EventResponse;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [baseline, setBaseline] = useState(event);
  const [retained, setRetained] = useState<API.EventResponse>();
  const [options, setOptions] = useState<API.EventResponse[]>([]);
  const [operationId, setOperationId] = useState('');
  const [operator, setOperator] = useState('');
  const [reason, setReason] = useState('');
  const [scope, setScope] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [evidence, setEvidence] = useState<API.CorrectionProof[]>([]);
  const [resolutions, setResolutions] = useState<
    Record<string, API.CorrectionResolution>
  >({});
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [retryReady, setRetryReady] = useState(false);
  const frozen = uncertain || retryReady;
  const [submitted, setSubmitted] = useState<API.SameRoundCorrectionRequest>();

  async function load() {
    setPending(true);
    try {
      const [current, listing] = await Promise.all([
        api.event(event.event_id, event.uid, event.stream),
        api.events(event.uid, event.stream, ''),
      ]);
      setBaseline(current);
      setOptions(
        listing.items.filter((item) => item.event_id !== current.event_id),
      );
      const id = retained?.event_id || current.recurrence_of?.event_id;
      setRetained(
        id ? await api.event(id, event.uid, event.stream) : undefined,
      );
      setOperationId(crypto.randomUUID());
      setRetryReady(false);
      setSubmitted(undefined);
      setEvidence([]);
      setResolutions({});
      setConfirmed(false);
      setMessage('已读取双方当前版本，请核对同轮依据和锁定影响。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '读取失败');
    } finally {
      setPending(false);
    }
  }

  const pair = retained ? [retained, baseline] : [baseline];
  const proofs = pair.flatMap((item) =>
    item.updated_by
      .flatMap((revision) => revision.changes || [])
      .filter((change) => change.effect === 'correction')
      .flatMap((change) =>
        change.evidence.map((proof) => ({
          source: proof.source,
          message_id: proof.message_id,
          digest: proof.digest,
          send_time: proof.send_time,
          quote: proof.quote,
        })),
      ),
  );
  const uniqueProofs = [
    ...new Map(proofs.map((proof) => [JSON.stringify(proof), proof])).values(),
  ];
  const fields = new Map<string, FieldChoices>();
  for (const item of pair) {
    for (const state of Object.values(item.field_states || {})) {
      const id = key(state.target);
      const entry: FieldChoices = fields.get(id) || {
        target: state.target,
        facts: [],
      };
      for (const fact of state.facts) {
        if (
          (state.adopted_ids.includes(fact.id) ||
            fact.status === 'suggested') &&
          fact.evidence_status !== 'superseded' &&
          !state.archived
        )
          entry.facts.push({
            event: item,
            fact,
            locked: state.locked_ids.includes(fact.id),
          });
      }
      fields.set(id, entry);
    }
  }

  async function submit() {
    if (!retained) return;
    const body: API.SameRoundCorrectionRequest =
      retryReady && submitted
        ? submitted
        : {
            uid: baseline.uid,
            stream: baseline.stream,
            retained_event_id: retained.event_id,
            expected_version: baseline.version,
            retained_expected_version: retained.version,
            operation_id: operationId,
            operator,
            reason,
            same_round: true,
            scope,
            evidence,
            resolutions: Object.values(resolutions),
          };
    setSubmitted(body);
    setPending(true);
    try {
      await api.correctSameRound(baseline.event_id, body);
      setMessage('同轮误拆已纠正，后续跟进进入保留事项。');
      setUncertain(false);
      onSaved();
    } catch (error) {
      setUncertain(
        error instanceof ApiError && [0, 503].includes(error.status),
      );
      setMessage(error instanceof Error ? error.message : '纠正未完成');
      if (error instanceof ApiError && error.status === 409) onSaved();
    } finally {
      setPending(false);
    }
  }

  async function verify() {
    if (!submitted) return;
    setPending(true);
    try {
      const receipt = await api.sameRoundReceipt(
        baseline.event_id,
        submitted.operation_id,
        baseline.uid,
        baseline.stream,
      );
      setUncertain(false);
      if (receipt.outcome === 'committed') {
        setMessage('已核对本次纠正提交成功。');
        onSaved();
      } else {
        setRetryReady(true);
        setMessage(
          '已在客户操作结束后确认未提交，可重试原请求或刷新双方重新核对。',
        );
      }
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : '结果仍待核对，请勿重复提交',
      );
    } finally {
      setPending(false);
    }
  }

  if (event.corrected_into || !event.recurrence_of) return null;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next && !frozen) void load();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="secondary">纠正同轮误拆</Button>
      </DialogTrigger>
      <DialogContent className="field-decision-dialog">
        <DialogHeading
          title="纠正同轮误拆"
          description="确认两项实际属于同一轮故障。旧身份和历史保留，后续只跟进保留事项。"
        />
        <label className="form-field">
          保留事项
          <select
            aria-label="保留事项"
            disabled={pending || frozen}
            value={retained?.event_id || ''}
            onChange={(e) => {
              setRetained(
                options.find((item) => item.event_id === e.target.value),
              );
              setResolutions({});
              setEvidence([]);
              setConfirmed(false);
            }}
          >
            <option value="">请选择</option>
            {options.map((item) => (
              <option key={item.event_id} value={item.event_id}>
                {item.summary}
              </option>
            ))}
          </select>
        </label>
        {pair.map((item) => (
          <details key={item.event_id}>
            <summary>
              {item.event_id === baseline.event_id ? '误拆事项' : '保留事项'}：
              {item.summary}
            </summary>
            <p>
              事项 {item.event_id} · 当前版本 {item.version}
            </p>
            <FieldFacts event={item} />
            {item.evidence.map((proof) => (
              <blockquote
                key={`${proof.source}:${proof.message_id}:${proof.digest}`}
              >
                {proof.content || proof.excerpt}
              </blockquote>
            ))}
          </details>
        ))}
        <fieldset>
          <legend>恢复更正的直接依据</legend>
          {uniqueProofs.map((proof) => (
            <label className="checkbox-field" key={JSON.stringify(proof)}>
              <input
                type="checkbox"
                disabled={frozen || pending}
                checked={evidence.some(
                  (item) => JSON.stringify(item) === JSON.stringify(proof),
                )}
                onChange={(e) =>
                  setEvidence(
                    e.target.checked
                      ? [...evidence, proof]
                      : evidence.filter(
                          (item) =>
                            JSON.stringify(item) !== JSON.stringify(proof),
                        ),
                  )
                }
              />
              {proof.quote}
            </label>
          ))}
        </fieldset>
        <label className="form-field">
          同轮对象与范围
          <input
            aria-label="同轮对象与范围"
            value={scope}
            disabled={pending || frozen}
            onChange={(e) => setScope(e.target.value)}
          />
        </label>
        <details>
          <summary>逐字段处置锁定或互斥事实</summary>
          <p>
            未指定处置的互斥事实保留待复核；涉及锁定冲突和锁定恢复结论时必须明确选定事实，并逐项核对锁定。
          </p>
          {[...fields].map(([id, entry]) => (
            <fieldset key={id}>
              <legend>
                {entry.target.type_label || '公共字段'} · {entry.target.name}
              </legend>
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  disabled={pending || frozen}
                  checked={Boolean(resolutions[id])}
                  onChange={(e) => {
                    const next = { ...resolutions };
                    if (e.target.checked)
                      next[id] = {
                        target: entry.target,
                        adopt: [],
                        acknowledged_locks: [],
                        locked: true,
                        reason: '',
                      };
                    else delete next[id];
                    setResolutions(next);
                  }}
                />
                明确处置本字段
              </label>
              {resolutions[id] ? (
                <>
                  {entry.facts.map(({ event: owner, fact, locked }) => {
                    const factOrigin = origin(owner, fact);
                    const token = JSON.stringify(factOrigin);
                    const resolution = resolutions[id];
                    const acknowledged = resolution.acknowledged_locks || [];
                    return (
                      <div key={token}>
                        <label className="checkbox-field">
                          <input
                            type="checkbox"
                            disabled={pending || frozen}
                            checked={resolution.adopt.some(
                              (value) => JSON.stringify(value) === token,
                            )}
                            onChange={(e) =>
                              setResolutions({
                                ...resolutions,
                                [id]: {
                                  ...resolution,
                                  adopt: e.target.checked
                                    ? [...resolution.adopt, factOrigin]
                                    : resolution.adopt.filter(
                                        (value) =>
                                          JSON.stringify(value) !== token,
                                      ),
                                },
                              })
                            }
                          />
                          采用
                          {owner.event_id === baseline.event_id
                            ? '误拆项'
                            : '原事项'}
                          事实：
                          {fact.value_state === 'value'
                            ? String(fact.value)
                            : fact.value_state}{' '}
                          · {fact.stage} · {fact.scope}
                        </label>
                        {locked ? (
                          <label className="checkbox-field">
                            <input
                              type="checkbox"
                              disabled={pending || frozen}
                              checked={acknowledged.some(
                                (value) => JSON.stringify(value) === token,
                              )}
                              onChange={(e) =>
                                setResolutions({
                                  ...resolutions,
                                  [id]: {
                                    ...resolution,
                                    acknowledged_locks: e.target.checked
                                      ? [...acknowledged, factOrigin]
                                      : acknowledged.filter(
                                          (value) =>
                                            JSON.stringify(value) !== token,
                                        ),
                                  },
                                })
                              }
                            />
                            已核对该事实的锁定影响
                          </label>
                        ) : null}
                      </div>
                    );
                  })}
                  <label className="checkbox-field">
                    <input
                      type="checkbox"
                      disabled={pending || frozen}
                      checked={resolutions[id].locked}
                      onChange={(e) =>
                        setResolutions({
                          ...resolutions,
                          [id]: {
                            ...resolutions[id],
                            locked: e.target.checked,
                          },
                        })
                      }
                    />
                    锁定本次选定事实
                  </label>
                  <label className="form-field">
                    本字段处置理由
                    <input
                      value={resolutions[id].reason}
                      disabled={pending || frozen}
                      onChange={(e) =>
                        setResolutions({
                          ...resolutions,
                          [id]: { ...resolutions[id], reason: e.target.value },
                        })
                      }
                    />
                  </label>
                </>
              ) : null}
            </fieldset>
          ))}
        </details>
        <label className="checkbox-field">
          <input
            type="checkbox"
            disabled={pending || frozen}
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          已核对同一对象、目标及发生轮次，纠正依据覆盖误拆边界
        </label>
        <label className="form-field">
          操作人（本地填写）
          <input
            aria-label="操作人（本地填写）"
            value={operator}
            disabled={pending || frozen}
            onChange={(e) => setOperator(e.target.value)}
          />
        </label>
        <label className="form-field">
          纠正理由
          <textarea
            aria-label="纠正理由"
            value={reason}
            disabled={pending || frozen}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <p role="status">{message}</p>
        <div className="field-decision-actions">
          <Button
            disabled={
              pending ||
              uncertain ||
              !retained ||
              !confirmed ||
              !evidence.length ||
              !reason.trim() ||
              !operator.trim() ||
              !scope.trim()
            }
            onClick={submit}
          >
            {retryReady ? '重试原纠正请求' : '保存同轮纠正'}
          </Button>
          <Button
            variant="secondary"
            disabled={pending || uncertain}
            onClick={load}
          >
            刷新双方并重新核对
          </Button>
          {uncertain ? (
            <Button variant="secondary" disabled={pending} onClick={verify}>
              核对本次纠正结果
            </Button>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
