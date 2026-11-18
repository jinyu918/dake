import { AlertTriangle, Lock, SlidersHorizontal, Unlock } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '@/services/api';
import { ItemPriorityDetails } from './ClassificationDetails';
import { ErrorNotice } from './Feedback';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from './ui/dialog';

const SELECTABLE_LABELS = [
  '商机',
  '流失',
  '排障',
  '资源需求',
  '功能需求',
  '商务需求',
  '其他',
] as const;

const LIFECYCLE_STATUSES = [
  '跟进中',
  '等待中',
  '已解决',
  '已取消',
  '状态未明',
] as const;

const WAITING_REASONS = ['等待客户', '等待内部支撑', '等待其他方'] as const;
const RESOLVED_REASONS = [
  '客户确认恢复',
  '支撑声明已修复',
  '已答复或已交付',
] as const;
const CANCELLED_REASONS = ['已拒绝', '已撤回', '确认不成立'] as const;
const PRIORITY_OPTIONS = ['P0', 'P1', 'P2', 'P3', '待评估'] as const;

const OPERATOR_STORAGE_KEY = 'dake_manual_operator';

function createOperationId() {
  const rand = Math.random().toString(36).slice(2, 10);
  return `op-${Date.now()}-${rand}`;
}

function sameList(left: string[], right: string[]) {
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}

/** 面向分类、状态、优先级、跟进人四项核心管理字段的轻量人工纠偏与加解锁弹窗。 */
export function ManualDecisionDialog({
  event,
  onUpdated,
}: {
  event: API.EventResponse;
  onUpdated: (updated: API.EventResponse) => void;
}) {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [operationId, setOperationId] = useState(createOperationId);

  const controls = event.manual_controls || {};
  const initialTypes =
    event.classification_status === '待分类' || event.type === '待分类'
      ? event.types || []
      : event.types?.length
        ? event.types
        : [event.type];

  const [types, setTypes] = useState<string[]>(initialTypes);
  const [typesLocked, setTypesLocked] = useState(
    Boolean(controls.types?.locked),
  );
  const [status, setStatus] = useState<string>(
    event.lifecycle_status || '状态未明',
  );
  const [waitingReason, setWaitingReason] = useState<string>(
    event.waiting_reason || '等待内部支撑',
  );
  const [resolution, setResolution] = useState<string>(
    event.resolution ||
      (event.types?.includes('排障') || event.type === '排障'
        ? '支撑声明已修复'
        : '已答复或已交付'),
  );
  const [statusLocked, setStatusLocked] = useState(
    Boolean(controls.status?.locked),
  );
  const [priority, setPriority] = useState<string>(event.priority || '待评估');
  const [priorityLocked, setPriorityLocked] = useState(
    Boolean(controls.priority?.locked),
  );
  const [assigneesText, setAssigneesText] = useState(
    (event.assignees || []).join('、'),
  );
  const [assigneesLocked, setAssigneesLocked] = useState(
    Boolean(controls.assignees?.locked),
  );
  const [operator, setOperator] = useState(() => {
    try {
      return window.localStorage.getItem(OPERATOR_STORAGE_KEY) || '值班人员';
    } catch {
      return '值班人员';
    }
  });
  const [reason, setReason] = useState('');

  useEffect(() => {
    if (!open) return;
    setError('');
    setOperationId(createOperationId());
    setTypes(
      event.classification_status === '待分类' || event.type === '待分类'
        ? event.types || []
        : event.types?.length
          ? event.types
          : [event.type],
    );
    setTypesLocked(Boolean(event.manual_controls?.types?.locked));
    setStatus(event.lifecycle_status || '状态未明');
    setWaitingReason(event.waiting_reason || '等待内部支撑');
    setResolution(
      event.resolution ||
        (event.types?.includes('排障') || event.type === '排障'
          ? '支撑声明已修复'
          : '已答复或已交付'),
    );
    setStatusLocked(Boolean(event.manual_controls?.status?.locked));
    setPriority(event.priority || '待评估');
    setPriorityLocked(Boolean(event.manual_controls?.priority?.locked));
    setAssigneesText((event.assignees || []).join('、'));
    setAssigneesLocked(Boolean(event.manual_controls?.assignees?.locked));
    setReason('');
  }, [open, event]);

  function markFormDirty() {
    setOperationId(createOperationId());
  }

  function toggleLabel(label: string) {
    markFormDirty();
    setTypes((prev) => {
      const next = prev.includes(label)
        ? prev.filter((item) => item !== label)
        : [...prev, label];
      setTypesLocked(true);
      return next;
    });
  }

  async function resolveSingleConflict(
    field: API.ManualFieldAction['field'],
    action: 'adopt_conflict' | 'dismiss_conflict',
  ) {
    const cleanOperator = operator.trim() || '值班人员';
    const autoReason =
      reason.trim() ||
      (action === 'adopt_conflict'
        ? `人工核对后采纳 ${field} 的新证据建议`
        : `人工核对后维持 ${field} 的当前锁定结论`);
    setSubmitting(true);
    setError('');
    try {
      const response = await api.submitManualDecision(event.event_id, {
        uid: event.uid,
        stream: event.stream,
        expected_version: event.version ?? 1,
        operation_id: createOperationId(),
        operator: cleanOperator,
        reason: autoReason,
        actions: [{ field, action }],
      });
      onUpdated(response.event);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交人工裁定失败');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSubmit(formEvent: React.FormEvent) {
    formEvent.preventDefault();
    const cleanOperator = operator.trim();
    const cleanReason = reason.trim();
    if (!cleanOperator || !cleanReason) {
      setError('请填写操作人和裁定说明。');
      return;
    }

    const actions: API.ManualFieldAction[] = [];

    if (!sameList(types, initialTypes)) {
      actions.push({
        field: 'types',
        action: 'set',
        types,
        locked: typesLocked,
      });
    } else if (typesLocked !== Boolean(controls.types?.locked)) {
      actions.push({
        field: 'types',
        action: typesLocked ? 'lock' : 'unlock',
      });
    }

    const effectiveWaiting = status === '等待中' ? waitingReason : null;
    const effectiveResolution =
      status === '已解决' || status === '已取消' ? resolution : null;
    const statusChanged =
      status !== (event.lifecycle_status || '状态未明') ||
      (effectiveWaiting || null) !== (event.waiting_reason || null) ||
      (effectiveResolution || null) !== (event.resolution || null);
    if (statusChanged) {
      actions.push({
        field: 'status',
        action: 'set',
        status,
        waiting_reason: effectiveWaiting,
        resolution: effectiveResolution,
        locked: statusLocked,
      });
    } else if (statusLocked !== Boolean(controls.status?.locked)) {
      actions.push({
        field: 'status',
        action: statusLocked ? 'lock' : 'unlock',
      });
    }

    if (priority !== (event.priority || '待评估')) {
      actions.push({
        field: 'priority',
        action: 'set',
        priority,
        locked: priorityLocked,
      });
    } else if (priorityLocked !== Boolean(controls.priority?.locked)) {
      actions.push({
        field: 'priority',
        action: priorityLocked ? 'lock' : 'unlock',
      });
    }

    const parsedAssignees = [
      ...new Set(
        assigneesText
          .split(/[、,，\s]+/)
          .map((item) => item.trim())
          .filter(Boolean),
      ),
    ];
    if (!sameList(parsedAssignees, event.assignees || [])) {
      actions.push({
        field: 'assignees',
        action: 'set',
        assignees: parsedAssignees,
        locked: assigneesLocked,
      });
    } else if (assigneesLocked !== Boolean(controls.assignees?.locked)) {
      actions.push({
        field: 'assignees',
        action: assigneesLocked ? 'lock' : 'unlock',
      });
    }

    if (!actions.length) {
      setError('尚未修改任何字段取值或锁定状态。');
      return;
    }

    try {
      window.localStorage.setItem(OPERATOR_STORAGE_KEY, cleanOperator);
    } catch {
      // 忽略受限环境下的本地存储异常
    }

    setSubmitting(true);
    setError('');
    try {
      const response = await api.submitManualDecision(event.event_id, {
        uid: event.uid,
        stream: event.stream,
        expected_version: event.version ?? 1,
        operation_id: operationId,
        operator: cleanOperator,
        reason: cleanReason,
        actions,
      });
      onUpdated(response.event);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交人工裁定失败');
    } finally {
      setSubmitting(false);
    }
  }

  const hasConflict = Object.values(controls).some((item) =>
    Boolean(item?.pending_conflict),
  );

  return (
    <>
      <button
        type="button"
        className={`manual-trigger-btn ${hasConflict ? 'has-conflict' : ''}`}
        onClick={() => setOpen(true)}
      >
        <SlidersHorizontal size={13} />
        {hasConflict ? '处理锁定冲突' : '人工纠偏'}
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="run-dialog manual-decision-dialog">
          <DialogTitle>核心字段人工纠偏与锁定</DialogTitle>
          <DialogDescription>
            修改后默认加锁，后续增量分析若出现不同证据结论将保留人工值并提示待核对（当前版本{' '}
            v{event.version ?? 1}）。
          </DialogDescription>

          {error ? <ErrorNotice message={error} /> : null}

          {hasConflict ? (
            <section
              className="manual-conflict-box"
              aria-label="待核对锁定冲突"
            >
              {(['types', 'status', 'priority', 'assignees'] as const).map(
                (field) => {
                  const conflict = controls[field]?.pending_conflict;
                  if (!conflict) return null;
                  return (
                    <div key={field} className="manual-conflict-row">
                      <p>
                        <AlertTriangle size={14} />
                        <span>
                          {String(
                            conflict.summary || `${field} 存在新证据冲突`,
                          )}
                        </span>
                      </p>
                      <div className="manual-conflict-actions">
                        <button
                          type="button"
                          disabled={submitting}
                          onClick={() =>
                            resolveSingleConflict(field, 'adopt_conflict')
                          }
                        >
                          采纳新建议
                        </button>
                        <button
                          type="button"
                          disabled={submitting}
                          onClick={() =>
                            resolveSingleConflict(field, 'dismiss_conflict')
                          }
                        >
                          维持人工值
                        </button>
                      </div>
                    </div>
                  );
                },
              )}
            </section>
          ) : null}

          <form className="manual-form" onSubmit={handleSubmit}>
            <div className="manual-field-block">
              <div className="manual-field-head">
                <span className="manual-field-label">
                  分类标签（可多选，全不选为「待分类」）
                </span>
                <button
                  type="button"
                  className={`lock-toggle ${typesLocked ? 'is-locked' : ''}`}
                  onClick={() => {
                    markFormDirty();
                    setTypesLocked((v) => !v);
                  }}
                >
                  {typesLocked ? <Lock size={12} /> : <Unlock size={12} />}
                  {typesLocked ? '已锁定' : '未锁定'}
                </button>
              </div>
              <div className="manual-label-chips">
                {SELECTABLE_LABELS.map((label) => {
                  const selected = types.includes(label);
                  return (
                    <button
                      key={label}
                      type="button"
                      aria-pressed={selected}
                      className={`manual-chip ${selected ? 'is-selected' : ''}`}
                      onClick={() => toggleLabel(label)}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="form-grid">
              <label className="form-field">
                <span className="manual-field-head">
                  <span>生命周期状态</span>
                  <button
                    type="button"
                    className={`lock-toggle ${statusLocked ? 'is-locked' : ''}`}
                    onClick={(e) => {
                      e.preventDefault();
                      markFormDirty();
                      setStatusLocked((v) => !v);
                    }}
                  >
                    {statusLocked ? <Lock size={12} /> : <Unlock size={12} />}
                    {statusLocked ? '已锁定' : '未锁定'}
                  </button>
                </span>
                <select
                  value={status}
                  onChange={(e) => {
                    const next = e.target.value;
                    markFormDirty();
                    setStatus(next);
                    setStatusLocked(true);
                    if (next === '已解决') {
                      setResolution('已答复或已交付');
                    } else if (next === '已取消') {
                      setResolution('已撤回');
                    }
                  }}
                >
                  {LIFECYCLE_STATUSES.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>

              {status === '等待中' ? (
                <label className="form-field">
                  <span>等待方</span>
                  <select
                    value={waitingReason}
                    onChange={(e) => {
                      markFormDirty();
                      setWaitingReason(e.target.value);
                      setStatusLocked(true);
                    }}
                  >
                    {WAITING_REASONS.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}

              {status === '已解决' ? (
                <label className="form-field">
                  <span>解决方式</span>
                  <select
                    value={
                      (RESOLVED_REASONS as readonly string[]).includes(
                        resolution,
                      )
                        ? resolution
                        : '已答复或已交付'
                    }
                    onChange={(e) => {
                      markFormDirty();
                      setResolution(e.target.value);
                      setStatusLocked(true);
                    }}
                  >
                    {RESOLVED_REASONS.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}

              {status === '已取消' ? (
                <label className="form-field">
                  <span>取消原因</span>
                  <select
                    value={
                      (CANCELLED_REASONS as readonly string[]).includes(
                        resolution,
                      )
                        ? resolution
                        : '已撤回'
                    }
                    onChange={(e) => {
                      markFormDirty();
                      setResolution(e.target.value);
                      setStatusLocked(true);
                    }}
                  >
                    {CANCELLED_REASONS.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}

              <label className="form-field">
                <span className="manual-field-head">
                  <span>业务优先级</span>
                  <button
                    type="button"
                    className={`lock-toggle ${priorityLocked ? 'is-locked' : ''}`}
                    onClick={(e) => {
                      e.preventDefault();
                      markFormDirty();
                      setPriorityLocked((v) => !v);
                    }}
                  >
                    {priorityLocked ? <Lock size={12} /> : <Unlock size={12} />}
                    {priorityLocked ? '已锁定' : '未锁定'}
                  </button>
                </span>
                <select
                  value={priority}
                  onChange={(e) => {
                    markFormDirty();
                    setPriority(e.target.value);
                    setPriorityLocked(true);
                  }}
                >
                  {PRIORITY_OPTIONS.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>

              <ItemPriorityDetails event={event} />

              <label className="form-field">
                <span className="manual-field-head">
                  <span>跟进人（顿号或逗号分隔）</span>
                  <button
                    type="button"
                    className={`lock-toggle ${assigneesLocked ? 'is-locked' : ''}`}
                    onClick={(e) => {
                      e.preventDefault();
                      markFormDirty();
                      setAssigneesLocked((v) => !v);
                    }}
                  >
                    {assigneesLocked ? (
                      <Lock size={12} />
                    ) : (
                      <Unlock size={12} />
                    )}
                    {assigneesLocked ? '已锁定' : '未锁定'}
                  </button>
                </span>
                <input
                  value={assigneesText}
                  placeholder="例如：张三、李四"
                  onChange={(e) => {
                    markFormDirty();
                    setAssigneesText(e.target.value);
                    setAssigneesLocked(true);
                  }}
                />
              </label>
            </div>

            <div className="form-grid">
              <label className="form-field">
                <span>操作人</span>
                <input
                  required
                  value={operator}
                  placeholder="填写操作人姓名或账号"
                  onChange={(e) => {
                    markFormDirty();
                    setOperator(e.target.value);
                  }}
                />
              </label>
              <label className="form-field">
                <span>裁定说明</span>
                <input
                  required
                  value={reason}
                  placeholder="简要记录人工调整依据"
                  onChange={(e) => {
                    markFormDirty();
                    setReason(e.target.value);
                  }}
                />
              </label>
            </div>

            <div className="dialog-actions">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setOpen(false)}
                disabled={submitting}
              >
                取消
              </Button>
              <Button type="submit" disabled={submitting}>
                {submitting ? '保存中…' : '保存裁定'}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
