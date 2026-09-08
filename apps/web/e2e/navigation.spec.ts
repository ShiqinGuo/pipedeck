import { expect, test } from '@playwright/test';

import { mockFullApi } from './api-fixtures';

async function expectNoPageOverflow(page: Parameters<typeof mockFullApi>[0]) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

const NAV_LABELS = ['总览', '仓库', '运行', '工作区', '资源', '设置'] as const;

test('new IA exposes six primary routes and stays dense on desktop', async ({ page }) => {
  await mockFullApi(page);
  await page.goto('/');

  await expect(page.getByRole('heading', { name: '本地 CI/CD 控制台' })).toBeVisible();
  const desktopNav = page.getByRole('navigation').filter({ has: page.locator('a[aria-label="总览"]') }).first();
  await expect(desktopNav.getByRole('link')).toHaveCount(NAV_LABELS.length);
  await expectNoPageOverflow(page);

  for (const label of NAV_LABELS) {
    await page.getByRole('link', { name: label, exact: true }).first().click();
    await expect(page.getByRole('link', { name: label, exact: true }).first()).toHaveAttribute('aria-current', 'page');
    await expectNoPageOverflow(page);
  }
});

test('primary routes keep content inside a 390px viewport without overlap', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'Mobile layout coverage is consolidated here.');
  await mockFullApi(page, {
    repositories: { repositories: [] },
    workspaces: { workspaces: [] },
    runs: { runs: [] },
  });
  await page.goto('/');
  for (const label of NAV_LABELS) {
    await page.getByRole('link', { name: label, exact: true }).first().click();
    await expectNoPageOverflow(page);
  }
});

test('workspace inspector keeps the service editor usable at 390px', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'The workspace inspector is the densest surface; it must survive 390px.');
  await mockFullApi(page);
  await page.goto('/workspaces');
  await page.getByTestId('workspace-row').getByRole('link').click();
  await expect(page.getByLabel('工作区名称')).toHaveValue('Supplier 本地集成');
  await page.getByTestId('service-row').first().click();
  for (const tab of ['环境', '目标', '依赖']) {
    await page.getByRole('tab', { name: tab, exact: true }).click();
    await expectNoPageOverflow(page);
  }
});
