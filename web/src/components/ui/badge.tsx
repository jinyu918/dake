import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils';

/** 状态标签只呈现后台状态，不把任务运行状态当作事件状态。 */
export function Badge({ className, ...props }: ComponentProps<'span'>) {
  return (
    <span
      data-slot="badge"
      className={cn(
        'inline-flex shrink-0 items-center gap-1 rounded-md border border-zinc-200 px-2 py-0.5 text-xs font-medium',
        className,
      )}
      {...props}
    />
  );
}
