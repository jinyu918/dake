import { expect, type Page, test } from '@playwright/test';

const uid = '555001';
const stream = 'synthetic-progress-a';
const moment = '2038-06-01T09:00:00+08:00';

/** 全部响应由虚构夹具提供；页面验收不会访问业务数据或提交任务。 */
function task(id: string, status: API.RunResponse['status']): API.RunResponse {
  return {
    run_id: id,
    uid,
    mode: 'official',
    status,
    request: {
      uid,
      mode: 'official',
      start: moment,
      end: '2038-06-02T09:00:00+08:00',
      interval: '5m',
      offline: true,
      context_lookback: '3d',
      match_window: '7d',
      closed_grace: '7d',
      ingest_lookback: '2d',
      default_status: '未知',
      default_severity: '未知',
      closed_statuses: [],
      provider: 'xhub',
      llm_model: null,
      llm_max_tokens: null,
      llm_timeout_seconds: null,
      analysis_stream: stream,
    },
    total_windows: 288,
    completed_windows: 12,
    current_window: {
      start: '2038-06-01T10:00:00+08:00',
      end: '2038-06-01T10:05:00+08:00',
    },
    created_at: moment,
    started_at: moment,
    finished_at: status === 'failed' ? '2038-06-03T10:05:00+08:00' : null,
    error: status === 'failed' ? '虚构校验失败：字段变化缺少依据' : null,
    cycles: [],
  };
}

function savedEvent(id: string): API.EventResponse {
  return {
    event_id: id,
    uid,
    stream,
    type: '排障',
    types: ['排障'],
    type_evidence: {},
    removed_types: {},
    classification_evidence_status: '未记录',
    classification_status: '已分类',
    other_reason: null,
    typed_attributes: {},
    topic_key: '虚构报告',
    start_time: moment,
    end_time: moment,
    created_on: moment,
    summary: `虚构报告 ${id} 的当前情况`,
    status: '未知',
    lifecycle_status: '状态未明',
    waiting_reason: null,
    resolution: null,
    status_detail: null,
    severity: '未知',
    priority: '待评估',
    base_priority: '待评估',
    priority_detail: null,
    evidence: [],
    assignees: [],
    attributes: {},
    extensions: {},
    review_flag: null,
    review_items: [],
    version: 1,
    manual_controls: {},
    manual_operations: [],
    closed_at: null,
    recovery: null,
    recurrence_of: null,
    schema_version: 1,
    updated_by: [],
    conversations: [],
    platforms: [],
  };
}

async function mockProgress(page: Page) {
  const state = {
    tasks: [
      task('synthetic-active', 'running'),
      task('synthetic-old', 'failed'),
    ],
    events: [savedEvent('synthetic-first')],
    failReads: false,
    writes: [] as string[],
    reads: [] as URL[],
    delayId: '',
    release: undefined as (() => void) | undefined,
  };
  await page.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET') {
      state.writes.push(request.method());
      return route.abort();
    }
    if (!['localhost', '127.0.0.1'].includes(url.hostname))
      return route.abort();
    if (!url.pathname.startsWith('/api/')) return route.continue();
    state.reads.push(url);
    if (url.pathname === '/api/v1/config') {
      const config: API.ConfigResponse = {
        uid,
        start: '2038-06-01',
        end: '2038-06-02',
        interval: '5m',
        context_lookback: '3d',
        match_window: '7d',
        ingest_lookback: '2d',
        closed_grace: '7d',
        event_types: ['排障'],
        filter_event_types: ['排障', '其他', '待分类'],
        lifecycle_statuses: [
          '跟进中',
          '等待中',
          '已解决',
          '已取消',
          '状态未明',
        ],
        waiting_reasons: ['等待客户', '等待内部支撑', '等待其他方'],
        resolutions: ['客户确认恢复', '支撑声明已修复', '已答复或已交付'],
        priorities: ['P0', 'P1', 'P2', 'P3', '待评估'],
        provider: 'xhub',
        customers: [
          {
            uid,
            label: '虚构验收客户',
            stream,
            message_count: 2,
            event_count: state.events.length,
          },
        ],
      };
      return route.fulfill({ json: config });
    }
    if (url.pathname.startsWith('/api/v1/runs')) {
      if (state.failReads)
        return route.fulfill({ status: 503, json: { detail: '虚构连接中断' } });
      const scoped = state.tasks.filter(
        (item) =>
          item.uid === url.searchParams.get('uid') &&
          item.request.analysis_stream === url.searchParams.get('stream'),
      );
      if (url.pathname === '/api/v1/runs')
        return route.fulfill({
          json: {
            items: scoped.map((item) => ({ ...item, cycles: [] })),
            total: scoped.length,
          },
        });
      const item = scoped.find((value) =>
        url.pathname.endsWith(`/${value.run_id}`),
      );
      if (!item)
        return route.fulfill({
          status: 404,
          json: { detail: '未找到处理任务' },
        });
      if (item.run_id === state.delayId)
        await new Promise<void>((resolve) => {
          state.release = resolve;
        });
      return route.fulfill({ json: item });
    }
    if (url.pathname === '/api/v1/events')
      return route.fulfill({
        json: { items: state.events, total: state.events.length },
      });
    return route.fulfill({
      status: 404,
      json: { detail: '虚构验收未配置此接口' },
    });
  });
  return state;
}

test('任务周期和事项弹窗显示已接手事项的等待时长', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2038-06-02T20:00:00+08:00'));
  const state = await mockProgress(page);
  const waiting = savedEvent('synthetic-waiting');
  const window = {
    start: '2038-06-02T11:55:00+08:00',
    end: '2038-06-02T12:00:00+08:00',
  };
  waiting.status = waiting.lifecycle_status = '等待中';
  waiting.priority_detail = {
    handoff_wait: { started_at: moment, accepted_at: moment },
  };
  waiting.updated_by = [
    {
      action: 'updated',
      source: 'automatic',
      window_start: window.start,
      window_end: window.end,
      message_ids: [],
      snapshot: null,
      revision_no: 2,
      recorded_at: null,
      change_kind: 'progress',
      change_summary: null,
      summary_changed: null,
      changed_fields: ['lifecycle_status'],
      changes: [],
      manual_operation: null,
      policy_version: null,
    },
  ];
  state.tasks[0].cycles = [
    {
      uid,
      stream,
      window,
      ingested: 1,
      processed: 1,
      created: [],
      updated: [waiting.event_id],
      review: [],
      events: [waiting],
      watermark: {
        next_start: null,
        last_send_time: null,
        last_message_id: null,
      },
      filter_audit: [],
      recovered_from_history: false,
      counts_complete: true,
      skipped_empty: false,
    },
  ];
  await page.goto('/tasks/run-detail');
  await page.getByText('展开已完成时段与事件变化（1 轮）').click();
  const row = page.getByRole('button', { name: /虚构报告 synthetic-waiting/ });
  await expect(row.locator('.handoff-age.yellow')).toHaveText(
    '超 6 小时未处理',
  );
  await row.click();
  await expect(
    page.getByRole('dialog').locator('.handoff-age.yellow'),
  ).toHaveText('超 6 小时未处理');
  expect(state.writes).toEqual([]);
});

test('默认选择进行中任务，自动显示停止原因并保留真实窗口进度', async ({
  page,
}) => {
  const state = await mockProgress(page);
  const current = state.tasks[0];
  current.cycles = [
    {
      uid,
      stream,
      window: { start: moment, end: '2038-06-01T09:05:00+08:00' },
      ingested: 1,
      processed: 1,
      created: [],
      updated: [],
      review: [],
      events: [],
      watermark: {
        next_start: null,
        last_send_time: null,
        last_message_id: null,
      },
      filter_audit: [],
      recovered_from_history: false,
      counts_complete: true,
      skipped_empty: false,
    },
  ];
  await page.goto('/tasks/run-detail');
  const detail = page.getByTestId('run-detail');
  await expect(detail.getByText('正在处理', { exact: true })).toBeVisible();
  await expect(detail.getByRole('progressbar')).toHaveAttribute('value', '12');
  await expect(
    detail.getByText('智能识别尚未启用', { exact: false }),
  ).toHaveCount(0);
  await expect(page.getByRole('button', { name: '开始处理' })).toHaveCount(0);
  await expect(detail.locator('.run-cycles')).not.toHaveAttribute('open');
  Object.assign(current, {
    status: 'failed',
    completed_windows: 13,
    error: '虚构校验失败：字段变化缺少依据',
    finished_at: moment,
  });
  await expect(detail.getByRole('alert')).toContainText(
    '虚构校验失败：字段变化缺少依据',
  );
  await expect(detail.locator('.run-current-window')).toContainText('停止时段');
  await expect(detail.getByRole('progressbar')).toHaveAttribute('value', '13');
  await expect(detail.getByText('任务已停止', { exact: true })).toBeVisible();
  if (process.env.DAKE_SCREENSHOTS) {
    await page.screenshot({
      path: `${process.env.DAKE_SCREENSHOTS}/run-progress-synthetic.png`,
      fullPage: true,
    });
  }
  expect(
    state.reads
      .filter((url) => url.pathname.startsWith('/api/v1/runs'))
      .every(
        (url) =>
          url.searchParams.get('uid') === uid &&
          url.searchParams.get('stream') === stream,
      ),
  ).toBeTruthy();
  expect(state.writes).toEqual([]);
});

test('历史失败与当前任务分别查看，任务结束后自动显示完成', async ({ page }) => {
  const state = await mockProgress(page);
  await page.goto('/tasks/run-detail?run=synthetic-old');
  const detail = page.getByTestId('run-detail');
  await expect(detail.getByText('任务已停止', { exact: true })).toBeVisible();
  await detail.getByRole('button', { name: '查看最新进度' }).click();
  await expect(detail.getByText('正在处理', { exact: true })).toBeVisible();
  Object.assign(state.tasks[0], {
    status: 'succeeded',
    completed_windows: 288,
    current_window: null,
    finished_at: moment,
  });
  await expect(detail.getByText('处理完成', { exact: true })).toBeVisible();
  await expect(detail.getByRole('progressbar')).toHaveAttribute('value', '288');
  await expect(detail.getByRole('alert')).toHaveCount(0);
  expect(state.writes).toEqual([]);
});

test('读取失败不能继续声称正在处理，也不伪造成任务失败', async ({ page }) => {
  const state = await mockProgress(page);
  await page.goto('/tasks/run-detail');
  const detail = page.getByTestId('run-detail');
  await expect(detail.getByText('正在处理', { exact: true })).toBeVisible();
  state.failReads = true;
  await expect(detail.getByRole('alert')).toContainText('进度读取失败');
  await expect(detail.getByText('任务已停止', { exact: true })).toHaveCount(0);
  await expect(detail.getByText('正在处理', { exact: true })).toHaveCount(0);
  state.failReads = false;
  state.delayId = 'synthetic-active';
  await expect.poll(() => Boolean(state.release)).toBeTruthy();
  await expect(detail.getByRole('alert')).toContainText('进度读取失败');
  await expect(detail.getByText('正在处理', { exact: true })).toHaveCount(0);
  state.delayId = '';
  state.release?.();
  await expect(detail.getByText('正在处理', { exact: true })).toBeVisible();
  expect(state.writes).toEqual([]);
});

test('事件页统一任务入口、自动更新事件数，并保留当前选择和三栏布局', async ({
  page,
}) => {
  const state = await mockProgress(page);
  await page.goto('/events');
  const link = page.locator('.inbox-toolbar-actions a[href="/tasks"]');
  await expect(link).toContainText('任务中心');
  const listHeading = page.getByRole('heading', { name: /^事件列表/ });
  await expect(listHeading).toHaveText(/事件列表\s*1$/);
  const selected = page.locator('.event-rows [aria-current=true]');
  await expect(selected).toContainText('synthetic-first');
  state.events.push(savedEvent('synthetic-second'));
  await expect(listHeading).toHaveText(/事件列表\s*2$/);
  await expect(selected).toContainText('synthetic-first');
  await expect(page.locator('.inbox-split')).toHaveCSS('display', 'grid');
  await page.setViewportSize({ width: 1024, height: 800 });
  await expect(link).toBeVisible();
  await page.getByRole('button', { name: '筛选', exact: true }).click();
  const filters = page.getByRole('dialog', { name: '筛选事件' });
  await expect(filters).toBeInViewport({ ratio: 1 });
  const fields = filters.getByRole('combobox');
  await expect(fields).toHaveCount(4);
  for (const field of await fields.all())
    await expect(field).toBeInViewport({ ratio: 1 });
  await page.keyboard.press('Escape');
  await link.click();
  await expect(page).toHaveURL(/\/tasks$/);
  expect(state.writes).toEqual([]);
});

test('切换客户和范围后取消旧详情，晚返回的结果不串入新页面', async ({
  page,
}) => {
  const state = await mockProgress(page);
  const other = task('synthetic-other', 'queued');
  other.uid = other.request.uid = '555002';
  other.request.analysis_stream = 'synthetic-progress-b';
  state.tasks.push(other);
  state.delayId = 'synthetic-active';
  await page.goto('/tasks/run-detail');
  await expect.poll(() => Boolean(state.release)).toBeTruthy();
  await page.evaluate(() => {
    window.history.pushState(
      null,
      '',
      '/tasks/run-detail?uid=555002&stream=synthetic-progress-b',
    );
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
  const detail = page.getByTestId('run-detail');
  await expect(detail.getByText('等待处理', { exact: true })).toBeVisible();
  state.delayId = '';
  state.release?.();
  await expect(detail.getByText('正在处理', { exact: true })).toHaveCount(0);
  await expect(page.locator('.run-list .run-item')).toHaveCount(1);
  expect(state.writes).toEqual([]);
});

test('没有任务与失败原因缺失均如实提示', async ({ page }) => {
  const state = await mockProgress(page);
  state.tasks = [];
  await page.goto('/tasks/run-detail');
  await expect(page.getByText('还没有处理记录', { exact: true })).toBeVisible();
  state.tasks = [task('synthetic-missing-reason', 'failed')];
  state.tasks[0].error = null;
  await expect(page.getByTestId('run-detail').getByRole('alert')).toContainText(
    '停止原因未记录',
  );
  expect(state.writes).toEqual([]);
});
