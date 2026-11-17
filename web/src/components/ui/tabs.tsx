import * as Primitive from '@radix-ui/react-tabs';
import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils';

/** shadcn/ui 风格的本地封装；方向键、焦点和内容关联由 Radix 维护。 */
export const Tabs = Primitive.Root;
export function TabsList({
  className,
  ...props
}: ComponentProps<typeof Primitive.List>) {
  return (
    <Primitive.List
      className={cn('flex gap-6 border-b border-zinc-200', className)}
      {...props}
    />
  );
}
export function TabsTrigger({
  className,
  ...props
}: ComponentProps<typeof Primitive.Trigger>) {
  return (
    <Primitive.Trigger
      className={cn(
        'border-b-2 border-transparent px-1 py-3 text-sm text-zinc-500 data-[state=active]:border-zinc-900 data-[state=active]:font-medium data-[state=active]:text-zinc-900',
        className,
      )}
      {...props}
    />
  );
}
export function TabsContent({
  className,
  ...props
}: ComponentProps<typeof Primitive.Content>) {
  return (
    <Primitive.Content
      className={cn('pt-6 outline-none', className)}
      {...props}
    />
  );
}
