/** 七牛预置产品编码的默认中文展示名映射与数值易读格式化工具。 */

export const DEFAULT_PRODUCT_LABELS: Record<string, string> = {
  ai_maas: 'AI 大模型与智能体',
  kodo: '对象存储 Kodo',
  cdn_dcdn: 'CDN 与边缘加速',
  rtc_media: '音视频与多媒体',
  compute_gpu: 'GPU 算力与云主机',
  cert_sms_ops: '证书域名与云通信',
  general_biz: '通用商务与账户',
  unclassified: '待识别产品',
};

export function productDisplayName(
  productKey: string | null | undefined,
  customMap?: Map<string, string>,
): string {
  if (!productKey) return '未标记产品';
  return (
    customMap?.get(productKey) ||
    DEFAULT_PRODUCT_LABELS[productKey] ||
    productKey
  );
}

/** 格式化指标数值（支持大整数自动展示万/亿辅助可读文本，保留精确千分位）。 */
export function formatMetricNumber(value: unknown, unit = ''): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    if (typeof value === 'boolean') return value ? '是' : '否';
    return String(value ?? '—');
  }
  const suffix = unit ? ` ${unit}` : '';
  const abs = Math.abs(value);
  if (abs >= 100_000_000) {
    return `${(value / 100_000_000).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 亿${suffix}`;
  }
  if (abs >= 10_000) {
    return `${(value / 10_000).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 万${suffix}`;
  }
  return `${value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })}${suffix}`;
}
