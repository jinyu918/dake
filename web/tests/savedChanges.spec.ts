import { expect, test } from '@playwright/test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  executionIssue,
  minuteTime,
  savedChanges,
  submittedRetry,
} from '../src/pages/events/changeTimeline';
import { ChangesContent } from '../src/pages/events/EventChangesDrawer';
import { SavedChanges } from '../src/pages/events/SavedChanges';
import { fieldDefaults, revisionDefaults } from './fieldDefaults';

/** 全部消息、摘要及身份均为虚构，不读取任何聊天夹具或服务。 */
function proof(id: string, time: string, text: string): API.ChangeEvidence {
  return {
    source: 'synthetic',
    message_id: id,
    digest: `synthetic-${id}`,
    conversation_id: 'synthetic-room',
    send_time: time,
    quote: text,
    evidence_status: null,
  };
}

function evidence(ref: API.ChangeEvidence): API.EvidenceResponse {
  return {
    ...ref,
    excerpt: ref.quote,
    content: null,
    platform: '',
    platform_label: '',
    conversation_name: '',
    sender_name: '',
    internal_hint: null,
    sender_role: 'unknown',
    sender_kind: 'unknown',
    origin_event_id: null,
  };
}

function fixture(): API.EventChangesResponse {
  const first = proof(
    'first',
    '2038-06-01T09:03:24+08:00',
    '虚构报告上传失败。',
  );
  const follow = proof(
    'follow',
    '2038-06-10T11:06:45+08:00',
    '虚构排查确认参数错误。',
  );
  const supplement = proof(
    'late',
    '2038-06-10T11:09:12+08:00',
    '虚构补充原截图证据。',
  );
  const one: API.EventSnapshot = {
    ...fieldDefaults,
    event_id: 'synthetic-event',
    uid: 'synthetic-customer',
    stream: 'synthetic-stream',
    type: '排障',
    types: ['排障'],
    type_evidence: {},
    removed_types: {},
    classification_evidence_status: '未记录',
    classification_status: '已分类',
    other_reason: null,
    topic_key: '虚构报告上传',
    start_time: first.send_time,
    end_time: first.send_time,
    created_on: first.send_time,
    status: '未知',
    severity: '未知',
    summary: first.quote,
    evidence: [evidence(first)],
    assignees: [],
    attributes: {},
    typed_attributes: {},
    extensions: {},
    review_flag: null,
    closed_at: null,
    recovery: null,
    recurrence_of: null,
    schema_version: 1,
  };
  const two = {
    ...one,
    summary: `${first.quote}\n${follow.quote}`,
    end_time: follow.send_time,
    evidence: [...one.evidence, evidence(follow)],
  };
  const three = { ...two, evidence: [...two.evidence, evidence(supplement)] };
  function revision(
    index: number,
    snapshot: API.EventSnapshot,
    ref: API.ChangeEvidence,
  ): API.EventRevision {
    return {
      action: index === 0 ? 'created' : 'updated',
      window_start: `${ref.send_time.slice(0, 10)}T00:00:00+08:00`,
      window_end: `${ref.send_time.slice(0, 10)}T23:59:59+08:00`,
      message_ids: [ref.message_id],
      snapshot,
      revision_no: index + 1,
      recorded_at: '2038-07-01T12:00:00+08:00',
      change_kind:
        index === 0 ? 'created' : index === 2 ? 'evidence_only' : 'progress',
      change_summary: null,
      summary_changed: index !== 2,
      changed_fields: index === 2 ? ['evidence'] : ['summary', 'evidence'],
      changes: [
        {
          kind: 'progress',
          effect: index === 2 ? 'evidence_only' : 'progress',
          text: ref.quote,
          replaces: null,
          evidence: [ref],
        },
      ],
      ...revisionDefaults,
      policy_version: 'synthetic-policy',
    };
  }
  return {
    details_included: true,
    uid: one.uid,
    stream: one.stream,
    event_id: one.event_id,
    revisions: [
      revision(0, one, first),
      revision(1, two, follow),
      revision(2, three, supplement),
    ],
    batches: [],
    tasks: [],
    total_runs: 0,
    offset: 0,
    next_offset: null,
    executions: [],
    execution_records_available: true,
    total_executions: 0,
    execution_offset: 0,
    next_execution_offset: null,
  };
}

function execution(
  changes: Partial<API.EventExecution> = {},
): API.EventExecution {
  return {
    attempt_id: 'synthetic-attempt',
    run_id: null,
    window_index: null,
    window: {
      start: '2038-06-10T11:05:00+08:00',
      end: '2038-06-10T11:10:00+08:00',
    },
    outcome: 'committed',
    created_at: null,
    finished_at: null,
    retry_of: null,
    retry_link_unavailable: false,
    related_by: ['candidate'],
    error_type: null,
    stages: [],
    inputs: [],
    candidates: [],
    contexts: [],
    context_reasons: [],
    context_omitted: null,
    suggestions: [],
    decisions: [],
    commit_status: 'committed',
    receipt_kind: 'analysis',
    receipt_prepared_at: null,
    processed: 1,
    created: false,
    updated: false,
    revision_indexes: [],
    revision_link_unavailable: false,
    delivery: [],
    ...changes,
  };
}

test('跨天主列表按触发消息到分钟，直接给引文、事实和摘要前后', () => {
  const data = fixture();
  const rows = savedChanges(data);
  expect(rows.map((row) => row.day)).toEqual([
    '2038-06-10',
    '2038-06-10',
    '2038-06-01',
  ]);
  expect(rows.map((row) => row.time && minuteTime(row.time))).toEqual([
    '11:09',
    '11:06',
    '09:03',
  ]);
  expect(rows[1].descriptions).toEqual(['虚构排查确认参数错误。']);
  expect(rows[1].previous?.summary).toBe('虚构报告上传失败。');
  const markup = renderToStaticMarkup(createElement(SavedChanges, { data }));
  expect(markup).toContain('变化前摘要');
  expect(markup).toContain('变化后摘要');
  expect(markup).toContain('虚构排查确认参数错误。');
  expect(markup).toContain('触发消息');
  expect(rows.some((row) => row.day === '2038-07-01')).toBe(false);
});

test('增量面板保留人工操作和字段采用记录，不把操作显示为聊天触发', () => {
  const data = fixture();
  const revision = data.revisions[1];
  if (!revision.snapshot) throw new Error('合成快照缺失');
  revision.source = 'manual';
  revision.recorded_at = '2038-07-02T14:05:00+08:00';
  revision.changes = [];
  revision.changed_fields = ['field_states'];
  revision.manual_operation = {
    operator: '虚构复核员',
    reason: '虚构字段采用理由',
    decided_at: revision.recorded_at,
  };
  revision.snapshot.field_states = {
    'common::status': {
      target: { kind: 'common', name: 'status' },
      facts: [],
      adopted_ids: [],
      locked_ids: [],
      locked: true,
      archived: false,
      decision: revision.manual_operation,
    },
  };
  data.revisions = [revision];
  const markup = renderToStaticMarkup(createElement(SavedChanges, { data }));
  expect(markup).toContain('class="change-delta"');
  expect(markup).toContain('完整摘要对比');
  expect(markup).toContain('2038-07-02');
  expect(markup).toContain('人工操作');
  expect(markup).toContain('虚构复核员');
  expect(markup).toContain('虚构字段采用理由');
  expect(markup).toContain('字段事实与人工裁定');
  expect(markup).toContain('跟进状态 · 已锁定');
  expect(markup).not.toContain('<summary>触发消息 ·');
});

test('仅补证据明确标注，进展确认即使摘要不变也不误标为仅补证据', () => {
  const data = fixture();
  expect(savedChanges(data)[0].evidenceOnly).toBe(true);
  data.revisions[2].change_kind = 'confirmation';
  data.revisions[2].changes[0].kind = 'confirmation';
  data.revisions[2].changes[0].effect = 'confirmation';
  const row = savedChanges(data)[0];
  expect(row.title).toBe('进展确认');
  expect(row.evidenceOnly).toBe(false);
  expect(row.previous?.summary).toBe(row.revision.snapshot?.summary);
});

test('仅候选、无变化、仅恢复重试、程序拟变化均不产生主列表条目', () => {
  const data = fixture();
  const before = savedChanges(data);
  data.executions = [
    execution(),
    execution({
      attempt_id: 'synthetic-retry',
      receipt_kind: 'delivery_recovery',
      retry_of: 'synthetic-prior',
    }),
    execution({
      attempt_id: 'synthetic-failed',
      outcome: 'not_committed',
      commit_status: 'not_committed',
      decisions: [
        {
          proposal_id: 'p',
          action: 'update',
          event_id: data.event_id,
          possible_event_ids: [],
          reason: '虚构拟变化',
          contributed_to_revision: true,
          change_scope: null,
          before: null,
          after: {
            event_id: data.event_id,
            summary: '虚构尚未保存的摘要',
            status: null,
          },
          added_evidence: [],
        },
      ],
    }),
  ];
  expect(savedChanges(data)).toEqual(before);
  expect(
    renderToStaticMarkup(createElement(SavedChanges, { data })),
  ).not.toContain('虚构尚未保存的摘要');
  data.revisions.push({ ...data.revisions[2], revision_no: 4, changes: [] });
  expect(savedChanges(data)).toHaveLength(3);
});

test('仅分类依据更新有独立标题且不会因摘要和事项证据未变而消失', () => {
  const data = fixture();
  const current = data.revisions[2];
  const previous = data.revisions[1].snapshot;
  if (!previous) throw new Error('合成前版缺失');
  current.change_kind = 'classification';
  current.changes = [];
  current.message_ids = [];
  current.changed_fields = ['type_evidence'];
  current.snapshot = {
    ...previous,
    type_evidence: {
      排障: [{ evidence_ids: ['synthetic:follow'], quote: '虚构排查依据' }],
    },
  };
  const row = savedChanges(data).find(
    (item) => item.revision.change_kind === 'classification',
  );
  expect(row?.title).toBe('分类依据更新');
  expect(row?.descriptions).toEqual(['分类依据已更新。']);
  expect(row?.evidenceOnly).toBe(false);
});

test('缺失原快照与触发正文显示未记录，不借当前摘要或模型建议补造', () => {
  const data = fixture();
  data.revisions = [
    {
      ...data.revisions[1],
      snapshot: null,
      change_kind: null,
      changes: [],
      message_ids: ['missing'],
    },
  ];
  const row = savedChanges(data)[0];
  expect(row.title).toBe('已保存修订');
  expect(row.time).toBeNull();
  expect(row.previous).toBeNull();
  expect(row.triggers).toEqual([
    {
      message_id: 'missing',
      quote: null,
      source: null,
      digest: null,
      conversation_id: null,
      send_time: null,
      role: 'new',
    },
  ]);
  const markup = renderToStaticMarkup(createElement(SavedChanges, { data }));
  expect(markup).toContain('触发消息正文未记录');
  expect(markup).toContain('变化前摘要</dt><dd>未记录');
  expect(markup).toContain('变化后摘要</dt><dd>未记录');
});

test('同 ID 编辑只引用此次保存的版本，不复用旧正文', () => {
  const data = fixture();
  const original = data.revisions[0].snapshot?.evidence[0];
  if (!original) throw new Error('虚构夹具无原证据');
  const changed = {
    ...original,
    digest: 'synthetic-edited',
    excerpt: '虚构编辑后的证据',
  };
  data.revisions = [
    data.revisions[0],
    {
      ...data.revisions[1],
      message_ids: [original.message_id],
      changes: [],
      snapshot: {
        ...(data.revisions[0].snapshot as API.EventSnapshot),
        summary: '虚构更正摘要',
        evidence: [original, changed],
      },
    },
  ];
  const row = savedChanges(data)[0];
  expect(row.triggers).toHaveLength(1);
  expect(row.triggers[0].digest).toBe('synthetic-edited');
  expect(row.triggers[0].quote).toBe('虚构编辑后的证据');
});

test('相同分钟的两个真实修订分别保留，不按时间或重试次数去重', () => {
  const data = fixture();
  data.revisions[2].changes[0].evidence[0].send_time =
    data.revisions[1].changes[0].evidence[0].send_time;
  expect(
    savedChanges(data)
      .slice(0, 2)
      .map((row) => row.index),
  ).toEqual([2, 1]);
});

test('迟到证据按原消息日期展示，摘要前后仍对应保存顺序，不倒退新结论', () => {
  const data = fixture();
  data.revisions[2].changes[0].evidence[0].send_time = '2038-06-02T01:05:00Z';
  const rows = savedChanges(data);
  expect(rows.map((row) => row.day)).toEqual([
    '2038-06-10',
    '2038-06-02',
    '2038-06-01',
  ]);
  const late = rows[1];
  expect(late.time && minuteTime(late.time)).toBe('09:05');
  expect(late.evidenceOnly).toBe(true);
  expect(late.previous?.summary).toBe(data.revisions[1].snapshot?.summary);
  expect(late.revision.snapshot?.summary).toBe(late.previous?.summary);
});

test('摘要快照不得跨客户或范围使用', () => {
  const data = fixture();
  const snapshot = data.revisions[0].snapshot;
  if (!snapshot) throw new Error('虚构夹具无快照');
  data.revisions[0].snapshot = {
    ...snapshot,
    uid: 'synthetic-other',
    summary: '禁止跨客户摘要',
  };
  const row = savedChanges(data).find((item) => item.index === 1);
  expect(row?.previous).toBeNull();
  expect(
    renderToStaticMarkup(createElement(SavedChanges, { data })),
  ).not.toContain('禁止跨客户摘要');
});

test('失败与待核实单独提示，技术过程默认折叠，已保存版本仍保留', () => {
  const data = fixture();
  data.executions = [
    execution({
      outcome: 'commit_unknown',
      updated: true,
      revision_indexes: [1],
    }),
  ];
  expect(executionIssue(data.executions[0])).toBe('提交待核实');
  expect(savedChanges(data)).toHaveLength(3);
  const markup = renderToStaticMarkup(createElement(ChangesContent, { data }));
  expect(markup).toContain('aria-label="失败与待核实"');
  expect(markup).toContain('不表示整批执行成功');
  expect(markup).toContain('<details class="change-section change-technical">');
  expect(markup).not.toContain(
    '<details class="change-section change-technical" open',
  );
  expect(executionIssue(execution())).toBeNull();
  expect(
    executionIssue(
      execution({
        delivery: [
          { destination: 'run_progress', status: 'failed', observed_at: null },
        ],
      }),
    ),
  ).toBe('业务已提交，展示保存失败或待核实');
});

test('明确重试提交后用历史提示替代旧失败告警，候选关联不冒充事件更新', () => {
  const data = fixture();
  const failed = execution({
    outcome: 'not_committed',
    commit_status: 'not_committed',
    receipt_kind: null,
  });
  const retry = execution({
    attempt_id: 'synthetic-retry',
    retry_of: failed.attempt_id,
  });
  data.executions = [retry, failed];
  const before = savedChanges(data);
  expect(submittedRetry(failed, data.executions)).toBe(retry);
  const markup = renderToStaticMarkup(createElement(ChangesContent, { data }));
  expect(markup).toContain('历史失败，后续重试已提交');
  expect(markup).not.toContain('aria-label="失败与待核实"');
  expect(markup).toContain('原失败记录保留在处理详情');
  expect(markup).toContain('以上方已保存版本为准');
  expect(savedChanges(data)).toEqual(before);
  expect(data.executions[1].outcome).toBe('not_committed');
});

test('尚未结束的执行单独显示中性状态，不冒充失败、成功或事务已核实', () => {
  const data = fixture();
  const pending = execution({
    outcome: 'running',
    commit_status: 'unconfirmed',
    receipt_kind: null,
  });
  data.executions = [pending];
  expect(executionIssue(pending)).toBeNull();
  const markup = renderToStaticMarkup(createElement(ChangesContent, { data }));
  expect(markup).toContain('aria-label="尚未记录处理结果"');
  expect(markup).toContain('不能认定为成功或失败');
  expect(markup).not.toContain('aria-label="失败与待核实"');
  pending.outcome = 'commit_unknown';
  const unknown = renderToStaticMarkup(createElement(ChangesContent, { data }));
  expect(unknown).toContain('aria-label="失败与待核实"');
  expect(unknown).toContain('提交待核实');
  pending.outcome = 'not_committed';
  pending.commit_status = 'not_committed';
  expect(
    renderToStaticMarkup(createElement(ChangesContent, { data })),
  ).toContain('该次执行未提交');
});

test('同时间无重试关联、缺收据或关联、提交不明及不同窗口均不能解除失败提示', () => {
  const failed = execution({
    outcome: 'not_committed',
    commit_status: 'not_committed',
  });
  const retry = execution({
    attempt_id: 'synthetic-retry',
    retry_of: failed.attempt_id,
  });
  for (const change of [
    { retry_of: null },
    { retry_link_unavailable: true },
    { commit_status: 'missing' },
    { outcome: 'commit_unknown' },
    { outcome: 'running' },
    { receipt_kind: 'delivery_recovery' },
    { window: { ...retry.window, end: '2038-06-10T11:15:00+08:00' } },
  ]) {
    expect(
      submittedRetry(failed, [failed, { ...retry, ...change }]),
    ).toBeNull();
  }
  expect(submittedRetry(failed, [failed])).toBeNull();
  expect(
    submittedRetry({ ...failed, outcome: 'commit_unknown' }, [retry]),
  ).toBeNull();
});

test('连续失败沿明确重试链核对，提交后的展示失败仍单独告警', () => {
  const data = fixture();
  const failed = execution({
    outcome: 'not_committed',
    commit_status: 'not_committed',
  });
  const again = {
    ...failed,
    attempt_id: 'synthetic-again',
    retry_of: failed.attempt_id,
  };
  const retry = execution({
    attempt_id: 'synthetic-retry',
    retry_of: again.attempt_id,
    delivery: [
      { destination: 'run_progress', status: 'failed', observed_at: null },
    ],
  });
  data.executions = [retry, again, failed];
  expect(submittedRetry(failed, data.executions)).toBe(retry);
  const markup = renderToStaticMarkup(createElement(ChangesContent, { data }));
  expect(markup.split('历史失败，后续重试已提交')).toHaveLength(2);
  expect(markup).toContain('业务已提交，展示保存失败或待核实');
  expect(markup).toContain('aria-label="失败与待核实"');
  expect(
    submittedRetry(failed, [{ ...failed, retry_of: again.attempt_id }, again]),
  ).toBeNull();
});
