import { expect, test } from '@playwright/test';

import { blockedPipelinePreviewFixture, mockFullApi, repositoryFixture } from './api-fixtures';

test('pipeline preview renders job table with stage, needs, image and when', async ({ page }) => {
  await mockFullApi(page);
  await page.goto(`/pipelines/${repositoryFixture.id}`);

  const table = page.getByTestId('pipeline-job-table');
  await expect(table).toBeVisible();
  await expect(table.getByTestId('pipeline-job-row')).toHaveCount(4);
  await expect(table).toContainText('build-app');
  await expect(table).toContainText('python:3.12');
  await expect(table).toContainText('lint, prepare?'.split(',')[0]!); // needs 展示
  await expect(table).toContainText('manual');
  await expect(table).toContainText('never');

  // 变量展示
  await expect(page.getByTestId('pipeline-variables')).toContainText('PIPELINE_LANG');
  await expect(page.getByTestId('pipeline-variables')).toContainText('python');
});

test('blocking issues are never silent: run stays disabled with recovery', async ({ page }) => {
  await mockFullApi(page, { pipelinePreview: blockedPipelinePreviewFixture });
  await page.goto(`/pipelines/${repositoryFixture.id}`);

  const blockers = page.getByTestId('plan-blockers');
  await expect(blockers.first()).toBeVisible();
  await expect(page.getByRole('alert').filter({ hasText: 'PIPELINE_INCLUDE_UNAVAILABLE' })).toContainText('强制刷新 include');
  const runButton = page.getByRole('button', { name: '运行', exact: true });
  await expect(runButton).toBeDisabled();
  await expect(runButton).toHaveAttribute('title', /存在阻断项/);
});

test('confirm refresh sends ?refresh=true and a healthy pipeline runs through plan then run', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Run flow write contract is verified once.');
  const api = await mockFullApi(page);
  const refreshRequests: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/pipeline') && request.method() === 'GET') refreshRequests.push(request.url());
  });
  await page.goto(`/pipelines/${repositoryFixture.id}`);

  await page.getByRole('button', { name: '强制刷新 include' }).click();
  await expect.poll(() => refreshRequests.some((url) => url.includes('refresh=true'))).toBe(true);

  await page.getByRole('button', { name: '运行', exact: true }).click();
  const planDialog = page.getByTestId('plan-dialog');
  await expect(planDialog).toContainText('运行计划');
  await expect(planDialog).toContainText('build-app');
  await expect(planDialog).toContainText('Immutable images'.slice(0, 8)); // deployments 段展示
  await expect(planDialog.getByRole('button', { name: '运行', exact: true })).toBeEnabled();
  await planDialog.getByRole('button', { name: '运行', exact: true }).click();

  await expect(page).toHaveURL(/\/runs\/run-created-001/);
  await expect.poll(() => api.writes.some((write) => write.path === '/api/v1/repositories/repository-backend/pipeline/plan')).toBe(true);
  const runWrite = api.writes.find((write) => write.path === '/api/v1/runs');
  expect(runWrite).toMatchObject({ token: 'e2e-token', body: { plan_id: 'plan-pipeline-001' } });
});

test('pipeline page shows equivalent CLI command', async ({ page }) => {
  await mockFullApi(page);
  await page.goto(`/pipelines/${repositoryFixture.id}`);
  await expect(page.locator('code', { hasText: `pipedeck pipeline list "${repositoryFixture.name}"` }).first()).toBeVisible();
  await expect(page.locator('code', { hasText: 'pipedeck run' }).first()).toBeVisible();
});

test('pipeline preview keeps the job table usable at 390px', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'Mobile coverage is consolidated here.');
  await mockFullApi(page);
  await page.goto(`/pipelines/${repositoryFixture.id}`);
  await expect(page.getByTestId('pipeline-job-table')).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
