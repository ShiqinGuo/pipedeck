import { expect, test } from '@playwright/test';

import { mockFullApi, postgresSecret, workspaceFixture } from './api-fixtures';
import type { components } from '../src/api/schema';

type WorkspaceRecord = components['schemas']['WorkspaceRecord'];

test('workspace list opens the editor and shows saved revision', async ({ page }) => {
  await mockFullApi(page);
  await page.goto('/workspaces');

  await page.getByTestId('workspace-row').getByRole('link').click();
  await expect(page).toHaveURL(/\/workspaces\/workspace-supplier/);
  await expect(page.getByLabel('工作区名称')).toHaveValue(workspaceFixture.name);
  await expect(page.getByText(`rev ${workspaceFixture.revision}`)).toBeVisible();
  await expect(page.getByTestId('service-row')).toHaveCount(1);
});

test('workspace edits save with expected_revision and preflight renders typed plan then creates a run', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'The full edit flow runs once.');
  const api = await mockFullApi(page);
  await page.goto('/workspaces');
  await page.getByTestId('workspace-row').getByRole('link').click();

  await page.getByLabel('工作区名称').fill('Supplier 本地验收');
  // PostgreSQL 数据库字段在服务 inspector 的“依赖” tab 中
  await page.getByRole('tab', { name: '依赖', exact: true }).click();
  await page.getByLabel('PostgreSQL 数据库').fill('supplier_local');

  // 保存:revision 预检随请求发送
  const save = page.getByRole('button', { name: '保存', exact: true });
  await expect(save).toBeEnabled();
  await save.click();
  await expect.poll(() => api.writes.some((write) => write.method === 'PUT' && write.path.endsWith(`/workspaces/${workspaceFixture.id}`))).toBe(true);
  const saveWrite = api.writes.find((write) => write.method === 'PUT' && write.path.endsWith(`/workspaces/${workspaceFixture.id}`));
  expect(saveWrite?.token).toBe('e2e-token');
  const payload = saveWrite?.body as WorkspaceRecord & { expected_revision: number };
  expect(payload.expected_revision).toBe(workspaceFixture.revision);
  const backend = payload.services.find((service) => service.project_id === 'project-backend');
  expect(backend?.connection_profiles).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ kind: 'postgres', database: 'supplier_local', secret_ref: postgresSecret.id }),
      expect.objectContaining({ kind: 'minio', access_key_secret_ref: 'secret-minio-access', secret_key_secret_ref: 'secret-minio-key' }),
    ]),
  );

  // 预检 → 计划弹层(先看再跑:命令与脱敏连接注入)
  const preflight = page.getByRole('button', { name: '运行预检' });
  await expect(preflight).toBeEnabled();
  await preflight.click();
  const dialog = page.getByTestId('plan-dialog');
  await expect(dialog).toContainText('运行计划');
  await expect(dialog.locator('.plan-argv').first()).toContainText('uv');
  await expect(dialog).toContainText('连接注入预览');
  await expect(dialog).toContainText('postgresql+asyncpg://supplier:***@127.0.0.1:5432/supplier');
  await expect(dialog).not.toContainText('fixture-postgres-password');

  // 运行 → 跳转运行详情
  await dialog.getByRole('button', { name: '运行', exact: true }).click();
  await expect(page).toHaveURL(/\/runs\/run-created-001/);
  await expect.poll(() => api.writes.some((write) => write.path === '/api/v1/runs' && write.token === 'e2e-token')).toBe(true);
});

test('environment secret source is saved as metadata reference', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Environment write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/workspaces');
  await page.getByTestId('workspace-row').getByRole('link').click();

  await page.getByRole('tab', { name: '环境', exact: true }).click();
  await page.getByRole('button', { name: '添加变量' }).click();
  await page.getByLabel('环境变量 2 名称').fill('APP_SECRET_TOKEN');
  await page.getByLabel('APP_SECRET_TOKEN 来源').selectOption('secret-store');
  await page.getByLabel('APP_SECRET_TOKEN Secret', { exact: true }).selectOption('secret-disposable');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => api.writes.some((write) => write.method === 'PUT' && write.path.includes('/workspaces/'))).toBe(true);
  const payload = api.writes.find((write) => write.method === 'PUT')?.body as WorkspaceRecord;
  const backend = payload.services.find((service) => service.project_id === 'project-backend');
  expect(backend?.environment).toContainEqual({ name: 'APP_SECRET_TOKEN', source: 'secret-store', value: null, reference: 'secret-disposable' });
});

test('target editor preserves argv token boundaries for commands with spaces', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Argv token contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/workspaces');
  await page.getByTestId('workspace-row').getByRole('link').click();

  await page.getByRole('tab', { name: '命令', exact: true }).click();
  const argvSection = page.locator('section', { has: page.getByLabel('类型检查 参数 3', { exact: true }) }).first();
  await argvSection.getByRole('button', { name: '添加 token' }).click();
  await page.getByLabel('类型检查 参数 4', { exact: true }).fill('--min-score 0.9');

  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => api.writes.some((write) => write.method === 'PUT')).toBe(true);
  const payload = api.writes.find((write) => write.method === 'PUT')?.body as WorkspaceRecord;
  const backend = payload.services.find((service) => service.project_id === 'project-backend');
  // token 边界保留:'--min-score 0.9' 是单个 argv token,不是两个
  expect(backend?.commands.find((command) => command.id === 'typecheck')?.argv).toEqual(['uv', 'run', 'pyright', '--min-score 0.9']);
});

test('docker unavailability blocks preflight with recovery instead of failing silently', async ({ page }) => {
  const { dockerDownFixture } = await import('./api-fixtures');
  await mockFullApi(page, { runtime: dockerDownFixture });
  await page.goto('/workspaces');
  await page.getByTestId('workspace-row').getByRole('link').click();

  const preflight = page.getByRole('button', { name: '运行预检' });
  await expect(preflight).toBeDisabled();
  await expect(preflight).toHaveAttribute('title', '启动 Docker Desktop 后重试');
});

test('workspace delete sends the current revision', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Write contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/workspaces');
  await page.getByTestId('workspace-row').getByRole('link').click();

  await page.getByRole('button', { name: '删除', exact: true }).click();
  const dialog = page.getByTestId('delete-workspace-dialog');
  await expect(dialog).toContainText('已有历史的工作区必须保留');
  await dialog.getByRole('button', { name: '删除工作区' }).click();
  await expect(page).toHaveURL(/\/workspaces$/);
  const deleteWrite = api.writes.find((write) => write.method === 'DELETE' && write.path.includes('/workspaces/'));
  expect(deleteWrite?.token).toBe('e2e-token');
  expect(deleteWrite?.query).toBe(`?expected_revision=${workspaceFixture.revision}`);
});

test('environments list, create with ref and delete after preview', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Environment lifecycle contract is verified once.');
  const api = await mockFullApi(page);
  await page.goto('/workspaces');
  await page.getByTestId('workspace-row').getByRole('link').click();

  const section = page.getByTestId('environments-section');
  await expect(section.getByTestId('environment-card').filter({ hasText: 'main' })).toContainText('worktrees');

  // 创建:创建后 ref 不可改(卡片只展示 ref)
  await section.getByRole('button', { name: '创建 Environment' }).click();
  const createDialog = page.getByTestId('environment-create-dialog');
  await createDialog.getByLabel('目标 ref(branch / tag)').fill('release/2.0');
  await createDialog.getByRole('button', { name: '创建 Environment' }).click();
  await expect(section.getByTestId('environment-card').filter({ hasText: 'release/2.0' })).toBeVisible();
  const createWrite = api.writes.find((write) => write.method === 'POST' && write.path.includes('/environments'));
  expect(createWrite).toMatchObject({ token: 'e2e-token', body: { ref: 'release/2.0' } });

  // 删除:预览清单 + 确认
  await section.getByRole('button', { name: '删除 Environment main' }).click();
  const deleteDialog = page.getByTestId('environment-delete-dialog');
  await expect(deleteDialog).toContainText('git worktree remove');
  await expect(deleteDialog.getByTestId('environment-delete-preview')).toContainText('worktree:D:\\pipedeck\\worktrees\\workspace-supplier\\main');
  await deleteDialog.getByRole('button', { name: '删除 Environment' }).click();
  await expect(section.getByTestId('environment-card').filter({ hasText: 'main' })).toHaveCount(0);
});

test('environment deletion blocked on dirty worktree exposes recovery', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Blocking contract is verified once.');
  await mockFullApi(page, {
    environments: [
      {
        id: 'environment-dirty',
        workspace_id: workspaceFixture.id,
        repository_id: 'repository-backend',
        ref: 'dirty-branch',
        worktree_path: 'D:\\pipedeck\\worktrees\\workspace-supplier\\dirty-branch',
        created_at: '2026-08-17T12:00:00Z',
        updated_at: '2026-08-17T12:00:00Z',
      },
    ],
  });
  await page.goto('/workspaces');
  await page.getByTestId('workspace-row').getByRole('link').click();

  const section = page.getByTestId('environments-section');
  await section.getByRole('button', { name: '删除 Environment dirty-branch' }).click();
  await page.getByTestId('environment-delete-dialog').getByRole('button', { name: '删除 Environment' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '操作未完成' })).toContainText('提交或暂存 worktree 变更后重试删除');
});

test('new workspace requires explicit secret selection before creation', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'Workspace construction contract is verified once.');
  const api = await mockFullApi(page, { workspaces: { workspaces: [] } });
  await page.goto('/workspaces');
  await page.getByRole('button', { name: '新建工作区' }).click();

  const dialog = page.getByTestId('create-workspace-dialog');
  await dialog.getByLabel('工作区名称').fill('Profile defaults');
  await dialog.getByTestId('project-selection-row').getByRole('checkbox').click();
  const createButton = dialog.getByRole('button', { name: '创建工作区' });
  // 未选择 Secret 前创建被禁用并解释原因
  await expect(createButton).toBeDisabled();
  await expect(createButton).toHaveAttribute('title', /需要选择/);
  await dialog.getByLabel('project-backend PostgreSQL 密码 Secret').selectOption(postgresSecret.id);
  await dialog.getByLabel('project-backend MinIO Access Key Secret').selectOption('secret-minio-access');
  await dialog.getByLabel('project-backend MinIO Secret Key Secret').selectOption('secret-minio-key');
  await createButton.click();

  await expect(page).toHaveURL(/\/workspaces\/workspace-created/);
  const createWrite = api.writes.find((write) => write.method === 'POST' && write.path === '/api/v1/workspaces');
  const service = (createWrite?.body as WorkspaceRecord).services[0];
  expect(service?.connection_profiles).toEqual([
    { kind: 'postgres', env_var: 'DATABASE_URL', scheme: 'postgresql', username: 'postgres', database: 'postgres', secret_ref: postgresSecret.id },
    { kind: 'minio', endpoint_env: 'S3_ENDPOINT', access_key_env: 'S3_ACCESS_KEY', secret_key_env: 'S3_SECRET_KEY', bucket_env: 'S3_BUCKET', bucket: 'local-assets', access_key_secret_ref: 'secret-minio-access', secret_key_secret_ref: 'secret-minio-key', secure: false },
  ]);
});
