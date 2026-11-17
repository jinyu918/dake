import { expect, test } from '@playwright/test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { EventPane } from '../src/pages/events/EventPane';
import { SavedChanges } from '../src/pages/events/SavedChanges';
import { fieldDefaults, revisionDefaults } from './fieldDefaults';

test('右侧只展示当前详情，历史变化集中在变化面板', () => {
  const snapshot: API.EventSnapshot = {
    ...fieldDefaults,
    event_id: 'synthetic-history-event',
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
    start_time: '2038-06-01T09:00:00+08:00',
    end_time: '2038-06-02T09:00:00+08:00',
    status: '未知',
    severity: '未知',
    summary: '虚构报告失败，待排查。',
    created_on: '2038-06-01T00:00:00+08:00',
    assignees: [],
    evidence: [],
    attributes: {},
    typed_attributes: {},
    extensions: {},
    review_flag: null,
    closed_at: null,
    recovery: null,
    recurrence_of: null,
    schema_version: 1,
  };
  const previous: API.EventRevision = {
    action: 'created',
    window_start: '2038-06-01T00:00:00+08:00',
    window_end: '2038-06-02T00:00:00+08:00',
    message_ids: [],
    snapshot,
    revision_no: null,
    recorded_at: null,
    change_kind: null,
    change_summary: null,
    summary_changed: null,
    changed_fields: [],
    changes: [],
    ...revisionDefaults,
    policy_version: null,
  };
  const current = { ...snapshot, summary: '虚构报告失败，已确认参数错误。' };
  const revision: API.EventRevision = {
    ...previous,
    action: 'updated',
    window_start: '2038-06-02T00:00:00+08:00',
    window_end: '2038-06-03T00:00:00+08:00',
    revision_no: 2,
    recorded_at: '2038-06-02T09:05:00+08:00',
    change_kind: 'progress',
    change_summary: '已确认参数错误。',
    summary_changed: true,
    changed_fields: ['summary'],
    snapshot: current,
    changes: [
      {
        kind: 'progress',
        effect: 'progress',
        text: '已确认参数错误。',
        replaces: '待排查',
        evidence: [
          {
            source: 'synthetic',
            message_id: 'synthetic-message',
            digest: 'synthetic-digest',
            conversation_id: 'synthetic-room',
            send_time: '2038-06-02T09:00:00+08:00',
            quote: '虚构测试结论：参数确实错误。',
            evidence_status: null,
          },
        ],
      },
    ],
  };
  const pane = renderToStaticMarkup(
    createElement(EventPane, {
      event: {
        ...current,
        updated_by: [previous, revision],
        conversations: [],
        platforms: [],
      },
    }),
  );
  expect(pane).toContain(current.summary);
  expect(pane).not.toContain('更新记录');
  expect(pane).not.toContain('版本 2');
  expect(pane).not.toContain('虚构测试结论：参数确实错误。');
  const markup = renderToStaticMarkup(
    createElement(SavedChanges, {
      data: {
        details_included: true,
        uid: current.uid,
        stream: current.stream,
        event_id: current.event_id,
        revisions: [previous, revision],
        batches: [],
        tasks: [],
        total_runs: 0,
        offset: 0,
        next_offset: null,
        executions: [],
        execution_records_available: false,
        total_executions: 0,
        execution_offset: 0,
        next_execution_offset: null,
      },
    }),
  );
  expect(markup).toContain('版本 2');
  expect(markup).toContain('版本 未记录');
  expect(markup).toContain('已确认参数错误。');
  expect(markup).toContain('变化前摘要');
  expect(markup).toContain(snapshot.summary);
  expect(markup).toContain(current.summary);
  expect(markup).toContain('记录时间：');
  expect(markup).toContain('虚构测试结论：参数确实错误。');
  expect(markup).toContain('synthetic-room');
});

test('复发详情区分前次恢复与本次依据，并保留同客户关联链接', () => {
  const proof: API.CustomerRecoveryEvidence = {
    source: 'synthetic',
    message_id: 'synthetic-confirmation',
    digest: 'synthetic-original-version',
    conversation_id: 'synthetic-prior-room',
    sender_id: 'synthetic-customer-account',
    sender_role: 'customer',
    send_time: '2038-06-02T09:00:00+08:00',
    quote: '虚构客户确认：我测试上传已经恢复。',
    evidence_status: null,
  };
  const recovery: API.RecoveryConfirmation = {
    confirmed_at: proof.send_time,
    evidence: [proof],
    identity_basis: 'confirmed_account',
    policy_version: 'synthetic-policy',
    review_status: 'confirmed',
    supporting_evidence: [],
    scope: null,
  };
  const event: API.EventResponse = {
    ...fieldDefaults,
    event_id: 'synthetic-recurrence',
    uid: 'synthetic-customer',
    stream: 'synthetic-stream',
    type: '排障',
    types: ['排障'],
    type_evidence: {},
    removed_types: {},
    classification_evidence_status: '未记录',
    classification_status: '已分类',
    other_reason: null,
    topic_key: '虚构上传',
    summary: '虚构上传再次失败。',
    start_time: '2038-06-10T09:00:00+08:00',
    end_time: '2038-06-10T09:00:00+08:00',
    created_on: '2038-06-10T00:00:00+08:00',
    status: '未知',
    severity: '未知',
    assignees: [],
    evidence: [],
    attributes: {},
    typed_attributes: {},
    extensions: {},
    updated_by: [],
    platforms: [],
    conversations: [],
    review_flag: null,
    closed_at: null,
    schema_version: 1,
    recovery: null,
    recurrence_of: {
      event_id: 'synthetic-prior',
      reason: '虚构客户明确报告恢复后再次发生。',
      recovery,
      evidence: [
        {
          ...proof,
          message_id: 'synthetic-again',
          quote: '虚构客户：今天又上传失败了。',
        },
      ],
      policy_version: 'synthetic-policy',
      review_status: 'confirmed',
      review_reason: null,
      retraction_evidence: [],
      retracted_by: null,
    },
  };
  const markup = renderToStaticMarkup(createElement(EventPane, { event }));
  expect(markup).toContain('本次为恢复后再次发生');
  expect(markup).toContain('关联时保存的前次恢复依据');
  expect(markup).toContain(proof.quote);
  expect(markup).toContain('虚构客户：今天又上传失败了。');
  expect(markup).toContain('uid=synthetic-customer');
  expect(markup).toContain('stream=synthetic-stream');
  expect(markup).toContain('event=synthetic-prior');
  expect(markup).not.toContain('客户已明确确认恢复');
  const parent = renderToStaticMarkup(
    createElement(EventPane, {
      event: { ...event, recovery, recurrence_of: null },
    }),
  );
  expect(parent).toContain('客户已明确确认恢复');
  expect(parent).not.toContain('本次为恢复后再次发生');
});
