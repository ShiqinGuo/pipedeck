import { test } from '@playwright/test';

import { backendProject, mockFullApi } from './api-fixtures';

test('capture complete client workbench', async ({ page }, testInfo) => {
  await mockFullApi(page);
  await page.goto('/');
  await page.screenshot({ path: testInfo.outputPath('overview.png'), fullPage: true });

  await page.getByRole('button', { name: '仓库', exact: true }).click();
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await page.getByLabel('本机目录').fill('D:\\code\\supplier-backend-v2');
  await page.screenshot({ path: testInfo.outputPath('repository-import.png'), fullPage: true });
  await page.getByRole('dialog').getByRole('button', { name: '取消' }).click();

  await page.getByRole('button', { name: '工作区', exact: true }).click();
  await page.locator('.service-row').filter({ hasText: backendProject.name }).click();
  await page.getByRole('tab', { name: '依赖', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('workspace.png'), fullPage: true });

  await page.getByRole('tab', { name: '目标', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('workspace-target.png'), fullPage: true });

  await page.getByRole('button', { name: '运行预检' }).click();
  await page.screenshot({ path: testInfo.outputPath('plan-deployment.png'), fullPage: true });
  await page.getByRole('dialog', { name: '运行计划' }).getByRole('button', { name: '返回配置' }).click();

  await page.getByRole('tab', { name: '依赖', exact: true }).click();
  await page.getByRole('button', { name: '管理 Secret', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('secrets.png'), fullPage: true });
  await page.getByRole('dialog', { name: 'Secret 管理' }).getByRole('button', { name: '完成' }).click();

  await page.getByRole('button', { name: '运行', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('runs.png'), fullPage: true });

  await page.getByRole('button', { name: '资源', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('resources.png'), fullPage: true });
  const managedSection = page.locator('.managed-section');
  await managedSection.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('resources-managed.png'), fullPage: true });
  await managedSection.getByRole('button', { name: '托管 PostgreSQL' }).click();
  await page.screenshot({ path: testInfo.outputPath('managed-postgres.png'), fullPage: true });
});

test('capture native directory picker', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Native directory selection is a desktop capability.');
  await page.addInitScript(() => {
    Object.defineProperty(window, '__TAURI_INTERNALS__', {
      configurable: true,
      value: {
        invoke: (command: string) => command === 'local_api_token'
          ? Promise.resolve('e2e-token')
          : Promise.resolve(null),
      },
    });
  });
  await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '仓库', exact: true }).click();
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('repository-import-native.png'), fullPage: true });
});
