import {
  ArrowRight,
  Check,
  Clock3,
  Crown,
  ListChecks,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
} from 'lucide-react';
import { useState } from 'react';
import { Link, useSearchParams } from 'umi';
import { EmptyState, ErrorNotice, LoadingRows } from '@/components/Feedback';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { STRATEGIC_CUSTOMER } from '@/lib/customerTier';
import { useResource } from '@/lib/useResource';
import {
  durationLabel,
  errorMessage,
  formatRange,
  formatTime,
} from '@/lib/utils';
import { api } from '@/services/api';
import { TaskForm } from './TaskForm';
import './tasks.css';

const statuses: Record<string, string> = {
  queued: '等待执行',
  running: '执行中',
  succeeded: '已完成',
  partial: '部分完成',
  failed: '失败',
  skipped: '已跳过',
};
const active = (status?: string) => status === 'queued' || status === 'running';
const scopeLabel = (c: API.TaskConfig) =>
  c.scope === 'selected'
    ? `指定 ${c.uids?.length || 0} 位客户`
    : c.levels?.join(',') === 'L6,L7,L8,L9,L10'
      ? '大客户 · L6–L10'
      : c.levels?.join(',') === 'L9,L10'
        ? `超大客（L9）及${STRATEGIC_CUSTOMER}（L10）`
        : `自选等级 · ${c.levels?.join('、')}`;
function Status({ status }: { status: string }) {
  return (
    <Badge className={`task-status ${status}`}>
      {status === 'running' && <RefreshCw size={12} className="spinning" />}
      {statuses[status] || status}
    </Badge>
  );
}

/** 配置和执行分别读取，切换任务时不会残留上一任务的进度。 */
export default function TasksPage() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [form, setForm] = useState<API.TaskResponse | 'new'>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [tab, setTab] = useState('progress');
  const tasks = useResource('task-center', api.tasks, 4000);
  const items = tasks.data?.items || [];
  const visible = items.filter(
    (t) =>
      t.config.name.toLowerCase().includes(search.toLowerCase()) &&
      (filter === 'all' ||
        (filter === 'automatic'
          ? t.config.mode === 'automatic'
          : t.config.mode === 'manual')),
  );
  const task =
    items.find((t) => t.task_id === params.get('task')) || visible[0];
  const history = useResource(
    task && tab === 'history' ? `task-history:${task.task_id}` : null,
    (signal) => api.taskExecutions(task?.task_id || '', signal),
    4000,
  );
  const executionId =
    params.get('execution') || task?.latest?.execution_id || '';
  const detail = useResource(
    task && executionId && tab === 'progress'
      ? `task-execution:${task.task_id}:${executionId}`
      : null,
    (signal) => api.taskExecution(executionId, signal),
    3000,
    (v) => active(v.status),
  );
  const execution =
    detail.data?.task_id === task?.task_id ? detail.data : undefined;
  function select(taskId: string, execution?: string) {
    setParams({ task: taskId, ...(execution ? { execution } : {}) });
    setError('');
    setTab('progress');
  }
  async function action(value: 'run' | 'pause' | 'resume') {
    if (!task || busy) return;
    setBusy(true);
    setError('');
    try {
      await api.taskAction(task.task_id, value);
      tasks.refresh();
      history.refresh();
      detail.refresh();
      if (value === 'run') select(task.task_id);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="page task-page">
      <header className="page-header">
        <div>
          <h1>任务中心</h1>
          {tasks.data && (
            <p>
              共 {items.length} 个任务 ·{' '}
              {
                items.filter(
                  (item) => item.config.mode === 'automatic' && item.enabled,
                ).length
              }{' '}
              个自动任务已启用
            </p>
          )}
        </div>
        <div className="page-header-actions">
          <Button variant="primary" onClick={() => setForm('new')}>
            <Plus size={16} />
            新建任务
          </Button>
        </div>
      </header>
      {tasks.error && (
        <ErrorNotice message={tasks.error} retry={tasks.refresh} />
      )}
      <div className="task-layout">
        <aside className="task-sidebar" aria-label="任务列表">
          <div className="task-list-tools">
            <div className="task-search">
              <Search size={15} />
              <Input
                aria-label="搜索任务"
                placeholder="搜索任务名称"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <fieldset className="task-list-filters" aria-label="任务类型">
              {[
                ['all', '全部'],
                ['automatic', '自动任务'],
                ['manual', '手动任务'],
              ].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={filter === value}
                  onClick={() => setFilter(value)}
                >
                  {label}
                </button>
              ))}
            </fieldset>
          </div>
          <div className="task-list">
            {tasks.loading && !tasks.data ? (
              <LoadingRows count={3} />
            ) : visible.length ? (
              visible.map((item) => (
                <button
                  type="button"
                  key={item.task_id}
                  className={`task-list-item ${item.task_id === task?.task_id ? 'selected' : ''}`}
                  aria-pressed={item.task_id === task?.task_id}
                  onClick={() => select(item.task_id)}
                >
                  <div className="task-item-heading">
                    <strong>{item.config.name}</strong>
                    <span
                      className={`task-enabled ${item.enabled ? 'on' : ''}`}
                    >
                      {item.config.mode === 'manual'
                        ? '手动'
                        : item.enabled
                          ? '已启用'
                          : '已暂停'}
                    </span>
                  </div>
                  <p>{scopeLabel(item.config)}</p>
                  <div className="task-item-meta">
                    <span>
                      <Clock3 size={12} />
                      {item.config.mode === 'automatic'
                        ? `每 ${durationLabel(item.config.frequency || '5m')}`
                        : '按指定范围'}
                    </span>
                    {item.latest ? (
                      <Status status={item.latest.status} />
                    ) : (
                      <span>尚未执行</span>
                    )}
                  </div>
                </button>
              ))
            ) : (
              <div className="task-list-empty">
                {items.length
                  ? '没有匹配的任务'
                  : '还没有任务，先创建一个配置。'}
              </div>
            )}
          </div>
          <div className="task-list-footer">任务列表每 4 秒刷新</div>
        </aside>
        <section className="task-detail" aria-label="任务详情">
          {task ? (
            <>
              <div className="task-detail-heading">
                <div>
                  <div className="task-kicker">
                    {task.config.operation === 'sync'
                      ? '客户与聊天同步'
                      : '聊天同步与事件分析'}
                  </div>
                  <h2>{task.config.name}</h2>
                  <div className="task-detail-meta">
                    <Badge>{scopeLabel(task.config)}</Badge>
                    <span>
                      {task.config.mode === 'automatic'
                        ? `每 ${durationLabel(task.config.frequency || '5m')} 检查`
                        : '手动执行'}{' '}
                      · 每轮 {durationLabel(task.config.interval || '5m')}
                    </span>
                  </div>
                </div>
                <div className="task-actions">
                  <Button
                    size="small"
                    disabled={busy}
                    onClick={() => setForm(task)}
                  >
                    <Pencil size={14} />
                    编辑
                  </Button>
                  {task.config.mode === 'automatic' && (
                    <Button
                      size="small"
                      disabled={busy}
                      onClick={() => action(task.enabled ? 'pause' : 'resume')}
                    >
                      {task.enabled ? <Pause size={14} /> : <Play size={14} />}
                      {task.enabled ? '暂停' : '启用'}
                    </Button>
                  )}
                  <Button
                    size="small"
                    variant="primary"
                    disabled={
                      busy ||
                      active(task.latest?.status) ||
                      (task.config.start_mode === 'activation' &&
                        !task.config.start)
                    }
                    onClick={() => action('run')}
                  >
                    <Play size={14} />
                    立即执行
                  </Button>
                </div>
              </div>
              <div className="task-schedule-note">
                <Clock3 size={14} />
                {task.enabled
                  ? `自动运行已启用${task.next_run_at && !active(task.latest?.status) ? ` · 下次检查 ${formatTime(task.next_run_at)}` : ''}，关闭网页仍会继续。`
                  : task.config.mode === 'automatic'
                    ? `自动运行已暂停${active(task.latest?.status) ? '，当前这一轮会继续完成。' : '，启用后按保存进度继续。'}`
                    : '点击立即执行后在后台运行，关闭网页不影响。'}
                {task.cursor_at && (
                  <span>已推进至 {formatTime(task.cursor_at)}</span>
                )}
              </div>
              {error && <ErrorNotice message={error} />}
              <Tabs value={tab} onValueChange={setTab}>
                <TabsList aria-label="任务详情分类">
                  <TabsTrigger value="progress">执行进度</TabsTrigger>
                  <TabsTrigger value="config">任务配置</TabsTrigger>
                  <TabsTrigger value="history">执行记录</TabsTrigger>
                </TabsList>
                <TabsContent value="progress">
                  {detail.error && (
                    <ErrorNotice
                      message={
                        execution
                          ? `刷新进度失败，当前显示上次结果。${detail.error}`
                          : detail.error
                      }
                      retry={detail.refresh}
                    />
                  )}
                  {execution ? (
                    <ExecutionProgress execution={execution} />
                  ) : detail.error ? null : executionId && detail.loading ? (
                    <LoadingRows />
                  ) : (
                    <EmptyState
                      title="等待首次执行"
                      description={
                        task.config.mode === 'automatic'
                          ? '配置已保存。启用后，系统会按所选范围持续同步与处理。'
                          : '配置已保存。点击“立即执行”开始本次处理。'
                      }
                      icon={<ListChecks size={26} />}
                    />
                  )}
                </TabsContent>
                <TabsContent value="config">
                  <ConfigSummary config={task.config} />
                  <p className="task-muted">
                    修改后的配置用于下一轮执行，已开始的执行保留当时的配置。
                  </p>
                </TabsContent>
                <TabsContent value="history">
                  {history.error && (
                    <ErrorNotice
                      message={history.error}
                      retry={history.refresh}
                    />
                  )}
                  {history.loading && !history.data ? (
                    <LoadingRows />
                  ) : history.data?.items.length ? (
                    <>
                      <p className="task-muted">
                        最近 {history.data.total} 次执行 ·
                        点击查看当次进度及配置
                      </p>
                      <div className="task-table-wrap">
                        <table className="task-table">
                          <thead>
                            <tr>
                              <th>开始时间</th>
                              <th>结束时间</th>
                              <th>状态</th>
                              <th>
                                <span className="sr-only">操作</span>
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {history.data.items.map((row) => (
                              <tr key={row.execution_id}>
                                <td>{formatTime(row.created_at)}</td>
                                <td>{formatTime(row.finished_at)}</td>
                                <td>
                                  <Status status={row.status} />
                                </td>
                                <td>
                                  <Button
                                    variant="ghost"
                                    size="small"
                                    onClick={() =>
                                      select(task.task_id, row.execution_id)
                                    }
                                  >
                                    查看 <ArrowRight size={14} />
                                  </Button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  ) : (
                    <EmptyState
                      title="还没有执行记录"
                      description="每次执行都会保存状态、结果和当时的配置。"
                    />
                  )}
                </TabsContent>
              </Tabs>
            </>
          ) : (
            <EmptyState
              title="让大客户处理持续运行"
              description="选择客户范围与处理频率，再随时查看执行进度。"
              icon={<Settings2 size={28} />}
              action={
                <Button variant="primary" onClick={() => setForm('new')}>
                  新建任务
                </Button>
              }
            />
          )}
        </section>
      </div>
      {form && (
        <TaskForm
          task={form === 'new' ? undefined : form}
          onClose={() => setForm(undefined)}
          onSaved={(saved) => {
            setForm(undefined);
            tasks.refresh();
            select(saved.task_id);
          }}
        />
      )}
    </main>
  );
}

function ExecutionProgress({
  execution: e,
}: {
  execution: API.TaskExecutionResponse;
}) {
  const included = e.customers.filter((c) => c.status !== 'skipped');
  const completed = included.filter(
    (c) => c.status === 'succeeded' || c.status === 'failed',
  ).length;
  const finished = !active(e.status);
  const discovery = e.stage === 'discovery';
  const current = discovery ? e.sync_completed : completed;
  const total = discovery ? e.sync_total : included.length;
  const percent = total ? Math.round((current / total) * 100) : null;
  return (
    <div className="task-execution">
      <div className="task-execution-heading">
        <div>
          <h3>
            {finished ? '执行结果' : '本次执行'} <Status status={e.status} />
          </h3>
          <p>
            {formatRange(e.start, e.end)} ·{' '}
            {e.end.endsWith('00:00:00+08:00')
              ? '按所选窗口处理'
              : '不含结束时刻'}
          </p>
        </div>
        <span className="task-muted">开始于 {formatTime(e.created_at)}</span>
      </div>
      {e.error && <ErrorNotice message={e.error} />}
      <section
        className="task-progress-band"
        aria-label={discovery ? '同步消息窗口' : '客户处理进度'}
      >
        <div className="task-progress-number">
          <span>{discovery ? '同步消息窗口' : '客户处理进度'}</span>
          <strong>
            {percent === null ? (
              '—'
            ) : (
              <>
                {percent}
                <small>%</small>
              </>
            )}
          </strong>
        </div>
        <div className="task-progress-track">
          <div className="task-progress-label">
            <strong>
              {current} / {total} {discovery ? '个窗口' : '位客户'}
            </strong>
            <span>
              {finished
                ? '本次执行已结束'
                : e.status === 'queued'
                  ? '等待后台处理'
                  : total
                    ? '进度自动更新'
                    : '正在准备本次执行范围'}
            </span>
          </div>
          <progress
            className="task-progress"
            aria-label="本次执行进度"
            value={current}
            max={Math.max(1, total)}
          />
        </div>
      </section>
      <ol className="task-phases" aria-label="执行阶段">
        {[
          ['取数', e.phases?.fetch],
          ['客户识别', e.phases?.discovery],
          ['分析', e.phases?.analysis],
        ].map(([label, phase], index) => (
          <li
            key={label}
            aria-current={phase === 'running' ? 'step' : undefined}
            className={
              phase === 'running' ? 'current' : phase === 'done' ? 'done' : ''
            }
          >
            <span className="task-phase-index" aria-hidden="true">
              {phase === 'done' ? <Check size={13} /> : index + 1}
            </span>
            <strong>{label}</strong>
            <small>
              {phase === 'running'
                ? '进行中'
                : phase === 'done'
                  ? '已有进展'
                  : '等待'}
            </small>
          </li>
        ))}
      </ol>
      <p className="task-muted">
        {[
          e.capacity_notice,
          finished &&
            (e.status === 'succeeded'
              ? '结果已保存。'
              : '请查看未完成项与原因。'),
          e.source_errors > 0 && `${e.source_errors} 次资料或群归属待确认`,
          e.oldest_wait_seconds != null &&
            `最早排队 ${e.oldest_wait_seconds} 秒`,
        ]
          .filter(Boolean)
          .join(' · ')}
      </p>
      <dl className="task-metrics" aria-label="本次执行统计">
        {[
          ['匹配客户', included.length],
          ['处理消息', e.processed],
          ['分析排队', e.backlog],
          ['分析名额占用', `${e.capacity_used} / ${e.capacity_total}`],
          ['隔离', e.isolated_count],
          ['新建事件', e.created_events],
          ['更新事件', e.updated_events],
        ].map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="task-section-heading">
        <h3>客户执行明细</h3>
        <span>共 {included.length} 位参与处理</span>
      </div>
      {included.length ? (
        <div className="task-table-wrap">
          <table className="task-table">
            <thead>
              <tr>
                <th>客户</th>
                <th>状态</th>
                <th>阶段</th>
                <th>窗口</th>
                <th>消息</th>
                <th>事件 新增 / 更新</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              {included.map((c) => (
                <tr key={c.uid}>
                  <td>
                    <strong>客户 {c.uid}</strong>
                    <small>
                      {(c.level === 'L9' || c.level === 'L10') && (
                        <Crown size={12} className="task-crown" />
                      )}
                      {c.level === 'L10'
                        ? `L10 · ${STRATEGIC_CUSTOMER}`
                        : c.level || '等级待确认'}
                    </small>
                  </td>
                  <td>
                    <Status status={c.status} />
                    {c.reason && (
                      <small className="task-reason">{c.reason}</small>
                    )}
                    {c.incomplete && (
                      <small className="task-reason">结果可能不完整</small>
                    )}
                  </td>
                  <td>
                    {c.phase === 'live' ? '实时' : '追赶'}
                    {c.analyzed_until && (
                      <small>已处理至 {formatTime(c.analyzed_until)}</small>
                    )}
                  </td>
                  <td>
                    {c.status === 'skipped'
                      ? '—'
                      : `${c.completed_windows} / ${c.total_windows}`}
                  </td>
                  <td>{c.processed}</td>
                  <td>
                    {c.created} / {c.updated}
                  </td>
                  <td>
                    {c.run_id && (
                      <Link
                        className="task-link"
                        to={`/tasks/run-detail?${new URLSearchParams({ uid: c.uid, run: c.run_id, stream: c.stream || '' })}`}
                      >
                        详情
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="task-list-empty">
          {discovery
            ? '完成同步后按最新等级与排除规则筛选客户。'
            : '本次范围没有可处理客户。'}
        </div>
      )}
      <p className="task-muted">
        消息和窗口计数包含重试；事件按事件编号去重统计。
      </p>
      <details className="task-snapshot">
        <summary>查看本次执行的配置</summary>
        <ConfigSummary config={e.config} />
      </details>
    </div>
  );
}

function ConfigSummary({ config: c }: { config: API.TaskConfig }) {
  const entries = [
    [
      '任务内容',
      c.operation === 'sync' ? '同步客户与聊天' : '同步聊天并分析事件',
    ],
    ['客户范围', scopeLabel(c)],
    ['等级筛选', c.levels?.join('、')],
    ['指定客户', c.scope === 'selected' ? c.uids?.join('、') : '按等级筛选'],
    ['自动纳入新客户', c.include_new ? '开启' : '关闭，保持保存时的名单'],
    ['排除客户', c.excluded_uids?.join('、') || '无'],
    ['排除群', c.excluded_groups?.join('、') || '无'],
    [
      '运行方式',
      c.mode === 'automatic'
        ? `自动 · 每 ${durationLabel(c.frequency || '5m')} 检查`
        : '手动执行',
    ],
    ['每轮消息范围', durationLabel(c.interval || '5m')],
    ['首次起点', c.start ? formatTime(c.start) : '首次启用时'],
    ...(c.mode === 'manual' ? [['结束时间', formatTime(c.end)]] : []),
    ['资料有效期', durationLabel(c.profile_ttl || '1h')],
    ['取数补漏回溯', durationLabel(c.lookback || '15m')],
    [
      '失败重试',
      `${c.retries || 0} 次 · 间隔 ${durationLabel(c.retry_delay || '1m')}`,
    ],
  ];
  return (
    <dl className="task-config-summary">
      {entries.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
