import { expect, test } from '@playwright/test';

test.skip(
  process.env.DAKE_CLASSIFICATION_LIVE !== '1',
  '仅显式连接第三阶段合成后端',
);

test.beforeEach(async ({ page, request }) => {
  const response = await request.get('/api/v1/config');
  expect(response.ok()).toBe(true);
  expect((await response.json()).uid).toBe('synthetic-classification');
  await page.route('**/*', (route) => {
    const request = route.request();
    return new URL(request.url()).origin ===
      new URL(process.env.DAKE_WEB_URL || '').origin &&
      request.method() === 'GET'
      ? route.continue()
      : route.abort();
  });
});

test('正式后端筛选多标签、其他、待分类与撤销标签，详情归属和旧依据准确', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.name));
  await page.goto(
    '/events?uid=synthetic-classification&stream=stub-v1&event=multi',
  );
  const rows = page.getByRole('list', { name: '售后事件' }).getByRole('button');
  await expect(rows).toHaveCount(5);
  const detail = page.getByRole('complementary', { name: '事件详情' });
  await expect(detail).toContainText('报价币种待核实');
  const resource = detail.locator('.classification-label-detail').filter({
    has: page.getByRole('heading', { name: '资源需求依据', exact: true }),
  });
  await expect(resource).toContainText('2000次');
  await expect(resource).toContainText('消息 resource');
  await expect(resource).not.toContainText('合成服务报价');
  const business = detail.locator('.classification-label-detail').filter({
    has: page.getByRole('heading', { name: '商务需求依据', exact: true }),
  });
  await expect(business).toContainText('未知');
  await expect(business).toContainText('消息 business');
  await expect(
    detail.getByRole('heading', { name: '已撤销标签' }),
  ).toBeVisible();
  await page.getByLabel('事件类型', { exact: true }).selectOption('商务需求');
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText('资源需求');
  await expect(rows.first()).toContainText('商务需求');
  await page.getByLabel('事件类型', { exact: true }).selectOption('资源需求');
  await expect(rows).toHaveCount(2);
  await page.getByLabel('事件类型', { exact: true }).selectOption('其他');
  await expect(rows).toHaveCount(1);
  await expect(detail).toContainText('额外内容是专项培训');
  await expect(detail).toContainText('消息 other');
  await page.getByLabel('事件类型', { exact: true }).selectOption('待分类');
  await expect(rows).toHaveCount(2);
  await expect(detail).toContainText('尚未确认类型');
  await page.getByLabel('事件类型', { exact: true }).selectOption('排障');
  await expect(rows).toHaveCount(1);
  await expect(detail).toContainText('历史记录未保存逐标签依据');
  await expect(detail).toContainText('标签依据未记录');
  await expect(
    detail.locator('.classification-fact-evidence blockquote'),
  ).toHaveCount(0);
  await page.getByLabel('事件类型', { exact: true }).selectOption('商机');
  await expect(page.getByText('暂无商机事件', { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('周期详情保留当时标签和依据，切换周期后展示更正快照', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.name));
  await page.goto('/runs?uid=synthetic-classification&stream=stub-v1&cycle=0');
  await page
    .locator('summary')
    .filter({ hasText: '展开已完成时段与事件变化' })
    .click();
  const changes = page.getByRole('region', { name: '本轮变化' });
  await expect(changes.locator('.cycle-event').first()).toContainText(
    '商机、资源需求、商务需求',
  );
  await changes.locator('.cycle-event').first().click();
  const dialog = page.getByRole('dialog', { name: '事件详情' });
  await expect(dialog).toContainText('历史快照');
  await expect(
    dialog.getByRole('heading', { name: '商机依据', exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole('heading', { name: '已撤销标签', exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press('Escape');
  await page
    .getByRole('region', { name: '处理周期' })
    .getByRole('button')
    .nth(1)
    .click();
  await changes.locator('.cycle-event').first().click();
  await expect(
    dialog.getByRole('heading', { name: '已撤销标签', exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole('heading', { name: '商机依据', exact: true }),
  ).toHaveCount(0);
  await expect(dialog).toContainText('之前采购意向说错，本次没有采购计划。');
  await expect(dialog).toContainText('报价币种待核实');
  await expect(dialog).toContainText('2000次');
  await page.keyboard.press('Escape');
  await page
    .getByRole('region', { name: '处理周期' })
    .getByRole('button')
    .first()
    .click();
  await changes
    .locator('.cycle-event')
    .filter({ hasText: '合成验收：legacy' })
    .click();
  await expect(dialog).toContainText('历史记录未保存逐标签依据');
  await expect(dialog).toContainText('标签依据未记录');
  await expect(
    dialog.locator('.classification-fact-evidence blockquote'),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});
