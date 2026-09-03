import type { components } from '@/api/schema';

type ProjectSummary = components['schemas']['ProjectSummary'];
type ServiceCommand = components['schemas']['ServiceCommand'];
type WorkspaceService = components['schemas']['WorkspaceService'];
type ConnectionProfile = components['schemas']['PostgresConnectionProfile'] | components['schemas']['MinioConnectionProfile'];
type PostgresConnectionProfile = components['schemas']['PostgresConnectionProfile'];
type MinioConnectionProfile = components['schemas']['MinioConnectionProfile'];
type HostTarget = components['schemas']['HostTarget'];
type ComposeTarget = components['schemas']['ComposeTarget'];
type WorkspaceRecord = components['schemas']['WorkspaceRecord'];

/** 工作区编辑的纯函数与默认值构造(列表创建与详情编辑共用) */

export const SUPPORTED_CONNECTION_KINDS = new Set(['postgres', 'minio']);

export type SecretRefPatch = { postgres?: string; minioAccess?: string; minioSecret?: string };

export function commandKind(commandId: string): ServiceCommand['kind'] {
  if (commandId === 'install') return 'dependencies';
  if (commandId === 'build') return 'build';
  if (commandId === 'start' || commandId === 'dev') return 'start';
  return 'quality';
}

export function defaultApplicationPort(project: ProjectSummary) {
  if (project.kind === 'python-uv') return 8000;
  if (project.kind === 'vite-react' || project.kind === 'vite-vue') return 5173;
  if (project.kind === 'nuxt') return 3000;
  return null;
}

export function defaultHostTarget(project: ProjectSummary): HostTarget {
  const port = defaultApplicationPort(project);
  return {
    kind: 'host',
    endpoints: port
      ? [{ name: 'http', protocol: 'tcp', host_port: port, injection: { kind: 'command-owned' } }]
      : [],
    readiness: null,
    readiness_timeout: 60,
    stop_timeout: 10,
  };
}

export function defaultComposeTarget(project: ProjectSummary): ComposeTarget {
  const port = defaultApplicationPort(project) ?? 8000;
  const composeFiles = project.container_capabilities.compose_files;
  return {
    kind: 'compose',
    source:
      composeFiles.length > 0
        ? { kind: 'existing-compose', compose_files: [...composeFiles], profiles: [], service_names: [project.name] }
        : { kind: 'dockerfile', context: '.', dockerfile: project.container_capabilities.dockerfile ?? 'Dockerfile' },
    endpoints: [{ name: 'http', protocol: 'tcp', host_port: port, container_port: port }],
    readiness: { kind: 'tcp', endpoint: 'http' },
    wait_timeout: 120,
  };
}

export function defaultConnectionProfile(kind: string, refs: SecretRefPatch = {}): ConnectionProfile | null {
  if (kind === 'postgres') {
    const profile: PostgresConnectionProfile = {
      kind,
      env_var: 'DATABASE_URL',
      scheme: 'postgresql',
      username: 'postgres',
      database: 'postgres',
      secret_ref: refs.postgres ?? '',
    };
    return profile;
  }
  if (kind === 'minio') {
    const profile: MinioConnectionProfile = {
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
    return profile;
  }
  return null;
}

export function serviceFromProject(project: ProjectSummary, refs: SecretRefPatch = {}): WorkspaceService {
  return {
    project_id: project.id,
    commands: project.commands.map((command) => ({
      id: command.id,
      label: command.label,
      kind: command.kind ?? commandKind(command.id),
      argv: [...command.argv],
      long_running: command.long_running,
    })),
    environment: [],
    connection_profiles: project.requirements.flatMap((kind) => {
      const profile = defaultConnectionProfile(kind, refs);
      return profile ? [profile] : [];
    }),
    execution_target: defaultHostTarget(project),
  };
}

export function cloneWorkspaceInput(record: WorkspaceRecord) {
  return {
    name: record.name,
    mode: record.mode,
    services: record.services.map((service) => ({
      project_id: service.project_id,
      commands: service.commands.map((command) => ({ ...command, argv: [...command.argv] })),
      environment: service.environment.map((binding) => ({ ...binding })),
      connection_profiles: service.connection_profiles.map((profile) => ({ ...profile })),
      execution_target: structuredClone(service.execution_target),
    })),
    bindings: record.bindings.map((binding) => ({ ...binding })),
  } satisfies components['schemas']['WorkspaceInput'];
}

export function isHealthyResource(resource: components['schemas']['RuntimeResource']) {
  return resource.health === 'healthy' || resource.health === 'running';
}

const ENVIRONMENT_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
const SECRET_REFERENCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const TARGET_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;

function sensitiveName(name: string) {
  return ['SECRET', 'PASSWORD', 'TOKEN', 'API_KEY', 'PRIVATE_KEY', 'DATABASE_URL', 'REDIS_URL', 'DSN', 'CREDENTIAL'].some((token) =>
    name.toUpperCase().includes(token),
  );
}

function secretReferenceReason(reference: string | null | undefined, secrets: components['schemas']['SecretMetadata'][], label: string) {
  if (!reference || !SECRET_REFERENCE_PATTERN.test(reference)) return `${label} 缺少有效的 Secret 引用`;
  const secret = secrets.find((candidate) => candidate.id === reference);
  if (!secret) return `${label} 引用的 Secret 不存在`;
  if (!secret.present) return `${label} 的凭据值缺失，请重新写入`;
  return null;
}

export function invalidEnvironmentReason(
  services: WorkspaceService[],
  secrets: components['schemas']['SecretMetadata'][],
) {
  for (const service of services) {
    for (const binding of service.environment) {
      if (!ENVIRONMENT_NAME_PATTERN.test(binding.name)) return '环境变量名格式不正确';
      if (binding.source === 'literal' && sensitiveName(binding.name)) return `${binding.name} 必须使用 host-env 或 Secret 引用`;
      if (binding.source === 'literal' && binding.value === null) return `${binding.name} 缺少 literal 值`;
      if (binding.source === 'host-env' && (!binding.reference || !ENVIRONMENT_NAME_PATTERN.test(binding.reference)))
        return `${binding.name} 缺少有效的本机环境变量引用`;
      if (binding.source === 'secret-store') {
        const reason = secretReferenceReason(binding.reference, secrets, binding.name || '环境变量');
        if (reason) return reason;
      }
    }
  }
  return null;
}

export function invalidConnectionReason(
  services: WorkspaceService[],
  projects: Map<string, ProjectSummary>,
  secrets: components['schemas']['SecretMetadata'][],
) {
  for (const service of services) {
    const project = projects.get(service.project_id);
    if (!project) continue;
    for (const kind of project.requirements) {
      if (!SUPPORTED_CONNECTION_KINDS.has(kind)) return `${kind} 连接适配器尚未支持`;
      const profile = service.connection_profiles.find((candidate) => candidate.kind === kind);
      if (!profile) return `${project.name} 缺少 ${kind} 连接配置`;
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
      const outputNames =
        profile.kind === 'postgres'
          ? [profile.env_var]
          : [profile.endpoint_env, profile.access_key_env, profile.secret_key_env, profile.bucket_env];
      const conflict = outputNames.find((name) => service.environment.some((binding) => binding.name === name));
      if (conflict) return `${project.name} 的 ${conflict} 同时由环境配置和连接 profile 提供`;
    }
  }
  return null;
}

function relativePathValid(value: string) {
  const normalized = value.replaceAll('\\', '/');
  return Boolean(value) && !/^(?:[A-Za-z]:|\/)/.test(normalized) && !normalized.split('/').includes('..');
}

export function invalidCommandReason(services: WorkspaceService[], projects: Map<string, ProjectSummary>) {
  for (const service of services) {
    const projectName = projects.get(service.project_id)?.name ?? service.project_id;
    for (const command of service.commands) {
      if (!command.id.trim() || !command.label.trim()) return `${projectName} 的命令名称不能为空`;
      if (command.argv.length === 0 || !command.argv[0]?.trim()) return `${projectName} 的 ${command.label || '命令'} 缺少可执行程序 token`;
    }
  }
  return null;
}

export function invalidTargetReason(services: WorkspaceService[], projects: Map<string, ProjectSummary>) {
  for (const service of services) {
    const projectName = projects.get(service.project_id)?.name ?? service.project_id;
    const target = service.execution_target;
    if (target.kind === 'host' && (target.readiness_timeout < 1 || target.readiness_timeout > 900 || target.stop_timeout < 1 || target.stop_timeout > 300))
      return `${projectName} 的 Host timeout 超出范围`;
    if (target.kind === 'compose' && (target.wait_timeout < 1 || target.wait_timeout > 900)) return `${projectName} 的 Compose wait timeout 超出范围`;
    if (target.kind === 'compose' && target.endpoints.length === 0) return `${projectName} 的 Compose target 至少需要一个 endpoint`;
    const names = target.endpoints.map((endpoint) => endpoint.name);
    if (names.some((name) => !TARGET_NAME_PATTERN.test(name))) return `${projectName} 的 endpoint 名称不合法`;
    if (new Set(names).size !== names.length) return `${projectName} 的 endpoint 名称不能重复`;
    if (
      target.endpoints.some(
        (endpoint) =>
          endpoint.host_port < 1 ||
          endpoint.host_port > 65535 ||
          ('container_port' in endpoint && (endpoint.container_port < 1 || endpoint.container_port > 65535)),
      )
    )
      return `${projectName} 的 endpoint 端口超出范围`;
    if (target.kind === 'host') {
      const invalidInjection = target.endpoints.find(
        (endpoint) =>
          endpoint.injection.kind === 'environment'
            ? !ENVIRONMENT_NAME_PATTERN.test(endpoint.injection.name)
            : endpoint.injection.kind === 'argument' && !endpoint.injection.option.trim(),
      );
      if (invalidInjection) return `${projectName} 的 ${invalidInjection.name} 端口注入配置不完整`;
    } else if (target.source.kind === 'existing-compose') {
      if (target.source.compose_files.length === 0 || target.source.compose_files.some((path) => !relativePathValid(path)))
        return `${projectName} 缺少合法的 Compose 文件相对路径`;
      if (target.source.service_names.length === 0 || target.source.service_names.some((name) => !name.trim()))
        return `${projectName} 至少需要一个 Compose service name`;
    } else if (!relativePathValid(target.source.context) || !relativePathValid(target.source.dockerfile)) {
      return `${projectName} 的 Dockerfile source 必须是 checkout 内相对路径`;
    }
    if (target.readiness && !names.includes(target.readiness.endpoint)) return `${projectName} 的 readiness endpoint 不存在`;
    if (target.readiness?.kind === 'http' && !target.readiness.path.startsWith('/')) return `${projectName} 的 HTTP readiness path 必须以 / 开头`;
  }
  return null;
}
