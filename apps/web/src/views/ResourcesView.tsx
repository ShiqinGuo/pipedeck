import { Activity, Box, CheckCircle2, Container, Database, Eraser, ExternalLink, KeyRound, LockKeyhole, Plus, RefreshCw, RotateCcw, ShieldCheck, TerminalSquare, Trash2, TriangleAlert } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { api } from '../api';
import type { CleanupApplyResponse, CleanupPreviewResponse, DeploymentRevisionStatus, ManagedMiddlewareProvisionRequest, ManagedResourceRecord, ManagedResourceStatus } from '../types';
import { BusyLabel, EmptyState, ErrorState, MIDDLEWARE_LABELS, MiddlewareLogo, Modal, MutationError, StatusDot, SuccessMessage, TokenReason } from '../ui';
import { useApiToken } from '../use-api-token';

const CLEANUP_STATUS_LABELS = {
  removed: '已删除',
  skipped: '已跳过',
  failed: '删除失败',
} as const;

const DEPLOYMENT_STATUS_LABELS: Record<DeploymentRevisionStatus, string> = {
  planned: '已计划', building: '构建中', applying: '应用中', verifying: '验证中', active: '生效', superseded: '已取代', failed: '失败', recovering: '恢复中', rolled_back: '已回滚', degraded: '需恢复',
};
const RECONCILABLE_STATUSES = new Set<DeploymentRevisionStatus>(['planned', 'building', 'applying', 'verifying', 'recovering', 'degraded']);
const MANAGED_STATUS_LABELS: Record<ManagedResourceStatus, string> = {
  planned: '已计划', provisioning: '创建中', active: '运行中', failed: '失败', removed: '已移除',
};
const MANAGED_PENDING_STATUSES = new Set<ManagedResourceStatus>(['planned', 'provisioning']);
const MANAGED_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_-]{0,62}$/;

const emptyProvisionForm = (): ManagedMiddlewareProvisionRequest => ({
  workspace_id: '',
  kind: 'postgres',
  host_port: 55432,
  username: 'postgres',
  database: 'postgres',
  password_secret_ref: '',
});

export function ResourcesView({ onOpenRun }: { onOpenRun: (runId: string) => void }) {
  const queryClient = useQueryClient();
  const token = useApiToken();
  const runtime = useQuery({ queryKey: ['runtime'], queryFn: api.runtime });
  const processes = useQuery({ queryKey: ['runtime-processes'], queryFn: api.runtimeProcesses, refetchInterval: 2500 });
  const deployments = useQuery({ queryKey: ['deployments'], queryFn: () => api.deployments(), refetchInterval: 3500 });
  const managed = useQuery({ queryKey: ['managed-middleware'], queryFn: () => api.managedMiddleware(), refetchInterval: 3500 });
  const workspaces = useQuery({ queryKey: ['workspaces'], queryFn: api.workspaces });
  const secrets = useQuery({ queryKey: ['secrets'], queryFn: api.secrets });
  const [preview, setPreview] = useState<CleanupPreviewResponse | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [result, setResult] = useState<CleanupApplyResponse | null>(null);
  const [provisionOpen, setProvisionOpen] = useState(false);
  const [provisionForm, setProvisionForm] = useState<ManagedMiddlewareProvisionRequest>(emptyProvisionForm);
  const [deleteResource, setDeleteResource] = useState<ManagedResourceRecord | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState('');

  const previewMutation = useMutation({ mutationFn: api.createCleanupPreview, onSuccess: (next) => { setPreview(next); setSelectedIds([]); setConfirmed(false); setResult(null); } });
  const applyMutation = useMutation({ mutationFn: ({ previewId, resourceIds }: { previewId: string; resourceIds: string[] }) => api.applyCleanup(previewId, { preview_id: previewId, resource_ids: resourceIds }), onSuccess: async (next) => { setResult(next); await Promise.all([queryClient.invalidateQueries({ queryKey: ['runtime'] }), queryClient.invalidateQueries({ queryKey: ['overview'] })]); } });
  const reconcileMutation = useMutation({ mutationFn: api.reconcileDeployment, onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['deployments'] }); } });
  const provisionMutation = useMutation({
    mutationFn: api.provisionManagedMiddleware,
    onSuccess: async () => {
      setProvisionOpen(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['managed-middleware'] }),
        queryClient.invalidateQueries({ queryKey: ['runtime'] }),
        queryClient.invalidateQueries({ queryKey: ['overview'] }),
      ]);
    },
  });
  const managedReconcileMutation = useMutation({
    mutationFn: api.reconcileManagedMiddleware,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['managed-middleware'] }),
        queryClient.invalidateQueries({ queryKey: ['runtime'] }),
      ]);
    },
  });
  const managedDeleteMutation = useMutation({
    mutationFn: api.deleteManagedMiddleware,
    onSuccess: async () => {
      setDeleteResource(null);
      setDeleteConfirmation('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['managed-middleware'] }),
        queryClient.invalidateQueries({ queryKey: ['runtime'] }),
        queryClient.invalidateQueries({ queryKey: ['overview'] }),
      ]);
    },
  });

  useEffect(() => { if (!preview) { setSelectedIds([]); setConfirmed(false); setResult(null); } }, [preview]);

  const resources = runtime.data?.resources ?? [];
  const processRecords = processes.data?.processes ?? [];
  const deploymentRecords = deployments.data?.deployments ?? [];
  const managedRecords = managed.data?.resources ?? [];
  const workspaceRecords = workspaces.data?.workspaces ?? [];
  const presentSecrets = (secrets.data?.secrets ?? []).filter((secret) => secret.present);
  const activeDeployments = deploymentRecords.filter((deployment) => deployment.status === 'active').length;
  const healthy = resources.filter((resource) => resource.health === 'healthy' || resource.health === 'running').length;
  const protectedCount = resources.filter((resource) => resource.protected).length;
  const removedCount = result?.results.filter((item) => item.status === 'removed').length ?? 0;
  const skippedCount = result?.results.filter((item) => item.status === 'skipped').length ?? 0;
  const failedCount = result?.results.filter((item) => item.status === 'failed').length ?? 0;
  const refreshing = runtime.isFetching || processes.isFetching || deployments.isFetching || managed.isFetching;
  const refreshAll = () => void Promise.all([runtime.refetch(), processes.refetch(), deployments.refetch(), managed.refetch(), workspaces.refetch(), secrets.refetch()]);
  const provisionError = !provisionForm.workspace_id
    ? '选择工作区'
    : !provisionForm.password_secret_ref
      ? '选择可用的密码 Secret'
      : provisionForm.host_port < 1024 || provisionForm.host_port > 65535
        ? '宿主端口必须在 1024-65535 之间'
        : !MANAGED_NAME_PATTERN.test(provisionForm.username)
          ? '用户名只能包含字母、数字、下划线或连字符，且需以字母或下划线开头'
          : !MANAGED_NAME_PATTERN.test(provisionForm.database)
            ? '数据库名只能包含字母、数字、下划线或连字符，且需以字母或下划线开头'
            : null;
  const provisionOpenDisabledReason = token.disabledReason
    ?? (workspaces.isLoading || secrets.isLoading ? '正在读取工作区与 Secret' : null)
    ?? (workspaces.isError ? '工作区读取失败，请刷新后重试' : null)
    ?? (secrets.isError ? 'Secret 读取失败，请刷新后重试' : null)
    ?? (workspaceRecords.length === 0 ? '请先创建并保存工作区' : null)
    ?? (presentSecrets.length === 0 ? '请先创建可用的密码 Secret' : null);
  const provisionDisabledReason = provisionOpenDisabledReason ?? provisionError;
  const openProvision = () => {
    setProvisionForm({
      ...emptyProvisionForm(),
      workspace_id: workspaceRecords[0]?.id ?? '',
      password_secret_ref: presentSecrets[0]?.id ?? '',
    });
    provisionMutation.reset();
    setProvisionOpen(true);
  };

  return (
    <div className="page-scroll">
      <div className="page-heading compact-heading"><div><span>LOCAL RUNTIME INVENTORY</span><h1>本地资源</h1><p>观察 Run-owned 应用进程、Compose revision 与 Docker 中间件</p></div><div className="heading-actions"><button className="secondary-button" type="button" aria-label="刷新本地资源" disabled={refreshing} onClick={refreshAll}><RefreshCw size={16} className={refreshing ? 'is-spinning' : ''} />刷新</button><button className="danger-button" type="button" disabled={!token.configured || previewMutation.isPending} title={token.disabledReason ?? '预览可清理资源'} onClick={() => previewMutation.mutate()}>{previewMutation.isPending ? <BusyLabel>生成预览</BusyLabel> : <><Eraser size={16} />清理预览</>}</button></div></div>
      <TokenReason />
      {!runtime.data?.docker_available && !runtime.isLoading && <ErrorState error={new Error(runtime.data?.recovery ?? 'Docker 当前不可用')} onRetry={() => void runtime.refetch()} title="Docker 不可用" />}
      <section className="metric-strip"><div><Activity size={19} /><span>应用进程</span><strong>{processRecords.length}</strong></div><div><Box size={19} /><span>Deployment</span><strong>{deploymentRecords.length}</strong></div><div><CheckCircle2 size={19} /><span>Active revision</span><strong>{activeDeployments}</strong></div><div><Database size={19} /><span>托管 PostgreSQL</span><strong>{managedRecords.filter((record) => record.status !== 'removed').length}</strong></div><div><Container size={19} /><span>Docker 中间件</span><strong>{resources.length}</strong></div><div><ShieldCheck size={19} /><span>受保护 / 健康</span><strong>{protectedCount} / {healthy}</strong></div></section>

      <section className="runtime-section"><header><div><Activity size={17} /><span><h2>应用进程</h2><p>仅显示 TripGuru Run 启动并拥有的本机进程；停止请打开 Run 后取消</p></span></div><span>{processRecords.length} RUN-OWNED</span></header>{processes.isError ? <ErrorState error={processes.error} onRetry={() => void processes.refetch()} title="无法读取应用进程" /> : processes.isLoading ? <div className="table-loading"><BusyLabel>正在读取应用进程</BusyLabel></div> : processRecords.length === 0 ? <EmptyState icon={TerminalSquare} title="没有运行中的应用进程" detail="从工作区启动 Run 后，长运行命令会显示在这里" /> : <div className="process-list">{processRecords.map((process) => <button type="button" className="process-row" key={process.id} onClick={() => onOpenRun(process.run_id)}><span className="process-pid">PID {process.pid}</span><span className="row-main"><strong>{process.project_name} · {process.label}</strong><small>{process.cwd}</small></span><code>{process.argv.map((token) => token.includes(' ') ? `“${token}”` : token).join(' ')}</code><span><small>Run {process.run_id.slice(0, 10)}</small><ExternalLink size={14} /></span></button>)}</div>}</section>

      <section className="runtime-section"><header><div><Box size={17} /><span><h2>应用部署</h2><p>Compose revision、前一版本与恢复结果来自本地 deployment store</p></span></div><span>{activeDeployments} ACTIVE</span></header>{deployments.isError ? <ErrorState error={deployments.error} onRetry={() => void deployments.refetch()} title="无法读取应用部署" /> : deployments.isLoading ? <div className="table-loading"><BusyLabel>正在读取 Deployment</BusyLabel></div> : deploymentRecords.length === 0 ? <EmptyState icon={Box} title="没有 Compose deployment" detail="将服务运行目标改为 Docker Compose 并启动 Run 后显示" /> : <div className="deployment-list">{deploymentRecords.map((deployment) => { const canReconcile = RECONCILABLE_STATUSES.has(deployment.status); return <article className="deployment-row" key={deployment.revision_id}><span className={deployment.status === 'active' ? 'deployment-state is-ready' : canReconcile ? 'deployment-state is-warning' : 'deployment-state'}>{DEPLOYMENT_STATUS_LABELS[deployment.status]}</span><div className="deployment-main"><strong>{deployment.project_name}</strong><small>r{deployment.workspace_revision} · {deployment.revision_id.slice(0, 12)}{deployment.previous_revision_id ? ` · previous ${deployment.previous_revision_id.slice(0, 10)}` : ''}</small><div>{deployment.services.map((service) => <code key={service}>{service}</code>)}{deployment.immutable_images.map((image) => <code className="image-ref" key={image}>{image}</code>)}</div>{(deployment.failure_detail || deployment.recovery_detail) && <p className={deployment.failure_detail ? 'deployment-recovery is-error' : 'deployment-recovery'}>{deployment.failure_code && <b>{deployment.failure_code}</b>}{deployment.failure_detail ?? deployment.recovery_detail}</p>}</div><button className="secondary-button" type="button" disabled={!canReconcile || !token.configured || reconcileMutation.isPending} title={!canReconcile ? '该 revision 已处于稳定终态' : token.disabledReason ?? '核对 Docker runtime 并继续恢复'} onClick={() => reconcileMutation.mutate(deployment.revision_id)}>{reconcileMutation.isPending && reconcileMutation.variables === deployment.revision_id ? <BusyLabel>核对中</BusyLabel> : <><RotateCcw size={14} />Reconcile</>}</button></article>; })}</div>}{reconcileMutation.isSuccess && <SuccessMessage>Deployment runtime 状态已重新核对</SuccessMessage>}{reconcileMutation.isError && <MutationError error={reconcileMutation.error} />}</section>

      <section className="runtime-section managed-section"><header><div><Database size={17} /><span><h2>平台托管 PostgreSQL</h2><p>声明工作区需要的 PostgreSQL 18；密码只引用 Secret metadata，客户端不读取凭据值</p></span></div><div className="runtime-header-actions"><span>{managedRecords.filter((record) => record.status !== 'removed').length} MANAGED</span><button className="primary-button" type="button" disabled={Boolean(provisionOpenDisabledReason)} title={provisionOpenDisabledReason ?? '创建工作区专属 PostgreSQL'} onClick={openProvision}><Plus size={15} />托管 PostgreSQL</button></div></header>
        <div className="managed-support-note"><TriangleAlert size={15} /><span><strong>当前只开放 PostgreSQL</strong>MinIO、Redis 与 Elasticsearch 仍需使用已有 Docker 资源，平台不会伪装创建或接管。</span></div>
        {managed.isError ? <ErrorState error={managed.error} onRetry={() => void managed.refetch()} title="无法读取托管中间件" /> : managed.isLoading ? <div className="table-loading"><BusyLabel>正在读取托管中间件</BusyLabel></div> : managedRecords.filter((record) => record.status !== 'removed').length === 0 ? <EmptyState icon={Database} title="没有平台托管的 PostgreSQL" detail="选择工作区、宿主端口与密码 Secret 后创建；后续状态由 Reconcile 恢复" /> : <div className="managed-list">{managedRecords.filter((record) => record.status !== 'removed').map((resource) => {
          const workspaceName = workspaceRecords.find((workspace) => workspace.id === resource.workspace_id)?.name ?? resource.workspace_id;
          const secretName = resource.intent ? secrets.data?.secrets.find((secret) => secret.id === resource.intent?.password_secret_ref)?.name : null;
          const pending = MANAGED_PENDING_STATUSES.has(resource.status);
          return <article className="managed-row" key={resource.id}>
            <span className={resource.status === 'active' ? 'managed-state is-ready' : resource.status === 'failed' ? 'managed-state is-error' : 'managed-state is-warning'}>{pending && <span className="managed-pulse" />}{MANAGED_STATUS_LABELS[resource.status]}</span>
            <div className="managed-main"><strong>{resource.name}</strong><small>{workspaceName} · {resource.runtime_id ? `runtime ${resource.runtime_id.slice(0, 12)}` : '尚未分配 runtime'}</small>{resource.intent && <div className="managed-facts"><code>127.0.0.1:{resource.intent.host_port} → 5432</code><span>{resource.intent.database}</span><span>{resource.intent.username}</span><span><KeyRound size={12} />{secretName ?? resource.intent.password_secret_ref}</span></div>}{resource.failure_code && <p><TriangleAlert size={13} /><b>{resource.failure_code}</b><span>可执行 Reconcile 重试当前声明</span></p>}</div>
            <div className="managed-actions"><button className="secondary-button" type="button" disabled={!token.configured || managedReconcileMutation.isPending || resource.status === 'removed'} title={token.disabledReason ?? '重新核对并应用当前 PostgreSQL 声明'} onClick={() => managedReconcileMutation.mutate(resource.id)}>{managedReconcileMutation.isPending && managedReconcileMutation.variables === resource.id ? <BusyLabel>核对中</BusyLabel> : <><RotateCcw size={14} />Reconcile</>}</button><button className="icon-button danger-icon" type="button" aria-label={`删除 ${resource.name}`} title="删除此平台托管 PostgreSQL" disabled={!token.configured || managedDeleteMutation.isPending} onClick={() => { setDeleteResource(resource); setDeleteConfirmation(''); managedDeleteMutation.reset(); }}><Trash2 size={15} /></button></div>
          </article>;
        })}</div>}
        {provisionMutation.isSuccess && <SuccessMessage>PostgreSQL 托管声明已创建，正在进入 provisioning</SuccessMessage>}
        {managedReconcileMutation.isSuccess && <SuccessMessage>PostgreSQL 声明已重新核对</SuccessMessage>}
        {(managedReconcileMutation.isError || managedDeleteMutation.isError) && <MutationError error={managedReconcileMutation.error ?? managedDeleteMutation.error} />}
      </section>

      <section className="runtime-section"><header><div><Container size={17} /><span><h2>Docker 中间件</h2><p>外部实例只观察；清理仅作用于明确托管且未受保护的容器</p></span></div><span>{resources.length} DISCOVERED</span></header>{runtime.isError ? <ErrorState error={runtime.error} onRetry={() => void runtime.refetch()} title="无法读取 Docker 资源" /> : runtime.isLoading ? <div className="table-loading"><BusyLabel>正在读取 Docker</BusyLabel></div> : resources.length === 0 ? <EmptyState icon={Container} title="没有中间件资源" detail="Docker 中未发现 PostgreSQL、Redis、Elasticsearch 或 MinIO" /> : (
        <section className="data-table resource-table" aria-label="Docker 中间件列表"><header><span>资源</span><span>类型 / 镜像</span><span>端口</span><span>健康</span><span>Ownership</span><span>保护</span></header>{resources.map((resource) => <div className="table-row" key={resource.id}><div className="repo-primary"><span className="row-icon"><MiddlewareLogo kind={resource.kind} /></span><span><strong>{resource.name}</strong><small>{resource.status_text}</small></span></div><div><strong>{MIDDLEWARE_LABELS[resource.kind]}</strong><small className="block-copy">{resource.image}</small></div><code>{resource.ports || '内部端口'}</code><span className={resource.health === 'healthy' || resource.health === 'running' ? 'state-label is-ready' : 'state-label is-warning'}><StatusDot active={resource.health === 'healthy' || resource.health === 'running'} />{resource.health}</span><span className={resource.managed ? 'state-label is-info' : 'state-label'}>{resource.managed ? 'TripGuru Local' : '外部资源'}</span><span className={resource.protected ? 'state-label is-info' : 'state-label'}>{resource.protected ? <><ShieldCheck size={13} />受保护</> : '未保护'}</span></div>)}</section>
      )}</section>
      {(previewMutation.isError || applyMutation.isError) && <MutationError error={previewMutation.error ?? applyMutation.error} />}

      {provisionOpen && <Modal title="托管 PostgreSQL" eyebrow="WORKSPACE-OWNED · POSTGRES 18" onClose={() => setProvisionOpen(false)} footer={<><button className="secondary-button" type="button" onClick={() => setProvisionOpen(false)}>取消</button><button className="primary-button" type="button" disabled={Boolean(provisionDisabledReason) || provisionMutation.isPending} title={provisionDisabledReason ?? '创建 PostgreSQL 托管声明'} onClick={() => provisionMutation.mutate(provisionForm)}>{provisionMutation.isPending ? <BusyLabel>提交中</BusyLabel> : <><Database size={15} />创建并配置</>}</button></>}>
        <div className="managed-provision-intro"><Database size={18} /><div><strong>创建隔离的 PostgreSQL 18 容器</strong><span>容器密码在执行时从所选 Secret 获取；响应、缓存与界面均不会包含原始值。</span></div></div>
        <div className="form-grid managed-provision-form"><label className="field is-wide"><span>所属工作区</span><select aria-label="托管 PostgreSQL 所属工作区" value={provisionForm.workspace_id} onChange={(event) => { setProvisionForm((current) => ({ ...current, workspace_id: event.target.value })); provisionMutation.reset(); }}><option value="">选择已保存工作区</option>{workspaceRecords.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} · r{workspace.revision}</option>)}</select></label><label className="field"><span>宿主端口</span><input aria-label="PostgreSQL 宿主端口" type="number" min={1024} max={65535} value={provisionForm.host_port} onChange={(event) => { setProvisionForm((current) => ({ ...current, host_port: Number(event.target.value) })); provisionMutation.reset(); }} /></label><label className="field"><span>镜像</span><input aria-label="PostgreSQL 镜像" value="postgres:18" disabled /></label><label className="field"><span>用户名</span><input aria-label="PostgreSQL 用户名" value={provisionForm.username} onChange={(event) => { setProvisionForm((current) => ({ ...current, username: event.target.value })); provisionMutation.reset(); }} /></label><label className="field"><span>数据库</span><input aria-label="PostgreSQL 数据库名" value={provisionForm.database} onChange={(event) => { setProvisionForm((current) => ({ ...current, database: event.target.value })); provisionMutation.reset(); }} /></label><label className="field is-wide"><span>密码 Secret</span><select aria-label="PostgreSQL 密码 Secret" value={provisionForm.password_secret_ref} onChange={(event) => { setProvisionForm((current) => ({ ...current, password_secret_ref: event.target.value })); provisionMutation.reset(); }}><option value="">选择 presence 可用的 Secret</option>{presentSecrets.map((secret) => <option key={secret.id} value={secret.id}>{secret.name} · {secret.id}</option>)}</select><small className="field-support">仅保存 Secret ID 引用；密码输入和回显均不在此流程中发生。</small></label></div>
        {provisionError && <div className="managed-validation" role="status"><TriangleAlert size={14} />{provisionError}</div>}
        {provisionMutation.isError && <MutationError error={provisionMutation.error} />}
      </Modal>}

      {deleteResource && <Modal title="删除托管 PostgreSQL" eyebrow="DESTRUCTIVE · EXACT CONFIRMATION" onClose={() => setDeleteResource(null)} footer={<><button className="secondary-button" type="button" onClick={() => setDeleteResource(null)}>取消</button><button className="danger-button" type="button" disabled={!token.configured || deleteConfirmation !== deleteResource.name || managedDeleteMutation.isPending} title={deleteConfirmation !== deleteResource.name ? `输入 ${deleteResource.name} 后才能删除` : token.disabledReason ?? '删除托管 PostgreSQL'} onClick={() => managedDeleteMutation.mutate(deleteResource.id)}>{managedDeleteMutation.isPending ? <BusyLabel>删除中</BusyLabel> : <><Trash2 size={15} />删除 PostgreSQL</>}</button></>}>
        <div className="managed-delete-warning"><TriangleAlert size={18} /><div><strong>这会删除平台托管容器</strong><span>工作区和 Secret 保留；运行中的连接将立即失效。输入完整资源名确认。</span></div></div><label className="field managed-delete-confirm"><span>资源名：<code>{deleteResource.name}</code></span><input aria-label="输入资源名确认删除" value={deleteConfirmation} autoComplete="off" onChange={(event) => { setDeleteConfirmation(event.target.value); managedDeleteMutation.reset(); }} placeholder={deleteResource.name} /></label>{managedDeleteMutation.isError && <MutationError error={managedDeleteMutation.error} />}
      </Modal>}

      {preview && <Modal title={result ? '清理结果' : '清理预览'} eyebrow="MANAGED RESOURCES ONLY" wide onClose={() => setPreview(null)} footer={result ? <button className="primary-button" type="button" onClick={() => setPreview(null)}>完成</button> : <><button className="secondary-button" type="button" onClick={() => setPreview(null)}>取消</button><button className="danger-button" type="button" disabled={!token.configured || selectedIds.length === 0 || !confirmed || applyMutation.isPending} title={selectedIds.length === 0 ? '至少选择一个可清理资源' : !confirmed ? '请先核对清理范围' : token.disabledReason ?? '执行清理'} onClick={() => applyMutation.mutate({ previewId: preview.id, resourceIds: selectedIds })}>{applyMutation.isPending ? <BusyLabel>清理中</BusyLabel> : <><Trash2 size={16} />清理 {selectedIds.length} 项</>}</button></>}>
        {result ? <div className="cleanup-results">{skippedCount === 0 && failedCount === 0 ? <SuccessMessage>已删除 {removedCount} 项资源</SuccessMessage> : <div className="cleanup-banner" role={failedCount > 0 ? 'alert' : 'status'}><TriangleAlert size={18} /><div><strong>清理已完成，部分资源未删除</strong><span>已删除 {removedCount} 项 · 已跳过 {skippedCount} 项 · 失败 {failedCount} 项</span></div></div>}{result.results.map((item) => <div className="cleanup-result" key={item.resource_id}><span className={item.status === 'removed' ? 'result-icon is-success' : 'result-icon is-warning'}>{item.status === 'removed' ? <CheckCircle2 size={17} /> : item.status === 'skipped' ? <ShieldCheck size={17} /> : <TriangleAlert size={17} />}</span><div><strong>{item.resource_name}</strong><span>{CLEANUP_STATUS_LABELS[item.status]}</span>{(item.detail || item.reason_code) && <small>{item.detail ?? item.reason_code}</small>}</div></div>)}</div> : <>
          <div className="cleanup-banner"><LockKeyhole size={18} /><div><strong>外部资源和受保护资源不可选择</strong><span>预览 ID {preview.id.slice(0, 12)} · runtime fingerprint {preview.runtime_fingerprint.slice(0, 12)}</span></div></div>
          <div className="cleanup-list">{preview.items.map((item) => <label className={item.eligible ? 'cleanup-row' : 'cleanup-row is-disabled'} key={item.resource.id}><input type="checkbox" disabled={!item.eligible} checked={selectedIds.includes(item.resource.id)} onChange={() => setSelectedIds((current) => current.includes(item.resource.id) ? current.filter((id) => id !== item.resource.id) : [...current, item.resource.id])} /><span className="row-icon"><MiddlewareLogo kind={item.resource.kind} /></span><span className="row-main"><strong>{item.resource.name}</strong><small>{item.resource.image}</small></span>{item.eligible ? <span className="state-label is-warning">可清理</span> : <span className="protection-reason"><ShieldCheck size={14} />{item.reason ?? '受保护'}</span>}</label>)}</div>
          <label className="confirmation-check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>我已核对所选资源；清理仅作用于平台托管容器</span></label>
        </>}
        {applyMutation.isError && <MutationError error={applyMutation.error} />}
      </Modal>}
    </div>
  );
}
