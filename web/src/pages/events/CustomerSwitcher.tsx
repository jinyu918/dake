import * as Popover from '@radix-ui/react-popover';
import { Check, ChevronDown, Crown, Search, UsersRound } from 'lucide-react';
import { useMemo, useState } from 'react';
import { savedEventCount } from '@/lib/customerEvents';
import { STRATEGIC_CUSTOMER } from '@/lib/customerTier';

export type CustomerTierFilter = 'all' | 'super_large' | 'large' | 'active';

export function isSuperLargeCustomer(customer?: Partial<API.CustomerResponse>) {
  if (!customer) return false;
  if (customer.is_super_large) return true;
  return (
    customer.level_state === 'fresh' &&
    (customer.level === 'L9' || customer.level === 'L10')
  );
}

export function isLargeCustomer(customer?: Partial<API.CustomerResponse>) {
  if (!customer) return false;
  return customer.segment === 'large' || isSuperLargeCustomer(customer);
}

export function customerTierBadge(customer?: Partial<API.CustomerResponse>) {
  if (!customer) return null;
  if (isSuperLargeCustomer(customer)) {
    const level = customer.level || 'L9+';
    return {
      level,
      text: `${level} · ${level === 'L10' ? STRATEGIC_CUSTOMER : '超级大客'}`,
      short: `${level} ${level === 'L10' ? STRATEGIC_CUSTOMER : '超大客'}`,
      kind: 'super' as const,
    };
  }
  if (isLargeCustomer(customer)) {
    const level = customer.level || 'L6+';
    return {
      level,
      text: `${level} · 大客户`,
      short: `${level} 大客`,
      kind: 'large' as const,
    };
  }
  if (customer.level) {
    return {
      level: customer.level,
      text: `${customer.level} · 待确认`,
      short: `${customer.level} 待确认`,
      kind: 'pending' as const,
    };
  }
  return null;
}

function levelNumber(customer: API.CustomerResponse) {
  if (customer.level_state !== 'fresh' || !customer.level) return -1;
  const matched = /^L(\d+)$/.exec(customer.level);
  return matched ? Number(matched[1]) : -1;
}

function sortCustomers(items: API.CustomerResponse[]): API.CustomerResponse[] {
  return [...items].sort((left, right) => {
    const superDiff =
      Number(isSuperLargeCustomer(right)) - Number(isSuperLargeCustomer(left));
    if (superDiff !== 0) return superDiff;
    const levelDiff = levelNumber(right) - levelNumber(left);
    if (levelDiff !== 0) return levelDiff;
    const eventDiff =
      (savedEventCount(right) ?? 0) - (savedEventCount(left) ?? 0);
    if (eventDiff !== 0) return eventDiff;
    return left.uid.localeCompare(right.uid);
  });
}

export function CustomerSwitcher({
  uid,
  customers,
  current,
  onSelect,
}: {
  uid: string;
  customers: API.CustomerResponse[];
  current?: API.CustomerResponse;
  onSelect: (nextUid: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [keyword, setKeyword] = useState('');
  const filtered = useMemo(() => {
    const query = keyword.trim().toLowerCase();
    return sortCustomers(customers).filter((item) =>
      [item.label, item.uid, item.tam, item.tam_b, ...(item.group_names || [])]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(query),
    );
  }, [customers, keyword]);

  const isAll = uid === 'all';
  const currentBadge = !isAll ? customerTierBadge(current) : null;

  function choose(nextUid: string) {
    onSelect(nextUid);
    setOpen(false);
  }

  return (
    <div className="workspace-customer">
      <select
        className="sr-only"
        aria-label="客户范围"
        value={uid}
        onChange={(event) => choose(event.target.value)}
      >
        <option value="all">全部客户</option>
        {!isAll && !customers.some((item) => item.uid === uid) && (
          <option value={uid}>客户 {uid}</option>
        )}
        {customers.map((item) => (
          <option key={item.uid} value={item.uid}>
            {item.label} · {item.uid}
          </option>
        ))}
      </select>
      <Popover.Root
        open={open}
        onOpenChange={(next) => {
          if (next) setKeyword('');
          setOpen(next);
        }}
      >
        <Popover.Trigger asChild>
          <button
            type="button"
            className={`customer-switcher-trigger ${isAll ? 'is-all' : ''}`}
            aria-label="切换客户"
          >
            <UsersRound size={16} className="customer-trigger-icon" />
            <span className="customer-trigger-copy">
              <span className="customer-trigger-name">
                {isAll ? '全部客户' : current?.label || `客户 ${uid}`}
              </span>
              {!isAll && <small>UID {uid}</small>}
            </span>
            {currentBadge?.kind === 'super' && (
              <span className="customer-tier-pill super">
                <Crown size={12} />
                {currentBadge.short}
              </span>
            )}
            <ChevronDown size={14} className="customer-trigger-chevron" />
          </button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            className="customer-switcher-popover"
            align="start"
            sideOffset={8}
            collisionPadding={12}
            aria-label="切换与检索大客户"
          >
            <label className="customer-popover-search">
              <Search size={14} />
              <input
                aria-label="搜索客户"
                placeholder="搜索客户名称 / UID"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
              />
            </label>

            <button
              type="button"
              className={`customer-all-option ${isAll ? 'is-active' : ''}`}
              onClick={() => choose('all')}
            >
              <div className="customer-all-option-main">
                <UsersRound size={18} />
                <strong>全部客户</strong>
              </div>
              {isAll && <Check size={18} className="customer-selected-check" />}
            </button>

            <fieldset
              className="customer-option-list"
              aria-label="可选客户列表"
            >
              {filtered.length ? (
                filtered.map((item) => {
                  const badge = customerTierBadge(item);
                  const active = !isAll && item.uid === uid;
                  return (
                    <button
                      key={item.uid}
                      type="button"
                      aria-pressed={active}
                      className={`customer-option-item ${active ? 'is-active' : ''}`}
                      onClick={() => choose(item.uid)}
                    >
                      <UsersRound size={18} className="customer-row-icon" />
                      <span className="customer-option-copy">
                        <span className="customer-option-name">
                          <strong>{item.label}</strong>
                          {badge?.kind === 'super' && (
                            <span className="customer-tier-pill super">
                              <Crown size={11} />
                              {badge.short}
                            </span>
                          )}
                        </span>
                        <small>UID {item.uid}</small>
                      </span>
                      {active && (
                        <Check size={18} className="customer-selected-check" />
                      )}
                    </button>
                  );
                })
              ) : (
                <p className="customer-option-empty">
                  没有匹配的客户，请尝试其他名称或 UID。
                </p>
              )}
            </fieldset>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </div>
  );
}
