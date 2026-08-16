import { AlertTriangle, Box, Braces, Check, ChevronRight, CircleDot, Container, Database, FileCog, FolderGit2, GitBranch, KeyRound, Layers3, ListChecks, LockKeyhole, Network, Pencil, Plus, RefreshCw, Save, Settings2, ShieldCheck, TerminalSquare, Trash2, Workflow, X } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { api } from '../api';
import type { ComposeTarget, ConnectionProfile, EnvironmentBinding, ExecutionTarget, HostTarget, MiddlewareBinding, MiddlewareKind, MinioConnectionProfile, PlanStepKind, PostgresConnectionProfile, ProjectCommand, ProjectSummary, ReadinessCheck, RunMode, RuntimeResource, SecretMetadata, ServiceCommand, WorkspaceInput, WorkspacePlanResponse, WorkspaceRecord, WorkspaceService } from '../types';
import { BusyLabel, EmptyState, ErrorState, KIND_LABELS, MIDDLEWARE_LABELS, MiddlewareLogo, Modal, MutationError, StatusPill, SuccessMessage, TechLogo, TokenReason, formatDate } from '../ui';
import { useApiToken } from '../use-api-token';

type InspectorTab = 'commands' | 'environment' | 'target' | 'dependencies';
type CreateSecretRefs = Record<string, { postgres?: string; minioAccess?: string; minioSecret?: string }>;

const ENVIRONMENT_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
const SECRET_REFERENCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const SUPPORTED_CONNECTION_KINDS = new Set<MiddlewareKind>(['postgres', 'minio']);

function commandKind(commandId: string): PlanStepKind {
  if (commandId === 'install') return 'dependencies';
  if (commandId === 'build') return 'build';
  if (commandId === 'start' || commandId === 'dev') return 'start';
  return 'quality';
}

function defaultCommands(commands: ProjectCommand[]): ServiceCommand[] {
  return commands.map((command) => ({ ...command, kind: command.kind ?? commandKind(command.id) }));
}

function defaultApplicationPort(project: ProjectSummary) {
  if (project.kind === 'python-uv') return 8000;
  if (project.kind === 'vite-react' || project.kind === 'vite-vue') return 5173;
  if (project.kind === 'nuxt') return 3000;
  return null;
}

function defaultHostTarget(project: ProjectSummary): HostTarget {
  const port = defaultApplicationPort(project);
  return {
    kind: 'host',
    endpoints: port ? [{ name: 'http', protocol: 'tcp', host_port: port, injection: { kind: 'command-owned' } }] : [],
    readiness: null,
    readiness_timeout: 60,
    stop_timeout: 10,
  };
}

function defaultComposeTarget(project: ProjectSummary): ComposeTarget {
  const port = defaultApplicationPort(project) ?? 8000;
  const composeFiles = project.container_capabilities.compose_files;
  return {
    kind: 'compose',
    source: composeFiles.length > 0
      ? { kind: 'existing-compose', compose_files: [...composeFiles], profiles: [], service_names: [project.name] }
      : { kind: 'dockerfile', context: '.', dockerfile: project.container_capabilities.dockerfile ?? 'Dockerfile' },
    endpoints: [{ name: 'http', protocol: 'tcp', host_port: port, container_port: port }],
    readiness: { kind: 'tcp', endpoint: 'http' },
    wait_timeout: 120,
  };
}

function isHealthy(resource: RuntimeResource) {
  return resource.health === 'healthy' || resource.health === 'running';
}

function defaultConnectionProfile(kind: MiddlewareKind, refs: CreateSecretRefs[string] = {}): ConnectionProfile | null {
  if (kind === 'postgres') {
    return { kind, env_var: 'DATABASE_URL', scheme: 'postgresql', username: 'postgres', database: 'postgres', secret_ref: refs.postgres ?? '' };
  }
  if (kind === 'minio') {
    return {
      kind,
      endpoint_env: 'S3_ENDPOINT',
      access_key_env: 'S3_ACCESS_KEY',
      secret_key_env: 'S3_SECRET_KEY',
      bucket_env: 'S3_BUCKET',
      bucket: 'local-assets',
      access_key_secret_ref: refs.minioAccess ?? '',
      secret_key_secret_ref: refs.minioSecret ?? '',
      secure: false,
    };
  }
  return null;
}

function defaultProfiles(project: ProjectSummary, refs: CreateSecretRefs[string] = {}) {
  return project.requirements.flatMap((kind) => {
    const profile = defaultConnectionProfile(kind, refs);
    return profile ? [profile] : [];
  });
}

function serviceFromProject(project: ProjectSummary, refs: CreateSecretRefs[string] = {}): WorkspaceService {
  return {
    project_id: project.id,
    commands: defaultCommands(project.commands),
    environment: [],
    connection_profiles: defaultProfiles(project, refs),
    execution_target: defaultHostTarget(project),
  };
}

function createWorkspaceInput(name: string, projectIds: string[], projects: ProjectSummary[], resources: RuntimeResource[], secretRefs: CreateSecretRefs): WorkspaceInput {
  const selected = projects.filter((project) => projectIds.includes(project.id));
  const required = [...new Set(selected.flatMap((project) => project.requirements))];
  const bindings = required.flatMap((kind) => {
    const resource = resources.find((candidate) => candidate.kind === kind && isHealthy(candidate));
    return resource ? [{ kind, resource_id: resource.id }] : [];
  });
  return {
    name,
    mode: 'development',
    services: selected.map((project) => serviceFromProject(project, secretRefs[project.id])),
    bindings,
  };
}

function cloneWorkspace(record: WorkspaceRecord): WorkspaceInput {
  const cloned = JSON.parse(JSON.stringify({ name: record.name, mode: record.mode, services: record.services, bindings: record.bindings })) as WorkspaceInput;
  return { ...cloned, services: cloned.services.map((service) => ({
    ...service,
    connection_profiles: service.connection_profiles ?? [],
    execution_target: service.execution_target ?? { kind: 'host', endpoints: [], readiness: null, readiness_timeout: 60, stop_timeout: 10 },
  })) };
}

function sensitiveName(name: string) {
  return ['SECRET', 'PASSWORD', 'TOKEN', 'API_KEY', 'PRIVATE_KEY', 'DATABASE_URL', 'REDIS_URL', 'DSN', 'CREDENTIAL'].some((token) => name.toUpperCase().includes(token));
}

function secretReferenceReason(reference: string | null, secrets: SecretMetadata[], label: string) {
  if (!reference || !SECRET_REFERENCE_PATTERN.test(reference)) return `${label} 缺少有效的 Secret 引用`;
  const secret = secrets.find((candidate) => candidate.id === reference);
  if (!secret) return `${label} 引用的 Secret 不存在`;
  if (!secret.present) return `${label} 的凭据值缺失，请重新写入`;
  return null;
}

function invalidEnvironmentReason(services: WorkspaceService[], secrets: SecretMetadata[]) {
  for (const service of services) {
    for (const binding of service.environment) {
      if (!ENVIRONMENT_NAME_PATTERN.test(binding.name)) return '环境变量名格式不正确';
      if (binding.source === 'literal' && sensitiveName(binding.name)) return `${binding.name} 必须使用 host-env 或 Secret 引用`;
      if (binding.source === 'literal' && binding.value === null) return `${binding.name} 缺少 literal 值`;
      if (binding.source === 'host-env' && (!binding.reference || !ENVIRONMENT_NAME_PATTERN.test(binding.reference))) return `${binding.name} 缺少有效的本机环境变量引用`;
      if (binding.source === 'secret-store') {
        const reason = secretReferenceReason(binding.reference, secrets, binding.name || '环境变量');
        if (reason) return reason;
      }
    }
  }
  return null;
}

function invalidConnectionReason(services: WorkspaceService[], projects: Map<string, ProjectSummary>, secrets: SecretMetadata[]) {
  for (const service of services) {
    const project = projects.get(service.project_id);
    if (!project) continue;
    for (const kind of project.requirements) {
      if (!SUPPORTED_CONNECTION_KINDS.has(kind)) return `${MIDDLEWARE_LABELS[kind]} 连接适配器尚未支持`;
      const profile = service.connection_profiles.find((candidate) => candidate.kind === kind);
      if (!profile) return `${project.name} 缺少 ${MIDDLEWARE_LABELS[kind]} 连接配置`;
      if (profile.kind === 'postgres') {
        if (!ENVIRONMENT_NAME_PATTERN.test(profile.env_var)) return `${project.name} 的 PostgreSQL 输出变量名不合法`;
        if (!/^postgresql(?:\+[a-z0-9_]+)?$/.test(profile.scheme)) return `${project.name} 的 PostgreSQL scheme 不合法`;
        if (!profile.username.trim() || !profile.database.trim()) return `${project.name} 的 PostgreSQL 用户名和数据库不能为空`;
        const reason = secretReferenceReason(profile.secret_ref, secrets, `${project.name} PostgreSQL 密码`);
        if (reason) return reason;
      } else {
        const outputNames = [profile.endpoint_env, profile.access_key_env, profile.secret_key_env, profile.bucket_env];
        if (outputNames.some((name) => !ENVIRONMENT_NAME_PATTERN.test(name))) return `${project.name} 的 MinIO 输出变量名不合法`;
        if (!profile.bucket.trim()) return `${project.name} 的 MinIO bucket 不能为空`;
        const accessReason = secretReferenceReason(profile.access_key_secret_ref, secrets, `${project.name} MinIO Access Key`);
        if (accessReason) return accessReason;
        const secretReason = secretReferenceReason(profile.secret_key_secret_ref, secrets, `${project.name} MinIO Secret Key`);
        if (secretReason) return secretReason;
      }
      const outputNames = profile.kind === 'postgres'
        ? [profile.env_var]
        : [profile.endpoint_env, profile.access_key_env, profile.secret_key_env, profile.bucket_env];
      const conflict = outputNames.find((name) => service.environment.some((binding) => binding.name === name));
      if (conflict) return `${project.name} 的 ${conflict} 同时由环境配置和连接 profile 提供`;
    }
  }
  return null;
}

const TARGET_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;

function relativePathValid(value: string) {
  const normalized = value.replaceAll('\\', '/');
  return Boolean(value) && !/^(?:[A-Za-z]:|\/)/.test(normalized) && !normalized.split('/').includes('..');
}

function invalidCommandReason(services: WorkspaceService[], projects: Map<string, ProjectSummary>) {
  for (const service of services) {
    const projectName = projects.get(service.project_id)?.name ?? service.project_id;
    for (const command of service.commands) {
      if (!command.id.trim() || !command.label.trim()) return `${projectName} 的命令名称不能为空`;
      if (command.argv.length === 0 || !command.argv[0]?.trim()) return `${projectName} 的 ${command.label || '命令'} 缺少可执行程序 token`;
    }
  }
  return null;
}

function invalidTargetReason(services: WorkspaceService[], projects: Map<string, ProjectSummary>) {
  for (const service of services) {
    const projectName = projects.get(service.project_id)?.name ?? service.project_id;
    const target = service.execution_target;
    if (target.kind === 'host' && (target.readiness_timeout < 1 || target.readiness_timeout > 900 || target.stop_timeout < 1 || target.stop_timeout > 300)) return `${projectName} 的 Host timeout 超出范围`;
    if (target.kind === 'compose' && (target.wait_timeout < 1 || target.wait_timeout > 900)) return `${projectName} 的 Compose wait timeout 超出范围`;
    if (target.kind === 'compose' && target.endpoints.length === 0) return `${projectName} 的 Compose target 至少需要一个 endpoint`;
    const names = target.endpoints.map((endpoint) => endpoint.name);
    if (names.some((name) => !TARGET_NAME_PATTERN.test(name))) return `${projectName} 的 endpoint 名称不合法`;
    if (new Set(names).size !== names.length) return `${projectName} 的 endpoint 名称不能重复`;
    if (target.endpoints.some((endpoint) => endpoint.host_port < 1 || endpoint.host_port > 65535 || ('container_port' in endpoint && (endpoint.container_port < 1 || endpoint.container_port > 65535)))) return `${projectName} 的 endpoint 端口超出范围`;
    if (target.kind === 'host') {
      const invalidInjection = target.endpoints.find((endpoint) => endpoint.injection.kind === 'environment'
        ? !ENVIRONMENT_NAME_PATTERN.test(endpoint.injection.name)
        : endpoint.injection.kind === 'argument' && !endpoint.injection.option.trim());
      if (invalidInjection) return `${projectName} 的 ${invalidInjection.name} 端口注入配置不完整`;
    } else if (target.source.kind === 'existing-compose') {
      if (target.source.compose_files.length === 0 || target.source.compose_files.some((path) => !relativePathValid(path))) return `${projectName} 缺少合法的 Compose 文件相对路径`;
      if (target.source.service_names.length === 0 || target.source.service_names.some((name) => !name.trim())) return `${projectName} 至少需要一个 Compose service name`;
    } else if (!relativePathValid(target.source.context) || !relativePathValid(target.source.dockerfile)) {
      return `${projectName} 的 Dockerfile source 必须是 checkout 内相对路径`;
    }
    if (target.readiness && !names.includes(target.readiness.endpoint)) return `${projectName} 的 readiness endpoint 不存在`;
    if (target.readiness?.kind === 'http' && !target.readiness.path.startsWith('/')) return `${projectName} 的 HTTP readiness path 必须以 / 开头`;
  }
  return null;
}

export function WorkspacesView({ initialWorkspaceId, onOpenRun }: { initialWorkspaceId: string | null; onOpenRun: (runId: string) => void }) {
  const queryClient = useQueryClient();
  const token = useApiToken();
  const workspaces = useQuery({ queryKey: ['workspaces'], queryFn: api.workspaces });
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: api.catalog });
  const runtime = useQuery({ queryKey: ['runtime'], queryFn: api.runtime });
  const secrets = useQuery({ queryKey: ['secrets'], queryFn: api.secrets });
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs, refetchInterval: 4000 });
  const [selectedId, setSelectedId] = useState<string | null>(initialWorkspaceId);
  const [draft, setDraft] = useState<WorkspaceInput | null>(null);
  const [selectedServiceIndex, setSelectedServiceIndex] = useState(0);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('commands');
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createProjects, setCreateProjects] = useState<string[]>([]);
  const [createSecretRefs, setCreateSecretRefs] = useState<CreateSecretRefs>({});
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [secretManagerOpen, setSecretManagerOpen] = useState(false);
  const [plan, setPlan] = useState<WorkspacePlanResponse | null>(null);
  const secretSelectionRef = useRef<((secretId: string) => void) | null>(null);
  const secretReturnToCreateRef = useRef(false);

  const records = useMemo(() => workspaces.data?.workspaces ?? [], [workspaces.data?.workspaces]);
  const selectedRecord = records.find((workspace) => workspace.id === selectedId) ?? null;
  const projectMap = useMemo(() => new Map((catalog.data?.projects ?? []).map((project) => [project.id, project])), [catalog.data?.projects]);
  const resources = useMemo(() => runtime.data?.resources ?? [], [runtime.data?.resources]);
  const secretRecords = useMemo(() => secrets.data?.secrets ?? [], [secrets.data?.secrets]);
  const activeRunByWorkspace = useMemo(() => new Map((runs.data?.runs ?? []).filter((run) => run.status === 'queued' || run.status === 'running').map((run) => [run.workspace_id, run])), [runs.data?.runs]);

  useEffect(() => { if (initialWorkspaceId) setSelectedId(initialWorkspaceId); }, [initialWorkspaceId]);
  useEffect(() => {
    if (!selectedId && records[0]) setSelectedId(records[0].id);
    if (selectedId && !records.some((record) => record.id === selectedId)) setSelectedId(records[0]?.id ?? null);
  }, [records, selectedId]);

  useEffect(() => {
    if (!selectedRecord) { setDraft(null); return; }
    setDraft(cloneWorkspace(selectedRecord));
    setSelectedServiceIndex(0);
    setPlan(null);
  }, [selectedRecord]);

  const dirty = Boolean(draft && selectedRecord && JSON.stringify(draft) !== JSON.stringify(cloneWorkspace(selectedRecord)));
  const selectedService = draft?.services[selectedServiceIndex] ?? null;
  const selectedProject = selectedService ? projectMap.get(selectedService.project_id) ?? null : null;

  const refreshWorkspaces = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
      queryClient.invalidateQueries({ queryKey: ['overview'] }),
    ]);
  };

  const createMutation = useMutation({
    mutationFn: api.createWorkspace,
    onSuccess: async (workspace) => { setCreateOpen(false); setCreateName(''); setCreateProjects([]); setCreateSecretRefs({}); await refreshWorkspaces(); setSelectedId(workspace.id); },
  });
  const saveMutation = useMutation({
    mutationFn: ({ workspaceId, payload }: { workspaceId: string; payload: WorkspaceInput }) => api.updateWorkspace(workspaceId, { ...payload, expected_revision: selectedRecord?.revision ?? 0 }),
    onSuccess: async () => { setPlan(null); await refreshWorkspaces(); },
  });
  const deleteMutation = useMutation({
    mutationFn: ({ workspaceId, expectedRevision }: { workspaceId: string; expectedRevision: number }) => api.deleteWorkspace(workspaceId, expectedRevision),
    onSuccess: async () => { setDeleteOpen(false); setSelectedId(null); await refreshWorkspaces(); },
  });
  const planMutation = useMutation({
    mutationFn: ({ workspaceId, revision }: { workspaceId: string; revision: number }) => api.createWorkspacePlan(workspaceId, { expected_revision: revision }),
    onSuccess: setPlan,
  });
  const runMutation = useMutation({
    mutationFn: api.createRun,
    onSuccess: async (run) => { setPlan(null); await queryClient.invalidateQueries({ queryKey: ['runs'] }); onOpenRun(run.id); },
  });

  const resetDraftFeedback = () => { setPlan(null); saveMutation.reset(); planMutation.reset(); };
  const openSecretManager = (onSelect?: (secretId: string) => void) => {
    secretSelectionRef.current = onSelect ?? null;
    if (createOpen) {
      secretReturnToCreateRef.current = true;
      setCreateOpen(false);
    }
    setSecretManagerOpen(true);
  };
  const closeSecretManager = () => {
    secretSelectionRef.current = null;
    setSecretManagerOpen(false);
    if (secretReturnToCreateRef.current) {
      secretReturnToCreateRef.current = false;
      setCreateOpen(true);
    }
  };
  const setMode = (mode: RunMode) => { setDraft((current) => current ? { ...current, mode } : current); resetDraftFeedback(); };
  const updateService = (index: number, updater: (service: WorkspaceService) => WorkspaceService) => {
    setDraft((current) => current ? { ...current, services: current.services.map((service, serviceIndex) => serviceIndex === index ? updater(service) : service) } : current);
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

  const addProject = (projectId: string) => {
    const project = projectMap.get(projectId);
    if (!project || !draft || draft.services.some((service) => service.project_id === projectId)) return;
    const nextService = serviceFromProject(project);
    setDraft({ ...draft, services: [...draft.services, nextService] });
    setSelectedServiceIndex(draft.services.length);
    resetDraftFeedback();
  };

  const removeService = (index: number) => {
    setDraft((current) => current ? { ...current, services: current.services.filter((_, serviceIndex) => serviceIndex !== index) } : current);
    setSelectedServiceIndex((current) => Math.max(0, Math.min(current, (draft?.services.length ?? 1) - 2)));
    resetDraftFeedback();
  };

  const addEnvironment = () => selectedService && updateService(selectedServiceIndex, (service) => ({ ...service, environment: [...service.environment, { name: '', source: 'literal', value: '', reference: null }] }));
  const updateEnvironment = (index: number, patch: Partial<EnvironmentBinding>) => updateService(selectedServiceIndex, (service) => ({ ...service, environment: service.environment.map((binding, bindingIndex) => bindingIndex === index ? { ...binding, ...patch } : binding) }));
  const updateProfile = (kind: 'postgres' | 'minio', patch: Partial<PostgresConnectionProfile> | Partial<MinioConnectionProfile>) => updateService(selectedServiceIndex, (service) => {
    const existing = service.connection_profiles.find((profile) => profile.kind === kind) ?? defaultConnectionProfile(kind);
    if (!existing) return service;
    const updated = { ...existing, ...patch } as ConnectionProfile;
    return { ...service, connection_profiles: [...service.connection_profiles.filter((profile) => profile.kind !== kind), updated] };
  });
  const clearDeletedSecret = (secretId: string) => {
    setDraft((current) => current ? {
      ...current,
      services: current.services.map((service) => ({
        ...service,
        environment: service.environment.map((binding) => binding.source === 'secret-store' && binding.reference === secretId ? { ...binding, reference: '' } : binding),
        connection_profiles: service.connection_profiles.map((profile) => profile.kind === 'postgres'
          ? profile.secret_ref === secretId ? { ...profile, secret_ref: '' } : profile
          : {
              ...profile,
              access_key_secret_ref: profile.access_key_secret_ref === secretId ? '' : profile.access_key_secret_ref,
              secret_key_secret_ref: profile.secret_key_secret_ref === secretId ? '' : profile.secret_key_secret_ref,
            }),
      })),
    } : current);
    setCreateSecretRefs((current) => Object.fromEntries(Object.entries(current).map(([projectId, refs]) => [projectId, {
      postgres: refs.postgres === secretId ? undefined : refs.postgres,
      minioAccess: refs.minioAccess === secretId ? undefined : refs.minioAccess,
      minioSecret: refs.minioSecret === secretId ? undefined : refs.minioSecret,
    }])));
    resetDraftFeedback();
  };
  const secretStateReason = secrets.isLoading ? '正在读取 Secret 状态' : secrets.isError ? '无法读取 Secret 状态，请重试' : null;
  const environmentError = draft ? secretStateReason ?? invalidEnvironmentReason(draft.services, secretRecords) : null;
  const connectionError = draft ? secretStateReason ?? invalidConnectionReason(draft.services, projectMap, secretRecords) : null;
  const commandError = draft ? invalidCommandReason(draft.services, projectMap) : null;
  const targetError = draft ? invalidTargetReason(draft.services, projectMap) : null;
  const requiredKinds = draft ? [...new Set(draft.services.flatMap((service) => projectMap.get(service.project_id)?.requirements ?? []))] : [];
  const missingDependency = requiredKinds.find((kind) => {
    const resourceId = draft?.bindings.find((binding) => binding.kind === kind)?.resource_id;
    return !resourceId || !resources.some((resource) => resource.id === resourceId && resource.kind === kind && isHealthy(resource));
  });
  const saveDisabledReason = token.disabledReason ?? (!dirty ? '没有待保存修改' : !draft?.name.trim() ? '工作区名称不能为空' : !draft.services.length ? '工作区至少需要一个服务' : commandError ?? targetError ?? environmentError ?? connectionError);
  const planDisabledReason = token.disabledReason ?? (dirty ? '请先保存工作区修改' : commandError ?? targetError ?? environmentError ?? connectionError ?? (!selectedRecord ? '未选择工作区' : runtime.isLoading ? '正在读取 Docker 状态' : runtime.isError ? '无法读取 Docker 状态' : !runtime.data?.docker_available ? runtime.data?.recovery ?? 'Docker 不可用' : missingDependency ? `缺少健康的 ${MIDDLEWARE_LABELS[missingDependency]} 绑定` : planMutation.isPending ? '正在运行预检' : null));
  const createServices = createProjects.flatMap((projectId) => {
    const project = projectMap.get(projectId);
    return project ? [serviceFromProject(project, createSecretRefs[projectId])] : [];
  });
  const createConnectionError = createProjects.length > 0
    ? invalidCommandReason(createServices, projectMap) ?? invalidTargetReason(createServices, projectMap) ?? secretStateReason ?? invalidConnectionReason(createServices, projectMap, secretRecords)
    : null;

  return (
    <div className="split-workbench">
      <aside className="context-sidebar">
        <header><div><span>WORKSPACES</span><h1>工作区</h1></div><button className="icon-button" type="button" aria-label="新建工作区" title={token.disabledReason ?? '新建工作区'} disabled={!token.configured} onClick={() => setCreateOpen(true)}><Plus size={18} /></button></header>
        <div className="context-list">
          {workspaces.isLoading ? <div className="sidebar-loading"><BusyLabel>正在读取</BusyLabel></div> : records.length === 0 ? <EmptyState icon={Workflow} title="没有工作区" detail="新建一个可保存的多项目组合" /> : records.map((workspace) => {
            const activeRun = activeRunByWorkspace.get(workspace.id);
            return <button className={selectedId === workspace.id ? 'context-row is-active' : 'context-row'} type="button" key={workspace.id} onClick={() => setSelectedId(workspace.id)}><span className="row-icon"><Workflow size={17} /></span><span className="row-main"><strong>{workspace.name}</strong><small>{workspace.services.length} 服务 · r{workspace.revision}</small></span>{activeRun ? <StatusPill status={activeRun.status} /> : <ChevronRight size={16} />}</button>;
          })}
        </div>
        <footer><TokenReason compact /></footer>
      </aside>

      <section className="workbench-main">
        {workspaces.isError ? <ErrorState error={workspaces.error} onRetry={() => void workspaces.refetch()} title="无法读取工作区" /> : !selectedRecord || !draft ? <EmptyState icon={Workflow} title="选择一个工作区" detail="已保存配置会显示在此处" /> : (
          <>
            <header className="object-header">
              <div className="object-title"><span className={dirty ? 'revision-badge is-dirty' : 'revision-badge'}>{dirty ? '未保存' : `REV ${selectedRecord.revision}`}</span><div><input className="title-input" aria-label="工作区名称" value={draft.name} onChange={(event) => { setDraft({ ...draft, name: event.target.value }); resetDraftFeedback(); }} /><p>{draft.services.length} 个服务 · 更新于 {formatDate(selectedRecord.updated_at)}</p></div></div>
              <div className="object-actions"><div className="segmented-control" aria-label="运行模式"><button type="button" aria-pressed={draft.mode === 'development'} className={draft.mode === 'development' ? 'is-active' : ''} onClick={() => setMode('development')}><TerminalSquare size={15} />开发</button><button type="button" aria-pressed={draft.mode === 'integrated'} className={draft.mode === 'integrated' ? 'is-active' : ''} onClick={() => setMode('integrated')}><Workflow size={15} />集成</button></div><button className="secondary-button" type="button" disabled={Boolean(saveDisabledReason) || saveMutation.isPending} title={saveDisabledReason ?? '保存工作区'} onClick={() => saveMutation.mutate({ workspaceId: selectedRecord.id, payload: draft })}>{saveMutation.isPending ? <BusyLabel>保存中</BusyLabel> : <><Save size={16} />保存</>}</button><button className="primary-button" type="button" disabled={Boolean(planDisabledReason)} title={planDisabledReason ?? '运行预检'} onClick={() => planMutation.mutate({ workspaceId: selectedRecord.id, revision: selectedRecord.revision })}>{planMutation.isPending ? <BusyLabel>预检中</BusyLabel> : <><ListChecks size={17} />运行预检</>}</button></div>
            </header>
            {(catalog.isError || runtime.isError) && <div className="workbench-alerts"><ErrorState error={catalog.error ?? runtime.error} onRetry={() => void Promise.all([catalog.refetch(), runtime.refetch()])} title="无法读取项目或 Docker 状态" /></div>}
            {(catalog.data?.errors.length ?? 0) > 0 && <div className="workbench-alerts"><div className="inline-message is-error" role="alert">扫描跳过 {catalog.data?.errors.length} 个目录：{catalog.data?.errors.join('；')}</div></div>}
            {!runtime.isLoading && runtime.data && !runtime.data.docker_available && <div className="workbench-alerts"><ErrorState error={new Error(runtime.data.recovery ?? 'Docker 不可用')} onRetry={() => void runtime.refetch()} title="Docker 不可用" /></div>}
            {(saveMutation.isError || planMutation.isError) && <MutationError error={saveMutation.error ?? planMutation.error} />}
            <div className="workspace-layout">
              <section className="service-column">
                <header><div><FolderGit2 size={17} /><h2>服务</h2><span>{draft.services.length}</span></div><select aria-label="添加项目服务" value="" onChange={(event) => addProject(event.target.value)}><option value="">添加项目…</option>{(catalog.data?.projects ?? []).filter((project) => !draft.services.some((service) => service.project_id === project.id)).map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}</select></header>
                <div className="service-rows">{draft.services.map((service, index) => {
                  const project = projectMap.get(service.project_id);
                  return <button className={selectedServiceIndex === index ? 'service-row is-active' : 'service-row'} type="button" key={`${service.project_id}-${index}`} onClick={() => setSelectedServiceIndex(index)}><span className={`service-logo kind-${project?.kind ?? 'unknown'}`}>{project ? <TechLogo kind={project.kind} /> : <Braces size={18} />}</span><span className="row-main"><strong>{project?.name ?? service.project_id}</strong><small>{service.commands.length} 命令 · {service.execution_target.kind === 'compose' ? 'Compose' : 'Host'} · {service.connection_profiles.length} 连接</small></span>{project?.dirty ? <CircleDot size={14} className="warning-icon" /> : <Check size={14} className="ready-icon" />}<ChevronRight size={16} /></button>;
                })}</div>
                <button className="danger-link" type="button" onClick={() => setDeleteOpen(true)}><Trash2 size={15} />删除工作区</button>
              </section>

              <section className="service-inspector">
                {!selectedService || !selectedProject ? <EmptyState icon={Settings2} title="选择服务" detail="配置命令、环境变量、运行目标和中间件" /> : <>
                  <header className="inspector-heading"><div><span className={`service-logo kind-${selectedProject.kind}`}><TechLogo kind={selectedProject.kind} /></span><div><h2>{selectedProject.name}</h2><p><GitBranch size={12} />{selectedProject.branch} · {KIND_LABELS[selectedProject.kind]}</p></div></div><button className="icon-button subtle" type="button" aria-label={`移除 ${selectedProject.name}`} title="从工作区移除" disabled={draft.services.length === 1} onClick={() => removeService(selectedServiceIndex)}><X size={17} /></button></header>
                  <nav className="inspector-tabs" aria-label="服务配置" role="tablist"><button type="button" role="tab" aria-selected={inspectorTab === 'commands'} className={inspectorTab === 'commands' ? 'is-active' : ''} onClick={() => setInspectorTab('commands')}><TerminalSquare size={15} />命令</button><button type="button" role="tab" aria-selected={inspectorTab === 'environment'} className={inspectorTab === 'environment' ? 'is-active' : ''} onClick={() => setInspectorTab('environment')}><KeyRound size={15} />环境</button><button type="button" role="tab" aria-selected={inspectorTab === 'target'} className={inspectorTab === 'target' ? 'is-active' : ''} onClick={() => setInspectorTab('target')}><Container size={15} />目标</button><button type="button" role="tab" aria-selected={inspectorTab === 'dependencies'} className={inspectorTab === 'dependencies' ? 'is-active' : ''} onClick={() => setInspectorTab('dependencies')}><Database size={15} />依赖</button></nav>
                  <div className="inspector-content" role="tabpanel">
                    {inspectorTab === 'commands' && <CommandEditor service={selectedService} onChange={(commands) => updateService(selectedServiceIndex, (service) => ({ ...service, commands }))} />}
                    {inspectorTab === 'environment' && <EnvironmentEditor bindings={selectedService.environment} secrets={secretRecords} secretsLoading={secrets.isLoading} secretsError={secrets.error} onRetrySecrets={() => void secrets.refetch()} onManageSecret={openSecretManager} onAdd={addEnvironment} onChange={updateEnvironment} onRemove={(index) => updateService(selectedServiceIndex, (service) => ({ ...service, environment: service.environment.filter((_, bindingIndex) => bindingIndex !== index) }))} />}
                    {inspectorTab === 'target' && <TargetEditor project={selectedProject} target={selectedService.execution_target} onChange={(execution_target) => updateService(selectedServiceIndex, (service) => ({ ...service, execution_target }))} />}
                    {inspectorTab === 'dependencies' && <DependencyEditor project={selectedProject} service={selectedService} bindings={draft.bindings} resources={resources} secrets={secretRecords} secretsLoading={secrets.isLoading} secretsError={secrets.error} onRetrySecrets={() => void secrets.refetch()} onBindingChange={setBinding} onProfileChange={updateProfile} onManageSecret={openSecretManager} />}
                  </div>
                </>}
              </section>
            </div>
          </>
        )}
      </section>

      {createOpen && <Modal title="新建工作区" eyebrow="SAVED WORKSPACE" onClose={() => setCreateOpen(false)} wide footer={<><button className="secondary-button" type="button" onClick={() => setCreateOpen(false)}>取消</button><button className="primary-button" type="button" disabled={!token.configured || !createName.trim() || createProjects.length === 0 || Boolean(createConnectionError) || createMutation.isPending} title={token.disabledReason ?? createConnectionError ?? '创建工作区'} onClick={() => createMutation.mutate(createWorkspaceInput(createName.trim(), createProjects, catalog.data?.projects ?? [], resources, createSecretRefs))}>{createMutation.isPending ? <BusyLabel>正在创建</BusyLabel> : <><Plus size={16} />创建工作区</>}</button></>}>
        <div className="form-grid"><label className="field is-wide"><span>工作区名称</span><input autoFocus value={createName} onChange={(event) => setCreateName(event.target.value)} placeholder="Supplier 本地集成" /></label></div>
        <div className="selection-list" aria-label="选择工作区项目">{catalog.isError ? <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} /> : (catalog.data?.projects ?? []).map((project) => <label key={project.id} className={createProjects.includes(project.id) ? 'selection-row is-selected' : 'selection-row'}><input type="checkbox" checked={createProjects.includes(project.id)} onChange={() => setCreateProjects((current) => current.includes(project.id) ? current.filter((id) => id !== project.id) : [...current, project.id])} /><span className={`service-logo kind-${project.kind}`}><TechLogo kind={project.kind} /></span><span className="row-main"><strong>{project.name}</strong><small>{project.path}</small></span><span>{project.branch}</span></label>)}</div>
        {createProjects.length > 0 && <CreateConnectionSetup projects={createProjects.map((projectId) => projectMap.get(projectId)).filter((project): project is ProjectSummary => Boolean(project))} refs={createSecretRefs} secrets={secretRecords} secretsLoading={secrets.isLoading} secretsError={secrets.error} onRetrySecrets={() => void secrets.refetch()} onChange={(projectId, patch) => setCreateSecretRefs((current) => ({ ...current, [projectId]: { ...current[projectId], ...patch } }))} onManageSecret={openSecretManager} />}
        {createConnectionError && <div className="inline-message is-error" role="alert"><AlertTriangle size={16} /><span>{createConnectionError}</span></div>}
        {createMutation.isError && <MutationError error={createMutation.error} />}
      </Modal>}

      {deleteOpen && selectedRecord && <Modal title="删除工作区" eyebrow="DESTRUCTIVE ACTION" onClose={() => setDeleteOpen(false)} footer={<><button className="secondary-button" type="button" onClick={() => setDeleteOpen(false)}>取消</button><button className="danger-button" type="button" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate({ workspaceId: selectedRecord.id, expectedRevision: selectedRecord.revision })}>{deleteMutation.isPending ? <BusyLabel>删除中</BusyLabel> : <><Trash2 size={16} />删除工作区</>}</button></>}><p className="modal-copy">仅可删除尚未产生预检计划或运行历史的本地配置；已有历史的工作区必须保留。不会删除仓库 checkout 或外部中间件。</p>{deleteMutation.isError && <MutationError error={deleteMutation.error} />}</Modal>}

      {plan && <PlanModal plan={plan} running={runMutation.isPending} runError={runMutation.error} onClose={() => setPlan(null)} onRun={() => plan.plan_id && runMutation.mutate({ plan_id: plan.plan_id, idempotency_key: crypto.randomUUID() })} />}
      {secretManagerOpen && <SecretManager secrets={secretRecords} loading={secrets.isLoading} queryError={secrets.error} onRetry={() => void secrets.refetch()} onClose={closeSecretManager} onUse={(secretId) => secretSelectionRef.current?.(secretId)} onDeleted={clearDeletedSecret} />}
    </div>
  );
}

function CommandEditor({ service, onChange }: { service: WorkspaceService; onChange: (commands: ServiceCommand[]) => void }) {
  const update = (index: number, patch: Partial<ServiceCommand>) => onChange(service.commands.map((command, commandIndex) => commandIndex === index ? { ...command, ...patch } : command));
  const updateToken = (commandIndex: number, tokenIndex: number, value: string) => {
    const command = service.commands[commandIndex];
    if (!command) return;
    update(commandIndex, { argv: command.argv.map((token, index) => index === tokenIndex ? value : token) });
  };
  const removeToken = (commandIndex: number, tokenIndex: number) => {
    const command = service.commands[commandIndex];
    if (!command) return;
    update(commandIndex, { argv: command.argv.filter((_, index) => index !== tokenIndex) });
  };
  return <div className="config-section"><div className="section-title"><div><TerminalSquare size={16} /><h3>生命周期命令</h3></div><button className="text-button" type="button" onClick={() => onChange([...service.commands, { id: `command-${service.commands.length + 1}`, label: '自定义命令', kind: 'quality', argv: [''], long_running: false }])}><Plus size={14} />添加命令</button></div><div className="command-editor">{service.commands.map((command, index) => <div className="command-row" key={`${command.id}-${index}`}><div className="command-meta"><input aria-label={`命令 ${index + 1} 名称`} value={command.label} onChange={(event) => update(index, { label: event.target.value })} /><select aria-label={`命令 ${index + 1} 阶段`} value={command.kind} onChange={(event) => update(index, { kind: event.target.value as PlanStepKind })}><option value="inspect">检查</option><option value="dependencies">依赖</option><option value="quality">质量</option><option value="build">构建</option><option value="deploy">部署</option><option value="start">启动</option></select><label className="compact-check"><input type="checkbox" checked={command.long_running} onChange={(event) => update(index, { long_running: event.target.checked })} />长运行</label><button className="icon-button subtle" type="button" aria-label={`删除 ${command.label}`} onClick={() => onChange(service.commands.filter((_, commandIndex) => commandIndex !== index))}><Trash2 size={15} /></button></div><section className="argv-editor" aria-label={`${command.label} argv token`}><header><span><Braces size={14} />ARGV TOKENS</span><button className="text-button" type="button" onClick={() => update(index, { argv: [...command.argv, ''] })}><Plus size={13} />添加 token</button></header>{command.argv.length === 0 ? <button className="argv-empty" type="button" onClick={() => update(index, { argv: [''] })}>添加可执行程序 token</button> : command.argv.map((token, tokenIndex) => <div className="argv-token" key={tokenIndex}><span>{String(tokenIndex).padStart(2, '0')}</span><input aria-label={`${command.label} 参数 ${tokenIndex + 1}`} value={token} onChange={(event) => updateToken(index, tokenIndex, event.target.value)} placeholder={tokenIndex === 0 ? '可执行程序' : '参数，可包含空格'} /><button className="icon-button subtle" type="button" aria-label={`删除 ${command.label} 参数 ${tokenIndex + 1}`} onClick={() => removeToken(index, tokenIndex)}><X size={14} /></button></div>)}</section></div>)}</div></div>;
}

function EnvironmentEditor({ bindings, secrets, secretsLoading, secretsError, onRetrySecrets, onManageSecret, onAdd, onChange, onRemove }: {
  bindings: EnvironmentBinding[];
  secrets: SecretMetadata[];
  secretsLoading: boolean;
  secretsError: unknown;
  onRetrySecrets: () => void;
  onManageSecret: (onSelect?: (secretId: string) => void) => void;
  onAdd: () => void;
  onChange: (index: number, patch: Partial<EnvironmentBinding>) => void;
  onRemove: (index: number) => void;
}) {
  return <div className="config-section">
    <div className="section-title"><div><KeyRound size={16} /><h3>环境变量</h3></div><div className="section-actions"><button className="text-button" type="button" onClick={() => onManageSecret()}><ShieldCheck size={14} />管理 Secret</button><button className="text-button" type="button" onClick={onAdd}><Plus size={14} />添加变量</button></div></div>
    {Boolean(secretsError) && <ErrorState error={secretsError} onRetry={onRetrySecrets} title="无法读取 Secret" />}
    {bindings.length === 0 ? <EmptyState icon={KeyRound} title="没有环境变量" detail="Literal 仅用于非敏感值；敏感值使用本机环境变量或系统 Secret" /> : <div className="env-table"><header><span>变量名</span><span>来源</span><span>值或引用</span><span /></header>{bindings.map((binding, index) => {
      const invalidSensitive = binding.source === 'literal' && sensitiveName(binding.name);
      const invalidReference = binding.source === 'host-env'
        ? !binding.reference || !ENVIRONMENT_NAME_PATTERN.test(binding.reference)
        : binding.source === 'secret-store' ? Boolean(secretReferenceReason(binding.reference, secrets, binding.name || '环境变量')) : false;
      return <div className={invalidSensitive || invalidReference ? 'env-row has-error' : 'env-row'} key={index}>
        <input aria-label={`环境变量 ${index + 1} 名称`} value={binding.name} onChange={(event) => onChange(index, { name: event.target.value })} placeholder="DATABASE_URL" />
        <select aria-label={`${binding.name || `变量 ${index + 1}`} 来源`} value={binding.source} onChange={(event) => { const source = event.target.value as EnvironmentBinding['source']; onChange(index, source === 'literal' ? { source, value: '', reference: null } : { source, value: null, reference: '' }); }}><option value="literal">Literal</option><option value="host-env">Host env</option><option value="secret-store">Secret</option></select>
        {binding.source === 'secret-store'
          ? <SecretSelect label={`${binding.name || `变量 ${index + 1}`} Secret`} value={binding.reference ?? ''} secrets={secrets} loading={secretsLoading} onChange={(reference) => onChange(index, { reference })} onManage={onManageSecret} />
          : <input aria-label={`${binding.name || `变量 ${index + 1}`} ${binding.source === 'literal' ? '值' : '引用'}`} value={binding.source === 'literal' ? binding.value ?? '' : binding.reference ?? ''} onChange={(event) => onChange(index, binding.source === 'literal' ? { value: event.target.value } : { reference: event.target.value })} placeholder={binding.source === 'literal' ? '非敏感配置值' : 'HOST_ENV_NAME'} />}
        <button className="icon-button subtle" type="button" aria-label={`删除 ${binding.name || `变量 ${index + 1}`}`} onClick={() => onRemove(index)}><Trash2 size={15} /></button>
        {invalidSensitive && <small>敏感变量必须使用 Host env 或 Secret</small>}
        {invalidReference && !invalidSensitive && <small>{binding.source === 'host-env' ? 'Host env 引用必须是合法环境变量名' : secretReferenceReason(binding.reference, secrets, binding.name || '环境变量')}</small>}
      </div>;
    })}</div>}
  </div>;
}

function TargetEditor({ project, target, onChange }: { project: ProjectSummary; target: ExecutionTarget; onChange: (target: ExecutionTarget) => void }) {
  const switchTarget = (kind: ExecutionTarget['kind']) => {
    if (kind === target.kind) return;
    if (kind === 'compose') {
      const next = defaultComposeTarget(project);
      if (target.kind === 'host' && target.endpoints.length > 0) next.endpoints = target.endpoints.map((endpoint) => ({ name: endpoint.name, protocol: endpoint.protocol, host_port: endpoint.host_port, container_port: endpoint.host_port }));
      onChange(next);
      return;
    }
    const next = defaultHostTarget(project);
    if (target.kind === 'compose') next.endpoints = target.endpoints.map((endpoint) => ({ name: endpoint.name, protocol: endpoint.protocol, host_port: endpoint.host_port, injection: { kind: 'command-owned' } }));
    onChange(next);
  };
  return <div className="config-section target-editor">
    <div className="section-title"><div><Container size={16} /><h3>运行目标</h3></div><div className="target-kind-control" aria-label="运行目标类型"><button type="button" aria-pressed={target.kind === 'host'} className={target.kind === 'host' ? 'is-active' : ''} onClick={() => switchTarget('host')}><TerminalSquare size={14} />本机进程</button><button type="button" aria-pressed={target.kind === 'compose'} className={target.kind === 'compose' ? 'is-active' : ''} onClick={() => switchTarget('compose')}><Container size={14} />Docker Compose</button></div></div>
    <div className="capability-strip"><FileCog size={15} /><span><strong>容器检测</strong><small>{project.container_capabilities.compose_files.length > 0 ? `Compose: ${project.container_capabilities.compose_files.join(', ')}` : '未检测到 Compose 文件'} · {project.container_capabilities.dockerfile ? `Dockerfile: ${project.container_capabilities.dockerfile}` : '未检测到 Dockerfile'}</small></span></div>
    {target.kind === 'host' ? <HostTargetEditor target={target} onChange={onChange} /> : <ComposeTargetEditor target={target} onChange={onChange} />}
  </div>;
}

function HostTargetEditor({ target, onChange }: { target: HostTarget; onChange: (target: HostTarget) => void }) {
  const updateEndpoint = (index: number, patch: Partial<HostTarget['endpoints'][number]>) => onChange({ ...target, endpoints: target.endpoints.map((endpoint, endpointIndex) => endpointIndex === index ? { ...endpoint, ...patch } : endpoint) });
  return <>
    <section className="target-block"><header><div><Network size={15} /><span><strong>Host endpoints</strong><small>声明端口所有权以及注入命令的方式</small></span></div><button className="text-button" type="button" onClick={() => onChange({ ...target, endpoints: [...target.endpoints, { name: `port-${target.endpoints.length + 1}`, protocol: 'tcp', host_port: 3000, injection: { kind: 'command-owned' } }] })}><Plus size={13} />添加 endpoint</button></header>{target.endpoints.length === 0 ? <EmptyState icon={Network} title="没有 endpoint" detail="无网络入口的 worker 可以保持为空" /> : <div className="target-endpoints">{target.endpoints.map((endpoint, index) => <div className="target-endpoint host-endpoint" key={index}><input aria-label={`Host endpoint ${index + 1} 名称`} value={endpoint.name} onChange={(event) => updateEndpoint(index, { name: event.target.value })} /><select aria-label={`${endpoint.name} 协议`} value={endpoint.protocol} onChange={(event) => updateEndpoint(index, { protocol: event.target.value as 'tcp' | 'udp' })}><option value="tcp">TCP</option><option value="udp">UDP</option></select><input type="number" min="1" max="65535" aria-label={`${endpoint.name} Host 端口`} value={endpoint.host_port} onChange={(event) => updateEndpoint(index, { host_port: Number(event.target.value) })} /><select aria-label={`${endpoint.name} 注入方式`} value={endpoint.injection.kind} onChange={(event) => { const kind = event.target.value; updateEndpoint(index, { injection: kind === 'environment' ? { kind, name: 'PORT' } : kind === 'argument' ? { kind, option: '--port' } : { kind: 'command-owned' } }); }}><option value="command-owned">命令自行监听</option><option value="environment">环境变量</option><option value="argument">命令参数</option></select>{endpoint.injection.kind === 'environment' && <input aria-label={`${endpoint.name} 端口环境变量`} value={endpoint.injection.name} onChange={(event) => updateEndpoint(index, { injection: { kind: 'environment', name: event.target.value } })} />}{endpoint.injection.kind === 'argument' && <input aria-label={`${endpoint.name} 端口参数选项`} value={endpoint.injection.option} onChange={(event) => updateEndpoint(index, { injection: { kind: 'argument', option: event.target.value } })} />}{endpoint.injection.kind === 'command-owned' && <span className="injection-owned">由命令负责</span>}<button className="icon-button subtle" type="button" aria-label={`删除 Host endpoint ${endpoint.name}`} onClick={() => onChange({ ...target, endpoints: target.endpoints.filter((_, endpointIndex) => endpointIndex !== index) })}><Trash2 size={14} /></button></div>)}</div>}</section>
    <ReadinessEditor endpoints={target.endpoints.map((endpoint) => endpoint.name)} value={target.readiness} allowNone onChange={(readiness) => onChange({ ...target, readiness })} />
    <div className="timeout-grid"><label className="field"><span>Readiness timeout</span><input type="number" min="1" max="900" aria-label="Host readiness timeout" value={target.readiness_timeout} onChange={(event) => onChange({ ...target, readiness_timeout: Number(event.target.value) })} /></label><label className="field"><span>Stop timeout</span><input type="number" min="1" max="300" aria-label="Host stop timeout" value={target.stop_timeout} onChange={(event) => onChange({ ...target, stop_timeout: Number(event.target.value) })} /></label></div>
  </>;
}

function ComposeTargetEditor({ target, onChange }: { target: ComposeTarget; onChange: (target: ComposeTarget) => void }) {
  const source = target.source;
  const updateEndpoint = (index: number, patch: Partial<ComposeTarget['endpoints'][number]>) => onChange({ ...target, endpoints: target.endpoints.map((endpoint, endpointIndex) => endpointIndex === index ? { ...endpoint, ...patch } : endpoint) });
  const switchSource = (kind: ComposeTarget['source']['kind']) => {
    if (kind === source.kind) return;
    onChange({ ...target, source: kind === 'existing-compose' ? { kind, compose_files: ['compose.yml'], profiles: [], service_names: ['app'] } : { kind, context: '.', dockerfile: 'Dockerfile' } });
  };
  return <>
    <section className="target-block"><header><div><Layers3 size={15} /><span><strong>Compose source</strong><small>所有路径都相对当前项目 checkout</small></span></div><div className="source-kind-control"><button type="button" aria-pressed={source.kind === 'existing-compose'} className={source.kind === 'existing-compose' ? 'is-active' : ''} onClick={() => switchSource('existing-compose')}>已有 Compose</button><button type="button" aria-pressed={source.kind === 'dockerfile'} className={source.kind === 'dockerfile' ? 'is-active' : ''} onClick={() => switchSource('dockerfile')}>Dockerfile</button></div></header>{source.kind === 'existing-compose' ? <div className="compose-source-lists"><StringListEditor label="Compose 文件" values={source.compose_files} placeholder="compose.yml" required onChange={(compose_files) => onChange({ ...target, source: { ...source, compose_files } })} /><StringListEditor label="Profiles" values={source.profiles} placeholder="dev" onChange={(profiles) => onChange({ ...target, source: { ...source, profiles } })} /><StringListEditor label="Service names" values={source.service_names} placeholder="api" required onChange={(service_names) => onChange({ ...target, source: { ...source, service_names } })} /></div> : <div className="dockerfile-source"><label className="field"><span>Build context</span><input aria-label="Dockerfile build context" value={source.context} onChange={(event) => onChange({ ...target, source: { ...source, context: event.target.value } })} /></label><label className="field"><span>Dockerfile</span><input aria-label="Dockerfile 相对路径" value={source.dockerfile} onChange={(event) => onChange({ ...target, source: { ...source, dockerfile: event.target.value } })} /></label></div>}</section>
    <section className="target-block"><header><div><Network size={15} /><span><strong>Compose endpoints</strong><small>映射 localhost host port 到容器端口</small></span></div><button className="text-button" type="button" onClick={() => onChange({ ...target, endpoints: [...target.endpoints, { name: `port-${target.endpoints.length + 1}`, protocol: 'tcp', host_port: 3000, container_port: 3000 }] })}><Plus size={13} />添加 endpoint</button></header><div className="target-endpoints">{target.endpoints.map((endpoint, index) => <div className="target-endpoint compose-endpoint" key={index}><input aria-label={`Compose endpoint ${index + 1} 名称`} value={endpoint.name} onChange={(event) => updateEndpoint(index, { name: event.target.value })} /><select aria-label={`${endpoint.name} 协议`} value={endpoint.protocol} onChange={(event) => updateEndpoint(index, { protocol: event.target.value as 'tcp' | 'udp' })}><option value="tcp">TCP</option><option value="udp">UDP</option></select><input type="number" min="1" max="65535" aria-label={`${endpoint.name} Host 端口`} value={endpoint.host_port} onChange={(event) => updateEndpoint(index, { host_port: Number(event.target.value) })} /><input type="number" min="1" max="65535" aria-label={`${endpoint.name} 容器端口`} value={endpoint.container_port} onChange={(event) => updateEndpoint(index, { container_port: Number(event.target.value) })} /><button className="icon-button subtle" type="button" aria-label={`删除 Compose endpoint ${endpoint.name}`} onClick={() => onChange({ ...target, endpoints: target.endpoints.filter((_, endpointIndex) => endpointIndex !== index) })}><Trash2 size={14} /></button></div>)}</div></section>
    <ReadinessEditor endpoints={target.endpoints.map((endpoint) => endpoint.name)} value={target.readiness} onChange={(readiness) => readiness && onChange({ ...target, readiness })} />
    <div className="timeout-grid"><label className="field"><span>Compose wait timeout</span><input type="number" min="1" max="900" aria-label="Compose wait timeout" value={target.wait_timeout} onChange={(event) => onChange({ ...target, wait_timeout: Number(event.target.value) })} /></label></div>
  </>;
}

function ReadinessEditor({ endpoints, value, allowNone = false, onChange }: { endpoints: string[]; value: ReadinessCheck | null; allowNone?: boolean; onChange: (value: ReadinessCheck | null) => void }) {
  const firstEndpoint = endpoints[0] ?? '';
  return <section className="target-block readiness-block"><header><div><Check size={15} /><span><strong>Readiness</strong><small>Run 仅在显式探针通过后进入可用状态</small></span></div><div className="source-kind-control">{allowNone && <button type="button" aria-pressed={!value} className={!value ? 'is-active' : ''} onClick={() => onChange(null)}>无</button>}<button type="button" disabled={!firstEndpoint} aria-pressed={value?.kind === 'http'} className={value?.kind === 'http' ? 'is-active' : ''} onClick={() => onChange({ kind: 'http', endpoint: value?.endpoint || firstEndpoint, path: value?.kind === 'http' ? value.path : '/health' })}>HTTP</button><button type="button" disabled={!firstEndpoint} aria-pressed={value?.kind === 'tcp'} className={value?.kind === 'tcp' ? 'is-active' : ''} onClick={() => onChange({ kind: 'tcp', endpoint: value?.endpoint || firstEndpoint })}>TCP</button></div></header>{value && <div className="readiness-fields"><label className="field"><span>Endpoint</span><select aria-label="Readiness endpoint" value={value.endpoint} onChange={(event) => onChange({ ...value, endpoint: event.target.value })}>{endpoints.map((endpoint) => <option key={endpoint} value={endpoint}>{endpoint}</option>)}</select></label>{value.kind === 'http' && <label className="field"><span>Path</span><input aria-label="HTTP readiness path" value={value.path} onChange={(event) => onChange({ ...value, path: event.target.value })} /></label>}</div>}</section>;
}

function StringListEditor({ label, values, placeholder, required = false, onChange }: { label: string; values: string[]; placeholder: string; required?: boolean; onChange: (values: string[]) => void }) {
  return <section className="string-list-editor"><header><span>{label}{required && <b>REQUIRED</b>}</span><button className="icon-button subtle" type="button" aria-label={`添加 ${label}`} title={`添加 ${label}`} onClick={() => onChange([...values, ''])}><Plus size={13} /></button></header>{values.length === 0 ? <button className="string-list-empty" type="button" onClick={() => onChange([''])}>添加</button> : values.map((value, index) => <div key={index}><input aria-label={`${label} ${index + 1}`} value={value} placeholder={placeholder} onChange={(event) => onChange(values.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} /><button className="icon-button subtle" type="button" aria-label={`删除 ${label} ${index + 1}`} onClick={() => onChange(values.filter((_, itemIndex) => itemIndex !== index))}><X size={13} /></button></div>)}</section>;
}

function SecretSelect({ label, value, secrets, loading, onChange, onManage }: { label: string; value: string; secrets: SecretMetadata[]; loading: boolean; onChange: (secretId: string) => void; onManage: (onSelect?: (secretId: string) => void) => void }) {
  const selectedExists = secrets.some((secret) => secret.id === value);
  return <span className="secret-select-control">
    <select aria-label={label} value={value} disabled={loading} onChange={(event) => onChange(event.target.value)}>
      <option value="">{loading ? '正在读取 Secret…' : '选择 Secret…'}</option>
      {value && !selectedExists && <option value={value}>引用不存在 · {value}</option>}
      {secrets.map((secret) => <option key={secret.id} value={secret.id} disabled={!secret.present}>{secret.name} · v{secret.version}{secret.present ? '' : ' · 值缺失'}</option>)}
    </select>
    <button className="icon-button subtle" type="button" aria-label={`管理 ${label}`} title="管理 Secret；新建后自动选入当前字段" onClick={() => onManage(onChange)}><KeyRound size={15} /></button>
  </span>;
}

function endpointLabel(resource: RuntimeResource | undefined, containerPort: number) {
  const endpoint = resource?.endpoints.find((candidate) => candidate.protocol === 'tcp' && candidate.container_port === containerPort);
  return endpoint ? `${endpoint.host}:${endpoint.host_port} → ${endpoint.container_port}/tcp` : null;
}

function DependencyEditor({ project, service, bindings, resources, secrets, secretsLoading, secretsError, onRetrySecrets, onBindingChange, onProfileChange, onManageSecret }: {
  project: ProjectSummary;
  service: WorkspaceService;
  bindings: MiddlewareBinding[];
  resources: RuntimeResource[];
  secrets: SecretMetadata[];
  secretsLoading: boolean;
  secretsError: unknown;
  onRetrySecrets: () => void;
  onBindingChange: (kind: MiddlewareKind, resourceId: string) => void;
  onProfileChange: (kind: 'postgres' | 'minio', patch: Partial<PostgresConnectionProfile> | Partial<MinioConnectionProfile>) => void;
  onManageSecret: (onSelect?: (secretId: string) => void) => void;
}) {
  return <div className="config-section connection-section">
    <div className="section-title"><div><Database size={16} /><h3>连接与注入</h3></div><button className="text-button" type="button" onClick={() => onManageSecret()}><ShieldCheck size={14} />管理 Secret</button></div>
    <p className="section-description">容器绑定属于工作区；连接 profile 属于当前服务。预检会解析宿主机 endpoint，并只展示脱敏输出。</p>
    {Boolean(secretsError) && <ErrorState error={secretsError} onRetry={onRetrySecrets} title="无法读取 Secret" />}
    {project.requirements.length === 0 ? <EmptyState icon={Database} title="无需外部中间件" detail="当前服务没有声明 PostgreSQL、Redis、Elasticsearch 或 MinIO" /> : <div className="connection-editor">{project.requirements.map((kind) => {
      const candidates = resources.filter((resource) => resource.kind === kind);
      const selectedId = bindings.find((binding) => binding.kind === kind)?.resource_id ?? '';
      const selected = candidates.find((resource) => resource.id === selectedId);
      const expectedPort = kind === 'postgres' ? 5432 : kind === 'minio' ? 9000 : 0;
      const endpoint = expectedPort ? endpointLabel(selected, expectedPort) : null;
      const profile = service.connection_profiles.find((candidate) => candidate.kind === kind);
      const supported = SUPPORTED_CONNECTION_KINDS.has(kind);
      return <section className={supported ? 'connection-block' : 'connection-block is-blocked'} key={kind} aria-label={`${MIDDLEWARE_LABELS[kind]} 连接配置`}>
        <header><span><MiddlewareLogo kind={kind} /><span><strong>{MIDDLEWARE_LABELS[kind]}</strong><small>{supported ? 'STRUCTURED CONNECTION' : 'ADAPTER BLOCKED'}</small></span></span>{supported ? <span className={selected && endpoint && profile ? 'state-label is-ready' : 'state-label is-warning'}>{selected && endpoint && profile ? '配置完整' : '需要配置'}</span> : <AlertTriangle size={17} />}</header>
        {!supported ? <div className="adapter-blocker" role="alert"><AlertTriangle size={17} /><div><strong>连接适配器尚未支持</strong><span>当前版本只支持 PostgreSQL 与 MinIO。此服务的保存与预检将被阻断。</span></div></div> : <>
          <div className="connection-target"><label className="field"><span>工作区资源目标</span><select aria-label={`${MIDDLEWARE_LABELS[kind]} 绑定`} value={selectedId} onChange={(event) => onBindingChange(kind, event.target.value)}><option value="">未绑定</option>{candidates.map((resource) => <option key={resource.id} value={resource.id} disabled={!isHealthy(resource)}>{resource.name} · {resource.health}</option>)}</select></label><div className={endpoint ? 'endpoint-readout is-ready' : 'endpoint-readout is-warning'}><Network size={15} /><span><strong>{endpoint ?? `未发布 ${expectedPort}/tcp`}</strong><small>{selected ? `${selected.name} · ${selected.protected ? '受保护' : '平台托管'}` : '选择健康实例后解析 endpoint'}</small></span></div></div>
          {!profile ? <div className="inline-message is-error"><AlertTriangle size={16} /><span>缺少服务连接 profile</span><button className="text-button" type="button" onClick={() => onProfileChange(kind as 'postgres' | 'minio', {})}>生成默认配置</button></div> : profile.kind === 'postgres'
            ? <PostgresProfileEditor profile={profile} secrets={secrets} secretsLoading={secretsLoading} onChange={(patch) => onProfileChange('postgres', patch)} onManageSecret={onManageSecret} />
            : <MinioProfileEditor profile={profile} secrets={secrets} secretsLoading={secretsLoading} onChange={(patch) => onProfileChange('minio', patch)} onManageSecret={onManageSecret} />}
        </>}
      </section>;
    })}</div>}
  </div>;
}

function PostgresProfileEditor({ profile, secrets, secretsLoading, onChange, onManageSecret }: { profile: PostgresConnectionProfile; secrets: SecretMetadata[]; secretsLoading: boolean; onChange: (patch: Partial<PostgresConnectionProfile>) => void; onManageSecret: (onSelect?: (secretId: string) => void) => void }) {
  return <div className="profile-fields postgres-profile"><label className="field"><span>输出环境变量</span><input aria-label="PostgreSQL 输出环境变量" value={profile.env_var} onChange={(event) => onChange({ env_var: event.target.value })} /></label><label className="field"><span>Scheme</span><input aria-label="PostgreSQL Scheme" value={profile.scheme} onChange={(event) => onChange({ scheme: event.target.value })} /></label><label className="field"><span>用户名</span><input aria-label="PostgreSQL 用户名" value={profile.username} onChange={(event) => onChange({ username: event.target.value })} /></label><label className="field"><span>数据库</span><input aria-label="PostgreSQL 数据库" value={profile.database} onChange={(event) => onChange({ database: event.target.value })} /></label><div className="field is-wide"><span>密码 Secret</span><SecretSelect label="PostgreSQL 密码 Secret" value={profile.secret_ref} secrets={secrets} loading={secretsLoading} onChange={(secret_ref) => onChange({ secret_ref })} onManage={onManageSecret} /></div></div>;
}

function MinioProfileEditor({ profile, secrets, secretsLoading, onChange, onManageSecret }: { profile: MinioConnectionProfile; secrets: SecretMetadata[]; secretsLoading: boolean; onChange: (patch: Partial<MinioConnectionProfile>) => void; onManageSecret: (onSelect?: (secretId: string) => void) => void }) {
  return <div className="profile-fields minio-profile"><label className="field"><span>Endpoint 输出</span><input aria-label="MinIO Endpoint 输出环境变量" value={profile.endpoint_env} onChange={(event) => onChange({ endpoint_env: event.target.value })} /></label><label className="field"><span>Access Key 输出</span><input aria-label="MinIO Access Key 输出环境变量" value={profile.access_key_env} onChange={(event) => onChange({ access_key_env: event.target.value })} /></label><label className="field"><span>Secret Key 输出</span><input aria-label="MinIO Secret Key 输出环境变量" value={profile.secret_key_env} onChange={(event) => onChange({ secret_key_env: event.target.value })} /></label><label className="field"><span>Bucket 输出</span><input aria-label="MinIO Bucket 输出环境变量" value={profile.bucket_env} onChange={(event) => onChange({ bucket_env: event.target.value })} /></label><label className="field"><span>Bucket</span><input aria-label="MinIO Bucket" value={profile.bucket} onChange={(event) => onChange({ bucket: event.target.value })} /></label><label className="toggle-field"><input type="checkbox" checked={profile.secure} onChange={(event) => onChange({ secure: event.target.checked })} /><span><strong>HTTPS</strong><small>{profile.secure ? '生成 https endpoint' : '生成 http endpoint'}</small></span></label><div className="field"><span>Access Key Secret</span><SecretSelect label="MinIO Access Key Secret" value={profile.access_key_secret_ref} secrets={secrets} loading={secretsLoading} onChange={(access_key_secret_ref) => onChange({ access_key_secret_ref })} onManage={onManageSecret} /></div><div className="field"><span>Secret Key Secret</span><SecretSelect label="MinIO Secret Key Secret" value={profile.secret_key_secret_ref} secrets={secrets} loading={secretsLoading} onChange={(secret_key_secret_ref) => onChange({ secret_key_secret_ref })} onManage={onManageSecret} /></div></div>;
}

function CreateConnectionSetup({ projects, refs, secrets, secretsLoading, secretsError, onRetrySecrets, onChange, onManageSecret }: { projects: ProjectSummary[]; refs: CreateSecretRefs; secrets: SecretMetadata[]; secretsLoading: boolean; secretsError: unknown; onRetrySecrets: () => void; onChange: (projectId: string, patch: CreateSecretRefs[string]) => void; onManageSecret: (onSelect?: (secretId: string) => void) => void }) {
  const requiredProjects = projects.filter((project) => project.requirements.length > 0);
  if (requiredProjects.length === 0) return null;
  return <section className="create-connection-setup"><header><div><Database size={16} /><span><strong>连接初始值</strong><small>创建时写入通用 profile，之后可在服务依赖页调整</small></span></div><button className="text-button" type="button" onClick={() => onManageSecret()}><ShieldCheck size={14} />管理 Secret</button></header>{Boolean(secretsError) && <ErrorState error={secretsError} onRetry={onRetrySecrets} title="无法读取 Secret" />}<div>{requiredProjects.map((project) => <div className="create-connection-project" key={project.id}><strong>{project.name}</strong>{project.requirements.map((kind) => {
    if (!SUPPORTED_CONNECTION_KINDS.has(kind)) return <div className="adapter-inline-block" role="alert" key={kind}><AlertTriangle size={15} /><span>{MIDDLEWARE_LABELS[kind]} 连接适配器尚未支持</span></div>;
    if (kind === 'postgres') return <div className="create-secret-row" key={kind}><span><MiddlewareLogo kind={kind} /><span><b>PostgreSQL</b><small>DATABASE_URL · postgresql · postgres/postgres</small></span></span><SecretSelect label={`${project.name} PostgreSQL 密码 Secret`} value={refs[project.id]?.postgres ?? ''} secrets={secrets} loading={secretsLoading} onChange={(postgres) => onChange(project.id, { postgres })} onManage={onManageSecret} /></div>;
    return <div className="create-secret-row is-minio" key={kind}><span><MiddlewareLogo kind={kind} /><span><b>MinIO</b><small>S3_ENDPOINT / ACCESS_KEY / SECRET_KEY / BUCKET</small></span></span><SecretSelect label={`${project.name} MinIO Access Key Secret`} value={refs[project.id]?.minioAccess ?? ''} secrets={secrets} loading={secretsLoading} onChange={(minioAccess) => onChange(project.id, { minioAccess })} onManage={onManageSecret} /><SecretSelect label={`${project.name} MinIO Secret Key Secret`} value={refs[project.id]?.minioSecret ?? ''} secrets={secrets} loading={secretsLoading} onChange={(minioSecret) => onChange(project.id, { minioSecret })} onManage={onManageSecret} /></div>;
  })}</div>)}</div></section>;
}

function SecretManager({ secrets, loading, queryError, onRetry, onClose, onUse, onDeleted }: { secrets: SecretMetadata[]; loading: boolean; queryError: unknown; onRetry: () => void; onClose: () => void; onUse: (secretId: string) => void; onDeleted: (secretId: string) => void }) {
  const queryClient = useQueryClient();
  const token = useApiToken();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [createName, setCreateName] = useState('');
  const [createValue, setCreateValue] = useState('');
  const [updateValue, setUpdateValue] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const selected = secrets.find((secret) => secret.id === selectedId) ?? secrets[0] ?? null;
  useEffect(() => { if (!selectedId && secrets[0]) setSelectedId(secrets[0].id); }, [secrets, selectedId]);
  const refresh = async () => { await queryClient.invalidateQueries({ queryKey: ['secrets'] }); };
  const createMutation = useMutation({
    mutationFn: api.createSecret,
    onSuccess: async (secret) => { setCreateName(''); setCreateValue(''); setSelectedId(secret.id); setSuccess(`已创建 ${secret.name} 并选入当前字段`); onUse(secret.id); await refresh(); },
  });
  const updateMutation = useMutation({
    mutationFn: ({ secretId, expectedVersion, value }: { secretId: string; expectedVersion: number; value: string }) => api.updateSecret(secretId, { expected_version: expectedVersion, value }),
    onSuccess: async (secret) => { setUpdateValue(''); setSuccess(`${secret.name} 已更新到 v${secret.version}`); await refresh(); },
  });
  const deleteMutation = useMutation({
    mutationFn: ({ secretId, expectedVersion }: { secretId: string; expectedVersion: number }) => api.deleteSecret(secretId, expectedVersion),
    onSuccess: async (_, variables) => { onDeleted(variables.secretId); setSelectedId(null); setConfirmDelete(false); setSuccess('Secret 已删除；未保存草稿中的相关引用已清空'); await refresh(); },
  });
  const resetFeedback = () => { setSuccess(null); createMutation.reset(); updateMutation.reset(); deleteMutation.reset(); };
  return <Modal title="Secret 管理" eyebrow="WINDOWS CREDENTIAL MANAGER" onClose={onClose} wide footer={<button className="secondary-button" type="button" onClick={onClose}>完成</button>}>
    <div className="secret-manager-intro"><LockKeyhole size={18} /><div><strong>值只写入系统凭据存储</strong><span>客户端只读取名称、presence 与版本；密码输入不会回显，也不会进入响应缓存。</span></div></div>
    {Boolean(queryError) && <ErrorState error={queryError} onRetry={onRetry} title="无法读取 Secret" />}
    <section className="secret-create"><header><strong>创建 Secret</strong><span>创建后自动选入发起操作的字段</span></header><div><label className="field"><span>显示名称</span><input aria-label="新 Secret 名称" value={createName} onChange={(event) => { setCreateName(event.target.value); resetFeedback(); }} placeholder="Supplier database password" /></label><label className="field"><span>Secret 值</span><input aria-label="新 Secret 值" type="password" autoComplete="new-password" value={createValue} onChange={(event) => { setCreateValue(event.target.value); resetFeedback(); }} placeholder="不会回显" /></label><button className="primary-button" type="button" disabled={!token.configured || !createName.trim() || !createValue || createMutation.isPending} title={token.disabledReason ?? '创建并选用'} onClick={() => createMutation.mutate({ name: createName.trim(), value: createValue })}>{createMutation.isPending ? <BusyLabel>创建中</BusyLabel> : <><Plus size={15} />创建并选用</>}</button></div></section>
    <div className="secret-workbench"><div className="secret-list" aria-label="Secret 列表">{loading ? <div className="sidebar-loading"><BusyLabel>正在读取 Secret</BusyLabel></div> : secrets.length === 0 ? <EmptyState icon={LockKeyhole} title="还没有 Secret" detail="在上方创建后即可用于连接 profile 或环境变量" /> : secrets.map((secret) => <button type="button" className={selected?.id === secret.id ? 'secret-row is-active' : 'secret-row'} key={secret.id} onClick={() => { setSelectedId(secret.id); setUpdateValue(''); setConfirmDelete(false); resetFeedback(); }}><span className={secret.present ? 'secret-presence is-ready' : 'secret-presence is-missing'}><span /></span><span><strong>{secret.name}</strong><small>{secret.id}</small></span><span>v{secret.version}</span></button>)}</div><section className="secret-detail">{selected ? <><header><div><span className={selected.present ? 'state-label is-ready' : 'state-label is-warning'}>{selected.present ? '凭据可用' : '值缺失'}</span><h3>{selected.name}</h3><code>{selected.id}</code></div><button className="secondary-button" type="button" disabled={!selected.present} title={selected.present ? '用于当前字段' : '凭据值缺失，不能选用'} onClick={() => { onUse(selected.id); setSuccess(`${selected.name} 已选入当前字段`); }}><Check size={15} />使用</button></header><dl><div><dt>版本</dt><dd>v{selected.version}</dd></div><div><dt>更新</dt><dd>{formatDate(selected.updated_at)}</dd></div><div><dt>值</dt><dd>从不回显</dd></div></dl><div className="secret-update"><label className="field"><span>写入新值</span><input aria-label={`更新 ${selected.name} 的值`} type="password" autoComplete="new-password" value={updateValue} disabled={!selected.present} onChange={(event) => { setUpdateValue(event.target.value); resetFeedback(); }} placeholder={selected.present ? '输入后覆盖旧值' : '值缺失时请删除并重建'} /></label><button className="secondary-button" type="button" disabled={!token.configured || !selected.present || !updateValue || updateMutation.isPending} title={!selected.present ? '当前值缺失，删除 metadata 后重新创建' : token.disabledReason ?? '按当前版本更新'} onClick={() => updateMutation.mutate({ secretId: selected.id, expectedVersion: selected.version, value: updateValue })}>{updateMutation.isPending ? <BusyLabel>更新中</BusyLabel> : <><Pencil size={15} />更新值</>}</button></div><div className="secret-delete"><label className="confirmation-check"><input type="checkbox" checked={confirmDelete} onChange={(event) => { setConfirmDelete(event.target.checked); resetFeedback(); }} />我确认删除此 Secret metadata 与系统凭据</label><button className="danger-button" type="button" disabled={!token.configured || !confirmDelete || deleteMutation.isPending} title={token.disabledReason ?? (!confirmDelete ? '先确认删除' : '被工作区引用时服务端将阻断')} onClick={() => deleteMutation.mutate({ secretId: selected.id, expectedVersion: selected.version })}>{deleteMutation.isPending ? <BusyLabel>删除中</BusyLabel> : <><Trash2 size={15} />删除 Secret</>}</button></div></> : <EmptyState icon={LockKeyhole} title="选择 Secret" detail="查看 presence、版本并执行受控操作" />}</section></div>
    {success && <SuccessMessage>{success}</SuccessMessage>}
    {(createMutation.isError || updateMutation.isError || deleteMutation.isError) && <div className="mutation-recovery"><MutationError error={createMutation.error ?? updateMutation.error ?? deleteMutation.error} /><button className="text-button" type="button" onClick={() => { resetFeedback(); onRetry(); }}><RefreshCw size={14} />刷新后重试</button></div>}
  </Modal>;
}

function PlanModal({ plan, running, runError, onClose, onRun }: { plan: WorkspacePlanResponse; running: boolean; runError: unknown; onClose: () => void; onRun: () => void }) {
  const token = useApiToken();
  return <Modal title="运行计划" eyebrow="IMMUTABLE PLAN" onClose={onClose} wide footer={<><button className="secondary-button" type="button" onClick={onClose}>返回配置</button><button className="primary-button" type="button" disabled={!plan.ready || !plan.plan_id || !token.configured || running} title={!plan.ready ? '计划存在阻断项' : !plan.plan_id ? '计划缺少可执行 ID' : token.disabledReason ?? '启动运行'} onClick={onRun}>{running ? <BusyLabel>正在启动</BusyLabel> : <><Workflow size={17} />运行工作区</>}</button></>}>
    <div className={plan.ready ? 'plan-banner is-ready' : 'plan-banner is-blocked'}>{plan.ready ? <Check size={19} /> : <ListChecks size={19} />}<div><strong>{plan.ready ? '预检通过' : `${plan.blockers.length} 项阻断`}</strong><span>Workspace revision {plan.workspace_revision ?? '—'} · {plan.mode === 'integrated' ? '集成模式' : '开发模式'}</span></div></div>
    {[...plan.blockers, ...plan.warnings].length > 0 && <div className="plan-issues">{[...plan.blockers, ...plan.warnings].map((issue) => <div key={`${issue.code}-${issue.title}`}><strong>{issue.title}</strong><span>{issue.detail}</span><small>{issue.recovery}</small></div>)}</div>}
    {plan.connection_mappings.length > 0 && <section className="connection-preview"><header><div><ShieldCheck size={16} /><span><strong>连接注入预览</strong><small>敏感值已脱敏，实际值只在运行进程环境中解析</small></span></div><span>{plan.connection_mappings.length} 个映射</span></header>{plan.connection_mappings.map((mapping) => <div className="connection-mapping" key={`${mapping.project_id}-${mapping.kind}`}><div><MiddlewareLogo kind={mapping.kind} /><span><strong>{MIDDLEWARE_LABELS[mapping.kind]} · {mapping.resource_name}</strong><small>{mapping.project_id} · {mapping.resource_id}</small></span></div><div>{mapping.outputs.map((output) => <code key={output.name}><span>{output.name}{output.sensitive && <LockKeyhole size={11} />}</span><b>{output.redacted_value}</b></code>)}</div></div>)}</section>}
    <ol className="plan-step-list">{plan.steps.map((step, index) => <li key={step.id}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{step.title}</strong><p>{step.detail}</p>{step.commands.map((command) => <code className="plan-command" key={`${command.project_id}-${command.command_id}`}><span>{command.project_name}<em>{command.label} · {command.cwd}</em></span><span className="plan-command-detail"><span className="plan-argv">{command.argv.map((token, tokenIndex) => <b key={tokenIndex}>{token || '""'}</b>)}</span>{(command.environment?.length ?? 0) > 0 && <small><LockKeyhole size={11} />环境 {command.environment.map((variable) => variable.name).join(' · ')} · 值已隐藏</small>}</span></code>)}{(step.deployments ?? []).map((deployment) => <section className="plan-deployment" key={deployment.revision_id}><header><Box size={16} /><span><strong>{deployment.project_id}</strong><small>revision {deployment.revision_id.slice(0, 12)} · wait {deployment.wait_timeout_seconds}s</small></span></header><dl><div><dt>Source</dt><dd><code>{deployment.source_fingerprint.slice(0, 16)}</code></dd></div><div><dt>Target config</dt><dd><code>{deployment.target_config_fingerprint.slice(0, 16)}</code></dd></div></dl><div className="deployment-artifacts"><span><b>服务</b>{deployment.services.map((service) => <code key={service}>{service}</code>)}</span><span><b>Immutable images</b>{deployment.immutable_images.map((image) => <code key={image}>{image}</code>)}</span></div></section>)}</div></li>)}</ol>
    {!!runError && <MutationError error={runError} />}
  </Modal>;
}
