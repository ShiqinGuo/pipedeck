import { Link, useNavigate } from '@tanstack/react-router';
import { Plus, Workflow } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { components } from '@/api/schema';
import { api } from '@/api/client';
import { useApiToken, useCatalog, useSecrets, useWorkspaces } from '@/api/hooks';
import { CliFooter } from '@/components/cli-command';
import { PageBody, PageHeader, PageScroll } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { BusyLabel, EmptyState, ErrorState, ListSkeleton, MutationError } from '@/components/states';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cli } from '@/lib/cli';
import { serviceFromProject, SUPPORTED_CONNECTION_KINDS, type SecretRefPatch as SecretRefPatchType } from '@/lib/workspace-editor';
import { MIDDLEWARE_LABELS } from '@/lib/status';

type ProjectSummary = components['schemas']['ProjectSummary'];
type SecretMetadata = components['schemas']['SecretMetadata'];
type CreateSecretRefs = Record<string, SecretRefPatchType>;

/** 新建工作区弹层:显式选择 Secret 后才生成连接 profile(缺省阻断) */
function CreateWorkspaceDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const catalog = useCatalog();
  const secrets = useSecrets();
  const [name, setName] = useState('');
  const [projectIds, setProjectIds] = useState<string[]>([]);
  const [secretRefs, setSecretRefs] = useState<CreateSecretRefs>({});
  const createMutation = useMutation({
    mutationFn: (payload: components['schemas']['WorkspaceInput']) => api.createWorkspace(payload),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      onClose();
      void navigate({ to: '/workspaces/$id', params: { id: created.id } });
    },
  });
  const projects = catalog.data?.projects ?? [];
  const selectedProjects = projectIds
    .map((projectId) => projects.find((project) => project.id === projectId))
    .filter((project): project is ProjectSummary => Boolean(project));
  const secretRecords = secrets.data?.secrets ?? [];
  const secretStateReason = secrets.isLoading
    ? t('workspaces.create.secretStateLoading')
    : secrets.isError
      ? t('workspaces.create.secretStateError')
      : null;
  // 未完成显式 Secret 选择的 requirement 视为缺失
  const missingSecret = selectedProjects.flatMap((project) =>
    project.requirements
      .filter((kind) => SUPPORTED_CONNECTION_KINDS.has(kind))
      .flatMap((kind) => {
        const refs = secretRefs[project.id] ?? {};
        const present = (secretId?: string) =>
          Boolean(secretId) && secretRecords.some((secret) => secret.id === secretId && secret.present);
        if (kind === 'postgres' && !present(refs.postgres)) return [t('workspaces.create.missingSecret', { project: project.name, middleware: t(MIDDLEWARE_LABELS[kind] ?? kind) })];
        if (kind === 'minio' && (!present(refs.minioAccess) || !present(refs.minioSecret)))
          return [t('workspaces.create.missingKeys', { project: project.name, middleware: t(MIDDLEWARE_LABELS[kind] ?? kind) })];
        return [];
      }),
  );
  const createDisabledReason =
    createMutation.isPending
      ? t('workspaces.create.reasonCreating')
      : !name.trim()
        ? t('workspaces.create.reasonNameEmpty')
        : projectIds.length === 0
          ? t('workspaces.create.reasonNoProject')
          : missingSecret.length > 0
            ? t('workspaces.create.reasonMissingSecret', { list: missingSecret.join('、') })
            : (secretStateReason ?? null);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent wide aria-describedby={undefined} data-testid="create-workspace-dialog">
        <DialogHeader eyebrow="WORKSPACES" title={t('workspaces.create.dialogTitle')} />
        <DialogBody>
          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="workspace-name">{t('workspaces.create.nameLabel')}</Label>
              <Input id="workspace-name" value={name} onChange={(event) => setName(event.target.value)} placeholder={t('workspaces.create.namePlaceholder')} />
            </div>
            {catalog.isError && <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} title={t('workspaces.create.catalogErrorTitle')} />}
            <div className="grid gap-1.5" aria-label={t('workspaces.create.projectAria')}>
              {projects.map((project) => {
                const checked = projectIds.includes(project.id);
                return (
                  <label
                    key={project.id}
                    className="flex min-h-11 cursor-pointer items-center gap-2.5 rounded-sm border border-border bg-surface-2 px-2.5 py-1.5 hover:bg-surface-3"
                    data-testid="project-selection-row"
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={(value) =>
                        setProjectIds((current) => (value ? [...current, project.id] : current.filter((id) => id !== project.id)))
                      }
                      aria-label={t('workspaces.create.selectProjectAria', { name: project.name })}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-semibold">{project.name}</span>
                      <span className="block truncate text-[11px] text-muted-foreground">{project.path}</span>
                    </span>
                    <span className="font-mono text-[11px] text-muted-foreground">{project.branch}</span>
                  </label>
                );
              })}
            </div>
            {selectedProjects.length > 0 && (
              <section className="grid gap-2 rounded-md border border-border px-3 py-2.5" aria-label={t('workspaces.create.secretSectionAria')}>
                <p className="text-[11px] text-muted-foreground">
                  {t('workspaces.create.secretHint')}
                </p>
                {selectedProjects.map((project) => (
                  <div key={project.id} className="grid gap-1.5 border-t border-border pt-2 first:border-t-0 first:pt-0">
                    <span className="text-xs font-semibold">{project.name}</span>
                    {project.requirements.map((kind) => (
                      <SecretRefField
                        key={kind}
                        project={project}
                        kind={kind}
                        refs={secretRefs[project.id] ?? {}}
                        secrets={secretRecords}
                        secretsLoading={secrets.isLoading}
                        onChange={(patch) =>
                          setSecretRefs((current) => ({ ...current, [project.id]: { ...current[project.id], ...patch } }))
                        }
                      />
                    ))}
                    {project.requirements.length === 0 && (
                      <p className="text-[11px] text-muted-foreground">{t('workspaces.create.noMiddleware')}</p>
                    )}
                  </div>
                ))}
              </section>
            )}
            {createMutation.isError && <MutationError error={createMutation.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('workspaces.common.cancel')}
          </Button>
          <Button
            disabled={Boolean(createDisabledReason)}
            title={createDisabledReason ?? t('workspaces.create.submit')}
            onClick={() =>
              createMutation.mutate({
                name: name.trim(),
                mode: 'development',
                services: selectedProjects.map((project) => serviceFromProject(project, secretRefs[project.id])),
                bindings: [],
              })
            }
          >
            {createMutation.isPending ? <BusyLabel>{t('workspaces.create.creatingBusy')}</BusyLabel> : <Plus />}
            {t('workspaces.create.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SecretRefField({
  project,
  kind,
  refs,
  secrets,
  secretsLoading,
  onChange,
}: {
  project: ProjectSummary;
  kind: string;
  refs: { postgres?: string; minioAccess?: string; minioSecret?: string };
  secrets: SecretMetadata[];
  secretsLoading: boolean;
  onChange: (patch: { postgres?: string; minioAccess?: string; minioSecret?: string }) => void;
}) {
  const { t } = useTranslation();
  // Secret 字段的 label 契约使用 project id(与 WorkspaceService.project_id 一致,便于精确关联)
  const fieldLabel = (suffix: string) => `${project.id} ${suffix}`;
  if (kind === 'postgres') {
    return (
      <div className="grid gap-1">
        <Label htmlFor={`${project.id}-postgres-secret`}>{fieldLabel(t('workspaces.secretField.postgresPassword'))}</Label>
        <select
          id={`${project.id}-postgres-secret`}
          aria-label={fieldLabel(t('workspaces.secretField.postgresPassword'))}
          value={refs.postgres ?? ''}
          disabled={secretsLoading}
          onChange={(event) => onChange({ postgres: event.target.value })}
          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2.5 text-xs"
        >
          <option value="">{secretsLoading ? t('workspaces.secretField.loading') : t('workspaces.secretField.select')}</option>
          {secrets.map((secret) => (
            <option key={secret.id} value={secret.id} disabled={!secret.present}>
              {secret.name} · v{secret.version}
              {secret.present ? '' : ` · ${t('workspaces.secretField.valueMissing')}`}
            </option>
          ))}
        </select>
      </div>
    );
  }
  if (kind === 'minio') {
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={`${project.id}-minio-access`}>{fieldLabel(t('workspaces.secretField.minioAccess'))}</Label>
        <select
          id={`${project.id}-minio-access`}
          aria-label={fieldLabel(t('workspaces.secretField.minioAccess'))}
          value={refs.minioAccess ?? ''}
          disabled={secretsLoading}
          onChange={(event) => onChange({ minioAccess: event.target.value })}
          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2.5 text-xs"
        >
          <option value="">{secretsLoading ? t('workspaces.secretField.loading') : t('workspaces.secretField.select')}</option>
          {secrets.map((secret) => (
            <option key={secret.id} value={secret.id} disabled={!secret.present}>
              {secret.name} · v{secret.version}
              {secret.present ? '' : ` · ${t('workspaces.secretField.valueMissing')}`}
            </option>
          ))}
        </select>
        <Label htmlFor={`${project.id}-minio-secret`}>{fieldLabel(t('workspaces.secretField.minioSecret'))}</Label>
        <select
          id={`${project.id}-minio-secret`}
          aria-label={fieldLabel(t('workspaces.secretField.minioSecret'))}
          value={refs.minioSecret ?? ''}
          disabled={secretsLoading}
          onChange={(event) => onChange({ minioSecret: event.target.value })}
          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2.5 text-xs"
        >
          <option value="">{secretsLoading ? t('workspaces.secretField.loading') : t('workspaces.secretField.select')}</option>
          {secrets.map((secret) => (
            <option key={secret.id} value={secret.id} disabled={!secret.present}>
              {secret.name} · v{secret.version}
              {secret.present ? '' : ` · ${t('workspaces.secretField.valueMissing')}`}
            </option>
          ))}
        </select>
      </div>
    );
  }
  return (
    <p className="text-[11px] text-warn">{t('workspaces.secretField.unsupported', { middleware: t(MIDDLEWARE_LABELS[kind] ?? kind) })}</p>
  );
}

export default function WorkspacesRoute() {
  const { t } = useTranslation();
  const workspaces = useWorkspaces();
  const token = useApiToken();
  const [createOpen, setCreateOpen] = useState(false);
  const records = workspaces.data?.workspaces ?? [];
  return (
    <PageScroll>
      <PageHeader
        eyebrow="WORKSPACES"
        title={t('workspaces.page.title')}
        description={t('workspaces.page.description')}
        actions={
          <Button
            size="sm"
            onClick={() => setCreateOpen(true)}
            disabled={!token.configured}
            title={token.disabledReason ?? t('workspaces.page.newButtonTitle')}
          >
            <Plus />
            {t('workspaces.page.newButton')}
          </Button>
        }
      />
      <PageBody>
        {workspaces.isLoading ? (
          <ListSkeleton rows={4} />
        ) : workspaces.isError ? (
          <ErrorState error={workspaces.error} onRetry={() => void workspaces.refetch()} title={t('workspaces.page.errorTitle')} />
        ) : records.length === 0 ? (
          <EmptyState
            icon={Workflow}
            title={t('workspaces.page.emptyTitle')}
            detail={t('workspaces.page.emptyDetail')}
            action={
              <Button size="sm" className="mt-1" onClick={() => setCreateOpen(true)} disabled={!token.configured} title={token.disabledReason ?? t('workspaces.page.newButtonTitle')}>
                <Plus />
                {t('workspaces.page.newButton')}
              </Button>
            }
          />
        ) : (
          <ul className="overflow-hidden rounded-md border border-border bg-card">
            {records.map((workspace) => (
              <li key={workspace.id} data-testid="workspace-row">
                <Link
                  to="/workspaces/$id"
                  params={{ id: workspace.id }}
                  className="grid min-h-13 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-[#252c28] px-3 py-2 hover:bg-surface-2"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-semibold">{workspace.name}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      {t('workspaces.page.serviceSummary', { count: workspace.services.length, mode: workspace.mode, updatedAt: new Date(workspace.updated_at).toLocaleString('zh-CN', { hour12: false }) })}
                    </span>
                  </span>
                  <Badge variant="outline">rev {workspace.revision}</Badge>
                </Link>
              </li>
            ))}
          </ul>
        )}
        <CliFooter command={cli.workspaces()} hint={t('workspaces.cliHint')} />
      </PageBody>
      {createOpen && <CreateWorkspaceDialog onClose={() => setCreateOpen(false)} />}
    </PageScroll>
  );
}
