import { invoke } from '@tauri-apps/api/core';

import type { components } from './schema';

/** 本地控制服务 API 基址:所有控制接口只绑定 loopback */
const configuredApiBaseUrl: unknown = import.meta.env.VITE_API_BASE_URL;
export const API_BASE_URL =
  typeof configuredApiBaseUrl === 'string' && configuredApiBaseUrl.length > 0
    ? configuredApiBaseUrl
    : 'http://127.0.0.1:7421';

let apiTokenPromise: Promise<string> | null = null;

/** token 来源:Tauri sidecar 生命周期 token 或本地开发 VITE_API_TOKEN */
export function resolveApiToken(): Promise<string> {
  if (!apiTokenPromise) {
    apiTokenPromise = (async () => {
      const tauriWindow = window as Window & { __TAURI_INTERNALS__?: unknown };
      if (tauriWindow.__TAURI_INTERNALS__) return (await invoke<string>('local_api_token')).trim();
      const configuredApiToken: unknown = import.meta.env.VITE_API_TOKEN;
      return typeof configuredApiToken === 'string' ? configuredApiToken.trim() : '';
    })();
  }
  return apiTokenPromise;
}

interface ErrorPayload {
  code?: string;
  title?: string;
  detail?: string;
  recovery?: string;
}

/** 后端 ApiProblem(problem code + recovery 文案)的强类型错误 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly code: string | null = null,
    readonly recovery: string | null = null,
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  const rawDetail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : payload;
  const detail = rawDetail && typeof rawDetail === 'object' ? (rawDetail as ErrorPayload) : null;
  const message =
    typeof rawDetail === 'string'
      ? rawDetail
      : (detail?.detail ?? detail?.title ?? `本地控制服务返回 ${response.status}`);
  return new ApiError(
    message,
    response.status,
    detail?.code ?? null,
    detail?.recovery ?? null,
    response.headers.get('x-request-id'),
  );
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch {
    throw new ApiError('本地控制服务暂时不可用', null, 'CONTROL_SERVICE_UNAVAILABLE', '确认 Pipedeck 控制服务正在运行');
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** 写操作统一注入 x-pipedeck-token 请求头 */
async function writeJson<T>(path: string, method: 'POST' | 'PUT' | 'DELETE', body?: unknown): Promise<T> {
  const apiToken = await resolveApiToken();
  if (!apiToken) {
    throw new ApiError('未配置本地写入令牌', null, 'API_TOKEN_NOT_CONFIGURED', '设置 VITE_API_TOKEN 后重启客户端');
  }
  return requestJson<T>(path, {
    method,
    headers: {
      ...(body === undefined ? {} : { 'content-type': 'application/json' }),
      'x-pipedeck-token': apiToken,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

type Schemas = components['schemas'];

function withQuery(path: string, params: Record<string, string | number | boolean | undefined>) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `${path}?${encoded}` : path;
}

/** 全部控制面端点:与 pipedeck CLI 消费同一 API */
export const api = {
  // —— 会话与概览 ——
  session: () => requestJson<Schemas['SessionResponse']>('/api/v1/session'),
  overview: () => requestJson<Schemas['OverviewResponse']>('/api/v1/overview'),
  catalog: () => requestJson<Schemas['CatalogResponse']>('/api/v1/catalog/projects'),

  // —— 运行时与资源 ——
  runtime: () => requestJson<Schemas['RuntimeResponse']>('/api/v1/runtime/resources'),
  runtimeProcesses: () => requestJson<Schemas['RuntimeProcessListResponse']>('/api/v1/runtime/processes'),
  managedMiddleware: (workspaceId?: string) =>
    requestJson<Schemas['ManagedResourceListResponse']>(withQuery('/api/v1/managed-middleware', { workspace_id: workspaceId })),
  provisionManagedMiddleware: (payload: Schemas['ManagedMiddlewareProvisionRequest']) =>
    writeJson<Schemas['ManagedResourceRecord']>('/api/v1/managed-middleware', 'POST', payload),
  reconcileManagedMiddleware: (resourceId: string) =>
    writeJson<Schemas['ManagedResourceRecord']>(`/api/v1/managed-middleware/${encodeURIComponent(resourceId)}/reconcile`, 'POST'),
  deleteManagedMiddleware: (resourceId: string) =>
    writeJson<Schemas['ManagedResourceRecord']>(`/api/v1/managed-middleware/${encodeURIComponent(resourceId)}`, 'DELETE'),

  // —— Secrets ——
  secrets: () => requestJson<Schemas['SecretListResponse']>('/api/v1/secrets'),
  createSecret: (payload: Schemas['SecretCreateRequest']) => writeJson<Schemas['SecretMetadata']>('/api/v1/secrets', 'POST', payload),
  updateSecret: (secretId: string, payload: Schemas['SecretUpdateRequest']) =>
    writeJson<Schemas['SecretMetadata']>(`/api/v1/secrets/${encodeURIComponent(secretId)}`, 'PUT', payload),
  deleteSecret: (secretId: string, expectedVersion: number) =>
    writeJson<void>(withQuery(`/api/v1/secrets/${encodeURIComponent(secretId)}`, { expected_version: expectedVersion }), 'DELETE'),

  // —— 仓库 ——
  repositories: () => requestJson<Schemas['RepositoryListResponse']>('/api/v1/repositories'),
  importRepository: (payload: Schemas['RepositoryImportRequest']) =>
    writeJson<Schemas['RepositoryRecord']>('/api/v1/repositories/import', 'POST', payload),
  cloneRepository: (payload: Schemas['RepositoryCloneRequest']) =>
    writeJson<Schemas['RepositoryRecord']>('/api/v1/repositories/clone', 'POST', payload),
  updateRepository: (repositoryId: string) =>
    writeJson<Schemas['RepositoryRecord']>(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/update`, 'POST'),
  checkoutRepository: (repositoryId: string, payload: Schemas['RepositoryCheckoutRequest']) =>
    writeJson<Schemas['RepositoryRecord']>(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/checkout`, 'POST', payload),
  repositoryPipelineFiles: (repositoryId: string) =>
    requestJson<Schemas['RepositoryPipelineFileListResponse']>(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/pipeline-files`),
  readPipelineFile: (repositoryId: string, path: string) =>
    requestJson<Schemas['RepositoryPipelineFileContentResponse']>(withQuery(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/pipeline-file`, { path })),
  savePipelineFile: (repositoryId: string, payload: Schemas['RepositoryPipelineFileSaveRequest']) =>
    writeJson<Schemas['RepositoryRecord']>(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/pipeline-file`, 'PUT', payload),
  selectPipelineFile: (repositoryId: string, payload: Schemas['RepositoryPipelineFileSelectRequest']) =>
    writeJson<Schemas['RepositoryRecord']>(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/pipeline-file/selection`, 'PUT', payload),

  // —— 管道 ——
  pipelinePreview: (repositoryId: string, refresh = false) =>
    requestJson<Schemas['GitlabPipelinePreview']>(withQuery(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/pipeline`, { refresh })),
  createPipelinePlan: (repositoryId: string, payload: Schemas['PipelinePlanRequest']) =>
    writeJson<Schemas['WorkspacePlanResponse']>(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/pipeline/plan`, 'POST', payload),

  // —— 工作区 ——
  workspaces: () => requestJson<Schemas['WorkspaceListResponse']>('/api/v1/workspaces'),
  workspace: (workspaceId: string) => requestJson<Schemas['WorkspaceRecord']>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}`),
  createWorkspace: (payload: Schemas['WorkspaceInput']) => writeJson<Schemas['WorkspaceRecord']>('/api/v1/workspaces', 'POST', payload),
  updateWorkspace: (workspaceId: string, payload: Schemas['WorkspaceUpdateRequest']) =>
    writeJson<Schemas['WorkspaceRecord']>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}`, 'PUT', payload),
  deleteWorkspace: (workspaceId: string, expectedRevision: number) =>
    writeJson<void>(withQuery(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}`, { expected_revision: expectedRevision }), 'DELETE'),
  createWorkspacePlan: (workspaceId: string, payload: Schemas['WorkspacePlanCreateRequest']) =>
    writeJson<Schemas['WorkspacePlanResponse']>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/plans`, 'POST', payload),

  // —— Environments(worktree 并存) ——
  environments: (workspaceId: string) =>
    requestJson<Schemas['EnvironmentListResponse']>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/environments`),
  createEnvironment: (workspaceId: string, payload: Schemas['EnvironmentCreateRequest']) =>
    writeJson<Schemas['EnvironmentRecord']>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/environments`, 'POST', payload),
  deleteEnvironment: (environmentId: string) =>
    writeJson<Schemas['EnvironmentRecord']>(`/api/v1/environments/${encodeURIComponent(environmentId)}`, 'DELETE'),

  // —— Runs ——
  runs: (workspaceId?: string) =>
    requestJson<Schemas['RunListResponse']>(withQuery('/api/v1/runs', { workspace_id: workspaceId })),
  run: (runId: string) => requestJson<Schemas['RunRecord']>(`/api/v1/runs/${encodeURIComponent(runId)}`),
  runEvents: (runId: string, after = 0) =>
    requestJson<Schemas['RunEventListResponse']>(withQuery(`/api/v1/runs/${encodeURIComponent(runId)}/events`, { after })),
  createRun: (payload: Schemas['RunCreateRequest']) => writeJson<Schemas['RunRecord']>('/api/v1/runs', 'POST', payload),
  cancelRun: (runId: string) => writeJson<Schemas['RunRecord']>(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`, 'POST'),
  retryRun: (runId: string) =>
    writeJson<Schemas['RunRecord']>(`/api/v1/runs/${encodeURIComponent(runId)}/retry`, 'POST', { idempotency_key: crypto.randomUUID() } satisfies Schemas['RunRetryRequest']),

  // —— Deployments(runtime 状态) ——
  deployments: (filters: { workspace_id?: string; target_id?: string } = {}) =>
    requestJson<Schemas['DeploymentRevisionListResponse']>(
      withQuery('/api/v1/deployments', {
        workspace_id: filters.workspace_id,
        target_id: filters.target_id,
      }),
    ),
  reconcileDeployment: (revisionId: string) =>
    writeJson<Schemas['DeploymentRevisionView']>(`/api/v1/deployments/${encodeURIComponent(revisionId)}/reconcile`, 'POST'),

  // —— 清理 ——
  createCleanupPreview: () => writeJson<Schemas['CleanupPreviewResponse']>('/api/v1/runtime/cleanup-previews', 'POST'),
  applyCleanup: (previewId: string, payload: Schemas['CleanupApplyRequest']) =>
    writeJson<Schemas['CleanupApplyResponse']>(`/api/v1/runtime/cleanup-previews/${encodeURIComponent(previewId)}/apply`, 'POST', payload),
};
