import { expect, test } from '@playwright/test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { compareEventsByPriority } from '../src/components/EventBadges';
import { EventPane } from '../src/pages/events/EventPane';
import { SavedChanges } from '../src/pages/events/SavedChanges';

function makeEvent(
  overrides: Partial<API.EventResponse> = {},
): API.EventResponse {
  return {
    event_id: 'synthetic-csp-event',
    uid: '1383603926',
    stream: 'stub-v1',
    type: '商机',
    types: ['商机', '商务需求'],
    type_evidence: {
      商机: [
        { quote: '新增 50Gbps 带宽采购', evidence_ids: ['official_chat:m1'] },
      ],
      商务需求: [
        { quote: '签署年度框架协议', evidence_ids: ['official_chat:m1'] },
      ],
    },
    removed_types: {
      排障: [
        { quote: '确认为询价而非故障', evidence_ids: ['official_chat:m2'] },
      ],
    },
    classification_evidence_status: '已记录',
    classification_status: '已分类',
    other_reason: null,
    typed_attributes: {
      商机: {
        计划规模: {
          value: '50Gbps',
          quote: '新增 50Gbps 带宽采购',
          evidence_ids: ['official_chat:m1'],
        },
      },
    },
    topic_key: 'cdn:deal',
    start_time: '2026-11-10T09:00:00+08:00',
    end_time: '2026-11-10T09:30:00+08:00',
    created_on: '2026-11-10T09:00:00+08:00',
    status: '等待中',
    lifecycle_status: '等待中',
    waiting_reason: '等待内部支撑',
    resolution: null,
    status_detail: { reason: '等待商务核算阶梯单价' },
    severity: 'P1',
    priority: 'P1',
    base_priority: 'P2',
    priority_detail: {
      vip_boosted: true,
      level_note: '因 L10 超级大客由基础 P2 提升一档至 P1',
    },
    summary: '合成双标签商机与合同办理事项',
    assignees: ['duty-alice'],
    evidence: [],
    attributes: { 计划规模: '50Gbps' },
    extensions: {},
    review_flag: '锁定状态（等待中）收到新状态证据（跟进中），待核对',
    review_items: [],
    version: 3,
    manual_controls: {
      status: {
        field: 'status',
        locked: true,
        operator: 'duty-alice',
        reason: '人工确认仍在等报价',
        decided_at: '2026-11-10T09:35:00+08:00',
        adopted_value: { status: '等待中', waiting_reason: '等待内部支撑' },
        pending_conflict: {
          summary: '锁定状态（等待中）收到新状态证据（跟进中），待核对',
        },
      },
    },
    manual_operations: [],
    closed_at: null,
    recovery: null,
    recurrence_of: null,
    schema_version: 1,
    updated_by: [],
    conversations: [],
    platforms: [],
    ...overrides,
  };
}

test('详情面板完整展示多标签、专属属性、结构化状态、提档优先级与锁定冲突提示', () => {
  const event = makeEvent();
  const markup = renderToStaticMarkup(
    createElement(EventPane, {
      event,
      onEventUpdated: () => {},
    }),
  );
  expect(markup).toContain('商机');
  expect(markup).toContain('商务需求');
  expect(markup).toContain('50Gbps');
  expect(markup).toContain('已更正移除的标签（排障）');
  expect(markup).toContain('等待中');
  expect(markup).toContain('等待内部支撑');
  expect(markup).toContain('P1');
  expect(markup).toContain('基 P2');
  expect(markup).toContain('因 L10 超级大客由基础 P2 提升一档至 P1');
  expect(markup).toContain('处理锁定冲突');
});

test('前端跨客户列表排序遵循未完结优先、P0 高于超大客普通咨询、同档超大客优先', () => {
  const customerMap = new Map<string, API.CustomerResponse>([
    [
      'uid-l6',
      {
        uid: 'uid-l6',
        label: '客户 L6',
        stream: 'stub-v1',
        message_count: 1,
        event_count: 1,
        level: 'L6',
        level_state: 'fresh',
        segment: 'large',
        is_super_large: false,
      },
    ],
    [
      'uid-l10',
      {
        uid: 'uid-l10',
        label: '客户 L10',
        stream: 'stub-v1',
        message_count: 1,
        event_count: 1,
        level: 'L10',
        level_state: 'fresh',
        segment: 'large',
        is_super_large: true,
      },
    ],
  ]);

  const resolvedP0 = makeEvent({
    event_id: 'resolved-p0',
    uid: 'uid-l10',
    lifecycle_status: '已解决',
    priority: 'P0',
  });
  const l10Minor = makeEvent({
    event_id: 'l10-p2',
    uid: 'uid-l10',
    lifecycle_status: '跟进中',
    priority: 'P2',
  });
  const l6P1 = makeEvent({
    event_id: 'l6-p1',
    uid: 'uid-l6',
    lifecycle_status: '跟进中',
    priority: 'P1',
  });
  const l10P1 = makeEvent({
    event_id: 'l10-p1',
    uid: 'uid-l10',
    lifecycle_status: '跟进中',
    priority: 'P1',
  });
  const l6Outage = makeEvent({
    event_id: 'l6-p0',
    uid: 'uid-l6',
    lifecycle_status: '跟进中',
    priority: 'P0',
  });

  const sorted = [resolvedP0, l10Minor, l6P1, l10P1, l6Outage].sort((a, b) =>
    compareEventsByPriority(a, b, customerMap),
  );
  expect(sorted.map((item) => item.event_id)).toEqual([
    'l6-p0',
    'l10-p1',
    'l6-p1',
    'l10-p2',
    'resolved-p0',
  ]);
});

test('变化面板正确识别并展示人工裁定修订', () => {
  const before = makeEvent({ priority: 'P2', lifecycle_status: '跟进中' });
  const after = makeEvent({ priority: 'P1', lifecycle_status: '等待中' });
  const revisions: API.EventRevision[] = [
    {
      action: 'created',
      source: 'automatic',
      window_start: '2026-11-10T09:00:00+08:00',
      window_end: '2026-11-10T09:15:00+08:00',
      message_ids: ['m1'],
      snapshot: before,
      revision_no: 1,
      recorded_at: '2026-11-10T09:15:01+08:00',
      change_kind: 'created',
      change_summary: '创建事项',
      summary_changed: true,
      changed_fields: ['summary'],
      changes: [],
      manual_operation: null,
      policy_version: null,
    },
    {
      action: 'updated',
      source: 'manual',
      window_start: '2026-11-10T09:00:00+08:00',
      window_end: '2026-11-10T09:15:00+08:00',
      message_ids: [],
      snapshot: after,
      revision_no: 2,
      recorded_at: '2026-11-10T09:20:00+08:00',
      change_kind: 'manual',
      change_summary: '人工裁定（duty-alice）：调整为等待内部支撑并升为 P1',
      summary_changed: false,
      changed_fields: ['lifecycle_status', 'priority'],
      changes: [],
      manual_operation: null,
      policy_version: null,
    },
  ];
  const markup = renderToStaticMarkup(
    createElement(SavedChanges, {
      data: {
        details_included: true,
        uid: before.uid,
        stream: before.stream,
        event_id: before.event_id,
        batches: [],
        tasks: [],
        revisions,
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
  expect(markup).toContain('人工裁定');
  expect(markup).toContain('调整为等待内部支撑并升为 P1');
});

function workbenchItem(
  id: string,
  level = 'L6',
  options: {
    uid?: string;
    priority?: API.EventResponse['priority'];
    due?: string;
    overdue?: boolean;
    signals?: Record<string, boolean>;
  } = {},
) {
  return makeEvent({
    event_id: id,
    uid: options.uid ?? '100000001',
    lifecycle_status: '跟进中',
    priority: options.priority ?? 'P1',
    summary: id,
    priority_detail: {
      rule_version: 'item-priority-v1',
      customer_level: level,
      level_state: 'fresh',
      clock: {
        deadline: options.due ?? '2038-06-01T10:00:00+08:00',
        phase: 'handoff',
        overdue: options.overdue ?? false,
      },
      ...options.signals,
    },
  });
}

test('同档优先按超时、保存等级、响应信号、绝对截止时间及事件 ID 排序', () => {
  const fixtures = [
    workbenchItem('late-due', 'L6', { due: '2038-06-01T03:00:00Z' }),
    workbenchItem('early-due'),
    workbenchItem('b-tie'),
    workbenchItem('a-tie'),
    workbenchItem('followup', 'L6', { signals: { strong_followup: true } }),
    workbenchItem('emotion', 'L6', { signals: { escalated_emotion: true } }),
    workbenchItem('higher-level', 'L10'),
    workbenchItem('overdue', 'L1', { overdue: true }),
    workbenchItem('review-first', 'L6', {
      priority: '待评估',
      signals: { review_required: true },
    }),
    workbenchItem('review-later', 'L6', {
      priority: '待评估',
      due: '2038-06-01T01:00:00Z',
    }),
  ];
  expect(
    fixtures.sort(compareEventsByPriority).map((item) => item.event_id),
  ).toEqual([
    'overdue',
    'higher-level',
    'emotion',
    'followup',
    'a-tie',
    'b-tie',
    'early-due',
    'late-due',
    'review-first',
    'review-later',
  ]);
});

test('工作台合并客户事项并按客户范围读取告警预览', async ({ page }) => {
  const firstUid = '100000001';
  const secondUid = '100000002';
  const first = workbenchItem('跨客户早到期', 'L6');
  const second = workbenchItem('跨客户晚到期', 'L6', {
    uid: secondUid,
    due: '2038-06-01T03:00:00Z',
  });
  const alertScopes: Array<string | null> = [];
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const uid = url.searchParams.get('uid');
    if (url.pathname.endsWith('/config')) {
      await route.fulfill({
        json: {
          customers: [firstUid, secondUid].map((id) => ({
            uid: id,
            label: `合成客户 ${id}`,
            stream: 'stub-v1',
            event_count: 1,
          })),
        },
      });
    } else if (url.pathname.endsWith('/events')) {
      await route.fulfill({
        json: { items: uid === firstUid ? [first] : [second], total: 1 },
      });
    } else if (url.pathname.endsWith('/alert-previews')) {
      alertScopes.push(uid);
      await route.fulfill({
        json: {
          items: [
            {
              source: 'local-fixture',
              alert_id: 'fixture-alert',
              round_id: 'fixture-round',
              priority: 'P1',
              state: '已恢复',
              queue: '值班或 TAM',
              pending: false,
              reminders: [
                {
                  message_id: 'fixture-trigger',
                  at: '2038-06-01T01:00:00Z',
                  priority: 'P1',
                  route: '值班或 TAM',
                  kind: '历史首次触发',
                },
              ],
            },
          ],
        },
      });
    } else {
      await route.fulfill({ json: {} });
    }
  });
  await page.goto('/events');
  await expect(page.locator('.event-row-preview')).toHaveText([
    '跨客户早到期',
    '跨客户晚到期',
  ]);
  await page.getByText('机器人告警预览', { exact: true }).click();
  await expect(page.getByText(/历史首次触发/)).toBeVisible();
  await page.goto(`/events?uid=${firstUid}`);
  await expect(page.locator('.event-row-preview')).toHaveText(['跨客户早到期']);
  await page.getByText('机器人告警预览', { exact: true }).click();
  await expect(page.getByText(/历史首次触发/)).toBeVisible();
  expect(alertScopes).toEqual([null, firstUid]);
});
