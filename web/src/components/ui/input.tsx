import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils';

/** 沿用 shadcn/ui 的本地组件方式，统一输入边框、禁用和键盘焦点。 */
export function Input({ className, ...props }: ComponentProps<'input'>) {
  return (
    <input
      data-slot="input"
      className={cn(
        'h-9 w-full min-w-0 rounded-md border border-zinc-200 bg-white px-3 text-sm outline-none focus-visible:border-zinc-500 focus-visible:ring-2 focus-visible:ring-zinc-200 disabled:opacity-50',
        className,
      )}
      {...props}
    />
  );
}
