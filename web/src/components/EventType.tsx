import {
  BriefcaseBusiness,
  CircleAlert,
  CircleHelp,
  CloudCog,
  Layers,
  Lightbulb,
  TrendingUp,
  Wrench,
} from 'lucide-react';

const types = {
  商机: { icon: TrendingUp, className: 'opportunity' },
  流失: { icon: CircleAlert, className: 'risk' },
  排障: { icon: Wrench, className: 'incident' },
  资源需求: { icon: CloudCog, className: 'resource' },
  功能需求: { icon: Lightbulb, className: 'feature' },
  商务需求: { icon: BriefcaseBusiness, className: 'business' },
  其他: { icon: Layers, className: 'other' },
  机器人告警: { icon: CircleAlert, className: 'incident' },
  待分类: { icon: CircleHelp, className: 'pending' },
};

export function eventTypeClass(type: string) {
  return types[type as keyof typeof types]?.className ?? 'pending';
}

/** 提取事件用于界面展示的完整分类标签列表；待分类事项展示单个「待分类」标签。 */
export function eventDisplayLabels(event: {
  type: string;
  types?: string[];
  classification_status?: string;
}): string[] {
  if (event.classification_status === '待分类' || event.type === '待分类') {
    return event.types?.length ? event.types : ['待分类'];
  }
  return event.types?.length ? event.types : [event.type || '待分类'];
}

/** 六类主事件及「其他 / 待分类」统一使用低饱和色和图标辅助快速识别。 */
export function EventType({
  type,
  compact = false,
}: {
  type: string;
  compact?: boolean;
}) {
  const display = types[type as keyof typeof types] || types.待分类;
  const Icon = display.icon;
  return (
    <span
      className={`event-type ${display.className} ${compact ? 'compact' : ''}`}
    >
      <span className="event-type-icon">
        <Icon size={14} strokeWidth={1.7} />
      </span>
      {!compact && type}
    </span>
  );
}
