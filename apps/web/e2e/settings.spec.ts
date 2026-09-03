import { expect, test } from '@playwright/test';

import { mockFullApi } from './api-fixtures';

test('settings shows session write state, CLI commands and scan roots', async ({ page }) => {
  await mockFullApi(page);
  await page.goto('/settings');

  const session = page.getByTestId('session-card');
  await expect(session).toContainText('已启用');
  await expect(session).toContainText('tauri-command-or-explicit-environment');

  // CLI 状态提示位:serve/run/doctor 等价命令
  await expect(page.locator('code', { hasText: 'pipedeck serve' }).first()).toBeVisible();
  await expect(page.locator('code', { hasText: 'pipedeck run <repo> --wait' }).first()).toBeVisible();
  await expect(page.locator('code', { hasText: 'pipedeck doctor' }).first()).toBeVisible();

  await expect(page.getByTestId('scan-roots-card')).toContainText('D:\\code');
});

test('settings explains read-only mode when no write token is configured', async ({ page }) => {
  const { readOnlySessionFixture } = await import('./api-fixtures');
  await mockFullApi(page, { session: readOnlySessionFixture });
  await page.goto('/settings');
  await expect(page.getByTestId('session-card')).toContainText('只读');
});
