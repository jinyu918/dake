import { Plus, Search } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { EVENT_TYPE_SCOPES, type SelectedScope } from './schemaLabels';

type ScopeNavigationProps = {
  products: API.ProductSchemaItem[];
  selected: SelectedScope;
  onSelect: (scope: SelectedScope) => void;
  onNewProduct: () => void;
};

/** 同一份目录在桌面分组展示，在窄屏直接选择配置对象。 */
export function ScopeNavigation({
  products,
  selected,
  onSelect,
  onNewProduct,
}: ScopeNavigationProps) {
  const [query, setQuery] = useState('');
  const term = query.trim().toLowerCase();
  const groups = [
    {
      type: 'product',
      title: '产品目录',
      items: products.map((p) => ({
        key: p.product_key,
        name: p.name,
        enabled: p.enabled,
      })),
    },
    {
      type: 'event_type',
      title: '事件类型',
      items: EVENT_TYPE_SCOPES.map((type) => ({
        key: type,
        name: type,
        enabled: true,
      })),
    },
  ] as const;
  const group = selected.scopeType === 'product' ? groups[0] : groups[1];
  const isProduct = group.type === 'product';
  const matches = group.items.filter((item) =>
    `${item.name} ${item.key}`.toLowerCase().includes(term),
  );
  return (
    <nav className="scope-navigation" aria-label="产品与事件类型目录">
      <fieldset className="scope-kind" aria-label="目录类别">
        {groups.map((entry) => (
          <button
            key={entry.type}
            type="button"
            aria-pressed={entry.type === group.type}
            onClick={() => {
              if (entry.type === group.type) return;
              setQuery('');
              onSelect({
                scopeType: entry.type,
                scopeKey: entry.items[0]?.key ?? '',
              });
            }}
          >
            {entry.title}
          </button>
        ))}
      </fieldset>
      <div className="scope-select-bar">
        <label htmlFor="schema-scope">配置范围</label>
        <select
          id="schema-scope"
          className="schemas-select"
          value={`${group.type}:${selected.scopeKey}`}
          onChange={(event) =>
            onSelect({
              scopeType: group.type,
              scopeKey: event.target.value.split(':')[1],
            })
          }
        >
          {group.items.map((item) => (
            <option key={item.key} value={`${group.type}:${item.key}`}>
              {item.name}
              {!item.enabled && '（已停用）'}
            </option>
          ))}
        </select>
        {isProduct && (
          <Button size="small" variant="ghost" onClick={onNewProduct}>
            <Plus size={14} />
            新增产品
          </Button>
        )}
      </div>
      <div className="scope-directory">
        {isProduct && (
          <div className="scope-product-tools">
            <label className="scope-search">
              <Search size={15} aria-hidden="true" />
              <input
                type="search"
                aria-label="搜索产品"
                placeholder="搜索产品"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <Button
              size="icon"
              variant="ghost"
              aria-label="新增产品"
              title="新增产品"
              onClick={onNewProduct}
            >
              <Plus size={16} />
            </Button>
          </div>
        )}
        <div className="scope-directory-list" key={group.type}>
          <ul aria-label={group.title}>
            {(isProduct ? matches : group.items).map((item) => (
              <li key={item.key}>
                <button
                  type="button"
                  aria-current={
                    selected.scopeKey === item.key ? 'true' : undefined
                  }
                  onClick={() =>
                    onSelect({ scopeType: group.type, scopeKey: item.key })
                  }
                >
                  <span>{item.name}</span>
                  {!item.enabled && <small>已停用</small>}
                </button>
              </li>
            ))}
          </ul>
          {isProduct && matches.length === 0 && (
            <p className="scope-no-results">无匹配项</p>
          )}
        </div>
      </div>
    </nav>
  );
}
