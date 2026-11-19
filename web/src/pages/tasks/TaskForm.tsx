import { useState } from 'react';
import { ErrorNotice } from '@/components/Feedback';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeading } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { STRATEGIC_CUSTOMER } from '@/lib/customerTier';
import { durationLabel, errorMessage } from '@/lib/utils';
import { api } from '@/services/api';

export function localTime(value: string | Date) {
  return new Date(value)
    .toLocaleString('sv-SE', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
    .replace(' ', 'T');
}
const intervals = ['5m', '15m', '1h', '1d'] as const;
const levels = Array.from({ length: 11 }, (_, i) => `L${i}`);
const split = (value: string) => value.split(/[\s,，]+/).filter(Boolean);

export function TaskForm({
  task,
  onClose,
  onSaved,
}: {
  task?: API.TaskResponse;
  onClose: () => void;
  onSaved: (task: API.TaskResponse) => void;
}) {
  const [value, setValue] = useState<API.TaskConfig>(() => ({
    name: '',
    operation: 'analyze',
    scope: 'levels',
    levels: levels.slice(6),
    uids: [],
    include_new: true,
    excluded_uids: [],
    excluded_groups: [],
    mode: 'automatic',
    frequency: '5m',
    interval: '15m',
    start_mode: 'activation',
    start: null,
    end: null,
    profile_ttl: '1h',
    lookback: '15m',
    retries: 1,
    retry_delay: '1m',
    ...task?.config,
  }));
  const [start, setStart] = useState(() =>
    localTime(task?.config.start || new Date(Date.now() - 3600000)),
  );
  const [end, setEnd] = useState(() =>
    localTime(task?.config.end || new Date()),
  );
  const [uids, setUids] = useState(value.uids?.join(', ') || '');
  const [excludedUids, setExcludedUids] = useState(
    value.excluded_uids?.join(', ') || '',
  );
  const [excludedGroups, setExcludedGroups] = useState(
    value.excluded_groups?.join(', ') || '',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<API.TaskPreview>();
  const [previewing, setPreviewing] = useState(false);
  const immutableStart = Boolean(task?.latest);
  function change(patch: Partial<API.TaskConfig>) {
    setValue((old) => ({ ...old, ...patch }));
    setPreview(undefined);
  }
  function payload(): API.TaskConfig {
    return {
      ...value,
      uids: split(uids),
      excluded_uids: split(excludedUids),
      excluded_groups: split(excludedGroups),
      start: immutableStart
        ? task?.config.start
        : value.start_mode === 'activation' && value.mode === 'automatic'
          ? value.start
          : `${start}:00+08:00`,
      end: value.mode === 'manual' ? `${end}:00+08:00` : null,
    };
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const result = task
        ? await api.updateTask(task.task_id, {
            config: payload(),
            version: task.version,
          })
        : await api.createTask(payload());
      onSaved(result);
    } catch (cause) {
      setError(errorMessage(cause));
      setBusy(false);
    }
  }
  async function inspect() {
    setPreviewing(true);
    setError('');
    try {
      setPreview(
        await api.previewTask({ ...payload(), name: value.name || '范围预览' }),
      );
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setPreviewing(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent
        className="task-sheet"
        onEscapeKeyDown={(e) => {
          if (busy) e.preventDefault();
        }}
      >
        <DialogHeading
          title={task ? '编辑任务' : '新建任务'}
          description="配置客户范围与运行方式，时间均为上海时间。"
        />
        <form onSubmit={save} className="task-form">
          <fieldset disabled={busy || previewing} className="task-form-body">
            <label htmlFor="task-field-1" className="task-field">
              任务名称
              <Input
                id="task-field-1"
                required
                maxLength={80}
                value={value.name}
                placeholder="例如：大客户持续处理"
                onChange={(e) => change({ name: e.target.value })}
              />
            </label>
            <label className="task-field">
              任务内容
              <select
                value={value.operation}
                onChange={(e) =>
                  change({
                    operation: e.target.value as API.TaskConfig['operation'],
                  })
                }
              >
                <option value="analyze">同步聊天并分析</option>
                <option value="sync">同步客户与聊天，不分析事件</option>
              </select>
            </label>
            <section>
              <h3>处理客户</h3>
              <div className="task-choice">
                <label>
                  <input
                    type="radio"
                    checked={value.scope === 'levels'}
                    onChange={() => change({ scope: 'levels' })}
                  />
                  按等级自动筛选
                </label>
                <label>
                  <input
                    type="radio"
                    checked={value.scope === 'selected'}
                    onChange={() => change({ scope: 'selected' })}
                  />
                  指定客户
                </label>
              </div>
              {value.scope === 'selected' && (
                <label htmlFor="task-field-2" className="task-field">
                  客户 UID
                  <Input
                    id="task-field-2"
                    required
                    value={uids}
                    placeholder="多个 UID 用逗号分隔"
                    onChange={(e) => setUids(e.target.value)}
                  />
                  <small>指定客户仍遵守下面的等级及排除规则。</small>
                </label>
              )}
              <label className="task-field">
                等级范围
                <select
                  aria-label="等级范围"
                  value={
                    value.levels?.join(',') === levels.slice(6).join(',')
                      ? 'large'
                      : value.levels?.join(',') === 'L9,L10'
                        ? 'super'
                        : 'custom'
                  }
                  onChange={(e) => {
                    if (e.target.value !== 'custom')
                      change({
                        levels:
                          e.target.value === 'large'
                            ? levels.slice(6)
                            : levels.slice(9),
                      });
                    else change({ levels: ['L6'] });
                  }}
                >
                  <option value="large">大客户 · L6–L10</option>
                  <option value="super">
                    超大客（L9）及{STRATEGIC_CUSTOMER}（L10）
                  </option>
                  <option value="custom">自选等级</option>
                </select>
              </label>
              <div className="task-levels">
                {levels.map((level) => (
                  <label key={level}>
                    <input
                      type="checkbox"
                      checked={value.levels?.includes(level) || false}
                      onChange={(e) =>
                        change({
                          levels: levels.filter((l) =>
                            l === level
                              ? e.target.checked
                              : value.levels?.includes(l),
                          ),
                        })
                      }
                    />
                    {level === 'L10' ? `L10 · ${STRATEGIC_CUSTOMER}` : level}
                  </label>
                ))}
              </div>
              <label className="task-check">
                <input
                  type="checkbox"
                  checked={value.include_new}
                  onChange={(e) => change({ include_new: e.target.checked })}
                />
                自动纳入符合条件的新客户
              </label>
              <details>
                <summary>排除客户 / 群（默认不排除）</summary>
                <label htmlFor="task-field-3" className="task-field">
                  排除客户 UID
                  <Input
                    id="task-field-3"
                    value={excludedUids}
                    onChange={(e) => setExcludedUids(e.target.value)}
                  />
                </label>
                <label htmlFor="task-field-4" className="task-field">
                  排除群 ID
                  <Input
                    id="task-field-4"
                    value={excludedGroups}
                    onChange={(e) => setExcludedGroups(e.target.value)}
                  />
                  <small>
                    多个标识用逗号分隔。排除群的新消息不进入分析，已有事件保留。
                  </small>
                </label>
              </details>
            </section>
            <section>
              <h3>运行方式</h3>
              <div className="task-choice">
                <label>
                  <input
                    type="radio"
                    checked={value.mode === 'manual'}
                    onChange={() =>
                      change({
                        mode: 'manual',
                        start_mode: immutableStart
                          ? value.start_mode
                          : 'specified',
                      })
                    }
                  />
                  手动执行一次
                </label>
                <label>
                  <input
                    type="radio"
                    checked={value.mode === 'automatic'}
                    onChange={() => change({ mode: 'automatic' })}
                  />
                  自动持续运行
                </label>
              </div>
              <div className="task-form-grid">
                {value.mode === 'automatic' && (
                  <label className="task-field">
                    检查频率
                    <select
                      value={value.frequency}
                      onChange={(e) =>
                        change({
                          frequency: e.target
                            .value as API.TaskConfig['frequency'],
                        })
                      }
                    >
                      {intervals.map((v) => (
                        <option key={v} value={v}>
                          每{durationLabel(v)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="task-field">
                  每轮消息范围
                  <select
                    value={value.interval}
                    onChange={(e) =>
                      change({
                        interval: e.target.value as API.TaskConfig['interval'],
                      })
                    }
                  >
                    {intervals.map((v) => (
                      <option key={v} value={v}>
                        {durationLabel(v)}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <small>同任务不重叠运行，按保存进度继续；积压时逐步补齐。</small>
            </section>
            <section>
              <h3>
                {value.mode === 'automatic' ? '首次处理起点' : '处理时间范围'}
              </h3>
              {value.mode === 'automatic' && (
                <div className="task-choice">
                  <label>
                    <input
                      type="radio"
                      disabled={immutableStart}
                      checked={value.start_mode === 'specified'}
                      onChange={() => change({ start_mode: 'specified' })}
                    />
                    从指定时间开始
                  </label>
                  <label>
                    <input
                      type="radio"
                      disabled={immutableStart}
                      checked={value.start_mode === 'activation'}
                      onChange={() =>
                        change({ start_mode: 'activation', start: null })
                      }
                    />
                    从启用时开始
                  </label>
                </div>
              )}
              <div className="task-form-grid">
                {(value.start_mode === 'specified' ||
                  value.mode === 'manual') && (
                  <label htmlFor="task-field-5" className="task-field">
                    开始时间
                    <Input
                      id="task-field-5"
                      type="datetime-local"
                      required
                      disabled={immutableStart}
                      value={start}
                      onChange={(e) => {
                        setStart(e.target.value);
                        setPreview(undefined);
                      }}
                    />
                  </label>
                )}
                {value.mode === 'manual' && (
                  <label htmlFor="task-field-6" className="task-field">
                    结束时间
                    <Input
                      id="task-field-6"
                      type="datetime-local"
                      required
                      value={end}
                      min={start}
                      onChange={(e) => {
                        setEnd(e.target.value);
                        setPreview(undefined);
                      }}
                    />
                  </label>
                )}
              </div>
              <small>
                {immutableStart
                  ? '已有执行的首次起点保留；需要换起点时请新建任务。'
                  : '首次补齐历史，自动任务追到当前后持续处理新增消息。'}
              </small>
            </section>
            <details>
              <summary>
                高级设置 <small>资料刷新 · 补漏回溯 · 失败重试</small>
              </summary>
              <div className="task-form-grid">
                <label className="task-field">
                  客户资料有效期
                  <select
                    value={value.profile_ttl}
                    onChange={(e) =>
                      change({
                        profile_ttl: e.target
                          .value as API.TaskConfig['profile_ttl'],
                      })
                    }
                  >
                    {['5m', '15m', '1h', '6h', '1d'].map((v) => (
                      <option key={v} value={v}>
                        {durationLabel(v)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="task-field">
                  取数补漏回溯
                  <select
                    value={value.lookback}
                    onChange={(e) =>
                      change({
                        lookback: e.target.value as API.TaskConfig['lookback'],
                      })
                    }
                  >
                    {['5m', '15m', '1h', '6h', '1d', '2d'].map((v) => (
                      <option key={v} value={v}>
                        {durationLabel(v)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="task-field">
                  失败重试次数
                  <select
                    value={value.retries}
                    onChange={(e) =>
                      change({ retries: Number(e.target.value) })
                    }
                  >
                    {[0, 1, 2, 3].map((n) => (
                      <option key={n} value={n}>
                        {n === 0 ? '不自动重试' : `${n} 次`}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="task-field">
                  重试间隔
                  <select
                    value={value.retry_delay}
                    onChange={(e) =>
                      change({
                        retry_delay: e.target
                          .value as API.TaskConfig['retry_delay'],
                      })
                    }
                  >
                    {['1m', '5m', '15m'].map((v) => (
                      <option key={v} value={v}>
                        {durationLabel(v)}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </details>
            <div className="task-preview">
              <Button onClick={inspect} disabled={previewing}>
                {previewing ? '正在查询' : '预览匹配客户'}
              </Button>
              {preview && (
                <details>
                  <summary>当前匹配 {preview.total} 位客户</summary>
                  <p>{preview.notice}</p>
                  <ul>
                    {preview.items.map((p) => (
                      <li key={p.uid}>
                        客户 {p.uid} ·{' '}
                        {p.level === 'L10'
                          ? `L10 · ${STRATEGIC_CUSTOMER}`
                          : p.level}{' '}
                        · {p.tam || '负责人未知'}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          </fieldset>
          <footer className="task-form-footer">
            {error && <ErrorNotice message={error} />}
            <small>
              {task
                ? '配置修改将在下一轮执行时生效。'
                : '保存后在任务中心点击“启用”或“立即执行”。'}
            </small>
            <div>
              <Button disabled={busy} onClick={onClose}>
                取消
              </Button>
              <Button
                variant="primary"
                type="submit"
                disabled={busy || previewing}
              >
                {busy ? '正在保存' : '保存配置'}
              </Button>
            </div>
          </footer>
        </form>
      </DialogContent>
    </Dialog>
  );
}
