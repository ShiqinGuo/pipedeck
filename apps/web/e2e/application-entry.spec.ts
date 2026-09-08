import { expect, test } from '@playwright/test';
import type { components } from '../src/api/schema';
import { mockFullApi, workspaceFixture } from './api-fixtures';

test('custom application path is saved separately from readiness and invalid paths explain recovery', async ({ page }, testInfo) => {
  const api = await mockFullApi(page);
  await page.goto(`/workspaces/${workspaceFixture.id}`);
  await page.getByRole('tab', { name: '目标', exact: true }).click();
  await page.getByRole('checkbox', { name: '自定义应用测试入口' }).check();
  await page.getByLabel('应用路径', { exact: true }).fill('//example.com');
  await expect(page.getByRole('button', { name: '保存', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '保存', exact: true })).toHaveAttribute('title', /单个/);
  await page.getByLabel('应用路径', { exact: true }).fill('/docs');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => api.writes.filter((write) => write.method === 'PUT').length).toBe(1);
  const saved = api.writes.find((write) => write.method === 'PUT')?.body as components['schemas']['WorkspaceRecord'];
  expect(saved.services[0]?.execution_target.application).toEqual({ endpoint: saved.services[0]?.execution_target.endpoints[0]?.name, path: '/docs' });
  expect(saved.services[0]?.execution_target.readiness).toEqual(workspaceFixture.services[0]?.execution_target.readiness);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('application-entry.png'), fullPage: true });
});

test('desktop test entry invokes the local browser command and offers recovery on failure', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'isTauri', { value: true });
    Object.defineProperty(window, '__TAURI_INTERNALS__', { value: { invoke: (command: string, payload?: { url?: string }) => {
      if (command === 'local_api_token') return Promise.resolve('e2e-token');
      if (command === 'open_local_application' && payload?.url === 'http://127.0.0.1:45173/docs') return Promise.reject(new Error('browser failed'));
      return Promise.resolve();
    } } });
  });
  await mockFullApi(page, { workspaceRuntime: {
    workspace_id: workspaceFixture.id, generated_at: '2026-09-08T08:00:00Z', ready: true, ready_count: 1, latest_run: null,
    services: [{ project_id: 'api', name: '本地 API', target: 'host', status: 'ready', configured: true, detail: 'Ready', recovery: null,
      run_id: null, revision_id: null, workspace_revision: 1, branch: 'main', head: null, dirty: false, url: 'http://127.0.0.1:45173/docs' }],
  } });
  await page.goto(`/workspaces/${workspaceFixture.id}`);
  await page.getByRole('button', { name: '打开应用', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('浏览器未能打开');
  await expect(page.getByRole('button', { name: '打开应用', exact: true })).toBeEnabled();
});
