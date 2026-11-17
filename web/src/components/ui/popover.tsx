import * as Primitive from '@radix-ui/react-popover';
import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils';

export const Popover = Primitive.Root;
export const PopoverTrigger = Primitive.Trigger;

/** 沿用 shadcn 的弹层结构，使用项目现有的 Radix 依赖。 */
export function PopoverContent({
  className,
  align = 'center',
  sideOffset = 4,
  ...props
}: ComponentProps<typeof Primitive.Content>) {
  return (
    <Primitive.Portal>
      <Primitive.Content
        data-slot="popover-content"
        align={align}
        sideOffset={sideOffset}
        className={cn(
          'z-60 w-72 max-h-(--radix-popover-content-available-height) overflow-auto rounded-lg border border-zinc-200 bg-white p-4 text-zinc-950 shadow-md outline-hidden',
          className,
        )}
        {...props}
      />
    </Primitive.Portal>
  );
}
