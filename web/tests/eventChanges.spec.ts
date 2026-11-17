import { expect, test } from '@playwright/test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  ChangesContent,
  VersionDifference,
} from '../src/pages/events/EventChangesDrawer';
import { fieldDefaults, revisionDefaults } from './fieldDefaults';

function data(): API.EventChangesResponse {
  return {
    details_included: true,
    uid: 'synthetic-customer',
    stream: 'synthetic-stream',
    event_id: 'synthetic-event',
    batches: [
      {
        run_id: 'synthetic-task',
        cycle_index: 0,
        window: {
          start: '2038-06-10T00:00:00+08:00',
          end: '2038-06-11T00:00:00+08:00',
        },
        processed: 1,
        counts_complete: true,
        recovered_from_history: false,
        created: false,
        updated: false,
        revision_indexes: [],
        attributions: [
          {
            requested_action: 'new',
            action: 'review',
            event_id: null,
            possible_event_ids: ['synthetic-event'],
            reason: '虚构记录：依据不足',
            revision_no: null,
            summary_changed: null,
            change_kind: null,
            new_evidence: [],
            basis: [
              {
                source: 'synthetic',
                message_id: 'old-message',
                digest: 'synthetic-old-version',
                conversation_id: 'synthetic-room',
                send_time: '2038-06-01T09:00:00+08:00',
                quote: '虚构旧消息引文',
                role: 'history',
              },
            ],
          },
        ],
      },
    ],
    tasks: [
      {
        run_id: 'synthetic-task',
        status: 'failed',
        created_at: null,
        total_windows: 2,
        completed_windows: 1,
        current_window: null,
        current_scope_confirmed: true,
        error: '虚构任务进度保存失败',
        recovered_by: null,
        recovery_link_unavailable: false,
      },
    ],
    revisions: [],
    total_runs: 1,
    offset: 0,
    next_offset: null,
    executions: [],
    execution_records_available: false,
    total_executions: 0,
    execution_offset: 0,
    next_execution_offset: null,
  };
}

test('变化面板分开建议、决定和已保存结果，缺失阶段不补造', () => {
  const markup = renderToStaticMarkup(
    createElement(ChangesContent, { data: data() }),
  );
  expect(markup).toContain('模型建议：新建');
  expect(markup).toContain('程序决定：待确认');
  expect(markup).toContain('本事件未列入本批新建或更新结果');
  expect(markup).toContain('历史候选 · 未记录');
  expect(markup).toContain('虚构旧消息引文');
  expect(markup).toContain('历史背景引用');
  expect(markup).toContain('synthetic-old-version');
  expect(markup).toContain('不能据此认定事件未提交');
  expect(markup).not.toContain('已回滚');
});

test('重试恢复结果标明来源，不伪造本次写入或完整输入', () => {
  const value = data();
  value.batches[0].recovered_from_history = true;
  value.batches[0].counts_complete = false;
  const markup = renderToStaticMarkup(
    createElement(ChangesContent, { data: value }),
  );
  expect(markup).toContain('包含从历史恢复的结果');
  expect(markup).toContain('计数不完整');
  expect(markup).toContain('完整输入清单未记录');
  expect(markup).toContain('本批具体版本关联未记录');
});

test('缺失历史快照时不能显示成摘要未变或状态已恢复', () => {
  const revision: API.EventRevision = {
    action: 'updated',
    window_start: '2038-06-10T00:00:00+08:00',
    window_end: '2038-06-11T00:00:00+08:00',
    message_ids: ['missing-message'],
    snapshot: null,
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
  const markup = renderToStaticMarkup(
    createElement(VersionDifference, { revision }),
  );
  expect(markup).toContain('本版摘要</dt><dd>未记录');
  expect(markup).toContain('提交时间：未记录');
  expect(markup).toContain('证据差异记录不完整');
  expect(markup).not.toContain('客户已确认');
  expect(markup).not.toContain('摘要未变化');
});

test('同一消息编辑保留前后证据版本与摘要状态差异', () => {
  const old: API.EvidenceResponse = {
    source: 'synthetic',
    message_id: 'edited-message',
    digest: 'synthetic-before',
    send_time: '2038-06-01T09:00:00+08:00',
    excerpt: '虚构旧引文：尚未恢复',
    content: null,
    platform: '',
    platform_label: '',
    conversation_id: 'synthetic-room',
    conversation_name: '',
    sender_name: '',
    internal_hint: null,
    sender_role: 'unknown',
    sender_kind: 'unknown',
    origin_event_id: null,
  };
  const before: API.EventSnapshot = {
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
    topic_key: null,
    start_time: old.send_time,
    end_time: old.send_time,
    created_on: old.send_time,
    status: '未知',
    severity: '未知',
    summary: '虚构旧摘要',
    evidence: [old],
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
  const edited = {
    ...old,
    digest: 'synthetic-after',
    excerpt: '虚构新引文：已明确更正',
  };
  const revision: API.EventRevision = {
    action: 'updated',
    window_start: '2038-06-10T00:00:00+08:00',
    window_end: '2038-06-11T00:00:00+08:00',
    message_ids: [old.message_id],
    snapshot: {
      ...before,
      summary: '虚构更正后摘要',
      status: '虚构处理中',
      evidence: [old, edited],
    },
    revision_no: 2,
    recorded_at: null,
    change_kind: 'correction',
    change_summary: null,
    summary_changed: true,
    changed_fields: ['summary', 'status'],
    changes: [],
    ...revisionDefaults,
    policy_version: null,
  };
  const markup = renderToStaticMarkup(
    createElement(VersionDifference, { revision, previous: before }),
  );
  expect(markup).toContain('更新 1 条已有消息的证据版本');
  expect(markup).toContain('此前保存的证据版本');
  for (const value of [
    old.excerpt,
    edited.excerpt,
    old.digest,
    edited.digest,
    before.summary,
    '虚构更正后摘要',
    '虚构处理中',
  ]) {
    expect(markup).toContain(value);
  }
});

function execution(
  changes: Partial<API.EventExecution> = {},
): API.EventExecution {
  return {
    attempt_id: 'synthetic-attempt',
    run_id: 'synthetic-run',
    window_index: 0,
    window: {
      start: '2038-06-10T00:00:00+08:00',
      end: '2038-06-11T00:00:00+08:00',
    },
    outcome: 'committed',
    created_at: null,
    finished_at: null,
    retry_of: null,
    retry_link_unavailable: false,
    related_by: ['candidate'],
    error_type: null,
    stages: [
      {
        stage: 'history',
        status: 'completed',
        recorded_at: null,
        call_no: null,
        error_type: null,
      },
    ],
    inputs: [],
    candidates: [
      {
        event_id: 'synthetic-event',
        summary: '虚构首日上传失败',
        status: '未知',
        hit_reasons: ['keyword'],
        selected_for_request: true,
      },
    ],
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

function executionMarkup(value: API.EventExecution) {
  const panel = data();
  panel.batches = [];
  panel.tasks[0].run_id = 'unrelated-task-should-not-display';
  panel.executions = [value];
  panel.execution_records_available = true;
  panel.total_executions = 1;
  return renderToStaticMarkup(createElement(ChangesContent, { data: panel }));
}

test('已有事件归属未形成修订时，程序决定与本次实际写入分开展示', () => {
  const state = {
    event_id: 'synthetic-event',
    summary: '虚构首日上传失败',
    status: '未知',
  };
  const markup = executionMarkup(
    execution({
      decisions: [
        {
          proposal_id: 'synthetic-proposal',
          action: 'update',
          event_id: 'synthetic-event',
          possible_event_ids: [],
          reason: '虚构消息没有新事实',
          contributed_to_revision: false,
          change_scope: null,
          before: state,
          after: state,
          added_evidence: [],
        },
      ],
    }),
  );
  expect(markup).toContain('这条建议未贡献拟写入修订');
  expect(markup).toContain('收据没有记录本次写入当前事件');
  expect(markup).not.toContain('本次提交更新了当前事件');
});

test('正式执行候选命中不代表更新，只显示关联批次，不展示范围无关任务', () => {
  const markup = executionMarkup(execution());
  expect(markup).toContain('候选命中不代表更新当前事件');
  expect(markup).toContain('虚构首日上传失败');
  expect(markup).toContain('收据没有记录本次写入当前事件');
  expect(markup).not.toContain('unrelated-task-should-not-display');
  expect(markup).not.toContain('同客户及分析范围的任务');
  expect(markup).toContain('结构化模型建议未记录');
});

test('有收据但应答不明仍标待核实，展示保存失败不能冒充整批成功', () => {
  const markup = executionMarkup(
    execution({
      outcome: 'commit_unknown',
      updated: true,
      revision_link_unavailable: true,
      delivery: [
        {
          destination: 'run_progress',
          status: 'unconfirmed',
          observed_at: null,
        },
      ],
    }),
  );
  expect(markup).toContain('提交待核实');
  expect(markup).toContain('事务收据证明业务已提交');
  expect(markup).toContain('不能标为整批成功');
  expect(markup).toContain('任务进度：写入未确认');
  expect(markup).toContain('原版本缺失或核验不一致');
  expect(markup).not.toContain('任务完成');
});

test('回滚时保留模型建议与程序拟变化，不能展示成已提交差异', () => {
  const markup = executionMarkup(
    execution({
      outcome: 'not_committed',
      commit_status: 'not_committed',
      receipt_kind: null,
      suggestions: [
        {
          proposal_id: 'synthetic-proposal',
          call_no: 1,
          action: 'update',
          event_id: 'synthetic-event',
          possible_event_ids: [],
          summary: '虚构模型整段建议',
          status: null,
          reason: '虚构建议理由',
          facts: ['虚构新排查'],
          basis: [],
        },
      ],
      decisions: [
        {
          proposal_id: 'synthetic-proposal',
          action: 'update',
          event_id: 'synthetic-event',
          possible_event_ids: [],
          reason: '虚构程序核对记录',
          contributed_to_revision: true,
          change_scope: 'single_proposal',
          before: {
            event_id: 'synthetic-event',
            summary: '虚构旧摘要',
            status: '未知',
          },
          after: {
            event_id: 'synthetic-event',
            summary: '虚构拟写摘要',
            status: '未知',
          },
          added_evidence: [],
        },
      ],
    }),
  );
  expect(markup).toContain('模型建议：旧事件进展');
  expect(markup).toContain('程序决定：旧事件进展');
  expect(markup).toContain('程序拟变化 · 不代表已提交');
  expect(markup).toContain('虚构拟写摘要');
  expect(markup).toContain('该执行已确认未提交');
  expect(markup).not.toContain('事务收据证明业务已提交');
});

test('重试仅恢复原结果，不再次声称写入事件，缺失关系不按窗口猜测', () => {
  const markup = executionMarkup(
    execution({
      receipt_kind: 'delivery_recovery',
      processed: 0,
      retry_of: 'synthetic-prior-attempt',
      related_by: ['retry', 'recovery'],
    }),
  );
  expect(markup).toContain('明确接续执行 synthetic-prior-attempt');
  expect(markup).toContain('仅恢复原结果，没有再次写入当前事件');
  expect(markup).toContain('实际消费 0 条消息版本');
  const missing = executionMarkup(
    execution({
      retry_link_unavailable: true,
      commit_status: 'unconfirmed',
      outcome: 'running',
    }),
  );
  expect(missing).toContain('前次执行关联记录未取得');
  expect(missing).toContain('提交结果尚未确认');
});

test('必要旧聊天按版本与片段展示，附近聊天不自动归属，正文保持转义', () => {
  const markup = executionMarkup(
    execution({
      contexts: [
        {
          source: 'synthetic',
          message_id: 'old',
          digest: 'old-version',
          conversation_id: 'synthetic-room',
          send_time: '2038-06-01T09:00:00+08:00',
          quote: null,
          role: 'history',
          availability: 'excerpt_only',
          truncated: true,
          fragments: [
            { start: 0, end: 17, text: '<script>虚构旧片段</script>' },
          ],
          links: [{ event_id: 'synthetic-event', kind: 'neighbor' }],
          selected_for_request: true,
        },
      ],
      context_reasons: ['neighbor_version_unavailable'],
      context_omitted: 1,
    }),
  );
  expect(markup).toContain('同群附近聊天');
  expect(markup).toContain('历史背景引用');
  expect(markup).toContain('old-version');
  expect(markup).toContain('历史版本无法还原');
  expect(markup).toContain('已截断');
  expect(markup).toContain('&lt;script&gt;');
  expect(markup).not.toContain('<script>');
});

test.describe('独立合成服务交互', () => {
  test.skip(
    !process.env.DAKE_CHANGE_PANEL_TEST_URL,
    '需要显式启动独立合成后端，禁止访问默认真实服务',
  );
  test('隔离合成后端：抽屉关闭、切换事件、错误恢复和三栏滚动', async ({
    page,
  }) => {
    const origin = process.env.DAKE_CHANGE_PANEL_TEST_URL;
    test.skip(!origin, '需要显式启动独立合成后端，禁止访问默认真实服务');
    if (!origin || !/^http:\/\/127\.0\.0\.1:\d+$/.test(origin))
      throw new Error('只允许临时回环合成服务');
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => {
      if (
        new URL(route.request().url()).origin === origin &&
        route.request().method() === 'GET'
      )
        return route.continue();
      return route.abort();
    });
    await page.goto(`${origin}/__acceptance__`);
    const links = await page
      .locator('li a')
      .evaluateAll((nodes) =>
        nodes.map((node) => (node as HTMLAnchorElement).href),
      );
    expect(links.length).toBeGreaterThanOrEqual(2);
    const firstUrl = new URL(links[0]);
    const secondUrl = new URL(links[1]);
    expect(firstUrl.searchParams.get('uid')).toBe(
      secondUrl.searchParams.get('uid'),
    );
    const firstId = firstUrl.searchParams.get('event');
    const secondId = secondUrl.searchParams.get('event');
    expect(firstId).toBeTruthy();
    expect(secondId).toBeTruthy();
    expect(firstId).not.toBe(secondId);
    await page.goto(firstUrl.href);
    await expect(page.getByRole('button', { name: '变化面板' })).toBeEnabled();
    await expect(page.getByRole('combobox', { name: '选择客户' })).toHaveCount(
      0,
    );
    await expect(page.getByRole('button', { name: '刷新事件' })).toHaveCount(0);
    await expect(page.locator('.main-nav a[href="/runs"]')).toHaveCount(0);
    const scrolls = page.locator(
      '.inbox-list > ul, .inbox-chat .inbox-pane-scroll, .inbox-aside .inbox-pane-scroll',
    );
    await expect(scrolls).toHaveCount(3);
    const before = await scrolls.evaluateAll((nodes) =>
      nodes.map((node) => ({
        height: node.clientHeight,
        overflow: getComputedStyle(node).overflowY,
      })),
    );
    expect(
      before.every((item) => item.height > 0 && item.overflow === 'auto'),
    ).toBe(true);
    await page.getByRole('button', { name: '变化面板' }).click();
    const dialog = page.getByRole('dialog', { name: '变化面板' });
    await expect(dialog).toBeVisible();
    await expect(page.locator('.inbox-aside .aside-updates')).toHaveCount(0);
    const saved = dialog.getByRole('region', { name: '已保存的事件变化' });
    await expect(saved).toBeVisible();
    await saved.getByText('完整摘要对比', { exact: true }).first().click();
    await expect(saved.getByText('变化前摘要').first()).toBeVisible();
    await expect(saved.getByText('变化后摘要').first()).toBeVisible();
    await expect(dialog.locator('.change-technical')).not.toHaveAttribute(
      'open',
      '',
    );
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    expect(new URL(page.url()).searchParams.get('event')).toBe(firstId);
    await expect(page.getByRole('button', { name: '变化面板' })).toBeFocused();
    const after = await scrolls.evaluateAll((nodes) =>
      nodes.map((node) => ({
        height: node.clientHeight,
        overflow: getComputedStyle(node).overflowY,
      })),
    );
    expect(after).toEqual(before);
    await page.goto(secondUrl.href);
    await page.getByRole('button', { name: '变化面板' }).click();
    await expect(
      dialog.getByText(`当前事件 ${secondId}`, { exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByText(`当前事件 ${firstId}`, { exact: true }),
    ).toHaveCount(0);
    await expect(dialog.locator('.change-technical')).not.toHaveAttribute(
      'open',
      '',
    );
    await page.keyboard.press('Escape');
    await page.route(`**/events/${secondId}/changes?**`, (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: '{"detail":"虚构读取失败"}',
      }),
    );
    await page.getByRole('button', { name: '变化面板' }).click();
    await expect(dialog.getByText('虚构读取失败')).toBeVisible();
    await page.unroute(`**/events/${secondId}/changes?**`);
    await dialog.getByRole('button', { name: /重试/ }).click();
    await expect(dialog.getByText('虚构读取失败')).toHaveCount(0);
    await expect(
      dialog.getByText(`当前事件 ${secondId}`, { exact: true }),
    ).toBeVisible();
    expect(errors).toEqual([]);
  });
});
