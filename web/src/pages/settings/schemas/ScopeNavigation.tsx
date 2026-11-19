import { Plus } from 'lucide-react';
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
  return (
    <nav className="scope-navigation" aria-label="产品与事件类型目录">
      <div className="scope-select-bar">
        <label htmlFor="schema-scope">配置范围</label>
        <select
          id="schema-scope"
          className="schemas-select"
          value={`${selected.scopeType}:${selected.scopeKey}`}
          onChange={(event) => {
            const [type, key] = event.target.value.split(':');
            onSelect({
              scopeType: type === 'product' ? 'product' : 'event_type',
              scopeKey: key,
            });
          }}
        >
          {groups.map((group) => (
            <optgroup key={group.type} label={group.title}>
              {group.items.map((item) => (
                <option key={item.key} value={`${group.type}:${item.key}`}>
                  {item.name}
                  {!item.enabled && '（已停用）'}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <Button size="small" variant="ghost" onClick={onNewProduct}>
          <Plus size={14} />
          新增产品
        </Button>
      </div>
      <div className="scope-directory">
        {groups.map((group) => (
          <section key={group.type} aria-label={group.title}>
            <div className="scope-group-heading">
              <h2>{group.title}</h2>
              {group.type === 'product' && (
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label="新增产品"
                  title="新增产品"
                  onClick={onNewProduct}
                >
                  <Plus size={16} />
                </Button>
              )}
            </div>
            <ul>
              {group.items.map((item) => (
                <li key={item.key}>
                  <button
                    type="button"
                    aria-current={
                      selected.scopeType === group.type &&
                      selected.scopeKey === item.key
                        ? 'true'
                        : undefined
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
          </section>
        ))}
      </div>
    </nav>
  );
}
