import {
  ArrowUpRight,
  Building2,
  Copy,
  Crown,
  RefreshCw,
  Search,
  UsersRound,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'umi';
import { ErrorNotice, LoadingRows } from '@/components/Feedback';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeading } from '@/components/ui/dialog';
import { useWorkspace } from '@/layouts/Workspace';
import { useResource } from '@/lib/useResource';
import { errorMessage, formatTime } from '@/lib/utils';
import { api } from '@/services/api';
import './style.css';

const statuses = {
  queued: '等待同步',
  running: '同步中',
  succeeded: '已完成',
  failed: '未全部完成',
};
const freshness: Record<string, string> = {
  fresh: '已更新',
  unknown: '未知',
  stale: '旧资料 · 刷新失败',
  error: '查询失败',
};

type CustomerFilter = NonNullable<API.listCustomerProfilesParams['segment']>;
const customerFilters: { value: CustomerFilter; label: string }[] = [
  { value: 'large', label: '大客户（L6～L10）' },
  { value: 'super_large', label: '超级大客户（L9～L10）' },
  { value: 'non_large', label: '非大客户（L0～L5）' },
  { value: 'pending', label: '等级待确认' },
  { value: 'all', label: '全部客户' },
];
const segmentLabels: Record<API.CustomerProfile['segment'], string> = {
  large: '大客户',
  non_large: '非大客户',
  pending: '等级待确认',
};

/** 输入始终使用上海时间，不依赖浏览器所在时区。 */
function localTime(value: Date) {
  return new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
    .format(value)
    .replace(' ', 'T');
}

function Paging({
  offset,
  total,
  onChange,
  pageSize = 50,
  onSizeChange,
  loading = false,
}: {
  offset: number;
  total: number;
  onChange: (value: number) => void;
  pageSize?: number;
  onSizeChange?: (value: number) => void;
  loading?: boolean;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.floor(offset / pageSize) + 1;
  const numbers = [...new Set([1, current - 1, current, current + 1, pages])]
    .filter((p) => p > 0 && p <= pages)
    .sort((a, b) => a - b);
  return (
    <nav
      className="customer-paging"
      aria-label={onSizeChange ? '客户分页' : '关联群分页'}
    >
      <span>
        共 {total} 条 · 第 {current} / {pages} 页
      </span>
      {onSizeChange && (
        <select
          aria-label="每页客户数"
          value={pageSize}
          disabled={loading}
          onChange={(e) => onSizeChange(Number(e.target.value))}
        >
          {[20, 50, 100].map((n) => (
            <option key={n} value={n}>
              {n} 条 / 页
            </option>
          ))}
        </select>
      )}
      <Button
        size="small"
        disabled={loading || current <= 1}
        onClick={() => onChange(Math.max(0, offset - pageSize))}
      >
        上一页
      </Button>
      {numbers.map((number, i) => (
        <span key={number} className="customer-page-number">
          {i > 0 && number > numbers[i - 1] + 1 && (
            <span aria-hidden="true">…</span>
          )}
          <Button
            size="small"
            variant={number === current ? 'primary' : 'ghost'}
            aria-label={`第 ${number} 页`}
            aria-current={number === current ? 'page' : undefined}
            disabled={loading || number === current}
            onClick={() => onChange((number - 1) * pageSize)}
          >
            {number}
          </Button>
        </span>
      ))}
      <Button
        size="small"
        disabled={loading || current >= pages}
        onClick={() => onChange(offset + pageSize)}
      >
        下一页
      </Button>
    </nav>
  );
}

/** 客户资料与同步记录只读取保存结果，切换客户时隔离查询。 */
export default function CustomersPage() {
  const [params, setParams] = useSearchParams();
  const workspace = useWorkspace();
  const [syncOpen, setSyncOpen] = useState(false);
  const [copyState, setCopyState] = useState({ uid: '', message: '' });
  const uid = params.get('uid') || '';
  const runId = params.get('sync') || '';
  const segment =
    customerFilters.find((item) => item.value === params.get('segment'))
      ?.value || 'large';
  const [pagination, setPagination] = useState({ key: '', offset: 0 });
  const [groupOffset, setGroupOffset] = useState(0);
  const [review, setReview] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [query, setQuery] = useState('');
  const [pageSize, setPageSize] = useState(20);
  const pageKey = `${segment}:${query}:${pageSize}`;
  const offset = pagination.key === pageKey ? pagination.offset : 0;
  const setOffset = (value: number) =>
    setPagination({ key: pageKey, offset: value });
  const [start, setStart] = useState(() =>
    localTime(new Date(Date.now() - 30 * 60 * 1000)),
  );
  const [end, setEnd] = useState(() => localTime(new Date()));
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const customers = useResource(
    `customers:${segment}:${offset}:${query}:${pageSize}`,
    (signal) => api.customers(offset, query, segment, signal, pageSize),
  );
  const selected =
    customers.data?.items.find((item) => item.uid === uid) ||
    (!uid ? customers.data?.items[0] : undefined);
  const selectedUid = selected?.uid || uid;
  const profile = workspace.data?.customers.find(
    (item) => item.uid === selectedUid,
  );
  const customerInfo = (value: string) =>
    workspace.data?.customers.find((item) => item.uid === value);
  const syncs = useResource('customer-syncs', api.customerSyncs, 3000);
  const detail = useResource(
    runId ? `customer-sync:${runId}` : null,
    (signal) => api.customerSync(runId, signal),
    1500,
    (data) => data.status === 'running' || data.status === 'queued',
  );
  const groups = useResource(
    review || selectedUid
      ? `customer-groups:${selectedUid}:${review}:${groupOffset}`
      : null,
    (signal) =>
      api.customerGroups(
        review ? undefined : selectedUid,
        review,
        groupOffset,
        signal,
      ),
  );
  const busy =
    submitting ||
    (syncs.data?.items.some(
      (item) => item.status === 'running' || item.status === 'queued',
    ) ??
      false);
  const run = detail.data;
  const selectedStart = run?.request.start;
  const selectedEnd = run?.request.end;
  useEffect(() => {
    if (selectedStart && selectedEnd) {
      setStart(localTime(new Date(selectedStart)));
      setEnd(localTime(new Date(selectedEnd)));
    }
  }, [selectedStart, selectedEnd]);
  const invalidRange =
    !start ||
    !end ||
    start >= end ||
    Date.parse(`${end}+08:00`) - Date.parse(`${start}+08:00`) > 86400000;
  const select = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next);
  };
  useEffect(() => {
    if (run?.status === 'succeeded' || run?.status === 'failed') {
      customers.refresh();
      workspace.refresh();
      groups.refresh();
      syncs.refresh();
    }
  }, [
    run?.status,
    customers.refresh,
    groups.refresh,
    syncs.refresh,
    workspace.refresh,
  ]);
  const refresh = () => {
    workspace.refresh();
    customers.refresh();
    groups.refresh();
    syncs.refresh();
    detail.refresh();
  };
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || invalidRange) return;
    setSubmitting(true);
    setSubmitError('');
    try {
      const created = await api.createCustomerSync({
        start: `${start}:00+08:00`,
        end: `${end}:00+08:00`,
      });
      select('sync', created.run_id);
      syncs.refresh();
    } catch (error) {
      setSubmitError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }
  const totals = (run?.cycles || []).reduce(
    (sum, item) => ({
      newCustomers: sum.newCustomers + item.new_customers,
      refreshed: sum.refreshed + item.refreshed_customers,
      reviews: sum.reviews + item.review_groups,
    }),
    { newCustomers: 0, refreshed: 0, reviews: 0 },
  );
  return (
    <main className="customers-page">
      <header className="customers-header">
        <div>
          <h1>
            客户管理{' '}
            <span className="heading-count">
              {customers.data?.total ?? '—'}
            </span>
          </h1>
          <p>维护客户分级、负责人和群归属</p>
        </div>
        <div className="customer-header-actions">
          <Button variant="primary" onClick={() => setSyncOpen(true)}>
            <RefreshCw size={15} />
            同步客户
          </Button>
          <Button onClick={refresh}>
            <RefreshCw size={15} />
            刷新
          </Button>
        </div>
      </header>
      <div className="customer-columns">
        <section className="customer-directory" aria-label="客户列表">
          <div className="customer-directory-tools">
            <div className="customer-filter">
              <label htmlFor="customer-segment">客户范围</label>
              <select
                id="customer-segment"
                value={segment}
                onChange={(event) => {
                  const next = new URLSearchParams(params);
                  next.set('segment', event.target.value);
                  next.delete('uid');
                  setParams(next);
                  setPagination({ key: '', offset: 0 });
                  setReview(false);
                  setGroupOffset(0);
                }}
              >
                {customerFilters.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
            <form
              className="customer-search"
              onSubmit={(event) => {
                event.preventDefault();
                setQuery(keyword.trim());
                setOffset(0);
              }}
            >
              <Search size={16} />
              <input
                aria-label="搜索客户 UID"
                placeholder="按客户 UID 搜索"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
              />
              <Button type="submit" size="small">
                查询
              </Button>
            </form>
          </div>
          {customers.error && (
            <ErrorNotice message={customers.error} retry={customers.refresh} />
          )}
          {!customers.data && customers.loading ? (
            <LoadingRows />
          ) : (
            <div className="customer-table-scroll">
              <table className="customer-table">
                <thead>
                  <tr>
                    <th>客户</th>
                    <th>分级</th>
                    <th>主 TAM</th>
                    <th>关联群</th>
                    <th>消息数</th>
                    <th>事件数</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {customers.data?.items.map((item) => (
                    <tr
                      key={item.uid}
                      className={selectedUid === item.uid ? 'selected' : ''}
                    >
                      <td>
                        <button
                          type="button"
                          className="customer-link"
                          aria-pressed={selectedUid === item.uid}
                          onClick={() => {
                            select('uid', item.uid);
                            setReview(false);
                            setGroupOffset(0);
                          }}
                        >
                          <Building2 size={18} />
                          <span>
                            <strong>
                              {customerInfo(item.uid)?.label ||
                                `客户 ${item.uid}`}
                            </strong>
                            <small>UID {item.uid}</small>
                          </span>
                        </button>
                      </td>
                      <td>
                        <div className="customer-level-cell">
                          <strong>{item.level || '未知'}</strong>
                          {item.is_super_large ? (
                            <span className="customer-tier-pill super">
                              <Crown size={11} />
                              超级大客
                            </span>
                          ) : null}
                        </div>
                        {item.level_state !== 'fresh' && (
                          <small className="customer-warning">
                            {freshness[item.level_state] || '待确认'}
                          </small>
                        )}
                      </td>
                      <td>
                        {item.tam || '—'}
                        {item.tam_state !== 'fresh' && (
                          <small className="customer-warning">
                            {freshness[item.tam_state] || '待确认'}
                          </small>
                        )}
                      </td>
                      <td>{item.group_count}</td>
                      <td>{customerInfo(item.uid)?.message_count ?? '—'}</td>
                      <td>{customerInfo(item.uid)?.event_count ?? '—'}</td>
                      <td>
                        <button
                          type="button"
                          className="customer-detail-link"
                          onClick={() => {
                            select('uid', item.uid);
                            setReview(false);
                            setGroupOffset(0);
                          }}
                        >
                          详情
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {customers.data?.total === 0 && (
                <div className="customer-empty">
                  <UsersRound size={24} />
                  <h3>
                    {query || segment !== 'all'
                      ? '没有符合筛选条件的客户'
                      : '还没有发现客户'}
                  </h3>
                  <p>
                    {query || segment !== 'all'
                      ? '可切换客户范围或调整 UID 搜索条件。'
                      : '同步一个有聊天的时间段后，客户资料会显示在这里。'}
                  </p>
                </div>
              )}
            </div>
          )}
          <Paging
            offset={offset}
            total={customers.data?.total || 0}
            pageSize={pageSize}
            loading={customers.loading}
            onSizeChange={(size) => {
              setPageSize(size);
              const next = new URLSearchParams(params);
              next.delete('uid');
              setParams(next);
            }}
            onChange={(value) => {
              setOffset(value);
              const next = new URLSearchParams(params);
              next.delete('uid');
              setParams(next);
            }}
          />
        </section>
        <aside className="customer-profile-panel" aria-label="客户资料">
          <h2>客户资料</h2>
          {selected ? (
            <>
              <h3 className="customer-profile-name">
                {profile?.label || `客户 ${selected.uid}`}
              </h3>
              <div className="customer-profile-uid">
                UID {selected.uid}
                <button
                  type="button"
                  aria-label="复制客户 UID"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(selected.uid);
                      setCopyState({ uid: selected.uid, message: '已复制' });
                    } catch {
                      setCopyState({
                        uid: selected.uid,
                        message: '复制失败，请手动复制 UID',
                      });
                    }
                  }}
                >
                  <Copy size={14} />
                </button>
              </div>
              <span role="status" className="customer-muted">
                {copyState.uid === selectedUid ? copyState.message : ''}
              </span>
              <div className="customer-profile-tier">
                <span
                  className={`customer-tier-pill ${selected.is_super_large ? 'super' : selected.segment === 'large' ? 'large' : 'pending'}`}
                >
                  {selected.is_super_large && <Crown size={12} />}
                  {selected.level || '未知'} ·{' '}
                  {selected.is_super_large
                    ? '超大客'
                    : segmentLabels[selected.segment]}
                </span>
              </div>
              <Button asChild className="customer-enter">
                <Link to={`/events?uid=${selected.uid}`}>
                  进入工作台
                  <ArrowUpRight size={16} />
                </Link>
              </Button>
              <section className="customer-owner">
                <h2>负责人</h2>
                <dl>
                  <dt>主 TAM</dt>
                  <dd>{selected.tam || '未知'}</dd>
                  <dt>备用 TAM</dt>
                  <dd>{selected.tam_b || '未知'}</dd>
                </dl>
                <p className="customer-muted">
                  {freshness[selected.tam_state] || '待确认'} · 最近查询{' '}
                  {formatTime(selected.attempted_at)}
                </p>
                {selected.level_state !== 'fresh' && (
                  <p className="customer-warning">
                    等级{freshness[selected.level_state] || '待确认'}
                    ，旧等级仅供参考。
                  </p>
                )}
              </section>
            </>
          ) : (
            <p className="customer-empty">
              {uid
                ? '当前列表中没有该客户，请调整筛选或选择其他客户。'
                : '选择客户查看资料'}
            </p>
          )}
          <div className="customer-group-heading">
            <h2>
              {review
                ? '归属待确认'
                : selectedUid
                  ? `关联群（${groups.data?.total ?? 0}）`
                  : '关联群'}
            </h2>
            <Button
              size="small"
              onClick={() => {
                setReview(!review);
                setGroupOffset(0);
              }}
            >
              {review ? '返回客户关联群' : '查看全部待确认群'}
            </Button>
          </div>
          {groups.error && (
            <ErrorNotice message={groups.error} retry={groups.refresh} />
          )}
          {groups.loading && !groups.data ? (
            <LoadingRows />
          ) : groups.data ? (
            <>
              <div className="customer-group-list">
                {groups.data.items.map((group) => (
                  <article key={`${group.source}:${group.group_id}`}>
                    <div>
                      <strong>{group.group_name || '群名未知'}</strong>
                      <span
                        className={
                          group.status === 'review' ? 'customer-warning' : ''
                        }
                      >
                        {group.status === 'review' ? '待确认' : '已确认'}
                      </span>
                    </div>
                    <small>
                      群 {group.group_id} · 客户 {group.uid || '未知'}
                    </small>
                    <p>{group.reason}</p>
                    {group.status === 'review' &&
                      group.candidate_uids.length > 0 && (
                        <p>候选客户：{group.candidate_uids.join('、')}</p>
                      )}
                  </article>
                ))}
              </div>
              {groups.data.total === 0 && (
                <p className="customer-empty">
                  {review
                    ? '目前没有归属待确认的群。'
                    : '该客户没有已保存的关联群。'}
                </p>
              )}
              <Paging
                offset={groupOffset}
                total={groups.data.total}
                onChange={setGroupOffset}
              />
            </>
          ) : (
            <p className="customer-empty">
              选择客户查看关联群，或查看全部待确认群。
            </p>
          )}
        </aside>
      </div>
      <Dialog open={syncOpen} onOpenChange={setSyncOpen}>
        <DialogContent className="customer-sync-dialog">
          <DialogHeading
            title="同步客户"
            description="从官方接口发现客户，更新等级、负责人和群归属。"
          />
          <form className="customer-sync-form" onSubmit={submit}>
            <div>
              <h2>同步客户</h2>
              <p>
                选择上海时间范围，最多一天。只更新客户资料，不进行事件分析。
              </p>
            </div>
            <label>
              开始时间
              <input
                type="datetime-local"
                value={start}
                onChange={(event) => setStart(event.target.value)}
                required
              />
            </label>
            <label>
              结束时间
              <input
                type="datetime-local"
                value={end}
                onChange={(event) => setEnd(event.target.value)}
                required
              />
            </label>
            <Button
              type="submit"
              variant="primary"
              disabled={busy || invalidRange}
            >
              {busy ? '同步进行中' : '开始同步'}
            </Button>
            {invalidRange && (
              <p className="customer-form-hint">
                请选择开始早于结束、且不超过一天的范围。
              </p>
            )}
          </form>
          {submitError && <ErrorNotice message={submitError} />}
          <aside className="customer-sync-panel" aria-label="客户同步记录">
            <h2>同步记录</h2>
            <p className="customer-muted">窗口内未出现的客户和群会继续保留。</p>
            {syncs.error && (
              <ErrorNotice message={syncs.error} retry={syncs.refresh} />
            )}
            <div className="customer-sync-list">
              {syncs.data?.items.map((item) => (
                <button
                  key={item.run_id}
                  type="button"
                  aria-pressed={runId === item.run_id}
                  className={runId === item.run_id ? 'selected' : ''}
                  onClick={() => select('sync', item.run_id)}
                >
                  <strong>{statuses[item.status]}</strong>
                  <span>
                    {item.completed_windows} / {item.total_windows} 个时段
                  </span>
                  <small>{formatTime(item.created_at)}</small>
                </button>
              ))}
            </div>
            {syncs.data?.total === 0 && (
              <p className="customer-empty">还没有同步记录。</p>
            )}
            {detail.error && (
              <ErrorNotice message={detail.error} retry={detail.refresh} />
            )}
            {run && (
              <section className="customer-sync-detail" aria-label="同步详情">
                <h3>{statuses[run.status]}</h3>
                <p>
                  {formatTime(run.request.start)} —{' '}
                  {formatTime(run.request.end)}
                </p>
                <progress
                  aria-label="客户同步进度"
                  value={run.completed_windows}
                  max={Math.max(1, run.total_windows)}
                />
                <p>
                  {run.completed_windows} / {run.total_windows} 个时段已保存
                </p>
                <dl>
                  <div>
                    <dt>新增客户</dt>
                    <dd>{totals.newCustomers}</dd>
                  </div>
                  <div>
                    <dt>刷新客户</dt>
                    <dd>{totals.refreshed}</dd>
                  </div>
                  <div>
                    <dt>待确认群次</dt>
                    <dd>{totals.reviews}</dd>
                  </div>
                </dl>
                {run.error && <ErrorNotice message={run.error} />}
                <small>
                  计数来自本次已保存结果。待确认群可能在多个时段重复出现。
                </small>
              </section>
            )}
          </aside>
        </DialogContent>
      </Dialog>
    </main>
  );
}
