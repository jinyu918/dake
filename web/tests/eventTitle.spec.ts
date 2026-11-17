import { expect, test } from '@playwright/test';
import {
  eventDisplayTitle,
  eventTitle,
  locatableMessageIds,
  revisionChangeText,
  revisionHeadline,
  sortRevisions,
} from '../src/pages/events/eventTitle';
import { revisionDefaults } from './fieldDefaults';

const legacyRevisionFields = {
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

test('同一处理窗口的多个修订按版本倒序展示', () => {
  const values = [2, 3].map((revision_no) => ({
    ...legacyRevisionFields,
    action: 'updated' as const,
    window_start: '2038-06-02T00:00:00+08:00',
    window_end: '2038-06-03T00:00:00+08:00',
    message_ids: ['synthetic-same-message'],
    snapshot: null,
    revision_no,
  }));
  expect(sortRevisions(values).map((item) => item.revision_no)).toEqual([3, 2]);
});

test('变化历史区分摘要更新、确认、纠正和证据补充', () => {
  const base = {
    action: 'updated' as const,
    message_ids: ['synthetic-message'],
  };
  expect(
    revisionHeadline({
      ...base,
      change_kind: 'progress',
      summary_changed: true,
    }),
  ).toBe('摘要更新');
  expect(
    revisionHeadline({
      ...base,
      change_kind: 'confirmation',
      summary_changed: false,
    }),
  ).toBe('进展确认');
  expect(
    revisionHeadline({
      ...base,
      change_kind: 'correction',
      summary_changed: true,
    }),
  ).toBe('结论更正');
  expect(
    revisionHeadline({
      ...base,
      change_kind: 'classification',
      summary_changed: false,
    }),
  ).toBe('分类依据更新');
  expect(
    revisionHeadline({
      ...base,
      change_kind: 'evidence_only',
      summary_changed: false,
    }),
  ).toBe('证据补充');
  expect(
    revisionHeadline({
      ...base,
      change_kind: 'progress',
      summary_changed: false,
    }),
  ).toBe('事件更新');
});

test('详情标题不使用主题路径，也不沿用列表截断串', () => {
  const event = {
    topic_key: 'anthropic/claude-opus-4-6',
    summary: '客户反馈模型调用超时需要继续观察处理方案中。后续还有补充说明。',
  };
  expect(eventDisplayTitle(event)).toBe(
    '客户反馈模型调用超时需要继续观察处理方案中',
  );
  expect(eventDisplayTitle(event)).not.toContain('anthropic/');
  expect(eventTitle(event)).toBe('客户反馈模型调用超时需要继续观察处理方案…');
  expect(eventDisplayTitle(event)).not.toBe(eventTitle(event));
});

test('已有 display_title 时优先使用，不改写 topic_key', () => {
  const event = {
    topic_key: 'anthropic/claude-opus-4-6',
    summary: '客户反馈模型调用超时。',
    extensions: { display_title: '调用超时跟进' },
  };
  expect(eventDisplayTitle(event)).toBe('调用超时跟进');
  expect(event.topic_key).toBe('anthropic/claude-opus-4-6');
});

test('英文标识主题回退到摘要首句；没有摘要时才完整保留主题原文', () => {
  expect(
    eventDisplayTitle({
      topic_key: 'storage-timeout',
      summary: '虚构：存储超时已有新的排查进展。后续更新。',
    }),
  ).toBe('虚构：存储超时已有新的排查进展');
  expect(
    eventDisplayTitle({
      topic_key: 'quota',
      summary: '虚构：继续跟进原配额申请。',
    }),
  ).toBe('虚构：继续跟进原配额申请');
  expect(eventDisplayTitle({ topic_key: 'storage-timeout', summary: '' })).toBe(
    'storage-timeout',
  );
});

test('更新记录在缺少 delta 时只用中性标题，且不把重复消息当成可定位目标', () => {
  expect(revisionHeadline({ action: 'created', message_ids: ['1'] })).toBe(
    '首次识别',
  );
  expect(revisionHeadline({ action: 'updated', message_ids: ['1'] })).toBe(
    '新增证据',
  );
  expect(revisionHeadline({ action: 'updated', message_ids: [] })).toBe(
    '事件更新',
  );
  expect(
    revisionChangeText({
      ...legacyRevisionFields,
      action: 'updated',
      window_start: '2026-11-10T00:00:00+08:00',
      window_end: '2026-11-11T00:00:00+08:00',
      message_ids: ['1'],
      snapshot: null,
    }),
  ).toBe('');
  const ordered = sortRevisions([
    {
      ...legacyRevisionFields,
      action: 'created',
      window_start: '2026-11-08T00:00:00+08:00',
      window_end: '2026-11-09T00:00:00+08:00',
      message_ids: ['1'],
      snapshot: null,
    },
    {
      ...legacyRevisionFields,
      action: 'updated',
      window_start: '2026-11-10T00:00:00+08:00',
      window_end: '2026-11-11T00:00:00+08:00',
      message_ids: ['2'],
      snapshot: null,
    },
  ]);
  expect(ordered.map((item) => item.action)).toEqual(['updated', 'created']);
  expect(
    locatableMessageIds({ message_ids: ['a', 'b'] }, [
      { message_id: 'a' },
      { message_id: 'b' },
    ]),
  ).toEqual(['a', 'b']);
  expect(
    locatableMessageIds({ message_ids: ['a'] }, [
      { message_id: 'a' },
      { message_id: 'a' },
    ]),
  ).toEqual([]);
});
