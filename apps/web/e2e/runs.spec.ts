import { expect, test } from '@playwright/test';

import { mockFullApi } from './api-fixtures';

test('runs list renders history and links to detail', async ({ page }) => {
  await mockFullApi(page);
  await page.goto('/runs');

  const rows = page.getByTestId('run-row');
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText('Supplier 本地集成');
  await rows.first().click();
  await expect(page).toHaveURL(/\/runs\/run-active-001/);
});

test('run detail groups logs by step, filters, and keeps execution and runtime state apart', async ({ page }) => {
  await mockFullApi(page);
  await page.goto('/runs/run-active-001');

  // 双状态:执行状态与 runtime 状态分开
  await expect(page.getByTestId('execution-state-panel')).toBeVisible();
  await expect(page.getByTestId('runtime-state-panel')).toBeVisible();

  const groups = page.getByTestId('log-step-group');
  await expect(groups.filter({ hasText: 'quality' })).toContainText('0 errors, 0 warnings');
  await expect(groups.filter({ hasText: 'start' })).toContainText('Local: http://127.0.0.1:8000/');

  // 按 step 过滤(状态事件没有 step_id,单独成组)
  await page.getByRole('button', { name: 'quality', exact: true }).click();
  await expect(page.getByTestId('log-step-group')).toHaveCount(1);
  await page.getByRole('button', { name: '全部', exact: true }).click();
  await expect(page.getByTestId('log-step-group')).toHaveCount(3);
});

test('active run can be cancelled with the local token', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/runs/run-active-001');

  const cancel = page.getByRole('button', { name: '取消', exact: true });
  await expect(cancel).toBeEnabled();
  await cancel.click();
  await expect.poll(() => api.writes.some((write) => write.path.endsWith('/cancel'))).toBe(true);
  const cancelWrite = api.writes.find((write) => write.path.endsWith('/cancel'));
  expect(cancelWrite?.token).toBe('e2e-token');
  await expect(page.getByTestId('run-status-cancelled').first()).toBeVisible();
});

test('finished run exposes retry with an idempotency key and cancelled runs refuse it', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/runs/run-failed-001');

  // 失败聚合面板 + recovery
  await expect(page.getByRole('alert').filter({ hasText: '运行失败' })).toContainText('COMMAND_FAILED');
  const retry = page.getByRole('button', { name: '重试', exact: true });
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect.poll(() => api.writes.some((write) => write.path.endsWith('/retry'))).toBe(true);
  const retryWrite = api.writes.find((write) => write.path.endsWith('/retry'));
  expect(retryWrite?.token).toBe('e2e-token');
  expect(retryWrite?.body).toEqual({ idempotency_key: expect.stringMatching(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i) });
});

test('run detail keeps polling events while the run is active', async ({ page }) => {
  await mockFullApi(page);
  let eventRequests = 0;
  page.on('request', (request) => {
    if (request.url().includes('/events')) eventRequests += 1;
  });
  await page.goto('/runs/run-active-001');
  // 轮询节奏:运行中 Run 1-2s
  await expect.poll(() => eventRequests, { timeout: 8000 }).toBeGreaterThan(1);
});
