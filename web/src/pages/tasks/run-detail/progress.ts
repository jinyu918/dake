import { Check, CircleAlert, Clock3, LoaderCircle } from 'lucide-react';

export const runStatuses = {
  queued: { label: '等待处理', icon: Clock3 },
  running: { label: '正在处理', icon: LoaderCircle },
  succeeded: { label: '处理完成', icon: Check },
  failed: { label: '已停止 · 处理未完成', icon: CircleAlert },
};

/** 优先显示记录中的执行任务；没有进行中的任务时显示最近一次结果。 */
export function currentRun(items: API.RunResponse[]) {
  return (
    items.find((run) => run.status === 'running') ||
    items.find((run) => run.status === 'queued') ||
    items[0]
  );
}
