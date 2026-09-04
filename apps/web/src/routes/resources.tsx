import { AlertTriangle, Check, Container, KeyRound, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

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
  const { t } = useTranslation();
  const runtime = useRuntime();
  const resources = runtime.data?.resources ?? [];
  return (
    <section className="rounded-md border border-border bg-card" data-testid="middleware-section">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold">{t('resources.middleware.title')}</h2>
          <p className="text-[11px] text-muted-foreground">{t('resources.middleware.description')}</p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void runtime.refetch()} disabled={runtime.isFetching}>
          {runtime.isFetching ? <RefreshCw className="is-spinning" /> : <RefreshCw />}
          {t('resources.middleware.refresh')}
        </Button>
      </header>
      <div className="p-3">
        {runtime.isLoading ? (
          <ListSkeleton rows={3} />
        ) : runtime.isError ? (
          <ErrorState error={runtime.error} onRetry={() => void runtime.refetch()} title={t('resources.middleware.errorTitle')} />
        ) : runtime.data && !runtime.data.docker_available ? (
          <ErrorState error={new Error(runtime.data.error_code ?? 'DOCKER_UNAVAILABLE')} onRetry={() => void runtime.refetch()} title={t('resources.middleware.dockerUnavailable')} />
        ) : resources.length === 0 ? (
          <EmptyState icon={Container} title={t('resources.middleware.emptyTitle')} detail={t('resources.middleware.emptyDetail')} />
        ) : (
          <ul className="grid gap-2">
            {resources.map((resource) => (
              <li key={resource.id} className="grid gap-1 rounded-md border border-border bg-surface-2 px-3 py-2 md:grid-cols-[minmax(0,1fr)_auto]" data-testid="middleware-row">
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 text-xs font-semibold">
                    {resource.name}
                    <Badge variant="info">{t(MIDDLEWARE_LABELS[resource.kind] ?? resource.kind)}</Badge>
                    {resource.protected && <Badge variant="warn">{t('resources.middleware.protected')}</Badge>}
                    {resource.managed && <Badge variant="outline">{t('resources.middleware.managed')}</Badge>}
                  </p>
                  <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                    {resource.image} · {resource.state} · {resource.health} · {resource.ports || t('resources.middleware.noPorts')}
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
  const { t } = useTranslation();
  const managed = useManagedMiddleware();
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ManagedResourceRecord | null>(null);
  const resources = managed.data?.resources ?? [];
  return (
    <section className="rounded-md border border-border bg-card" data-testid="managed-section">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold">{t('resources.managed.title')}</h2>
          <p className="text-[11px] text-muted-foreground">
            {t('resources.managed.description')}
          </p>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)} title={t('resources.managed.createTitle')}>
          <Plus />
          {t('resources.managed.createButton')}
        </Button>
      </header>
      <div className="p-3">
        {managed.isLoading ? (
          <ListSkeleton rows={2} />
        ) : managed.isError ? (
          <ErrorState error={managed.error} onRetry={() => void managed.refetch()} title={t('resources.managed.errorTitle')} />
        ) : resources.length === 0 ? (
          <EmptyState icon={Container} title={t('resources.managed.emptyTitle')} detail={t('resources.managed.emptyDetail')} />
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
  const { t } = useTranslation();
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
            {t(MANAGED_STATUS_LABELS[resource.status])}
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
          <Button variant="secondary" size="sm" disabled={reconcile.isPending} title={reconcile.isPending ? t('resources.managed.row.reconciling') : t('resources.managed.row.reconcileTitle')} onClick={() => reconcile.mutate()}>
            {reconcile.isPending ? <BusyLabel>{t('resources.managed.row.restoring')}</BusyLabel> : <RefreshCw />}
            Reconcile
          </Button>
        )}
        <Button variant="ghost" size="sm" aria-label={t('resources.managed.row.deleteAria', { name: resource.name })} title={t('resources.managed.row.deleteTitle')} onClick={onDelete}>
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
  const { t } = useTranslation();
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
    ? t('resources.managed.create.reasonWorkspace')
    : !Number.isInteger(hostPortNumber) || hostPortNumber < 1024 || hostPortNumber > 65535
      ? t('resources.managed.create.reasonPort')
      : !username.trim() || !database.trim()
        ? t('resources.managed.create.reasonCredentials')
        : !passwordSecretRef
          ? t('resources.managed.create.reasonSecret')
          : (provision.isPending ? t('resources.managed.create.reasonProvisioning') : null);
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined} data-testid="managed-create-dialog">
        <DialogHeader eyebrow="MANAGED MIDDLEWARE" title={t('resources.managed.create.dialogTitle')} />
        <DialogBody>
          <div className="grid gap-2.5">
            <div className="grid gap-1.5">
              <Label htmlFor="managed-workspace">{t('resources.managed.create.workspaceLabel')}</Label>
              <select
                id="managed-workspace"
                value={workspaceId}
                onChange={(event) => setWorkspaceId(event.target.value)}
                className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
              >
                <option value="">{t('resources.managed.create.selectWorkspace')}</option>
                {workspaces.map((workspace) => (
                  <option key={workspace.id} value={workspace.id}>
                    {workspace.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="managed-host-port">{t('resources.managed.create.hostPortLabel')}</Label>
              <Input id="managed-host-port" type="number" min={1024} max={65535} value={hostPort} onChange={(event) => setHostPort(event.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="managed-username">{t('resources.managed.create.usernameLabel')}</Label>
              <Input id="managed-username" value={username} onChange={(event) => setUsername(event.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="managed-database">{t('resources.managed.create.databaseLabel')}</Label>
              <Input id="managed-database" value={database} onChange={(event) => setDatabase(event.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="managed-secret">{t('resources.managed.create.secretLabel')}</Label>
              <select
                id="managed-secret"
                aria-label={t('resources.managed.create.secretLabel')}
                value={passwordSecretRef}
                onChange={(event) => setPasswordSecretRef(event.target.value)}
                className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
              >
                <option value="">{t('resources.common.selectSecret')}</option>
                {secrets.map((secret) => (
                  <option key={secret.id} value={secret.id} disabled={!secret.present}>
                    {secret.name} · v{secret.version}
                    {secret.present ? '' : ` · ${t('resources.secret.valueMissing')}`}
                  </option>
                ))}
              </select>
              <p className="text-[11px] text-muted-foreground">{t('resources.managed.create.description')}</p>
            </div>
            {provision.isError && <MutationError error={provision.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('resources.common.cancel')}
          </Button>
          <Button disabled={Boolean(disabledReason)} title={disabledReason ?? t('resources.managed.create.submit')} onClick={() => provision.mutate({ workspace_id: workspaceId, kind: 'postgres', host_port: hostPortNumber, username: username.trim(), database: database.trim(), password_secret_ref: passwordSecretRef })}>
            {provision.isPending ? <BusyLabel>{t('resources.common.creating')}</BusyLabel> : <Plus />}
            {t('resources.managed.create.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ManagedDeleteDialog({ resource, onClose }: { resource: ManagedResourceRecord; onClose: () => void }) {
  const { t } = useTranslation();
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
        <DialogHeader eyebrow="DESTRUCTIVE ACTION" title={t('resources.managed.delete.title')} />
        <DialogBody>
          <div className="grid gap-2.5">
            <p className="text-xs leading-relaxed text-muted-foreground">
              {t('resources.managed.delete.body', { name: resource.name })}
            </p>
            <div className="grid gap-1.5">
              <Label htmlFor="managed-delete-confirm">{t('resources.managed.delete.confirmLabel')}</Label>
              <Input id="managed-delete-confirm" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder={resource.name} />
            </div>
            {remove.isError && <MutationError error={remove.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('resources.common.cancel')}
          </Button>
          <Button variant="destructive" disabled={!confirmed || remove.isPending} title={!confirmed ? t('resources.managed.delete.reasonMismatch') : remove.isPending ? t('resources.common.deleting') : t('resources.managed.delete.confirm')} onClick={() => remove.mutate()}>
            {remove.isPending ? <BusyLabel>{t('resources.common.deleting')}</BusyLabel> : <Trash2 />}
            {t('resources.managed.delete.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ============ Secrets 管理 ============ */

function SecretsSection() {
  const { t } = useTranslation();
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
          <p className="text-[11px] text-muted-foreground">{t('resources.secrets.description')}</p>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)} title={t('resources.secrets.createTitle')}>
          <Plus />
          {t('resources.secrets.createButton')}
        </Button>
      </header>
      <div className="p-3">
        {secrets.isLoading ? (
          <ListSkeleton rows={3} />
        ) : secrets.isError ? (
          <ErrorState error={secrets.error} onRetry={() => void secrets.refetch()} title={t('resources.secrets.errorTitle')} />
        ) : records.length === 0 ? (
          <EmptyState icon={KeyRound} title={t('resources.secrets.emptyTitle')} detail={t('resources.secrets.emptyDetail')} />
        ) : (
          <ul className="grid gap-1.5">
            {records.map((secret) => (
              <li key={secret.id} className="grid items-center gap-2 rounded-sm bg-surface-2 px-3 py-2 md:grid-cols-[minmax(0,1fr)_auto_auto]" data-testid="secret-row">
                <span className="min-w-0">
                  <span className="block truncate text-xs font-semibold">{secret.name}</span>
                  <span className="block font-mono text-[11px] text-muted-foreground">
                    v{secret.version} · {t('resources.secrets.updated')} {new Date(secret.updated_at).toLocaleString('zh-CN', { hour12: false })}
                  </span>
                </span>
                {secret.present ? <Badge variant="ok">{t('resources.secret.valueSaved')}</Badge> : <Badge variant="warn">{t('resources.secret.valueMissing')}</Badge>}
                <Button variant="ghost" size="sm" aria-label={t('resources.secrets.deleteAria', { name: secret.name })} title={t('resources.secrets.deleteTitle')} onClick={() => setDeleteTarget(secret)}>
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
  const { t } = useTranslation();
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
        <DialogHeader eyebrow="SECRETS" title={t('resources.secretCreate.title')} />
        <DialogBody>
          <div className="grid gap-2.5">
            <p className="text-[11px] leading-relaxed text-warn">{t('resources.secretCreate.warning')}</p>
            <div className="grid gap-1.5">
              <Label htmlFor="secret-create-name">{t('resources.secretCreate.nameLabel')}</Label>
              <Input id="secret-create-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="One-off credential" />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="secret-create-value">{t('resources.secretCreate.valueLabel')}</Label>
              <Input id="secret-create-value" type="password" autoComplete="new-password" value={value} onChange={(event) => setValue(event.target.value)} />
            </div>
            {create.isError && <MutationError error={create.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('resources.common.cancel')}
          </Button>
          <Button
            disabled={create.isPending || !name.trim() || !value}
            title={!name.trim() || !value ? t('resources.secretCreate.reasonEmpty') : t('resources.secretCreate.submit')}
            onClick={() => create.mutate({ name: name.trim(), value })}
          >
            {create.isPending ? <BusyLabel>{t('resources.common.creating')}</BusyLabel> : <Plus />}
            {t('resources.secretCreate.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SecretDeleteDialog({ secret, onClose }: { secret: SecretMetadata; onClose: () => void }) {
  const { t } = useTranslation();
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
        <DialogHeader eyebrow="DESTRUCTIVE ACTION" title={t('resources.secretDelete.title', { name: secret.name })} />
        <DialogBody>
          <div className="grid gap-2.5">
            <p className="text-xs leading-relaxed text-muted-foreground">{t('resources.secretDelete.body')}</p>
            <label className="flex items-center gap-2 text-xs">
              <Checkbox checked={confirmed} onCheckedChange={(value) => setConfirmed(Boolean(value))} aria-label={t('resources.secretDelete.confirmLabel')} />
              {t('resources.secretDelete.confirmLabel')}
            </label>
            {remove.isError && <MutationError error={remove.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('resources.common.cancel')}
          </Button>
          <Button variant="destructive" disabled={!confirmed || remove.isPending} title={!confirmed ? t('resources.secretDelete.reasonConfirmFirst') : remove.isPending ? t('resources.common.deleting') : t('resources.secretDelete.confirm')} onClick={() => remove.mutate()}>
            {remove.isPending ? <BusyLabel>{t('resources.common.deleting')}</BusyLabel> : <Trash2 />}
            {t('resources.secretDelete.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ============ 清理预览/执行 ============ */

function CleanupSection() {
  const { t } = useTranslation();
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
          <h2 className="text-xs font-semibold">{t('resources.cleanup.title')}</h2>
          <p className="text-[11px] text-muted-foreground">
            {t('resources.cleanup.description')}
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          disabled={!token.configured || createPreview.isPending}
          title={token.disabledReason ?? (createPreview.isPending ? t('resources.cleanup.generating') : t('resources.cleanup.previewTitle'))}
          onClick={() => createPreview.mutate()}
        >
          {createPreview.isPending ? <BusyLabel>{t('resources.cleanup.generatingBusy')}</BusyLabel> : <Trash2 />}
          {t('resources.cleanup.previewButton')}
        </Button>
      </header>
      <div className="px-3 py-2 text-[11px] text-muted-foreground">
        {t('resources.cleanup.hint')}
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
  const { t } = useTranslation();
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
        <DialogHeader eyebrow="CLEANUP PREVIEW" title={t('resources.cleanupPreview.title')} />
        <DialogBody>
          <div className="grid gap-2">
            <p className="text-[11px] text-muted-foreground">
              {t('resources.cleanupPreview.meta', { id: preview.id, fingerprint: preview.runtime_fingerprint })}
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
                    aria-label={t('resources.cleanupPreview.selectAria', { name: item.resource.name })}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2 text-xs font-semibold">
                      {item.resource.name}
                      <Badge variant="info">{t(MIDDLEWARE_LABELS[item.resource.kind] ?? item.resource.kind)}</Badge>
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
              {t('resources.cleanupPreview.confirmLabel', { count: selected.length })}
            </label>
            {apply.isError && <MutationError error={apply.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('resources.common.cancel')}
          </Button>
          <Button
            variant="destructive"
            disabled={selected.length === 0 || !confirmed || apply.isPending}
            title={selected.length === 0 ? t('resources.cleanupPreview.reasonSelect') : !confirmed ? t('resources.cleanupPreview.reasonConfirm') : apply.isPending ? t('resources.cleanupPreview.cleaning') : t('resources.cleanupPreview.submitTitle', { count: selected.length })}
            onClick={() => apply.mutate({ preview_id: preview.id, resource_ids: selected })}
          >
            {apply.isPending ? <BusyLabel>{t('resources.cleanupPreview.cleaningBusy')}</BusyLabel> : <Trash2 />}
            {t('resources.cleanupPreview.submit', { count: selected.length })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CleanupResultsDialog({ results, onClose }: { results: CleanupResultItem[]; onClose: () => void }) {
  const { t } = useTranslation();
  const removed = results.filter((result) => result.status === 'removed');
  const skipped = results.filter((result) => result.status === 'skipped');
  const failed = results.filter((result) => result.status === 'failed');
  const label = (status: string) => (status === 'removed' ? t('resources.cleanupResults.removed') : status === 'skipped' ? t('resources.cleanupResults.skipped') : t('resources.cleanupResults.failed'));
  const tone = (status: string) => (status === 'removed' ? 'ok' : status === 'skipped' ? 'warn' : 'danger');
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined} data-testid="cleanup-results-dialog">
        <DialogHeader eyebrow="CLEANUP RESULT" title={t('resources.cleanupResults.title')} />
        <DialogBody>
          <div className="grid gap-2">
            <p className="text-xs font-semibold" data-testid="cleanup-summary">
              {t('resources.cleanupResults.summary', { removed: removed.length, skipped: skipped.length, failed: failed.length })}
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
          <Button onClick={onClose}>{t('resources.cleanupResults.done')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function ResourcesRoute() {
  const { t } = useTranslation();
  const workspaces = useWorkspaces();
  const secrets = useSecrets();
  return (
    <PageScroll>
      <PageHeader
        eyebrow="RESOURCES"
        title={t('resources.page.title')}
        description={t('resources.page.description')}
      />
      <PageBody>
        <MiddlewareSection />
        <ManagedSection workspaces={workspaces.data?.workspaces ?? []} secrets={secrets.data?.secrets ?? []} />
        <SecretsSection />
        <CleanupSection />
        <CliFooter command={cli.secrets()} hint={t('resources.cliHint')} />
      </PageBody>
    </PageScroll>
  );
}
