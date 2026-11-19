export type SelectedScope = {
  scopeType: API.AttributeFieldSchemaItem['scope_type'];
  scopeKey: string;
};

export const EVENT_TYPE_SCOPES = [
  '商机',
  '流失',
  '排障',
  '资源需求',
  '功能需求',
  '商务需求',
] as const;

export const DATA_TYPE_LABELS: Record<
  API.AttributeFieldSchemaItem['data_type'],
  string
> = {
  number: '数值',
  enum: '单选',
  boolean: '是 / 否',
  string: '文本',
};

export const AGG_LABELS: Record<
  API.AttributeFieldSchemaItem['agg_ops'][number],
  string
> = {
  sum: '总和',
  avg: '平均值',
  max: '最大值',
  terms: '按值计数',
};
