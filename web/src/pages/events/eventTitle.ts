/** 列表和详情标题共用同一来源：优先已有展示标题，否则只用可展示主题或摘要首句，不改写 topic_key。 */

const TITLE_LIMIT = 20;

type TitleSource = {
  topic_key?: string | null;
  summary: string;
  attributes?: Record<string, unknown>;
  extensions?: Record<string, unknown>;
  display_title?: unknown;
};

/** 详情标题：完整展示文本，不做列表式截断。 */
export function eventDisplayTitle(event: TitleSource) {
  const named = namedDisplayTitle(event);
  if (named) return named;
  const topic = event.topic_key?.trim() ?? '';
  if (isDisplayableTopic(topic)) return topic;
  const clause = firstClause(event.summary);
  if (clause) return clause;
  return topic;
}

/** 列表标题：对同一展示文本做简写，避免把主题路径截成标题。 */
export function eventTitle(event: TitleSource) {
  return clipText(eventDisplayTitle(event), TITLE_LIMIT);
}

function namedDisplayTitle(event: TitleSource) {
  const values = [
    event.display_title,
    event.extensions?.display_title,
    event.attributes?.display_title,
  ];
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function firstClause(summary: string) {
  const text = summary.trim();
  return text.split(/[。！？!?;；]/)[0]?.trim() || text;
}

function isDisplayableTopic(value: string) {
  if (!value) return false;
  if (
    /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(
      value,
    )
  ) {
    return false;
  }
  if (/^[0-9a-fA-F]{16,}$/.test(value)) return false;
  // 路径、命名空间是事件身份，不能直接当标题。
  if (/[\\/]/.test(value) || value.includes('::')) return false;
  // 英文标识或短横、下划线、点分键名只用于匹配。
  if (
    !/[\u4e00-\u9fff]/.test(value) &&
    /^[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*$/.test(value)
  ) {
    return false;
  }
  return true;
}

function clipText(value: string, max: number) {
  const text = value.trim();
  if (!text) return text;
  if (text.length <= max) return text;
  return `${text.slice(0, max).trimEnd()}…`;
}

export function formatDay(value: string) {
  return new Date(value).toLocaleDateString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: 'long',
    day: 'numeric',
  });
}

export function formatClock(value: string) {
  return new Date(value).toLocaleTimeString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

/** 左右按所属一方区分，客服与内部同侧，标签保留具体角色。 */
export function messageIdentity(
  role: API.EvidenceResponse['sender_role'],
  kind: API.EvidenceResponse['sender_kind'] = 'unknown',
) {
  if (kind === 'bot')
    return {
      side: role === 'internal' || role === 'support' ? 'internal' : 'unknown',
      label: '机器人',
      avatar: '机',
    };
  const identities = {
    internal: { side: 'internal', label: '内部', avatar: '内' },
    support: { side: 'internal', label: '客服', avatar: '服' },
    customer: { side: 'customer', label: '客户', avatar: '客' },
    external: { side: 'customer', label: '外部', avatar: '外' },
    unknown: { side: 'unknown', label: '', avatar: '' },
  };
  return identities[role] || identities.unknown;
}

export function shanghaiDay(value: string) {
  return new Date(value).toLocaleDateString('sv-SE', {
    timeZone: 'Asia/Shanghai',
  });
}

export function sortRevisions(revisions: API.EventRevision[]) {
  return [...revisions].sort((left, right) => {
    const end = Date.parse(right.window_end) - Date.parse(left.window_end);
    if (end) return end;
    const version = (right.revision_no ?? 0) - (left.revision_no ?? 0);
    if (version) return version;
    return Date.parse(right.window_start) - Date.parse(left.window_start);
  });
}

/** 接口没有 change_summary 或 delta 时不编造业务进展。 */
export function revisionChangeText(revision: API.EventRevision) {
  const record = revision as API.EventRevision & {
    change_summary?: unknown;
    delta?: unknown;
  };
  const values = [
    record.change_summary,
    record.delta,
    record.snapshot?.extensions?.change_summary,
    record.snapshot?.attributes?.change_summary,
  ];
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

export function revisionHeadline(revision: {
  action: API.EventRevision['action'];
  message_ids: string[];
  change_kind?: API.EventRevision['change_kind'];
  summary_changed?: boolean | null;
}) {
  if (revision.action === 'created') return '首次识别';
  if (revision.change_kind === 'manual') return '人工裁定';
  if (revision.change_kind === 'evidence_only') return '证据补充';
  if (revision.change_kind === 'confirmation') return '进展确认';
  if (revision.change_kind === 'correction') return '结论更正';
  if (revision.change_kind === 'classification') return '分类依据更新';
  if (revision.summary_changed) return '摘要更新';
  if (revision.change_kind === 'progress') return '事件更新';
  if (revision.message_ids.length) return '新增证据';
  return '事件更新';
}

/** 仅当该轮全部 message_id 都能在当前聊天里唯一对应时才可定位。 */
export function locatableMessageIds(
  revision: { message_ids: string[] },
  evidence: { message_id: string }[],
) {
  const counts = new Map<string, number>();
  for (const item of evidence) {
    counts.set(item.message_id, (counts.get(item.message_id) || 0) + 1);
  }
  const ids = revision.message_ids;
  if (!ids.length) return [];
  if (ids.every((id) => counts.get(id) === 1)) return [...ids];
  return [];
}

export function conversationLabel(name?: string | null) {
  const text = name?.trim() ?? '';
  return text || '群聊未标明';
}
