import { ChevronDown, Crown, Play, Search } from 'lucide-react';
import { useEffect, useMemo, useRef } from 'react';
import { Link, useSearchParams } from 'umi';
import { eventTypeClass } from '@/components/EventType';
import { EmptyState, ErrorNotice, LoadingRows } from '@/components/Feedback';
import { Button } from '@/components/ui/button';
import { useWorkspace } from '@/layouts/Workspace';
import { useResource } from '@/lib/useResource';
import { formatTime } from '@/lib/utils';
import { api } from '@/services/api';
import { CustomerSwitcher, customerTierBadge } from './CustomerSwitcher';
import { EventChangesDrawer } from './EventChangesDrawer';
import { EventPane } from './EventPane';
import { EventTimeFilter, timeRanges } from './EventTimeFilter';
import { inMessageRange } from './eventTimeRange';
import { eventTitle, revisionHeadline } from './eventTitle';

const DAY = 24 * 60 * 60 * 1000;
const additionalTypes = ['其他', '待分类'];

/** 上海时区的自然日序号，用于按天比较。 */
function dayIndex(value: string) {
  const key = new Date(value).toLocaleDateString('sv-SE', {
    timeZone: 'Asia/Shanghai',
  });
  return Math.round(Date.parse(`${key}T00:00:00Z`) / DAY);
}

function latestChange(event: API.EventResponse, anchorDay: number) {
  const revision = (event.updated_by || [])
    .filter((item) => dayIndex(item.window_start) === anchorDay)
    .pop();
  if (!revision) return '';
  return revisionHeadline(revision);
}

export default function EventsPage() {
  const workspace = useWorkspace();
  const [params, setParams] = useSearchParams();
  const uid = params.get('uid') || 'all';
  const isAllCustomers = uid === 'all';
  const customerMap = useMemo(() => {
    const map = new Map<string, API.CustomerResponse>();
    for (const item of workspace.data?.customers || []) {
      map.set(item.uid, item);
    }
    return map;
  }, [workspace.data?.customers]);
  const customer = isAllCustomers
    ? undefined
    : customerMap.get(uid) ||
      (workspace.data && /^[0-9]+$/.test(uid)
        ? {
            uid,
            label: `客户 ${uid}`,
            stream: params.get('stream') || '',
            message_count: 0,
            event_count: 0,
          }
        : undefined);
  const stream = isAllCustomers
    ? ''
    : params.get('stream') || customer?.stream || '';
  const type = params.get('type') || '';
  const search = params.get('q') || '';
  const group = (params.get('group') || '').split(',')[0].trim();
  const range = timeRanges.find((item) => item.value === params.get('range'));
  const startDate = params.get('startDate') || '';
  const endDate = params.get('endDate') || '';
  const invalidRange =
    range?.value === 'custom' &&
    Boolean(startDate && endDate && startDate > endDate);
  const eventId = params.get('event') || '';
  const dismissed = useRef(false);
  const targetCustomers = useMemo(
    () =>
      (workspace.data?.customers || []).filter(
        (item) => item.event_count === undefined || item.event_count > 0,
      ),
    [workspace.data?.customers],
  );
  const eventsKey = isAllCustomers
    ? workspace.data
      ? `events:all:${type}:${targetCustomers.map((item) => `${item.uid}:${item.stream}`).join(',')}`
      : null
    : customer
      ? `events:${uid}:${stream}:${type}`
      : null;
  const events = useResource(
    eventsKey,
    async (signal) => {
      if (!isAllCustomers) {
        return api.events(uid, stream, type, signal);
      }
      const results = await Promise.all(
        targetCustomers.map((item) =>
          api.events(item.uid, item.stream, type, signal),
        ),
      );
      const merged = results
        .flatMap((result) => result.items)
        .sort((left, right) => right.end_time.localeCompare(left.end_time));
      return { items: merged, total: merged.length };
    },
    5000,
  );

  const items = events.data?.items ?? [];
  const linkedEvent = useResource(
    eventId && customer && !items.some((item) => item.event_id === eventId)
      ? `event-link:${uid}:${stream}:${eventId}`
      : null,
    () => api.event(eventId, uid, stream),
  );
  const groups = useMemo(() => {
    const map = new Map<string, string>();
    for (const event of items) {
      for (const item of event.conversations || []) {
        if (item.id) map.set(item.id, item.name || item.id);
      }
    }
    return [...map.entries()].map(([id, name]) => ({ id, name }));
  }, [items]);

  const anchor = useMemo(
    () =>
      items
        .map((event) => event.end_time)
        .sort()
        .at(-1) ?? '',
    [items],
  );
  const anchorDay = anchor ? dayIndex(anchor) : 0;

  const visible = useMemo(() => {
    return items
      .filter((event) => {
        if (
          search &&
          !`${eventTitle(event)} ${event.summary} ${event.event_id} ${event.uid}`
            .toLowerCase()
            .includes(search.toLowerCase())
        )
          return false;
        if (
          group &&
          !(event.conversations || []).some((item) => item.id === group)
        ) {
          return false;
        }
        if (range?.days && anchor) {
          const last = dayIndex(event.end_time);
          if (last <= anchorDay - range.days) return false;
        }
        if (range?.value === 'custom') {
          return (
            !invalidRange && inMessageRange(event.end_time, startDate, endDate)
          );
        }
        return true;
      })
      .sort((left, right) => right.end_time.localeCompare(left.end_time));
  }, [
    items,
    search,
    group,
    range,
    anchor,
    anchorDay,
    startDate,
    endDate,
    invalidRange,
  ]);

  const selected =
    visible.find((event) => event.event_id === eventId) ||
    (linkedEvent.data?.event_id === eventId ? linkedEvent.data : undefined);
  const activeUid = isAllCustomers
    ? selected?.uid || workspace.data?.uid || ''
    : uid;
  const activeStream = isAllCustomers
    ? selected?.stream || customerMap.get(activeUid)?.stream || ''
    : stream;
  const filtering = Boolean(type || group || range?.value);

  function selectCustomer(nextUid: string, nextStream = '') {
    update({
      uid: nextUid,
      stream: nextStream,
      tier: '',
      event: '',
      group: '',
      q: '',
    });
  }

  function update(values: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    next.delete('day');
    dismissed.current = false;
    setParams(next);
  }

  useEffect(() => {
    if (!visible.length) return;
    if (eventId && visible.some((event) => event.event_id === eventId)) return;
    if (!eventId && dismissed.current) return;
    const next = new URLSearchParams(params);
    next.set('event', visible[0].event_id);
    setParams(next);
  }, [eventId, visible, params, setParams]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        event.key !== 'Escape' ||
        !params.get('event') ||
        document.querySelector('[role="dialog"]')
      )
        return;
      dismissed.current = true;
      const next = new URLSearchParams(params);
      next.delete('event');
      setParams(next);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [params, setParams]);

  const filters =
    items.length || filtering ? (
      <div className="inbox-filters">
        <label className="filter-select">
          <span className="sr-only">事件类型</span>
          <select
            aria-label="事件类型"
            value={type}
            onChange={(event) =>
              update({ type: event.target.value, event: '' })
            }
          >
            <option value="">全部类型</option>
            {(workspace.data?.event_types || []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
            {additionalTypes.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <ChevronDown size={14} />
        </label>
        <label className="filter-select filter-select-group">
          <span className="sr-only">群聊</span>
          <select
            aria-label="群聊"
            title={
              groups.find((item) => item.id === group)?.name ||
              (group ? undefined : '全部群聊')
            }
            value={group}
            onChange={(event) =>
              update({ group: event.target.value, event: '' })
            }
          >
            <option value="">全部群聊</option>
            {groups.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
          <ChevronDown size={14} />
        </label>
      </div>
    ) : null;

  return (
    <main className="page events-workspace">
      <header className="inbox-toolbar">
        <div className="inbox-toolbar-start">
          <h1>
            工作台
            <span className="heading-count">{events.data?.total ?? '—'}</span>
          </h1>

          <EventTimeFilter
            key={`${uid}:${stream}`}
            mode={range?.value || ''}
            start={startDate}
            end={endDate}
            anchor={anchor}
            onApply={update}
          />
        </div>
        <div className="inbox-toolbar-actions">
          <EventChangesDrawer
            key={JSON.stringify([
              selected?.uid || activeUid,
              selected?.stream || activeStream,
              selected?.event_id,
            ])}
            event={selected}
          />
          <Button variant="primary" asChild>
            <Link to="/tasks">
              <Play size={15} />
              任务中心
            </Link>
          </Button>
        </div>
      </header>

      {invalidRange ? (
        <p role="alert">开始日期不能晚于结束日期，请调整范围。</p>
      ) : null}
      {workspace.error ? (
        <ErrorNotice message={workspace.error} retry={workspace.refresh} />
      ) : null}
      {events.error ? (
        <ErrorNotice message={events.error} retry={events.refresh} />
      ) : null}

      <div className="inbox-split">
        <section className="inbox-list" aria-label="事件列表">
          <header className="event-list-tools">
            <CustomerSwitcher
              uid={uid}
              customers={workspace.data?.customers || []}
              current={customer}
              onSelect={selectCustomer}
            />
            <h2>
              事件列表 <span>{visible.length}</span>
            </h2>
            {filters}
            <label className="event-search">
              <Search size={15} />
              <input
                aria-label="搜索事件"
                placeholder="搜索事件"
                value={search}
                onChange={(event) =>
                  update({ q: event.target.value, event: '' })
                }
              />
            </label>
          </header>
          {!workspace.data || (events.loading && !events.data) ? (
            !workspace.error && <LoadingRows />
          ) : !isAllCustomers && !customer ? (
            <EmptyState title="未找到这个客户" />
          ) : visible.length ? (
            <ul className="event-rows" aria-label="售后事件">
              {visible.map((event) => {
                const active = selected?.event_id === event.event_id;
                const change = latestChange(event, anchorDay);
                const eventCustomer = customerMap.get(event.uid);
                const badge = customerTierBadge(eventCustomer);
                return (
                  <li key={event.event_id}>
                    <button
                      type="button"
                      className={active ? 'is-active' : ''}
                      aria-current={active ? 'true' : undefined}
                      onClick={() => update({ event: event.event_id })}
                    >
                      <span
                        className={`event-row-dot ${eventTypeClass(event.type)}`}
                      />
                      <span className="event-row-title">
                        {eventTitle(event)}
                      </span>
                      <span className="event-row-preview">{event.summary}</span>
                      <span className="event-row-sub">
                        {(event.types?.length ? event.types : [event.type]).map(
                          (type) => (
                            <span className="type-tag" key={type}>
                              {type}
                            </span>
                          ),
                        )}
                        {isAllCustomers || badge ? (
                          <span
                            className={`event-customer-tag ${badge?.kind || ''}`}
                          >
                            {badge?.kind === 'super' && <Crown size={10} />}
                            {badge ? `${badge.short}` : ''}
                            {isAllCustomers
                              ? `${badge ? ' · ' : ''}${event.uid}`
                              : ''}
                          </span>
                        ) : null}
                        <time dateTime={event.end_time}>
                          {formatTime(event.end_time)}
                        </time>
                        {change ? (
                          <span className="change-tag">{change}</span>
                        ) : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            !events.error && (
              <EmptyState title={type ? `暂无${type}事件` : '暂无事件'} />
            )
          )}
        </section>
        {selected ? (
          <EventPane
            key={JSON.stringify([
              selected.uid,
              selected.stream,
              selected.event_id,
            ])}
            event={selected}
            targets={workspace.data?.field_targets || []}
            onSaved={events.refresh}
            customer={customerMap.get(selected.uid) || customer}
            isAllCustomers={isAllCustomers}
            onSelectCustomer={selectCustomer}
          />
        ) : (
          <section className="inbox-chat">
            <EmptyState
              title="选择事件查看原文与详情"
              description="没有数据时，请到任务中心配置并执行任务。"
            />
          </section>
        )}
      </div>
    </main>
  );
}
