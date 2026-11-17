import { DayPicker, getDefaultClassNames } from '@daypicker/react';
import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils';

const defaults = getDefaultClassNames();
const navigationButton =
  'grid size-8 place-items-center rounded-md text-zinc-600 hover:bg-zinc-100 aria-disabled:opacity-40';

/** 采用 shadcn 的日历布局，通过 DayPicker 的类名设置外观。 */
export function Calendar({
  className,
  classNames,
  ...props
}: ComponentProps<typeof DayPicker>) {
  return (
    <DayPicker
      className={cn('text-xs tabular-nums', className)}
      classNames={{
        months: 'relative',
        nav: 'absolute inset-x-0 top-0 flex justify-between',
        button_previous: navigationButton,
        button_next: navigationButton,
        chevron: 'size-4 fill-current',
        month_caption: 'grid h-8 place-items-center text-sm font-medium',
        month_grid: 'w-full table-fixed border-collapse',
        weekday: 'h-7 font-normal text-zinc-500',
        day: 'h-8 p-0 text-center',
        day_button: cn(
          defaults.day_button,
          'mx-auto grid size-8 place-items-center rounded-md hover:bg-zinc-100',
        ),
        hidden: cn(defaults.hidden, 'invisible'),
        today:
          '[&>button]:font-semibold [&>button]:underline [&>button]:underline-offset-4',
        selected: String.raw`[&:not(.rdp-range\_middle)>button]:bg-zinc-900 [&:not(.rdp-range\_middle)>button]:text-white`,
        range_start: 'rounded-l-md bg-amber-50',
        range_end: 'rounded-r-md bg-amber-50',
        range_middle: cn(defaults.range_middle, 'bg-amber-50 text-amber-950'),
        ...classNames,
      }}
      {...props}
    />
  );
}
