import { AlertTriangle, Check, Container, KeyRound, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { components } from '@/api/schema';
import { api } from '@/api/client';
import { useApiToken, useManagedMiddleware, useRuntime, useSecrets, useWorkspaces } from '@/api/hooks';
import { CliFooter } from '@/components/cli-command';
import { PageBody, PageHeader, PageScroll } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { BusyLabel, EmptyState, ErrorState, ListSkeleton, MutationError } from '@/components/states';
import { cli } from '@/lib/cli';
import { MANAGED_STATUS_LABELS, MIDDLEWARE_LABELS } from '@/lib/status';

type ManagedResourceRecord = components['schemas']['ManagedResourceRecord'];
type SecretMetadata = components['schemas']['SecretMetadata'];
type CleanupPreviewResponse = components['schemas']['CleanupPreviewResponse'];
type CleanupResultItem = components['schemas']['CleanupResultItem'];
type WorkspaceRecord = components['schemas']['WorkspaceRecord'];

/* ============ Docker 中间件 ============ */

function MiddlewareSection() {
  const runtime = useRuntime();
  const resources = runtime.data?.resources ?? [];
  return (
    <section className="rounded-md border border-border bg-card" data-testid="middleware-section">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold">Docker 中间件</h2>
          <p className="text-[11px] text-muted-foreground">本机容器状态;带保护标记的实例由平台保护规则守护。</p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void runtime.refetch()} disabled={runtime.isFetching}>
          {runtime.isFetching ? <RefreshCw className="is-spinning" /> : <RefreshCw />}
          刷新
        </Button>
      </header>
      <div className="p-3">
        {runtime.isLoading ? (
          <ListSkeleton rows={3} />
        ) : runtime.isError ? (
          <ErrorState error={runtime.error} onRetry={() => void runtime.refetch()} title="无法读取 Docker 运行时" />
        ) : runtime.data && !runtime.data.docker_available ? (
          <ErrorState error={new Error(runtime.data.error_code ?? 'DOCKER_UNAVAILABLE')} onRetry={() => void runtime.refetch()} title="Docker 不可用" />
        ) : resources.length === 0 ? (
          <EmptyState icon={Container} title="没有检测到中间件容器" detail="启动 PostgreSQL、Redis、Elasticsearch 或 MinIO 容器后会在此展示" />
        ) : (
          <ul className="grid gap-2">
            {resources.map((resource) => (
              <li key={resource.id} className="grid gap-1 rounded-md border border-border bg-surface-2 px-3 py-2 md:grid-cols-[minmax(0,1fr)_auto]" data-testid="middleware-row">
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 text-xs font-semibold">
                    {resource.name}
                    <Badge variant="info">{MIDDLEWARE_LABELS[resource.kind] ?? resource.kind}</Badge>
                    {resource.protected && <Badge variant="warn">受保护</Badge>}
                    {resource.managed && <Badge variant="outline">平台托管</Badge>}
                  </p>
                  <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                    {resource.image} · {resource.state} · {resource.health} · {resource.ports || '未发布端口'}
                  </p>
                </div>
                <Badge variant={resource.health === 'healthy' || resource.health === 'running' ? 'ok' : 'warn'}>{resource.status_text}</Badge>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

/* ============ 平台托管中间件 ============ */

function ManagedSection({ workspaces, secrets }: { workspaces: WorkspaceRecord[]; secrets: SecretMetadata[] }) {
  const managed = useManagedMiddleware();
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ManagedResourceRecord | null>(null);
  const resources = managed.data?.resources ?? [];
  return (
    <section className="rounded-md border border-border bg-card" data-testid="managed-section">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold">平台托管 PostgreSQL</h2>
          <p className="text-[11px] text-muted-foreground">
            由平台创建并治理(ownership label);MinIO、Redis 与 Elasticsearch 适配器逐步接入。响应与界面均不会包含原始值。
          </p>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)} title="创建托管 PostgreSQL">
          <Plus />
          托管 PostgreSQL
        </Button>
      </header>
      <div className="p-3">
        {managed.isLoading ? (
          <ListSkeleton rows={2} />
        ) : managed.isError ? (
          <ErrorState error={managed.error} onRetry={() => void managed.refetch()} title="无法读取托管资源" />
        ) : resources.length === 0 ? (
          <EmptyState icon={Container} title="没有托管资源" detail="创建一个归属工作区的托管 PostgreSQL 实例;清理时受保护" />
        ) : (
          <ul className="grid gap-2">
            {resources.map((resource) => (
              <ManagedRow key={resource.id} resource={resource} onDelete={() => setDeleteTarget(resource)} />
            ))}
          </ul>
        )}
      </div>
      {createOpen && (
        <ManagedCreateDialog
          workspaces={workspaces}
          secrets={secrets}
          onClose={() => setCreateOpen(false)}
        />
      )}
      {deleteTarget && <ManagedDeleteDialog resource={deleteTarget} onClose={() => setDeleteTarget(null)} />}
    </section>
  );
}

function ManagedRow({ resource, onDelete }: { resource: ManagedResourceRecord; onDelete: () => void }) {
  const queryClient = useQueryClient();
  const reconcile = useMutation({
    mutationFn: () => api.reconcileManagedMiddleware(resource.id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['managed-middleware'] }),
  });
  const recoverable = resource.status === 'failed' || resource.status === 'provisioning' || resource.status === 'planned';
  const intent = resource.intent;
  return (
    <li className="grid gap-2 rounded-md border border-border bg-surface-2 px-3 py-2 md:grid-cols-[minmax(0,1fr)_auto]" data-testid="managed-row">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-xs font-semibold">
          {resource.name}
          <Badge variant={resource.status === 'active' ? 'ok' : resource.status === 'failed' ? 'danger' : 'outline'}>
            {MANAGED_STATUS_LABELS[resource.status]}
          </Badge>
          {resource.failure_code && <Badge variant="danger">{resource.failure_code}</Badge>}
        </p>
        <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
          {intent ? `127.0.0.1:${intent.host_port} → ${intent.container_port}/tcp · ${intent.username}@${intent.database} · ${intent.image}` : resource.kind}
        </p>
        {reconcile.isError && <MutationError error={reconcile.error} />}
      </div>
      <div className="flex items-start gap-2">
        {recoverable && (
          <Button variant="secondary" size="sm" disabled={reconcile.isPending} title={reconcile.isPending ? '正在恢复' : '重新对账并恢复该资源'} onClick={() => reconcile.mutate()}>
            {reconcile.isPending ? <BusyLabel>恢复中</BusyLabel> : <RefreshCw />}
            Reconcile
          </Button>
        )}
        <Button variant="ghost" size="sm" aria-label={`删除 ${resource.name}`} title="删除前需要输入资源名确认" onClick={onDelete}>
          <Trash2 />
        </Button>
      </div>
    </li>
  );
}

function ManagedCreateDialog({
  workspaces,
  secrets,
  onClose,
}: {
  workspaces: WorkspaceRecord[];
  secrets: SecretMetadata[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [workspaceId, setWorkspaceId] = useState(workspaces[0]?.id ?? '');
  const [hostPort, setHostPort] = useState('55432');
  const [username, setUsername] = useState('postgres');
  const [database, setDatabase] = useState('postgres');
  const [passwordSecretRef, setPasswordSecretRef] = useState('');
  const provision = useMutation({
    mutationFn: (payload: components['schemas']['ManagedMiddlewareProvisionRequest']) => api.provisionManagedMiddleware(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['managed-middleware'] });
      onClose();
    },
  });
  const hostPortNumber = Number(hostPort);
  const disabledReason = !workspaceId
    ? '选择所属工作区'
    : !Number.isInteger(hostPortNumber) || hostPortNumber < 1024 || hostPortNumber > 65535
      ? '宿主端口需在 1024-65535 之间'
      : !username.trim() || !database.trim()
        ? '用户名与数据库不能为空'
        : !passwordSecretRef
          ? '选择 presence 可用的密码 Secret'
          : (provision.isPending ? '正在创建' : null);
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined} data-testid="managed-create-dialog">
        <DialogHeader eyebrow="MANAGED MIDDLEWARE" title="托管 PostgreSQL" />
        <DialogBody>
          <div className="grid gap-2.5">
            <div className="grid gap-1.5">
              <Label htmlFor="managed-workspace">托管 PostgreSQL 所属工作区</Label>
              <select
                id="managed-workspace"
                value={workspaceId}
                onChange={(event) => setWorkspaceId(event.target.value)}
                className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
              >
                <option value="">选择工作区…</option>
                {workspaces.map((workspace) => (
                  <option key={workspace.id} value={workspace.id}>
                    {workspace.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="managed-host-port">PostgreSQL 宿主端口</Label>
              <Input id="managed-host-port" type="number" min={1024} max={65535} value={hostPort} onChange={(event) => setHostPort(event.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="managed-username">PostgreSQL 用户名</Label>
              <Input id="managed-username" value={username} onChange={(event) => setUsername(event.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="managed-database">PostgreSQL 数据库名</Label>
              <Input id="managed-database" value={database} onChange={(event) => setDatabase(event.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="managed-secret">PostgreSQL 密码 Secret</Label>
              <select
                id="managed-secret"
                aria-label="PostgreSQL 密码 Secret"
                value={passwordSecretRef}
                onChange={(event) => setPasswordSecretRef(event.target.value)}
                className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
              >
                <option value="">选择 Secret…</option>
                {secrets.map((secret) => (
                  <option key={secret.id} value={secret.id} disabled={!secret.present}>
                    {secret.name} · v{secret.version}
                    {secret.present ? '' : ' · 值缺失'}
                  </option>
                ))}
              </select>
              <p className="text-[11px] text-muted-foreground">响应、缓存与界面均不会包含原始值。</p>
            </div>
            {provision.isError && <MutationError error={provision.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button disabled={Boolean(disabledReason)} title={disabledReason ?? '创建并配置'} onClick={() => provision.mutate({ workspace_id: workspaceId, kind: 'postgres', host_port: hostPortNumber, username: username.trim(), database: database.trim(), password_secret_ref: passwordSecretRef })}>
            {provision.isPending ? <BusyLabel>创建中</BusyLabel> : <Plus />}
            创建并配置
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ManagedDeleteDialog({ resource, onClose }: { resource: ManagedResourceRecord; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [confirmation, setConfirmation] = useState('');
  const remove = useMutation({
    mutationFn: () => api.deleteManagedMiddleware(resource.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['managed-middleware'] });
      onClose();
    },
  });
  const confirmed = confirmation === resource.name;
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined} data-testid="managed-delete-dialog">
        <DialogHeader eyebrow="DESTRUCTIVE ACTION" title="删除托管 PostgreSQL" />
        <DialogBody>
          <div className="grid gap-2.5">
            <p className="text-xs leading-relaxed text-muted-foreground">
              将删除容器 {resource.name} 与其数据卷。输入资源名以确认:
            </p>
            <div className="grid gap-1.5">
              <Label htmlFor="managed-delete-confirm">输入资源名确认删除</Label>
              <Input id="managed-delete-confirm" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder={resource.name} />
            </div>
            {remove.isError && <MutationError error={remove.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button variant="destructive" disabled={!confirmed || remove.isPending} title={!confirmed ? '输入的资源名不匹配' : remove.isPending ? '正在删除' : '删除 PostgreSQL'} onClick={() => remove.mutate()}>
            {remove.isPending ? <BusyLabel>删除中</BusyLabel> : <Trash2 />}
            删除 PostgreSQL
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ============ Secrets 管理 ============ */

function SecretsSection() {
  const secrets = useSecrets();
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<SecretMetadata | null>(null);
  const records = secrets.data?.secrets ?? [];
  return (
    <section className="rounded-md border border-border bg-card" data-testid="secrets-section">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold">Secrets</h2>
          <p className="text-[11px] text-muted-foreground">凭据仅存 Windows Credential Manager;界面与日志只显示 metadata 与 presence。</p>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)} title="创建 Secret">
          <Plus />
          新建 Secret
        </Button>
      </header>
      <div className="p-3">
        {secrets.isLoading ? (
          <ListSkeleton rows={3} />
        ) : secrets.isError ? (
          <ErrorState error={secrets.error} onRetry={() => void secrets.refetch()} title="无法读取 Secret" />
        ) : records.length === 0 ? (
          <EmptyState icon={KeyRound} title="没有 Secret" detail="创建第一个凭据;连接 profile 与环境变量将引用它" />
        ) : (
          <ul className="grid gap-1.5">
            {records.map((secret) => (
              <li key={secret.id} className="grid items-center gap-2 rounded-sm bg-surface-2 px-3 py-2 md:grid-cols-[minmax(0,1fr)_auto_auto]" data-testid="secret-row">
                <span className="min-w-0">
                  <span className="block truncate text-xs font-semibold">{secret.name}</span>
                  <span className="block font-mono text-[11px] text-muted-foreground">
                    v{secret.version} · 更新 {new Date(secret.updated_at).toLocaleString('zh-CN', { hour12: false })}
                  </span>
                </span>
                {secret.present ? <Badge variant="ok">值已保存</Badge> : <Badge variant="warn">值缺失</Badge>}
                <Button variant="ghost" size="sm" aria-label={`删除 Secret ${secret.name}`} title="删除 metadata 与系统凭据" onClick={() => setDeleteTarget(secret)}>
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {createOpen && <SecretCreateDialog onClose={() => setCreateOpen(false)} />}
      {deleteTarget && <SecretDeleteDialog secret={deleteTarget} onClose={() => setDeleteTarget(null)} />}
      {/* queryClient 仅用于子组件;此处显式引用避免 lint 误报 */}
      <span className="hidden">{queryClient.getQueryState(['secrets'])?.dataUpdatedAt ?? 0}</span>
    </section>
  );
}

function SecretCreateDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const create = useMutation({
    mutationFn: (payload: components['schemas']['SecretCreateRequest']) => api.createSecret(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['secrets'] });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined} data-testid="secret-create-dialog">
        <DialogHeader eyebrow="SECRETS" title="新建 Secret" />
        <DialogBody>
          <div className="grid gap-2.5">
            <p className="text-[11px] leading-relaxed text-warn">值只写入 Windows Credential Manager;响应、日志与界面都不会回显。</p>
            <div className="grid gap-1.5">
              <Label htmlFor="secret-create-name">新 Secret 名称</Label>
              <Input id="secret-create-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="One-off credential" />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="secret-create-value">新 Secret 值</Label>
              <Input id="secret-create-value" type="password" autoComplete="new-password" value={value} onChange={(event) => setValue(event.target.value)} />
            </div>
            {create.isError && <MutationError error={create.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            disabled={create.isPending || !name.trim() || !value}
            title={!name.trim() || !value ? '名称与值不能为空' : '创建 Secret'}
            onClick={() => create.mutate({ name: name.trim(), value })}
          >
            {create.isPending ? <BusyLabel>创建中</BusyLabel> : <Plus />}
            创建 Secret
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SecretDeleteDialog({ secret, onClose }: { secret: SecretMetadata; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [confirmed, setConfirmed] = useState(false);
  const remove = useMutation({
    mutationFn: () => api.deleteSecret(secret.id, secret.version),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['secrets'] });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined} data-testid="secret-delete-dialog">
        <DialogHeader eyebrow="DESTRUCTIVE ACTION" title={`删除 Secret ${secret.name}`} />
        <DialogBody>
          <div className="grid gap-2.5">
            <p className="text-xs leading-relaxed text-muted-foreground">将删除 Secret metadata 与系统凭据值;正被工作区引用时会被拒绝。</p>
            <label className="flex items-center gap-2 text-xs">
              <Checkbox checked={confirmed} onCheckedChange={(value) => setConfirmed(Boolean(value))} aria-label="我确认删除此 Secret metadata 与系统凭据" />
              我确认删除此 Secret metadata 与系统凭据
            </label>
            {remove.isError && <MutationError error={remove.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button variant="destructive" disabled={!confirmed || remove.isPending} title={!confirmed ? '先勾选确认' : remove.isPending ? '正在删除' : '删除 Secret'} onClick={() => remove.mutate()}>
            {remove.isPending ? <BusyLabel>删除中</BusyLabel> : <Trash2 />}
            删除 Secret
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ============ 清理预览/执行 ============ */

function CleanupSection() {
  const queryClient = useQueryClient();
  const [preview, setPreview] = useState<CleanupPreviewResponse | null>(null);
  const [results, setResults] = useState<CleanupResultItem[] | null>(null);
  const token = useApiToken();
  const createPreview = useMutation({
    mutationFn: () => api.createCleanupPreview(),
    onSuccess: (data) => {
      setResults(null);
      setPreview(data);
    },
  });
  const closePreview = () => {
    setPreview(null);
    void queryClient.invalidateQueries({ queryKey: ['runtime'] });
  };
  return (
    <section className="rounded-md border border-border bg-card" data-testid="cleanup-section">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold">清理</h2>
          <p className="text-[11px] text-muted-foreground">
            只作用于带 ownership label 的资源;MinIO 与每类中间件最后健康实例永不进入可删集合。
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          disabled={!token.configured || createPreview.isPending}
          title={token.disabledReason ?? (createPreview.isPending ? '正在生成预览' : '生成清理预览')}
          onClick={() => createPreview.mutate()}
        >
          {createPreview.isPending ? <BusyLabel>生成中</BusyLabel> : <Trash2 />}
          清理预览
        </Button>
      </header>
      <div className="px-3 py-2 text-[11px] text-muted-foreground">
        清理执行前必须先预览作用范围,并在弹层中逐项确认;执行结果按项展示已删除/已跳过/失败。
      </div>
      {createPreview.isError && (
        <div className="px-3 pb-3">
          <MutationError error={createPreview.error} />
        </div>
      )}
      {preview && <CleanupPreviewDialog preview={preview} onClose={closePreview} onApplied={setResults} />}
      {results && <CleanupResultsDialog results={results} onClose={() => setResults(null)} />}
    </section>
  );
}

function CleanupPreviewDialog({
  preview,
  onClose,
  onApplied,
}: {
  preview: CleanupPreviewResponse;
  onClose: () => void;
  onApplied: (results: CleanupResultItem[]) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const apply = useMutation({
    mutationFn: (payload: components['schemas']['CleanupApplyRequest']) => api.applyCleanup(preview.id, payload),
    onSuccess: (data) => {
      onApplied(data.results);
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent wide aria-describedby={undefined} data-testid="cleanup-preview-dialog">
        <DialogHeader eyebrow="CLEANUP PREVIEW" title="清理预览" />
        <DialogBody>
          <div className="grid gap-2">
            <p className="text-[11px] text-muted-foreground">
              预览身份 {preview.id} · 指纹 {preview.runtime_fingerprint};保护项不可勾选并解释原因。
            </p>
            <ul className="grid gap-1.5">
              {preview.items.map((item) => (
                <li key={item.resource.id} className="flex items-start gap-2.5 rounded-sm bg-surface-2 px-3 py-2" data-testid="cleanup-row">
                  <Checkbox
                    className="mt-0.5"
                    disabled={!item.eligible}
                    checked={selected.includes(item.resource.id)}
                    onCheckedChange={(value) =>
                      setSelected((current) => (value ? [...current, item.resource.id] : current.filter((id) => id !== item.resource.id)))
                    }
                    aria-label={`选择 ${item.resource.name}`}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2 text-xs font-semibold">
                      {item.resource.name}
                      <Badge variant="info">{MIDDLEWARE_LABELS[item.resource.kind] ?? item.resource.kind}</Badge>
                      {!item.eligible && <Badge variant="warn">{item.reason_code}</Badge>}
                    </span>
                    <span className="mt-0.5 block truncate font-mono text-[11px] text-muted-foreground">
                      {item.resource.image} · {item.resource.state}
                    </span>
                    {item.reason && <span className="mt-0.5 block text-[11px] text-warn">{item.reason}</span>}
                  </span>
                </li>
              ))}
            </ul>
            <label className="flex items-center gap-2 text-xs">
              <Checkbox checked={confirmed} onCheckedChange={(value) => setConfirmed(Boolean(value))} />
              我已核对所选资源({selected.length} 项)
            </label>
            {apply.isError && <MutationError error={apply.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="destructive"
            disabled={selected.length === 0 || !confirmed || apply.isPending}
            title={selected.length === 0 ? '选择要清理的资源' : !confirmed ? '先核对所选资源' : apply.isPending ? '正在清理' : `清理 ${selected.length} 项`}
            onClick={() => apply.mutate({ preview_id: preview.id, resource_ids: selected })}
          >
            {apply.isPending ? <BusyLabel>清理中</BusyLabel> : <Trash2 />}
            清理 {selected.length} 项
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CleanupResultsDialog({ results, onClose }: { results: CleanupResultItem[]; onClose: () => void }) {
  const removed = results.filter((result) => result.status === 'removed');
  const skipped = results.filter((result) => result.status === 'skipped');
  const failed = results.filter((result) => result.status === 'failed');
  const label = (status: string) => (status === 'removed' ? '已删除' : status === 'skipped' ? '已跳过' : '删除失败');
  const tone = (status: string) => (status === 'removed' ? 'ok' : status === 'skipped' ? 'warn' : 'danger');
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined} data-testid="cleanup-results-dialog">
        <DialogHeader eyebrow="CLEANUP RESULT" title="清理结果" />
        <DialogBody>
          <div className="grid gap-2">
            <p className="text-xs font-semibold" data-testid="cleanup-summary">
              已删除 {removed.length} 项 · 已跳过 {skipped.length} 项 · 失败 {failed.length} 项
            </p>
            <ul className="grid gap-1.5">
              {results.map((result) => (
                <li key={result.resource_id} className="flex items-start gap-2 rounded-sm bg-surface-2 px-3 py-2" data-testid="cleanup-result">
                  {result.status === 'removed' ? <Check className="mt-0.5 size-4 text-ok" /> : <AlertTriangle className="mt-0.5 size-4 text-warn" />}
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-2 text-xs font-semibold">
                      {result.resource_name}
                      <Badge variant={tone(result.status)}>{label(result.status)}</Badge>
                    </span>
                    {(result.reason_code || result.detail) && (
                      <span className="mt-0.5 block text-[11px] text-muted-foreground">
                        {result.reason_code ? `${result.reason_code}` : ''}
                        {result.detail ? ` · ${result.detail}` : ''}
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button onClick={onClose}>完成</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function ResourcesRoute() {
  const workspaces = useWorkspaces();
  const secrets = useSecrets();
  return (
    <PageScroll>
      <PageHeader
        eyebrow="RESOURCES"
        title="本地资源"
        description="Docker 中间件、平台托管资源、Secrets 与清理;清理预览制,受保护资源永不可删。"
      />
      <PageBody>
        <MiddlewareSection />
        <ManagedSection workspaces={workspaces.data?.workspaces ?? []} secrets={secrets.data?.secrets ?? []} />
        <SecretsSection />
        <CleanupSection />
        <CliFooter command={cli.secrets()} hint="等价 CLI:Secrets 与 doctor" />
      </PageBody>
    </PageScroll>
  );
}
