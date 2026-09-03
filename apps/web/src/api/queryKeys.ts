/**
 * TanStack Query 键空间约定:
 * - 列表键放在最前,便于 invalidateQueries 前缀匹配
 * - 轮询节奏:运行中 Run 1-2s、列表 3-4s、静态资源手动刷新
 */
export const queryKeys = {
  session: ['session'] as const,
  overview: ['overview'] as const,
  catalog: ['catalog'] as const,
  repositories: ['repositories'] as const,
  pipelinePreview: (repositoryId: string, refresh: boolean) => ['pipeline-preview', repositoryId, refresh] as const,
  runtime: ['runtime'] as const,
  runtimeProcesses: ['runtime-processes'] as const,
  managedMiddleware: ['managed-middleware'] as const,
  secrets: ['secrets'] as const,
  workspaces: ['workspaces'] as const,
  workspace: (workspaceId: string) => ['workspaces', workspaceId] as const,
  environments: (workspaceId: string) => ['workspaces', workspaceId, 'environments'] as const,
  runs: (workspaceId?: string) => (workspaceId ? (['runs', workspaceId] as const) : (['runs'] as const)),
  run: (runId: string) => ['runs', 'detail', runId] as const,
  runEvents: (runId: string, after: number) => ['runs', 'detail', runId, 'events', after] as const,
  deployments: (filters: { workspace_id?: string; target_id?: string }) => ['deployments', filters] as const,
};
