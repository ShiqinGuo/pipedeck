import { Link, useNavigate, useParams } from '@tanstack/react-router';
import {
  AlertTriangle,
  ArrowLeft,
  Braces,
  Check,
  Container,
  Database,
  FileCog,
  GitBranch,
  KeyRound,
  Layers3,
  ListChecks,
  Network,
  Plus,
  Rocket,
  Save,
  Settings2,
  TerminalSquare,
  Trash2,
  Workflow,
  X,
} from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import type { components } from '@/api/schema';
import { api } from '@/api/client';
import {
  useApiToken,
  useCatalog,
  useEnvironments,
  useRuntime,
  useSecrets,
  useWorkspace,
} from '@/api/hooks';
import { CliFooter } from '@/components/cli-command';
import { PlanDialog } from '@/components/plan-dialog';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { BusyLabel, EmptyState, ErrorState, ListSkeleton, MutationError } from '@/components/states';
import { cli } from '@/lib/cli';
import {
  cloneWorkspaceInput,
  defaultComposeTarget,
  defaultConnectionProfile,
  defaultHostTarget,
  invalidCommandReason,
  invalidConnectionReason,
  invalidEnvironmentReason,
  invalidTargetReason,
  isHealthyResource,
} from '@/lib/workspace-editor';
import { MIDDLEWARE_LABELS } from '@/lib/status';
import { cn, formatDate } from '@/lib/utils';

type WorkspaceInput = components['schemas']['WorkspaceInput'];
type WorkspaceService = components['schemas']['WorkspaceService'];
type WorkspacePlanResponse = components['schemas']['WorkspacePlanResponse'];
type ProjectSummary = components['schemas']['ProjectSummary'];
type SecretMetadata = components['schemas']['SecretMetadata'];
type EnvironmentBinding = components['schemas']['EnvironmentBinding'];
type ServiceCommand = components['schemas']['ServiceCommand'];
type ConnectionProfile = components['schemas']['PostgresConnectionProfile'] | components['schemas']['MinioConnectionProfile'];
type PostgresConnectionProfile = components['schemas']['PostgresConnectionProfile'];
type MinioConnectionProfile = components['schemas']['MinioConnectionProfile'];
type RuntimeResource = components['schemas']['RuntimeResource'];
type EnvironmentRecord = components['schemas']['EnvironmentRecord'];
type ExecutionTarget = components['schemas']['HostTarget'] | components['schemas']['ComposeTarget'];
type HostTarget = components['schemas']['HostTarget'];
type ComposeTarget = components['schemas']['ComposeTarget'];
type MiddlewareKind = components['schemas']['MiddlewareKind'];

type InspectorTab = 'commands' | 'environment' | 'target' | 'dependencies';

export default function WorkspaceDetailRoute() {
  const { id } = useParams({ from: '/workspaces/$id' });
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const token = useApiToken();
  const workspace = useWorkspace(id);
  const catalog = useCatalog();
  const runtime = useRuntime();
  const secrets = useSecrets();

  const [draft, setDraft] = useState<WorkspaceInput | null>(null);
  const [selectedServiceIndex, setSelectedServiceIndex] = useState(0);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('commands');
  const [plan, setPlan] = useState<WorkspacePlanResponse | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const projectMap = useMemo(() => new Map((catalog.data?.projects ?? []).map((project) => [project.id, project])), [catalog.data?.projects]);
  const secretRecords = useMemo(() => secrets.data?.secrets ?? [], [secrets.data?.secrets]);
  const resources = useMemo(() => runtime.data?.resources ?? [], [runtime.data?.resources]);

  const record = workspace.data ?? null;
  useEffect(() => {
    if (record) {
      setDraft(cloneWorkspaceInput(record));
      setSelectedServiceIndex(0);
      setPlan(null);
    }
    // record 引用变化(轮询返回新对象)时仅在数据内容变化时重建草稿
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.dataUpdatedAt]);

  const dirty = Boolean(draft && record && JSON.stringify(draft) !== JSON.stringify(cloneWorkspaceInput(record)));
  const selectedService = draft?.services[selectedServiceIndex] ?? null;
  const selectedProject = selectedService ? (projectMap.get(selectedService.project_id) ?? null) : null;

  const refreshAfterWrite = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
      queryClient.invalidateQueries({ queryKey: ['overview'] }),
    ]);
  };

  const saveMutation = useMutation({
    mutationFn: (payload: WorkspaceInput & { expected_revision: number }) => api.updateWorkspace(id, payload),
    onSuccess: async () => {
      setPlan(null);
      await refreshAfterWrite();
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteWorkspace(id, record?.revision ?? 0),
    onSuccess: async () => {
      await refreshAfterWrite();
      void navigate({ to: '/workspaces' });
    },
  });
  const planMutation = useMutation({
    mutationFn: (payload: { expected_revision: number }) => api.createWorkspacePlan(id, payload),
    onSuccess: setPlan,
  });

  const resetDraftFeedback = () => {
    setPlan(null);
    saveMutation.reset();
    planMutation.reset();
  };

  const updateService = (index: number, updater: (service: WorkspaceService) => WorkspaceService) => {
    setDraft((current) =>
      current
        ? { ...current, services: current.services.map((service, serviceIndex) => (serviceIndex === index ? updater(service) : service)) }
        : current,
    );
    resetDraftFeedback();
  };

  const setBinding = (kind: MiddlewareKind, resourceId: string) => {
    setDraft((current) => {
      if (!current) return current;
      const bindings = current.bindings.filter((binding) => binding.kind !== kind);
      return { ...current, bindings: resourceId ? [...bindings, { kind, resource_id: resourceId }] : bindings };
    });
    resetDraftFeedback();
  };

  const updateProfile = (kind: 'postgres' | 'minio', patch: Partial<PostgresConnectionProfile> | Partial<MinioConnectionProfile>) =>
    updateService(selectedServiceIndex, (service) => {
      const existing = service.connection_profiles.find((profile) => profile.kind === kind) ?? defaultConnectionProfile(kind);
      if (!existing) return service;
      const updated = { ...existing, ...patch } as ConnectionProfile;
      return { ...service, connection_profiles: [...service.connection_profiles.filter((profile) => profile.kind !== kind), updated] };
    });

  const secretStateReason = secrets.isLoading ? '正在读取 Secret 状态' : secrets.isError ? '无法读取 Secret 状态，请重试' : null;
  const commandError = draft ? invalidCommandReason(draft.services, projectMap) : null;
  const targetError = draft ? invalidTargetReason(draft.services, projectMap) : null;
  const environmentError = draft ? (secretStateReason ?? invalidEnvironmentReason(draft.services, secretRecords)) : null;
  const connectionError = draft ? (secretStateReason ?? invalidConnectionReason(draft.services, projectMap, secretRecords)) : null;
  const requiredKinds = draft ? [...new Set(draft.services.flatMap((service) => projectMap.get(service.project_id)?.requirements ?? []))] : [];
  const missingDependency = requiredKinds.find((kind) => {
    const resourceId = draft?.bindings.find((binding) => binding.kind === kind)?.resource_id;
    return !resourceId || !resources.some((resource) => resource.id === resourceId && resource.kind === kind && isHealthyResource(resource));
  });
  const dockerUnavailable = !runtime.isLoading && !runtime.isError && runtime.data !== undefined && !runtime.data.docker_available;

  const saveDisabledReason =
    token.disabledReason ??
    (!dirty ? '没有待保存修改' : !draft?.name.trim() ? '工作区名称不能为空' : !draft.services.length ? '工作区至少需要一个服务' : (commandError ?? targetError ?? environmentError ?? connectionError));
  const planDisabledReason =
    token.disabledReason ??
    (dirty
      ? '请先保存工作区修改'
      : (commandError ?? targetError ?? environmentError ?? connectionError) ??
        (!record ? '未加载工作区' : runtime.isLoading ? '正在读取 Docker 状态' : runtime.isError ? '无法读取 Docker 状态' : dockerUnavailable ? (runtime.data?.recovery ?? 'Docker 不可用') : missingDependency ? `缺少健康的 ${MIDDLEWARE_LABELS[missingDependency] ?? missingDependency} 绑定` : planMutation.isPending ? '正在运行预检' : null));
  const deleteDisabledReason = token.disabledReason ?? (deleteMutation.isPending ? '正在删除' : null);

  if (workspace.isLoading) {
    return (
      <PageScroll>
        <PageHeader eyebrow="WORKSPACE" title="工作区详情" compact />
        <PageBody>
          <ListSkeleton rows={4} />
        </PageBody>
      </PageScroll>
    );
  }
  if (workspace.isError || !record) {
    return (
      <PageScroll>
        <PageHeader eyebrow="WORKSPACE" title="工作区详情" compact />
        <PageBody>
          <ErrorState error={workspace.error} onRetry={() => void workspace.refetch()} title="无法读取工作区" />
        </PageBody>
      </PageScroll>
    );
  }

  return (
    <PageScroll>
      <PageHeader
        eyebrow="WORKSPACE"
        compact
        title={record.name}
        description={
          <>
            {record.services.length} 个服务 · 更新于 {formatDate(record.updated_at)} ·{' '}
            <Badge variant={dirty ? 'warn' : 'outline'}>{dirty ? '未保存' : `rev ${record.revision}`}</Badge>
          </>
        }
        actions={
          <>
            <Button asChild variant="ghost" size="sm">
              <Link to="/workspaces">
                <ArrowLeft />
                返回列表
              </Link>
            </Button>
            <div className="flex items-center overflow-hidden rounded-sm border border-[#46524a]" aria-label="运行模式">
              {(['development', 'integrated'] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={draft?.mode === mode}
                  className={cn('flex h-8 items-center gap-1.5 px-2.5 text-xs', draft?.mode === mode ? 'bg-surface-3 text-foreground' : 'text-muted-foreground hover:bg-accent')}
                  onClick={() => {
                    setDraft((current) => (current ? { ...current, mode } : current));
                    resetDraftFeedback();
                  }}
                >
                  <TerminalSquare className="size-3.5" />
                  {mode === 'development' ? '开发' : '集成'}
                </button>
              ))}
            </div>
            <Button
              variant="secondary"
              size="sm"
              disabled={Boolean(saveDisabledReason) || saveMutation.isPending}
              title={saveDisabledReason ?? '保存工作区'}
              onClick={() =>
                draft &&
                saveMutation.mutate({
                  ...draft,
                  name: draft.name.trim() || draft.name,
                  expected_revision: record.revision,
                })
              }
            >
              {saveMutation.isPending ? <BusyLabel>保存中</BusyLabel> : <Save />}
              保存
            </Button>
            <Button
              size="sm"
              disabled={Boolean(planDisabledReason)}
              title={planDisabledReason ?? '运行预检'}
              onClick={() => planMutation.mutate({ expected_revision: record.revision })}
            >
              {planMutation.isPending ? <BusyLabel>预检中</BusyLabel> : <ListChecks />}
              运行预检
            </Button>
            <Button variant="secondary" size="sm" title={token.disabledReason ?? '删除工作区'} disabled={!token.configured} onClick={() => setDeleteOpen(true)}>
              <Trash2 />
              删除
            </Button>
          </>
        }
      />
      <PageBody>
        <div className="grid gap-1.5">
          <Label htmlFor="workspace-name-input">工作区名称</Label>
          <Input
            id="workspace-name-input"
            value={draft?.name ?? ''}
            onChange={(event) => {
              setDraft((current) => (current ? { ...current, name: event.target.value } : current));
              resetDraftFeedback();
            }}
            className="max-w-md"
          />
        </div>

        {(saveMutation.isError || planMutation.isError || deleteMutation.isError) && (
          <MutationError error={saveMutation.error ?? planMutation.error ?? deleteMutation.error} />
        )}
        {dockerUnavailable && (
          <ErrorState
            error={new Error(runtime.data?.error_code ?? 'Docker 不可用')}
            title="Docker 不可用"
            onRetry={() => void runtime.refetch()}
          />
        )}

        {draft && (
          <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
            {/* 服务列表 */}
            <aside className="rounded-md border border-border bg-card">
              <header className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
                <span className="flex items-center gap-2 text-xs font-semibold">
                  <Workflow className="size-4 text-muted-foreground" />
                  服务
                </span>
                <select
                  aria-label="添加项目服务"
                  value=""
                  onChange={(event) => {
                    const projectId = event.target.value;
                    const project = projectMap.get(projectId);
                    if (!project || draft.services.some((service) => service.project_id === projectId)) return;
                    const nextService: WorkspaceService = {
                      project_id: project.id,
                      commands: project.commands.map((command) => ({
                        id: command.id,
                        label: command.label,
                        kind: command.kind ?? 'quality',
                        argv: [...command.argv],
                        long_running: command.long_running,
                      })),
                      environment: [],
                      connection_profiles: project.requirements.flatMap((kind) => {
                        const profile = defaultConnectionProfile(kind);
                        return profile ? [profile] : [];
                      }),
                      execution_target: defaultHostTarget(project),
                    };
                    setDraft({ ...draft, services: [...draft.services, nextService] });
                    setSelectedServiceIndex(draft.services.length);
                    resetDraftFeedback();
                  }}
                  className="h-8 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
                >
                  <option value="">添加项目…</option>
                  {(catalog.data?.projects ?? [])
                    .filter((project) => !draft.services.some((service) => service.project_id === project.id))
                    .map((project) => (
                      <option key={project.id} value={project.id}>
                        {project.name}
                      </option>
                    ))}
                </select>
              </header>
              <ul>
                {draft.services.map((service, index) => {
                  const project = projectMap.get(service.project_id);
                  return (
                    <li key={`${service.project_id}-${index}`}>
                      <button
                        type="button"
                        className={cn(
                          'grid w-full min-h-12 grid-cols-[minmax(0,1fr)_auto] items-center gap-2 border-b border-[#252c28] px-3 py-2 text-left hover:bg-surface-2',
                          selectedServiceIndex === index && 'bg-surface-2',
                        )}
                        data-testid="service-row"
                        onClick={() => setSelectedServiceIndex(index)}
                      >
                        <span className="min-w-0">
                          <span className="block truncate text-xs font-semibold">{project?.name ?? service.project_id}</span>
                          <span className="block truncate text-[11px] text-muted-foreground">
                            {service.commands.length} 命令 · {service.execution_target.kind === 'compose' ? 'Compose' : 'Host'}
                          </span>
                        </span>
                        {project?.dirty ? <AlertTriangle className="size-4 text-warn" /> : <Check className="size-4 text-ok" />}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </aside>

            {/* 服务 inspector(容器不用 <section>:避免与命令编辑器的 section 形成嵌套歧义) */}
            <div className="rounded-md border border-border bg-card">
              {!selectedService || !selectedProject ? (
                <div className="p-4">
                  <EmptyState icon={Settings2} title="选择服务" detail="配置命令、环境变量、运行目标和中间件连接" />
                </div>
              ) : (
                <Tabs value={inspectorTab} onValueChange={(value) => setInspectorTab(value as InspectorTab)}>
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
                    <div className="min-w-0">
                      <h2 className="truncate text-xs font-semibold">{selectedProject.name}</h2>
                      <p className="flex items-center gap-1 font-mono text-[11px] text-muted-foreground">
                        <GitBranch className="size-3" />
                        {selectedProject.branch}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`移除 ${selectedProject.name}`}
                      disabled={draft.services.length === 1}
                      title={draft.services.length === 1 ? '工作区至少需要一个服务' : '从工作区移除'}
                      onClick={() => {
                        setDraft((current) =>
                          current ? { ...current, services: current.services.filter((_, serviceIndex) => serviceIndex !== selectedServiceIndex) } : current,
                        );
                        setSelectedServiceIndex((current) => Math.max(0, current - 1));
                        resetDraftFeedback();
                      }}
                    >
                      <X />
                      移除服务
                    </Button>
                  </div>
                  <TabsList className="m-2" aria-label="服务配置">
                    <TabsTrigger value="commands">
                      <TerminalSquare className="size-3.5" />
                      命令
                    </TabsTrigger>
                    <TabsTrigger value="environment">
                      <KeyRound className="size-3.5" />
                      环境
                    </TabsTrigger>
                    <TabsTrigger value="target">
                      <Container className="size-3.5" />
                      目标
                    </TabsTrigger>
                    <TabsTrigger value="dependencies">
                      <Database className="size-3.5" />
                      依赖
                    </TabsTrigger>
                  </TabsList>
                  <div className="px-3 pb-3">
                    <TabsContent value="commands">
                      <CommandEditor service={selectedService} onChange={(commands) => updateService(selectedServiceIndex, (service) => ({ ...service, commands }))} />
                    </TabsContent>
                    <TabsContent value="environment">
                      <EnvironmentEditor
                        bindings={selectedService.environment}
                        secrets={secretRecords}
                        secretsLoading={secrets.isLoading}
                        onAdd={() =>
                          updateService(selectedServiceIndex, (service) => ({
                            ...service,
                            environment: [...service.environment, { name: '', source: 'literal', value: '', reference: null }],
                          }))
                        }
                        onChange={(index, patch) =>
                          updateService(selectedServiceIndex, (service) => ({
                            ...service,
                            environment: service.environment.map((binding, bindingIndex) => (bindingIndex === index ? { ...binding, ...patch } : binding)),
                          }))
                        }
                        onRemove={(index) =>
                          updateService(selectedServiceIndex, (service) => ({
                            ...service,
                            environment: service.environment.filter((_, bindingIndex) => bindingIndex !== index),
                          }))
                        }
                      />
                    </TabsContent>
                    <TabsContent value="target">
                      <TargetEditor
                        project={selectedProject}
                        target={selectedService.execution_target}
                        onChange={(execution_target) => updateService(selectedServiceIndex, (service) => ({ ...service, execution_target }))}
                      />
                    </TabsContent>
                    <TabsContent value="dependencies">
                      <DependencyEditor
                        project={selectedProject}
                        service={selectedService}
                        bindings={draft.bindings}
                        resources={resources}
                        secrets={secretRecords}
                        secretsLoading={secrets.isLoading}
                        onBindingChange={setBinding}
                        onProfileChange={updateProfile}
                      />
                    </TabsContent>
                  </div>
                </Tabs>
              )}
            </div>
          </div>
        )}

        <EnvironmentsSection
          workspaceId={id}
          tokenReady={token.configured}
          tokenReason={token.disabledReason}
          revision={record?.revision ?? 1}
        />

        <CliFooter command={cli.workspaces()} hint="等价 CLI:工作区列表与环境管理" />
      </PageBody>

      {plan && <PlanDialog plan={plan} onClose={() => setPlan(null)} />}
      {deleteOpen && record && (
        <Dialog open onOpenChange={(open) => !open && setDeleteOpen(false)}>
          <DialogContent aria-describedby={undefined} data-testid="delete-workspace-dialog">
            <DialogHeader eyebrow="DESTRUCTIVE ACTION" title="删除工作区" />
            <DialogBody>
              <p className="text-xs leading-relaxed text-muted-foreground">
                仅可删除尚未产生预检计划或运行历史的本地配置;已有历史的工作区必须保留。不会删除仓库 checkout 或外部中间件。
              </p>
              {deleteMutation.isError && <MutationError error={deleteMutation.error} />}
            </DialogBody>
            <DialogFooter>
              <Button variant="secondary" onClick={() => setDeleteOpen(false)}>
                取消
              </Button>
              <Button variant="destructive" disabled={Boolean(deleteDisabledReason)} title={deleteDisabledReason ?? '删除工作区'} onClick={() => deleteMutation.mutate()}>
                {deleteMutation.isPending ? <BusyLabel>删除中</BusyLabel> : <Trash2 />}
                删除工作区
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </PageScroll>
  );
}

/* ============ 命令编辑器(argv token 保留空格边界) ============ */

function CommandEditor({ service, onChange }: { service: WorkspaceService; onChange: (commands: ServiceCommand[]) => void }) {
  const update = (index: number, patch: Partial<ServiceCommand>) =>
    onChange(service.commands.map((command, commandIndex) => (commandIndex === index ? { ...command, ...patch } : command)));
  const updateToken = (commandIndex: number, tokenIndex: number, value: string) => {
    const command = service.commands[commandIndex];
    if (!command) return;
    update(commandIndex, { argv: command.argv.map((token, index) => (index === tokenIndex ? value : token)) });
  };
  const removeToken = (commandIndex: number, tokenIndex: number) => {
    const command = service.commands[commandIndex];
    if (!command) return;
    update(commandIndex, { argv: command.argv.filter((_, index) => index !== tokenIndex) });
  };
  return (
    <div className="grid gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold">生命周期命令</h3>
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            onChange([...service.commands, { id: `command-${service.commands.length + 1}`, label: '自定义命令', kind: 'quality', argv: [''], long_running: false }])
          }
        >
          <Plus />
          添加命令
        </Button>
      </div>
      {service.commands.map((command, index) => (
        <section key={`${command.id}-${index}`} className="rounded-md border border-border p-2.5" aria-label={`${command.label} argv token`}>
          <div className="flex flex-wrap items-center gap-2">
            <Input aria-label={`命令 ${index + 1} 名称`} value={command.label} onChange={(event) => update(index, { label: event.target.value })} className="w-32" />
            <select
              aria-label={`命令 ${index + 1} 阶段`}
              value={command.kind}
              onChange={(event) => update(index, { kind: event.target.value as ServiceCommand['kind'] })}
              className="h-9 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
            >
              <option value="inspect">检查</option>
              <option value="dependencies">依赖</option>
              <option value="quality">质量</option>
              <option value="build">构建</option>
              <option value="deploy">部署</option>
              <option value="start">启动</option>
            </select>
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Checkbox checked={command.long_running} onCheckedChange={(value) => update(index, { long_running: Boolean(value) })} aria-label={`${command.label} 长运行`} />
              长运行
            </label>
            <Button variant="ghost" size="icon-sm" aria-label={`删除 ${command.label}`} onClick={() => onChange(service.commands.filter((_, commandIndex) => commandIndex !== index))}>
              <Trash2 />
            </Button>
          </div>
          <div className="mt-2 grid gap-1.5">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1 font-mono text-[10px] tracking-wider text-muted-foreground uppercase">
                <Braces className="size-3.5" />
                ARGV TOKENS
              </span>
              <Button variant="ghost" size="sm" onClick={() => update(index, { argv: [...command.argv, ''] })}>
                <Plus />
                添加 token
              </Button>
            </div>
            {command.argv.map((token, tokenIndex) => (
              <div key={tokenIndex} className="flex items-center gap-1.5">
                <span className="font-mono text-[10px] text-[#5f6a64]">{String(tokenIndex).padStart(2, '0')}</span>
                <Input
                  aria-label={`${command.label} 参数 ${tokenIndex + 1}`}
                  value={token}
                  onChange={(event) => updateToken(index, tokenIndex, event.target.value)}
                  placeholder={tokenIndex === 0 ? '可执行程序' : '参数，可包含空格'}
                  className="flex-1"
                />
                <Button variant="ghost" size="icon-sm" aria-label={`删除 ${command.label} 参数 ${tokenIndex + 1}`} onClick={() => removeToken(index, tokenIndex)}>
                  <X />
                </Button>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/* ============ 环境变量编辑器 ============ */

const ENV_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;

function EnvironmentEditor({
  bindings,
  secrets,
  secretsLoading,
  onAdd,
  onChange,
  onRemove,
}: {
  bindings: EnvironmentBinding[];
  secrets: SecretMetadata[];
  secretsLoading: boolean;
  onAdd: () => void;
  onChange: (index: number, patch: Partial<EnvironmentBinding>) => void;
  onRemove: (index: number) => void;
}) {
  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold">环境变量</h3>
        <Button variant="ghost" size="sm" onClick={onAdd}>
          <Plus />
          添加变量
        </Button>
      </div>
      {bindings.length === 0 ? (
        <EmptyState icon={KeyRound} title="没有环境变量" detail="Literal 仅用于非敏感值；敏感值使用本机环境变量或系统 Secret" />
      ) : (
        <div className="grid gap-1.5">
          {bindings.map((binding, index) => {
            const invalidSensitive = binding.source === 'literal' && /SECRET|PASSWORD|TOKEN|API_KEY|PRIVATE_KEY|DATABASE_URL|CREDENTIAL/i.test(binding.name);
            const invalidReference =
              binding.source === 'host-env'
                ? !binding.reference || !ENV_NAME_PATTERN.test(binding.reference)
                : binding.source === 'secret-store'
                  ? Boolean(
                      !binding.reference ||
                        !secrets.some((candidate) => candidate.id === binding.reference && candidate.present),
                    )
                  : false;
            return (
              <div key={index} className={cn('grid gap-1.5 rounded-sm border border-transparent bg-surface-2 p-2 md:grid-cols-[minmax(0,1fr)_130px_minmax(0,1.4fr)_auto]', (invalidSensitive || invalidReference) && 'border-[#754246]')}>
                <Input aria-label={`环境变量 ${index + 1} 名称`} value={binding.name} onChange={(event) => onChange(index, { name: event.target.value })} placeholder="DATABASE_URL" />
                <select
                  aria-label={`${binding.name || `变量 ${index + 1}`} 来源`}
                  value={binding.source}
                  onChange={(event) => {
                    const source = event.target.value as EnvironmentBinding['source'];
                    onChange(index, source === 'literal' ? { source, value: '', reference: null } : { source, value: null, reference: '' });
                  }}
                  className="h-9 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
                >
                  <option value="literal">Literal</option>
                  <option value="host-env">Host env</option>
                  <option value="secret-store">Secret</option>
                </select>
                {binding.source === 'secret-store' ? (
                  <select
                    aria-label={`${binding.name || `变量 ${index + 1}`} Secret`}
                    value={binding.reference ?? ''}
                    disabled={secretsLoading}
                    onChange={(event) => onChange(index, { reference: event.target.value })}
                    className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
                  >
                    <option value="">{secretsLoading ? '正在读取 Secret…' : '选择 Secret…'}</option>
                    {secrets.map((secret) => (
                      <option key={secret.id} value={secret.id} disabled={!secret.present}>
                        {secret.name} · v{secret.version}
                        {secret.present ? '' : ' · 值缺失'}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Input
                    aria-label={`${binding.name || `变量 ${index + 1}`} ${binding.source === 'literal' ? '值' : '引用'}`}
                    value={binding.source === 'literal' ? (binding.value ?? '') : (binding.reference ?? '')}
                    onChange={(event) => onChange(index, binding.source === 'literal' ? { value: event.target.value } : { reference: event.target.value })}
                    placeholder={binding.source === 'literal' ? '非敏感配置值' : 'HOST_ENV_NAME'}
                  />
                )}
                <Button variant="ghost" size="icon-sm" aria-label={`删除 ${binding.name || `变量 ${index + 1}`}`} onClick={() => onRemove(index)}>
                  <Trash2 />
                </Button>
                {(invalidSensitive || invalidReference) && (
                  <small className="text-[11px] text-danger md:col-span-4">
                    {invalidSensitive ? '敏感变量必须使用 Host env 或 Secret' : binding.source === 'host-env' ? 'Host env 引用必须是合法环境变量名' : '缺少可用的 Secret 引用'}
                  </small>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ============ 运行目标编辑器(Host / Compose) ============ */

function TargetEditor({
  project,
  target,
  onChange,
}: {
  project: ProjectSummary;
  target: ExecutionTarget;
  onChange: (target: ExecutionTarget) => void;
}) {
  const switchTarget = (kind: ExecutionTarget['kind']) => {
    if (kind === target.kind) return;
    if (kind === 'compose') {
      const next = defaultComposeTarget(project);
      if (target.kind === 'host' && target.endpoints.length > 0)
        next.endpoints = target.endpoints.map((endpoint) => ({ name: endpoint.name, protocol: endpoint.protocol, host_port: endpoint.host_port, container_port: endpoint.host_port }));
      onChange(next);
      return;
    }
    const next = defaultHostTarget(project);
    if (target.kind === 'compose')
      next.endpoints = target.endpoints.map((endpoint) => ({ name: endpoint.name, protocol: endpoint.protocol, host_port: endpoint.host_port, injection: { kind: 'command-owned' } as const }));
    onChange(next);
  };
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold">运行目标</h3>
        <div className="flex items-center gap-1 overflow-hidden rounded-sm border border-[#46524a]" aria-label="运行目标类型">
          <button type="button" aria-pressed={target.kind === 'host'} className={cn('flex h-8 items-center gap-1.5 px-2.5 text-xs', target.kind === 'host' ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')} onClick={() => switchTarget('host')}>
            <TerminalSquare className="size-3.5" />
            本机进程
          </button>
          <button type="button" aria-pressed={target.kind === 'compose'} className={cn('flex h-8 items-center gap-1.5 px-2.5 text-xs', target.kind === 'compose' ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')} onClick={() => switchTarget('compose')}>
            <Container className="size-3.5" />
            Docker Compose
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2 rounded-sm bg-surface-2 px-2.5 py-2 text-[11px] text-muted-foreground">
        <FileCog className="size-4 shrink-0" />
        <span>
          容器检测:{project.container_capabilities.compose_files.length > 0 ? `Compose: ${project.container_capabilities.compose_files.join(', ')}` : '未检测到 Compose 文件'} ·{' '}
          {project.container_capabilities.dockerfile ? `Dockerfile: ${project.container_capabilities.dockerfile}` : '未检测到 Dockerfile'}
        </span>
      </div>
      {target.kind === 'host' ? <HostTargetEditor target={target} onChange={onChange} /> : <ComposeTargetEditor target={target} onChange={onChange} />}
    </div>
  );
}

function HostTargetEditor({ target, onChange }: { target: HostTarget; onChange: (target: ExecutionTarget) => void }) {
  const updateEndpoint = (index: number, patch: Partial<HostTarget['endpoints'][number]>) =>
    onChange({ ...target, endpoints: target.endpoints.map((endpoint, endpointIndex) => (endpointIndex === index ? { ...endpoint, ...patch } : endpoint)) });
  return (
    <>
      <section className="rounded-md border border-border p-2.5">
        <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="flex items-center gap-1.5 text-xs font-semibold">
              <Network className="size-3.5" />
              Host endpoints
            </p>
            <p className="text-[11px] text-muted-foreground">声明端口所有权以及注入命令的方式</p>
          </div>
          <Button variant="ghost" size="sm" onClick={() => onChange({ ...target, endpoints: [...target.endpoints, { name: `port-${target.endpoints.length + 1}`, protocol: 'tcp', host_port: 3000, injection: { kind: 'command-owned' } }] })}>
            <Plus />
            添加 endpoint
          </Button>
        </header>
        {target.endpoints.length === 0 ? (
          <EmptyState icon={Network} title="没有 endpoint" detail="无网络入口的 worker 可以保持为空" />
        ) : (
          <div className="grid gap-1.5">
            {target.endpoints.map((endpoint, index) => (
              <div key={index} className="grid items-center gap-1.5 rounded-sm bg-surface-2 p-2 md:grid-cols-[minmax(0,1fr)_80px_100px_150px_minmax(0,1fr)_auto]">
                <Input aria-label={`${endpoint.name} endpoint 名称`} value={endpoint.name} onChange={(event) => updateEndpoint(index, { name: event.target.value })} />
                <select aria-label={`${endpoint.name} 协议`} value={endpoint.protocol} onChange={(event) => updateEndpoint(index, { protocol: event.target.value as 'tcp' | 'udp' })} className="h-9 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs">
                  <option value="tcp">TCP</option>
                  <option value="udp">UDP</option>
                </select>
                <Input type="number" min={1} max={65535} aria-label={`${endpoint.name} Host 端口`} value={endpoint.host_port} onChange={(event) => updateEndpoint(index, { host_port: Number(event.target.value) })} />
                <select
                  aria-label={`${endpoint.name} 注入方式`}
                  value={endpoint.injection.kind}
                  onChange={(event) => {
                    const kind = event.target.value as 'command-owned' | 'environment' | 'argument';
                    updateEndpoint(index, {
                      injection: kind === 'environment' ? { kind, name: 'PORT' } : kind === 'argument' ? { kind, option: '--port' } : { kind: 'command-owned' },
                    });
                  }}
                  className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
                >
                  <option value="command-owned">命令自行监听</option>
                  <option value="environment">环境变量</option>
                  <option value="argument">命令参数</option>
                </select>
                {endpoint.injection.kind === 'environment' && (
                  <Input aria-label={`${endpoint.name} 端口环境变量`} value={endpoint.injection.name} onChange={(event) => updateEndpoint(index, { injection: { kind: 'environment', name: event.target.value } })} />
                )}
                {endpoint.injection.kind === 'argument' && (
                  <Input aria-label={`${endpoint.name} 端口参数选项`} value={endpoint.injection.option} onChange={(event) => updateEndpoint(index, { injection: { kind: 'argument', option: event.target.value } })} />
                )}
                {endpoint.injection.kind === 'command-owned' && <span className="text-[11px] text-muted-foreground">由命令负责</span>}
                <Button variant="ghost" size="icon-sm" aria-label={`删除 endpoint ${endpoint.name}`} onClick={() => onChange({ ...target, endpoints: target.endpoints.filter((_, endpointIndex) => endpointIndex !== index) })}>
                  <Trash2 />
                </Button>
              </div>
            ))}
          </div>
        )}
      </section>
      <ReadinessEditor endpoints={target.endpoints.map((endpoint) => endpoint.name)} value={target.readiness} allowNone onChange={(readiness) => onChange({ ...target, readiness })} />
      <div className="grid grid-cols-2 gap-2">
        <div className="grid gap-1">
          <Label htmlFor="host-readiness-timeout">Readiness timeout</Label>
          <Input id="host-readiness-timeout" type="number" min={1} max={900} value={target.readiness_timeout} onChange={(event) => onChange({ ...target, readiness_timeout: Number(event.target.value) })} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="host-stop-timeout">Stop timeout</Label>
          <Input id="host-stop-timeout" type="number" min={1} max={300} value={target.stop_timeout} onChange={(event) => onChange({ ...target, stop_timeout: Number(event.target.value) })} />
        </div>
      </div>
    </>
  );
}

function ComposeTargetEditor({ target, onChange }: { target: ComposeTarget; onChange: (target: ExecutionTarget) => void }) {
  const source = target.source;
  const updateEndpoint = (index: number, patch: Partial<ComposeTarget['endpoints'][number]>) =>
    onChange({ ...target, endpoints: target.endpoints.map((endpoint, endpointIndex) => (endpointIndex === index ? { ...endpoint, ...patch } : endpoint)) });
  const switchSource = (kind: ComposeTarget['source']['kind']) => {
    if (kind === source.kind) return;
    onChange({
      ...target,
      source: kind === 'existing-compose' ? { kind, compose_files: ['compose.yml'], profiles: [], service_names: ['app'] } : { kind, context: '.', dockerfile: 'Dockerfile' },
    });
  };
  return (
    <>
      <section className="rounded-md border border-border p-2.5">
        <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="flex items-center gap-1.5 text-xs font-semibold">
              <Layers3 className="size-3.5" />
              Compose source
            </p>
            <p className="text-[11px] text-muted-foreground">所有路径都相对当前项目 checkout</p>
          </div>
          <div className="flex items-center gap-1 overflow-hidden rounded-sm border border-[#46524a]">
            <button type="button" aria-pressed={source.kind === 'existing-compose'} className={cn('flex h-8 items-center px-2.5 text-xs', source.kind === 'existing-compose' ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')} onClick={() => switchSource('existing-compose')}>
              已有 Compose
            </button>
            <button type="button" aria-pressed={source.kind === 'dockerfile'} className={cn('flex h-8 items-center px-2.5 text-xs', source.kind === 'dockerfile' ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')} onClick={() => switchSource('dockerfile')}>
              Dockerfile
            </button>
          </div>
        </header>
        {source.kind === 'existing-compose' ? (
          <div className="grid gap-2">
            <StringListEditor label="Compose 文件" values={source.compose_files} placeholder="compose.yml" required onChange={(compose_files) => onChange({ ...target, source: { ...source, compose_files } })} />
            <StringListEditor label="Profiles" values={source.profiles} placeholder="dev" onChange={(profiles) => onChange({ ...target, source: { ...source, profiles } })} />
            <StringListEditor label="Service names" values={source.service_names} placeholder="api" required onChange={(service_names) => onChange({ ...target, source: { ...source, service_names } })} />
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            <div className="grid gap-1">
              <Label htmlFor="compose-build-context">Build context</Label>
              <Input id="compose-build-context" aria-label="Dockerfile build context" value={source.context} onChange={(event) => onChange({ ...target, source: { ...source, context: event.target.value } })} />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="compose-dockerfile">Dockerfile</Label>
              <Input id="compose-dockerfile" aria-label="Dockerfile 相对路径" value={source.dockerfile} onChange={(event) => onChange({ ...target, source: { ...source, dockerfile: event.target.value } })} />
            </div>
          </div>
        )}
      </section>
      <section className="rounded-md border border-border p-2.5">
        <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="flex items-center gap-1.5 text-xs font-semibold">
              <Network className="size-3.5" />
              Compose endpoints
            </p>
            <p className="text-[11px] text-muted-foreground">映射 localhost host port 到容器端口</p>
          </div>
          <Button variant="ghost" size="sm" onClick={() => onChange({ ...target, endpoints: [...target.endpoints, { name: `port-${target.endpoints.length + 1}`, protocol: 'tcp', host_port: 3000, container_port: 3000 }] })}>
            <Plus />
            添加 endpoint
          </Button>
        </header>
        <div className="grid gap-1.5">
          {target.endpoints.map((endpoint, index) => (
            <div key={index} className="grid items-center gap-1.5 rounded-sm bg-surface-2 p-2 md:grid-cols-[minmax(0,1fr)_80px_100px_100px_auto]">
              <Input aria-label={`${endpoint.name} Compose endpoint 名称`} value={endpoint.name} onChange={(event) => updateEndpoint(index, { name: event.target.value })} />
              <select aria-label={`${endpoint.name} Compose 协议`} value={endpoint.protocol} onChange={(event) => updateEndpoint(index, { protocol: event.target.value as 'tcp' | 'udp' })} className="h-9 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs">
                <option value="tcp">TCP</option>
                <option value="udp">UDP</option>
              </select>
              <Input type="number" min={1} max={65535} aria-label={`${endpoint.name} Host 端口`} value={endpoint.host_port} onChange={(event) => updateEndpoint(index, { host_port: Number(event.target.value) })} />
              <Input type="number" min={1} max={65535} aria-label={`${endpoint.name} 容器端口`} value={endpoint.container_port} onChange={(event) => updateEndpoint(index, { container_port: Number(event.target.value) })} />
              <Button variant="ghost" size="icon-sm" aria-label={`删除 Compose endpoint ${endpoint.name}`} onClick={() => onChange({ ...target, endpoints: target.endpoints.filter((_, endpointIndex) => endpointIndex !== index) })}>
                <Trash2 />
              </Button>
            </div>
          ))}
        </div>
      </section>
      <ReadinessEditor endpoints={target.endpoints.map((endpoint) => endpoint.name)} value={target.readiness} onChange={(readiness) => readiness && onChange({ ...target, readiness })} />
      <div className="grid gap-1 md:w-56">
        <Label htmlFor="compose-wait-timeout">Compose wait timeout</Label>
        <Input id="compose-wait-timeout" type="number" min={1} max={900} value={target.wait_timeout} onChange={(event) => onChange({ ...target, wait_timeout: Number(event.target.value) })} />
      </div>
    </>
  );
}

function ReadinessEditor({
  endpoints,
  value,
  allowNone = false,
  onChange,
}: {
  endpoints: string[];
  value: components['schemas']['HttpReadiness'] | components['schemas']['TcpReadiness'] | null | undefined;
  allowNone?: boolean;
  onChange: (value: components['schemas']['HttpReadiness'] | components['schemas']['TcpReadiness'] | null) => void;
}) {
  const firstEndpoint = endpoints[0] ?? '';
  return (
    <section className="rounded-md border border-border p-2.5">
      <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold">Readiness</p>
          <p className="text-[11px] text-muted-foreground">Run 仅在显式探针通过后进入可用状态</p>
        </div>
        <div className="flex items-center gap-1 overflow-hidden rounded-sm border border-[#46524a]">
          {allowNone && (
            <button type="button" aria-pressed={!value} className={cn('flex h-8 items-center px-2.5 text-xs', !value ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')} onClick={() => onChange(null)}>
              无
            </button>
          )}
          <button
            type="button"
            disabled={!firstEndpoint}
            aria-pressed={value?.kind === 'http'}
            className={cn('flex h-8 items-center px-2.5 text-xs', value?.kind === 'http' ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')}
            onClick={() => onChange({ kind: 'http', endpoint: value?.endpoint || firstEndpoint, path: value?.kind === 'http' ? value.path : '/health' })}
          >
            HTTP
          </button>
          <button
            type="button"
            disabled={!firstEndpoint}
            aria-pressed={value?.kind === 'tcp'}
            className={cn('flex h-8 items-center px-2.5 text-xs', value?.kind === 'tcp' ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')}
            onClick={() => onChange({ kind: 'tcp', endpoint: value?.endpoint || firstEndpoint })}
          >
            TCP
          </button>
        </div>
      </header>
      {value && (
        <div className="grid grid-cols-2 gap-2">
          <div className="grid gap-1">
            <Label htmlFor="readiness-endpoint">Endpoint</Label>
            <select id="readiness-endpoint" aria-label="Readiness endpoint" value={value.endpoint} onChange={(event) => onChange({ ...value, endpoint: event.target.value })} className="h-9 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs">
              {endpoints.map((endpoint) => (
                <option key={endpoint} value={endpoint}>
                  {endpoint}
                </option>
              ))}
            </select>
          </div>
          {value.kind === 'http' && (
            <div className="grid gap-1">
              <Label htmlFor="readiness-path">Path</Label>
              <Input id="readiness-path" aria-label="HTTP readiness path" value={value.path} onChange={(event) => onChange({ ...value, path: event.target.value })} />
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function StringListEditor({
  label,
  values,
  placeholder,
  required = false,
  onChange,
}: {
  label: string;
  values: string[];
  placeholder: string;
  required?: boolean;
  onChange: (values: string[]) => void;
}) {
  return (
    <section className="grid gap-1">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-muted-foreground">
          {label}
          {required && <b className="ml-1 text-warn">REQUIRED</b>}
        </span>
        <Button variant="ghost" size="sm" aria-label={`添加 ${label}`} onClick={() => onChange([...values, ''])}>
          <Plus />
        </Button>
      </div>
      {values.map((value, index) => (
        <div key={index} className="flex items-center gap-1.5">
          <Input aria-label={`${label} ${index + 1}`} value={value} placeholder={placeholder} onChange={(event) => onChange(values.map((item, itemIndex) => (itemIndex === index ? event.target.value : item)))} className="flex-1" />
          <Button variant="ghost" size="icon-sm" aria-label={`删除 ${label} ${index + 1}`} onClick={() => onChange(values.filter((_, itemIndex) => itemIndex !== index))}>
            <X />
          </Button>
        </div>
      ))}
    </section>
  );
}

/* ============ 依赖(绑定 + 连接 profile)编辑器 ============ */

function DependencyEditor({
  project,
  service,
  bindings,
  resources,
  secrets,
  secretsLoading,
  onBindingChange,
  onProfileChange,
}: {
  project: ProjectSummary;
  service: WorkspaceService;
  bindings: components['schemas']['MiddlewareBinding'][];
  resources: RuntimeResource[];
  secrets: SecretMetadata[];
  secretsLoading: boolean;
  onBindingChange: (kind: MiddlewareKind, resourceId: string) => void;
  onProfileChange: (kind: 'postgres' | 'minio', patch: Partial<PostgresConnectionProfile> | Partial<MinioConnectionProfile>) => void;
}) {
  const endpointLabel = (resource: RuntimeResource | undefined, containerPort: number) => {
    const endpoint = resource?.endpoints.find((candidate) => candidate.protocol === 'tcp' && candidate.container_port === containerPort);
    return endpoint ? `${endpoint.host}:${endpoint.host_port} → ${endpoint.container_port}/tcp` : null;
  };
  return (
    <div className="grid gap-3">
      <div>
        <h3 className="text-xs font-semibold">连接与注入</h3>
        <p className="text-[11px] text-muted-foreground">容器绑定属于工作区;连接 profile 属于当前服务。预检会解析宿主机 endpoint,并只展示脱敏输出。</p>
      </div>
      {project.requirements.length === 0 ? (
        <EmptyState icon={Database} title="无需外部中间件" detail="当前服务没有声明 PostgreSQL、Redis、Elasticsearch 或 MinIO" />
      ) : (
        <div className="grid gap-2">
          {project.requirements.map((kind) => {
            const candidates = resources.filter((resource) => resource.kind === kind);
            const selectedId = bindings.find((binding) => binding.kind === kind)?.resource_id ?? '';
            const selected = candidates.find((resource) => resource.id === selectedId);
            const expectedPort = kind === 'postgres' ? 5432 : kind === 'minio' ? 9000 : 0;
            const endpoint = expectedPort ? endpointLabel(selected, expectedPort) : null;
            const profile = service.connection_profiles.find((candidate) => candidate.kind === kind);
            const supported = kind === 'postgres' || kind === 'minio';
            return (
              <section key={kind} className={cn('rounded-md border p-2.5', supported ? 'border-border' : 'border-[#754246]')} aria-label={`${MIDDLEWARE_LABELS[kind] ?? kind} 连接配置`}>
                <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-semibold">
                    {MIDDLEWARE_LABELS[kind] ?? kind}
                    <span className="ml-2 font-mono text-[10px] text-muted-foreground">{supported ? 'STRUCTURED CONNECTION' : 'ADAPTER BLOCKED'}</span>
                  </span>
                  {!supported && <AlertTriangle className="size-4 text-danger" />}
                </header>
                {!supported ? (
                  <div role="alert" className="rounded-sm bg-danger-soft px-2.5 py-2 text-[11px] text-danger">
                    <strong className="block">连接适配器尚未支持</strong>
                    当前版本只支持 PostgreSQL 与 MinIO。此服务的保存与预检将被阻断。
                  </div>
                ) : (
                  <div className="grid gap-2">
                    <div className="grid gap-2 md:grid-cols-2">
                      <div className="grid gap-1">
                        <Label htmlFor={`${kind}-binding`}>{MIDDLEWARE_LABELS[kind]} 绑定</Label>
                        <select
                          id={`${kind}-binding`}
                          aria-label={`${MIDDLEWARE_LABELS[kind]} 绑定`}
                          value={selectedId}
                          onChange={(event) => onBindingChange(kind, event.target.value)}
                          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
                        >
                          <option value="">未绑定</option>
                          {candidates.map((resource) => (
                            <option key={resource.id} value={resource.id} disabled={!isHealthyResource(resource)}>
                              {resource.name} · {resource.health}
                            </option>
                          ))}
                        </select>
                        {endpoint ? (
                          <span className="text-[11px] font-mono text-ok">{endpoint}</span>
                        ) : (
                          <span className="text-[11px] text-warn">未发布 {expectedPort}/tcp;选择健康实例后解析 endpoint</span>
                        )}
                      </div>
                    </div>
                    {!profile ? (
                      <div className="flex flex-wrap items-center gap-2 rounded-sm bg-danger-soft px-2.5 py-2 text-[11px] text-danger">
                        缺少服务连接 profile
                        <Button variant="ghost" size="sm" onClick={() => onProfileChange(kind, {})}>
                          生成默认配置
                        </Button>
                      </div>
                    ) : profile.kind === 'postgres' ? (
                      <PostgresProfileEditor profile={profile} secrets={secrets} secretsLoading={secretsLoading} onChange={(patch) => onProfileChange('postgres', patch)} />
                    ) : (
                      <MinioProfileEditor profile={profile} secrets={secrets} secretsLoading={secretsLoading} onChange={(patch) => onProfileChange('minio', patch)} />
                    )}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

function PostgresProfileEditor({
  profile,
  secrets,
  secretsLoading,
  onChange,
}: {
  profile: PostgresConnectionProfile;
  secrets: SecretMetadata[];
  secretsLoading: boolean;
  onChange: (patch: Partial<PostgresConnectionProfile>) => void;
}) {
  return (
    <div className="grid gap-2 md:grid-cols-2">
      <div className="grid gap-1">
        <Label htmlFor="pg-env-var">输出变量名</Label>
        <Input id="pg-env-var" aria-label="PostgreSQL 输出变量名" value={profile.env_var} onChange={(event) => onChange({ env_var: event.target.value })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="pg-scheme">Scheme</Label>
        <Input id="pg-scheme" aria-label="PostgreSQL scheme" value={profile.scheme} onChange={(event) => onChange({ scheme: event.target.value })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="pg-username">用户名</Label>
        <Input id="pg-username" aria-label="PostgreSQL 用户名" value={profile.username} onChange={(event) => onChange({ username: event.target.value })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="pg-database">数据库</Label>
        <Input id="pg-database" aria-label="PostgreSQL 数据库" value={profile.database} onChange={(event) => onChange({ database: event.target.value })} />
      </div>
      <div className="grid gap-1 md:col-span-2">
        <Label htmlFor="pg-secret">PostgreSQL 密码 Secret</Label>
        <select
          id="pg-secret"
          aria-label="PostgreSQL 密码 Secret"
          value={profile.secret_ref}
          disabled={secretsLoading}
          onChange={(event) => onChange({ secret_ref: event.target.value })}
          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
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
    </div>
  );
}

function MinioProfileEditor({
  profile,
  secrets,
  secretsLoading,
  onChange,
}: {
  profile: MinioConnectionProfile;
  secrets: SecretMetadata[];
  secretsLoading: boolean;
  onChange: (patch: Partial<MinioConnectionProfile>) => void;
}) {
  return (
    <div className="grid gap-2 md:grid-cols-2">
      <div className="grid gap-1">
        <Label htmlFor="minio-endpoint-env">Endpoint 变量</Label>
        <Input id="minio-endpoint-env" aria-label="MinIO endpoint 变量" value={profile.endpoint_env} onChange={(event) => onChange({ endpoint_env: event.target.value })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="minio-bucket">Bucket</Label>
        <Input id="minio-bucket" aria-label="MinIO bucket" value={profile.bucket} onChange={(event) => onChange({ bucket: event.target.value })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="minio-access-secret">MinIO Access Key Secret</Label>
        <select
          id="minio-access-secret"
          aria-label="MinIO Access Key Secret"
          value={profile.access_key_secret_ref}
          disabled={secretsLoading}
          onChange={(event) => onChange({ access_key_secret_ref: event.target.value })}
          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
        >
          <option value="">{secretsLoading ? '正在读取 Secret…' : '选择 Secret…'}</option>
          {secrets.map((secret) => (
            <option key={secret.id} value={secret.id} disabled={!secret.present}>
              {secret.name} · v{secret.version}
            </option>
          ))}
        </select>
      </div>
      <div className="grid gap-1">
        <Label htmlFor="minio-secret-secret">MinIO Secret Key Secret</Label>
        <select
          id="minio-secret-secret"
          aria-label="MinIO Secret Key Secret"
          value={profile.secret_key_secret_ref}
          disabled={secretsLoading}
          onChange={(event) => onChange({ secret_key_secret_ref: event.target.value })}
          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
        >
          <option value="">{secretsLoading ? '正在读取 Secret…' : '选择 Secret…'}</option>
          {secrets.map((secret) => (
            <option key={secret.id} value={secret.id} disabled={!secret.present}>
              {secret.name} · v{secret.version}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

/* ============ Environments(worktree 并存卡片) ============ */

function EnvironmentsSection({
  workspaceId,
  tokenReady,
  tokenReason,
  revision,
}: {
  workspaceId: string;
  tokenReady: boolean;
  tokenReason: string | null;
  revision: number;
}) {
  const environments = useEnvironments(workspaceId);
  const [deleteTarget, setDeleteTarget] = useState<EnvironmentRecord | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [deployPlan, setDeployPlan] = useState<components['schemas']['WorkspacePlanResponse'] | null>(null);
  const planMutation = useMutation({
    mutationFn: () => api.createWorkspacePlan(workspaceId, { expected_revision: revision }),
    onSuccess: setDeployPlan,
  });
  const records = environments.data?.environments ?? [];
  return (
    <section className="rounded-md border border-border bg-card" data-testid="environments-section">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold">Environments(worktree 并存)</h2>
          <p className="text-[11px] text-muted-foreground">每个 ref 一张卡:独立 worktree、独立 Compose project。创建后 ref 不可改,可删重建。</p>
        </div>
        <Button size="sm" disabled={!tokenReady} title={tokenReason ?? '创建 Environment'} onClick={() => setCreateOpen(true)}>
          <GitBranch />
          创建 Environment
        </Button>
      </header>
      <div className="p-3">
        {environments.isLoading ? (
          <ListSkeleton rows={2} />
        ) : environments.isError ? (
          <ErrorState error={environments.error} onRetry={() => void environments.refetch()} title="无法读取 Environments" />
        ) : records.length === 0 ? (
          <EmptyState icon={GitBranch} title="没有 Environment" detail="为这个工作区创建一个 ref 环境;创建时会生成平台拥有的 git worktree" />
        ) : (
          <ul className="grid gap-2 md:grid-cols-2">
            {records.map((environment) => (
              <li key={environment.id} className="rounded-md border border-border bg-surface-2 p-2.5" data-testid="environment-card">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-2">
                    <GitBranch className="size-4 shrink-0 text-info" />
                    <span className="min-w-0">
                      <span className="block truncate font-mono text-xs font-semibold">{environment.ref}</span>
                      <span className="block truncate text-[11px] text-muted-foreground" title={environment.worktree_path}>
                        {environment.worktree_path}
                      </span>
                    </span>
                  </span>
                  <span className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={!tokenReady || planMutation.isPending}
                      title={tokenReason ?? '生成部署计划并运行(先看再跑)'}
                      onClick={() => planMutation.mutate()}
                    >
                      {planMutation.isPending ? <BusyLabel>预检中</BusyLabel> : <Rocket />}
                      部署
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`删除 Environment ${environment.ref}`}
                      disabled={!tokenReady}
                      title={tokenReason ?? '删除前会预览清理清单'}
                      onClick={() => setDeleteTarget(environment)}
                    >
                      <Trash2 />
                    </Button>
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      {planMutation.isError && (
        <div className="px-3 pb-3">
          <MutationError error={planMutation.error} />
        </div>
      )}
      {deleteTarget && (
        <EnvironmentDeleteDialog
          environment={deleteTarget}
          onClose={() => setDeleteTarget(null)}
        />
      )}
      {createOpen && <EnvironmentCreateDialog workspaceId={workspaceId} onClose={() => setCreateOpen(false)} />}
      {deployPlan && <PlanDialog plan={deployPlan} onClose={() => setDeployPlan(null)} />}
    </section>
  );
}

function EnvironmentCreateDialog({ workspaceId, onClose }: { workspaceId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [ref, setRef] = useState('');
  const createMutation = useMutation({
    mutationFn: (payload: { ref: string }) => api.createEnvironment(workspaceId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workspaces', workspaceId, 'environments'] });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined} data-testid="environment-create-dialog">
        <DialogHeader eyebrow="ENVIRONMENTS" title="创建 Environment" />
        <DialogBody>
          <div className="grid gap-2">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              为选定的 ref 生成平台拥有的 git worktree;创建后 ref 不可改(可删重建)。
            </p>
            <div className="grid gap-1.5">
              <Label htmlFor="environment-ref">目标 ref(branch / tag)</Label>
              <Input id="environment-ref" value={ref} onChange={(event) => setRef(event.target.value)} placeholder="main" />
            </div>
            {createMutation.isError && <MutationError error={createMutation.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button disabled={createMutation.isPending || !ref.trim()} title={!ref.trim() ? '请填写目标 ref' : '创建 Environment'} onClick={() => createMutation.mutate({ ref: ref.trim() })}>
            {createMutation.isPending ? <BusyLabel>创建中</BusyLabel> : <GitBranch />}
            创建 Environment
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EnvironmentDeleteDialog({ environment, onClose }: { environment: EnvironmentRecord; onClose: () => void }) {
  const queryClient = useQueryClient();
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteEnvironment(environment.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workspaces', environment.workspace_id, 'environments'] });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined} data-testid="environment-delete-dialog">
        <DialogHeader eyebrow="DESTRUCTIVE ACTION" title={`删除 Environment ${environment.ref}`} />
        <DialogBody>
          <div className="grid gap-2 text-xs">
            <p className="leading-relaxed text-muted-foreground">
              删除会执行 git worktree remove 并清理该 Environment 的 Compose 资源。预览清理清单:
            </p>
            <ul className="grid gap-1 rounded-sm bg-surface-2 p-2 font-mono text-[11px] text-muted-foreground" data-testid="environment-delete-preview">
              <li>worktree:{environment.worktree_path}</li>
              <li>ref:{environment.ref}(不可恢复,可重建)</li>
            </ul>
            {deleteMutation.isError && <MutationError error={deleteMutation.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button variant="destructive" disabled={deleteMutation.isPending} title={deleteMutation.isPending ? '正在删除' : '确认删除 Environment'} onClick={() => deleteMutation.mutate()}>
            {deleteMutation.isPending ? <BusyLabel>删除中</BusyLabel> : <Trash2 />}
            删除 Environment
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
