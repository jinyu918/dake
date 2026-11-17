import { shanghaiDay } from './eventTitle';
import { evidenceKey, revisionEvidence } from './revisionEvidence';

const fields = {
  summary: '摘要',
  status: '状态',
  severity: '等级',
  assignees: '跟进人',
  attributes: '事件属性',
  types: '类型标签',
  type_evidence: '分类依据',
  removed_types: '标签撤销记录',
  typed_attributes: '类型专属属性',
  classification_status: '分类状态',
  classification_evidence_status: '分类依据状态',
  other_reason: '其他业务理由',
  recovery: '恢复确认',
  recurrence_of: '复发关联',
  review_flag: '待确认标记',
  closed_at: '关闭状态',
  type: '事件类型',
  topic_key: '事件主题',
  field_states: '字段采用与锁定',
  field_issues: '字段待复核问题',
  manual_operations: '人工裁定记录',
} as const;

function same(left: unknown, right: unknown) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function scopedSnapshot(
  snapshot: API.EventSnapshot | null | undefined,
  data: API.EventChangesResponse,
) {
  return snapshot?.uid === data.uid &&
    snapshot.stream === data.stream &&
    snapshot.event_id === data.event_id
    ? snapshot
    : null;
}

/** 触发引用只来自当前修订，绝不把整批输入、历史候选或当前聊天当作触发消息。 */
function triggerMessages(
  revision: API.EventRevision,
  previous: API.EventSnapshot | null,
) {
  const refs: API.RecordedMessageRef[] = revision.changes.flatMap((change) =>
    change.evidence.map((ref) => ({ ...ref, role: 'new' })),
  );
  const evidence = revisionEvidence(revision, previous, []).entries;
  for (const item of evidence) {
    // 缺前版时，同一 ID 有多个保存版本就不能断定哪一版触发此次变化。
    if (
      !previous &&
      revision.action !== 'created' &&
      evidence.filter(
        (ref) =>
          ref.source === item.source && ref.message_id === item.message_id,
      ).length > 1
    )
      continue;
    refs.push({
      source: item.source,
      message_id: item.message_id,
      digest: item.digest,
      conversation_id: item.conversation_id,
      send_time: item.send_time,
      quote: item.excerpt,
      role: 'new',
    });
  }
  const unique = new Map<string, API.RecordedMessageRef>();
  for (const ref of refs) {
    const key = JSON.stringify([ref.source, ref.message_id, ref.digest]);
    const old = unique.get(key);
    // 保留已保存的变化引文；不拼接成未曾记录的消息全文。
    if (!old || (!old.quote && ref.quote)) unique.set(key, ref);
  }
  for (const id of new Set(revision.message_ids)) {
    if (![...unique.values()].some((ref) => ref.message_id === id)) {
      unique.set(`missing:${id}`, {
        message_id: id,
        quote: null,
        source: null,
        digest: null,
        conversation_id: null,
        send_time: null,
        role: 'new',
      });
    }
  }
  return [...unique.values()];
}

/** 主列表以持久修订为唯一条目来源；执行次数、候选和重试均不产生条目。 */
export function savedChanges(data: API.EventChangesResponse) {
  return data.revisions
    .flatMap((raw, index) => {
      const previous = scopedSnapshot(
        data.revisions[index - 1]?.snapshot,
        data,
      );
      const revision = { ...raw, snapshot: scopedSnapshot(raw.snapshot, data) };
      const after = revision.snapshot;
      const changed =
        previous && after
          ? (Object.keys(fields) as (keyof typeof fields)[]).filter(
              (key) => !same(previous[key], after[key]),
            )
          : [];
      const evidenceChanged =
        previous && after
          ? !same(
              [...new Set(previous.evidence.map(evidenceKey))].sort(),
              [...new Set(after.evidence.map(evidenceKey))].sort(),
            )
          : null;
      if (
        revision.action !== 'created' &&
        previous &&
        after &&
        !changed.length &&
        !evidenceChanged
      )
        return [];
      const triggers = triggerMessages(revision, previous);
      const times = triggers
        .map((ref) => ref.send_time)
        .filter(
          (time): time is string =>
            Boolean(time) && Number.isFinite(Date.parse(time || '')),
        )
        .sort((left, right) => Date.parse(left) - Date.parse(right));
      const time =
        revision.source === 'manual'
          ? revision.recorded_at
          : (times.at(-1) ?? null);
      const evidenceOnly =
        revision.action !== 'created' &&
        !changed.length &&
        (revision.change_kind === 'evidence_only' ||
          (revision.changes.length > 0 &&
            revision.changes.every(
              (change) => change.effect === 'evidence_only',
            )) ||
          (!revision.change_kind &&
            !revision.changes.length &&
            Boolean(previous && after) &&
            evidenceChanged === true));
      const descriptions = [
        ...new Set(
          revision.changes
            .map((change) => change.text)
            .filter((text) => text.trim()),
        ),
      ];
      if (!descriptions.length && revision.change_summary?.trim())
        descriptions.push(revision.change_summary);
      if (!descriptions.length && changed.length)
        descriptions.push(
          `${changed.map((key) => fields[key]).join('、')}已更新。`,
        );
      return [
        {
          index,
          revision,
          previous,
          triggers,
          time,
          day: time ? shanghaiDay(time) : '触发时间未记录',
          evidenceOnly,
          descriptions,
          title:
            revision.action === 'created'
              ? '新建事件'
              : revision.source === 'manual'
                ? '人工裁定'
                : evidenceOnly
                  ? '仅补证据'
                  : revision.change_kind === 'confirmation'
                    ? '进展确认'
                    : revision.change_kind === 'correction'
                      ? '结论更正'
                      : revision.change_kind === 'progress'
                        ? '事件进展'
                        : revision.change_kind === 'classification'
                          ? '分类依据更新'
                          : '已保存修订',
        },
      ];
    })
    .sort(
      (left, right) =>
        (right.time ? Date.parse(right.time) : -Infinity) -
          (left.time ? Date.parse(left.time) : -Infinity) ||
        right.index - left.index,
    );
}

/** 只提示已记录的异常或未决状态，不从候选／程序拟变化推断提交。 */
export function executionIssue(item: API.EventExecution) {
  if (item.outcome === 'commit_unknown') return '提交待核实';
  if (item.outcome === 'running') return null;
  if (item.commit_status === 'not_committed') return '该次执行未提交';
  if (item.commit_status !== 'committed') return '提交记录缺失或待核实';
  if (
    item.delivery.some((value) =>
      ['failed', 'unconfirmed'].includes(value.status),
    )
  )
    return '业务已提交，展示保存失败或待核实';
  if (item.error_type) return '业务已提交，后续执行异常';
  if (item.revision_link_unavailable) return '已提交结果的版本关联未核对';
  return null;
}

/** 仅沿当前页明确的同窗口重试链核对收据；不把同时间、候选或待核实当成成功。 */
export function submittedRetry(
  failed: API.EventExecution,
  executions: API.EventExecution[],
) {
  if (
    failed.outcome !== 'not_committed' ||
    failed.commit_status !== 'not_committed'
  )
    return null;
  const parents = [failed.attempt_id];
  const seen = new Set(parents);
  for (const parent of parents) {
    for (const item of executions) {
      if (
        item.retry_of !== parent ||
        item.retry_link_unavailable ||
        seen.has(item.attempt_id) ||
        item.window.start !== failed.window.start ||
        item.window.end !== failed.window.end
      )
        continue;
      seen.add(item.attempt_id);
      if (
        item.outcome === 'committed' &&
        item.commit_status === 'committed' &&
        item.receipt_kind === 'analysis'
      )
        return item;
      if (
        item.outcome === 'not_committed' &&
        item.commit_status === 'not_committed'
      )
        parents.push(item.attempt_id);
    }
  }
  return null;
}

export function minuteTime(value: string) {
  return new Date(value).toLocaleTimeString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}
