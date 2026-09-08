import { expect, test } from '@playwright/test';
import { mockFullApi, workspaceFixture } from './api-fixtures';

test('runtime shows every service and offers only verified HTTP test entries', async ({ page }, testInfo) => {
  await mockFullApi(page, { workspaceRuntime: {
    workspace_id: workspaceFixture.id, generated_at: '2026-09-07T08:00:00Z', ready: false, ready_count: 1, latest_run: null,
    services: [
      { project_id: 'frontend', name: '测试前端', target: 'host', status: 'ready', configured: true, detail: '就绪检查通过', recovery: null, url: 'http://127.0.0.1:45173/', run_id: 'run-active-001', revision_id: null, workspace_revision: 1, branch: 'feature/integration', head: 'abc123456789', dirty: false },
      { project_id: 'backend', name: '测试后端', target: 'compose', status: 'unhealthy', configured: true, detail: '数据库连接失败', recovery: '检查 PostgreSQL 连接配置后重试', url: null, run_id: 'run-active-001', revision_id: 'revision-1', workspace_revision: 1, branch: 'main', head: 'def123456789', dirty: false },
    ],
  } });
  await page.goto(`/workspaces/${workspaceFixture.id}`);
  const panel = page.getByTestId('runtime-state-panel');
  await expect(panel).toContainText('1 / 2 服务就绪');
  await expect(panel.getByTestId('integration-service')).toHaveCount(2);
  await expect(panel.getByRole('link', { name: '打开应用' })).toHaveCount(1);
  await expect(panel.getByRole('link', { name: '打开应用' })).toHaveAttribute('href', 'http://127.0.0.1:45173/');
  await expect(panel).toContainText('数据库连接失败');
  await expect(panel).toContainText('feature/integration');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('integration-environment.png'), fullPage: true });
});

test('refresh preserves edits and a newer server revision cannot overwrite the draft', async ({ page }) => {
  await mockFullApi(page);
  await page.goto(`/workspaces/${workspaceFixture.id}`);
  const name = page.getByLabel('工作区名称');
  await name.fill('我的未保存配置');
  await page.getByRole('button', { name: '刷新全部本地状态' }).click();
  await expect(name).toHaveValue('我的未保存配置');
  await page.route(`**/api/v1/workspaces/${workspaceFixture.id}`, async (route) => route.fulfill({ json: { ...workspaceFixture, revision: workspaceFixture.revision + 1, name: '另一窗口保存的配置' } }));
  await page.getByRole('button', { name: '刷新全部本地状态' }).click();
  await expect(page.getByRole('alert')).toContainText('你的修改已保留');
  await expect(name).toHaveValue('我的未保存配置');
  await expect(page.getByRole('button', { name: '保存', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '放弃草稿并载入最新配置' }).click();
  await expect(name).toHaveValue('另一窗口保存的配置');
});

test('branch preflight applies the chosen checkout before creating the plan', async ({ page }) => {
  const api = await mockFullApi(page);
  await page.goto(`/workspaces/${workspaceFixture.id}`);
  await page.getByTestId('environment-card').first().getByRole('button', { name: '应用并预检' }).click();
  await expect(page.getByTestId('plan-dialog')).toBeVisible();
  const applyIndex = api.writes.findIndex((write) => write.path.endsWith('/apply'));
  const planIndex = api.writes.findIndex((write) => write.path.endsWith('/plans'));
  expect(applyIndex).toBeGreaterThanOrEqual(0);
  expect(planIndex).toBeGreaterThan(applyIndex);
  expect(api.writes[planIndex]?.body).toEqual({ expected_revision: workspaceFixture.revision + 1 });
});
