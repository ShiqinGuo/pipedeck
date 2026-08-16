import { expect, test } from '@playwright/test';

import {
  activeManagedPostgresFixture,
  backendProject,
  catalogFixture,
  cleanupPreviewFixture,
  degradedDeploymentFixture,
  disposableSecret,
  failedManagedPostgresFixture,
  failedRunFixture,
  frontendProject,
  managedRedisResource,
  minioAccessSecret,
  minioKeySecret,
  minioResource,
  mockFullApi,
  postgresSecret,
  postgresResource,
  runningRunFixture,
  runtimeProcessFixture,
  workspaceFixture,
} from './api-fixtures';
import { BROWSER_DIRECTORY_PICKER_REASON } from '../src/lib/directory-picker';
import type { CleanupPreviewResponse, CleanupResultItem, MiddlewareKind, RunRecord, WorkspaceRecord } from '../src/types';

async function expectNoPageOverflow(page: Parameters<typeof mockFullApi>[0]) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

const secondaryWorkspace = { ...workspaceFixture, id: 'workspace-partner', name: 'Partner API 联调', revision: 5 } as WorkspaceRecord;
const thirdWorkspace = { ...workspaceFixture, id: 'workspace-inventory', name: 'Inventory 联调', revision: 2 } as WorkspaceRecord;
const thirdRun = {
  ...runningRunFixture,
  id: 'run-inventory-001',
  workspace_id: thirdWorkspace.id,
  workspace_name: thirdWorkspace.name,
  status: 'succeeded',
  current_step: 'start',
  finished_at: '2026-08-17T12:03:00Z',
} as RunRecord;

const cleanupRemovedResource = { ...managedRedisResource, id: 'resource-redis-removed', name: 'redis-old-removed' };
const cleanupSkippedResource = { ...managedRedisResource, id: 'resource-redis-skipped', name: 'redis-still-in-use' };
const cleanupFailedResource = { ...managedRedisResource, id: 'resource-redis-failed', name: 'redis-remove-failed' };
const mixedCleanupPreview = {
  ...cleanupPreviewFixture,
  id: 'cleanup-preview-mixed',
  items: [
    ...cleanupPreviewFixture.items.filter((item) => !item.eligible),
    { resource: cleanupRemovedResource, eligible: true, reason_code: null, reason: null },
    { resource: cleanupSkippedResource, eligible: true, reason_code: null, reason: null },
    { resource: cleanupFailedResource, eligible: true, reason_code: null, reason: null },
  ],
} as CleanupPreviewResponse;
const mixedCleanupResults: CleanupResultItem[] = [
  { resource_id: cleanupRemovedResource.id, resource_name: cleanupRemovedResource.name, status: 'removed', reason_code: null, detail: null },
  { resource_id: cleanupSkippedResource.id, resource_name: cleanupSkippedResource.name, status: 'skipped', reason_code: 'RESOURCE_NO_LONGER_ELIGIBLE', detail: '运行时状态已变化，未执行删除' },
  { resource_id: cleanupFailedResource.id, resource_name: cleanupFailedResource.name, status: 'failed', reason_code: 'REMOVE_FAILED', detail: 'Docker 拒绝删除容器' },
];

test('five-view local control plane stays dense and usable on desktop and mobile', async ({ page }) => {
  await mockFullApi(page);
  await page.goto('/');

  await expect(page.getByRole('heading', { name: '本地开发总览' })).toBeVisible();
  await expect(page.getByLabel('主导航').getByRole('button')).toHaveCount(5);
  await expect(page.getByText('Supplier 本地集成', { exact: true }).first()).toBeVisible();
  await expectNoPageOverflow(page);

  for (const name of ['工作区', '仓库', '运行', '资源', '总览']) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page.getByRole('button', { name, exact: true })).toHaveAttribute('aria-current', 'page');
    await expectNoPageOverflow(page);
  }
});

test('overview opens the exact workspace and run and keeps selection after reordered refresh', async ({ page }) => {
  const api = await mockFullApi(page, {
    workspaces: { workspaces: [workspaceFixture, secondaryWorkspace, thirdWorkspace] },
    runs: { runs: [runningRunFixture, failedRunFixture, thirdRun] },
  });
  await page.goto('/');

  await page.locator('.workspace-summary-row').filter({ hasText: secondaryWorkspace.name }).click();
  await expect(page.getByLabel('工作区名称')).toHaveValue(secondaryWorkspace.name);
  await expect(page.locator('.context-row.is-active')).toContainText(secondaryWorkspace.name);

  api.reorderWorkspaces([thirdWorkspace.id, workspaceFixture.id, secondaryWorkspace.id]);
  await page.getByRole('button', { name: '刷新全部本地状态' }).click();
  await expect(page.locator('.context-row').first()).toContainText(thirdWorkspace.name);
  await expect(page.locator('.context-row.is-active')).toContainText(secondaryWorkspace.name);
  await expect(page.getByLabel('工作区名称')).toHaveValue(secondaryWorkspace.name);

  await page.getByRole('button', { name: '总览', exact: true }).click();
  await page.locator('.run-summary-row').filter({ hasText: '失败' }).click();
  await expect(page.locator('.run-context-row.is-active')).toContainText('失败');
  await expect(page.getByText('supplier-backend-v2 类型检查失败')).toBeVisible();

  api.reorderRuns([thirdRun.id, runningRunFixture.id, failedRunFixture.id]);
  await page.getByRole('button', { name: '刷新全部本地状态' }).click();
  await expect(page.locator('.run-context-row').first()).toContainText(thirdWorkspace.name);
  await expect(page.locator('.run-context-row.is-active')).toContainText('失败');
  await expect(page.getByText('supplier-backend-v2 类型检查失败')).toBeVisible();
  await expectNoPageOverflow(page);
});

test('workspace edits save a revision, preflight renders typed commands, then creates a run', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'The full edit flow runs once; mobile layout is covered separately.');
  const api = await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '工作区', exact: true }).click();

  await expect(page.getByLabel('工作区名称')).toHaveValue(workspaceFixture.name);
  await expect(page.locator('.service-row')).toHaveCount(2);
  await page.locator('.service-row').filter({ hasText: backendProject.name }).click();
  await page.getByRole('tab', { name: '依赖', exact: true }).click();
  await expect(page.getByLabel('PostgreSQL 绑定')).toHaveValue(postgresResource.id);
  await expect(page.getByLabel('MinIO 绑定')).toHaveValue(minioResource.id);
  await expect(page.getByText('127.0.0.1:5432 → 5432/tcp')).toBeVisible();
  await expect(page.getByText('127.0.0.1:9000 → 9000/tcp')).toBeVisible();
  await expect(page.getByLabel('PostgreSQL 密码 Secret', { exact: true })).toHaveValue(postgresSecret.id);
  await expect(page.getByLabel('MinIO Access Key Secret', { exact: true })).toHaveValue(minioAccessSecret.id);
  await expect(page.getByLabel('MinIO Secret Key Secret', { exact: true })).toHaveValue(minioKeySecret.id);
  await page.getByLabel('PostgreSQL 数据库').fill('supplier_local');

  const name = page.getByLabel('工作区名称');
  await name.fill('Supplier 本地验收');
  const save = page.getByRole('button', { name: '保存', exact: true });
  await expect(save).toBeEnabled();
  await save.click();
  await expect.poll(() => api.writes.some((write) => write.method === 'PUT' && write.path.endsWith(`/workspaces/${workspaceFixture.id}`) && write.token === 'e2e-token')).toBe(true);
  const workspaceWrite = api.writes.find((write) => write.method === 'PUT' && write.path.endsWith(`/workspaces/${workspaceFixture.id}`));
  const savedBackend = (workspaceWrite?.body as WorkspaceRecord & { expected_revision: number }).services.find((service) => service.project_id === backendProject.id);
  expect(savedBackend?.connection_profiles).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'postgres', database: 'supplier_local', secret_ref: postgresSecret.id }),
    expect.objectContaining({ kind: 'minio', access_key_secret_ref: minioAccessSecret.id, secret_key_secret_ref: minioKeySecret.id }),
  ]));

  const preflight = page.getByRole('button', { name: '运行预检' });
  await expect(preflight).toBeEnabled();
  await preflight.click();
  const dialog = page.getByRole('dialog', { name: '运行计划' });
  await expect(dialog).toContainText(backendProject.path);
  await expect(dialog.locator('.plan-argv').first().locator('b')).toHaveText(['uv', 'run', 'pyright']);
  await expect(dialog).toContainText('连接注入预览');
  await expect(dialog).toContainText('postgresql+asyncpg://supplier:***@127.0.0.1:5432/supplier');
  await expect(dialog).not.toContainText('fixture-postgres-password');
  await expect(dialog).toContainText('Immutable images');
  await expect(dialog).toContainText('tripguru.local/supplier-admin@sha256:abcdef0123456789');
  await expect(dialog).not.toContainText('private-compose.yml');
  await expect(dialog).not.toContainText('fixture-raw-runtime-secret');
  await expect(dialog).not.toContainText(postgresSecret.id);
  await expect(dialog.getByRole('button', { name: '关闭' })).toBeFocused();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Shift+Tab');
  await expect(dialog.getByRole('button', { name: '关闭' })).toBeFocused();
  await dialog.getByRole('button', { name: '运行工作区' }).click();

  await expect(page.getByText('run-created', { exact: false }).first()).toBeVisible();
  await expect.poll(() => api.writes.some((write) => write.path === '/api/v1/runs' && write.token === 'e2e-token')).toBe(true);
});

test('target inspector preserves argv token boundaries and saves the discriminated targets', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'The target write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '工作区', exact: true }).click();

  await page.locator('.service-row').filter({ hasText: backendProject.name }).click();
  await page.getByRole('tab', { name: '命令', exact: true }).click();
  const testCommand = page.getByRole('region', { name: '运行测试 argv token' });
  await expect(testCommand.getByLabel('运行测试 参数 5', { exact: true })).toHaveValue('connection profile');
  await testCommand.getByLabel('运行测试 参数 5', { exact: true }).fill('connection profile with spaces');
  await testCommand.getByRole('button', { name: '添加 token' }).click();
  await testCommand.getByLabel('运行测试 参数 6', { exact: true }).fill('--maxfail=1');

  await page.getByRole('tab', { name: '目标', exact: true }).click();
  await expect(page.getByLabel('运行目标类型').getByRole('button', { name: '本机进程' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByLabel('http Host 端口')).toHaveValue('8000');
  await expect(page.getByLabel('http 端口参数选项')).toHaveValue('--port');

  await page.locator('.service-row').filter({ hasText: frontendProject.name }).click();
  await expect(page.getByLabel('运行目标类型').getByRole('button', { name: 'Docker Compose' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByLabel('Dockerfile build context')).toHaveValue('.');
  await expect(page.getByLabel('Dockerfile 相对路径')).toHaveValue('Dockerfile');
  await page.getByRole('button', { name: '已有 Compose' }).click();
  await page.getByRole('textbox', { name: 'Service names 1', exact: true }).fill('web');
  await page.getByRole('button', { name: '添加 Profiles' }).click();
  await page.getByRole('textbox', { name: 'Profiles 1', exact: true }).fill('local');

  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => api.writes.some((write) => write.method === 'PUT' && write.path.endsWith(`/workspaces/${workspaceFixture.id}`))).toBe(true);
  const write = [...api.writes].reverse().find((candidate) => candidate.method === 'PUT');
  const payload = write?.body as WorkspaceRecord & { expected_revision: number };
  const backend = payload.services.find((service) => service.project_id === backendProject.id);
  const frontend = payload.services.find((service) => service.project_id === frontendProject.id);
  expect(write?.token).toBe('e2e-token');
  expect(backend?.commands.find((command) => command.id === 'test')?.argv).toEqual(['uv', 'run', 'pytest', '-k', 'connection profile with spaces', '--maxfail=1']);
  expect(backend?.execution_target).toEqual(workspaceFixture.services[0]?.execution_target);
  expect(frontend?.execution_target).toMatchObject({ kind: 'compose', source: { kind: 'existing-compose', compose_files: ['compose.yml'], profiles: ['local'], service_names: ['web'] }, endpoints: [{ name: 'web', host_port: 5173, container_port: 5173 }], readiness: { kind: 'tcp', endpoint: 'web' }, wait_timeout: 120 });
  expect(backend && Object.hasOwn(backend, 'ports')).toBe(false);
  expect(frontend && Object.hasOwn(frontend, 'ports')).toBe(false);
});

test('target inspector remains usable on desktop and 390px', async ({ page }, testInfo) => {
  await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '工作区', exact: true }).click();
  await page.locator('.service-row').filter({ hasText: backendProject.name }).click();
  await page.getByRole('tab', { name: '目标', exact: true }).click();
  await expect(page.getByText('Host endpoints')).toBeVisible();
  await expect(page.getByText('Compose: compose.yml')).toBeVisible();
  await expect(page.getByLabel('Host readiness timeout')).toHaveValue('60');
  if (testInfo.project.name === 'mobile') {
    const box = await page.getByLabel('运行目标类型').getByRole('button', { name: 'Docker Compose' }).boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  }
  await expectNoPageOverflow(page);
});

test('Secret manager creates, auto-selects, versions, deletes and reports in-use references', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Secret write lifecycle is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '工作区', exact: true }).click();
  await page.locator('.service-row').filter({ hasText: backendProject.name }).click();
  await page.getByRole('tab', { name: '依赖', exact: true }).click();
  await page.getByRole('button', { name: '管理 PostgreSQL 密码 Secret' }).click();

  let dialog = page.getByRole('dialog', { name: 'Secret 管理' });
  await expect(dialog.getByLabel('新 Secret 值')).toHaveAttribute('type', 'password');
  await expect(dialog.getByLabel('新 Secret 值')).toHaveValue('');
  await dialog.getByLabel('新 Secret 名称').fill('One-off credential');
  await dialog.getByLabel('新 Secret 值').fill('raw-create-secret-value');
  const createResponsePromise = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/secrets');
  await dialog.getByRole('button', { name: '创建并选用' }).click();
  const createJson = await (await createResponsePromise).json() as Record<string, unknown>;
  expect(createJson).not.toHaveProperty('value');
  expect(createJson.id).toBe('secret-created-1');
  await expect(dialog.getByText('已创建 One-off credential 并选入当前字段')).toBeVisible();
  await expect(dialog.getByLabel('新 Secret 值')).toHaveValue('');
  await expect(dialog).not.toContainText('raw-create-secret-value');

  const updateInput = dialog.getByLabel('更新 One-off credential 的值');
  await expect(updateInput).toHaveValue('');
  await updateInput.fill('raw-updated-secret-value');
  const updateResponsePromise = page.waitForResponse((response) => response.request().method() === 'PUT' && new URL(response.url()).pathname === '/api/v1/secrets/secret-created-1');
  await dialog.getByRole('button', { name: '更新值' }).click();
  const updateJson = await (await updateResponsePromise).json() as Record<string, unknown>;
  expect(updateJson).not.toHaveProperty('value');
  expect(updateJson.version).toBe(2);
  await expect(updateInput).toHaveValue('');
  await expect(dialog).not.toContainText('raw-updated-secret-value');

  await dialog.getByLabel('我确认删除此 Secret metadata 与系统凭据').check();
  await dialog.getByRole('button', { name: '删除 Secret' }).click();
  await expect(dialog.getByText('Secret 已删除；未保存草稿中的相关引用已清空')).toBeVisible();
  expect(api.getSecrets().every((secret) => !Object.hasOwn(secret, 'value'))).toBe(true);
  await dialog.getByRole('button', { name: '完成' }).click();
  await expect(page.getByLabel('PostgreSQL 密码 Secret', { exact: true })).toHaveValue('');

  await page.getByRole('button', { name: '管理 Secret', exact: true }).click();
  dialog = page.getByRole('dialog', { name: 'Secret 管理' });
  await dialog.locator('.secret-row').filter({ hasText: postgresSecret.name }).click();
  await dialog.getByLabel('我确认删除此 Secret metadata 与系统凭据').check();
  await dialog.getByRole('button', { name: '删除 Secret' }).click();
  await expect(dialog.getByRole('alert')).toContainText('正被 Workspace 使用');
  await expect(dialog.getByRole('alert')).toContainText('先移除工作区引用并保存');

  const createWrite = api.writes.find((write) => write.method === 'POST' && write.path === '/api/v1/secrets');
  const updateWrite = api.writes.find((write) => write.method === 'PUT' && write.path === '/api/v1/secrets/secret-created-1');
  const createdDelete = api.writes.find((write) => write.method === 'DELETE' && write.path === '/api/v1/secrets/secret-created-1');
  expect(createWrite).toMatchObject({ token: 'e2e-token', body: { name: 'One-off credential', value: 'raw-create-secret-value' } });
  expect(updateWrite).toMatchObject({ token: 'e2e-token', body: { expected_version: 1, value: 'raw-updated-secret-value' } });
  expect(createdDelete?.query).toBe('?expected_version=2');
});

test('environment Secret source uses metadata selection while host-env keeps name validation', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Environment write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '工作区', exact: true }).click();
  await page.locator('.service-row').filter({ hasText: backendProject.name }).click();
  await page.getByRole('tab', { name: '环境', exact: true }).click();
  await page.getByRole('button', { name: '添加变量' }).click();
  await page.getByLabel('环境变量 2 名称').fill('APP_SECRET_TOKEN');
  await page.getByLabel('APP_SECRET_TOKEN 来源').selectOption('secret-store');
  await page.getByLabel('APP_SECRET_TOKEN Secret', { exact: true }).selectOption(disposableSecret.id);
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => api.writes.some((write) => write.method === 'PUT' && write.path.includes('/workspaces/'))).toBe(true);
  const payload = [...api.writes].reverse().find((write) => write.method === 'PUT')?.body as WorkspaceRecord;
  const backend = payload.services.find((service) => service.project_id === backendProject.id);
  expect(backend?.environment).toContainEqual({ name: 'APP_SECRET_TOKEN', source: 'secret-store', value: null, reference: disposableSecret.id });
});

test('new workspace generates generic profiles only after explicit Secret selection', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Workspace construction contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '工作区', exact: true }).click();
  await page.getByRole('button', { name: '新建工作区' }).click();
  const dialog = page.getByRole('dialog', { name: '新建工作区' });
  await dialog.getByLabel('工作区名称').fill('Profile defaults');
  await dialog.locator('.selection-row').filter({ hasText: backendProject.name }).getByRole('checkbox').check();
  await expect(dialog.getByRole('button', { name: '创建工作区' })).toBeDisabled();
  await dialog.getByLabel(`${backendProject.name} PostgreSQL 密码 Secret`, { exact: true }).selectOption(postgresSecret.id);
  await dialog.getByLabel(`${backendProject.name} MinIO Access Key Secret`, { exact: true }).selectOption(minioAccessSecret.id);
  await dialog.getByLabel(`${backendProject.name} MinIO Secret Key Secret`, { exact: true }).selectOption(minioKeySecret.id);
  await dialog.getByRole('button', { name: '创建工作区' }).click();
  await expect(page.getByLabel('工作区名称')).toHaveValue('Profile defaults');
  const createWrite = api.writes.find((write) => write.method === 'POST' && write.path === '/api/v1/workspaces');
  const service = (createWrite?.body as WorkspaceRecord).services[0];
  expect(service?.connection_profiles).toEqual([
    { kind: 'postgres', env_var: 'DATABASE_URL', scheme: 'postgresql', username: 'postgres', database: 'postgres', secret_ref: postgresSecret.id },
    { kind: 'minio', endpoint_env: 'S3_ENDPOINT', access_key_env: 'S3_ACCESS_KEY', secret_key_env: 'S3_SECRET_KEY', bucket_env: 'S3_BUCKET', bucket: 'local-assets', access_key_secret_ref: minioAccessSecret.id, secret_key_secret_ref: minioKeySecret.id, secure: false },
  ]);
});

test('unsupported connection adapters are explicit preflight blockers', async ({ page }) => {
  const redisProject = { ...frontendProject, id: 'project-redis-client', name: 'redis-client', requirements: ['redis'] as MiddlewareKind[] };
  const redisWorkspace = { ...workspaceFixture, id: 'workspace-redis-client', name: 'Redis adapter check', services: [{ ...workspaceFixture.services[1]!, project_id: redisProject.id, connection_profiles: [] }], bindings: [] };
  await mockFullApi(page, { catalog: { ...catalogFixture, projects: [redisProject] }, workspaces: { workspaces: [redisWorkspace] } });
  await page.goto('/');
  await page.getByRole('button', { name: '工作区', exact: true }).click();
  await page.getByRole('tab', { name: '依赖', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('连接适配器尚未支持');
  await expect(page.getByRole('button', { name: '运行预检' })).toHaveAttribute('title', 'Redis 连接适配器尚未支持');
});

test('repository import and clone are explicit writes while dirty checkout update stays disabled', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '仓库', exact: true }).click();

  await expect(page.getByRole('button', { name: `更新 ${backendProject.name}` })).toBeDisabled();
  await page.getByRole('button', { name: '导入', exact: true }).click();
  const browserPicker = page.getByRole('dialog').getByRole('button', { name: '选择目录' });
  await expect(browserPicker).toBeDisabled();
  await expect(browserPicker).toHaveAttribute('title', BROWSER_DIRECTORY_PICKER_REASON);
  await expect(page.getByText('浏览器开发模式无法打开系统目录选择器，请手工输入完整路径')).toBeVisible();
  await page.getByLabel('本机目录').fill('D:\\code\\local-api');
  await page.getByRole('dialog').getByRole('button', { name: '导入仓库' }).click();
  await expect(page.getByText('imported-repository')).toBeVisible();

  await page.getByRole('button', { name: '克隆仓库', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('button', { name: '选择目录' })).toBeDisabled();
  await page.getByLabel('Git URL').fill('git@gitlab.example.com:tripguru/local-web.git');
  await page.getByLabel('目录名（可选）').fill('local-web');
  await page.getByRole('dialog').getByRole('button', { name: '开始克隆' }).click();
  await expect(page.getByText('local-web', { exact: true })).toBeVisible();
  await expect.poll(() => api.writes.filter((write) => write.path.startsWith('/api/v1/repositories/')).every((write) => write.token === 'e2e-token')).toBe(true);
});

test('run history exposes stages and logs, supports cancel and retry', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '运行', exact: true }).click();

  await expect(page.getByLabel('运行日志')).toContainText('0 errors, 0 warnings');
  await expect(page.locator('.run-timeline').getByText('启动本地服务').first()).toBeVisible();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect.poll(() => api.writes.some((write) => write.path.endsWith('/cancel'))).toBe(true);

  await page.locator('.run-context-row').nth(1).click();
  await expect(page.getByText('supplier-backend-v2 类型检查失败')).toBeVisible();
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.locator('.run-context-row').first()).toContainText('排队中');
  const retryWrite = api.writes.find((write) => write.path.endsWith('/retry'));
  expect(retryWrite?.token).toBe('e2e-token');
  expect(retryWrite?.body).toEqual({ idempotency_key: expect.stringMatching(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i) });
});

test('native directory selection fills paths and cancellation preserves the current value', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'The native bridge contract is verified once.');
  await page.addInitScript(() => {
    let pickCount = 0;
    Object.defineProperty(window, '__TAURI_INTERNALS__', {
      configurable: true,
      value: {
        invoke: (command: string) => {
          if (command === 'local_api_token') return Promise.resolve('e2e-token');
          if (command === 'plugin:dialog|open') {
            pickCount += 1;
            if (pickCount === 1) return Promise.resolve('D:\\code\\picked-repository');
            if (pickCount === 2) return Promise.resolve(null);
            return Promise.resolve('D:\\worktrees');
          }
          return Promise.reject(new Error(`Unexpected Tauri command: ${command}`));
        },
      },
    });
  });
  await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '仓库', exact: true }).click();

  await page.getByRole('button', { name: '导入', exact: true }).click();
  const importPath = page.getByLabel('本机目录');
  const importPicker = page.getByRole('dialog').getByRole('button', { name: '选择目录' });
  await importPath.fill('D:\\code\\existing');
  await importPicker.focus();
  await page.keyboard.press('Enter');
  await expect(importPath).toHaveValue('D:\\code\\picked-repository');
  await importPicker.focus();
  await page.keyboard.press('Enter');
  await expect(importPath).toHaveValue('D:\\code\\picked-repository');
  await page.getByRole('dialog').getByRole('button', { name: '取消' }).click();

  await page.getByRole('button', { name: '克隆仓库', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: '选择目录' }).click();
  await expect(page.getByLabel('目标父目录')).toHaveValue('D:\\worktrees');
});

test('repository path picker keeps its recovery and controls usable on desktop and mobile', async ({ page }, testInfo) => {
  await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '仓库', exact: true }).click();
  await page.getByRole('button', { name: '导入', exact: true }).click();

  const dialog = page.getByRole('dialog', { name: '导入本机仓库' });
  const pathInput = dialog.getByLabel('本机目录');
  const picker = dialog.getByRole('button', { name: '选择目录' });
  await pathInput.fill('D:\\code\\manual-checkout');
  await expect(pathInput).toHaveValue('D:\\code\\manual-checkout');
  await expect(picker).toBeDisabled();
  await expect(dialog.getByText(BROWSER_DIRECTORY_PICKER_REASON)).toBeVisible();
  await expectNoPageOverflow(page);

  const pickerBox = await picker.boundingBox();
  expect(pickerBox).not.toBeNull();
  expect(pickerBox!.height).toBeGreaterThanOrEqual(testInfo.project.name === 'mobile' ? 44 : 36);
});

test('workspace delete sends the current revision and explains history retention', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Write contract is verified once.');
  const api = await mockFullApi(page, { runs: { runs: [] } });
  await page.goto('/');
  await page.getByRole('button', { name: '工作区', exact: true }).click();
  await page.getByRole('button', { name: '删除工作区' }).click();

  const dialog = page.getByRole('dialog', { name: '删除工作区' });
  await expect(dialog).toContainText('已有历史的工作区必须保留');
  await dialog.getByRole('button', { name: '删除工作区' }).click();
  await expect(page.getByText('没有工作区')).toBeVisible();

  const deleteWrite = api.writes.find((write) => write.method === 'DELETE');
  expect(deleteWrite?.token).toBe('e2e-token');
  expect(deleteWrite?.query).toBe(`?expected_revision=${workspaceFixture.revision}`);
});

test('resources navigate owned processes to Run and reconcile degraded deployments', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Runtime mutation and navigation contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '资源', exact: true }).click();

  await expect(page.getByRole('heading', { name: '应用进程' })).toBeVisible();
  const processRow = page.locator('.process-row').filter({ hasText: runtimeProcessFixture.project_name });
  await expect(processRow).toContainText('“directory with spaces”');
  await processRow.click();
  await expect(page.getByRole('heading', { name: runningRunFixture.workspace_name })).toBeVisible();
  await expect(page.getByRole('button', { name: '运行', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('button', { name: '取消', exact: true })).toBeEnabled();

  await page.getByRole('button', { name: '资源', exact: true }).click();
  const degraded = page.locator('.deployment-row').filter({ hasText: 'DEPLOYMENT_INTERRUPTED' });
  await expect(degraded).toContainText('需恢复');
  await expect(degraded).toContainText('DEPLOYMENT_INTERRUPTED');
  await degraded.getByRole('button', { name: 'Reconcile' }).click();
  const recovered = page.locator('.deployment-row').filter({ hasText: 'previous revision 已恢复且 readiness 通过' });
  await expect(recovered).toContainText('已回滚');
  const reconcileWrite = api.writes.find((write) => write.path === `/api/v1/deployments/${degradedDeploymentFixture.revision_id}/reconcile`);
  expect(reconcileWrite?.token).toBe('e2e-token');

  const filtered = await page.evaluate(async ({ workspaceId, targetId }) => {
    const response = await fetch(`http://127.0.0.1:7421/api/v1/deployments?workspace_id=${workspaceId}&target_id=${targetId}`);
    return response.json() as Promise<{ deployments: { project_id: string }[] }>;
  }, { workspaceId: workspaceFixture.id, targetId: frontendProject.id });
  expect(filtered.deployments).toHaveLength(2);
  expect(filtered.deployments.every((deployment) => deployment.project_id === frontendProject.id)).toBe(true);

  const missing = await page.evaluate(async () => {
    const response = await fetch('http://127.0.0.1:7421/api/v1/deployments/missing-revision/reconcile', { method: 'POST', headers: { 'x-tripguru-local-token': 'e2e-token' } });
    return { status: response.status, body: await response.json() as { detail: { code: string } } };
  });
  expect(missing.status).toBe(404);
  expect(missing.body.detail.code).toBe('DEPLOYMENT_REVISION_NOT_FOUND');
  await expectNoPageOverflow(page);
});

test('managed PostgreSQL uses workspace and Secret metadata for a controlled lifecycle', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Managed middleware write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '资源', exact: true }).click();

  const section = page.locator('.managed-section');
  await expect(section.getByRole('heading', { name: '平台托管 PostgreSQL' })).toBeVisible();
  await expect(section).toContainText('MinIO、Redis 与 Elasticsearch');
  await expect(section).not.toContainText('fixture-postgres-password');

  const failedRow = section.locator('.managed-row').filter({ hasText: failedManagedPostgresFixture.name });
  await expect(failedRow).toContainText('MANAGED_HOST_PORT_IN_USE');
  await failedRow.getByRole('button', { name: 'Reconcile' }).click();
  await expect(failedRow).toContainText('运行中');
  expect(api.writes.find((write) => write.path === `/api/v1/managed-middleware/${failedManagedPostgresFixture.id}/reconcile`)).toMatchObject({ method: 'POST', token: 'e2e-token', body: null });

  await section.getByRole('button', { name: '托管 PostgreSQL' }).click();
  const createDialog = page.getByRole('dialog', { name: '托管 PostgreSQL' });
  await expect(createDialog.getByLabel('托管 PostgreSQL 所属工作区')).toHaveValue(workspaceFixture.id);
  await expect(createDialog.getByLabel('PostgreSQL 密码 Secret')).toHaveValue(postgresSecret.id);
  await createDialog.getByLabel('PostgreSQL 宿主端口').fill('56432');
  await createDialog.getByLabel('PostgreSQL 用户名').fill('supplier_local');
  await createDialog.getByLabel('PostgreSQL 数据库名').fill('supplier_local');
  await createDialog.getByRole('button', { name: '创建并配置' }).click();
  await expect(section).toContainText('127.0.0.1:56432');

  const provisionWrite = api.writes.find((write) => write.method === 'POST' && write.path === '/api/v1/managed-middleware');
  expect(provisionWrite).toEqual({
    path: '/api/v1/managed-middleware', query: '', method: 'POST', token: 'e2e-token',
    body: { workspace_id: workspaceFixture.id, kind: 'postgres', host_port: 56432, username: 'supplier_local', database: 'supplier_local', password_secret_ref: postgresSecret.id },
  });
  const created = api.getManagedResources().find((resource) => resource.intent?.host_port === 56432);
  expect(created).toBeDefined();
  expect(JSON.stringify(created)).not.toContain('fixture-postgres-password');
  expect(created).not.toHaveProperty('value');

  await section.getByRole('button', { name: `删除 ${activeManagedPostgresFixture.name}`, exact: true }).click();
  const deleteDialog = page.getByRole('dialog', { name: '删除托管 PostgreSQL' });
  const deleteButton = deleteDialog.getByRole('button', { name: '删除 PostgreSQL' });
  await expect(deleteButton).toBeDisabled();
  await deleteDialog.getByLabel('输入资源名确认删除').fill('wrong-name');
  await expect(deleteButton).toBeDisabled();
  await deleteDialog.getByLabel('输入资源名确认删除').fill(activeManagedPostgresFixture.name);
  await deleteButton.click();
  await expect(section.locator('.managed-row strong').getByText(activeManagedPostgresFixture.name, { exact: true })).toHaveCount(0);
  expect(api.writes.find((write) => write.path === `/api/v1/managed-middleware/${activeManagedPostgresFixture.id}`)).toMatchObject({ method: 'DELETE', token: 'e2e-token', body: null });

  const filtered = await page.evaluate(async (workspaceId) => {
    const response = await fetch(`http://127.0.0.1:7421/api/v1/managed-middleware?workspace_id=${workspaceId}`);
    return response.json() as Promise<{ resources: { workspace_id: string }[] }>;
  }, workspaceFixture.id);
  expect(filtered.resources.every((resource) => resource.workspace_id === workspaceFixture.id)).toBe(true);
});

test('managed PostgreSQL form stays usable at desktop and 390px', async ({ page }, testInfo) => {
  await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '资源', exact: true }).click();
  const section = page.locator('.managed-section');
  await expect(section.locator('.managed-row').first()).toBeVisible();
  await section.getByRole('button', { name: '托管 PostgreSQL' }).click();
  const dialog = page.getByRole('dialog', { name: '托管 PostgreSQL' });
  await expect(dialog.getByLabel('PostgreSQL 密码 Secret')).toBeVisible();
  await expect(dialog.getByText('响应、缓存与界面均不会包含原始值。')).toBeVisible();
  await expectNoPageOverflow(page);
  if (testInfo.project.name === 'mobile') {
    const createButton = dialog.getByRole('button', { name: '创建并配置' });
    const secretSelect = dialog.getByLabel('PostgreSQL 密码 Secret');
    expect((await createButton.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    expect((await secretSelect.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  }
});

test('cleanup preview keeps MinIO and the last healthy middleware protected', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Destructive confirmation flow is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: '资源', exact: true }).click();
  await page.getByRole('button', { name: '清理预览' }).click();

  const dialog = page.getByRole('dialog', { name: '清理预览' });
  await expect(dialog).toContainText('MinIO 始终受保护');
  await expect(dialog).toContainText('PostgreSQL 至少保留 1 个健康实例');
  const eligible = dialog.locator('.cleanup-row').filter({ hasText: managedRedisResource.name });
  await eligible.getByRole('checkbox').check();
  await dialog.getByText('我已核对所选资源').click();
  await dialog.getByRole('button', { name: /清理 1 项/ }).click();
  await expect(page.getByRole('dialog', { name: '清理结果' })).toContainText('已删除 1 项资源');
  await expect.poll(() => api.writes.some((write) => write.path === `/api/v1/runtime/cleanup-previews/${cleanupPreviewFixture.id}/apply` && write.token === 'e2e-token')).toBe(true);
});

test('cleanup renders removed, skipped and failed outcomes on desktop and mobile', async ({ page }) => {
  const api = await mockFullApi(page, { cleanupPreview: mixedCleanupPreview, cleanupResults: mixedCleanupResults });
  await page.goto('/');
  await page.getByRole('button', { name: '资源', exact: true }).click();
  await page.getByRole('button', { name: '清理预览' }).click();

  const previewDialog = page.getByRole('dialog', { name: '清理预览' });
  const eligibleCheckboxes = previewDialog.locator('.cleanup-row input:not(:disabled)');
  await expect(eligibleCheckboxes).toHaveCount(3);
  for (const checkbox of await eligibleCheckboxes.all()) await checkbox.check();
  await previewDialog.getByText('我已核对所选资源').click();
  await previewDialog.getByRole('button', { name: /清理 3 项/ }).click();

  const resultDialog = page.getByRole('dialog', { name: '清理结果' });
  await expect(resultDialog).toContainText('已删除 1 项 · 已跳过 1 项 · 失败 1 项');
  await expect(resultDialog.locator('.cleanup-result').filter({ hasText: cleanupRemovedResource.name })).toContainText('已删除');
  await expect(resultDialog.locator('.cleanup-result').filter({ hasText: cleanupSkippedResource.name })).toContainText('已跳过');
  await expect(resultDialog.locator('.cleanup-result').filter({ hasText: cleanupFailedResource.name })).toContainText('删除失败');
  const applyWrite = api.writes.find((write) => write.path === `/api/v1/runtime/cleanup-previews/${mixedCleanupPreview.id}/apply`);
  expect(applyWrite?.body).toEqual({ preview_id: mixedCleanupPreview.id, resource_ids: mixedCleanupResults.map((item) => item.resource_id) });
  await expectNoPageOverflow(page);
});

test('workspace blocks preflight when Docker is unavailable and exposes recovery', async ({ page }) => {
  await mockFullApi(page, { runtime: { generated_at: '2026-08-17T12:00:00Z', docker_available: false, resources: [], error_code: 'DOCKER_UNAVAILABLE', recovery: '启动 Docker Desktop 后重试' } });
  await page.goto('/');
  await page.getByRole('button', { name: '工作区', exact: true }).click();

  await expect(page.getByRole('alert').filter({ hasText: 'Docker 不可用' })).toContainText('启动 Docker Desktop 后重试');
  await expect(page.getByRole('button', { name: '运行预检' })).toHaveAttribute('title', '启动 Docker Desktop 后重试');
  await expect(page.getByRole('button', { name: '运行预检' })).toBeDisabled();
});
