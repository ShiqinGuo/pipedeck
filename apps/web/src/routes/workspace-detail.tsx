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
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

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
import { ApplicationEntryEditor } from '@/components/application-entry-editor';
import { PlanDialog } from '@/components/plan-dialog';
import { WorkspaceRuntime } from '@/components/workspace-runtime';
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
type WorkspaceRecord = components['schemas']['WorkspaceRecord'];
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
  const { t } = useTranslation();
  const { id } = useParams({ from: '/workspaces/$id' });
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const token = useApiToken();
  const workspace = useWorkspace(id);
  const catalog = useCatalog();
  const runtime = useRuntime();
  const secrets = useSecrets();

  const [draft, setDraft] = useState<WorkspaceInput | null>(null);
  const editorBase = useRef<WorkspaceRecord | null>(null);
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
      const base = editorBase.current;
      if (base?.id === record.id && base.revision === record.revision) return;
      if (base?.id === record.id && draft && JSON.stringify(draft) !== JSON.stringify(cloneWorkspaceInput(base))) return;
      editorBase.current = record;
      setDraft(cloneWorkspaceInput(record));
      setSelectedServiceIndex(0);
      setPlan(null);
    }
  }, [record, draft]);

  const dirty = Boolean(draft && editorBase.current && JSON.stringify(draft) !== JSON.stringify(cloneWorkspaceInput(editorBase.current)));
  const draftConflict = Boolean(record && editorBase.current?.id === id && editorBase.current.revision !== record.revision);
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
    onMutate: () => draft,
    onSuccess: async (saved, _payload, submittedDraft) => {
      queryClient.setQueryData<WorkspaceRecord>(['workspaces', saved.id], (current) =>
        current && current.revision > saved.revision ? current : saved,
      );
      if (editorBase.current?.id === saved.id && editorBase.current.revision <= saved.revision) {
        editorBase.current = saved;
        setDraft((current) => JSON.stringify(current) === JSON.stringify(submittedDraft) ? cloneWorkspaceInput(saved) : current);
        setPlan(null);
      }
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
    if (!saveMutation.isPending) saveMutation.reset();
    if (!planMutation.isPending) planMutation.reset();
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

  const secretStateReason = secrets.isLoading ? t('workspaceDetail.secretState.loading') : secrets.isError ? t('workspaceDetail.secretState.error') : null;
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
  const requiresDocker = requiredKinds.length > 0 || Boolean(draft?.services.some((service) => service.execution_target.kind === 'compose'));

  const saveDisabledReason =
    token.disabledReason ??
    (draftConflict ? t('integration.draftConflict') : null) ??
    (!dirty ? t('workspaceDetail.save.reasonNoChanges') : !draft?.name.trim() ? t('workspaceDetail.save.reasonNameEmpty') : !draft.services.length ? t('workspaceDetail.save.reasonNoService') : (commandError ?? targetError ?? environmentError ?? connectionError));
  const planDisabledReason =
    token.disabledReason ??
    (dirty
      ? t('workspaceDetail.plan.reasonDirty')
      : (commandError ?? targetError ?? environmentError ?? connectionError) ??
        (!record ? t('workspaceDetail.plan.reasonNotLoaded') : requiresDocker && runtime.isLoading ? t('workspaceDetail.plan.reasonDockerLoading') : requiresDocker && runtime.isError ? t('workspaceDetail.plan.reasonDockerError') : requiresDocker && dockerUnavailable ? (runtime.data?.recovery ?? t('workspaceDetail.plan.reasonDockerUnavailable')) : missingDependency ? t('workspaceDetail.plan.reasonMissingDependency', { middleware: t(MIDDLEWARE_LABELS[missingDependency] ?? missingDependency) }) : planMutation.isPending ? t('workspaceDetail.plan.reasonRunning') : null));
  const deleteDisabledReason = token.disabledReason ?? (deleteMutation.isPending ? t('workspaceDetail.delete.reasonDeleting') : null);

  if (workspace.isLoading) {
    return (
      <PageScroll>
        <PageHeader eyebrow="WORKSPACE" title={t('workspaceDetail.page.title')} compact />
        <PageBody>
          <ListSkeleton rows={4} />
        </PageBody>
      </PageScroll>
    );
  }
  if (workspace.isError || !record) {
    return (
      <PageScroll>
        <PageHeader eyebrow="WORKSPACE" title={t('workspaceDetail.page.title')} compact />
        <PageBody>
          <ErrorState error={workspace.error} onRetry={() => void workspace.refetch()} title={t('workspaceDetail.page.errorTitle')} />
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
            {t('workspaceDetail.page.serviceSummary', { count: record.services.length, updatedAt: formatDate(record.updated_at) })} ·{' '}
            <Badge variant={dirty ? 'warn' : 'outline'}>{dirty ? t('workspaceDetail.page.unsaved') : `rev ${record.revision}`}</Badge>
          </>
        }
        actions={
          <>
            <Button asChild variant="ghost" size="sm">
              <Link to="/workspaces">
                <ArrowLeft />
                {t('workspaceDetail.page.backToList')}
              </Link>
            </Button>
            <div className="flex items-center overflow-hidden rounded-sm border border-[#46524a]" aria-label={t('workspaceDetail.page.modeAria')}>
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
                  {mode === 'development' ? t('workspaceDetail.page.modeDev') : t('workspaceDetail.page.modeIntegrated')}
                </button>
              ))}
            </div>
            <Button
              variant="secondary"
              size="sm"
              disabled={Boolean(saveDisabledReason) || saveMutation.isPending}
              title={saveDisabledReason ?? t('workspaceDetail.save.title')}
              onClick={() =>
                draft &&
                saveMutation.mutate({
                  ...draft,
                  name: draft.name.trim() || draft.name,
                  expected_revision: record.revision,
                })
              }
            >
              {saveMutation.isPending ? <BusyLabel>{t('workspaceDetail.save.busy')}</BusyLabel> : <Save />}
              {t('workspaceDetail.save.action')}
            </Button>
            <Button
              size="sm"
              disabled={Boolean(planDisabledReason)}
              title={planDisabledReason ?? t('workspaceDetail.plan.title')}
              onClick={() => planMutation.mutate({ expected_revision: record.revision })}
            >
              {planMutation.isPending ? <BusyLabel>{t('workspaceDetail.plan.busy')}</BusyLabel> : <ListChecks />}
              {t('workspaceDetail.plan.action')}
            </Button>
            <Button variant="secondary" size="sm" title={token.disabledReason ?? t('workspaceDetail.delete.title')} disabled={!token.configured} onClick={() => setDeleteOpen(true)}>
              <Trash2 />
              {t('workspaceDetail.delete.action')}
            </Button>
          </>
        }
      />
      <PageBody>
        <WorkspaceRuntime workspaceId={id} revision={record.revision} />
        {draftConflict && <div role="alert" className="grid gap-2 rounded-md border border-warn p-3 text-xs text-warn">
          <p>{t('integration.draftConflict')}</p>
          <Button variant="secondary" size="sm" onClick={() => {
            editorBase.current = record;
            setDraft(cloneWorkspaceInput(record));
            setPlan(null);
          }}>{t('integration.reload')}</Button>
        </div>}
        <div className="grid gap-1.5">
          <Label htmlFor="workspace-name-input">{t('workspaceDetail.nameLabel')}</Label>
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
        {requiresDocker && dockerUnavailable && (
          <ErrorState
            error={new Error(runtime.data?.error_code ?? t('workspaceDetail.dockerUnavailable'))}
            title={t('workspaceDetail.dockerUnavailable')}
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
                  {t('workspaceDetail.services.title')}
                </span>
                <select
                  aria-label={t('workspaceDetail.services.addAria')}
                  value=""
                  onChange={(event) => {
                    const projectId = event.target.value;
                    const project = projectMap.get(projectId);
                    if (!project || draft.services.some((service) => service.project_id === projectId)) return;
                    const nextService: WorkspaceService = {
                      project_id: project.id,
                      depends_on: [],
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
                  <option value="">{t('workspaceDetail.services.addProject')}</option>
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
                            {t('workspaceDetail.services.commandCount', { count: service.commands.length })} · {service.execution_target.kind === 'compose' ? 'Compose' : 'Host'}
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
                  <EmptyState icon={Settings2} title={t('workspaceDetail.inspector.emptyTitle')} detail={t('workspaceDetail.inspector.emptyDetail')} />
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
                      aria-label={t('workspaceDetail.inspector.removeAria', { name: selectedProject.name })}
                      disabled={draft.services.length === 1}
                      title={draft.services.length === 1 ? t('workspaceDetail.inspector.removeDisabled') : t('workspaceDetail.inspector.removeTitle')}
                      onClick={() => {
                        setDraft((current) =>
                          current ? { ...current, services: current.services
                            .filter((_, serviceIndex) => serviceIndex !== selectedServiceIndex)
                            .map((service) => ({ ...service, depends_on: (service.depends_on ?? []).filter((projectId) => projectId !== selectedService.project_id) })) } : current,
                        );
                        setSelectedServiceIndex((current) => Math.max(0, current - 1));
                        resetDraftFeedback();
                      }}
                    >
                      <X />
                      {t('workspaceDetail.inspector.remove')}
                    </Button>
                  </div>
                  <TabsList className="m-2" aria-label={t('workspaceDetail.inspector.tabsAria')}>
                    <TabsTrigger value="commands">
                      <TerminalSquare className="size-3.5" />
                      {t('workspaceDetail.tabs.commands')}
                    </TabsTrigger>
                    <TabsTrigger value="environment">
                      <KeyRound className="size-3.5" />
                      {t('workspaceDetail.tabs.environment')}
                    </TabsTrigger>
                    <TabsTrigger value="target">
                      <Container className="size-3.5" />
                      {t('workspaceDetail.tabs.target')}
                    </TabsTrigger>
                    <TabsTrigger value="dependencies">
                      <Database className="size-3.5" />
                      {t('workspaceDetail.tabs.dependencies')}
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
                      <fieldset className="mb-4 grid gap-2 rounded-sm border border-border p-3">
                        <legend className="px-1 text-xs font-semibold">{t('integration.dependencies')}</legend>
                        <p className="text-xs text-muted-foreground">{t('integration.dependenciesHint')}</p>
                        {draft.services.filter((item) => item.project_id !== selectedService.project_id).map((dependency) => (
                          <label key={dependency.project_id} className="flex items-center gap-2 text-xs">
                            <Checkbox aria-label={`${t('integration.dependencies')}: ${projectMap.get(dependency.project_id)?.name ?? dependency.project_id}`}
                              checked={(selectedService.depends_on ?? []).includes(dependency.project_id)}
                              onCheckedChange={(checked) => updateService(selectedServiceIndex, (service) => ({ ...service,
                                depends_on: checked ? [...(service.depends_on ?? []), dependency.project_id] : (service.depends_on ?? []).filter((value) => value !== dependency.project_id),
                              }))} />
                            {projectMap.get(dependency.project_id)?.name ?? dependency.project_id}
                          </label>
                        ))}
                      </fieldset>
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
          tokenReady={token.configured && !dirty && !draftConflict}
          tokenReason={token.disabledReason ?? (dirty ? t('workspaceDetail.plan.reasonDirty') : null)}
          revision={record?.revision ?? 1}
        />

        <CliFooter command={cli.workspaces()} hint={t('workspaceDetail.cliHint')} />
      </PageBody>

      {plan && <PlanDialog plan={plan} onClose={() => setPlan(null)} />}
      {deleteOpen && record && (
        <Dialog open onOpenChange={(open) => !open && setDeleteOpen(false)}>
          <DialogContent aria-describedby={undefined} data-testid="delete-workspace-dialog">
            <DialogHeader eyebrow="DESTRUCTIVE ACTION" title={t('workspaceDetail.deleteDialog.title')} />
            <DialogBody>
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t('workspaceDetail.deleteDialog.body')}
              </p>
              {deleteMutation.isError && <MutationError error={deleteMutation.error} />}
            </DialogBody>
            <DialogFooter>
              <Button variant="secondary" onClick={() => setDeleteOpen(false)}>
                {t('workspaceDetail.common.cancel')}
              </Button>
              <Button variant="destructive" disabled={Boolean(deleteDisabledReason)} title={deleteDisabledReason ?? t('workspaceDetail.deleteDialog.submitTitle')} onClick={() => deleteMutation.mutate()}>
                {deleteMutation.isPending ? <BusyLabel>{t('workspaceDetail.common.deleting')}</BusyLabel> : <Trash2 />}
                {t('workspaceDetail.deleteDialog.submit')}
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
  const { t } = useTranslation();
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
        <h3 className="text-xs font-semibold">{t('workspaceDetail.commands.title')}</h3>
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            onChange([...service.commands, { id: `command-${service.commands.length + 1}`, label: t('workspaceDetail.commands.defaultLabel'), kind: 'quality', argv: [''], long_running: false }])
          }
        >
          <Plus />
          {t('workspaceDetail.commands.add')}
        </Button>
      </div>
      {service.commands.map((command, index) => (
        <section key={`${command.id}-${index}`} className="rounded-md border border-border p-2.5" aria-label={t('workspaceDetail.commands.argvTokenAria', { label: command.label })}>
          <div className="flex flex-wrap items-center gap-2">
            <Input aria-label={t('workspaceDetail.commands.nameAria', { index: index + 1 })} value={command.label} onChange={(event) => update(index, { label: event.target.value })} className="w-32" />
            <select
              aria-label={t('workspaceDetail.commands.kindAria', { index: index + 1 })}
              value={command.kind}
              onChange={(event) => update(index, { kind: event.target.value as ServiceCommand['kind'] })}
              className="h-9 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
            >
              <option value="inspect">{t('workspaceDetail.commands.kind.inspect')}</option>
              <option value="dependencies">{t('workspaceDetail.commands.kind.dependencies')}</option>
              <option value="quality">{t('workspaceDetail.commands.kind.quality')}</option>
              <option value="build">{t('workspaceDetail.commands.kind.build')}</option>
              <option value="deploy">{t('workspaceDetail.commands.kind.deploy')}</option>
              <option value="start">{t('workspaceDetail.commands.kind.start')}</option>
            </select>
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Checkbox checked={command.long_running} onCheckedChange={(value) => update(index, { long_running: Boolean(value) })} aria-label={t('workspaceDetail.commands.longRunningAria', { label: command.label })} />
              {t('workspaceDetail.commands.longRunning')}
            </label>
            <Button variant="ghost" size="icon-sm" aria-label={t('workspaceDetail.commands.deleteAria', { label: command.label })} onClick={() => onChange(service.commands.filter((_, commandIndex) => commandIndex !== index))}>
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
                {t('workspaceDetail.commands.addToken')}
              </Button>
            </div>
            {command.argv.map((token, tokenIndex) => (
              <div key={tokenIndex} className="flex items-center gap-1.5">
                <span className="font-mono text-[10px] text-[#5f6a64]">{String(tokenIndex).padStart(2, '0')}</span>
                <Input
                  aria-label={t('workspaceDetail.commands.tokenAria', { label: command.label, index: tokenIndex + 1 })}
                  value={token}
                  onChange={(event) => updateToken(index, tokenIndex, event.target.value)}
                  placeholder={tokenIndex === 0 ? t('workspaceDetail.commands.execPlaceholder') : t('workspaceDetail.commands.argPlaceholder')}
                  className="flex-1"
                />
                <Button variant="ghost" size="icon-sm" aria-label={t('workspaceDetail.commands.deleteTokenAria', { label: command.label, index: tokenIndex + 1 })} onClick={() => removeToken(index, tokenIndex)}>
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
  const { t } = useTranslation();
  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold">{t('workspaceDetail.environment.title')}</h3>
        <Button variant="ghost" size="sm" onClick={onAdd}>
          <Plus />
          {t('workspaceDetail.environment.add')}
        </Button>
      </div>
      {bindings.length === 0 ? (
        <EmptyState icon={KeyRound} title={t('workspaceDetail.environment.emptyTitle')} detail={t('workspaceDetail.environment.emptyDetail')} />
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
                <Input aria-label={t('workspaceDetail.environment.nameAria', { index: index + 1 })} value={binding.name} onChange={(event) => onChange(index, { name: event.target.value })} placeholder="DATABASE_URL" />
                <select
                  aria-label={t('workspaceDetail.environment.sourceAria', { name: binding.name || t('workspaceDetail.environment.variable', { index: index + 1 }) })}
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
                    aria-label={t('workspaceDetail.environment.secretAria', { name: binding.name || t('workspaceDetail.environment.variable', { index: index + 1 }) })}
                    value={binding.reference ?? ''}
                    disabled={secretsLoading}
                    onChange={(event) => onChange(index, { reference: event.target.value })}
                    className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
                  >
                    <option value="">{secretsLoading ? t('workspaceDetail.secret.loading') : t('workspaceDetail.secret.select')}</option>
                    {secrets.map((secret) => (
                      <option key={secret.id} value={secret.id} disabled={!secret.present}>
                        {secret.name} · v{secret.version}
                        {secret.present ? '' : ` · ${t('workspaceDetail.secret.valueMissing')}`}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Input
                    aria-label={t('workspaceDetail.environment.valueAria', { name: binding.name || t('workspaceDetail.environment.variable', { index: index + 1 }), kind: binding.source === 'literal' ? t('workspaceDetail.environment.value') : t('workspaceDetail.environment.reference') })}
                    value={binding.source === 'literal' ? (binding.value ?? '') : (binding.reference ?? '')}
                    onChange={(event) => onChange(index, binding.source === 'literal' ? { value: event.target.value } : { reference: event.target.value })}
                    placeholder={binding.source === 'literal' ? t('workspaceDetail.environment.valuePlaceholder') : 'HOST_ENV_NAME'}
                  />
                )}
                <Button variant="ghost" size="icon-sm" aria-label={t('workspaceDetail.environment.deleteAria', { name: binding.name || t('workspaceDetail.environment.variable', { index: index + 1 }) })} onClick={() => onRemove(index)}>
                  <Trash2 />
                </Button>
                {(invalidSensitive || invalidReference) && (
                  <small className="text-[11px] text-danger md:col-span-4">
                    {invalidSensitive ? t('workspaceDetail.environment.errorSensitive') : binding.source === 'host-env' ? t('workspaceDetail.environment.errorReference') : t('workspaceDetail.environment.errorSecret')}
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
  const { t } = useTranslation();
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
        <h3 className="text-xs font-semibold">{t('workspaceDetail.target.title')}</h3>
        <div className="flex items-center gap-1 overflow-hidden rounded-sm border border-[#46524a]" aria-label={t('workspaceDetail.target.kindAria')}>
          <button type="button" aria-pressed={target.kind === 'host'} className={cn('flex h-8 items-center gap-1.5 px-2.5 text-xs', target.kind === 'host' ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')} onClick={() => switchTarget('host')}>
            <TerminalSquare className="size-3.5" />
            {t('workspaceDetail.target.host')}
          </button>
          <button type="button" aria-pressed={target.kind === 'compose'} className={cn('flex h-8 items-center gap-1.5 px-2.5 text-xs', target.kind === 'compose' ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')} onClick={() => switchTarget('compose')}>
            <Container className="size-3.5" />
            {t('workspaceDetail.target.compose')}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2 rounded-sm bg-surface-2 px-2.5 py-2 text-[11px] text-muted-foreground">
        <FileCog className="size-4 shrink-0" />
        <span>
          {t('workspaceDetail.target.detectionPrefix')}{project.container_capabilities.compose_files.length > 0 ? `Compose: ${project.container_capabilities.compose_files.join(', ')}` : t('workspaceDetail.target.noComposeFile')} ·{' '}
          {project.container_capabilities.dockerfile ? `Dockerfile: ${project.container_capabilities.dockerfile}` : t('workspaceDetail.target.noDockerfile')}
        </span>
      </div>
      {target.kind === 'host' ? <HostTargetEditor target={target} onChange={onChange} /> : <ComposeTargetEditor target={target} onChange={onChange} />}
      <ApplicationEntryEditor target={target} onChange={onChange} />
    </div>
  );
}

function HostTargetEditor({ target, onChange }: { target: HostTarget; onChange: (target: ExecutionTarget) => void }) {
  const { t } = useTranslation();
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
            <p className="text-[11px] text-muted-foreground">{t('workspaceDetail.hostTarget.description')}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={() => onChange({ ...target, endpoints: [...target.endpoints, { name: `port-${target.endpoints.length + 1}`, protocol: 'tcp', host_port: 3000, injection: { kind: 'command-owned' } }] })}>
            <Plus />
            {t('workspaceDetail.hostTarget.addEndpoint')}
          </Button>
        </header>
        {target.endpoints.length === 0 ? (
          <EmptyState icon={Network} title={t('workspaceDetail.hostTarget.emptyTitle')} detail={t('workspaceDetail.hostTarget.emptyDetail')} />
        ) : (
          <div className="grid gap-1.5">
            {target.endpoints.map((endpoint, index) => (
              <div key={index} className="grid items-center gap-1.5 rounded-sm bg-surface-2 p-2 md:grid-cols-[minmax(0,1fr)_80px_100px_150px_minmax(0,1fr)_auto]">
                <Input aria-label={t('workspaceDetail.hostTarget.nameAria', { name: endpoint.name })} value={endpoint.name} onChange={(event) => updateEndpoint(index, { name: event.target.value })} />
                <select aria-label={t('workspaceDetail.hostTarget.protocolAria', { name: endpoint.name })} value={endpoint.protocol} onChange={(event) => updateEndpoint(index, { protocol: event.target.value as 'tcp' | 'udp' })} className="h-9 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs">
                  <option value="tcp">TCP</option>
                  <option value="udp">UDP</option>
                </select>
                <Input type="number" min={1} max={65535} aria-label={t('workspaceDetail.hostTarget.hostPortAria', { name: endpoint.name })} value={endpoint.host_port} onChange={(event) => updateEndpoint(index, { host_port: Number(event.target.value) })} />
                <select
                  aria-label={t('workspaceDetail.hostTarget.injectionAria', { name: endpoint.name })}
                  value={endpoint.injection.kind}
                  onChange={(event) => {
                    const kind = event.target.value as 'command-owned' | 'environment' | 'argument';
                    updateEndpoint(index, {
                      injection: kind === 'environment' ? { kind, name: 'PORT' } : kind === 'argument' ? { kind, option: '--port' } : { kind: 'command-owned' },
                    });
                  }}
                  className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
                >
                  <option value="command-owned">{t('workspaceDetail.hostTarget.injection.commandOwned')}</option>
                  <option value="environment">{t('workspaceDetail.hostTarget.injection.environment')}</option>
                  <option value="argument">{t('workspaceDetail.hostTarget.injection.argument')}</option>
                </select>
                {endpoint.injection.kind === 'environment' && (
                  <Input aria-label={t('workspaceDetail.hostTarget.envVarAria', { name: endpoint.name })} value={endpoint.injection.name} onChange={(event) => updateEndpoint(index, { injection: { kind: 'environment', name: event.target.value } })} />
                )}
                {endpoint.injection.kind === 'argument' && (
                  <Input aria-label={t('workspaceDetail.hostTarget.argumentAria', { name: endpoint.name })} value={endpoint.injection.option} onChange={(event) => updateEndpoint(index, { injection: { kind: 'argument', option: event.target.value } })} />
                )}
                {endpoint.injection.kind === 'command-owned' && <span className="text-[11px] text-muted-foreground">{t('workspaceDetail.hostTarget.commandOwned')}</span>}
                <Button variant="ghost" size="icon-sm" aria-label={t('workspaceDetail.hostTarget.deleteAria', { name: endpoint.name })} onClick={() => onChange({ ...target, endpoints: target.endpoints.filter((_, endpointIndex) => endpointIndex !== index) })}>
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
  const { t } = useTranslation();
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
            <p className="text-[11px] text-muted-foreground">{t('workspaceDetail.composeTarget.pathsHint')}</p>
          </div>
          <div className="flex items-center gap-1 overflow-hidden rounded-sm border border-[#46524a]">
            <button type="button" aria-pressed={source.kind === 'existing-compose'} className={cn('flex h-8 items-center px-2.5 text-xs', source.kind === 'existing-compose' ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')} onClick={() => switchSource('existing-compose')}>
              {t('workspaceDetail.composeTarget.existingCompose')}
            </button>
            <button type="button" aria-pressed={source.kind === 'dockerfile'} className={cn('flex h-8 items-center px-2.5 text-xs', source.kind === 'dockerfile' ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')} onClick={() => switchSource('dockerfile')}>
              Dockerfile
            </button>
          </div>
        </header>
        {source.kind === 'existing-compose' ? (
          <div className="grid gap-2">
            <StringListEditor label={t('workspaceDetail.composeTarget.composeFiles')} values={source.compose_files} placeholder="compose.yml" required onChange={(compose_files) => onChange({ ...target, source: { ...source, compose_files } })} />
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
              <Input id="compose-dockerfile" aria-label={t('workspaceDetail.composeTarget.dockerfileAria')} value={source.dockerfile} onChange={(event) => onChange({ ...target, source: { ...source, dockerfile: event.target.value } })} />
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
            <p className="text-[11px] text-muted-foreground">{t('workspaceDetail.composeTarget.endpointHint')}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={() => onChange({ ...target, endpoints: [...target.endpoints, { name: `port-${target.endpoints.length + 1}`, protocol: 'tcp', host_port: 3000, container_port: 3000 }] })}>
            <Plus />
            {t('workspaceDetail.composeTarget.addEndpoint')}
          </Button>
        </header>
        <div className="grid gap-1.5">
          {target.endpoints.map((endpoint, index) => (
            <div key={index} className="grid items-center gap-1.5 rounded-sm bg-surface-2 p-2 md:grid-cols-[minmax(0,1fr)_80px_100px_100px_auto]">
              <Input aria-label={t('workspaceDetail.composeTarget.nameAria', { name: endpoint.name })} value={endpoint.name} onChange={(event) => updateEndpoint(index, { name: event.target.value })} />
              <select aria-label={t('workspaceDetail.composeTarget.protocolAria', { name: endpoint.name })} value={endpoint.protocol} onChange={(event) => updateEndpoint(index, { protocol: event.target.value as 'tcp' | 'udp' })} className="h-9 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs">
                <option value="tcp">TCP</option>
                <option value="udp">UDP</option>
              </select>
              <Input type="number" min={1} max={65535} aria-label={t('workspaceDetail.composeTarget.hostPortAria', { name: endpoint.name })} value={endpoint.host_port} onChange={(event) => updateEndpoint(index, { host_port: Number(event.target.value) })} />
              <Input type="number" min={1} max={65535} aria-label={t('workspaceDetail.composeTarget.containerPortAria', { name: endpoint.name })} value={endpoint.container_port} onChange={(event) => updateEndpoint(index, { container_port: Number(event.target.value) })} />
              <Button variant="ghost" size="icon-sm" aria-label={t('workspaceDetail.composeTarget.deleteAria', { name: endpoint.name })} onClick={() => onChange({ ...target, endpoints: target.endpoints.filter((_, endpointIndex) => endpointIndex !== index) })}>
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
  const { t } = useTranslation();
  const firstEndpoint = endpoints[0] ?? '';
  return (
    <section className="rounded-md border border-border p-2.5">
      <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold">Readiness</p>
          <p className="text-[11px] text-muted-foreground">{t('workspaceDetail.readiness.description')}</p>
        </div>
        <div className="flex items-center gap-1 overflow-hidden rounded-sm border border-[#46524a]">
          {allowNone && (
            <button type="button" aria-pressed={!value} className={cn('flex h-8 items-center px-2.5 text-xs', !value ? 'bg-surface-3 text-foreground' : 'text-muted-foreground')} onClick={() => onChange(null)}>
              {t('workspaceDetail.readiness.none')}
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
  const { t } = useTranslation();
  return (
    <section className="grid gap-1">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-muted-foreground">
          {label}
          {required && <b className="ml-1 text-warn">REQUIRED</b>}
        </span>
        <Button variant="ghost" size="sm" aria-label={t('workspaceDetail.stringList.addAria', { label })} onClick={() => onChange([...values, ''])}>
          <Plus />
        </Button>
      </div>
      {values.map((value, index) => (
        <div key={index} className="flex items-center gap-1.5">
          <Input aria-label={t('workspaceDetail.stringList.itemAria', { label, index: index + 1 })} value={value} placeholder={placeholder} onChange={(event) => onChange(values.map((item, itemIndex) => (itemIndex === index ? event.target.value : item)))} className="flex-1" />
          <Button variant="ghost" size="icon-sm" aria-label={t('workspaceDetail.stringList.deleteAria', { label, index: index + 1 })} onClick={() => onChange(values.filter((_, itemIndex) => itemIndex !== index))}>
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
  const { t } = useTranslation();
  const endpointLabel = (resource: RuntimeResource | undefined, containerPort: number) => {
    const endpoint = resource?.endpoints.find((candidate) => candidate.protocol === 'tcp' && candidate.container_port === containerPort);
    return endpoint ? `${endpoint.host}:${endpoint.host_port} → ${endpoint.container_port}/tcp` : null;
  };
  return (
    <div className="grid gap-3">
      <div>
        <h3 className="text-xs font-semibold">{t('workspaceDetail.dependency.title')}</h3>
        <p className="text-[11px] text-muted-foreground">{t('workspaceDetail.dependency.description')}</p>
      </div>
      {project.requirements.length === 0 ? (
        <EmptyState icon={Database} title={t('workspaceDetail.dependency.emptyTitle')} detail={t('workspaceDetail.dependency.emptyDetail')} />
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
              <section key={kind} className={cn('rounded-md border p-2.5', supported ? 'border-border' : 'border-[#754246]')} aria-label={t('workspaceDetail.dependency.sectionAria', { middleware: t(MIDDLEWARE_LABELS[kind] ?? kind) })}>
                <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-semibold">
                    {t(MIDDLEWARE_LABELS[kind] ?? kind)}
                    <span className="ml-2 font-mono text-[10px] text-muted-foreground">{supported ? 'STRUCTURED CONNECTION' : 'ADAPTER BLOCKED'}</span>
                  </span>
                  {!supported && <AlertTriangle className="size-4 text-danger" />}
                </header>
                {!supported ? (
                  <div role="alert" className="rounded-sm bg-danger-soft px-2.5 py-2 text-[11px] text-danger">
                    <strong className="block">{t('workspaceDetail.dependency.adapterUnsupported')}</strong>
                    {t('workspaceDetail.dependency.adapterDetail')}
                  </div>
                ) : (
                  <div className="grid gap-2">
                    <div className="grid gap-2 md:grid-cols-2">
                      <div className="grid gap-1">
                        <Label htmlFor={`${kind}-binding`}>{t('workspaceDetail.dependency.bindingLabel', { middleware: t(MIDDLEWARE_LABELS[kind] ?? kind) })}</Label>
                        <select
                          id={`${kind}-binding`}
                          aria-label={t('workspaceDetail.dependency.bindingAria', { middleware: t(MIDDLEWARE_LABELS[kind] ?? kind) })}
                          value={selectedId}
                          onChange={(event) => onBindingChange(kind, event.target.value)}
                          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
                        >
                          <option value="">{t('workspaceDetail.dependency.unbound')}</option>
                          {candidates.map((resource) => (
                            <option key={resource.id} value={resource.id} disabled={!isHealthyResource(resource)}>
                              {resource.name} · {resource.health}
                            </option>
                          ))}
                        </select>
                        {endpoint ? (
                          <span className="text-[11px] font-mono text-ok">{endpoint}</span>
                        ) : (
                          <span className="text-[11px] text-warn">{t('workspaceDetail.dependency.endpointPending', { port: expectedPort })}</span>
                        )}
                      </div>
                    </div>
                    {!profile ? (
                      <div className="flex flex-wrap items-center gap-2 rounded-sm bg-danger-soft px-2.5 py-2 text-[11px] text-danger">
                        {t('workspaceDetail.dependency.missingProfile')}
                        <Button variant="ghost" size="sm" onClick={() => onProfileChange(kind, {})}>
                          {t('workspaceDetail.dependency.defaultProfile')}
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
  const { t } = useTranslation();
  return (
    <div className="grid gap-2 md:grid-cols-2">
      <div className="grid gap-1">
        <Label htmlFor="pg-env-var">{t('workspaceDetail.postgres.envVar')}</Label>
        <Input id="pg-env-var" aria-label={t('workspaceDetail.postgres.envVarAria')} value={profile.env_var} onChange={(event) => onChange({ env_var: event.target.value })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="pg-scheme">Scheme</Label>
        <Input id="pg-scheme" aria-label="PostgreSQL scheme" value={profile.scheme} onChange={(event) => onChange({ scheme: event.target.value })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="pg-username">{t('workspaceDetail.postgres.username')}</Label>
        <Input id="pg-username" aria-label={t('workspaceDetail.postgres.usernameAria')} value={profile.username} onChange={(event) => onChange({ username: event.target.value })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="pg-database">{t('workspaceDetail.postgres.database')}</Label>
        <Input id="pg-database" aria-label={t('workspaceDetail.postgres.databaseAria')} value={profile.database} onChange={(event) => onChange({ database: event.target.value })} />
      </div>
      <div className="grid gap-1 md:col-span-2">
        <Label htmlFor="pg-secret">{t('workspaceDetail.postgres.secretLabel')}</Label>
        <select
          id="pg-secret"
          aria-label={t('workspaceDetail.postgres.secretLabel')}
          value={profile.secret_ref}
          disabled={secretsLoading}
          onChange={(event) => onChange({ secret_ref: event.target.value })}
          className="h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2 text-xs"
        >
          <option value="">{secretsLoading ? t('workspaceDetail.secret.loading') : t('workspaceDetail.secret.select')}</option>
          {secrets.map((secret) => (
            <option key={secret.id} value={secret.id} disabled={!secret.present}>
              {secret.name} · v{secret.version}
              {secret.present ? '' : ` · ${t('workspaceDetail.secret.valueMissing')}`}
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
  const { t } = useTranslation();
  return (
    <div className="grid gap-2 md:grid-cols-2">
      <div className="grid gap-1">
        <Label htmlFor="minio-endpoint-env">{t('workspaceDetail.minio.endpointEnv')}</Label>
        <Input id="minio-endpoint-env" aria-label={t('workspaceDetail.minio.endpointEnvAria')} value={profile.endpoint_env} onChange={(event) => onChange({ endpoint_env: event.target.value })} />
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
          <option value="">{secretsLoading ? t('workspaceDetail.secret.loading') : t('workspaceDetail.secret.select')}</option>
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
          <option value="">{secretsLoading ? t('workspaceDetail.secret.loading') : t('workspaceDetail.secret.select')}</option>
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
  const { t } = useTranslation();
  const environments = useEnvironments(workspaceId);
  const workspace = useWorkspace(workspaceId);
  const catalog = useCatalog();
  const queryClient = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<EnvironmentRecord | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [deployPlan, setDeployPlan] = useState<components['schemas']['WorkspacePlanResponse'] | null>(null);
  const planMutation = useMutation({
    mutationFn: async (environment: EnvironmentRecord) => {
      const updated = await api.applyEnvironment(workspaceId, environment.id, revision);
      queryClient.setQueryData(['workspaces', workspaceId], updated);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['catalog'] }),
        queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
      ]);
      return api.createWorkspacePlan(workspaceId, { expected_revision: updated.revision });
    },
    onSuccess: setDeployPlan,
  });
  const records = environments.data?.environments ?? [];
  return (
    <section className="rounded-md border border-border bg-card" data-testid="environments-section">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold">{t('workspaceDetail.environments.title')}</h2>
          <p className="text-[11px] text-muted-foreground">{t('workspaceDetail.environments.description')}</p>
        </div>
        <Button size="sm" disabled={!tokenReady} title={tokenReason ?? t('workspaceDetail.environments.createTitle')} onClick={() => setCreateOpen(true)}>
          <GitBranch />
          {t('workspaceDetail.environments.create')}
        </Button>
      </header>
      <div className="p-3">
        {environments.isLoading ? (
          <ListSkeleton rows={2} />
        ) : environments.isError ? (
          <ErrorState error={environments.error} onRetry={() => void environments.refetch()} title={t('workspaceDetail.environments.errorTitle')} />
        ) : records.length === 0 ? (
          <EmptyState icon={GitBranch} title={t('workspaceDetail.environments.emptyTitle')} detail={t('workspaceDetail.environments.emptyDetail')} />
        ) : (
          <ul className="grid gap-2 md:grid-cols-2">
            {records.map((environment) => {
              const sourceId = environment.source_repository_id;
              const sourceName = sourceId
                ? catalog.data?.projects.find((project) => project.id === sourceId)?.name ?? sourceId
                : t('workspaceDetail.environments.sourceUnknown');
              const applied = workspace.data?.services.some((service) => service.project_id === environment.repository_id);
              return (
              <li key={environment.id} className="rounded-md border border-border bg-surface-2 p-2.5" data-testid="environment-card">
                <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                  <span className="flex min-w-0 flex-1 basis-40 items-center gap-2">
                    <GitBranch className="size-4 shrink-0 text-info" />
                    <span className="min-w-0">
                      <span className="mb-1 block truncate text-xs text-foreground" title={sourceName}>{t('workspaceDetail.environments.sourceProject', { name: sourceName })}</span>
                      <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                        <span className="min-w-0 truncate font-mono text-xs font-semibold">{environment.ref}</span>
                        {applied && <Badge variant="ok" title={t('workspaceDetail.environments.appliedHint')}>{t('workspaceDetail.environments.applied')}</Badge>}
                      </span>
                      <span className="block truncate text-[11px] text-muted-foreground" title={environment.worktree_path}>
                        {environment.worktree_path}
                      </span>
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={!tokenReady || planMutation.isPending}
                      title={tokenReason ?? t('workspaceDetail.environments.deployTitle')}
                      onClick={() => planMutation.mutate(environment)}
                    >
                      {planMutation.isPending ? <BusyLabel>{t('workspaceDetail.plan.busy')}</BusyLabel> : <Rocket />}
                      {t('workspaceDetail.environments.deploy')}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={t('workspaceDetail.environments.deleteAria', { ref: environment.ref })}
                      disabled={!tokenReady}
                      title={tokenReason ?? t('workspaceDetail.environments.deleteTitle')}
                      onClick={() => setDeleteTarget(environment)}
                    >
                      <Trash2 />
                    </Button>
                  </span>
                </div>
              </li>
              );
            })}
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
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [ref, setRef] = useState('');
  const workspace = useWorkspace(workspaceId);
  const catalog = useCatalog();
  const [projectId, setProjectId] = useState('');
  const selectedProjectId = projectId || workspace.data?.services[0]?.project_id || '';
  const createMutation = useMutation({
    mutationFn: (payload: { ref: string }) => api.createEnvironment(workspaceId, { ...payload, repository_id: selectedProjectId }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workspaces', workspaceId, 'environments'] });
      onClose();
    },
  });
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined} data-testid="environment-create-dialog">
        <DialogHeader eyebrow="ENVIRONMENTS" title={t('workspaceDetail.environmentCreate.title')} />
        <DialogBody>
          <div className="grid gap-2">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {t('workspaceDetail.environmentCreate.description')}
            </p>
            <div className="grid gap-1.5">
              <Label htmlFor="environment-project">{t('integration.selectProject')}</Label>
              <select id="environment-project" value={selectedProjectId} onChange={(event) => setProjectId(event.target.value)} className="h-9 rounded-sm border border-input bg-surface px-2 text-xs">
                {workspace.data?.services.map((service) => <option key={service.project_id} value={service.project_id}>{catalog.data?.projects.find((project) => project.id === service.project_id)?.name ?? service.project_id}</option>)}
              </select>
              <Label htmlFor="environment-ref">{t('workspaceDetail.environmentCreate.refLabel')}</Label>
              <Input id="environment-ref" value={ref} onChange={(event) => setRef(event.target.value)} placeholder="main" />
            </div>
            {createMutation.isError && <MutationError error={createMutation.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('workspaceDetail.common.cancel')}
          </Button>
          <Button disabled={createMutation.isPending || !ref.trim()} title={!ref.trim() ? t('workspaceDetail.environmentCreate.reasonEmpty') : t('workspaceDetail.environmentCreate.submitTitle')} onClick={() => createMutation.mutate({ ref: ref.trim() })}>
            {createMutation.isPending ? <BusyLabel>{t('workspaceDetail.common.creating')}</BusyLabel> : <GitBranch />}
            {t('workspaceDetail.environmentCreate.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EnvironmentDeleteDialog({ environment, onClose }: { environment: EnvironmentRecord; onClose: () => void }) {
  const { t } = useTranslation();
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
        <DialogHeader eyebrow="DESTRUCTIVE ACTION" title={t('workspaceDetail.environmentDelete.title', { ref: environment.ref })} />
        <DialogBody>
          <div className="grid gap-2 text-xs">
            <p className="leading-relaxed text-muted-foreground">
              {t('workspaceDetail.environmentDelete.body')}
            </p>
            <ul className="grid gap-1 rounded-sm bg-surface-2 p-2 font-mono text-[11px] text-muted-foreground" data-testid="environment-delete-preview">
              <li>worktree:{environment.worktree_path}</li>
              <li>ref:{environment.ref}({t('workspaceDetail.environmentDelete.recoverHint')})</li>
            </ul>
            {deleteMutation.isError && <MutationError error={deleteMutation.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('workspaceDetail.common.cancel')}
          </Button>
          <Button variant="destructive" disabled={deleteMutation.isPending} title={deleteMutation.isPending ? t('workspaceDetail.common.deleting') : t('workspaceDetail.environmentDelete.submitTitle')} onClick={() => deleteMutation.mutate()}>
            {deleteMutation.isPending ? <BusyLabel>{t('workspaceDetail.common.deleting')}</BusyLabel> : <Trash2 />}
            {t('workspaceDetail.environmentDelete.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
