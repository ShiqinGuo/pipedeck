import { expect, test } from '@playwright/test';

import { mockFullApi, repositoryFixture } from './api-fixtures';

test('repository rows expose branch, HEAD and dirty state with per-row actions', async ({ page }) => {
  await mockFullApi(page);
  await page.goto('/projects');

  const row = page.getByTestId('repository-row').filter({ hasText: repositoryFixture.name });
  await expect(row).toContainText('feat/local-control');
  await expect(row).toContainText('0a1b2…90123');
  await expect(row).toContainText('DIRTY');
});

test('dirty repository blocks update with an explicit reason while clean ones update', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/projects');

  const dirtyRow = page.getByTestId('repository-row').filter({ hasText: repositoryFixture.name });
  await expect(dirtyRow.getByRole('button', { name: `更新 ${repositoryFixture.name}` })).toBeDisabled();
  // dirty worktree 禁用原因必须可见而非静默
  await expect(dirtyRow.getByRole('button', { name: `更新 ${repositoryFixture.name}` })).toHaveAttribute('title', /未提交修改/);

  // 克隆一个 clean 仓库后更新可用
  await page.getByRole('button', { name: '克隆仓库' }).click();
  await page.getByLabel('Git URL').fill('git@gitlab.example.com:pipedeck/local-web.git');
  await page.getByLabel('目标父目录').fill('D:\\code');
  await page.getByLabel('目录名（可选）').fill('local-web');
  await page.getByRole('button', { name: '开始克隆' }).click();
  await expect(page.getByTestId('repository-row').filter({ hasText: 'local-web' })).toBeVisible();
  await page.getByRole('button', { name: '更新 local-web' }).click();
  await expect.poll(() => api.writes.some((write) => write.method === 'POST' && write.path.endsWith('/update'))).toBe(true);
});

test('import and clone send typed payloads with the local write token', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/projects');

  await page.getByRole('button', { name: '导入', exact: true }).click();
  // 浏览器开发模式:原生目录选择器禁用且解释原因
  const browserPicker = page.getByRole('dialog').getByRole('button', { name: '选择目录' });
  await expect(browserPicker).toBeDisabled();
  await expect(browserPicker).toHaveAttribute('title', /浏览器开发模式无法打开系统目录选择器/);
  await page.getByLabel('本机目录').fill('D:\\code\\local-api');
  await page.getByRole('dialog').getByRole('button', { name: '导入仓库' }).click();
  await expect(page.getByTestId('repository-row').filter({ hasText: 'imported-repository' })).toBeVisible();

  const importWrite = api.writes.find((write) => write.path === '/api/v1/repositories/import');
  expect(importWrite).toMatchObject({ method: 'POST', token: 'e2e-token', body: { path: 'D:\\code\\local-api' } });
});

test('checkout is previewed, blocked on dirty worktree and recovers after fix', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Checkout blocking contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/projects');

  const row = page.getByTestId('repository-row').filter({ hasText: repositoryFixture.name });
  await row.getByRole('button', { name: '切换 ref' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('dirty worktree 不重置不覆盖');
  await page.getByLabel('目标 ref(branch / tag / SHA)').fill('release/1.4');

  // dirty worktree:后端 409,UI 显式阻断并给 recovery
  await dialog.getByRole('button', { name: '切换 ref', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('先提交或暂存修改');
  await expect(dialog.getByRole('button', { name: '重试切换' })).toBeVisible();

  // 修复 dirty 后重试成功
  await page.route('**/api/v1/repositories/*/checkout', async (route) => {
    const request = route.request();
    if (request.method() !== 'POST') return route.fallback();
    const body = request.postDataJSON() as { ref: string };
    await route.fulfill({ json: { ...repositoryFixture, dirty: false, branch: body.ref, head_sha: 'b0b1b2b3b4b5b67890123' } });
  });
  await dialog.getByRole('button', { name: '重试切换' }).click();
  await expect(page.getByTestId('repository-row').filter({ hasText: 'release/1.4' })).toBeVisible();
  const checkoutWrite = api.writes.find((write) => write.path.endsWith('/checkout'));
  expect(checkoutWrite).toMatchObject({ method: 'POST', token: 'e2e-token', body: { ref: 'release/1.4' } });
});
