import type {
  CatalogResponse,
  CleanupApplyRequest,
  CleanupApplyResponse,
  CleanupPreviewResponse,
  DeploymentRevisionListResponse,
  DeploymentRevisionView,
  ManagedMiddlewareProvisionRequest,
  ManagedResourceListResponse,
  ManagedResourceRecord,
  OverviewResponse,
  RepositoryCloneRequest,
  RepositoryImportRequest,
  RepositoryListResponse,
  RepositoryRecord,
  RunCreateRequest,
  RunEventListResponse,
  RunListResponse,
  RunRecord,
  RuntimeResponse,
  RuntimeProcessListResponse,
  SecretCreateRequest,
  SecretListResponse,
  SecretMetadata,
  SecretUpdateRequest,
  WorkspaceInput,
  WorkspaceListResponse,
  WorkspacePlanRequest,
  WorkspacePlanResponse,
  WorkspaceRecord,
  WorkspaceUpdateRequest,
} from './types';
import { invoke } from '@tauri-apps/api/core';

const configuredApiBaseUrl: unknown = import.meta.env.VITE_API_BASE_URL;
const API_BASE_URL =
  typeof configuredApiBaseUrl === 'string' && configuredApiBaseUrl.length > 0
    ? configuredApiBaseUrl
    : 'http://127.0.0.1:7421';
let apiTokenPromise: Promise<string> | null = null;

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
  const rawDetail = payload && typeof payload === 'object' && 'detail' in payload
    ? payload.detail
    : payload;
  const detail = rawDetail && typeof rawDetail === 'object' ? rawDetail as ErrorPayload : null;
  const message = typeof rawDetail === 'string'
    ? rawDetail
    : detail?.detail ?? detail?.title ?? `本地控制服务返回 ${response.status}`;
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
    throw new ApiError('本地控制服务暂时不可用', null, 'CONTROL_SERVICE_UNAVAILABLE', '确认 TripGuru Local 控制服务正在运行');
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function writeJson<T>(path: string, method: 'POST' | 'PUT' | 'DELETE', body?: unknown): Promise<T> {
  const apiToken = await resolveApiToken();
  if (!apiToken) {
    throw new ApiError('未配置本地写入令牌', null, 'API_TOKEN_NOT_CONFIGURED', '设置 VITE_API_TOKEN 后重启客户端');
  }
  return requestJson<T>(path, {
    method,
    headers: {
      ...(body === undefined ? {} : { 'content-type': 'application/json' }),
      'x-tripguru-local-token': apiToken,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  catalog: () => requestJson<CatalogResponse>('/api/v1/catalog/projects'),
  overview: () => requestJson<OverviewResponse>('/api/v1/overview'),
  runtime: () => requestJson<RuntimeResponse>('/api/v1/runtime/resources'),
  runtimeProcesses: () => requestJson<RuntimeProcessListResponse>('/api/v1/runtime/processes'),
  managedMiddleware: (workspaceId?: string) => requestJson<ManagedResourceListResponse>(`/api/v1/managed-middleware${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''}`),
  provisionManagedMiddleware: (payload: ManagedMiddlewareProvisionRequest) =>
    writeJson<ManagedResourceRecord>('/api/v1/managed-middleware', 'POST', payload),
  reconcileManagedMiddleware: (resourceId: string) =>
    writeJson<ManagedResourceRecord>(`/api/v1/managed-middleware/${encodeURIComponent(resourceId)}/reconcile`, 'POST'),
  deleteManagedMiddleware: (resourceId: string) =>
    writeJson<ManagedResourceRecord>(`/api/v1/managed-middleware/${encodeURIComponent(resourceId)}`, 'DELETE'),
  deployments: (filters: { workspace_id?: string; target_id?: string } = {}) => {
    const query = new URLSearchParams(filters).toString();
    return requestJson<DeploymentRevisionListResponse>(`/api/v1/deployments${query ? `?${query}` : ''}`);
  },
  reconcileDeployment: (revisionId: string) =>
    writeJson<DeploymentRevisionView>(`/api/v1/deployments/${encodeURIComponent(revisionId)}/reconcile`, 'POST'),

  secrets: () => requestJson<SecretListResponse>('/api/v1/secrets'),
  createSecret: (payload: SecretCreateRequest) =>
    writeJson<SecretMetadata>('/api/v1/secrets', 'POST', payload),
  updateSecret: (secretId: string, payload: SecretUpdateRequest) =>
    writeJson<SecretMetadata>(`/api/v1/secrets/${encodeURIComponent(secretId)}`, 'PUT', payload),
  deleteSecret: (secretId: string, expectedVersion: number) =>
    writeJson<void>(
      `/api/v1/secrets/${encodeURIComponent(secretId)}?expected_version=${expectedVersion}`,
      'DELETE',
    ),

  repositories: () => requestJson<RepositoryListResponse>('/api/v1/repositories'),
  importRepository: (payload: RepositoryImportRequest) =>
    writeJson<RepositoryRecord>('/api/v1/repositories/import', 'POST', payload),
  cloneRepository: (payload: RepositoryCloneRequest) =>
    writeJson<RepositoryRecord>('/api/v1/repositories/clone', 'POST', payload),
  updateRepository: (repositoryId: string) =>
    writeJson<RepositoryRecord>(`/api/v1/repositories/${encodeURIComponent(repositoryId)}/update`, 'POST'),

  workspaces: () => requestJson<WorkspaceListResponse>('/api/v1/workspaces'),
  workspace: (workspaceId: string) =>
    requestJson<WorkspaceRecord>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}`),
  createWorkspace: (payload: WorkspaceInput) => writeJson<WorkspaceRecord>('/api/v1/workspaces', 'POST', payload),
  updateWorkspace: (workspaceId: string, payload: WorkspaceUpdateRequest) =>
    writeJson<WorkspaceRecord>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}`, 'PUT', payload),
  deleteWorkspace: (workspaceId: string, expectedRevision: number) =>
    writeJson<void>(
      `/api/v1/workspaces/${encodeURIComponent(workspaceId)}?expected_revision=${expectedRevision}`,
      'DELETE',
    ),
  createWorkspacePlan: (workspaceId: string, payload: WorkspacePlanRequest) =>
    writeJson<WorkspacePlanResponse>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/plans`, 'POST', payload),

  runs: () => requestJson<RunListResponse>('/api/v1/runs'),
  run: (runId: string) => requestJson<RunRecord>(`/api/v1/runs/${encodeURIComponent(runId)}`),
  runEvents: (runId: string, after = 0) =>
    requestJson<RunEventListResponse>(`/api/v1/runs/${encodeURIComponent(runId)}/events?after=${after}`),
  createRun: (payload: RunCreateRequest) => writeJson<RunRecord>('/api/v1/runs', 'POST', payload),
  cancelRun: (runId: string) => writeJson<RunRecord>(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`, 'POST'),
  retryRun: (runId: string) =>
    writeJson<RunRecord>(`/api/v1/runs/${encodeURIComponent(runId)}/retry`, 'POST', {
      idempotency_key: crypto.randomUUID(),
    }),

  createCleanupPreview: () => writeJson<CleanupPreviewResponse>('/api/v1/runtime/cleanup-previews', 'POST'),
  applyCleanup: (previewId: string, payload: CleanupApplyRequest) =>
    writeJson<CleanupApplyResponse>(`/api/v1/runtime/cleanup-previews/${encodeURIComponent(previewId)}/apply`, 'POST', payload),
};
