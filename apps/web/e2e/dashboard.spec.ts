import { expect, test } from '@playwright/test';

import { dockerDownFixture, mockFullApi, runListFixture } from './api-fixtures';

test('first-run doctor card shows recovery when Docker is unavailable and vanishes when healthy', async ({ page }) => {
  await mockFullApi(page, { runtime: dockerDownFixture });
  await page.goto('/');

  const doctorCard = page.getByTestId('first-run-card');
  await expect(doctorCard).toBeVisible();
  await expect(doctorCard.getByTestId('doctor-Docker')).toContainText('启动 Docker Desktop 后重试');
  await expect(doctorCard.getByTestId('doctor-Git')).toContainText('就绪');
  // 修复 Docker 后卡片消失(doctor 全绿)
  await page.route('**/api/v1/runtime/resources', (route) =>
    route.fulfill({ json: { ...dockerDownFixture, docker_available: true, resources: [], error_code: null, recovery: null } }),
  );
  await page.getByRole('button', { name: '刷新全部本地状态' }).click();
  await expect(page.getByTestId('first-run-card')).toBeHidden();
});

test('dashboard surfaces recent runs and active environments with deep links', async ({ page }) => {
  await mockFullApi(page);
  await page.goto('/');

  await expect(page.getByTestId('overview-metrics')).toBeVisible();
  const recentRun = page.getByTestId('recent-run-row').first();
  await expect(recentRun).toContainText('Supplier 本地集成');
  await recentRun.click();
  await expect(page).toHaveURL(/\/runs\/run-active-001/);

  await page.getByRole('link', { name: '总览', exact: true }).first().click();
  const activeWorkspace = page.getByTestId('active-workspace-row').first();
  await expect(activeWorkspace).toContainText('Supplier 本地集成');
  await activeWorkspace.click();
  await expect(page).toHaveURL(/\/workspaces\/workspace-supplier/);
  await expect(page.getByLabel('工作区名称')).toHaveValue('Supplier 本地集成');
});

test('dashboard run list rows carry their status and failure detail', async ({ page }) => {
  await mockFullApi(page, { runs: { runs: [runListFixture.runs[1]!] } });
  await page.goto('/');
  await expect(page.getByTestId('recent-run-row').first()).toContainText('类型检查失败');
});
