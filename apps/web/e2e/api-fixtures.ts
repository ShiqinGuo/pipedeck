import type { Page } from '@playwright/test';

import type {
  CleanupApplyRequest,
  CleanupPreviewResponse,
  CleanupResultItem,
  DeploymentRevisionView,
  ManagedMiddlewareProvisionRequest,
  ManagedResourceRecord,
  RepositoryRecord,
  RunRecord,
  RuntimeProcessRecord,
  SecretMetadata,
  WorkspaceRecord,
} from '../src/types';

const generatedAt = '2026-08-17T12:00:00Z';

export const backendProject = {
  id: 'project-backend',
  name: 'supplier-backend-v2',
  path: 'D:\\code\\supplier-backend-v2',
  kind: 'python-uv',
  branch: 'feat/local-control',
  dirty: true,
  commands: [
    { id: 'install', label: '安装依赖', argv: ['uv', 'sync'], kind: 'dependencies', long_running: false },
    { id: 'typecheck', label: '类型检查', argv: ['uv', 'run', 'pyright'], kind: 'quality', long_running: false },
    { id: 'test', label: '运行测试', argv: ['uv', 'run', 'pytest', '-k', 'connection profile'], kind: 'quality', long_running: false },
    { id: 'start', label: '启动服务', argv: ['uv', 'run', 'uvicorn', 'app.main:app', '--reload'], kind: 'start', long_running: true },
  ],
  requirements: ['postgres', 'minio'],
  warnings: ['工作区包含未提交修改'],
  container_capabilities: { dockerfile: 'Dockerfile', compose_files: ['compose.yml'] },
};

export const frontendProject = {
  id: 'project-frontend',
  name: 'supplier-admin-frontend-v2',
  path: 'D:\\code\\supplier-admin-frontend-v2',
  kind: 'vite-react',
  branch: 'main',
  dirty: false,
  commands: [
    { id: 'install', label: '安装依赖', argv: ['pnpm', 'install', '--frozen-lockfile'], kind: 'dependencies', long_running: false },
    { id: 'typecheck', label: '类型检查', argv: ['pnpm', 'typecheck'], kind: 'quality', long_running: false },
    { id: 'build', label: '构建前端', argv: ['pnpm', 'build'], kind: 'build', long_running: false },
    { id: 'start', label: '启动前端', argv: ['pnpm', 'dev'], kind: 'start', long_running: true },
  ],
  requirements: [],
  warnings: [],
  container_capabilities: { dockerfile: 'Dockerfile', compose_files: [] },
};

export const catalogFixture = {
  generated_at: generatedAt,
  roots: ['D:\\code'],
  projects: [backendProject, frontendProject],
  errors: [],
};

export const postgresResource = {
  id: 'resource-postgres', name: 'tripguru-postgres', kind: 'postgres', image: 'postgres:18', state: 'running',
  status_text: 'Up 3 hours (healthy)', health: 'healthy', managed: false, protected: true, ports: '127.0.0.1:5432->5432/tcp',
  endpoints: [{ protocol: 'tcp', container_port: 5432, host: '127.0.0.1', host_port: 5432 }], owner_workspace_id: null,
};
export const minioResource = {
  id: 'resource-minio', name: 'tripguru-minio', kind: 'minio', image: 'minio/minio:latest', state: 'running',
  status_text: 'Up 3 hours (healthy)', health: 'healthy', managed: false, protected: true, ports: '127.0.0.1:9000->9000/tcp',
  endpoints: [{ protocol: 'tcp', container_port: 9000, host: '127.0.0.1', host_port: 9000 }], owner_workspace_id: null,
};
export const managedRedisResource = {
  id: 'resource-redis-old', name: 'tripguru-old-redis', kind: 'redis', image: 'redis:8', state: 'exited',
  status_text: 'Exited 2 days ago', health: 'stopped', managed: true, protected: false, ports: '', endpoints: [], owner_workspace_id: null,
};

export const postgresSecret = { id: 'secret-pg-password', name: 'Supplier PostgreSQL password', present: true, version: 1, created_at: generatedAt, updated_at: generatedAt } satisfies SecretMetadata;
export const minioAccessSecret = { id: 'secret-minio-access', name: 'Supplier MinIO access key', present: true, version: 1, created_at: generatedAt, updated_at: generatedAt } satisfies SecretMetadata;
export const minioKeySecret = { id: 'secret-minio-key', name: 'Supplier MinIO secret key', present: true, version: 1, created_at: generatedAt, updated_at: generatedAt } satisfies SecretMetadata;
export const missingSecret = { id: 'secret-missing-value', name: 'Credential metadata only', present: false, version: 2, created_at: generatedAt, updated_at: generatedAt } satisfies SecretMetadata;
export const disposableSecret = { id: 'secret-disposable', name: 'Disposable secret', present: true, version: 1, created_at: generatedAt, updated_at: generatedAt } satisfies SecretMetadata;
export const secretListFixture = { secrets: [postgresSecret, minioAccessSecret, minioKeySecret, missingSecret, disposableSecret] };

export const runtimeFixture = {
  generated_at: generatedAt,
  docker_available: true,
  resources: [postgresResource, minioResource, managedRedisResource],
  error_code: null,
  recovery: null,
};

export const overviewFixture = {
  generated_at: generatedAt,
  api_status: 'ready',
  docker_available: true,
  project_count: 2,
  dirty_project_count: 1,
  middleware_count: 3,
  protected_kinds: ['postgres', 'minio'],
};

export const repositoryFixture = {
  id: 'repository-backend', name: backendProject.name, path: backendProject.path,
  origin_url: 'git@gitlab.example.com:tripguru/supplier-backend-v2.git', branch: backendProject.branch,
  head_sha: '0a1b2c3d4e5f67890123', upstream: `origin/${backendProject.branch}`, dirty: true,
  created_at: generatedAt, updated_at: generatedAt,
};

export const workspaceFixture = {
  id: 'workspace-supplier', name: 'Supplier 本地集成', mode: 'development', revision: 3,
  services: [
    {
      project_id: backendProject.id,
      commands: backendProject.commands.map((command) => ({ ...command, kind: command.kind ?? 'quality' })),
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
    {
      project_id: frontendProject.id,
      commands: frontendProject.commands.map((command) => ({ ...command, kind: command.kind ?? 'quality' })),
      environment: [{ name: 'VITE_API_BASE_URL', source: 'literal', value: 'http://127.0.0.1:8000', reference: null }],
      connection_profiles: [],
      execution_target: {
        kind: 'compose',
        source: { kind: 'dockerfile', context: '.', dockerfile: 'Dockerfile' },
        endpoints: [{ name: 'web', protocol: 'tcp', host_port: 5173, container_port: 5173 }],
        readiness: { kind: 'tcp', endpoint: 'web' },
        wait_timeout: 120,
      },
    },
  ],
  bindings: [
    { kind: 'postgres', resource_id: postgresResource.id },
    { kind: 'minio', resource_id: minioResource.id },
  ],
  created_at: generatedAt,
  updated_at: generatedAt,
};

export const repositoryListFixture = { repositories: [repositoryFixture] };
export const workspaceListFixture = { workspaces: [workspaceFixture] };

const planCommand = (project: typeof backendProject | typeof frontendProject, commandId: string, label: string, argv: string[], longRunning = false) => ({
  project_id: project.id, project_name: project.name, command_id: commandId, label, cwd: project.path, argv,
  environment: commandId === 'start' ? [{ name: 'RUNTIME_CREDENTIAL', value: 'fixture-raw-runtime-secret' }] : [],
  long_running: longRunning,
});

const sourceFingerprint = '1'.repeat(64);
const targetConfigFingerprint = '2'.repeat(64);
const frontendDeploymentPlan = {
  revision_id: 'deployment-revision-frontend-r3', workspace_id: workspaceFixture.id, project_id: frontendProject.id, workspace_revision: 3,
  source_fingerprint: sourceFingerprint, target_config_fingerprint: targetConfigFingerprint,
  checkout_path: frontendProject.path, frozen_compose_path: 'D:\\tripguru-local\\deployments\\private-compose.yml',
  services: ['supplier-admin'], immutable_images: ['tripguru.local/supplier-admin@sha256:abcdef0123456789'], wait_timeout_seconds: 120,
  probe: { kind: 'tcp', host: '127.0.0.1', port: 5173, timeout_seconds: 120 },
  environment_spec: { environment: [{ name: 'APP_SECRET', source: 'secret-store', value: null, reference: postgresSecret.id }], connection_profiles: [], bindings: [] },
};

export const planFixture = {
  generated_at: generatedAt,
  ready: true,
  mode: 'development',
  projects: [backendProject, frontendProject],
  steps: [
    { id: 'quality', kind: 'quality', title: '运行质量门禁', detail: '在各自 checkout 中运行类型检查', commands: [planCommand(backendProject, 'typecheck', '类型检查', ['uv', 'run', 'pyright']), planCommand(frontendProject, 'typecheck', '类型检查', ['pnpm', 'typecheck'])], deployments: [] },
    { id: 'deploy', kind: 'deploy', title: '部署 Compose target', detail: '构建 immutable image 并原子切换 revision', commands: [], deployments: [frontendDeploymentPlan] },
    { id: 'start', kind: 'start', title: '启动本地服务', detail: '使用已保存的命令、环境变量和 target 启动', commands: [planCommand(backendProject, 'start', '启动服务', ['uv', 'run', 'uvicorn', 'app.main:app', '--reload'], true)], deployments: [] },
  ],
  blockers: [],
  warnings: [{ code: 'DIRTY_WORKTREE', title: '后端有未提交修改', detail: '计划固定当前源码指纹', recovery: '确认这些修改是本次本地验证的一部分' }],
  connection_mappings: [
    { project_id: backendProject.id, kind: 'postgres', resource_id: postgresResource.id, resource_name: postgresResource.name, outputs: [{ name: 'DATABASE_URL', redacted_value: 'postgresql+asyncpg://supplier:***@127.0.0.1:5432/supplier', sensitive: true }] },
    { project_id: backendProject.id, kind: 'minio', resource_id: minioResource.id, resource_name: minioResource.name, outputs: [{ name: 'S3_ENDPOINT', redacted_value: 'http://127.0.0.1:9000', sensitive: false }, { name: 'S3_ACCESS_KEY', redacted_value: '***', sensitive: true }, { name: 'S3_SECRET_KEY', redacted_value: '***', sensitive: true }, { name: 'S3_BUCKET', redacted_value: 'local-assets', sensitive: false }] },
  ],
  plan_id: 'plan-supplier-r3', workspace_id: workspaceFixture.id, workspace_revision: 3,
  config_fingerprint: targetConfigFingerprint, source_fingerprint: sourceFingerprint,
};

export const runningRunFixture = {
  id: 'run-active-001', workspace_id: workspaceFixture.id, workspace_name: workspaceFixture.name, workspace_revision: 3,
  plan_id: planFixture.plan_id, mode: 'development', status: 'running', current_step: 'start',
  config_fingerprint: planFixture.config_fingerprint, source_fingerprint: planFixture.source_fingerprint, retry_of: null,
  created_at: generatedAt, started_at: generatedAt, finished_at: null, failure_code: null, failure_detail: null,
};
export const failedRunFixture = {
  ...runningRunFixture,
  id: 'run-failed-001', status: 'failed', current_step: 'quality', finished_at: '2026-08-17T12:01:10Z',
  failure_code: 'COMMAND_FAILED', failure_detail: 'supplier-backend-v2 类型检查失败',
};
export const runListFixture = { runs: [runningRunFixture, failedRunFixture] };
export const runtimeProcessFixture = {
  id: 'process-backend-start', pid: 43120, run_id: runningRunFixture.id, project_id: backendProject.id, project_name: backendProject.name,
  command_id: 'start', label: '启动服务', cwd: backendProject.path,
  argv: ['uv', 'run', 'uvicorn', 'app.main:app', '--app-dir', 'directory with spaces'], long_running: true, started_at: generatedAt,
} satisfies RuntimeProcessRecord;
export const runtimeProcessListFixture = { generated_at: generatedAt, processes: [runtimeProcessFixture] };

export const activeDeploymentFixture = {
  revision_id: frontendDeploymentPlan.revision_id, workspace_id: workspaceFixture.id, project_id: frontendProject.id, workspace_revision: 3,
  project_name: frontendProject.name, previous_revision_id: 'deployment-revision-frontend-r2', status: 'active',
  source_fingerprint: sourceFingerprint, target_config_fingerprint: targetConfigFingerprint,
  services: frontendDeploymentPlan.services, immutable_images: frontendDeploymentPlan.immutable_images,
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
export const deploymentListFixture = { deployments: [degradedDeploymentFixture, activeDeploymentFixture] };
export const activeManagedPostgresFixture = {
  id: 'managed-postgres-supplier',
  runtime_id: 'aabbccddeeff00112233445566778899',
  name: 'tripguru-pg-workspace-supplier',
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
  name: 'tripguru-pg-workspace-recovery',
  status: 'failed',
  intent: { ...activeManagedPostgresFixture.intent, host_port: 55433 },
  failure_code: 'MANAGED_HOST_PORT_IN_USE',
} satisfies ManagedResourceRecord;
export const managedMiddlewareListFixture = { resources: [failedManagedPostgresFixture, activeManagedPostgresFixture] };
export const eventsFixture = {
  events: [
    { sequence: 1, run_id: runningRunFixture.id, created_at: generatedAt, kind: 'status', step_id: null, project_id: null, message: '运行已启动' },
    { sequence: 2, run_id: runningRunFixture.id, created_at: generatedAt, kind: 'stage', step_id: 'quality', project_id: null, message: '开始运行质量门禁' },
    { sequence: 3, run_id: runningRunFixture.id, created_at: generatedAt, kind: 'stdout', step_id: 'quality', project_id: backendProject.id, message: '0 errors, 0 warnings' },
    { sequence: 4, run_id: runningRunFixture.id, created_at: generatedAt, kind: 'stage', step_id: 'start', project_id: null, message: '启动本地服务' },
    { sequence: 5, run_id: runningRunFixture.id, created_at: generatedAt, kind: 'system', step_id: 'start', project_id: frontendProject.id, message: 'Local: http://127.0.0.1:5173/' },
  ],
  next_after: 5,
};

export const cleanupPreviewFixture = {
  id: 'cleanup-preview-001', generated_at: generatedAt, runtime_fingerprint: 'runtime-fingerprint-001',
  items: [
    { resource: postgresResource, eligible: false, reason_code: 'PROTECTED_MINIMUM', reason: 'PostgreSQL 至少保留 1 个健康实例' },
    { resource: minioResource, eligible: false, reason_code: 'MINIO_ALWAYS_PROTECTED', reason: 'MinIO 始终受保护' },
    { resource: managedRedisResource, eligible: true, reason_code: null, reason: null },
  ],
};

type FixtureOptions = {
  catalog?: object;
  runtime?: object;
  repositories?: object;
  secrets?: object;
  workspaces?: object;
  runs?: object;
  processes?: object;
  deployments?: object;
  managedResources?: object;
  cleanupPreview?: CleanupPreviewResponse;
  cleanupResults?: CleanupResultItem[];
};

export async function mockFullApi(page: Page, options: FixtureOptions = {}) {
  let repositories = structuredClone((options.repositories ?? repositoryListFixture) as typeof repositoryListFixture).repositories as RepositoryRecord[];
  let secrets = structuredClone((options.secrets ?? secretListFixture) as typeof secretListFixture).secrets as SecretMetadata[];
  let workspaces = structuredClone((options.workspaces ?? workspaceListFixture) as typeof workspaceListFixture).workspaces as WorkspaceRecord[];
  let runs = structuredClone((options.runs ?? runListFixture) as typeof runListFixture).runs as RunRecord[];
  const processes = structuredClone((options.processes ?? runtimeProcessListFixture) as typeof runtimeProcessListFixture).processes as RuntimeProcessRecord[];
  let deployments = structuredClone((options.deployments ?? deploymentListFixture) as typeof deploymentListFixture).deployments as DeploymentRevisionView[];
  let managedResources = structuredClone((options.managedResources ?? managedMiddlewareListFixture) as typeof managedMiddlewareListFixture).resources as ManagedResourceRecord[];
  let secretSequence = 0;
  const secretValues = new Map([
    [postgresSecret.id, 'fixture-postgres-password'],
    [minioAccessSecret.id, 'fixture-minio-access'],
    [minioKeySecret.id, 'fixture-minio-key'],
    [disposableSecret.id, 'fixture-disposable-value'],
  ]);
  const cleanupPreview = structuredClone(options.cleanupPreview ?? cleanupPreviewFixture) as CleanupPreviewResponse;
  const writes: { path: string; query: string; method: string; token: string | null; body: unknown }[] = [];
  const secretAvailable = (secretId: string) => secrets.some((secret) => secret.id === secretId && secret.present);
  const validBackendConnections = (services: WorkspaceRecord['services'] | undefined) => {
    const backend = services?.find((service) => service.project_id === backendProject.id);
    if (!backend) return true;
    const postgres = backend.connection_profiles.find((profile) => profile.kind === 'postgres');
    const minio = backend.connection_profiles.find((profile) => profile.kind === 'minio');
    const postgresValid = postgres?.kind === 'postgres'
      && /^[A-Za-z_][A-Za-z0-9_]*$/.test(postgres.env_var)
      && /^postgresql(?:\+[a-z0-9_]+)?$/.test(postgres.scheme)
      && Boolean(postgres.username && postgres.database)
      && secretAvailable(postgres.secret_ref);
    const minioValid = minio?.kind === 'minio'
      && [minio.endpoint_env, minio.access_key_env, minio.secret_key_env, minio.bucket_env].every((name) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(name))
      && Boolean(minio.bucket)
      && secretAvailable(minio.access_key_secret_ref)
      && secretAvailable(minio.secret_key_secret_ref);
    const environmentValid = backend.environment.every((binding) => binding.source !== 'secret-store' || Boolean(binding.reference && secretAvailable(binding.reference)));
    return Boolean(postgresValid && minioValid && environmentValid);
  };
  const validTarget = (service: WorkspaceRecord['services'][number]) => {
    if ('ports' in service || !service.execution_target) return false;
    const target = service.execution_target;
    const names = target.endpoints.map((endpoint) => endpoint.name);
    if (names.some((name) => !/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(name)) || new Set(names).size !== names.length) return false;
    if (target.endpoints.some((endpoint) => endpoint.host_port < 1 || endpoint.host_port > 65535)) return false;
    if (target.readiness && !names.includes(target.readiness.endpoint)) return false;
    if (target.kind === 'host') {
      return target.readiness_timeout >= 1 && target.readiness_timeout <= 900
        && target.stop_timeout >= 1 && target.stop_timeout <= 300
        && target.endpoints.every((endpoint) => endpoint.injection.kind !== 'environment' || /^[A-Za-z_][A-Za-z0-9_]*$/.test(endpoint.injection.name));
    }
    if (target.endpoints.length === 0 || !target.readiness || target.wait_timeout < 1 || target.wait_timeout > 900 || target.endpoints.some((endpoint) => endpoint.container_port < 1 || endpoint.container_port > 65535)) return false;
    if (target.source.kind === 'existing-compose') return target.source.compose_files.length > 0 && target.source.service_names.length > 0;
    return Boolean(target.source.context && target.source.dockerfile);
  };
  const validWorkspacePayload = (payload: WorkspaceRecord) => validBackendConnections(payload.services)
    && Array.isArray(payload.services)
    && payload.services.length > 0
    && payload.services.every(validTarget)
    && payload.services.every((service) => service.commands.every((command) => command.argv.length > 0 && Boolean(command.argv[0])));

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const isWrite = method !== 'GET';
    const body = request.postData() ? request.postDataJSON() as unknown : null;
    if (isWrite) {
      const token = request.headers()['x-tripguru-local-token'] ?? null;
      writes.push({ path, query: url.search, method, token, body });
      if (token !== 'e2e-token') {
        await route.fulfill({ status: 401, json: { detail: { code: 'WRITE_AUTH_REQUIRED', detail: '缺少本地写入令牌', recovery: '重新打开客户端' } } });
        return;
      }
    }

    if (method === 'GET' && path === '/api/v1/overview') return route.fulfill({ json: overviewFixture });
    if (method === 'GET' && path === '/api/v1/catalog/projects') return route.fulfill({ json: options.catalog ?? catalogFixture });
    if (method === 'GET' && path === '/api/v1/runtime/resources') return route.fulfill({ json: options.runtime ?? runtimeFixture });
    if (method === 'GET' && path === '/api/v1/runtime/processes') return route.fulfill({ json: { generated_at: generatedAt, processes } });
    if (method === 'GET' && path === '/api/v1/managed-middleware') {
      const workspaceId = url.searchParams.get('workspace_id');
      return route.fulfill({ json: { resources: managedResources.filter((resource) => !workspaceId || resource.workspace_id === workspaceId) } });
    }
    if (method === 'POST' && path === '/api/v1/managed-middleware') {
      const payload = body as ManagedMiddlewareProvisionRequest;
      const expectedKeys = ['database', 'host_port', 'kind', 'password_secret_ref', 'username', 'workspace_id'];
      const keys = body && typeof body === 'object' ? Object.keys(body).sort() : [];
      if (keys.join('|') !== expectedKeys.join('|')) return route.fulfill({ status: 422, json: { detail: { code: 'MANAGED_MIDDLEWARE_REQUEST_INVALID', detail: '托管 PostgreSQL 请求字段不精确', recovery: '刷新表单后重试' } } });
      if (payload.kind !== 'postgres') return route.fulfill({ status: 400, json: { detail: { code: 'MANAGED_MIDDLEWARE_KIND_UNSUPPORTED', detail: '只支持 PostgreSQL', recovery: '使用已有 Docker 中间件' } } });
      if (!workspaces.some((workspace) => workspace.id === payload.workspace_id)) return route.fulfill({ status: 404, json: { detail: { code: 'WORKSPACE_NOT_FOUND', detail: '托管中间件对应的工作区不存在', recovery: '刷新工作区列表后重试' } } });
      if (!secretAvailable(payload.password_secret_ref)) return route.fulfill({ status: 404, json: { detail: { code: 'SECRET_MISSING', detail: '密码 Secret 不存在或值缺失', recovery: '选择 presence 可用的 Secret' } } });
      if (!Number.isInteger(payload.host_port) || payload.host_port < 1024 || payload.host_port > 65535 || !/^[A-Za-z_][A-Za-z0-9_-]{0,62}$/.test(payload.username) || !/^[A-Za-z_][A-Za-z0-9_-]{0,62}$/.test(payload.database)) return route.fulfill({ status: 422, json: { detail: { code: 'MANAGED_MIDDLEWARE_REQUEST_INVALID', detail: '端口或数据库标识符无效', recovery: '修正字段后重试' } } });
      const sequence = managedResources.length + 1;
      const created: ManagedResourceRecord = {
        id: `managed-postgres-created-${sequence}`,
        runtime_id: null,
        name: `tripguru-pg-${payload.workspace_id}-${sequence}`,
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
      if (!current) return route.fulfill({ status: 404, json: { detail: { code: 'MANAGED_MIDDLEWARE_NOT_FOUND', detail: '托管中间件不存在', recovery: '刷新托管中间件列表' } } });
      const next: ManagedResourceRecord = current.status === 'failed' || current.status === 'provisioning' || current.status === 'planned'
        ? { ...current, status: 'active', runtime_id: 'ffeeddccbbaa99887766554433221100', failure_code: null, updated_at: generatedAt }
        : current;
      managedResources = managedResources.map((resource) => resource.id === resourceId ? next : resource);
      return route.fulfill({ status: 202, json: next });
    }
    const managedResourceMatch = path.match(/^\/api\/v1\/managed-middleware\/([^/]+)$/);
    if (managedResourceMatch && method === 'DELETE') {
      const resourceId = decodeURIComponent(managedResourceMatch[1]!);
      const current = managedResources.find((resource) => resource.id === resourceId);
      if (!current) return route.fulfill({ status: 404, json: { detail: { code: 'MANAGED_MIDDLEWARE_NOT_FOUND', detail: '托管中间件不存在', recovery: '刷新托管中间件列表' } } });
      const removed: ManagedResourceRecord = { ...current, status: 'removed', runtime_id: null, failure_code: null, updated_at: generatedAt };
      managedResources = managedResources.map((resource) => resource.id === resourceId ? removed : resource);
      return route.fulfill({ json: removed });
    }
    if (method === 'GET' && path === '/api/v1/deployments') {
      const workspaceId = url.searchParams.get('workspace_id');
      const targetId = url.searchParams.get('target_id');
      return route.fulfill({ json: { deployments: deployments.filter((deployment) => (!workspaceId || deployment.workspace_id === workspaceId) && (!targetId || deployment.project_id === targetId)) } });
    }
    const reconcileMatch = path.match(/^\/api\/v1\/deployments\/([^/]+)\/reconcile$/);
    if (reconcileMatch && method === 'POST') {
      const revisionId = decodeURIComponent(reconcileMatch[1]!);
      const current = deployments.find((deployment) => deployment.revision_id === revisionId);
      if (!current) return route.fulfill({ status: 404, json: { detail: { code: 'DEPLOYMENT_REVISION_NOT_FOUND', detail: 'Deployment revision 不存在', recovery: '刷新 Deployment 列表' } } });
      const next: DeploymentRevisionView = current.status === 'degraded'
        ? { ...current, status: 'rolled_back', failure_code: null, failure_detail: null, recovery_detail: 'previous revision 已恢复且 readiness 通过', updated_at: generatedAt }
        : current;
      deployments = deployments.map((deployment) => deployment.revision_id === revisionId ? next : deployment);
      return route.fulfill({ json: next });
    }
    if (method === 'GET' && path === '/api/v1/secrets') return route.fulfill({ json: { secrets } });
    if (method === 'POST' && path === '/api/v1/secrets') {
      const payload = body as { name?: unknown; value?: unknown };
      if (typeof payload?.name !== 'string' || !payload.name.trim() || typeof payload.value !== 'string' || !payload.value) {
        return route.fulfill({ status: 422, json: { detail: { code: 'INVALID_SECRET_CREATE', detail: 'Secret 名称和值不能为空', recovery: '补全字段后重试' } } });
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
      if (!current) return route.fulfill({ status: 404, json: { detail: { code: 'SECRET_NOT_FOUND', detail: `Secret 不存在：${secretId}`, recovery: '刷新 Secret 列表' } } });
      if (payload.expected_version !== current.version) return route.fulfill({ status: 409, json: { detail: { code: 'SECRET_VERSION_CONFLICT', detail: 'Secret version 已变化', recovery: '刷新后重新输入新值' } } });
      if (!current.present || !secretValues.has(secretId)) return route.fulfill({ status: 404, json: { detail: { code: 'SECRET_MISSING', detail: '系统凭据值缺失', recovery: '删除 metadata 后重新创建 Secret' } } });
      if (typeof payload.value !== 'string' || !payload.value) return route.fulfill({ status: 422, json: { detail: { code: 'INVALID_SECRET_UPDATE', detail: '新值不能为空', recovery: '输入新值后重试' } } });
      secretValues.set(secretId, payload.value);
      const updated: SecretMetadata = { ...current, present: true, version: current.version + 1, updated_at: generatedAt };
      secrets = secrets.map((secret) => secret.id === secretId ? updated : secret);
      return route.fulfill({ json: updated });
    }
    if (secretMatch && method === 'DELETE') {
      const secretId = decodeURIComponent(secretMatch[1]!);
      const current = secrets.find((secret) => secret.id === secretId);
      if (!current) return route.fulfill({ status: 404, json: { detail: { code: 'SECRET_NOT_FOUND', detail: `Secret 不存在：${secretId}`, recovery: '刷新 Secret 列表' } } });
      if (url.searchParams.get('expected_version') !== String(current.version)) return route.fulfill({ status: 409, json: { detail: { code: 'SECRET_VERSION_CONFLICT', detail: 'Secret version 已变化', recovery: '刷新后重试' } } });
      const referenced = workspaces.some((workspace) => workspace.services.some((service) => service.environment.some((binding) => binding.source === 'secret-store' && binding.reference === secretId) || service.connection_profiles.some((profile) => profile.kind === 'postgres' ? profile.secret_ref === secretId : profile.access_key_secret_ref === secretId || profile.secret_key_secret_ref === secretId)));
      if (referenced) return route.fulfill({ status: 409, json: { detail: { code: 'SECRET_IN_USE', detail: `Secret ${secretId} 正被 Workspace 使用`, recovery: '先移除工作区引用并保存，再重试删除' } } });
      secrets = secrets.filter((secret) => secret.id !== secretId);
      secretValues.delete(secretId);
      return route.fulfill({ status: 204, body: '' });
    }
    if (method === 'GET' && path === '/api/v1/repositories') return route.fulfill({ json: { repositories } });
    if (method === 'POST' && path === '/api/v1/repositories/import') {
      const imported = { ...repositoryFixture, id: 'repository-imported', name: 'imported-repository', path: (body as { path: string }).path, dirty: false };
      repositories = [...repositories, imported];
      return route.fulfill({ status: 201, json: imported });
    }
    if (method === 'POST' && path === '/api/v1/repositories/clone') {
      const payload = body as { destination_parent: string; directory_name: string | null; url: string };
      const cloned = { ...repositoryFixture, id: 'repository-cloned', name: payload.directory_name ?? 'cloned-repository', path: `${payload.destination_parent}\\${payload.directory_name ?? 'cloned-repository'}`, origin_url: payload.url, dirty: false };
      repositories = [...repositories, cloned];
      return route.fulfill({ status: 201, json: cloned });
    }
    if (method === 'POST' && /^\/api\/v1\/repositories\/[^/]+\/update$/.test(path)) return route.fulfill({ json: repositoryFixture });
    if (method === 'GET' && path === '/api/v1/workspaces') return route.fulfill({ json: { workspaces } });
    if (method === 'POST' && path === '/api/v1/workspaces') {
      const payload = body as WorkspaceRecord;
      if (!validWorkspacePayload(payload)) return route.fulfill({ status: 422, json: { detail: { code: 'WORKSPACE_TARGET_INVALID', detail: 'Workspace target、命令或连接 profile 不完整', recovery: '完成服务配置后重试' } } });
      const created = { ...(body as object), id: 'workspace-created', revision: 1, created_at: generatedAt, updated_at: generatedAt } as WorkspaceRecord;
      workspaces = [...workspaces, created];
      return route.fulfill({ status: 201, json: created });
    }
    const workspaceMatch = path.match(/^\/api\/v1\/workspaces\/([^/]+)$/);
    if (workspaceMatch && method === 'GET') return route.fulfill({ json: workspaces.find((workspace) => workspace.id === workspaceMatch[1]) });
    if (workspaceMatch && method === 'PUT') {
      const current = workspaces.find((workspace) => workspace.id === workspaceMatch[1]) ?? workspaceFixture;
      const payload = body as WorkspaceRecord & { expected_revision?: number };
      if (payload.expected_revision !== current.revision) return route.fulfill({ status: 409, json: { detail: { code: 'WORKSPACE_REVISION_CONFLICT', detail: '工作区 revision 不匹配', recovery: '刷新工作区后重试' } } });
      if (!validWorkspacePayload(payload)) return route.fulfill({ status: 422, json: { detail: { code: 'WORKSPACE_TARGET_INVALID', detail: 'Workspace target、命令或连接 profile 不完整', recovery: '补全服务配置后重试' } } });
      const workspacePayload = { name: payload.name, mode: payload.mode, services: payload.services, bindings: payload.bindings };
      const updated: WorkspaceRecord = { ...current, ...workspacePayload, revision: current.revision + 1, updated_at: generatedAt };
      workspaces = workspaces.map((workspace) => workspace.id === updated.id ? updated : workspace);
      return route.fulfill({ json: updated });
    }
    if (workspaceMatch && method === 'DELETE') {
      const current = workspaces.find((workspace) => workspace.id === workspaceMatch[1]);
      const expectedRevision = url.searchParams.get('expected_revision');
      if (!current || expectedRevision !== String(current.revision)) {
        return route.fulfill({ status: 409, json: { detail: { code: 'WORKSPACE_REVISION_CONFLICT', detail: '工作区 revision 不匹配', recovery: '刷新工作区后重试' } } });
      }
      workspaces = workspaces.filter((workspace) => workspace.id !== workspaceMatch[1]);
      return route.fulfill({ status: 204, body: '' });
    }
    if (method === 'POST' && /^\/api\/v1\/workspaces\/[^/]+\/plans$/.test(path)) {
      const workspaceId = path.split('/')[4];
      const workspace = workspaces.find((record) => record.id === workspaceId);
      if (!workspace || (body as { expected_revision?: unknown })?.expected_revision !== workspace.revision || !workspace.services.every(validTarget)) return route.fulfill({ status: 409, json: { detail: { code: 'WORKSPACE_REVISION_CONFLICT', detail: '工作区配置已变化', recovery: '刷新工作区后重新预检' } } });
      return route.fulfill({ status: 201, json: { ...planFixture, workspace_id: workspace.id, workspace_revision: workspace.revision } });
    }
    if (method === 'GET' && path === '/api/v1/runs') return route.fulfill({ json: { runs } });
    if (method === 'POST' && path === '/api/v1/runs') {
      const created: RunRecord = { ...runningRunFixture, mode: 'development', id: 'run-created-001', plan_id: (body as { plan_id: string }).plan_id, status: 'queued', current_step: null };
      runs = [created, ...runs];
      return route.fulfill({ status: 201, json: created });
    }
    const eventsMatch = path.match(/^\/api\/v1\/runs\/([^/]+)\/events$/);
    if (eventsMatch && method === 'GET') return route.fulfill({ json: { ...eventsFixture, events: eventsFixture.events.map((event) => ({ ...event, run_id: eventsMatch[1] })) } });
    const runActionMatch = path.match(/^\/api\/v1\/runs\/([^/]+)\/(cancel|retry)$/);
    if (runActionMatch && method === 'POST') {
      const current = (runs.find((run) => run.id === runActionMatch[1]) ?? runningRunFixture) as RunRecord;
      if (runActionMatch[2] === 'retry') {
        const idempotencyKey = body && typeof body === 'object' && 'idempotency_key' in body
          ? body.idempotency_key
          : null;
        if (typeof idempotencyKey !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(idempotencyKey)) {
          return route.fulfill({ status: 422, json: { detail: { code: 'INVALID_IDEMPOTENCY_KEY', detail: '重试缺少合法幂等键', recovery: '重新提交重试' } } });
        }
      }
      const next: RunRecord = runActionMatch[2] === 'cancel'
        ? { ...current, status: 'cancelled', finished_at: generatedAt }
        : { ...current, id: 'run-retry-001', status: 'queued', current_step: null, retry_of: current.id, finished_at: null };
      runs = runActionMatch[2] === 'cancel' ? runs.map((run) => run.id === current.id ? next : run) : [next, ...runs];
      return route.fulfill({ status: runActionMatch[2] === 'retry' ? 201 : 200, json: next });
    }
    const runMatch = path.match(/^\/api\/v1\/runs\/([^/]+)$/);
    if (runMatch && method === 'GET') return route.fulfill({ json: runs.find((run) => run.id === runMatch[1]) ?? runningRunFixture });
    if (method === 'POST' && path === '/api/v1/runtime/cleanup-previews') return route.fulfill({ status: 201, json: cleanupPreview });
    const cleanupApplyMatch = path.match(/^\/api\/v1\/runtime\/cleanup-previews\/([^/]+)\/apply$/);
    if (method === 'POST' && cleanupApplyMatch) {
      const payload = body as CleanupApplyRequest;
      const resourceIds = Array.isArray(payload?.resource_ids) ? payload.resource_ids : [];
      const configuredResults = options.cleanupResults ?? resourceIds.map((id): CleanupResultItem => ({
        resource_id: id,
        resource_name: cleanupPreview.items.find((item) => item.resource.id === id)?.resource.name ?? id,
        status: 'removed',
        reason_code: null,
        detail: null,
      }));
      const invalidRequest = payload?.preview_id !== cleanupApplyMatch[1]
        || payload.preview_id !== cleanupPreview.id
        || resourceIds.length === 0
        || configuredResults.some((result) => !resourceIds.includes(result.resource_id));
      if (invalidRequest) {
        return route.fulfill({ status: 422, json: { detail: { code: 'INVALID_CLEANUP_APPLY', detail: '清理请求与预览或结果 fixture 不匹配', recovery: '重新生成清理预览' } } });
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

  return { writes, reorderWorkspaces, reorderRuns, getSecrets: () => secrets, getSecretValue: (secretId: string) => secretValues.get(secretId), getDeployments: () => deployments, getManagedResources: () => managedResources };
}
