import { expect, test } from '@playwright/test';

/** 只读连接真实本地后端，不同步聊天、不截图或保存客户内容。 */
test('客户等级筛选、搜索及刷新保留', async ({ page }) => {
  await page.goto('/customers');
  const selector = page.getByLabel('客户范围');
  await expect(selector).toHaveValue('large');
  for (const segment of [
    'large',
    'super_large',
    'non_large',
    'pending',
    'all',
  ]) {
    const response = await page.request.get(
      `/api/v1/customers?segment=${segment}&limit=20`,
    );
    expect(response.status()).toBe(200);
    const result = await response.json();
    if (segment === 'super_large') {
      expect(
        result.items.every(
          (item: { segment: string; is_super_large: boolean }) =>
            item.segment === 'large' && item.is_super_large,
        ),
      ).toBe(true);
    } else if (segment !== 'all') {
      expect(
        result.items.every(
          (item: { segment: string }) => item.segment === segment,
        ),
      ).toBe(true);
    }
    await selector.selectOption(segment);
    await expect(
      page.locator('.customer-directory .customer-paging').first(),
    ).toContainText(`共 ${result.total} 条`);
    await expect(page.locator('.customer-table tbody tr')).toHaveCount(
      result.items.length,
    );
    if (!result.total) {
      await expect(
        page.getByRole('heading', { name: '没有符合筛选条件的客户' }),
      ).toBeVisible();
    }
  }
  await selector.selectOption('non_large');
  await page.reload();
  await expect(selector).toHaveValue('non_large');
  await selector.selectOption('large');
  await page
    .getByLabel('搜索客户 UID')
    .fill('synthetic-no-such-filter-customer');
  const search = page.waitForResponse(
    (response) =>
      response.url().includes('/api/v1/customers?') &&
      response.url().includes('keyword=synthetic-no-such-filter-customer'),
  );
  await page.getByRole('button', { name: '查询', exact: true }).click();
  const searched = await search;
  expect(new URL(searched.url()).searchParams.get('segment')).toBe('large');
  expect(new URL(searched.url()).searchParams.get('offset')).toBe('0');
  await expect(
    page.getByRole('heading', { name: '没有符合筛选条件的客户' }),
  ).toBeVisible();
  await page.getByLabel('搜索客户 UID').fill('');
  await page.getByRole('button', { name: '查询', exact: true }).click();
  await selector.selectOption('all');
  await expect(page.locator('.customer-link').first()).toBeVisible();
  await page.locator('.customer-link').first().click();
  expect(new URL(page.url()).searchParams.has('uid')).toBe(true);
  await selector.selectOption('pending');
  expect(new URL(page.url()).searchParams.has('uid')).toBe(false);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(selector).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
