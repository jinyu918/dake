import { zhCN } from '@daypicker/react/locale';
import { ArrowRight, CalendarDays, Check, ChevronDown } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { messageDay } from './eventTimeRange';

export const timeRanges = [
  { value: '', label: '全部时间', days: 0 },
  { value: 'today', label: '最新一天', days: 1 },
  { value: '3d', label: '近 3 天', days: 3 },
  { value: '7d', label: '近 7 天', days: 7 },
  { value: 'custom', label: '自定义', days: 0 },
];

const dateInputClass =
  'block w-full min-w-0 text-xs text-zinc-800 tabular-nums outline-hidden [&::-webkit-calendar-picker-indicator]:hidden';
const dateLabelClass =
  'min-w-0 rounded-sm px-1.5 py-0.5 text-[11px] focus-within:bg-white focus-within:ring-1 focus-within:ring-zinc-400';

/** 日历只表示日期，不进行业务消息的时区转换。 */
function parseDay(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined;
  const date = new Date(`${value}T12:00:00`);
  return Number.isFinite(date.getTime()) && dateKey(date) === value
    ? date
    : undefined;
}
function dateKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
function presetDates(mode: string, anchor: string) {
  const days = timeRanges.find((item) => item.value === mode)?.days;
  const last = parseDay(anchor);
  if (!days || !last) return ['', ''];
  last.setDate(last.getDate() - days + 1);
  return [dateKey(last), anchor];
}
/** 草稿和已应用筛选分开；关闭弹层不会改变列表。 */
export function EventTimeFilter({
  mode,
  start,
  end,
  anchor,
  onApply,
}: {
  mode: string;
  start: string;
  end: string;
  anchor: string;
  onApply: (values: Record<string, string>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draftMode, setDraftMode] = useState(mode);
  const [from, setFrom] = useState(start);
  const [to, setTo] = useState(end);
  const [month, setMonth] = useState<Date>();
  const anchorDate = messageDay(anchor);
  const applied =
    mode === 'custom' ? [start, end] : presetDates(mode, anchorDate);
  const label = !mode
    ? '全部时间'
    : applied[0] || applied[1]
      ? `${applied[0] || '不限'} 至 ${applied[1] || '不限'}`
      : '选择日期范围';
  const invalid = Boolean(
    (from && !parseDay(from)) ||
      (to && !parseDay(to)) ||
      (from && to && from > to),
  );
  const incomplete = draftMode === 'custom' && (!from || !to);
  function resetDraft(nextMode: string) {
    const dates =
      nextMode === 'custom' ? [start, end] : presetDates(nextMode, anchorDate);
    setDraftMode(nextMode);
    setFrom(dates[0]);
    setTo(dates[1]);
    setMonth(parseDay(dates[0] || anchorDate) || new Date());
  }
  return (
    <Popover
      open={open}
      onOpenChange={(value) => {
        if (value) resetDraft(mode);
        setOpen(value);
      }}
    >
      <PopoverTrigger asChild>
        <Button aria-label={`时间范围：${label}`}>
          <CalendarDays size={15} />
          <span>{label}</span>
          <ChevronDown size={14} />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[min(408px,calc(100vw-24px))] p-0"
        align="start"
        sideOffset={8}
        collisionPadding={12}
        aria-label="选择时间范围"
      >
        <div className="grid min-[440px]:grid-cols-[116px_minmax(0,1fr)]">
          <div className="flex flex-col justify-between gap-2 border-b border-zinc-100 bg-zinc-50 p-2.5 min-[440px]:border-r min-[440px]:border-b-0">
            <fieldset
              className="grid grid-cols-4 gap-1 min-[440px]:grid-cols-1"
              aria-label="快捷范围"
            >
              {timeRanges
                .filter((item) => item.value !== 'custom')
                .map((item) => (
                  <button
                    type="button"
                    key={item.value}
                    className="flex h-8 items-center justify-center gap-1 rounded-md px-1 text-xs whitespace-nowrap text-zinc-600 enabled:hover:bg-zinc-100 disabled:opacity-50 aria-pressed:bg-amber-100 aria-pressed:font-medium aria-pressed:text-amber-950 min-[440px]:justify-between min-[440px]:px-2"
                    aria-pressed={draftMode === item.value}
                    disabled={Boolean(item.days && !anchorDate)}
                    onClick={() => resetDraft(item.value)}
                  >
                    {item.label}
                    {draftMode === item.value && (
                      <Check
                        size={13}
                        className="hidden min-[440px]:block"
                        aria-hidden="true"
                      />
                    )}
                  </button>
                ))}
            </fieldset>
            <p className="px-1 text-[11px] text-zinc-500">
              {anchorDate ? (
                <>
                  数据截至{' '}
                  <time
                    className="whitespace-nowrap min-[440px]:block"
                    dateTime={anchorDate}
                  >
                    {anchorDate}
                  </time>
                </>
              ) : (
                '暂无数据'
              )}
            </p>
          </div>
          <div className="min-w-0 space-y-2.5 p-3">
            <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 rounded-md border border-zinc-200 bg-zinc-50 p-1 text-zinc-500">
              <label className={dateLabelClass}>
                <span>开始日期</span>
                <input
                  className={dateInputClass}
                  type="date"
                  aria-invalid={invalid || undefined}
                  value={from}
                  onChange={(e) => {
                    const date = parseDay(e.target.value);
                    setDraftMode('custom');
                    setFrom(e.target.value);
                    if (date) setMonth(date);
                  }}
                />
              </label>
              <ArrowRight size={14} aria-hidden="true" />
              <label className={dateLabelClass}>
                <span>结束日期</span>
                <input
                  className={dateInputClass}
                  type="date"
                  aria-invalid={invalid || undefined}
                  value={to}
                  onChange={(e) => {
                    setDraftMode('custom');
                    setTo(e.target.value);
                  }}
                />
              </label>
            </div>
            <Calendar
              mode="range"
              locale={zhCN}
              weekStartsOn={1}
              fixedWeeks
              resetOnSelect
              month={month}
              onMonthChange={setMonth}
              selected={
                parseDay(from)
                  ? { from: parseDay(from), to: parseDay(to) }
                  : undefined
              }
              onSelect={(value) => {
                setDraftMode('custom');
                setFrom(value?.from ? dateKey(value.from) : '');
                setTo(value?.to ? dateKey(value.to) : '');
              }}
              labels={{
                labelPrevious: () => '上个月',
                labelNext: () => '下个月',
              }}
            />
            {invalid && (
              <p className="text-xs text-red-700" role="alert">
                开始日期不能晚于结束日期。
              </p>
            )}
          </div>
        </div>
        <footer className="flex items-center justify-between gap-2 border-t border-zinc-100 p-2.5">
          <Button
            variant="ghost"
            size="small"
            disabled={!mode && !draftMode}
            onClick={() => {
              onApply({ range: '', startDate: '', endDate: '', event: '' });
              setOpen(false);
            }}
          >
            清除筛选
          </Button>
          <div className="flex gap-1">
            <Button variant="ghost" size="small" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              variant="primary"
              size="small"
              disabled={invalid || incomplete}
              onClick={() => {
                onApply({
                  range: draftMode,
                  startDate: draftMode === 'custom' ? from : '',
                  endDate: draftMode === 'custom' ? to : '',
                  event: '',
                });
                setOpen(false);
              }}
            >
              应用
            </Button>
          </div>
        </footer>
      </PopoverContent>
    </Popover>
  );
}
