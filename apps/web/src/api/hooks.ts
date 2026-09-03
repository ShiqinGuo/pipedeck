import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query';

import type { components } from './schema';
import { api, resolveApiToken } from './client';
import { queryKeys } from './queryKeys';

type Schemas = components['schemas'];

/** 统一的 query 封装:15s staleTime、窗口聚焦不刷新 */
type ApiQueryOptions<T> = Pick<UseQueryOptions<T>, 'queryKey' | 'queryFn'> & {
  enabled?: boolean;
  staleTime?: number;
  refetchInterval?: number | false;
  retry?: boolean | number;
  refetchOnWindowFocus?: boolean;
  initialData?: T;
};

export function useApiQuery<T>(options: ApiQueryOptions<T>) {
  return useQuery({
    staleTime: 15_000,
    refetchOnWindowFocus: false,
    ...options,
  });
}

/** 本地写入令牌:GUI 全部写操作依赖它 */
export function useApiToken() {
  const query = useApiQuery<string>({ queryKey: ['api-token'], queryFn: resolveApiToken, staleTime: Infinity, retry: false });
  return {
    token: query.data ?? '',
    configured: Boolean(query.data),
    loading: query.isLoading,
    error: query.error,
    disabledReason: query.isLoading
      ? '正在读取本地写入令牌'
      : query.isError
        ? '无法读取本地写入令牌'
        : query.data
          ? null
          : '未配置本地写入令牌',
  };
}

export function useSession() {
  return useApiQuery<Schemas['SessionResponse']>({ queryKey: queryKeys.session, queryFn: api.session });
}

export function useOverview() {
  return useApiQuery<Schemas['OverviewResponse']>({ queryKey: queryKeys.overview, queryFn: api.overview, refetchInterval: 8000 });
}

export function useCatalog() {
  return useApiQuery<Schemas['CatalogResponse']>({ queryKey: queryKeys.catalog, queryFn: api.catalog });
}

export function useRepositories() {
  return useApiQuery<Schemas['RepositoryListResponse']>({ queryKey: queryKeys.repositories, queryFn: () => api.repositories() });
}

export function usePipelineFiles(repositoryId: string) {
  return useApiQuery<Schemas['RepositoryPipelineFileListResponse']>({
    queryKey: queryKeys.pipelineFiles(repositoryId),
    queryFn: () => api.repositoryPipelineFiles(repositoryId),
    enabled: Boolean(repositoryId),
    retry: false,
  });
}

export function usePipelineFileContent(repositoryId: string, path: string | null) {
  return useApiQuery<Schemas['RepositoryPipelineFileContentResponse']>({
    queryKey: queryKeys.pipelineFileContent(repositoryId, path ?? ''),
    queryFn: () => api.readPipelineFile(repositoryId, path as string),
    enabled: Boolean(repositoryId && path),
    retry: false,
  });
}

export function useSavePipelineFile() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ repositoryId, payload }: { repositoryId: string; payload: Schemas['RepositoryPipelineFileSaveRequest'] }) =>
      api.savePipelineFile(repositoryId, payload),
    onSuccess: (_record, { repositoryId, payload }) => {
      invalidate([
        queryKeys.repositories,
        queryKeys.pipelineFiles(repositoryId),
        queryKeys.pipelineFileContent(repositoryId, payload.path),
        ['pipeline-preview', repositoryId],
      ]);
    },
  });
}

export function useSelectPipelineFile() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ repositoryId, pipelineFile }: { repositoryId: string; pipelineFile: string }) =>
      api.selectPipelineFile(repositoryId, { pipeline_file: pipelineFile }),
    onSuccess: (_record, { repositoryId }) => {
      invalidate([
        queryKeys.repositories,
        queryKeys.pipelineFiles(repositoryId),
        ['pipeline-preview', repositoryId],
      ]);
    },
  });
}

export function useRuntime() {
  return useApiQuery<Schemas['RuntimeResponse']>({ queryKey: queryKeys.runtime, queryFn: api.runtime });
}

export function useRuntimeProcesses() {
  return useApiQuery<Schemas['RuntimeProcessListResponse']>({ queryKey: queryKeys.runtimeProcesses, queryFn: api.runtimeProcesses, refetchInterval: 4000 });
}

export function useManagedMiddleware(workspaceId?: string) {
  return useApiQuery<Schemas['ManagedResourceListResponse']>({ queryKey: queryKeys.managedMiddleware, queryFn: () => api.managedMiddleware(workspaceId) });
}

export function useSecrets() {
  return useApiQuery<Schemas['SecretListResponse']>({ queryKey: queryKeys.secrets, queryFn: api.secrets });
}

export function useWorkspaces() {
  return useApiQuery<Schemas['WorkspaceListResponse']>({ queryKey: queryKeys.workspaces, queryFn: api.workspaces });
}

export function useWorkspace(workspaceId: string) {
  return useApiQuery<Schemas['WorkspaceRecord']>({
    queryKey: queryKeys.workspace(workspaceId),
    queryFn: () => api.workspace(workspaceId),
    enabled: Boolean(workspaceId),
  });
}

export function useEnvironments(workspaceId: string) {
  return useApiQuery<Schemas['EnvironmentListResponse']>({
    queryKey: queryKeys.environments(workspaceId),
    queryFn: () => api.environments(workspaceId),
    enabled: Boolean(workspaceId),
  });
}

export function useRuns(workspaceId?: string, refetchInterval = 4000) {
  return useApiQuery<Schemas['RunListResponse']>({
    queryKey: queryKeys.runs(workspaceId),
    queryFn: () => api.runs(workspaceId),
    refetchInterval,
  });
}

export function useRun(runId: string) {
  const base = useApiQuery<Schemas['RunRecord']>({
    queryKey: queryKeys.run(runId),
    queryFn: () => api.run(runId),
    enabled: Boolean(runId),
  });
  // 运行中 Run 1.5s 轮询;结束后降为静态
  const active = base.data ? base.data.status === 'queued' || base.data.status === 'running' : false;
  return useQuery({
    queryKey: queryKeys.run(runId),
    queryFn: () => api.run(runId),
    enabled: Boolean(runId),
    staleTime: active ? 1_000 : 15_000,
    refetchInterval: active ? 1_500 : false,
    refetchOnWindowFocus: false,
    initialData: base.data,
  });
}

/** 增量拉取 run 事件:after 之前的事件不再重复传输 */
export function useRunEvents(runId: string) {
  const base = useQuery({
    queryKey: ['runs', 'detail', runId, 'events-stream'],
    queryFn: () => api.runEvents(runId, 0),
    enabled: Boolean(runId),
    staleTime: 1_000,
    refetchInterval: 1_500,
    refetchOnWindowFocus: false,
  });
  return base;
}

export function useDeployments(filters: { workspace_id?: string; target_id?: string }) {
  return useApiQuery<Schemas['DeploymentRevisionListResponse']>({
    queryKey: queryKeys.deployments(filters),
    queryFn: () => api.deployments(filters),
  });
}

export function usePipelinePreview(repositoryId: string, refresh: boolean) {
  return useApiQuery<Schemas['GitlabPipelinePreview']>({
    queryKey: queryKeys.pipelinePreview(repositoryId, refresh),
    queryFn: () => api.pipelinePreview(repositoryId, refresh),
    enabled: Boolean(repositoryId),
    retry: false,
  });
}

/** 简单失效助手:写操作完成后刷新相关列表 */
export function useInvalidate() {
  const queryClient = useQueryClient();
  return (keys: readonly (readonly unknown[])[]) => {
    for (const key of keys) void queryClient.invalidateQueries({ queryKey: key });
  };
}

export function useCancelRun() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (runId: string) => api.cancelRun(runId),
    onSuccess: () => invalidate([queryKeys.runs(), ['runs', 'detail']]),
  });
}

export function useRetryRun() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (runId: string) => api.retryRun(runId),
    onSuccess: () => invalidate([queryKeys.runs(), ['runs', 'detail']]),
  });
}

export function useCreateRun() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (payload: Schemas['RunCreateRequest']) => api.createRun(payload),
    onSuccess: () => invalidate([queryKeys.runs(), queryKeys.overview]),
  });
}
