import { expect, type Page, test } from '@playwright/test';

test.skip(process.env.DAKE_SAME_ROUND_LIVE !== '1', '仅连接独立合成纠正服务');
const uid = 'synthetic-classification-replay';
const stream = 'stub-v1';

async function editor(page: Page) {
  await page.getByRole('button', { name: '纠正同轮误拆', exact: true }).click();
  const dialog = page.getByRole('dialog', {
    name: '纠正同轮误拆',
    exact: true,
  });
  await expect(dialog.getByRole('status')).toContainText('已读取双方当前版本');
  await dialog
    .getByRole('textbox', { name: '操作人（本地填写）', exact: true })
    .fill('合成操作人');
  await dialog
    .getByRole('textbox', { name: '纠正理由', exact: true })
    .fill('核对相同环境与本次持续未恢复');
  await dialog
    .getByRole('textbox', { name: '同轮对象与范围', exact: true })
    .fill('A上传');
  await dialog
    .getByRole('checkbox', { name: '之前测试用错环境', exact: false })
    .check();
  await dialog
    .getByRole('checkbox', { name: '已核对同一对象', exact: false })
    .check();
  return dialog;
}

test.beforeEach(async ({ page, request }) => {
  await page.route('**/*', (route) =>
    new URL(route.request().url()).origin ===
    new URL(process.env.DAKE_WEB_URL || '').origin
      ? route.continue()
      : route.abort(),
  );
  const response = await request.post('/synthetic/reset', { data: {} });
  expect(response.ok()).toBe(true);
  const data = await response.json();
  await page.goto(`/events?uid=${uid}&stream=${stream}&event=${data.event_id}`);
});

test('正式HTTP纠正后列表唯一、旧链接可查且自动后续只进入保留项', async ({
  page,
  request,
}) => {
  const duplicate = new URL(page.url()).searchParams.get('event') || '';
  const dialog = await editor(page);
  const response = page.waitForResponse(
    (item) =>
      item.request().method() === 'POST' &&
      item.url().endsWith('/same-round-corrections'),
  );
  await dialog
    .getByRole('button', { name: '保存同轮纠正', exact: true })
    .click();
  expect((await response).status()).toBe(200);
  await expect(
    page.getByRole('link', { name: '保留事项', exact: true }),
  ).toBeVisible();
  expect(new URL(page.url()).searchParams.get('event')).toBe(duplicate);
  const listing = await (
    await request.get('/api/v1/events', { params: { uid, stream } })
  ).json();
  expect(listing.total).toBe(1);
  const follow = await request.post('/synthetic/follow', { data: {} });
  expect(follow.ok()).toBe(true);
  expect((await follow.json()).updated).toEqual([listing.items[0].event_id]);
  await page.getByRole('link', { name: '保留事项', exact: true }).click();
  await expect(
    page
      .getByText('A上传同一次故障仍失败，请继续跟进。', { exact: false })
      .first(),
  ).toBeVisible();
});

test('旧版本409保留草稿并可明确刷新双方', async ({ page, request }) => {
  const dialog = await editor(page);
  const eventId = new URL(page.url()).searchParams.get('event') || '';
  const current = await (
    await request.get(`/api/v1/events/${eventId}`, { params: { uid, stream } })
  ).json();
  const changed = await request.post(
    `/api/v1/events/${eventId}/field-decisions`,
    {
      data: {
        uid,
        stream,
        expected_version: current.version,
        operation_id: 'browser-stale',
        operator: '合成其他人',
        reason: '改变事项版本',
        actions: [
          {
            target: { kind: 'common', type_label: null, name: 'severity' },
            action: 'unknown',
          },
        ],
      },
    },
  );
  expect(changed.ok()).toBe(true);
  const response = page.waitForResponse(
    (item) =>
      item.request().method() === 'POST' &&
      item.url().endsWith('/same-round-corrections'),
  );
  await dialog
    .getByRole('button', { name: '保存同轮纠正', exact: true })
    .click();
  expect((await response).status()).toBe(409);
  await expect(
    dialog.getByRole('textbox', { name: '纠正理由', exact: true }),
  ).toHaveValue('核对相同环境与本次持续未恢复');
  await dialog
    .getByRole('button', { name: '刷新双方并重新核对', exact: true })
    .click();
  await expect(dialog.getByRole('status')).toContainText('已读取双方当前版本');
});

test('提交应答丢失后核对固定操作收据且不重复保存', async ({
  page,
  request,
}) => {
  const dialog = await editor(page);
  let operationId = '';
  await page.route('**/same-round-corrections', async (route) => {
    operationId = route.request().postDataJSON().operation_id;
    const saved = await route.fetch();
    expect(saved.status()).toBe(200);
    await route.abort('failed');
  });
  await dialog
    .getByRole('button', { name: '保存同轮纠正', exact: true })
    .click();
  await expect(
    dialog.getByRole('button', { name: '核对本次纠正结果', exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: '保存同轮纠正', exact: true }),
  ).toBeDisabled();
  const eventId = new URL(page.url()).searchParams.get('event') || '';
  const before = await (
    await request.get(`/api/v1/events/${eventId}`, { params: { uid, stream } })
  ).json();
  await dialog
    .getByRole('button', { name: '核对本次纠正结果', exact: true })
    .click();
  await expect(
    page.getByRole('link', { name: '保留事项', exact: true }),
  ).toBeVisible();
  const after = await (
    await request.get(`/api/v1/events/${eventId}`, { params: { uid, stream } })
  ).json();
  expect(after.version).toBe(before.version);
  expect(after.identity_corrections[0].operation_id).toBe(operationId);
});

test('确认未提交后只重试原请求，编辑前须重新核对', async ({ page }) => {
  const dialog = await editor(page);
  const requests: unknown[] = [];
  await page.route('**/same-round-corrections', async (route) => {
    requests.push(route.request().postDataJSON());
    if (requests.length === 1) await route.abort('failed');
    else await route.continue();
  });
  await dialog
    .getByRole('button', { name: '保存同轮纠正', exact: true })
    .click();
  await dialog
    .getByRole('button', { name: '核对本次纠正结果', exact: true })
    .click();
  await expect(dialog.getByRole('status')).toContainText('确认未提交');
  await expect(
    dialog.getByRole('textbox', { name: '纠正理由', exact: true }),
  ).toBeDisabled();
  await dialog
    .getByRole('button', { name: '重试原纠正请求', exact: true })
    .click();
  await expect(
    page.getByRole('link', { name: '保留事项', exact: true }),
  ).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
});
