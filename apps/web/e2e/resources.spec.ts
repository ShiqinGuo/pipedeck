import { expect, test } from '@playwright/test';

import {
  activeManagedPostgresFixture,
  cleanupPreviewFixture,
  disposableSecret,
  failedManagedPostgresFixture,
  managedRedisResource,
  minioResource,
  mockFullApi,
  postgresResource,
} from './api-fixtures';
import type { components } from '../src/api/schema';

type CleanupPreviewResponse = components['schemas']['CleanupPreviewResponse'];
type CleanupResultItem = components['schemas']['CleanupResultItem'];

test('docker middleware table shows protection state and endpoints', async ({ page }) => {
  await mockFullApi(page);
  await page.goto('/resources');

  const section = page.getByTestId('middleware-section');
  await expect(section.getByTestId('middleware-row')).toHaveCount(3);
  await expect(section.getByTestId('middleware-row').filter({ hasText: minioResource.name })).toContainText('受保护');
  await expect(section.getByTestId('middleware-row').filter({ hasText: postgresResource.name })).toContainText('127.0.0.1:5432->5432/tcp');
});

test('managed PostgreSQL provisions from workspace metadata and reconciles failures', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Managed middleware write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/resources');

  const section = page.getByTestId('managed-section');
  const failedRow = section.getByTestId('managed-row').filter({ hasText: failedManagedPostgresFixture.name });
  await expect(failedRow).toContainText('MANAGED_HOST_PORT_IN_USE');
  await failedRow.getByRole('button', { name: 'Reconcile' }).click();
  await expect(section.getByTestId('managed-row').filter({ hasText: failedManagedPostgresFixture.name })).toContainText('运行中');
  expect(api.writes.find((write) => write.path === `/api/v1/managed-middleware/${failedManagedPostgresFixture.id}/reconcile`)).toMatchObject({
    method: 'POST',
    token: 'e2e-token',
  });

  await section.getByRole('button', { name: '托管 PostgreSQL' }).click();
  const createDialog = page.getByTestId('managed-create-dialog');
  await createDialog.getByLabel('托管 PostgreSQL 所属工作区').selectOption('workspace-supplier');
  await createDialog.getByLabel('PostgreSQL 宿主端口').fill('56432');
  await createDialog.getByLabel('PostgreSQL 用户名').fill('supplier_local');
  await createDialog.getByLabel('PostgreSQL 数据库名').fill('supplier_local');
  await createDialog.getByLabel('PostgreSQL 密码 Secret').selectOption('secret-pg-password');
  await expect(createDialog).toContainText('响应、缓存与界面均不会包含原始值。');
  await createDialog.getByRole('button', { name: '创建并配置' }).click();
  await expect(section.getByTestId('managed-row').filter({ hasText: '127.0.0.1:56432' })).toBeVisible();

  const provisionWrite = api.writes.find((write) => write.method === 'POST' && write.path === '/api/v1/managed-middleware');
  expect(provisionWrite).toMatchObject({
    method: 'POST',
    token: 'e2e-token',
    body: { workspace_id: 'workspace-supplier', kind: 'postgres', host_port: 56432, username: 'supplier_local', database: 'supplier_local', password_secret_ref: 'secret-pg-password' },
  });
  expect(JSON.stringify(api.getManagedResources())).not.toContain('fixture-postgres-password');
});

test('managed PostgreSQL deletion requires typing the resource name', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Destructive confirmation is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/resources');

  const section = page.getByTestId('managed-section');
  await section.getByRole('button', { name: `删除 ${activeManagedPostgresFixture.name}` }).click();
  const deleteDialog = page.getByTestId('managed-delete-dialog');
  const deleteButton = deleteDialog.getByRole('button', { name: '删除 PostgreSQL' });
  await expect(deleteButton).toBeDisabled();
  await deleteDialog.getByLabel('输入资源名确认删除').fill('wrong-name');
  await expect(deleteButton).toBeDisabled();
  await deleteDialog.getByLabel('输入资源名确认删除').fill(activeManagedPostgresFixture.name);
  await deleteButton.click();
  await expect(section.getByTestId('managed-row').filter({ hasText: activeManagedPostgresFixture.name })).toHaveCount(0);
  expect(api.writes.find((write) => write.path === `/api/v1/managed-middleware/${activeManagedPostgresFixture.id}`)).toMatchObject({
    method: 'DELETE',
    token: 'e2e-token',
  });
});

test('secret lifecycle creates versioned metadata and refuses in-use deletion', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Secret lifecycle is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/resources');

  const section = page.getByTestId('secrets-section');
  await section.getByRole('button', { name: '新建 Secret' }).click();
  const createDialog = page.getByTestId('secret-create-dialog');
  await expect(createDialog.getByLabel('新 Secret 值')).toHaveAttribute('type', 'password');
  await createDialog.getByLabel('新 Secret 名称').fill('One-off credential');
  await createDialog.getByLabel('新 Secret 值').fill('raw-create-secret-value');
  const createResponse = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/secrets');
  await createDialog.getByRole('button', { name: '创建 Secret' }).click();
  const createJson = (await (await createResponse).json()) as Record<string, unknown>;
  expect(createJson).not.toHaveProperty('value');
  expect(createJson.id).toBe('secret-created-1');
  await expect(section.getByTestId('secret-row').filter({ hasText: 'One-off credential' })).toContainText('值已保存');
  await expect(page.getByText('raw-create-secret-value')).toHaveCount(0);

  // in-use 引用的 Secret 删除被拒并给 recovery
  await section.getByRole('button', { name: `删除 Secret ${disposableSecret.name}` }).click();
  const deleteDialog = page.getByTestId('secret-delete-dialog');
  await deleteDialog.getByLabel('我确认删除此 Secret metadata 与系统凭据').click();
  await deleteDialog.getByRole('button', { name: '删除 Secret' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '操作未完成' })).toContainText('先移除工作区引用并保存');

  const createWrite = api.writes.find((write) => write.method === 'POST' && write.path === '/api/v1/secrets');
  expect(createWrite).toMatchObject({ token: 'e2e-token', body: { name: 'One-off credential', value: 'raw-create-secret-value' } });
});

test('cleanup preview keeps MinIO and the last healthy middleware protected', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Destructive confirmation flow is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/resources');

  await page.getByTestId('cleanup-section').getByRole('button', { name: '清理预览' }).click();
  const dialog = page.getByTestId('cleanup-preview-dialog');
  await expect(dialog).toContainText('MinIO 始终受保护');
  await expect(dialog).toContainText('PostgreSQL 至少保留 1 个健康实例');
  // 保护项不可勾选:只有 managedRedis 一项可选
  const checkable = dialog.getByTestId('cleanup-row').locator('input:not(:disabled)');
  await expect(checkable).toHaveCount(1);
  await checkable.check();
  await dialog.getByLabel('我已核对所选资源(1 项)').click();
  await dialog.getByRole('button', { name: '清理 1 项' }).click();
  await expect(page.getByTestId('cleanup-results-dialog').getByTestId('cleanup-summary')).toContainText('已删除 1 项');
  await expect
    .poll(() => api.writes.some((write) => write.path === `/api/v1/runtime/cleanup-previews/${cleanupPreviewFixture.id}/apply` && write.token === 'e2e-token'))
    .toBe(true);
});

test('cleanup renders removed, skipped and failed outcomes', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Mixed outcomes are verified once.');
  const removedResource = { ...managedRedisResource, id: 'resource-redis-removed', name: 'redis-old-removed' };
  const skippedResource = { ...managedRedisResource, id: 'resource-redis-skipped', name: 'redis-still-in-use' };
  const failedResource = { ...managedRedisResource, id: 'resource-redis-failed', name: 'redis-remove-failed' };
  const mixedPreview = {
    ...cleanupPreviewFixture,
    id: 'cleanup-preview-mixed',
    items: [
      ...cleanupPreviewFixture.items.filter((item) => !item.eligible),
      { resource: removedResource, eligible: true, reason_code: null, reason: null },
      { resource: skippedResource, eligible: true, reason_code: null, reason: null },
      { resource: failedResource, eligible: true, reason_code: null, reason: null },
    ],
  } as CleanupPreviewResponse;
  const mixedResults: CleanupResultItem[] = [
    { resource_id: removedResource.id, resource_name: removedResource.name, status: 'removed', reason_code: null, detail: null },
    { resource_id: skippedResource.id, resource_name: skippedResource.name, status: 'skipped', reason_code: 'RESOURCE_NO_LONGER_ELIGIBLE', detail: '运行时状态已变化，未执行删除' },
    { resource_id: failedResource.id, resource_name: failedResource.name, status: 'failed', reason_code: 'REMOVE_FAILED', detail: 'Docker 拒绝删除容器' },
  ];
  const api = await mockFullApi(page, { cleanupPreview: mixedPreview, cleanupResults: mixedResults });
  await page.goto('/resources');

  await page.getByTestId('cleanup-section').getByRole('button', { name: '清理预览' }).click();
  const previewDialog = page.getByTestId('cleanup-preview-dialog');
  const checkable = previewDialog.getByTestId('cleanup-row').locator('input:not(:disabled)');
  await expect(checkable).toHaveCount(3);
  for (const checkbox of await checkable.all()) await checkbox.check();
  await previewDialog.getByLabel('我已核对所选资源(3 项)').click();
  await previewDialog.getByRole('button', { name: '清理 3 项' }).click();

  const resultDialog = page.getByTestId('cleanup-results-dialog');
  await expect(resultDialog.getByTestId('cleanup-summary')).toContainText('已删除 1 项 · 已跳过 1 项 · 失败 1 项');
  await expect(resultDialog.getByTestId('cleanup-result').filter({ hasText: removedResource.name })).toContainText('已删除');
  await expect(resultDialog.getByTestId('cleanup-result').filter({ hasText: skippedResource.name })).toContainText('已跳过');
  await expect(resultDialog.getByTestId('cleanup-result').filter({ hasText: failedResource.name })).toContainText('删除失败');
  const applyWrite = api.writes.find((write) => write.path === `/api/v1/runtime/cleanup-previews/${mixedPreview.id}/apply`);
  expect(applyWrite?.body).toEqual({ preview_id: mixedPreview.id, resource_ids: mixedResults.map((item) => item.resource_id) });
});

test('resources page stays usable at 390px', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'Mobile coverage is consolidated here.');
  await mockFullApi(page);
  await page.goto('/resources');
  await expect(page.getByTestId('middleware-section')).toBeVisible();
  await expect(page.getByTestId('secrets-section')).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
