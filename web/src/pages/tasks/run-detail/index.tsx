import { ArrowUpRight, FileClock, RefreshCw } from 'lucide-react';
import { Link, useSearchParams } from 'umi';
import { EmptyState, ErrorNotice, LoadingRows } from '@/components/Feedback';
import { Button } from '@/components/ui/button';
import { useWorkspace } from '@/layouts/Workspace';
import { useResource } from '@/lib/useResource';
import { durationLabel, formatRange, formatTime } from '@/lib/utils';
import { api } from '@/services/api';
import { CyclePanel } from './CyclePanel';
import { currentRun, runStatuses as statuses } from './progress';

/** 列表与选中任务都从服务端恢复，关闭或刷新页面不会丢失执行进度。 */
export default function RunsPage() {
  const workspace = useWorkspace();
  const [params, setParams] = useSearchParams();
  const uid = params.get('uid') || workspace.data?.uid || '';
  const customer = workspace.data?.customers.find((item) => item.uid === uid);
  const stream = params.get('stream') || customer?.stream || '';
  const runs = useResource(
    uid && stream ? `runs:${uid}:${stream}` : null,
    (signal) => api.scopedRuns(uid, stream, signal),
    4000,
  );
  const latest = currentRun(runs.data?.items || []);
  const runId = params.get('run') || latest?.run_id || '';
  const detail = useResource(
    runId && uid && stream ? `run:${uid}:${stream}:${runId}` : null,
    async (signal) => ({
      ...(await api.scopedRun(runId, uid, stream, signal)),
      observedAt: new Date().toISOString(),
    }),
    4000,
    (value) => value.status === 'queued' || value.status === 'running',
  );
  const run = detail.data;
  const cycles = run?.cycles || [];
  const requestedCycle = Number(
    params.get('cycle') ?? Math.max(0, cycles.length - 1),
  );
  const cycleIndex = Math.max(
    0,
    Math.min(
      Number.isFinite(requestedCycle) ? requestedCycle : 0,
      cycles.length - 1,
    ),
  );
  function update(values: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next);
  }
  function selectRun(id: string) {
    update({ run: id, cycle: '' });
  }
  const counts = cycles.reduce(
    (total, cycle) => ({
      processed: total.processed + cycle.processed,
      created: total.created + cycle.created.length,
      updated: total.updated + cycle.updated.length,
      review: total.review + cycle.review.length,
    }),
    { processed: 0, created: 0, updated: 0, review: 0 },
  );
  const currentStatus = run ? statuses[run.status] : undefined;
  const countsComplete =
    cycles.length === run?.completed_windows &&
    cycles.every((cycle) => cycle.counts_complete !== false);
  function countLabel(value: number) {
    return countsComplete ? value : cycles.length ? `≥ ${value}` : '未记录';
  }
  const StatusIcon = currentStatus?.icon || FileClock;
  const eventUrl = `/events?${new URLSearchParams({ uid, stream })}`;
  return (
    <main className="page runs-page">
      <header className="page-header">
        <div>
          <div className="page-eyebrow">任务状态</div>
          <h1>客户执行详情</h1>
          <p>每 4 秒自动读取进度。失败会保留原因和已完成结果。</p>
        </div>
        <div className="page-header-actions">
          <Button asChild>
            <Link to="/tasks">返回任务中心</Link>
          </Button>
          <Button asChild>
            <Link to={eventUrl}>
              <ArrowUpRight size={16} />
              返回事件
            </Link>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="刷新进度"
            onClick={() => {
              runs.refresh();
              detail.refresh();
            }}
          >
            <RefreshCw size={16} />
          </Button>
        </div>
      </header>
      {workspace.error && (
        <ErrorNotice message={workspace.error} retry={workspace.refresh} />
      )}
      <div className="runs-columns">
        <aside className="run-list-pane" aria-label="处理记录列表">
          <div className="section-label">
            当前客户的任务<span>{runs.data?.total ?? '—'}</span>
          </div>
          {runs.error && (
            <ErrorNotice message={runs.error} retry={runs.refresh} />
          )}
          {runs.loading && !runs.data ? (
            <LoadingRows count={3} />
          ) : runs.data?.items.length ? (
            <div className="run-list">
              {runs.data.items.map((item) => {
                const status = statuses[item.status];
                const Icon = status.icon;
                return (
                  <button
                    type="button"
                    key={item.run_id}
                    className={`run-item ${runId === item.run_id ? 'selected' : ''}`}
                    aria-pressed={runId === item.run_id}
                    onClick={() => selectRun(item.run_id)}
                  >
                    <div className="run-item-top">
                      <strong>正式处理</strong>
                      <time>{formatTime(item.created_at)}</time>
                    </div>
                    <p>
                      {formatRange(item.request.start, item.request.end)}
                      <span>·</span>每轮 {durationLabel(item.request.interval)}
                    </p>
                    <div className={`run-state ${item.status}`}>
                      <Icon
                        size={12}
                        className={item.status === 'running' ? 'spinning' : ''}
                      />
                      {status.label}
                      <span>
                        {item.completed_windows} / {item.total_windows} 轮
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          ) : (
            !runs.error && (
              <div className="no-runs">
                <FileClock size={21} strokeWidth={1.4} />
                <p>还没有处理记录</p>
                <small>当前客户和分析范围内没有已保存任务。</small>
              </div>
            )
          )}
        </aside>
        <section
          className="run-detail-pane"
          aria-label="处理详情"
          data-testid="run-detail"
        >
          {detail.error ? (
            <ErrorNotice
              message={`进度读取失败：${detail.error}`}
              retry={detail.refresh}
            />
          ) : (!runs.data && runs.loading) ||
            (runId && !run && detail.loading) ? (
            <LoadingRows />
          ) : run ? (
            <>
              <div className="run-detail-heading">
                <div>
                  <h2>正式增量处理</h2>
                  <p>
                    {`${formatRange(run.request.start, run.request.end)} · 每轮 ${durationLabel(run.request.interval)} · ${run.request.offline ? '使用已保存的来源' : '读取官方聊天'}`}
                  </p>
                </div>
                {run && (
                  <span className={`run-state ${run.status}`}>
                    <StatusIcon
                      size={14}
                      className={run.status === 'running' ? 'spinning' : ''}
                    />
                    {currentStatus?.label}
                  </span>
                )}
              </div>
              {latest && runId !== latest.run_id && (
                <div className="notice compact-notice">
                  正在查看较早的任务记录。
                  <Button
                    variant="ghost"
                    size="small"
                    onClick={() => selectRun(latest.run_id)}
                  >
                    查看最新进度
                  </Button>
                </div>
              )}
              {run && (
                <section
                  className={`run-progress ${run.status}`}
                  aria-label="任务进度"
                >
                  <div>
                    <span>已完成时段</span>
                    <strong>
                      {run.completed_windows} / {run.total_windows}
                      <small>
                        {' '}
                        轮 ·{' '}
                        {run.total_windows
                          ? Math.floor(
                              (run.completed_windows / run.total_windows) * 100,
                            )
                          : 0}
                        %
                      </small>
                    </strong>
                  </div>
                  <progress
                    value={run.completed_windows}
                    max={Math.max(1, run.total_windows)}
                    aria-label="处理完成进度"
                  />
                  {run.current_window && (
                    <p className="run-current-window">
                      {run.status === 'failed' ? '停止时段' : '当前时段'}{' '}
                      {formatTime(run.current_window.start)} —{' '}
                      {formatTime(run.current_window.end)}
                    </p>
                  )}
                  <p>
                    已快速跳过{' '}
                    {cycles.filter((cycle) => cycle.skipped_empty).length}{' '}
                    个无待处理消息窗口；进度表示已检查的范围，不等于模型调用次数。
                  </p>
                  {run.status === 'failed' && (
                    <div className="run-failure" role="alert">
                      <strong>任务已停止</strong>
                      <p>{run.error || '停止原因未记录。'}</p>
                      <small>
                        已完成结果保留；停止时段的保存情况以事件变化面板为准。本页不会自动重跑。
                      </small>
                    </div>
                  )}
                  {run.status !== 'failed' && run.error && (
                    <ErrorNotice message={run.error} />
                  )}
                  <p className="run-observed">
                    页面最近读取：
                    {new Date(run.observedAt).toLocaleTimeString('zh-CN', {
                      timeZone: 'Asia/Shanghai',
                      hour12: false,
                    })}{' '}
                    · 状态来自保存的记录，读取时间不代表任务心跳。
                  </p>
                </section>
              )}
              {run.request.provider === 'stub' && (
                <div className="notice compact-notice">
                  本次整理与保存客户聊天。智能识别尚未启用，暂不生成分析事件。
                </div>
              )}
              <dl className="run-counts">
                <div>
                  <dd>{countLabel(counts.processed)}</dd>
                  <dt>
                    {countsComplete ? '本任务处理消息' : '本任务已知消息'}
                  </dt>
                </div>
                <div>
                  <dd>{countLabel(counts.created)}</dd>
                  <dt>
                    <i className="count-dot new" />
                    本任务新建
                  </dt>
                </div>
                <div>
                  <dd>{countLabel(counts.updated)}</dd>
                  <dt>
                    <i className="count-dot update" />
                    本任务更新
                  </dt>
                </div>
                <div>
                  <dd>{countLabel(counts.review)}</dd>
                  <dt>
                    <i className="count-dot review" />
                    待核对
                  </dt>
                </div>
              </dl>
              <p className="run-count-note">
                计数来自本任务已保存的逐轮结果，不是客户累计总数；缺失记录不会补算。
              </p>
              <details className="run-cycles" key={`${uid}:${stream}:${runId}`}>
                <summary>
                  展开已完成时段与事件变化（{cycles.length} 轮）
                </summary>
                <CyclePanel
                  cycles={cycles}
                  selected={cycleIndex}
                  onSelect={(index) => update({ cycle: String(index) })}
                />
              </details>
              {run && (
                <div className="run-footnote">
                  {run.started_at ? '开始于' : '任务创建于'}{' '}
                  {formatTime(run.started_at || run.created_at)}
                  {run.finished_at &&
                    ` · 结束于 ${formatTime(run.finished_at)}`}
                  <span>记录 {run.run_id.slice(0, 8)}</span>
                </div>
              )}
            </>
          ) : (
            <EmptyState
              title="每一轮进展，都有记录"
              description="有任务时会自动选择当前任务；也可以在左侧查看此前的结果。"
              icon={<FileClock size={28} strokeWidth={1.3} />}
            />
          )}
        </section>
      </div>
    </main>
  );
}
