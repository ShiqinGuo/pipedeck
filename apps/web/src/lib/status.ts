import type { components } from '@/api/schema';

type RunStatus = components['schemas']['RunStatus'];
type ManagedResourceStatus = components['schemas']['ManagedResourceRecord']['status'];
type DeploymentRevisionStatus = components['schemas']['DeploymentRevisionView']['status'];

/** Run 状态中文标签 */
export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  queued: '排队中',
  running: '运行中',
  succeeded: '已成功',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
};

/** Run 状态对应的语义色 class */
export const RUN_STATUS_TONES: Record<RunStatus, string> = {
  queued: 'bg-info-soft text-info',
  running: 'bg-info-soft text-info',
  succeeded: 'bg-ok-soft text-ok',
  failed: 'bg-danger-soft text-danger',
  cancelled: 'bg-muted text-muted-foreground',
  interrupted: 'bg-warn-soft text-warn',
};

export function isRunActive(status: RunStatus | undefined | null) {
  return status === 'queued' || status === 'running';
}

/** 托管资源状态中文标签 */
export const MANAGED_STATUS_LABELS: Record<ManagedResourceStatus, string> = {
  planned: '已规划',
  provisioning: '创建中',
  active: '运行中',
  failed: '失败',
  removed: '已移除',
};

/** Deployment revision 状态中文标签 */
export const DEPLOYMENT_STATUS_LABELS: Record<DeploymentRevisionStatus, string> = {
  planned: '已规划',
  building: '构建中',
  applying: '应用中',
  verifying: '验证中',
  active: '活跃',
  superseded: '已替代',
  failed: '失败',
  recovering: '恢复中',
  rolled_back: '已回滚',
  degraded: '需恢复',
};

/** 需要用户介入的 deployment 状态 */
export function isDeploymentDegraded(status: DeploymentRevisionStatus | null | undefined) {
  return status === 'degraded' || status === 'failed';
}

/** 运行事件 kind 标签(i18n key) */
export const RUN_EVENT_KIND_LABELS: Record<string, string> = {
  status: 'lib.runEventKind.status',
  stage: 'lib.runEventKind.stage',
  stdout: 'lib.runEventKind.stdout',
  stderr: 'lib.runEventKind.stderr',
  system: 'lib.runEventKind.system',
};

/** 中间件类型标签(i18n key) */
export const MIDDLEWARE_LABELS: Record<string, string> = {
  postgres: 'lib.middleware.postgres',
  redis: 'lib.middleware.redis',
  elasticsearch: 'lib.middleware.elasticsearch',
  minio: 'lib.middleware.minio',
};
