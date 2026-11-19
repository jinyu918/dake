import { expect, type Page, test } from '@playwright/test';

const stamp = '2038-06-01T09:00:00+08:00';
/** 自造数据验证交互，写请求全部拦截。 */
async function fixture(page: Page, initiallyEmpty = false, historical = false) {
  const writes: Record<string, unknown>[] = [];
  let fail = false;
  let hasEvents = !initiallyEmpty;
  const archive = 'xhub-v1-aaaaaaaaaaaaaaaaaaaa';
  const savedStream = (uid: string) =>
    historical && uid === '1383603926' ? archive : `stream-${uid}`;
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
      stream: savedStream(uid),
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
              event_count: hasEvents && !historical ? 2 : 0,
              archived_event_counts:
                hasEvents && historical ? { [archive]: 2 } : {},
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
              level: 'L10',
              level_state: 'fresh',
              segment: 'large',
              is_super_large: true,
              tam: '合成负责人B',
              group_count: 1,
              group_names: ['合成协作群B'],
            },
          ],
        },
      });
    if (url.pathname.endsWith('/events')) {
      const uid = url.searchParams.get('uid') || '';
      const items =
        !hasEvents ||
        (historical && url.searchParams.get('stream') !== savedStream(uid))
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

test('当前与历史事件都可查看，旧版本仅供核对', async ({ page }) => {
  const state = await fixture(page, false, true);
  await page.goto('/events');
  await expect(page.locator('.event-rows > li')).toHaveCount(3);
  await page.getByRole('button', { name: /测试事项A/ }).click();
  await expect(
    page.getByText('历史分析 · 只读', { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '人工纠偏' })).toHaveCount(0);
  await page.getByRole('button', { name: '只看该客户' }).click();
  await expect(page.locator('.event-rows > li')).toHaveCount(2);
  await page.getByLabel('客户范围').selectOption('555002');
  await expect(page.locator('.event-chat-title')).toHaveText('测试事项C');
  await expect(page.getByRole('button', { name: '人工纠偏' })).toBeVisible();
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
test('Esc 先关闭浮层，保留选中的事件并将焦点还给入口', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/events?event=B');
  const title = page.locator('.event-chat-title');
  await expect(title).toHaveText('测试事项B');
  for (const trigger of [
    page.getByRole('button', { name: /^时间范围：/ }),
    page.getByRole('button', { name: '筛选', exact: true }),
    page.getByRole('button', { name: '切换客户' }),
    page.getByRole('button', { name: '变化面板' }),
  ]) {
    await trigger.click();
    await expect(page.getByRole('dialog')).toBeInViewport();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await expect(title).toHaveText('测试事项B');
    expect(new URL(page.url()).searchParams.get('event')).toBe('B');
  }
  await page.keyboard.press('Escape');
  await expect(title).toHaveCount(0);
  expect(state.writes).toEqual([]);
});

test('事件筛选保留在链接中；清除四项筛选时保留客户搜索', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/events?uid=all');
  await expect(page.locator('.event-rows > li')).toHaveCount(3);
  await page.getByLabel('搜索事件').fill('测试客户555002');
  await expect(page.locator('.event-rows > li')).toHaveCount(1);
  await page.getByRole('button', { name: '筛选', exact: true }).click();
  const panel = page.getByRole('dialog', { name: '筛选事件' });
  await panel.getByRole('combobox', { name: '优先级' }).selectOption('P1');
  await panel.getByRole('combobox', { name: '事件类型' }).selectOption('排障');
  await expect(page).toHaveURL(/priority=P1/);
  await page.reload();
  await page.getByRole('button', { name: /筛选.*2/ }).click();
  await expect(panel.getByLabel('优先级')).toHaveValue('P1');
  await expect(panel.getByLabel('事件类型')).toHaveValue('排障');
  await panel.getByRole('button', { name: '清除条件' }).click();
  await expect(panel.getByLabel('优先级')).toHaveValue('');
  await expect(panel.getByLabel('事件类型')).toHaveValue('');
  await page.keyboard.press('Escape');
  await expect(page.getByLabel('搜索事件')).toHaveValue('测试客户555002');
  await expect(page.locator('.event-chat-title')).toHaveText('测试事项C');
  expect(state.writes).toHaveLength(0);
});

test('L10 战略级客户标识贯穿客户筛选、列表及事项切换', async ({ page }) => {
  const state = await fixture(page);
  let segment: string | null = null;
  await page.route('**/api/v1/customers**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/customers')) {
      segment = url.searchParams.get('segment');
      return route.fulfill({
        json: {
          items: [
            ['555002', 'L10'],
            ['1383603926', 'L9'],
          ].map(([uid, level]) => ({
            uid,
            level,
            level_state: 'fresh',
            tam_state: 'fresh',
            segment: 'large',
            is_super_large: true,
            group_count: 0,
          })),
          total: 2,
        },
      });
    }
    return route.fulfill({ json: { items: [], total: 0 } });
  });
  await page.goto('/customers');
  const scope = page.getByLabel('客户范围');
  await expect(scope.locator('option[value="super_large"]')).toHaveText(
    '超级大客户（L9）及战略级客户（L10）',
  );
  await scope.selectOption('super_large');
  const rows = page.getByRole('region', { name: '客户列表' }).getByRole('row');
  await expect(rows.filter({ hasText: '555002' })).toContainText('战略级客户');
  await expect(rows.filter({ hasText: '1383603926' })).toContainText(
    '超级大客',
  );
  await expect(
    page.getByRole('complementary', { name: '客户资料' }),
  ).toContainText('L10 · 战略级客户');
  expect(segment).toBe('super_large');

  await page.goto('/events?uid=all');
  await expect(page.locator('.event-rows')).toContainText('L10 战略级客户');
  const switcher = page.getByRole('button', { name: '切换客户' });
  await switcher.click();
  const choices = page.getByRole('group', { name: '可选客户列表' });
  await expect(
    choices.getByRole('button', { name: /1383603926/ }),
  ).toContainText('L9 超大客');
  await choices.getByRole('button', { name: /555002.*战略级客户/ }).click();
  await expect(switcher).toContainText('L10 战略级客户');
  await expect(page.getByLabel('客户范围')).toHaveValue('555002');
  expect(state.writes).toEqual([]);
});

test('客户同步起止时间和提交入口在视口内，取消后可继续浏览客户', async ({
  page,
}) => {
  const state = await fixture(page);
  await page.route('**/api/v1/customers**', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: { items: [], total: 0 } })
      : route.abort(),
  );
  await page.goto('/customers');
  const trigger = page.getByRole('button', { name: '同步客户', exact: true });
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: '同步客户' });
  await expect(dialog.getByLabel('开始时间')).toBeInViewport({ ratio: 1 });
  await expect(dialog.getByLabel('结束时间')).toBeInViewport({ ratio: 1 });
  await expect(dialog.getByRole('button', { name: '开始同步' })).toBeInViewport(
    { ratio: 1 },
  );
  await dialog.getByRole('button', { name: '关闭' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(state.writes).toEqual([]);
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
