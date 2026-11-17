import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from '@playwright/test';

test.skip(
  process.env.DAKE_FIELD_DECISIONS_LIVE !== '1',
  '仅显式连接第五阶段自造数据后端',
);

const uid = 'synthetic-classification-replay';
const stream = 'stub-v1';
const target = 'attribute:资源需求:目标总量与增减量';

async function current(
  request: APIRequestContext,
  eventId: string,
): Promise<API.EventResponse> {
  const response = await request.get(`/api/v1/events/${eventId}`, {
    params: { uid, stream },
  });
  expect(response.ok()).toBe(true);
  return response.json();
}

async function openEditor(page: Page) {
  await page
    .getByRole('button', { name: '人工确认与裁定', exact: true })
    .click();
  const dialog = page.getByRole('dialog', {
    name: '人工确认与裁定',
    exact: true,
  });
  await dialog
    .getByRole('combobox', { name: '裁定字段', exact: true })
    .selectOption(target);
  await dialog
    .getByRole('textbox', { name: '操作人（本地填写）', exact: true })
    .fill('合成操作人');
  await dialog
    .getByRole('textbox', { name: '裁定理由', exact: true })
    .fill('合成浏览器裁定');
  return dialog;
}

async function action(page: Page, name: API.FieldAction['action']) {
  const dialog = await openEditor(page);
  await dialog
    .getByRole('combobox', { name: '人工动作', exact: true })
    .selectOption(name);
  return dialog;
}

test.beforeEach(async ({ page, request }) => {
  const config = await request.get('/api/v1/config');
  expect(config.ok()).toBe(true);
  expect((await config.json()).uid).toBe(uid);
  await page.route('**/*', (route) =>
    new URL(route.request().url()).origin ===
    new URL(process.env.DAKE_WEB_URL || '').origin
      ? route.continue()
      : route.abort(),
  );
  const reset = await request.post('/synthetic/reset', { data: {} });
  expect(reset.ok()).toBe(true);
  const { event_id } = await reset.json();
  await page.goto(`/events?uid=${uid}&stream=${stream}&event=${event_id}`);
  await expect(
    page.getByText('合成验收：全部为自造数据', { exact: false }),
  ).toBeVisible();
});

test('A36 页面锁定、同消息修订、解锁与采用，历史快照只读', async ({
  page,
  request,
}, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.name));
  const eventId = new URL(page.url()).searchParams.get('event') || '';
  let dialog = await action(page, 'confirm');
  await dialog.getByRole('button', { name: '保存裁定', exact: true }).click();
  await expect(dialog.getByRole('status')).toHaveText('裁定已保存。');
  await dialog
    .getByRole('combobox', { name: '人工动作', exact: true })
    .selectOption('lock');
  await dialog.getByRole('button', { name: '保存裁定', exact: true }).click();
  await expect
    .poll(
      async () => (await current(request, eventId)).field_states[target].locked,
    )
    .toBe(true);
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  const historical = (await current(request, eventId)).updated_by[0].snapshot;
  const revised = await request.post('/synthetic/revise', { data: {} });
  expect(revised.ok()).toBe(true);
  await expect(
    page.getByRole('region', { name: '字段事实与人工裁定' }),
  ).toContainText('新建议，待复核：200');
  let event = await current(request, eventId);
  expect(event.attributes.目标总量与增减量).toBe('2000');
  expect(event.summary).toContain('本次办理继续');
  expect(event.summary).not.toContain('字段建议待复核：本次办理继续');
  expect(event.field_states[target].locked).toBe(true);
  expect(event.updated_by[0].snapshot).toEqual(historical);
  await page.screenshot({
    path: testInfo.outputPath('synthetic-a36-review.png'),
    fullPage: true,
  });
  dialog = await action(page, 'unlock');
  await dialog.getByRole('button', { name: '保存裁定', exact: true }).click();
  await expect
    .poll(
      async () => (await current(request, eventId)).field_states[target].locked,
    )
    .toBe(false);
  event = await current(request, eventId);
  expect(event.attributes.目标总量与增减量).toBe('2000');
  expect(event.field_issues.some((issue) => issue.status === 'open')).toBe(
    true,
  );
  await dialog
    .getByRole('combobox', { name: '人工动作', exact: true })
    .selectOption('adopt');
  const suggestion = event.field_states[target].facts.find(
    (fact: API.FieldFact) => fact.status === 'suggested',
  );
  await dialog
    .getByRole('combobox', { name: '事实目标', exact: true })
    .selectOption(suggestion.id);
  for (const checkbox of await dialog.getByRole('checkbox').all())
    await checkbox.check();
  await dialog.getByRole('button', { name: '保存裁定', exact: true }).click();
  await expect
    .poll(
      async () => (await current(request, eventId)).attributes.目标总量与增减量,
    )
    .toBe('200');
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.goto(`/runs?uid=${uid}&stream=${stream}&cycle=0`);
  await page
    .locator('summary')
    .filter({ hasText: '展开已完成时段与事件变化' })
    .click();
  await page
    .getByRole('region', { name: '本轮变化' })
    .locator('.cycle-event')
    .first()
    .click();
  const history = page.getByRole('dialog', { name: '事件详情', exact: true });
  await expect(history).toContainText('历史快照');
  await expect(history).toContainText('2000');
  await expect(
    history.getByRole('button', { name: '人工确认与裁定', exact: true }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('双页面旧版本提交返回409并保留草稿，主动核对后可提交', async ({
  page,
  context,
  request,
}) => {
  const second = await context.newPage();
  await second.goto(page.url());
  const stale = await action(second, 'confirm');
  await stale
    .getByRole('textbox', { name: '裁定理由', exact: true })
    .fill('合成旧页面草稿保留');
  const fresh = await action(page, 'confirm');
  await fresh.getByRole('button', { name: '保存裁定', exact: true }).click();
  await expect(fresh.getByRole('status')).toHaveText('裁定已保存。');
  const response = second.waitForResponse(
    (item) =>
      item.request().method() === 'POST' &&
      item.url().endsWith('/field-decisions'),
  );
  await stale.getByRole('button', { name: '保存裁定', exact: true }).click();
  expect((await response).status()).toBe(409);
  await expect(
    stale.getByRole('textbox', { name: '裁定理由', exact: true }),
  ).toHaveValue('合成旧页面草稿保留');
  await expect(stale.getByRole('status')).toContainText('本次未追加裁定');
  const eventId = new URL(page.url()).searchParams.get('event') || '';
  expect((await current(request, eventId)).manual_operations).toHaveLength(1);
  await expect(
    stale.getByText('当前记录已有新版本', { exact: false }),
  ).toBeVisible();
  await stale
    .getByRole('button', { name: '重新核对当前版本', exact: true })
    .click();
  await stale.getByRole('button', { name: '保存裁定', exact: true }).click();
  await expect(stale.getByRole('status')).toHaveText('裁定已保存。');
  await second.close();
});

test('提交已落库但响应丢失时先核对操作收据，不重复写入', async ({
  page,
  request,
}) => {
  const eventId = new URL(page.url()).searchParams.get('event') || '';
  const dialog = await action(page, 'confirm');
  await page.route(
    '**/field-decisions',
    async (route) => {
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      await route.abort('failed');
    },
    { times: 1 },
  );
  await dialog.getByRole('button', { name: '保存裁定', exact: true }).click();
  await expect(
    dialog.getByRole('button', { name: '保存裁定', exact: true }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole('button', { name: '重新核对当前版本', exact: true }),
  ).toBeDisabled();
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page
    .getByRole('button', { name: '人工确认与裁定', exact: true })
    .click();
  await dialog
    .getByRole('button', { name: '核对本次提交结果', exact: true })
    .click();
  await expect(dialog.getByRole('status')).toContainText(
    '已核对本次操作提交成功',
  );
  expect((await current(request, eventId)).manual_operations).toHaveLength(1);
  await expect(
    dialog.getByRole('button', { name: '保存裁定', exact: true }),
  ).toBeEnabled();
});
