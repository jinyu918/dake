import {
  Check,
  ChevronDown,
  History,
  Pencil,
  Plus,
  RefreshCw,
} from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { ErrorNotice, LoadingRows } from '@/components/Feedback';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeading } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { productDisplayName } from '@/lib/productLabels';
import { useResource } from '@/lib/useResource';
import { errorMessage } from '@/lib/utils';
import { api } from '@/services/api';
import { AnalyticsResults } from './AnalyticsResults';
import { FieldList } from './FieldList';
import { ScopeNavigation } from './ScopeNavigation';
import {
  AGG_LABELS,
  DATA_TYPE_LABELS,
  EVENT_TYPE_SCOPES,
  type SelectedScope,
} from './schemaLabels';
import './style.css';

/** 根据用户填写的展示单位自动匹配口语换算规则，免去手工选择换算族的负担。 */
function inferUnitFamily(unit: string): API.AttributeFieldInput['unit_family'] {
  const clean = unit.trim().toLowerCase();
  if (!clean) return 'count_scale';
  if (clean.includes('百万') && clean.includes('token'))
    return 'million_tokens';
  if (['bps', 'kbps', 'mbps', 'gbps', '带宽'].some((k) => clean.includes(k))) {
    return 'bandwidth_mbps';
  }
  if (['mb', 'gb', 'tb', 'pb'].includes(clean)) return 'storage_tb';
  if (['元', 'cny', 'rmb', '¥'].includes(clean)) return 'currency_cny';
  if (['ms', '毫秒', '秒', 's'].includes(clean)) return 'duration_ms';
  return 'count_scale';
}

type FieldDraft = {
  fieldId?: string;
  isEdit: boolean;
  scope_type: 'product' | 'event_type';
  scope_key: string;
  field_key: string;
  label: string;
  data_type: 'number' | 'enum' | 'boolean' | 'string';
  unit: string;
  unit_family: API.AttributeFieldInput['unit_family'];
  enum_text: string;
  agg_ops: API.AttributeFieldInput['agg_ops'];
  applicable_types: string[];
  extraction_hint: string;
  sort_order: number;
  enabled: boolean;
};

type ProductDraft = {
  isEdit: boolean;
  product_key: string;
  name: string;
  description: string;
  keywords_text: string;
  sort_order: number;
  enabled: boolean;
};

type DeleteTarget =
  | { kind: 'field'; id: string; label: string }
  | { kind: 'product'; key: string; label: string };

/** 可视化管理产品目录、结构化事件属性模板，并查看多维指标汇总分析。 */
export default function AttributeSchemasPage() {
  const [tab, setTab] = useState<'config' | 'analytics'>('config');
  const [selection, setSelected] = useState<SelectedScope>();
  const [analyticsProduct, setAnalyticsProduct] = useState('');
  const [analyticsType, setAnalyticsType] = useState('');

  const [fieldDraft, setFieldDraft] = useState<FieldDraft>();
  const [productDraft, setProductDraft] = useState<ProductDraft>();
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget>();
  const [backfillOpen, setBackfillOpen] = useState(false);
  const [backfillScope, setBackfillScope] = useState<'all' | 'current'>('all');
  const [onlyMissing, setOnlyMissing] = useState(false);

  const [busy, setBusy] = useState(false);
  const [togglingId, setTogglingId] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const opener = useRef<HTMLElement | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const dialogFocus = {
    onOpenAutoFocus: () => {
      opener.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
    },
    onCloseAutoFocus: (event: Event) => {
      event.preventDefault();
      (opener.current?.isConnected ? opener.current : heading.current)?.focus();
    },
  };

  const registry = useResource('attribute-schemas', api.attributeSchemas);
  const analytics = useResource(
    tab === 'analytics'
      ? `product-analytics:${analyticsProduct}:${analyticsType}`
      : null,
    (signal) => api.productAnalytics(analyticsProduct, analyticsType, signal),
  );

  const products = registry.data?.products || [];
  const fields = registry.data?.fields || [];

  const productNameMap = useMemo(
    () => new Map(products.map((p) => [p.product_key, p.name])),
    [products],
  );

  const currentProduct =
    selection?.scopeType === 'event_type'
      ? undefined
      : (products.find(
          (product) => product.product_key === selection?.scopeKey,
        ) ?? products[0]);
  const selected: SelectedScope = currentProduct
    ? { scopeType: 'product', scopeKey: currentProduct.product_key }
    : selection?.scopeType === 'event_type'
      ? selection
      : { scopeType: 'event_type', scopeKey: EVENT_TYPE_SCOPES[0] };
  const currentScopeTitle = currentProduct?.name ?? selected.scopeKey;
  const scopedFields = fields.filter(
    (field) =>
      field.scope_type === selected.scopeType &&
      field.scope_key === selected.scopeKey,
  );
  const savingField = busy || Boolean(togglingId) || registry.loading;

  function closeDialog() {
    if (busy) return;
    setError('');
    setFieldDraft(undefined);
    setProductDraft(undefined);
    setDeleteTarget(undefined);
    setBackfillOpen(false);
  }

  function onDialogOpenChange(open: boolean) {
    if (!open) closeDialog();
  }

  function openNewProduct() {
    setError('');
    setProductDraft({
      isEdit: false,
      product_key: '',
      name: '',
      description: '',
      keywords_text: '',
      sort_order: (products.length + 1) * 10,
      enabled: true,
    });
  }

  function openEditProduct(prod: API.ProductSchemaItem) {
    setError('');
    setProductDraft({
      isEdit: true,
      product_key: prod.product_key,
      name: prod.name,
      description: prod.description || '',
      keywords_text: (prod.keywords || []).join(', '),
      sort_order: prod.sort_order ?? 100,
      enabled: prod.enabled,
    });
  }

  function openNewField() {
    setError('');
    setFieldDraft({
      isEdit: false,
      scope_type: selected.scopeType,
      scope_key: selected.scopeKey,
      field_key: '',
      label: '',
      data_type: 'number',
      unit: '',
      unit_family: 'count_scale',
      enum_text: '',
      agg_ops: ['sum', 'max'],
      applicable_types:
        selected.scopeType === 'event_type' ? [selected.scopeKey] : [],
      extraction_hint: '',
      sort_order: (scopedFields.length + 1) * 10,
      enabled: true,
    });
  }

  function openEditField(item: API.AttributeFieldSchemaItem) {
    setError('');
    setFieldDraft({
      fieldId: item.field_id,
      isEdit: true,
      scope_type: item.scope_type,
      scope_key: item.scope_key,
      field_key: item.field_key,
      label: item.label,
      data_type: item.data_type,
      unit: item.unit || '',
      unit_family: item.unit_family || 'none',
      enum_text: (item.enum_options || []).join(', '),
      agg_ops: item.agg_ops || [],
      applicable_types: item.applicable_types || [],
      extraction_hint: item.extraction_hint,
      sort_order: item.sort_order ?? 100,
      enabled: item.enabled,
    });
  }

  async function toggleFieldEnabled(item: API.AttributeFieldSchemaItem) {
    if (!registry.data || savingField) return;
    setTogglingId(item.field_id);
    setError('');
    setNotice('');
    try {
      await api.saveAttributeField({
        expected_version: registry.data.version,
        field_id: item.field_id,
        field: {
          scope_type: item.scope_type,
          scope_key: item.scope_key,
          field_key: item.field_key,
          label: item.label,
          data_type: item.data_type,
          unit: item.unit || '',
          unit_family: item.unit_family || 'none',
          enum_options: item.enum_options || [],
          agg_ops: item.agg_ops || [],
          applicable_types: item.applicable_types || [],
          extraction_hint: item.extraction_hint,
          sort_order: item.sort_order ?? 100,
          enabled: !item.enabled,
        },
      });
      setNotice(`字段「${item.label}」已${!item.enabled ? '启用' : '停用'}。`);
      registry.refresh();
      analytics.refresh();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setTogglingId('');
    }
  }

  async function submitProduct() {
    if (!productDraft || !registry.data || busy) return;
    if (
      !productDraft.isEdit &&
      products.some(
        (product) => product.product_key === productDraft.product_key.trim(),
      )
    ) {
      setError('该产品编码已存在。请更换编码，或在目录中编辑已有产品。');
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const keywords = productDraft.keywords_text
        .split(/[,，\n]/)
        .map((s) => s.trim())
        .filter(Boolean);
      await api.saveProductSchema({
        expected_version: registry.data.version,
        product: {
          product_key: productDraft.product_key.trim(),
          name: productDraft.name.trim(),
          description: productDraft.description.trim(),
          keywords,
          sort_order: productDraft.sort_order,
          enabled: productDraft.enabled,
        },
      });
      setSelected({
        scopeType: 'product',
        scopeKey: productDraft.product_key.trim(),
      });
      setProductDraft(undefined);
      setNotice('产品已保存。');
      registry.refresh();
      analytics.refresh();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function submitField() {
    if (!fieldDraft || !registry.data || busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const enumOptions =
        fieldDraft.data_type === 'enum'
          ? fieldDraft.enum_text
              .split(/[,，\n]/)
              .map((s) => s.trim())
              .filter(Boolean)
          : [];
      const aggOps: API.AttributeFieldInput['agg_ops'] =
        fieldDraft.data_type === 'number'
          ? fieldDraft.agg_ops?.length
            ? fieldDraft.agg_ops
            : ['sum', 'max']
          : ['terms'];
      const unitClean =
        fieldDraft.data_type === 'number' ? fieldDraft.unit.trim() : '';
      const autoFamily =
        fieldDraft.data_type === 'number' ? fieldDraft.unit_family : 'none';
      await api.saveAttributeField({
        expected_version: registry.data.version,
        field_id: fieldDraft.fieldId,
        field: {
          scope_type: fieldDraft.scope_type,
          scope_key: fieldDraft.scope_key,
          field_key: fieldDraft.field_key.trim(),
          label: fieldDraft.label.trim(),
          data_type: fieldDraft.data_type,
          unit: unitClean,
          unit_family: autoFamily,
          enum_options: enumOptions,
          agg_ops: aggOps,
          applicable_types: fieldDraft.applicable_types,
          extraction_hint: fieldDraft.extraction_hint.trim(),
          sort_order: fieldDraft.sort_order,
          enabled: fieldDraft.enabled,
        },
      });
      setFieldDraft(undefined);
      setNotice('字段已保存，将在后续事件处理中生效。');
      registry.refresh();
      analytics.refresh();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget || !registry.data || busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      if (deleteTarget.kind === 'field') {
        await api.deleteAttributeField({
          expected_version: registry.data.version,
          field_id: deleteTarget.id,
        });
        setNotice(`已删除自定义字段「${deleteTarget.label}」。`);
      } else {
        await api.deleteProductSchema({
          expected_version: registry.data.version,
          product_key: deleteTarget.key,
        });
        setSelected(undefined);
        setNotice(`已删除自定义产品「${deleteTarget.label}」。`);
      }
      setDeleteTarget(undefined);
      registry.refresh();
      analytics.refresh();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function runBackfill() {
    if (busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const payload: API.AttributeBackfillRequest = {
        only_missing: onlyMissing,
        limit: 150,
        use_llm: true,
      };
      if (backfillScope === 'current') {
        if (backfillProduct) payload.product_key = backfillProduct;
        if (backfillEventType) payload.event_type = backfillEventType;
      }
      const res = await api.backfillAttributeSchemas(payload);
      setBackfillOpen(false);
      setNotice(
        `已完成历史事件字段补齐：共扫描 ${res.scanned} 条事件，成功更新 ${res.updated} 条（智能提取 ${res.llm_enriched} 条${res.failed ? `，失败 ${res.failed} 条` : ''}）。`,
      );
      analytics.refresh();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  function toggleNumberAggOp(op: 'sum' | 'avg' | 'max') {
    if (!fieldDraft) return;
    const current = new Set(fieldDraft.agg_ops || []);
    if (current.has(op)) {
      if (current.size > 1) current.delete(op);
    } else {
      current.add(op);
    }
    setFieldDraft({
      ...fieldDraft,
      agg_ops: Array.from(current) as API.AttributeFieldInput['agg_ops'],
    });
  }

  function toggleApplicableType(etype: string) {
    if (!fieldDraft) return;
    const current = new Set(fieldDraft.applicable_types || []);
    if (current.has(etype)) {
      current.delete(etype);
    } else {
      current.add(etype);
    }
    setFieldDraft({
      ...fieldDraft,
      applicable_types: Array.from(current),
    });
  }

  const hasAnalyticsFilter = Boolean(analyticsProduct || analyticsType);
  const backfillProduct =
    tab === 'analytics' ? analyticsProduct : currentProduct?.product_key;
  const backfillEventType =
    tab === 'analytics'
      ? analyticsType
      : selected.scopeType === 'event_type'
        ? selected.scopeKey
        : '';
  const backfillLabel = [
    backfillProduct && productDisplayName(backfillProduct, productNameMap),
    backfillEventType,
  ]
    .filter(Boolean)
    .join(' · ');

  function openBackfill() {
    setError('');
    setBackfillScope('all');
    setBackfillOpen(true);
  }

  return (
    <main className="page schemas-page">
      <header className="page-header">
        <div>
          <h1 ref={heading} tabIndex={-1}>
            属性配置
          </h1>
          <p>选择要从聊天中提取的信息。</p>
        </div>
        <div className="schemas-header-actions">
          <Button
            variant="secondary"
            disabled={!registry.data}
            onClick={openBackfill}
          >
            <History size={15} />
            补齐历史字段
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="刷新"
            title={
              registry.data ? `刷新配置（v${registry.data.version}）` : '刷新'
            }
            disabled={registry.loading || analytics.loading}
            onClick={() => {
              registry.refresh();
              analytics.refresh();
            }}
          >
            <RefreshCw
              size={15}
              className={
                registry.loading || analytics.loading ? 'spinning' : ''
              }
            />
          </Button>
        </div>
      </header>

      {notice && (
        <p role="status" className="schemas-save-notice">
          <Check size={16} aria-hidden="true" />
          {notice}
        </p>
      )}
      {registry.error && (
        <ErrorNotice message={registry.error} retry={registry.refresh} />
      )}
      {error &&
        !fieldDraft &&
        !productDraft &&
        !backfillOpen &&
        !deleteTarget && (
          <ErrorNotice message={error} retry={registry.refresh} />
        )}

      <Tabs
        value={tab}
        onValueChange={(val) =>
          setTab(val === 'analytics' ? 'analytics' : 'config')
        }
      >
        <TabsList aria-label="属性配置视图">
          <TabsTrigger value="config">字段管理</TabsTrigger>
          <TabsTrigger value="analytics">指标汇总</TabsTrigger>
        </TabsList>

        <TabsContent value="config">
          {registry.loading && !registry.data ? (
            <LoadingRows />
          ) : registry.data ? (
            <div className="schemas-layout">
              <ScopeNavigation
                products={products}
                selected={selected}
                onSelect={setSelected}
                onNewProduct={openNewProduct}
              />
              <section
                className="scope-workspace"
                aria-labelledby="scope-heading"
              >
                <div className="scope-heading">
                  <h2 id="scope-heading">
                    <span className="scope-heading-name">
                      {currentScopeTitle}
                    </span>
                    <span className="scope-heading-mobile">字段列表</span>
                    <small>
                      {scopedFields.length} 个字段
                      {currentProduct &&
                        !currentProduct.enabled &&
                        ' · 产品已停用'}
                    </small>
                  </h2>
                  <Button variant="primary" onClick={openNewField}>
                    <Plus size={16} />
                    新增字段
                  </Button>
                </div>
                {currentProduct ? (
                  <div className="scope-options">
                    {(currentProduct.description ||
                      currentProduct.keywords.length > 0) && (
                      <details
                        className="scope-keywords"
                        key={currentProduct.product_key}
                      >
                        <summary>
                          <ChevronDown
                            size={14}
                            className="scope-rule-chevron"
                            aria-hidden="true"
                          />
                          产品识别规则
                        </summary>
                        {currentProduct.description && (
                          <p>{currentProduct.description}</p>
                        )}
                        {currentProduct.keywords.length > 0 && (
                          <p>
                            <strong>识别词</strong>{' '}
                            {currentProduct.keywords.join('、')}
                          </p>
                        )}
                      </details>
                    )}
                    <div className="scope-actions">
                      <Button
                        variant="ghost"
                        size="small"
                        onClick={() => openEditProduct(currentProduct)}
                      >
                        <Pencil size={14} />
                        编辑产品
                      </Button>
                      {!currentProduct.builtin && (
                        <Button
                          variant="ghost"
                          size="small"
                          className="schemas-danger-btn"
                          onClick={() => {
                            setError('');
                            setDeleteTarget({
                              kind: 'product',
                              key: currentProduct.product_key,
                              label: currentProduct.name,
                            });
                          }}
                        >
                          删除产品
                        </Button>
                      )}
                    </div>
                  </div>
                ) : (
                  <p className="scope-description">
                    适用于各产品的{selected.scopeKey}事件。
                  </p>
                )}
                <FieldList
                  fields={scopedFields}
                  saving={savingField}
                  onEdit={openEditField}
                  onToggle={toggleFieldEnabled}
                  onDelete={(field) => {
                    setError('');
                    setDeleteTarget({
                      kind: 'field',
                      id: field.field_id,
                      label: field.label,
                    });
                  }}
                />
              </section>
            </div>
          ) : null}
        </TabsContent>

        <TabsContent value="analytics">
          <div className="analytics-filter-bar">
            <label>
              产品
              <select
                className="schemas-select"
                value={analyticsProduct}
                onChange={(e) => setAnalyticsProduct(e.target.value)}
              >
                <option value="">全部产品</option>
                {products.map((product) => (
                  <option key={product.product_key} value={product.product_key}>
                    {product.name}
                  </option>
                ))}
                <option value="unclassified">待识别产品</option>
              </select>
            </label>
            <label>
              事件类型
              <select
                className="schemas-select"
                value={analyticsType}
                onChange={(e) => setAnalyticsType(e.target.value)}
              >
                <option value="">全部事件类型</option>
                {EVENT_TYPE_SCOPES.map((type) => (
                  <option key={type}>{type}</option>
                ))}
              </select>
            </label>
            {hasAnalyticsFilter && (
              <Button
                variant="ghost"
                size="small"
                onClick={() => {
                  setAnalyticsProduct('');
                  setAnalyticsType('');
                }}
              >
                清除筛选
              </Button>
            )}
          </div>
          {analytics.error && (
            <ErrorNotice message={analytics.error} retry={analytics.refresh} />
          )}
          {analytics.loading && !analytics.data ? (
            <LoadingRows />
          ) : analytics.data ? (
            <AnalyticsResults
              data={analytics.data}
              productNames={productNameMap}
              onSelectProduct={setAnalyticsProduct}
            />
          ) : null}
        </TabsContent>
      </Tabs>

      {/* 新增 / 编辑结构化字段弹窗 */}
      <Dialog open={Boolean(fieldDraft)} onOpenChange={onDialogOpenChange}>
        <DialogContent
          {...dialogFocus}
          className="dialog-centered schemas-dialog"
        >
          <DialogHeading
            title={fieldDraft?.isEdit ? '编辑字段' : '新增字段'}
            description={currentScopeTitle}
          />
          {error && fieldDraft && <ErrorNotice message={error} />}
          {fieldDraft && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void submitField();
              }}
            >
              {fieldDraft.isEdit && (
                <div className="schemas-readonly-meta">
                  <span>
                    字段编码：<code>{fieldDraft.field_key}</code>
                  </span>
                  <span>·</span>
                  <span>
                    数据类型：{DATA_TYPE_LABELS[fieldDraft.data_type]}
                  </span>
                </div>
              )}

              <div className="schemas-form-grid">
                <div
                  className={`schemas-form-field ${fieldDraft.isEdit && fieldDraft.data_type !== 'number' ? 'full' : ''}`}
                >
                  <label htmlFor="field-label">字段名称 *</label>
                  <Input
                    id="field-label"
                    required
                    maxLength={64}
                    placeholder="如：申请带宽"
                    value={fieldDraft.label}
                    onChange={(e) =>
                      setFieldDraft({ ...fieldDraft, label: e.target.value })
                    }
                  />
                </div>

                {!fieldDraft.isEdit && (
                  <>
                    <div className="schemas-form-field">
                      <label htmlFor="field-key">字段编码 *</label>
                      <Input
                        id="field-key"
                        required
                        minLength={2}
                        maxLength={48}
                        pattern="[a-z][a-z0-9_]{1,47}"
                        title="以小写字母开头，可用小写字母、数字和下划线"
                        placeholder="如：requested_bandwidth"
                        value={fieldDraft.field_key}
                        onChange={(e) =>
                          setFieldDraft({
                            ...fieldDraft,
                            field_key: e.target.value,
                          })
                        }
                      />
                    </div>
                    <div
                      className={`schemas-form-field ${fieldDraft.data_type !== 'number' ? 'full' : ''}`}
                    >
                      <label htmlFor="field-dtype">字段类型</label>
                      <select
                        id="field-dtype"
                        className="schemas-select"
                        value={fieldDraft.data_type}
                        onChange={(e) => {
                          const dt = e.target.value as FieldDraft['data_type'];
                          setFieldDraft({
                            ...fieldDraft,
                            data_type: dt,
                            unit: '',
                            unit_family:
                              dt === 'number' ? 'count_scale' : 'none',
                            agg_ops:
                              dt === 'number' ? ['sum', 'max'] : ['terms'],
                          });
                        }}
                      >
                        {Object.entries(DATA_TYPE_LABELS).map(
                          ([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ),
                        )}
                      </select>
                    </div>
                  </>
                )}

                {fieldDraft.data_type === 'number' && (
                  <div className="schemas-form-field">
                    <label htmlFor="field-unit">计量单位</label>
                    <Input
                      id="field-unit"
                      maxLength={32}
                      placeholder="如：Mbps"
                      value={fieldDraft.unit}
                      onChange={(e) =>
                        setFieldDraft({
                          ...fieldDraft,
                          unit: e.target.value,
                          unit_family: inferUnitFamily(e.target.value),
                        })
                      }
                    />
                  </div>
                )}

                {fieldDraft.data_type === 'enum' && (
                  <div className="schemas-form-field full">
                    <label htmlFor="field-enums">可选值 *</label>
                    <Input
                      id="field-enums"
                      required
                      placeholder="至少两项，用逗号分隔"
                      value={fieldDraft.enum_text}
                      onChange={(e) =>
                        setFieldDraft({
                          ...fieldDraft,
                          enum_text: e.target.value,
                        })
                      }
                    />
                  </div>
                )}

                <div className="schemas-form-field full">
                  <label htmlFor="field-hint">提取条件 *</label>
                  <textarea
                    id="field-hint"
                    required
                    className="schemas-textarea"
                    minLength={2}
                    maxLength={512}
                    placeholder="如：客户明确说出申请带宽时，记录目标带宽。"
                    value={fieldDraft.extraction_hint}
                    onChange={(e) =>
                      setFieldDraft({
                        ...fieldDraft,
                        extraction_hint: e.target.value,
                      })
                    }
                  />
                </div>
              </div>

              {(fieldDraft.data_type === 'number' ||
                fieldDraft.scope_type === 'product') && (
                <details className="schemas-advanced">
                  <summary>
                    {fieldDraft.data_type === 'number' &&
                    fieldDraft.scope_type === 'product'
                      ? '统计方式与适用事件'
                      : fieldDraft.data_type === 'number'
                        ? '统计方式'
                        : '适用事件'}
                  </summary>
                  <div className="schemas-form-grid mt-3">
                    {fieldDraft.data_type === 'number' && (
                      <div className="schemas-form-field full">
                        <span className="schemas-field-title">统计方式</span>
                        <div className="schemas-check-chips">
                          {(['sum', 'max', 'avg'] as const).map((op) => {
                            const checked = (fieldDraft.agg_ops || []).includes(
                              op,
                            );
                            return (
                              <label
                                key={op}
                                className={`schemas-check-chip ${checked ? 'checked' : ''}`}
                              >
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  onChange={() => toggleNumberAggOp(op)}
                                />
                                {AGG_LABELS[op]}
                              </label>
                            );
                          })}
                        </div>
                      </div>
                    )}
                    {fieldDraft.scope_type === 'product' && (
                      <div className="schemas-form-field full">
                        <span className="schemas-field-title">
                          适用事件（留空表示全部）
                        </span>
                        <div className="schemas-check-chips">
                          {EVENT_TYPE_SCOPES.map((etype) => {
                            const checked =
                              fieldDraft.applicable_types.includes(etype);
                            return (
                              <label
                                key={etype}
                                className={`schemas-check-chip ${checked ? 'checked' : ''}`}
                              >
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  onChange={() => toggleApplicableType(etype)}
                                />
                                {etype}
                              </label>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                </details>
              )}

              <div className="dialog-actions">
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={closeDialog}
                >
                  取消
                </Button>
                <Button type="submit" variant="primary" disabled={busy}>
                  {busy ? '保存中…' : '保存字段'}
                </Button>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>

      {/* 新增 / 编辑产品目录弹窗 */}
      <Dialog open={Boolean(productDraft)} onOpenChange={onDialogOpenChange}>
        <DialogContent
          {...dialogFocus}
          className="dialog-centered schemas-dialog"
        >
          <DialogHeading
            title={productDraft?.isEdit ? '编辑产品' : '新增产品'}
            description="识别词用于把聊天中的事件归到这个产品。"
          />
          {error && productDraft && <ErrorNotice message={error} />}
          {productDraft && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void submitProduct();
              }}
            >
              <div className="schemas-form-grid">
                <div className="schemas-form-field">
                  <label htmlFor="prod-name">产品名称 *</label>
                  <Input
                    id="prod-name"
                    required
                    maxLength={64}
                    placeholder="例如：边缘函数服务"
                    value={productDraft.name}
                    onChange={(e) =>
                      setProductDraft({ ...productDraft, name: e.target.value })
                    }
                  />
                </div>
                <div className="schemas-form-field">
                  <label htmlFor="prod-key">产品编码 *</label>
                  <Input
                    id="prod-key"
                    required
                    minLength={2}
                    maxLength={32}
                    pattern="[a-z][a-z0-9_]{1,31}"
                    title="以小写字母开头，可用小写字母、数字和下划线，保存后固定"
                    disabled={productDraft.isEdit}
                    placeholder="小写字母与下划线，如 edge_fn"
                    value={productDraft.product_key}
                    onChange={(e) =>
                      setProductDraft({
                        ...productDraft,
                        product_key: e.target.value,
                      })
                    }
                  />
                </div>
                <div className="schemas-form-field full">
                  <label htmlFor="prod-desc">产品说明</label>
                  <Input
                    id="prod-desc"
                    maxLength={256}
                    placeholder="简要描述该产品涵盖的具体子服务"
                    value={productDraft.description}
                    onChange={(e) =>
                      setProductDraft({
                        ...productDraft,
                        description: e.target.value,
                      })
                    }
                  />
                </div>
                <div className="schemas-form-field full">
                  <label htmlFor="prod-kws">识别词（用逗号或换行分隔）</label>
                  <textarea
                    id="prod-kws"
                    className="schemas-textarea"
                    placeholder="例如：tpm, rpm, token, deepseek, 429, 限流"
                    value={productDraft.keywords_text}
                    onChange={(e) =>
                      setProductDraft({
                        ...productDraft,
                        keywords_text: e.target.value,
                      })
                    }
                  />
                </div>
                <div className="schemas-form-field">
                  <label htmlFor="prod-enabled">启用状态</label>
                  <select
                    id="prod-enabled"
                    className="schemas-select"
                    value={productDraft.enabled ? '1' : '0'}
                    onChange={(e) =>
                      setProductDraft({
                        ...productDraft,
                        enabled: e.target.value === '1',
                      })
                    }
                  >
                    <option value="1">已启用</option>
                    <option value="0">已停用</option>
                  </select>
                </div>
              </div>
              <div className="dialog-actions">
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={closeDialog}
                >
                  取消
                </Button>
                <Button type="submit" variant="primary" disabled={busy}>
                  {busy ? '保存中…' : '保存产品'}
                </Button>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>

      {/* 删除自定义字段或产品确认弹窗 */}
      <Dialog open={Boolean(deleteTarget)} onOpenChange={onDialogOpenChange}>
        <DialogContent
          {...dialogFocus}
          className="dialog-centered schemas-dialog-compact"
        >
          <DialogHeading
            title={deleteTarget?.kind === 'product' ? '删除产品' : '删除字段'}
            description={
              deleteTarget?.kind === 'product'
                ? `删除产品「${deleteTarget.label}」将同时移除其下属的自定义字段配置，确定继续吗？`
                : `确定删除自定义字段「${deleteTarget?.label}」吗？删除后新窗口不再提取该字段。`
            }
          />
          {error && deleteTarget && <ErrorNotice message={error} />}
          <div className="dialog-actions">
            <Button variant="secondary" disabled={busy} onClick={closeDialog}>
              取消
            </Button>
            <Button variant="primary" disabled={busy} onClick={confirmDelete}>
              {busy ? '删除中…' : '确认删除'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 补齐历史事件结构化字段弹窗 */}
      <Dialog open={backfillOpen} onOpenChange={onDialogOpenChange}>
        <DialogContent
          {...dialogFocus}
          className="dialog-centered schemas-dialog-compact"
        >
          <DialogHeading
            title="补齐历史字段"
            description="按已启用的字段重新检查历史事件，每次最多处理 150 条。"
          />
          {error && backfillOpen && <ErrorNotice message={error} />}
          <div className="flex flex-col gap-2.5 my-2">
            <span className="schemas-field-title">选择补齐范围</span>
            <label
              className={`schemas-radio-option ${backfillScope === 'all' ? 'selected' : ''}`}
            >
              <input
                type="radio"
                name="backfill-scope"
                checked={backfillScope === 'all'}
                onChange={() => setBackfillScope('all')}
              />
              <div>
                <div className="font-medium text-sm text-neutral-900">
                  全部历史事件
                </div>
              </div>
            </label>
            {backfillLabel && (
              <label
                className={`schemas-radio-option ${backfillScope === 'current' ? 'selected' : ''}`}
              >
                <input
                  type="radio"
                  name="backfill-scope"
                  checked={backfillScope === 'current'}
                  onChange={() => setBackfillScope('current')}
                />
                <div>
                  <div className="font-medium text-sm text-neutral-900">
                    当前范围 · {backfillLabel}
                  </div>
                  {backfillProduct && (
                    <small className="text-xs text-neutral-500">
                      也会检查尚未识别产品的事件。
                    </small>
                  )}
                </div>
              </label>
            )}

            <label className="schemas-check-chip mt-1">
              <input
                type="checkbox"
                checked={onlyMissing}
                onChange={(e) => setOnlyMissing(e.target.checked)}
              />
              <span>仅处理尚无字段值的事件</span>
            </label>
          </div>
          <div className="dialog-actions">
            <Button variant="secondary" disabled={busy} onClick={closeDialog}>
              取消
            </Button>
            <Button variant="primary" disabled={busy} onClick={runBackfill}>
              {busy ? (
                <RefreshCw size={15} className="spinning" />
              ) : (
                <History size={15} />
              )}
              {busy ? '正在补齐中…' : '开始补齐'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </main>
  );
}
