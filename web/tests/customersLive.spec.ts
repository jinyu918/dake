import { expect, test } from '@playwright/test';

/** 仅在显式提供同步范围时运行；连接真实后端，不记录截图、轨迹或人员信息。 */
test('客户同步、资料查询、刷新恢复与重复同步', async ({ page }) => {
  const start = process.env.DAKE_CUSTOMER_SYNC_START;
  const end = process.env.DAKE_CUSTOMER_SYNC_END;
  test.skip(!start || !end, '须显式指定本地客户同步范围');
  test.setTimeout(180000);
  await page.goto('/customers?segment=all');
  await expect(
    page.getByRole('heading', { name: '客户', exact: true }),
  ).toBeVisible();
  await page.getByLabel('开始时间').fill(start || '');
  await page.getByLabel('结束时间').fill(end || '');
  const sync = async () => {
    await page.getByLabel('开始时间').fill(start || '');
    await page.getByLabel('结束时间').fill(end || '');
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith('/api/v1/customers/syncs') &&
        r.request().method() === 'POST',
    );
    await page.getByRole('button', { name: '开始同步', exact: true }).click();
    const submitted = await response;
    expect(submitted.status()).toBe(202);
    const { run_id: runId } = await submitted.json();
    await expect
      .poll(
        async () => {
          const result = await page.request.get(
            `/api/v1/customers/syncs/${runId}`,
          );
          return (await result.json()).status;
        },
        { timeout: 90000 },
      )
      .toMatch(/succeeded|failed/);
    const data = await (
      await page.request.get(`/api/v1/customers/syncs/${runId}`)
    ).json();
    expect(data.status).toBe('succeeded');
    expect(data.completed_windows).toBe(data.total_windows);
    await expect(
      page
        .getByRole('region', { name: '同步详情' })
        .getByRole('heading', { name: '已完成' }),
    ).toBeVisible();
    return data;
  };
  const first = await sync();
  await page.reload();
  await expect(page.getByRole('region', { name: '同步详情' })).toBeVisible();
  expect(new URL(page.url()).searchParams.get('sync')).toBe(first.run_id);
  const list = await (await page.request.get('/api/v1/customers')).json();
  expect(list.total).toBeGreaterThan(0);
  await page.locator('.customer-link').first().click();
  await expect(
    page.locator('.customer-group-list article').first(),
  ).toBeVisible();
  await page.getByRole('button', { name: '查看全部待确认群' }).click();
  await expect(
    page.getByRole('heading', { name: '归属待确认', exact: true }),
  ).toBeVisible();
  await page.getByLabel('搜索客户 UID').fill('synthetic-nonexistent-ui-query');
  await page.getByRole('button', { name: '查询', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: '没有符合筛选条件的客户' }),
  ).toBeVisible();
  await page.getByLabel('搜索客户 UID').fill('');
  await page.getByRole('button', { name: '查询', exact: true }).click();
  await expect(page.locator('.customer-link').first()).toBeVisible();
  const second = await sync();
  expect(
    second.cycles.reduce(
      (n: number, item: { new_customers: number }) => n + item.new_customers,
      0,
    ),
  ).toBe(0);
  expect(
    second.cycles.reduce(
      (n: number, item: { new_groups: number }) => n + item.new_groups,
      0,
    ),
  ).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await expect(
    page.getByRole('heading', { name: '客户', exact: true }),
  ).toBeVisible();
});
