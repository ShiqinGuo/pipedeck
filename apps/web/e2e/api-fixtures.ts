import type { Page } from '@playwright/test';

import type { components } from '../src/api/schema';

type Schemas = components['schemas'];
type RepositoryRecord = Schemas['RepositoryRecord'];
type RunRecord = Schemas['RunRecord'];
type SecretMetadata = Schemas['SecretMetadata'];
type WorkspaceRecord = Schemas['WorkspaceRecord'];
type DeploymentRevisionView = Schemas['DeploymentRevisionView'];
type ManagedResourceRecord = Schemas['ManagedResourceRecord'];
type EnvironmentRecord = Schemas['EnvironmentRecord'];
type CleanupPreviewResponse = Schemas['CleanupPreviewResponse'];
type CleanupResultItem = Schemas['CleanupResultItem'];
type GitlabPipelinePreview = Schemas['GitlabPipelinePreview'];
type WorkspacePlanResponse = Schemas['WorkspacePlanResponse'];
type RuntimeResource = Schemas['RuntimeResource'];
type RuntimeProcessRecord = Schemas['RuntimeProcessRecord'];

const generatedAt = '2026-08-17T12:00:00Z';

export const sessionFixture = {
  write_enabled: true,
  authentication: 'tauri-command-or-explicit-environment',
  version: '0.1.0',
} satisfies Schemas['SessionResponse'];

export const readOnlySessionFixture = {
  write_enabled: false,
  authentication: 'tauri-command-or-explicit-environment',
  version: '0.1.0',
} satisfies Schemas['SessionResponse'];

export const backendProject = {
  id: 'project-backend', name: 'supplier-backend-v2', path: 'D:\\code\\supplier-backend-v2',
  kind: 'python-uv', branch: 'feat/local-control', dirty: true,
  commands: [
    { id: 'typecheck', label: '类型检查', argv: ['uv', 'run', 'pyright'], kind: 'quality', long_running: false },
    { id: 'start', label: '启动服务', argv: ['uv', 'run', 'uvicorn', 'app.main:app', '--reload'], kind: 'start', long_running: true },
  ],
  requirements: ['postgres', 'minio'],
  warnings: ['工作区包含未提交修改'],
  container_capabilities: { dockerfile: 'Dockerfile', compose_files: [] },
} satisfies Schemas['ProjectSummary'];

export const catalogFixture = {
  generated_at: generatedAt,
  roots: ['D:\\code'],
  projects: [backendProject],
  errors: [],
} satisfies Schemas['CatalogResponse'];

export const postgresResource = {
  id: 'resource-postgres', name: 'pipedeck-postgres', kind: 'postgres', image: 'postgres:18', state: 'running',
  status_text: 'Up 3 hours (healthy)', health: 'healthy', managed: false, protected: true, ports: '127.0.0.1:5432->5432/tcp',
  endpoints: [{ protocol: 'tcp', container_port: 5432, host: '127.0.0.1', host_port: 5432 }], owner_workspace_id: null,
} satisfies RuntimeResource;
export const minioResource = {
  id: 'resource-minio', name: 'pipedeck-minio', kind: 'minio', image: 'minio/minio:latest', state: 'running',
  status_text: 'Up 3 hours (healthy)', health: 'healthy', managed: false, protected: true, ports: '127.0.0.1:9000->9000/tcp',
  endpoints: [{ protocol: 'tcp', container_port: 9000, host: '127.0.0.1', host_port: 9000 }], owner_workspace_id: null,
} satisfies RuntimeResource;
export const managedRedisResource = {
  id: 'resource-redis-old', name: 'pipedeck-old-redis', kind: 'redis', image: 'redis:8', state: 'exited',
  status_text: 'Exited 2 days ago', health: 'stopped', managed: true, protected: false, ports: '', endpoints: [], owner_workspace_id: null,
} satisfies RuntimeResource;

export const runtimeFixture = {
  generated_at: generatedAt,
  docker_available: true,
  resources: [postgresResource, minioResource, managedRedisResource],
  error_code: null,
  recovery: null,
} satisfies Schemas['RuntimeResponse'];

export const dockerDownFixture = {
  generated_at: generatedAt,
  docker_available: false,
  resources: [],
  error_code: 'DOCKER_UNAVAILABLE',
  recovery: '启动 Docker Desktop 后重试',
} satisfies Schemas['RuntimeResponse'];

export const overviewFixture = {
  generated_at: generatedAt,
  api_status: 'ready',
  docker_available: true,
  project_count: 1,
  dirty_project_count: 1,
  middleware_count: 3,
  protected_kinds: ['postgres', 'minio'],
} satisfies Schemas['OverviewResponse'];

export const repositoryFixture = {
  id: 'repository-backend', name: 'supplier-backend-v2', path: 'D:\\code\\supplier-backend-v2',
  pipeline_file: '.gitlab-ci.yml',
  origin_url: 'git@gitlab.example.com:pipedeck/supplier-backend-v2.git', branch: 'feat/local-control',
  head_sha: '0a1b2c3d4e5f67890123', upstream: 'origin/feat/local-control', dirty: true,
  created_at: generatedAt, updated_at: generatedAt,
} satisfies RepositoryRecord;

export const repositoryListFixture = { repositories: [repositoryFixture] } satisfies Schemas['RepositoryListResponse'];

/** 管道预览:健康管道 */
export const pipelinePreviewFixture = {
  repository_id: repositoryFixture.id,
  ready: true,
  stages: ['build', 'test', 'deploy'],
  jobs: [
    {
      name: 'build-app', stage: 'build', script: ['pip install -e .'], before_script: [], after_script: [],
      image: { name: 'python:3.12', entrypoint: [] }, variables: { PIP_CACHE_DIR: '.cache/pip' }, needs: null,
      artifacts: [], dotenv_reports: [], allow_failure: false, when: 'on_success', included: true, unsupported: [],
    },
    {
      name: 'lint', stage: 'test', script: ['ruff check .'], before_script: [], after_script: [],
      image: { name: 'python:3.12', entrypoint: [] }, variables: {}, needs: [{ job: 'build-app', artifacts: true, optional: false }],
      artifacts: [], dotenv_reports: [], allow_failure: false, when: 'on_success', included: true, unsupported: [],
    },
    {
      name: 'deploy-staging', stage: 'deploy', script: ['make deploy'], before_script: [], after_script: [],
      image: null, variables: {}, needs: [{ job: 'lint', artifacts: true, optional: false }],
      artifacts: [], dotenv_reports: [], allow_failure: false, when: 'manual', included: true, unsupported: [],
    },
    {
      name: 'nightly-cleanup', stage: 'cleanup', script: ['make clean'], before_script: [], after_script: [],
      image: null, variables: {}, needs: null, artifacts: [], dotenv_reports: [], allow_failure: true, when: 'never', included: true, unsupported: [],
    },
  ],
  global_variables: { PIPELINE_LANG: 'python', MAX_JOBS: '4' },
  blockers: [],
  warnings: [{ code: 'DIRTY_WORKTREE', title: '仓库有未提交修改', detail: '计划固定当前源码指纹', recovery: '确认这些修改是本次本地验证的一部分' }],
  config_fingerprint: 'c'.repeat(64),
  source_fingerprint: 'd'.repeat(64),
  generated_at: generatedAt,
} satisfies GitlabPipelinePreview;

/** 管道预览:解析失败(阻断) */
export const blockedPipelinePreviewFixture = {
  ...pipelinePreviewFixture,
  ready: false,
  jobs: [],
  blockers: [
    {
      code: 'PIPELINE_INCLUDE_UNAVAILABLE',
      title: 'include 文件不可用',
      detail: '远程 include 无法拉取且缓存不存在',
      recovery: '检查网络后点击“强制刷新 include”,或修正 .gitlab-ci.yml',
    },
  ],
} satisfies GitlabPipelinePreview;

export const pipelinePlanFixture = {
  generated_at: generatedAt,
  ready: true,
  mode: 'development',
  projects: [],
  steps: [
    {
      id: 'build-app', kind: 'build', title: '运行 build-app', detail: 'job 容器执行(python:3.12)',
      commands: [
        {
          project_id: 'pipeline', project_name: repositoryFixture.name, command_id: 'build-app', label: 'build-app',
          cwd: repositoryFixture.path, argv: ['docker', 'run', 'python:3.12', 'pip', 'install', '-e', '.'],
          environment: [{ name: 'PIP_CACHE_DIR', value: '.cache/pip' }], long_running: false,
        },
      ],
      deployments: [
        {
          revision_id: 'deployment-revision-pipeline-r1', workspace_id: 'workspace-supplier', project_id: 'project-frontend',
          workspace_revision: 3, source_fingerprint: '1'.repeat(64), target_config_fingerprint: '2'.repeat(64),
          checkout_path: 'D:\\code\\supplier-admin-frontend', frozen_compose_path: 'D:\\pipedeck\\compose\\pipeline\\docker-compose.frozen.yml',
          services: ['supplier-admin'], immutable_images: ['tripguru.local/supplier-admin@sha256:abcdef0123456789'],
          wait_timeout_seconds: 120, probe: { kind: 'http', url: 'http://127.0.0.1:3000/health', timeout_seconds: 30 },
          environment_spec: { environment: [], connection_profiles: [], bindings: [] },
        },
      ],
      pipeline_job: null,
    },
    {
      id: 'lint', kind: 'quality', title: '运行 lint', detail: 'job 容器执行(python:3.12)',
      commands: [
        {
          project_id: 'pipeline', project_name: repositoryFixture.name, command_id: 'lint', label: 'lint',
          cwd: repositoryFixture.path, argv: ['docker', 'run', 'python:3.12', 'ruff', 'check', '.'],
          environment: [], long_running: false,
        },
      ],
      deployments: [], pipeline_job: null,
    },
  ],
  blockers: [],
  warnings: pipelinePreviewFixture.warnings,
  connection_mappings: [],
  plan_id: 'plan-pipeline-001',
  workspace_id: null,
  workspace_revision: null,
  config_fingerprint: 'c'.repeat(64),
  source_fingerprint: 'd'.repeat(64),
} satisfies WorkspacePlanResponse;

export const postgresSecret = { id: 'secret-pg-password', name: 'Supplier PostgreSQL password', present: true, version: 1, created_at: generatedAt, updated_at: generatedAt } satisfies SecretMetadata;
export const minioAccessSecret = { id: 'secret-minio-access', name: 'Supplier MinIO access key', present: true, version: 1, created_at: generatedAt, updated_at: generatedAt } satisfies SecretMetadata;
export const minioKeySecret = { id: 'secret-minio-key', name: 'Supplier MinIO secret key', present: true, version: 1, created_at: generatedAt, updated_at: generatedAt } satisfies SecretMetadata;
export const missingSecret = { id: 'secret-missing-value', name: 'Credential metadata only', present: false, version: 2, created_at: generatedAt, updated_at: generatedAt } satisfies SecretMetadata;
export const disposableSecret = { id: 'secret-disposable', name: 'Disposable secret', present: true, version: 1, created_at: generatedAt, updated_at: generatedAt } satisfies SecretMetadata;
export const secretListFixture = { secrets: [postgresSecret, minioAccessSecret, minioKeySecret, missingSecret, disposableSecret] } satisfies Schemas['SecretListResponse'];

export const workspaceFixture = {
  id: 'workspace-supplier', name: 'Supplier 本地集成', mode: 'development', revision: 3,
  services: [
    {
      project_id: 'project-backend',
      commands: [
        { id: 'typecheck', label: '类型检查', kind: 'quality', argv: ['uv', 'run', 'pyright'], long_running: false },
        { id: 'start', label: '启动服务', kind: 'start', argv: ['uv', 'run', 'uvicorn', 'app.main:app', '--reload'], long_running: true },
      ],
      environment: [{ name: 'SUPPLIER_DATABASE_URL', source: 'host-env', value: null, reference: 'LOCAL_SUPPLIER_DATABASE_URL' }],
      connection_profiles: [
        { kind: 'postgres', env_var: 'DATABASE_URL', scheme: 'postgresql+asyncpg', username: 'supplier', database: 'supplier', secret_ref: postgresSecret.id },
        { kind: 'minio', endpoint_env: 'S3_ENDPOINT', access_key_env: 'S3_ACCESS_KEY', secret_key_env: 'S3_SECRET_KEY', bucket_env: 'S3_BUCKET', bucket: 'local-assets', access_key_secret_ref: minioAccessSecret.id, secret_key_secret_ref: minioKeySecret.id, secure: false },
      ],
      execution_target: {
        kind: 'host',
        endpoints: [{ name: 'http', protocol: 'tcp', host_port: 8000, injection: { kind: 'argument', option: '--port' } }],
        readiness: { kind: 'http', endpoint: 'http', path: '/health' },
        readiness_timeout: 60,
        stop_timeout: 10,
      },
    },
  ],
  bindings: [
    { kind: 'postgres', resource_id: postgresResource.id },
    { kind: 'minio', resource_id: minioResource.id },
  ],
  created_at: generatedAt,
  updated_at: generatedAt,
} satisfies WorkspaceRecord;

export const workspaceListFixture = { workspaces: [workspaceFixture] } satisfies Schemas['WorkspaceListResponse'];

export const environmentFixture = {
  id: 'environment-main',
  workspace_id: workspaceFixture.id,
  repository_id: repositoryFixture.id,
  ref: 'main',
  worktree_path: 'D:\\pipedeck\\worktrees\\workspace-supplier\\main',
  created_at: generatedAt,
  updated_at: generatedAt,
} satisfies EnvironmentRecord;
export const environmentListFixture = { environments: [environmentFixture] } satisfies Schemas['EnvironmentListResponse'];

export const workspacePlanFixture = {
  generated_at: generatedAt,
  ready: true,
  mode: 'development',
  projects: [],
  steps: [
    {
      id: 'quality', kind: 'quality', title: '运行质量门禁', detail: '在各自 checkout 中运行类型检查',
      commands: [
        {
          project_id: 'project-backend', project_name: 'supplier-backend-v2', command_id: 'typecheck', label: '类型检查',
          cwd: 'D:\\code\\supplier-backend-v2', argv: ['uv', 'run', 'pyright'], environment: [], long_running: false,
        },
      ],
      deployments: [], pipeline_job: null,
    },
    {
      id: 'start', kind: 'start', title: '启动本地服务', detail: '使用已保存的命令、环境变量和 target 启动',
      commands: [
        {
          project_id: 'project-backend', project_name: 'supplier-backend-v2', command_id: 'start', label: '启动服务',
          cwd: 'D:\\code\\supplier-backend-v2', argv: ['uv', 'run', 'uvicorn', 'app.main:app', '--reload'], environment: [], long_running: true,
        },
      ],
      deployments: [], pipeline_job: null,
    },
  ],
  blockers: [],
  warnings: [{ code: 'DIRTY_WORKTREE', title: '后端有未提交修改', detail: '计划固定当前源码指纹', recovery: '确认这些修改是本次本地验证的一部分' }],
  connection_mappings: [
    {
      project_id: 'project-backend', kind: 'postgres', resource_id: postgresResource.id, resource_name: postgresResource.name,
      outputs: [
        { name: 'DATABASE_URL', redacted_value: 'postgresql+asyncpg://supplier:***@127.0.0.1:5432/supplier', sensitive: true },
      ],
    },
    {
      project_id: 'project-backend', kind: 'minio', resource_id: minioResource.id, resource_name: minioResource.name,
      outputs: [
        { name: 'S3_ENDPOINT', redacted_value: 'http://127.0.0.1:9000', sensitive: false },
        { name: 'S3_ACCESS_KEY', redacted_value: '***', sensitive: true },
        { name: 'S3_SECRET_KEY', redacted_value: '***', sensitive: true },
        { name: 'S3_BUCKET', redacted_value: 'local-assets', sensitive: false },
      ],
    },
  ],
  plan_id: 'plan-supplier-r3',
  workspace_id: workspaceFixture.id,
  workspace_revision: 3,
  config_fingerprint: '2'.repeat(64),
  source_fingerprint: '1'.repeat(64),
} satisfies WorkspacePlanResponse;

export const runningRunFixture = {
  id: 'run-active-001', workspace_id: workspaceFixture.id, workspace_name: workspaceFixture.name, workspace_revision: 3,
  plan_id: workspacePlanFixture.plan_id ?? 'plan-supplier-r3', mode: 'development', status: 'running', current_step: 'start',
  config_fingerprint: '2'.repeat(64), source_fingerprint: '1'.repeat(64), retry_of: null,
  created_at: generatedAt, started_at: generatedAt, finished_at: null, failure_code: null, failure_detail: null,
} satisfies RunRecord;

export const failedRunFixture = {
  ...runningRunFixture,
  id: 'run-failed-001', status: 'failed', current_step: 'quality', finished_at: '2026-08-17T12:01:10Z',
  failure_code: 'COMMAND_FAILED', failure_detail: 'supplier-backend-v2 类型检查失败',
} satisfies RunRecord;

export const runListFixture = { runs: [runningRunFixture, failedRunFixture] } satisfies Schemas['RunListResponse'];

export const runtimeProcessFixture = {
  id: 'process-backend-start', pid: 43120, run_id: runningRunFixture.id, project_id: 'project-backend', project_name: 'supplier-backend-v2',
  command_id: 'start', label: '启动服务', cwd: 'D:\\code\\supplier-backend-v2',
  argv: ['uv', 'run', 'uvicorn', 'app.main:app', '--app-dir', 'directory with spaces'], long_running: true, started_at: generatedAt,
} satisfies RuntimeProcessRecord;
export const runtimeProcessListFixture = { generated_at: generatedAt, processes: [runtimeProcessFixture] } satisfies Schemas['RuntimeProcessListResponse'];

export const eventsFixture = {
  events: [
    { sequence: 1, run_id: runningRunFixture.id, created_at: generatedAt, kind: 'status', step_id: null, project_id: null, message: '运行已启动' },
    { sequence: 2, run_id: runningRunFixture.id, created_at: generatedAt, kind: 'stage', step_id: 'quality', project_id: null, message: '开始运行质量门禁' },
    { sequence: 3, run_id: runningRunFixture.id, created_at: generatedAt, kind: 'stdout', step_id: 'quality', project_id: 'project-backend', message: '0 errors, 0 warnings' },
    { sequence: 4, run_id: runningRunFixture.id, created_at: generatedAt, kind: 'stage', step_id: 'start', project_id: null, message: '启动本地服务' },
    { sequence: 5, run_id: runningRunFixture.id, created_at: generatedAt, kind: 'system', step_id: 'start', project_id: 'project-backend', message: 'Local: http://127.0.0.1:8000/' },
  ],
  next_after: 5,
} satisfies Schemas['RunEventListResponse'];

export const activeDeploymentFixture = {
  revision_id: 'deployment-revision-frontend-r3', workspace_id: workspaceFixture.id, project_id: 'project-frontend', workspace_revision: 3,
  project_name: 'supplier-admin-frontend', previous_revision_id: 'deployment-revision-frontend-r2', status: 'active',
  source_fingerprint: '1'.repeat(64), target_config_fingerprint: '2'.repeat(64),
  services: ['supplier-admin'], immutable_images: ['tripguru.local/supplier-admin@sha256:abcdef0123456789'],
  created_at: generatedAt, updated_at: generatedAt, failure_code: null, failure_detail: null, recovery_detail: null,
} satisfies DeploymentRevisionView;

export const degradedDeploymentFixture = {
  ...activeDeploymentFixture,
  revision_id: 'deployment-revision-frontend-degraded',
  previous_revision_id: activeDeploymentFixture.revision_id,
  status: 'degraded',
  failure_code: 'DEPLOYMENT_INTERRUPTED',
  failure_detail: 'Docker revision label 与 readiness 无法证明恢复完成',
  recovery_detail: '重新核对 runtime 并恢复 previous revision',
} satisfies DeploymentRevisionView;

export const deploymentListFixture = { deployments: [degradedDeploymentFixture, activeDeploymentFixture] } satisfies Schemas['DeploymentRevisionListResponse'];

export const activeManagedPostgresFixture = {
  id: 'managed-postgres-supplier',
  runtime_id: 'aabbccddeeff00112233445566778899',
  name: 'pipedeck-pg-workspace-supplier',
  kind: 'postgres',
  workspace_id: workspaceFixture.id,
  created_at: generatedAt,
  updated_at: generatedAt,
  status: 'active',
  intent: {
    kind: 'postgres', image: 'postgres:18', host_port: 55432, container_port: 5432,
    username: 'supplier', database: 'supplier', password_secret_ref: postgresSecret.id,
  },
  failure_code: null,
} satisfies ManagedResourceRecord;

export const failedManagedPostgresFixture = {
  ...activeManagedPostgresFixture,
  id: 'managed-postgres-failed',
  runtime_id: null,
  name: 'pipedeck-pg-workspace-recovery',
  status: 'failed',
  intent: { ...activeManagedPostgresFixture.intent, host_port: 55433 },
  failure_code: 'MANAGED_HOST_PORT_IN_USE',
} satisfies ManagedResourceRecord;

export const managedMiddlewareListFixture = { resources: [failedManagedPostgresFixture, activeManagedPostgresFixture] } satisfies Schemas['ManagedResourceListResponse'];

export const cleanupPreviewFixture = {
  id: 'cleanup-preview-001', generated_at: generatedAt, runtime_fingerprint: 'runtime-fingerprint-001',
  items: [
    { resource: postgresResource, eligible: false, reason_code: 'PROTECTED_MINIMUM', reason: 'PostgreSQL 至少保留 1 个健康实例' },
    { resource: minioResource, eligible: false, reason_code: 'MINIO_ALWAYS_PROTECTED', reason: 'MinIO 始终受保护' },
    { resource: managedRedisResource, eligible: true, reason_code: null, reason: null },
  ],
} satisfies CleanupPreviewResponse;

type FixtureOptions = {
  session?: Schemas['SessionResponse'];
  catalog?: Schemas['CatalogResponse'];
  runtime?: Schemas['RuntimeResponse'];
  repositories?: { repositories: RepositoryRecord[] };
  secrets?: { secrets: SecretMetadata[] };
  workspaces?: { workspaces: WorkspaceRecord[] };
  environments?: EnvironmentRecord[];
  runs?: { runs: RunRecord[] };
  processes?: Schemas['RuntimeProcessListResponse'];
  deployments?: { deployments: DeploymentRevisionView[] };
  managedResources?: { resources: ManagedResourceRecord[] };
  pipelinePreview?: GitlabPipelinePreview;
  pipelinePlan?: WorkspacePlanResponse;
  cleanupPreview?: CleanupPreviewResponse;
  cleanupResults?: CleanupResultItem[];
};

/** 全控制面 mock:与真实后端同形(类型来自生成的 OpenAPI schema) */
export async function mockFullApi(page: Page, options: FixtureOptions = {}) {
  let repositories = structuredClone((options.repositories ?? repositoryListFixture).repositories);
  let secrets = structuredClone((options.secrets ?? secretListFixture).secrets) as SecretMetadata[];
  let workspaces = structuredClone((options.workspaces ?? workspaceListFixture).workspaces);
  let environments = structuredClone(options.environments ?? [environmentFixture]);
  let runs = structuredClone((options.runs ?? runListFixture).runs);
  let deployments = structuredClone((options.deployments ?? deploymentListFixture).deployments);
  let managedResources = structuredClone((options.managedResources ?? managedMiddlewareListFixture).resources);
  const pipelinePreview = structuredClone(options.pipelinePreview ?? pipelinePreviewFixture) as GitlabPipelinePreview;
  const pipelinePlan = structuredClone(options.pipelinePlan ?? pipelinePlanFixture);
  const cleanupPreview = structuredClone(options.cleanupPreview ?? cleanupPreviewFixture);
  let secretSequence = 0;
  const secretValues = new Map([
    [postgresSecret.id, 'fixture-postgres-password'],
    [minioAccessSecret.id, 'fixture-minio-access'],
    [minioKeySecret.id, 'fixture-minio-key'],
    [disposableSecret.id, 'fixture-disposable-value'],
  ]);
  const checkoutConflict = options.repositories?.repositories === undefined ? null : null;
  void checkoutConflict;
  const writes: { path: string; query: string; method: string; token: string | null; body: unknown }[] = [];
  const problem = (status: number, code: string, detail: string, recovery: string) => ({
    status,
    json: { detail: { code, detail, recovery } },
  });

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() ? (request.postDataJSON() as unknown) : null;
    if (method !== 'GET') {
      const token = request.headers()['x-pipedeck-token'] ?? null;
      writes.push({ path, query: url.search, method, token, body });
      if (token !== 'e2e-token') {
        await route.fulfill(problem(401, 'WRITE_AUTH_REQUIRED', '缺少本地写入令牌', '重新打开客户端'));
        return;
      }
    }

    if (method === 'GET' && path === '/api/v1/session') return route.fulfill({ json: options.session ?? sessionFixture });
    if (method === 'GET' && path === '/api/v1/overview') return route.fulfill({ json: overviewFixture });
    if (method === 'GET' && path === '/api/v1/catalog/projects') return route.fulfill({ json: options.catalog ?? catalogFixture });
    if (method === 'GET' && path === '/api/v1/runtime/resources') return route.fulfill({ json: options.runtime ?? runtimeFixture });
    if (method === 'GET' && path === '/api/v1/runtime/processes') return route.fulfill({ json: options.processes ?? runtimeProcessListFixture });

    // —— 托管中间件 ——
    if (method === 'GET' && path === '/api/v1/managed-middleware') {
      const workspaceId = url.searchParams.get('workspace_id');
      return route.fulfill({ json: { resources: managedResources.filter((resource) => !workspaceId || resource.workspace_id === workspaceId) } });
    }
    if (method === 'POST' && path === '/api/v1/managed-middleware') {
      const payload = body as components['schemas']['ManagedMiddlewareProvisionRequest'];
      const expectedKeys = ['database', 'host_port', 'kind', 'password_secret_ref', 'username', 'workspace_id'];
      const keys = body && typeof body === 'object' ? Object.keys(body).sort() : [];
      if (keys.join('|') !== expectedKeys.join('|')) return route.fulfill(problem(422, 'MANAGED_MIDDLEWARE_REQUEST_INVALID', '托管 PostgreSQL 请求字段不精确', '刷新表单后重试'));
      if (payload.kind !== 'postgres') return route.fulfill(problem(400, 'MANAGED_MIDDLEWARE_KIND_UNSUPPORTED', '只支持 PostgreSQL', '使用已有 Docker 中间件'));
      if (!workspaces.some((workspace) => workspace.id === payload.workspace_id)) return route.fulfill(problem(404, 'WORKSPACE_NOT_FOUND', '托管中间件对应的工作区不存在', '刷新工作区列表后重试'));
      if (!secrets.some((secret) => secret.id === payload.password_secret_ref && secret.present)) return route.fulfill(problem(404, 'SECRET_MISSING', '密码 Secret 不存在或值缺失', '选择 presence 可用的 Secret'));
      if (!Number.isInteger(payload.host_port) || payload.host_port < 1024 || payload.host_port > 65535 || !/^[A-Za-z_][A-Za-z0-9_-]{0,62}$/.test(payload.username) || !/^[A-Za-z_][A-Za-z0-9_-]{0,62}$/.test(payload.database)) return route.fulfill(problem(422, 'MANAGED_MIDDLEWARE_REQUEST_INVALID', '端口或数据库标识符无效', '修正字段后重试'));
      const sequence = managedResources.length + 1;
      const created: ManagedResourceRecord = {
        id: `managed-postgres-created-${sequence}`,
        runtime_id: null,
        name: `pipedeck-pg-${payload.workspace_id}-${sequence}`,
        kind: 'postgres',
        workspace_id: payload.workspace_id,
        created_at: generatedAt,
        updated_at: null,
        status: 'provisioning',
        intent: { kind: 'postgres', image: 'postgres:18', host_port: payload.host_port, container_port: 5432, username: payload.username, database: payload.database, password_secret_ref: payload.password_secret_ref },
        failure_code: null,
      };
      managedResources = [created, ...managedResources];
      return route.fulfill({ status: 202, json: created });
    }
    const managedReconcileMatch = path.match(/^\/api\/v1\/managed-middleware\/([^/]+)\/reconcile$/);
    if (managedReconcileMatch && method === 'POST') {
      const resourceId = decodeURIComponent(managedReconcileMatch[1]!);
      const current = managedResources.find((resource) => resource.id === resourceId);
      if (!current) return route.fulfill(problem(404, 'MANAGED_MIDDLEWARE_NOT_FOUND', '托管中间件不存在', '刷新托管中间件列表'));
      const next: ManagedResourceRecord = ['failed', 'provisioning', 'planned'].includes(current.status)
        ? { ...current, status: 'active', runtime_id: 'ffeeddccbbaa99887766554433221100', failure_code: null, updated_at: generatedAt }
        : current;
      managedResources = managedResources.map((resource) => (resource.id === resourceId ? next : resource));
      return route.fulfill({ status: 202, json: next });
    }
    const managedResourceMatch = path.match(/^\/api\/v1\/managed-middleware\/([^/]+)$/);
    if (managedResourceMatch && method === 'DELETE') {
      const resourceId = decodeURIComponent(managedResourceMatch[1]!);
      const current = managedResources.find((resource) => resource.id === resourceId);
      if (!current) return route.fulfill(problem(404, 'MANAGED_MIDDLEWARE_NOT_FOUND', '托管中间件不存在', '刷新托管中间件列表'));
      // 与真实后端一致:删除后 GET 列表不再返回该资源
      managedResources = managedResources.filter((resource) => resource.id !== resourceId);
      return route.fulfill({ json: { ...current, status: 'removed', runtime_id: null, failure_code: null, updated_at: generatedAt } });
    }

    // —— Deployments ——
    if (method === 'GET' && path === '/api/v1/deployments') {
      const workspaceId = url.searchParams.get('workspace_id');
      const targetId = url.searchParams.get('target_id');
      return route.fulfill({
        json: { deployments: deployments.filter((deployment) => (!workspaceId || deployment.workspace_id === workspaceId) && (!targetId || deployment.project_id === targetId)) },
      });
    }
    const reconcileMatch = path.match(/^\/api\/v1\/deployments\/([^/]+)\/reconcile$/);
    if (reconcileMatch && method === 'POST') {
      const revisionId = decodeURIComponent(reconcileMatch[1]!);
      const current = deployments.find((deployment) => deployment.revision_id === revisionId);
      if (!current) return route.fulfill(problem(404, 'DEPLOYMENT_REVISION_NOT_FOUND', 'Deployment revision 不存在', '刷新 Deployment 列表'));
      const next: DeploymentRevisionView = current.status === 'degraded'
        ? { ...current, status: 'rolled_back', failure_code: null, failure_detail: null, recovery_detail: 'previous revision 已恢复且 readiness 通过', updated_at: generatedAt }
        : current;
      deployments = deployments.map((deployment) => (deployment.revision_id === revisionId ? next : deployment));
      return route.fulfill({ json: next });
    }

    // —— Secrets ——
    if (method === 'GET' && path === '/api/v1/secrets') return route.fulfill({ json: { secrets } });
    if (method === 'POST' && path === '/api/v1/secrets') {
      const payload = body as { name?: unknown; value?: unknown };
      if (typeof payload?.name !== 'string' || !payload.name.trim() || typeof payload.value !== 'string' || !payload.value) {
        return route.fulfill(problem(422, 'INVALID_SECRET_CREATE', 'Secret 名称和值不能为空', '补全字段后重试'));
      }
      secretSequence += 1;
      const metadata: SecretMetadata = { id: `secret-created-${secretSequence}`, name: payload.name.trim(), present: true, version: 1, created_at: generatedAt, updated_at: generatedAt };
      secretValues.set(metadata.id, payload.value);
      secrets = [...secrets, metadata];
      return route.fulfill({ status: 201, json: metadata });
    }
    const secretMatch = path.match(/^\/api\/v1\/secrets\/([^/]+)$/);
    if (secretMatch && method === 'PUT') {
      const secretId = decodeURIComponent(secretMatch[1]!);
      const current = secrets.find((secret) => secret.id === secretId);
      const payload = body as { expected_version?: unknown; value?: unknown };
      if (!current) return route.fulfill(problem(404, 'SECRET_NOT_FOUND', `Secret 不存在：${secretId}`, '刷新 Secret 列表'));
      if (payload.expected_version !== current.version) return route.fulfill(problem(409, 'SECRET_VERSION_CONFLICT', 'Secret version 已变化', '刷新后重新输入新值'));
      if (!current.present || !secretValues.has(secretId)) return route.fulfill(problem(404, 'SECRET_MISSING', '系统凭据值缺失', '删除 metadata 后重新创建 Secret'));
      if (typeof payload.value !== 'string' || !payload.value) return route.fulfill(problem(422, 'INVALID_SECRET_UPDATE', '新值不能为空', '输入新值后重试'));
      secretValues.set(secretId, payload.value);
      const updated: SecretMetadata = { ...current, present: true, version: current.version + 1, updated_at: generatedAt };
      secrets = secrets.map((secret) => (secret.id === secretId ? updated : secret));
      return route.fulfill({ json: updated });
    }
    if (secretMatch && method === 'DELETE') {
      const secretId = decodeURIComponent(secretMatch[1]!);
      const current = secrets.find((secret) => secret.id === secretId);
      if (!current) return route.fulfill(problem(404, 'SECRET_NOT_FOUND', `Secret 不存在：${secretId}`, '刷新 Secret 列表'));
      if (url.searchParams.get('expected_version') !== String(current.version)) return route.fulfill(problem(409, 'SECRET_VERSION_CONFLICT', 'Secret version 已变化', '刷新后重试'));
      // disposableSecret 模拟被某个 workspace 引用的 Secret(fixture 工作区之外的 in-use 契约样例)
      const referenced =
        secretId === disposableSecret.id ||
        workspaces.some((workspace) =>
        workspace.services.some(
          (service) =>
            service.environment.some((binding) => binding.source === 'secret-store' && binding.reference === secretId) ||
            service.connection_profiles.some((profile) =>
              profile.kind === 'postgres' ? profile.secret_ref === secretId : profile.access_key_secret_ref === secretId || profile.secret_key_secret_ref === secretId,
            ),
        ),
      );
      if (referenced) return route.fulfill(problem(409, 'SECRET_IN_USE', `Secret ${secretId} 正被 Workspace 使用`, '先移除工作区引用并保存，再重试删除'));
      secrets = secrets.filter((secret) => secret.id !== secretId);
      secretValues.delete(secretId);
      return route.fulfill({ status: 204, body: '' });
    }

    // —— 仓库 ——
    if (method === 'GET' && path === '/api/v1/repositories') return route.fulfill({ json: { repositories } });
    if (method === 'POST' && path === '/api/v1/repositories/import') {
      const payload = body as { path: string };
      const imported: RepositoryRecord = { ...repositoryFixture, id: 'repository-imported', name: 'imported-repository', path: payload.path, branch: 'main', dirty: false };
      repositories = [...repositories, imported];
      return route.fulfill({ status: 201, json: imported });
    }
    if (method === 'POST' && path === '/api/v1/repositories/clone') {
      const payload = body as { destination_parent: string; directory_name: string | null; url: string };
      const cloned: RepositoryRecord = {
        ...repositoryFixture,
        id: 'repository-cloned',
        name: payload.directory_name ?? 'cloned-repository',
        path: `${payload.destination_parent}\\${payload.directory_name ?? 'cloned-repository'}`,
        origin_url: payload.url,
        dirty: false,
      };
      repositories = [...repositories, cloned];
      return route.fulfill({ status: 201, json: cloned });
    }
    const updateMatch = path.match(/^\/api\/v1\/repositories\/([^/]+)\/update$/);
    if (updateMatch && method === 'POST') {
      const repositoryId = decodeURIComponent(updateMatch[1]!);
      const current = repositories.find((repository) => repository.id === repositoryId);
      if (!current) return route.fulfill(problem(404, 'REPOSITORY_NOT_FOUND', '仓库不存在', '刷新仓库列表'));
      if (current.dirty) return route.fulfill(problem(409, 'REPOSITORY_DIRTY_WORKTREE', 'worktree 有未提交修改,更新被拒绝', '先提交或暂存修改'));
      const updated: RepositoryRecord = { ...current, updated_at: generatedAt };
      return route.fulfill({ json: updated });
    }
    const checkoutMatch = path.match(/^\/api\/v1\/repositories\/([^/]+)\/checkout$/);
    if (checkoutMatch && method === 'POST') {
      const repositoryId = decodeURIComponent(checkoutMatch[1]!);
      const current = repositories.find((repository) => repository.id === repositoryId);
      const payload = body as { ref?: unknown };
      if (!current) return route.fulfill(problem(404, 'REPOSITORY_NOT_FOUND', '仓库不存在', '刷新仓库列表'));
      if (current.dirty) return route.fulfill(problem(409, 'REPOSITORY_CHECKOUT_DIRTY', 'worktree 有未提交修改,切换 ref 被阻断', '先提交或暂存修改,再重试切换'));
      if (typeof payload?.ref !== 'string' || !payload.ref.trim()) return route.fulfill(problem(422, 'REPOSITORY_REF_INVALID', '目标 ref 不能为空', '填写 branch、tag 或 SHA'));
      const checkedOut: RepositoryRecord = { ...current, branch: payload.ref.trim(), head_sha: 'b0b1b2b3b4b5b67890123', updated_at: generatedAt };
      repositories = repositories.map((repository) => (repository.id === repositoryId ? checkedOut : repository));
      return route.fulfill({ json: checkedOut });
    }

    // —— 管道 ——
    const pipelineMatch = path.match(/^\/api\/v1\/repositories\/([^/]+)\/pipeline$/);
    if (pipelineMatch && method === 'GET') {
      const repositoryId = decodeURIComponent(pipelineMatch[1]!);
      if (!repositories.some((repository) => repository.id === repositoryId)) return route.fulfill(problem(404, 'REPOSITORY_NOT_FOUND', '仓库不存在', '刷新仓库列表'));
      return route.fulfill({ json: { ...pipelinePreview, repository_id: repositoryId } });
    }
    const pipelinePlanMatch = path.match(/^\/api\/v1\/repositories\/([^/]+)\/pipeline\/plan$/);
    if (pipelinePlanMatch && method === 'POST') {
      const repositoryId = decodeURIComponent(pipelinePlanMatch[1]!);
      if (!pipelinePreview.ready) {
        return route.fulfill(problem(409, 'PIPELINE_BLOCKED', '管道存在阻断项,未通过计划校验', '修正 .gitlab-ci.yml 后重新生成预检计划'));
      }
      if (!repositories.some((repository) => repository.id === repositoryId)) return route.fulfill(problem(404, 'REPOSITORY_NOT_FOUND', '仓库不存在', '刷新仓库列表'));
      return route.fulfill({ status: 201, json: { ...pipelinePlan, plan_id: 'plan-pipeline-001' } });
    }

    // —— 工作区 ——
    if (method === 'GET' && path === '/api/v1/workspaces') return route.fulfill({ json: { workspaces } });
    if (method === 'POST' && path === '/api/v1/workspaces') {
      const payload = body as WorkspaceRecord;
      const created: WorkspaceRecord = { ...payload, id: 'workspace-created', revision: 1, created_at: generatedAt, updated_at: generatedAt };
      workspaces = [...workspaces, created];
      return route.fulfill({ status: 201, json: created });
    }
    const workspaceMatch = path.match(/^\/api\/v1\/workspaces\/([^/]+)$/);
    if (workspaceMatch && method === 'GET') {
      const found = workspaces.find((workspace) => workspace.id === workspaceMatch[1]);
      if (!found) return route.fulfill(problem(404, 'WORKSPACE_NOT_FOUND', '工作区不存在', '刷新工作区列表后重新选择'));
      return route.fulfill({ json: found });
    }
    if (workspaceMatch && method === 'PUT') {
      const current = workspaces.find((workspace) => workspace.id === workspaceMatch[1]);
      const payload = body as WorkspaceRecord & { expected_revision?: number };
      if (!current) return route.fulfill(problem(404, 'WORKSPACE_NOT_FOUND', '工作区不存在', '刷新工作区列表后重新选择'));
      if (payload.expected_revision !== current.revision) return route.fulfill(problem(409, 'WORKSPACE_REVISION_CONFLICT', '工作区 revision 不匹配', '刷新工作区后重试'));
      const updated: WorkspaceRecord = {
        ...current,
        name: payload.name,
        mode: payload.mode,
        services: payload.services,
        bindings: payload.bindings,
        revision: current.revision + 1,
        updated_at: generatedAt,
      };
      workspaces = workspaces.map((workspace) => (workspace.id === updated.id ? updated : workspace));
      return route.fulfill({ json: updated });
    }
    if (workspaceMatch && method === 'DELETE') {
      const current = workspaces.find((workspace) => workspace.id === workspaceMatch[1]);
      const expectedRevision = url.searchParams.get('expected_revision');
      if (!current || expectedRevision !== String(current.revision)) {
        return route.fulfill(problem(409, 'WORKSPACE_REVISION_CONFLICT', '工作区 revision 不匹配', '刷新工作区后重试'));
      }
      workspaces = workspaces.filter((workspace) => workspace.id !== workspaceMatch[1]);
      return route.fulfill({ status: 204, body: '' });
    }
    if (method === 'POST' && /^\/api\/v1\/workspaces\/[^/]+\/plans$/.test(path)) {
      const workspaceId = path.split('/')[4];
      const workspace = workspaces.find((record) => record.id === workspaceId);
      if (!workspace || (body as { expected_revision?: unknown })?.expected_revision !== workspace.revision) {
        return route.fulfill(problem(409, 'WORKSPACE_REVISION_CONFLICT', '工作区配置已变化', '刷新工作区后重新预检'));
      }
      return route.fulfill({ status: 201, json: { ...workspacePlanFixture, workspace_id: workspace.id, workspace_revision: workspace.revision } });
    }

    // —— Environments ——
    const environmentsMatch = path.match(/^\/api\/v1\/workspaces\/([^/]+)\/environments$/);
    if (environmentsMatch && method === 'GET') {
      const workspaceId = decodeURIComponent(environmentsMatch[1]!);
      return route.fulfill({ json: { environments: environments.filter((environment) => environment.workspace_id === workspaceId) } });
    }
    if (environmentsMatch && method === 'POST') {
      const workspaceId = decodeURIComponent(environmentsMatch[1]!);
      const payload = body as { ref?: unknown };
      if (!workspaces.some((workspace) => workspace.id === workspaceId)) return route.fulfill(problem(404, 'WORKSPACE_NOT_FOUND', '工作区不存在', '刷新工作区列表后重试'));
      if (typeof payload?.ref !== 'string' || !payload.ref.trim()) return route.fulfill(problem(422, 'ENVIRONMENT_REF_INVALID', '目标 ref 不能为空', '填写 branch 或 tag'));
      const ref = payload.ref.trim();
      if (environments.some((environment) => environment.workspace_id === workspaceId && environment.ref === ref)) {
        return route.fulfill(problem(409, 'ENVIRONMENT_REF_EXISTS', `ref ${ref} 已存在 Environment`, '清理已有环境后重试'));
      }
      const created: EnvironmentRecord = {
        id: `environment-${ref.replace(/[^a-z0-9]/gi, '-')}`,
        workspace_id: workspaceId,
        repository_id: repositoryFixture.id,
        ref,
        worktree_path: `D:\\pipedeck\\worktrees\\${workspaceId}\\${ref}`,
        created_at: generatedAt,
        updated_at: generatedAt,
      };
      environments = [...environments, created];
      return route.fulfill({ status: 201, json: created });
    }
    const environmentMatch = path.match(/^\/api\/v1\/environments\/([^/]+)$/);
    if (environmentMatch && method === 'DELETE') {
      const environmentId = decodeURIComponent(environmentMatch[1]!);
      const current = environments.find((environment) => environment.id === environmentId);
      if (!current) return route.fulfill(problem(404, 'ENVIRONMENT_NOT_FOUND', 'Environment 不存在', '刷新环境列表'));
      if (current.ref === 'dirty-branch') {
        return route.fulfill(problem(409, 'ENVIRONMENT_WORKTREE_DIRTY', 'worktree 有未提交修改,删除被阻断', '提交或暂存 worktree 变更后重试删除'));
      }
      environments = environments.filter((environment) => environment.id !== environmentId);
      return route.fulfill({ json: current });
    }

    // —— Runs ——
    if (method === 'GET' && path === '/api/v1/runs') return route.fulfill({ json: { runs } });
    if (method === 'POST' && path === '/api/v1/runs') {
      const payload = body as { plan_id?: unknown; idempotency_key?: unknown };
      if (typeof payload?.plan_id !== 'string') return route.fulfill(problem(422, 'INVALID_RUN_CREATE', '缺少 plan_id', '重新生成预检计划'));
      if (payload.plan_id === 'stale-plan') return route.fulfill(problem(409, 'PLAN_STALE', '计划指纹已过期', '重新生成预检计划后再运行'));
      const created: RunRecord = {
        ...runningRunFixture,
        id: 'run-created-001',
        plan_id: payload.plan_id,
        status: 'queued',
        current_step: null,
        started_at: null,
        finished_at: null,
      };
      runs = [created, ...runs];
      return route.fulfill({ status: 201, json: created });
    }
    const eventsMatch = path.match(/^\/api\/v1\/runs\/([^/]+)\/events$/);
    if (eventsMatch && method === 'GET') {
      return route.fulfill({ json: { ...eventsFixture, events: eventsFixture.events.map((event) => ({ ...event, run_id: eventsMatch[1]! })) } });
    }
    const runActionMatch = path.match(/^\/api\/v1\/runs\/([^/]+)\/(cancel|retry)$/);
    if (runActionMatch && method === 'POST') {
      const current = runs.find((run) => run.id === runActionMatch[1]) ?? runningRunFixture;
      if (runActionMatch[2] === 'retry') {
        const idempotencyKey = body && typeof body === 'object' && 'idempotency_key' in body ? body.idempotency_key : null;
        if (typeof idempotencyKey !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(idempotencyKey)) {
          return route.fulfill(problem(422, 'INVALID_IDEMPOTENCY_KEY', '重试缺少合法幂等键', '重新提交重试'));
        }
        if (!['succeeded', 'failed', 'cancelled', 'interrupted'].includes(current.status)) {
          return route.fulfill(problem(409, 'RUN_NOT_RETRYABLE', '只能重试已结束的运行', '等待当前 Run 结束或先取消'));
        }
      }
      const next: RunRecord = runActionMatch[2] === 'cancel'
        ? { ...current, status: 'cancelled', finished_at: generatedAt }
        : { ...current, id: 'run-retry-001', status: 'queued', current_step: null, retry_of: current.id, finished_at: null };
      runs = runActionMatch[2] === 'cancel' ? runs.map((run) => (run.id === current.id ? next : run)) : [next, ...runs];
      return route.fulfill({ status: runActionMatch[2] === 'retry' ? 201 : 200, json: next });
    }
    const runMatch = path.match(/^\/api\/v1\/runs\/([^/]+)$/);
    if (runMatch && method === 'GET') {
      const found = runs.find((run) => run.id === runMatch[1]);
      if (!found) return route.fulfill(problem(404, 'RUN_NOT_FOUND', '运行记录不存在', '刷新运行记录列表'));
      return route.fulfill({ json: found });
    }

    // —— 清理 ——
    if (method === 'POST' && path === '/api/v1/runtime/cleanup-previews') return route.fulfill({ status: 201, json: cleanupPreview });
    const cleanupApplyMatch = path.match(/^\/api\/v1\/runtime\/cleanup-previews\/([^/]+)\/apply$/);
    if (method === 'POST' && cleanupApplyMatch) {
      const payload = body as Schemas['CleanupApplyRequest'];
      const resourceIds = Array.isArray(payload?.resource_ids) ? payload.resource_ids : [];
      const configuredResults = options.cleanupResults ?? resourceIds.map((id): CleanupResultItem => ({
        resource_id: id,
        resource_name: cleanupPreview.items.find((item) => item.resource.id === id)?.resource.name ?? id,
        status: 'removed',
        reason_code: null,
        detail: null,
      }));
      const invalidRequest =
        payload?.preview_id !== cleanupApplyMatch[1] ||
        payload.preview_id !== cleanupPreview.id ||
        resourceIds.length === 0 ||
        configuredResults.some((result) => !resourceIds.includes(result.resource_id));
      if (invalidRequest) {
        return route.fulfill(problem(422, 'INVALID_CLEANUP_APPLY', '清理请求与预览或结果 fixture 不匹配', '重新生成清理预览'));
      }
      return route.fulfill({ json: { preview_id: cleanupPreview.id, results: configuredResults } });
    }

    return route.fulfill({ status: 404, json: { detail: `Unmocked ${method} ${path}` } });
  });

  const reorderWorkspaces = (ids: string[]) => {
    const records = new Map(workspaces.map((workspace) => [workspace.id, workspace]));
    workspaces = ids.map((id) => records.get(id)).filter((workspace): workspace is WorkspaceRecord => Boolean(workspace));
  };
  const reorderRuns = (ids: string[]) => {
    const records = new Map(runs.map((run) => [run.id, run]));
    runs = ids.map((id) => records.get(id)).filter((run): run is RunRecord => Boolean(run));
  };

  return {
    writes,
    reorderWorkspaces,
    reorderRuns,
    getSecrets: () => secrets,
    getRepositories: () => repositories,
    getEnvironments: () => environments,
    getRuns: () => runs,
    getDeployments: () => deployments,
    getManagedResources: () => managedResources,
  };
}
