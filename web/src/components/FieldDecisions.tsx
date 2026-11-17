import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeading,
  DialogTrigger,
} from '@/components/ui/dialog';
import { formatTime } from '@/lib/utils';
import { api } from '@/services/api';
import { ApiError } from '@/services/request';

const names: Record<string, string> = {
  summary: '摘要',
  status: '跟进状态',
  severity: '等级',
  assignees: '主要跟进人',
  other_reason: '其他业务理由',
};
const actions: Record<API.FieldAction['action'], string> = {
  confirm: '确认当前值',
  adopt: '采用指定建议',
  set: '填写裁定值',
  clear: '主动清空',
  unknown: '标记未知',
  not_applicable: '标记不适用',
  lock: '锁定采用值',
  unlock: '解除锁定',
  resolve: '保留当前值并解决指定问题',
};
const states: Record<string, string> = {
  value: '有值',
  cleared: '人工主动清空',
  unknown: '未知',
  not_applicable: '不适用',
};
const statuses: Record<string, string> = {
  adopted: '当前采用',
  suggested: '新建议，待复核',
  historical: '历史事实',
  corrected: '已更正',
  confirmed: '补充确认',
  archived: '已归档',
  rejected: '已保留原值并驳回建议',
};

function key(target: API.FieldTarget) {
  return [target.kind, target.type_label || '', target.name].join(':');
}

function label(target: API.FieldTarget) {
  return `${target.type_label ? `${target.type_label} · ` : target.kind === 'label' ? '标签 · ' : ''}${names[target.name] || target.name}`;
}

function valueText(value: unknown) {
  if (typeof value === 'boolean') return value ? '采用标签' : '撤销标签';
  return Array.isArray(value) ? value.join('、') : String(value ?? '');
}

export function FieldFacts({
  event,
}: {
  event: Pick<API.EventSnapshot, 'field_states' | 'field_issues'>;
}) {
  const entries: API.FieldState[] = Object.values(event.field_states || {});
  const issues = event.field_issues || [];
  if (!entries.length && !issues.length) return null;
  return (
    <section className="field-facts" aria-label="字段事实与人工裁定">
      <h3>字段事实与人工裁定</h3>
      {entries.map((state) => (
        <details
          key={key(state.target)}
          open={issues.some(
            (item) =>
              item.status === 'open' && item.field_key === key(state.target),
          )}
        >
          <summary>
            {label(state.target)} · {state.locked ? '已锁定' : '未锁定'}
            {state.archived ? ' · 已归档' : ''}
          </summary>
          {state.decision ? (
            <p>
              操作人（本地填写）：{String(state.decision.operator)} ·{' '}
              {formatTime(String(state.decision.decided_at))}
              <br />
              理由：{String(state.decision.reason)}
            </p>
          ) : null}
          {state.inherited_decisions?.map((decision) => (
            <p key={JSON.stringify(decision)}>
              已保留来源事项 {String(decision.origin_event_id)}{' '}
              的人工决定及锁定记录，可从原事项历史核对。
            </p>
          ))}
          {state.facts.map((fact) => (
            <div className="field-fact" key={fact.id}>
              <strong>
                {state.adopted_ids.includes(fact.id)
                  ? '当前采用'
                  : statuses[fact.status] || fact.status}
                ：
                {fact.value_state === 'value'
                  ? valueText(fact.value)
                  : states[fact.value_state]}
              </strong>
              {state.locked_ids.includes(fact.id) ? (
                <span> · 锁定保护</span>
              ) : null}
              <p>
                {fact.stage} · {fact.scope} ·{' '}
                {fact.time_kind === 'planned'
                  ? '计划时间'
                  : fact.time_kind === 'actual'
                    ? '实际时间'
                    : '业务时间未知'}
                {fact.business_time ? `：${fact.business_time}` : ''}
              </p>
              <p>
                来源：
                {fact.source === 'manual'
                  ? '人工裁定'
                  : fact.source === 'legacy'
                    ? '历史记录'
                    : '自动提取'}{' '}
                · {fact.reason}
              </p>
              {fact.origin_event_id ? (
                <p>
                  来源事项：{fact.origin_event_id} · 原事实：
                  {fact.origin_fact_id}
                </p>
              ) : null}
              {fact.source === 'manual' ? (
                <p>
                  裁定操作人（本地填写）：{fact.operator || '未记录'} ·{' '}
                  {fact.decided_at
                    ? formatTime(fact.decided_at)
                    : '裁定时间未记录'}
                  。以下为裁定时参考的依据，裁定值来自人工操作。
                </p>
              ) : null}
              {fact.evidence_status === 'superseded' ? (
                <p className="notice-warning">
                  直接消息依据已修订，以下旧引文仅供历史核对，采用决定待复核。
                </p>
              ) : fact.evidence_status === 'partially_superseded' ? (
                <p>部分旧依据已修订，仍保留有效的独立依据。</p>
              ) : null}
              {fact.evidence.length ? (
                fact.evidence.map((proof) => (
                  <blockquote key={JSON.stringify(proof)}>
                    <p>{proof.quote || '原文未记录'}</p>
                    {proof.evidence_status === 'superseded' ? (
                      <p className="notice-warning">
                        旧依据已修订，仅供历史核对。
                      </p>
                    ) : null}
                    <small>
                      消息 {proof.message_id} ·{' '}
                      {proof.send_time
                        ? formatTime(proof.send_time)
                        : '时间未记录'}
                    </small>
                    <details>
                      <summary>依据版本</summary>
                      <code>{proof.digest || '版本未记录'}</code>
                    </details>
                  </blockquote>
                ))
              ) : (
                <p>聊天原文依据未记录；人工裁定不冒充聊天原文。</p>
              )}
            </div>
          ))}
          {issues
            .filter((item) => item.field_key === key(state.target))
            .map((item) => (
              <p
                key={item.id}
                className={
                  item.status === 'open' ? 'notice notice-warning' : ''
                }
              >
                {item.status === 'open' ? '待复核' : '已解决'}：{item.reason}
              </p>
            ))}
        </details>
      ))}
    </section>
  );
}

export function FieldDecisionEditor({
  event,
  targets,
  onSaved,
}: {
  event: API.EventResponse;
  targets: API.FieldTarget[];
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [baseline, setBaseline] = useState(event);
  const [targetKey, setTargetKey] = useState('');
  const [action, setAction] = useState<API.FieldAction['action']>('confirm');
  const [operator, setOperator] = useState('');
  const [reason, setReason] = useState('');
  const [value, setValue] = useState('');
  const [stage, setStage] = useState('人工确认');
  const [scope, setScope] = useState('当前事项');
  const [factId, setFactId] = useState('');
  const [problemIds, setProblemIds] = useState<string[]>([]);
  const [archive, setArchive] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [operationId, setOperationId] = useState('');
  const available = targets.length
    ? targets
    : Object.values(baseline.field_states || {}).map((state) => state.target);
  const target =
    available.find((item) => key(item) === targetKey) || available[0];
  const state: API.FieldState | undefined = target
    ? baseline.field_states?.[key(target)]
    : undefined;
  const problems = (baseline.field_issues || []).filter(
    (item) =>
      target && item.field_key === key(target) && item.status === 'open',
  );
  const facts =
    state?.facts.filter((fact) =>
      action === 'adopt'
        ? fact.status === 'suggested' && fact.evidence_status !== 'superseded'
        : state.adopted_ids.includes(fact.id),
    ) || [];

  function capture() {
    setBaseline(structuredClone(event));
    setOperationId(crypto.randomUUID());
    setProblemIds([]);
    setUncertain(false);
    setMessage('');
  }

  async function submit() {
    if (!target) return;
    let proposedValue: string | string[] | boolean | null = value;
    if (target.kind === 'label') proposedValue = value === 'true';
    if (target.kind === 'common' && target.name === 'assignees')
      proposedValue = value
        .split('、')
        .map((item) => item.trim())
        .filter(Boolean);
    const selectedFact = facts.find((fact) => fact.id === factId);
    const body: API.FieldDecisionRequest = {
      uid: baseline.uid,
      stream: baseline.stream,
      expected_version: baseline.version,
      operation_id: operationId,
      operator,
      reason,
      actions: [
        {
          target,
          action,
          value: action === 'set' ? proposedValue : null,
          fact_id: factId || null,
          stage: selectedFact?.stage || stage,
          scope: selectedFact?.scope || scope,
          business_time: selectedFact?.business_time || null,
          time_kind: selectedFact?.time_kind || 'unknown',
          issue_ids: ['confirm', 'lock', 'unlock'].includes(action)
            ? []
            : problemIds,
          archive_dependents: archive,
        },
      ],
    };
    setPending(true);
    try {
      const saved = await api.decideFields(baseline.event_id, body);
      setBaseline(saved);
      setOperationId(crypto.randomUUID());
      setProblemIds([]);
      setFactId('');
      setMessage('裁定已保存。');
      onSaved();
    } catch (error) {
      setUncertain(
        error instanceof ApiError && [0, 503].includes(error.status),
      );
      setMessage(error instanceof Error ? error.message : '裁定未完成。');
      if (error instanceof ApiError && error.status === 409) onSaved();
    } finally {
      setPending(false);
    }
  }

  async function verify() {
    setPending(true);
    try {
      const receipt = await api.fieldDecision(
        baseline.event_id,
        operationId,
        baseline.uid,
        baseline.stream,
      );
      const current = await api.event(
        baseline.event_id,
        baseline.uid,
        baseline.stream,
      );
      setBaseline(current);
      setOperationId(crypto.randomUUID());
      setProblemIds([]);
      setFactId('');
      setMessage(`已核对本次操作提交成功：${receipt.reason}`);
      setUncertain(false);
      onSaved();
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : '尚未核对提交结果，请勿重复提交。',
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next && !uncertain) capture();
        setOpen(next);
      }}
    >
      <DialogTrigger asChild>
        <Button variant="secondary">人工确认与裁定</Button>
      </DialogTrigger>
      <DialogContent className="field-decision-dialog">
        <DialogHeading
          title="人工确认与裁定"
          description="逐字段保存采用决定、理由和锁定。操作人为本地填写；解除锁定不会采用新建议。"
        />
        <form
          onSubmit={(submitEvent) => {
            submitEvent.preventDefault();
            void submit();
          }}
        >
          <label className="form-field">
            裁定字段
            <select
              value={target ? key(target) : ''}
              onChange={(e) => {
                setTargetKey(e.target.value);
                setFactId('');
                setProblemIds([]);
              }}
            >
              {available.map((item) => (
                <option key={key(item)} value={key(item)}>
                  {label(item)}
                </option>
              ))}
            </select>
          </label>
          <label className="form-field">
            人工动作
            <select
              value={action}
              onChange={(e) => {
                setAction(e.target.value as API.FieldAction['action']);
                setFactId('');
                setProblemIds([]);
              }}
            >
              {Object.entries(actions).map(([name, title]) => (
                <option key={name} value={name}>
                  {title}
                </option>
              ))}
            </select>
          </label>
          {['adopt', 'set', 'clear', 'unknown', 'not_applicable'].includes(
            action,
          ) && facts.length ? (
            <label className="form-field">
              事实目标
              <select
                value={factId}
                required={action === 'adopt'}
                onChange={(e) => setFactId(e.target.value)}
              >
                <option value="">请选择事实</option>
                {facts.map((fact) => (
                  <option value={fact.id} key={fact.id}>
                    {fact.stage} · {fact.scope}：{valueText(fact.value)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {action === 'set' ? (
            <>
              <label className="form-field" htmlFor="field-decision-value">
                裁定采用值
                {target?.kind === 'label' ? (
                  <select
                    id="field-decision-value"
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    required
                  >
                    <option value="">请选择</option>
                    <option value="true">采用标签</option>
                    <option value="false">撤销标签</option>
                  </select>
                ) : (
                  <textarea
                    id="field-decision-value"
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    required
                    placeholder={
                      target?.name === 'assignees' ? '多位跟进人以顿号分隔' : ''
                    }
                  />
                )}
              </label>
              {!factId ? (
                <>
                  <label className="form-field">
                    事实环节
                    <input
                      value={stage}
                      onChange={(e) => setStage(e.target.value)}
                      required
                    />
                  </label>
                  <label className="form-field">
                    适用对象与范围
                    <input
                      value={scope}
                      onChange={(e) => setScope(e.target.value)}
                      required
                    />
                  </label>
                </>
              ) : null}
            </>
          ) : null}
          {!['confirm', 'lock', 'unlock'].includes(action) &&
          problems.length ? (
            <fieldset>
              <legend>本次明确解决的问题</legend>
              {problems.map((item) => (
                <label className="checkbox-field" key={item.id}>
                  <input
                    type="checkbox"
                    checked={problemIds.includes(item.id)}
                    onChange={(e) =>
                      setProblemIds(
                        e.target.checked
                          ? [...problemIds, item.id]
                          : problemIds.filter((id) => id !== item.id),
                      )
                    }
                  />
                  {item.reason}
                </label>
              ))}
            </fieldset>
          ) : null}
          {target?.kind === 'label' && ['set', 'adopt'].includes(action) ? (
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={archive}
                onChange={(e) => setArchive(e.target.checked)}
              />
              撤销时归档该标签下受影响的属性与锁定决定
            </label>
          ) : null}
          <label className="form-field">
            操作人（本地填写）
            <input
              value={operator}
              onChange={(e) => setOperator(e.target.value)}
              required
              maxLength={128}
            />
          </label>
          <label className="form-field">
            裁定理由
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
              maxLength={2000}
            />
          </label>
          {event.version !== baseline.version ? (
            <p className="notice notice-warning">
              当前记录已有新版本，草稿仍按打开时版本提交。
            </p>
          ) : null}
          {message ? <p role="status">{message}</p> : null}
          <div className="field-decision-actions">
            <Button
              type="submit"
              disabled={
                pending ||
                uncertain ||
                !target ||
                (['adopt', 'resolve'].includes(action) && !problemIds.length)
              }
            >
              保存裁定
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={pending || uncertain}
              onClick={capture}
            >
              重新核对当前版本
            </Button>
            {uncertain ? (
              <Button
                type="button"
                variant="secondary"
                disabled={pending}
                onClick={() => void verify()}
              >
                核对本次提交结果
              </Button>
            ) : null}
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
