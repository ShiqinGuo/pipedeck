import type { components } from '@/api/schema';

type RepositoryRecord = components['schemas']['RepositoryRecord'];

/**
 * 等价 CLI 命令构造:GUI 是 CLI 的壳,每个操作面板页脚展示等价 pipedeck 命令。
 * 命令与实际执行严格同步——GUI 写操作与 CLI 走同一控制 API。
 */
export const cli = {
  status: () => 'pipedeck status',
  reposList: () => 'pipedeck repos list',
  reposImport: (path: string) => `pipedeck repos import "${path}"`,
  reposClone: (url: string, destinationParent: string) =>
    `pipedeck repos clone "${url}" --into "${destinationParent}"`,
  reposUpdate: (repository: Pick<RepositoryRecord, 'name'>) => `pipedeck repos update "${repository.name}"`,
  reposCheckout: (repository: Pick<RepositoryRecord, 'name'>, ref: string) =>
    `pipedeck repos checkout "${repository.name}" "${ref}"`,
  pipelineList: (repository: Pick<RepositoryRecord, 'name'>) => `pipedeck pipeline list "${repository.name}"`,
  pipelineRefresh: (repository: Pick<RepositoryRecord, 'name'>) =>
    `pipedeck pipeline list "${repository.name}" --refresh-includes`,
  run: (repository: Pick<RepositoryRecord, 'name'>) => `pipedeck run "${repository.name}" --wait`,
  runs: () => 'pipedeck runs list',
  logs: (runId: string) => `pipedeck logs "${runId}"`,
  cancel: (runId: string) => `pipedeck runs cancel "${runId}"`,
  retry: (runId: string) => `pipedeck runs retry "${runId}"`,
  envList: (workspaceId: string) => `pipedeck env list "${workspaceId}"`,
  envCreate: (workspaceId: string, ref: string) => `pipedeck env create "${workspaceId}" "${ref}"`,
  envRemove: (environmentId: string) => `pipedeck env remove "${environmentId}"`,
  workspaces: () => 'pipedeck workspaces list',
  secrets: () => 'pipedeck secrets list',
  doctor: () => 'pipedeck doctor',
  serve: () => 'pipedeck serve',
};
