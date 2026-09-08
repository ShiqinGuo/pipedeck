import { expect, test } from '@playwright/test';

import { backendProject, catalogFixture, environmentFixture, mockFullApi, workspaceFixture } from './api-fixtures';

test('same-name branches show their source projects and only configured checkouts are marked applied', async ({ page }) => {
  const frontend = { ...backendProject, id: 'project-frontend', name: 'Supplier 测试前端' };
  const backendCheckout = { ...backendProject, id: 'backend-checkout', name: 'backend-feature-checkout' };
  await mockFullApi(page, {
    catalog: { ...catalogFixture, projects: [backendProject, frontend, backendCheckout] },
    workspaces: { workspaces: [{ ...workspaceFixture, services: [
      { ...workspaceFixture.services[0]!, project_id: backendCheckout.id },
      { ...workspaceFixture.services[0]!, project_id: frontend.id },
    ] }] },
    environments: [
      { ...environmentFixture, id: 'backend-env', repository_id: backendCheckout.id, source_repository_id: backendProject.id, ref: 'feature/test', worktree_path: 'D:\\worktrees\\backend\\feature-test' },
      { ...environmentFixture, id: 'frontend-env', repository_id: 'frontend-checkout', source_repository_id: frontend.id, ref: 'feature/test', worktree_path: 'D:\\worktrees\\frontend\\feature-test' },
    ],
  });
  await page.goto(`/workspaces/${workspaceFixture.id}`);
  const cards = page.getByTestId('environment-card');
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toContainText('所属项目：supplier-backend-v2');
  await expect(cards.nth(0)).toContainText('当前已应用');
  await expect(cards.nth(0)).not.toContainText('backend-feature-checkout');
  await expect(cards.nth(1)).toContainText('所属项目：Supplier 测试前端');
  await expect(cards.nth(1)).not.toContainText('当前已应用');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
