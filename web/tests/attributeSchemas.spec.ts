import { expect, type Page, test } from '@playwright/test';

/** 配置写入和历史补齐仅由浏览器内的合成接口接收。 */
async function mockSchemas(page: Page) {
  const product: API.ProductSchemaItem = {
    product_key: 'example',
    name: '合成带宽产品',
    description: '合成产品说明',
    keywords: Array.from({ length: 18 }, (_, i) => `合成识别词${i + 1}`),
    sort_order: 0,
    enabled: true,
    builtin: true,
  };
  let field: API.AttributeFieldSchemaItem = {
    field_id: 'product:example:bandwidth',
    scope_type: 'product',
    scope_key: 'example',
    field_key: 'bandwidth',
    label: '申请带宽',
    data_type: 'number',
    unit: 'Mbps',
    unit_family: 'none',
    agg_ops: ['avg'],
    enum_options: [],
    applicable_types: ['资源需求'],
    extraction_hint: '客户明确提出扩容时，记录申请的带宽。',
    sort_order: 0,
    enabled: true,
    builtin: false,
  };
  let version = 4;
  const saved: API.AttributeFieldSaveRequest[] = [];
  const backfills: API.AttributeBackfillRequest[] = [];
  const registry = () => ({
    version,
    products: [product],
    fields: [field],
    unit_families: [],
    updated_at: '',
  });
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/config'))
      return route.fulfill({
        json: {
          uid: 'synthetic',
          customers: [],
          event_types: [],
          provider: 'stub',
        },
      });
    if (url.pathname.endsWith('/attribute-schemas'))
      return route.fulfill({ json: registry() });
    if (url.pathname.endsWith('/fields')) {
      const body: API.AttributeFieldSaveRequest = route
        .request()
        .postDataJSON();
      saved.push(body);
      field = { ...field, ...body.field };
      version++;
      return route.fulfill({ json: registry() });
    }
    if (url.pathname.endsWith('/analytics')) {
      const metrics: API.NumericMetricStat[] = [
        {
          field_id: field.field_id,
          scope_type: 'product',
          scope_key: product.product_key,
          field_key: 'bandwidth',
          label: '申请带宽',
          unit: 'Mbps',
          agg_ops: ['avg'],
          hit_count: 2,
          sum_value: 60,
          avg_value: 30,
          max_value: 40,
        },
      ];
      return route.fulfill({
        json: {
          total_events: 2,
          total_customers: 1,
          selected_product: url.searchParams.get('product_key'),
          selected_event_type: url.searchParams.get('event_type'),
          product_summaries: [],
          numeric_metrics: metrics,
          term_distributions: [],
          events: [],
        },
      });
    }
    if (url.pathname.endsWith('/backfill-job')) {
      if (route.request().method() === 'GET')
        return route.fulfill({
          json: { status: backfills.length ? 'running' : 'idle' },
        });
      backfills.push(route.request().postDataJSON());
      return route.fulfill({
        json: {
          status: 'running',
        },
      });
    }
    return route.fulfill({ status: 404, json: { detail: '合成接口未定义' } });
  });
  return { saved, backfills };
}

test('规则按需展开，编辑名称和停用操作保留字段的其他配置', async ({ page }) => {
  const { saved } = await mockSchemas(page);
  await page.goto('/settings/schemas');
  const field = page.getByRole('button', { name: '申请带宽', exact: true });
  await expect(field).toHaveAttribute('aria-expanded', 'false');
  await expect(
    page.getByText('客户明确提出扩容时，记录申请的带宽。'),
  ).toBeHidden();
  await field.focus();
  await page.keyboard.press('Enter');
  await expect(
    page.getByText('客户明确提出扩容时，记录申请的带宽。'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '删除字段' })).toBeVisible();
  await page.getByText('产品识别规则', { exact: true }).click();
  await expect(page.getByText(/合成识别词18/)).toBeVisible();
  const edit = page.getByRole('button', { name: '编辑申请带宽' });
  await edit.click();
  await page.keyboard.press('Escape');
  await expect(edit).toBeFocused();
  await edit.click();
  await page.getByLabel('字段名称').fill('目标带宽');
  await page.getByRole('button', { name: '保存字段' }).click();
  const toggle = page.getByRole('switch', { name: '目标带宽启用状态' });
  await expect(toggle).toBeEnabled();
  expect(saved[0]).toMatchObject({
    expected_version: 4,
    field: {
      label: '目标带宽',
      unit_family: 'none',
      sort_order: 0,
      agg_ops: ['avg'],
    },
  });
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  expect(saved[1]).toMatchObject({
    expected_version: 5,
    field: { enabled: false },
  });
});

test('手机首屏可管理字段，切换到事件类型后从正确范围新增', async ({ page }) => {
  const { saved } = await mockSchemas(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/settings/schemas');
  await expect(
    page.getByRole('button', { name: '编辑申请带宽' }),
  ).toBeInViewport();
  await expect(page.getByRole('switch')).toBeInViewport();
  const viewportFits = () =>
    page.evaluate(
      () => document.documentElement.scrollWidth === window.innerWidth,
    );
  expect(await viewportFits()).toBe(true);
  await page.getByRole('button', { name: '事件类型', exact: true }).click();
  await page
    .getByRole('combobox', { name: '配置范围', exact: true })
    .selectOption('event_type:排障');
  await expect(page.getByText('尚未配置字段。')).toBeVisible();
  await page.getByRole('button', { name: '新增字段' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('排障', { exact: true })).toBeVisible();
  await expect(dialog.getByLabel('计量单位')).toHaveValue('');
  await dialog.getByLabel('字段名称').fill('故障类别');
  await dialog.getByLabel('字段编码').fill('example_issue');
  await dialog.getByLabel('字段类型').selectOption('enum');
  await dialog.getByLabel('可选值').fill('超时，限流');
  await dialog.getByLabel('提取条件').fill('按合成描述记录故障类别。');
  await dialog.getByRole('button', { name: '保存字段' }).click();
  await expect(dialog).toHaveCount(0);
  expect(saved[0]).toMatchObject({
    field: {
      scope_type: 'event_type',
      scope_key: '排障',
      data_type: 'enum',
      enum_options: ['超时', '限流'],
      applicable_types: ['排障'],
    },
  });
  expect(await viewportFits()).toBe(true);
});

test('新增时提示已有产品编码，取消后返回目录', async ({ page }) => {
  await mockSchemas(page);
  await page.goto('/settings/schemas');
  await page.getByRole('button', { name: '新增产品' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('产品名称').fill('合成重名产品');
  await dialog.getByLabel('产品编码').fill('example');
  await dialog.getByRole('button', { name: '保存产品' }).click();
  await expect(dialog.getByRole('alert')).toContainText('该产品编码已存在');
  await dialog.getByRole('button', { name: '取消' }).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: '合成带宽产品' }),
  ).toBeVisible();
});

test('指标按配置展示，补齐当前范围使用汇总页的两个筛选条件', async ({
  page,
}) => {
  const { backfills } = await mockSchemas(page);
  await page.goto('/settings/schemas');
  await page.getByRole('tab', { name: '指标汇总' }).click();
  await page
    .getByRole('combobox', { name: '产品', exact: true })
    .selectOption('example');
  await page.getByRole('combobox', { name: '事件类型' }).selectOption('排障');
  await expect(page.getByText('30 Mbps', { exact: true })).toBeVisible();
  await expect(page.getByText(/40 Mbps|60 Mbps/)).toHaveCount(0);
  await page.getByRole('button', { name: '补齐历史字段' }).click();
  await page
    .getByRole('radio', { name: /当前范围.*合成带宽产品.*排障/ })
    .check();
  await page.getByRole('button', { name: '开始补齐' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(backfills).toEqual([
    {
      product_key: 'example',
      event_type: '排障',
      limit: 150,
      only_missing: false,
      use_llm: true,
    },
  ]);
});
