import { expect, type Page, test } from '@playwright/test';

/** 页面验收只使用合成账号，不向业务库写入配置。 */
async function mockSettings(page: Page) {
  let version = '1'.repeat(64);
  let items: API.SenderRuleResponse[] = [];
  let conflict = false;
  const saved: API.SenderRuleSave[] = [];
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/config')) {
      return route.fulfill({
        json: {
          uid: 'synthetic-customer',
          provider: 'stub',
          customers: [],
          event_types: [],
        },
      });
    }
    if (url.pathname.endsWith('/sender-rules/candidates')) {
      return route.fulfill({
        json: {
          total: 1,
          items: [
            {
              uid: 'synthetic-customer',
              source: 'official_chat',
              sender_id: 'synthetic-bot',
              conversation_id: 'synthetic-group',
              sender_name: '合成MAAS通知',
              conversation_name: '合成客户群',
              message_count: 3,
              last_seen_at: '2026-11-10T10:00:00+08:00',
              sender_kind: items[0]?.enabled ? 'bot' : 'unknown',
              configured: items.length > 0,
              rule_id: 'a'.repeat(64),
            },
          ],
        },
      });
    }
    if (url.pathname.endsWith('/sender-rules')) {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as API.SenderRuleSave;
        saved.push(body);
        if (conflict)
          return route.fulfill({
            status: 409,
            json: { detail: '配置已被更新，请刷新后再保存' },
          });
        items = [
          {
            ...body.rule,
            kind: body.rule.kind || 'bot',
            label: body.rule.label || '',
            enabled: body.rule.enabled ?? true,
            conversation_id: body.rule.conversation_id || null,
            rule_id: 'a'.repeat(64),
          },
        ];
        version = '2'.repeat(64);
      }
      return route.fulfill({ json: { version, items } });
    }
    return route.fulfill({ json: { total: 0, items: [] } });
  });
  return {
    saved,
    setConflict: () => {
      conflict = true;
    },
  };
}

test('查找候选、配置当前群、保存后保留以及启停规则', async ({ page }) => {
  const state = await mockSettings(page);
  await page.goto('/settings/bots');
  await expect(
    page.getByRole('heading', { name: '机器人筛选', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: '查找账号', exact: true }).click();
  await page
    .getByRole('button', { name: '查找 MAAS 账号', exact: true })
    .click();
  const row = page.getByRole('row').filter({ hasText: '合成MAAS通知' });
  await expect(row.getByText('待确认', { exact: true })).toBeVisible();
  await row.getByRole('button', { name: '配置账号', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByLabel('适用范围')).toHaveValue('group');
  await dialog.getByLabel('确认依据').fill('合成测试人工核实');
  await dialog.getByRole('button', { name: '保存规则', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.saved[0].rule.conversation_id).toBe('synthetic-group');
  expect(state.saved[0].rule.kind).toBe('bot');
  await page.reload();
  await expect(page.getByText('已启用', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '停用', exact: true }).click();
  await expect(page.getByText('已停用', { exact: true })).toBeVisible();
  expect(state.saved.at(-1)?.rule.enabled).toBe(false);
  await page.getByRole('button', { name: '启用', exact: true }).click();
  await expect(page.getByText('已启用', { exact: true })).toBeVisible();
});

test('跨页面修改冲突保留表单，不显示保存成功', async ({ page }) => {
  const state = await mockSettings(page);
  await page.goto('/settings/bots');
  await page.getByRole('button', { name: '查找账号', exact: true }).click();
  await page.getByRole('button', { name: '配置账号', exact: true }).click();
  await page
    .getByRole('dialog')
    .getByLabel('适用范围')
    .selectOption('customer');
  state.setConflict();
  await page.getByRole('button', { name: '保存规则', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('配置已被更新');
  await expect(
    page.getByText('已保存，从后续处理窗口生效。历史事件保留原记录。'),
  ).toHaveCount(0);
  expect(state.saved[0].rule.conversation_id).toBeNull();
});
