import { ChevronRight } from 'lucide-react';
import { useId, useState } from 'react';
import { Button } from '@/components/ui/button';
import { AGG_LABELS, DATA_TYPE_LABELS } from './schemaLabels';

type FieldActions = {
  saving: boolean;
  onEdit: (field: API.AttributeFieldSchemaItem) => void;
  onToggle: (field: API.AttributeFieldSchemaItem) => void;
  onDelete: (field: API.AttributeFieldSchemaItem) => void;
};

function FieldRow({
  field,
  saving,
  onEdit,
  onToggle,
  onDelete,
}: FieldActions & { field: API.AttributeFieldSchemaItem }) {
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  const typeLabel = [DATA_TYPE_LABELS[field.data_type], field.unit]
    .filter(Boolean)
    .join(' · ');
  return (
    <>
      <tr className="field-row">
        <th scope="row">
          <button
            className="field-disclosure"
            type="button"
            aria-expanded={expanded}
            aria-controls={detailId}
            onClick={() => setExpanded(!expanded)}
          >
            <ChevronRight
              size={16}
              className="field-chevron"
              aria-hidden="true"
            />
            <span>
              <strong>{field.label}</strong>
              <small className="field-mobile-type">{typeLabel}</small>
            </span>
          </button>
        </th>
        <td className="field-type">{typeLabel}</td>
        <td>
          <button
            type="button"
            role="switch"
            aria-label={`${field.label}启用状态`}
            aria-checked={field.enabled}
            className="field-status"
            disabled={saving}
            onClick={() => onToggle(field)}
          >
            <span className="field-switch-track" aria-hidden="true" />
            <span>{field.enabled ? '已启用' : '已停用'}</span>
          </button>
        </td>
        <td className="field-action">
          <Button
            size="small"
            variant="ghost"
            aria-label={`编辑${field.label}`}
            onClick={() => onEdit(field)}
          >
            编辑
          </Button>
        </td>
      </tr>
      <tr id={detailId} hidden={!expanded} className="field-details">
        <td colSpan={4}>
          <dl>
            <dt>提取条件</dt>
            <dd>{field.extraction_hint}</dd>
            {field.enum_options.length > 0 && (
              <>
                <dt>可选值</dt>
                <dd>{field.enum_options.join('、')}</dd>
              </>
            )}
            {field.scope_type === 'product' && (
              <>
                <dt>适用事件</dt>
                <dd>
                  {field.applicable_types.length
                    ? field.applicable_types.join('、')
                    : '全部事件类型'}
                </dd>
              </>
            )}
            {field.agg_ops.length > 0 && (
              <>
                <dt>统计方式</dt>
                <dd>{field.agg_ops.map((op) => AGG_LABELS[op]).join('、')}</dd>
              </>
            )}
            <dt>字段编码</dt>
            <dd>
              <code>{field.field_key}</code>
            </dd>
          </dl>
          {!field.builtin && (
            <Button
              size="small"
              variant="ghost"
              className="schemas-danger-btn"
              onClick={() => onDelete(field)}
            >
              删除字段
            </Button>
          )}
        </td>
      </tr>
    </>
  );
}

/** 列表优先展示可管理的字段，完整提取规则按行展开。 */
export function FieldList({
  fields,
  ...actions
}: FieldActions & {
  fields: API.AttributeFieldSchemaItem[];
}) {
  return (
    <div className="field-list">
      {fields.length ? (
        <table className="field-table" aria-label="字段列表">
          <thead>
            <tr>
              <th scope="col">字段名称</th>
              <th scope="col" className="field-type">
                类型 / 单位
              </th>
              <th scope="col" className="field-status-col">
                状态
              </th>
              <th scope="col" className="field-action">
                操作
              </th>
            </tr>
          </thead>
          <tbody>
            {fields.map((field) => (
              <FieldRow key={field.field_id} field={field} {...actions} />
            ))}
          </tbody>
        </table>
      ) : (
        <p className="field-empty">尚未配置字段。</p>
      )}
    </div>
  );
}
