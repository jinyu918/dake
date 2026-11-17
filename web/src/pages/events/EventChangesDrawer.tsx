import { ListChecks } from 'lucide-react';
import { type ReactNode, useRef, useState } from 'react';
import { EmptyState, ErrorNotice, LoadingRows } from '@/components/Feedback';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeading,
  DialogTrigger,
} from '@/components/ui/dialog';
import { useResource } from '@/lib/useResource';
import { formatTime } from '@/lib/utils';
import { api } from '@/services/api';
import { Proofs, VersionDifference } from './ChangeEvidence';
import { executionIssue, submittedRetry } from './changeTimeline';
import { ExecutionChanges } from './ExecutionChanges';
import { eventTitle } from './eventTitle';
import { SavedChanges } from './SavedChanges';

export { VersionDifference } from './ChangeEvidence';

const actions: Record<string, string> = {
  new: '新建',
  update: '旧事件进展',
  duplicate: '重复',
  review: '待确认',
};
const statuses = {
  queued: '等待处理',
  running: '处理中',
  succeeded: '任务完成',
  failed: '任务失败',
};
const actionLabel = (value: string | null) =>
  value ? actions[value] || value : '未记录';

/** 逐步展开保存的阶段结果，未记录的阶段不显示成已完成。 */
export function ChangeBatch({
  batch,
  data,
}: {
  batch: API.EventChangeBatch;
  data: API.EventChangesResponse;
}) {
  const task = data.tasks.find((value) => value.run_id === batch.run_id);
  const refs = batch.attributions.flatMap((record) => record.new_evidence);
  const history = batch.attributions.flatMap((record) =>
    record.basis.filter((ref) => ref.role === 'history'),
  );
  return (
    <details className="change-batch">
      <summary>
        <span>
          {formatTime(batch.window.start)} — {formatTime(batch.window.end)}
        </span>
        <small>批次 {batch.cycle_index + 1} · 旧批次记录</small>
      </summary>
      <p className="change-muted">任务 {batch.run_id} · 窗口左闭右开</p>
      <p>独立提交收据未记录，以下仅为已保存的旧批次结果。</p>
      {task ? <p>关联任务：{statuses[task.status]}</p> : null}
      {task?.status === 'failed' ? (
        <p className="change-notice">
          {task.error || '失败原因未记录'}
          。失败阶段及事务提交结果无法由任务状态确认，不能据此认定事件未提交。
        </p>
      ) : null}
      {batch.recovered_from_history ? (
        <p className="change-notice">
          包含从历史恢复的结果，不代表全部由本次执行产生；原失败阶段未记录。
        </p>
      ) : null}
      <ol className="change-stages">
        <li>
          <details>
            <summary>本批新消息 · 部分记录</summary>
            <p>
              整批处理 {batch.processed ?? '未记录'} 条
              {batch.counts_complete ? '' : '（计数不完整）'}
              ；完整输入清单未记录。
            </p>
            <p>以下仅为本事件归属记录中的本轮消息引用，不等于整批输入。</p>
            {refs.length ? <Proofs items={refs} /> : <p>本轮消息引用未记录</p>}
          </details>
        </li>
        <li>
          <details>
            <summary>历史候选 · 未记录</summary>
            <p>
              实际候选列表及命中原因未记录。归属中的备选事件不能代替检索结果。
            </p>
          </details>
        </li>
        <li>
          <details>
            <summary>
              必要旧聊天 · {history.length ? '部分依据已记录' : '未记录'}
            </summary>
            <p>
              实际补取集合、缺失和截断未记录；这里只展示归属收据引用的历史依据，不是本轮新消息。
            </p>
            <Proofs items={history} />
          </details>
        </li>
        <li>
          <details>
            <summary>
              模型建议与程序决定 ·{' '}
              {batch.attributions.length ? '已记录动作' : '未记录'}
            </summary>
            {batch.attributions.length ? (
              batch.attributions.map((record, index) => (
                <div
                  className="change-decision"
                  // biome-ignore lint/suspicious/noArrayIndexKey: 同一已保存批次的收据次序固定，重复收据也要分别展示。
                  key={index}
                >
                  <p>
                    模型建议：{actionLabel(record.requested_action)} ·
                    完整建议及原始理由未记录
                  </p>
                  <p>程序决定：{actionLabel(record.action)}</p>
                  <p>
                    目标事件：{record.event_id || '未指定'}
                    {record.event_id === data.event_id ? '（当前事件）' : ''}
                  </p>
                  {record.possible_event_ids.length ? (
                    <p>待确认备选：{record.possible_event_ids.join('、')}</p>
                  ) : null}
                  <p>最终记录理由：{record.reason || '未记录'}</p>
                  {record.change_kind === 'no_change' ? (
                    <p>未产生事件新版本</p>
                  ) : null}
                  <details>
                    <summary>实际归属依据</summary>
                    <Proofs items={record.basis} showRole />
                  </details>
                </div>
              ))
            ) : (
              <p>归属收据未记录，不能从最终事件反推模型建议或程序决定。</p>
            )}
          </details>
        </li>
        <li>
          <details>
            <summary>已保存结果与差异</summary>
            <p>
              {batch.created ? '本事件列入新建结果。' : ''}
              {batch.updated ? '本事件列入更新结果。' : ''}
              {!batch.created && !batch.updated
                ? '本事件未列入本批新建或更新结果。'
                : ''}
            </p>
            {batch.revision_indexes.length ? (
              batch.revision_indexes.map((index) => {
                const revision = data.revisions[index];
                if (!revision) return null;
                return (
                  <div key={index}>
                    <h4>版本 {revision.revision_no ?? '未记录'}</h4>
                    <VersionDifference
                      revision={revision}
                      previous={data.revisions[index - 1]?.snapshot}
                    />
                  </div>
                );
              })
            ) : (
              <p>
                本批具体版本关联未记录；下方可查看已保存事件版本，不能仅按相同窗口认定本次写入。
              </p>
            )}
          </details>
        </li>
      </ol>
    </details>
  );
}

export function ChangesContent({
  data,
  children,
  onDetailsOpen,
}: {
  data: API.EventChangesResponse;
  children?: ReactNode;
  onDetailsOpen?: () => void;
}) {
  const technicalRef = useRef<HTMLDetailsElement>(null);
  const recovered = new Map<string, API.EventExecution>();
  const pending = (data.executions || []).filter(
    (item) => item.outcome === 'running',
  );
  const issues = (data.executions || []).flatMap((item) => {
    const retry = submittedRetry(item, data.executions);
    if (retry) {
      recovered.set(retry.attempt_id, retry);
      return [];
    }
    const label = executionIssue(item);
    return label ? [{ item, label }] : [];
  });
  const failedTasks = data.tasks.filter(
    (task) =>
      task.status === 'failed' &&
      data.batches.some((batch) => batch.run_id === task.run_id),
  );
  return (
    <>
      <p className="change-muted">
        先看消息带来的实际变化；排查归属与更新原因时，可查看输入、候选、判断和提交记录。
      </p>
      <button
        type="button"
        className="expandable-toggle"
        onClick={() => {
          if (technicalRef.current) {
            technicalRef.current.open = true;
            technicalRef.current.scrollIntoView({ block: 'start' });
          }
        }}
      >
        查看处理链路 · 输入 → 候选 → 判断 → 提交
      </button>
      <SavedChanges data={data} />
      {pending.length ? (
        <section
          className="change-progress-notice"
          aria-label="尚未记录处理结果"
        >
          {pending.map((item) => (
            <p key={item.attempt_id}>
              <strong>结果尚未确认</strong> · {formatTime(item.window.start)}{' '}
              这一批。
            </p>
          ))}
          <p className="change-muted">
            完成或失败结果尚未记录，不能认定为成功或失败；收到结果后更新。
          </p>
        </section>
      ) : null}
      {recovered.size ? (
        <section className="change-retry-notice" aria-label="重试提交结果">
          {[...recovered.values()].map((item) => (
            <p key={item.attempt_id}>
              <strong>历史失败，后续重试已提交</strong> ·{' '}
              {formatTime(item.window.start)} 这一批。
            </p>
          ))}
          <p className="change-muted">
            原失败记录保留在处理详情；本事件是否变化，以上方已保存版本为准。
          </p>
        </section>
      ) : null}
      {issues.length || failedTasks.length ? (
        <section className="change-alerts" aria-label="失败与待核实">
          <h3>失败与待核实</h3>
          {issues.map(({ item, label }) => (
            <p key={item.attempt_id}>
              <strong>{label}</strong> · {formatTime(item.window.start)}
              {item.commit_status === 'committed'
                ? '。已保存的收据保留，不表示整批执行成功。'
                : '。不作为新增事件变化。'}
            </p>
          ))}
          {failedTasks.map((task) => (
            <p key={task.run_id}>
              旧任务失败：{task.error || '原因未记录'}。不能据此认定事件未提交。
            </p>
          ))}
          <p>
            这里只提示已记录状态，不自动核实、重试或重新分析；具体依据见处理详情。
          </p>
        </section>
      ) : null}
      {!data.execution_records_available ? (
        <p className="change-muted">
          完整执行记录暂不可用；上方仍展示已保存版本，缺失过程不补造。
        </p>
      ) : null}
      <details
        ref={technicalRef}
        className="change-section change-technical"
        onToggle={(event) => {
          if (event.currentTarget.open) onDetailsOpen?.();
        }}
      >
        <summary>处理详情 · 候选、判断、提交与重试</summary>
        {data.details_included === false ? (
          <p>处理详情尚未加载，正在读取；读取失败时可点击刷新重试。</p>
        ) : (
          <>
            <p>
              候选命中、无变化或仅恢复展示的执行不增加主列表条目。建议与程序拟变化不是提交结果。
            </p>
            <h3>与本事件有关的执行批次</h3>
            {(data.executions || []).map((item) => (
              <ExecutionChanges key={item.attempt_id} item={item} data={data} />
            ))}
            {data.batches.length ? <h3>旧批次记录</h3> : null}
            {data.batches.length ? (
              data.batches.map((batch) => (
                <ChangeBatch
                  key={`${batch.run_id}:${batch.cycle_index}`}
                  batch={batch}
                  data={data}
                />
              ))
            ) : !(data.executions || []).length ? (
              <EmptyState title="本页没有当前事件的关联批次记录" />
            ) : null}
            <details className="change-section">
              <summary>原始版本记录 · {data.revisions.length} 版</summary>
              <p>
                这里保留全部已保存版本；没有归属收据指向时，与具体任务的关系未记录。
              </p>
              {data.revisions.map((revision, index) => (
                <details
                  // biome-ignore lint/suspicious/noArrayIndexKey: 修订历史只追加，旧版无版本号时使用其保存位置。
                  key={index}
                  className="change-batch"
                >
                  <summary>
                    版本 {revision.revision_no ?? '未记录'} ·{' '}
                    {revision.source === 'manual'
                      ? `人工裁定 · ${revision.recorded_at ? formatTime(revision.recorded_at) : '时间未记录'}`
                      : formatTime(revision.window_start)}
                  </summary>
                  <VersionDifference
                    revision={revision}
                    previous={data.revisions[index - 1]?.snapshot}
                  />
                </details>
              ))}
            </details>
            {children}
          </>
        )}
      </details>
    </>
  );
}

/** 首次只取变化和异常；详情按需加载，有未结束的关联任务时才轮询。 */
export function EventChangesDrawer({ event }: { event?: API.EventResponse }) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [executionOffset, setExecutionOffset] = useState(0);
  const [detailsRequested, setDetailsRequested] = useState(false);
  const key =
    event && open
      ? JSON.stringify([
          'changes',
          event.uid,
          event.stream,
          event.event_id,
          offset,
          executionOffset,
        ])
      : null;
  const resource = useResource(
    key,
    (signal) =>
      api.eventChanges(
        event?.event_id || '',
        event?.uid || '',
        event?.stream || '',
        offset,
        signal,
        executionOffset,
        detailsRequested,
      ),
    3000,
    (data) =>
      data.executions.some((item) => item.outcome === 'running') ||
      data.tasks.some((item) => ['queued', 'running'].includes(item.status)),
  );
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (!value) {
          setOffset(0);
          setExecutionOffset(0);
          setDetailsRequested(false);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="ghost" disabled={!event}>
          <ListChecks size={16} />
          变化面板
        </Button>
      </DialogTrigger>
      <DialogContent
        className="event-changes-drawer"
        onEscapeKeyDown={(e) => e.stopPropagation()}
      >
        <DialogHeading
          title="变化面板"
          description={event ? eventTitle(event) : '请先选择事件'}
        />
        <div className="event-changes-scroll">
          <Button
            variant="ghost"
            onClick={resource.refresh}
            disabled={resource.loading}
          >
            刷新
          </Button>
          {event ? (
            <p className="change-muted">当前事件 {event.event_id}</p>
          ) : null}
          {resource.error ? (
            <ErrorNotice message={resource.error} retry={resource.refresh} />
          ) : null}
          {resource.error && resource.data ? (
            <p>以下为上次成功读取的记录，本次刷新失败。</p>
          ) : null}
          {resource.loading && !resource.data ? <LoadingRows /> : null}
          {resource.data ? (
            <ChangesContent
              data={resource.data}
              onDetailsOpen={() => {
                if (!detailsRequested) {
                  setDetailsRequested(true);
                  resource.refresh();
                }
              }}
            >
              <div className="change-pagination">
                <span>
                  本事件关联执行共 {resource.data.total_executions ?? 0} 次
                </span>
                {executionOffset > 0 ? (
                  <Button variant="ghost" onClick={() => setExecutionOffset(0)}>
                    回到最新执行
                  </Button>
                ) : null}
                {resource.data.next_execution_offset != null ? (
                  <Button
                    variant="ghost"
                    onClick={() =>
                      setExecutionOffset(
                        resource.data?.next_execution_offset ?? 0,
                      )
                    }
                  >
                    更早执行
                  </Button>
                ) : null}
                {offset > 0 ? (
                  <Button variant="ghost" onClick={() => setOffset(0)}>
                    回到最新旧记录
                  </Button>
                ) : null}
                {resource.data.next_offset !== null ? (
                  <Button
                    variant="ghost"
                    onClick={() => setOffset(resource.data?.next_offset ?? 0)}
                  >
                    更早旧记录
                  </Button>
                ) : null}
              </div>
            </ChangesContent>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
