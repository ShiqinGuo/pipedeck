import { Link, useNavigate } from '@tanstack/react-router';
import { Plus, Workflow } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

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
    ? '正在读取 Secret 状态'
    : secrets.isError
      ? '无法读取 Secret 状态，请重试'
      : null;
  // 未完成显式 Secret 选择的 requirement 视为缺失
  const missingSecret = selectedProjects.flatMap((project) =>
    project.requirements
      .filter((kind) => SUPPORTED_CONNECTION_KINDS.has(kind))
      .flatMap((kind) => {
        const refs = secretRefs[project.id] ?? {};
        const present = (secretId?: string) =>
          Boolean(secretId) && secretRecords.some((secret) => secret.id === secretId && secret.present);
        if (kind === 'postgres' && !present(refs.postgres)) return [`${project.name} 的 ${MIDDLEWARE_LABELS[kind]} 密码 Secret`];
        if (kind === 'minio' && (!present(refs.minioAccess) || !present(refs.minioSecret)))
          return [`${project.name} 的 ${MIDDLEWARE_LABELS[kind]} Access/Secret Key`];
        return [];
      }),
  );
  const createDisabledReason =
    createMutation.isPending
      ? '正在创建'
      : !name.trim()
        ? '工作区名称不能为空'
        : projectIds.length === 0
          ? '至少选择一个项目'
          : missingSecret.length > 0
            ? `需要选择:${missingSecret.join('、')}`
            : (secretStateReason ?? null);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent wide aria-describedby={undefined} data-testid="create-workspace-dialog">
        <DialogHeader eyebrow="WORKSPACES" title="新建工作区" />
        <DialogBody>
          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="workspace-name">工作区名称</Label>
              <Input id="workspace-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Supplier 本地集成" />
            </div>
            {catalog.isError && <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} title="无法读取项目目录" />}
            <div className="grid gap-1.5" aria-label="选择工作区项目">
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
                      aria-label={`选择项目 ${project.name}`}
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
              <section className="grid gap-2 rounded-md border border-border px-3 py-2.5" aria-label="项目 Secret 选择">
                <p className="text-[11px] text-muted-foreground">
                  连接 profile 只在显式选择 Secret 后生成;MinIO、Redis 与 Elasticsearch 之外的适配器尚未支持,将被显式阻断。
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
                      <p className="text-[11px] text-muted-foreground">无需外部中间件</p>
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
            取消
          </Button>
          <Button
            disabled={Boolean(createDisabledReason)}
            title={createDisabledReason ?? '创建工作区'}
            onClick={() =>
              createMutation.mutate({
                name: name.trim(),
                mode: 'development',
                services: selectedProjects.map((project) => serviceFromProject(project, secretRefs[project.id])),
                bindings: [],
              })
            }
          >
            {createMutation.isPending ? <BusyLabel>正在创建</BusyLabel> : <Plus />}
            创建工作区
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
  // Secret 字段的 label 契约使用 project id(与 WorkspaceService.project_id 一致,便于精确关联)
  const fieldLabel = (suffix: string) => `${project.id} ${suffix}`;
  if (kind === 'postgres') {
    return (
      <div className="grid gap-1">
        <Label htmlFor={`${project.id}-postgres-secret`}>{fieldLabel('PostgreSQL 密码 Secret')}</Label>
        <select
          id={`${project.id}-postgres-secret`}
          aria-label={fieldLabel('PostgreSQL 密码 Secret')}
          value={refs.postgres ?? ''}
          disabled={secretsLoading}
          onChange={(event) => onChange({ postgres: event.target.value })}
          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2.5 text-xs"
        >
          <option value="">{secretsLoading ? '正在读取 Secret…' : '选择 Secret…'}</option>
          {secrets.map((secret) => (
            <option key={secret.id} value={secret.id} disabled={!secret.present}>
              {secret.name} · v{secret.version}
              {secret.present ? '' : ' · 值缺失'}
            </option>
          ))}
        </select>
      </div>
    );
  }
  if (kind === 'minio') {
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={`${project.id}-minio-access`}>{fieldLabel('MinIO Access Key Secret')}</Label>
        <select
          id={`${project.id}-minio-access`}
          aria-label={fieldLabel('MinIO Access Key Secret')}
          value={refs.minioAccess ?? ''}
          disabled={secretsLoading}
          onChange={(event) => onChange({ minioAccess: event.target.value })}
          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2.5 text-xs"
        >
          <option value="">{secretsLoading ? '正在读取 Secret…' : '选择 Secret…'}</option>
          {secrets.map((secret) => (
            <option key={secret.id} value={secret.id} disabled={!secret.present}>
              {secret.name} · v{secret.version}
              {secret.present ? '' : ' · 值缺失'}
            </option>
          ))}
        </select>
        <Label htmlFor={`${project.id}-minio-secret`}>{fieldLabel('MinIO Secret Key Secret')}</Label>
        <select
          id={`${project.id}-minio-secret`}
          aria-label={fieldLabel('MinIO Secret Key Secret')}
          value={refs.minioSecret ?? ''}
          disabled={secretsLoading}
          onChange={(event) => onChange({ minioSecret: event.target.value })}
          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2.5 text-xs"
        >
          <option value="">{secretsLoading ? '正在读取 Secret…' : '选择 Secret…'}</option>
          {secrets.map((secret) => (
            <option key={secret.id} value={secret.id} disabled={!secret.present}>
              {secret.name} · v{secret.version}
              {secret.present ? '' : ' · 值缺失'}
            </option>
          ))}
        </select>
      </div>
    );
  }
  return (
    <p className="text-[11px] text-warn">{MIDDLEWARE_LABELS[kind] ?? kind} 连接适配器尚未支持;此项目当前无法加入工作区。</p>
  );
}

export default function WorkspacesRoute() {
  const workspaces = useWorkspaces();
  const token = useApiToken();
  const [createOpen, setCreateOpen] = useState(false);
  const records = workspaces.data?.workspaces ?? [];
  return (
    <PageScroll>
      <PageHeader
        eyebrow="WORKSPACES"
        title="工作区"
        description="多项目工作区:保存命令、环境变量、连接 profile 与运行目标;按 rev 版本化。"
        actions={
          <Button
            size="sm"
            onClick={() => setCreateOpen(true)}
            disabled={!token.configured}
            title={token.disabledReason ?? '新建工作区'}
          >
            <Plus />
            新建工作区
          </Button>
        }
      />
      <PageBody>
        {workspaces.isLoading ? (
          <ListSkeleton rows={4} />
        ) : workspaces.isError ? (
          <ErrorState error={workspaces.error} onRetry={() => void workspaces.refetch()} title="无法读取工作区" />
        ) : records.length === 0 ? (
          <EmptyState
            icon={Workflow}
            title="没有工作区"
            detail="新建一个可保存的多项目组合:命令、环境变量、连接 profile 与运行目标都会被固化"
            action={
              <Button size="sm" className="mt-1" onClick={() => setCreateOpen(true)} disabled={!token.configured} title={token.disabledReason ?? '新建工作区'}>
                <Plus />
                新建工作区
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
                      {workspace.services.length} 服务 · {workspace.mode} · 更新于 {new Date(workspace.updated_at).toLocaleString('zh-CN', { hour12: false })}
                    </span>
                  </span>
                  <Badge variant="outline">rev {workspace.revision}</Badge>
                </Link>
              </li>
            ))}
          </ul>
        )}
        <CliFooter command={cli.workspaces()} hint="等价 CLI:工作区列表" />
      </PageBody>
      {createOpen && <CreateWorkspaceDialog onClose={() => setCreateOpen(false)} />}
    </PageScroll>
  );
}
