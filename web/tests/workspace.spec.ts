import { expect, type Page, test } from '@playwright/test';

const stamp = '2038-06-01T09:00:00+08:00';
/** 自造数据验证交互，写请求全部拦截。 */
async function fixture(page: Page, initiallyEmpty = false) {
  const writes: Record<string, unknown>[] = [];
  let fail = false;
  let hasEvents = !initiallyEmpty;
  const message = {
    source: 'synthetic',
    message_id: 'm1',
    digest: 'v1',
    send_time: stamp,
    sender_name: '测试客户',
    sender_role: 'customer',
    excerpt: '测试上传失败',
    content: '测试上传失败',
    conversation_id: 'g1',
  };
  function event(id: string, uid: string) {
    return {
      event_id: id,
      uid,
      stream: `stream-${uid}`,
      topic_key: `测试事项${id}`,
      type: '排障',
      summary: `测试摘要${id}`,
      start_time: stamp,
      end_time: stamp,
      status: '未知',
      severity: '未知',
      assignees: [],
      evidence: [message],
      conversations: [],
      platforms: [],
      updated_by: [
        {
          action: 'created',
          window_start: stamp,
          window_end: stamp,
          message_ids: ['m1'],
          snapshot: { evidence: [message] },
          change_summary: '测试首次反馈',
        },
      ],
    };
  }
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== 'GET') {
      writes.push(route.request().postDataJSON());
      if (fail)
        return route.fulfill({
          status: 409,
          json: { detail: '当前客户已有执行任务' },
        });
      const body = writes.at(-1);
      return route.fulfill({
        status: 202,
        json: {
          run_id: 'test-run',
          uid: body?.uid,
          request: { analysis_stream: `stream-${body?.uid}` },
        },
      });
    }
    if (url.pathname.endsWith('/config'))
      return route.fulfill({
        json: {
          uid: '1383603926',
          start: '2026-11-08',
          end: '2026-11-12',
          interval: '15m',
          provider: 'xhub',
          event_types: ['排障'],
          customers: [
            {
              uid: '1383603926',
              label: '测试客户1383603926',
              stream: 'stream-1383603926',
              message_count: 10,
              event_count: hasEvents ? 2 : 0,
              level: 'L9',
              level_state: 'fresh',
              segment: 'large',
              is_super_large: true,
              tam: '合成负责人A',
              group_count: 1,
              group_names: ['合成核心群A'],
            },
            {
              uid: '555002',
              label: '测试客户555002',
              stream: 'stream-555002',
              message_count: 5,
              event_count: hasEvents ? 1 : 0,
              level: 'L7',
              level_state: 'fresh',
              segment: 'large',
              is_super_large: false,
              tam: '合成负责人B',
              group_count: 1,
              group_names: ['合成协作群B'],
            },
          ],
        },
      });
    if (url.pathname.endsWith('/events')) {
      const uid = url.searchParams.get('uid') || '';
      const items = !hasEvents
        ? []
        : uid === '555002'
          ? [event('C', uid)]
          : [event('A', uid), event('B', uid)];
      return route.fulfill({ json: { items, total: items.length } });
    }
    if (url.pathname.endsWith('/runs'))
      return route.fulfill({ json: { items: [], total: 0 } });
    return route.fulfill({ status: 404, json: { detail: '测试记录不存在' } });
  });
  return {
    writes,
    publish: () => {
      hasEvents = true;
    },
    fail: () => {
      fail = true;
    },
  };
}
test('首次产出事件后，全部客户工作台在下次刷新中展示', async ({ page }) => {
  const state = await fixture(page, true);
  await page.goto('/events?uid=all');
  await expect(
    page.getByRole('heading', { name: '暂无事件', exact: true }),
  ).toBeVisible();
  state.publish();
  await expect(page.locator('.event-rows > li')).toHaveCount(3, {
    timeout: 8000,
  });
  expect(state.writes).toEqual([]);
});
test('切换、搜索、原文定位与客户隔离', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/events');
  await page.getByRole('button', { name: /测试事项B/ }).click();
  await expect(page.locator('.event-chat-title')).toHaveText('测试事项B');
  await expect(page.locator('.aside-summary')).toContainText('测试摘要B');
  await page.getByRole('button', { name: '定位原文', exact: true }).click();
  await expect(page.locator('.evidence-focused')).toContainText('测试上传失败');
  await page.getByLabel('搜索事件').fill('测试事项A');
  await expect(page.locator('.event-rows > li')).toHaveCount(1);
  await page.getByLabel('客户范围').selectOption('555002');
  await expect(page.locator('.event-chat-title')).toHaveText('测试事项C');
  await expect(page.getByLabel('搜索事件')).toHaveValue('');
  await page.reload();
  await expect(page.getByLabel('客户范围')).toHaveValue('555002');
  await page.getByRole('button', { name: '切换客户' }).click();
  const popover = page.getByRole('dialog', { name: '切换与检索大客户' });
  await expect(popover).toBeVisible();
  await popover.getByLabel('搜索客户').fill('合成负责人A');
  const customerOptions = popover
    .getByRole('group', { name: '可选客户列表' })
    .getByRole('button');
  await expect(customerOptions).toHaveCount(1);
  await expect(customerOptions.first()).toContainText('UID 1383603926');
  await popover.getByLabel('搜索客户').fill('');
  await popover.getByRole('button', { name: '全部客户', exact: true }).click();
  await expect(page.locator('.event-rows > li')).toHaveCount(3);
  await page.getByRole('button', { name: /测试事项A/ }).click();
  await page.getByRole('button', { name: '只看该客户' }).click();
  await expect(page.getByLabel('客户范围')).toHaveValue('1383603926');
  await expect(page.locator('.event-rows > li')).toHaveCount(2);
  expect(state.writes).toHaveLength(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
test('工作台只保留任务中心入口，旧链接跳转且不提交任务', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/events');
  await expect(page.getByRole('button', { name: '拉取并处理' })).toHaveCount(0);
  await expect(
    page.locator('.inbox-toolbar-actions a[href="/tasks"]'),
  ).toBeVisible();
  await page.goto('/runs?new=official');
  await expect(page).toHaveURL(/\/tasks$/);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(state.writes).toEqual([]);
});
test('真实后端只读验收', async ({ page }) => {
  test.skip(process.env.DAKE_LIVE_PANEL !== '1', '显式开启本地验收');
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.name));
  await page.route('**/api/**', (route) =>
    route.request().method() === 'GET' ? route.continue() : route.abort(),
  );
  await page.goto('/events');
  await expect(page.locator('.event-rows > li').first()).toBeVisible();
  await page.locator('.event-rows > li').nth(1).getByRole('button').click();
  await expect(page.locator('.inbox-chat')).toBeVisible();
  await expect(
    page.getByRole('complementary', { name: '事件详情' }),
  ).toBeVisible();
  await expect(
    page.locator('.inbox-toolbar-actions a[href="/tasks"]'),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
