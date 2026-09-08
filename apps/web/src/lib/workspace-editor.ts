import i18n from '@/i18n';

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
    depends_on: [],
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
      depends_on: [...(service.depends_on ?? [])],
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
  if (!reference || !SECRET_REFERENCE_PATTERN.test(reference)) return i18n.t('lib.workspaceEditor.invalidSecretReference', { label });
  const secret = secrets.find((candidate) => candidate.id === reference);
  if (!secret) return i18n.t('lib.workspaceEditor.secretNotFound', { label });
  if (!secret.present) return i18n.t('lib.workspaceEditor.secretValueMissing', { label });
  return null;
}

export function invalidEnvironmentReason(
  services: WorkspaceService[],
  secrets: components['schemas']['SecretMetadata'][],
) {
  for (const service of services) {
    for (const binding of service.environment) {
      if (!ENVIRONMENT_NAME_PATTERN.test(binding.name)) return i18n.t('lib.workspaceEditor.invalidEnvName');
      if (binding.source === 'literal' && sensitiveName(binding.name)) return i18n.t('lib.workspaceEditor.sensitiveLiteral', { name: binding.name });
      if (binding.source === 'literal' && binding.value === null) return i18n.t('lib.workspaceEditor.missingLiteralValue', { name: binding.name });
      if (binding.source === 'host-env' && (!binding.reference || !ENVIRONMENT_NAME_PATTERN.test(binding.reference)))
        return i18n.t('lib.workspaceEditor.invalidHostEnvReference', { name: binding.name });
      if (binding.source === 'secret-store') {
        const reason = secretReferenceReason(binding.reference, secrets, binding.name || i18n.t('lib.workspaceEditor.environment'));
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
      if (!SUPPORTED_CONNECTION_KINDS.has(kind)) return i18n.t('lib.workspaceEditor.adapterUnsupported', { kind });
      const profile = service.connection_profiles.find((candidate) => candidate.kind === kind);
      if (!profile) return i18n.t('lib.workspaceEditor.missingConnectionProfile', { project: project.name, kind });
      if (profile.kind === 'postgres') {
        if (!ENVIRONMENT_NAME_PATTERN.test(profile.env_var)) return i18n.t('lib.workspaceEditor.invalidPostgresEnvVar', { project: project.name });
        if (!/^postgresql(?:\+[a-z0-9_]+)?$/.test(profile.scheme)) return i18n.t('lib.workspaceEditor.invalidPostgresScheme', { project: project.name });
        if (!profile.username.trim() || !profile.database.trim()) return i18n.t('lib.workspaceEditor.postgresCredentialsEmpty', { project: project.name });
        const reason = secretReferenceReason(profile.secret_ref, secrets, i18n.t('lib.workspaceEditor.postgresPassword', { project: project.name }));
        if (reason) return reason;
      } else {
        const outputNames = [profile.endpoint_env, profile.access_key_env, profile.secret_key_env, profile.bucket_env];
        if (outputNames.some((name) => !ENVIRONMENT_NAME_PATTERN.test(name))) return i18n.t('lib.workspaceEditor.invalidMinioEnvVar', { project: project.name });
        if (!profile.bucket.trim()) return i18n.t('lib.workspaceEditor.minioBucketEmpty', { project: project.name });
        const accessReason = secretReferenceReason(profile.access_key_secret_ref, secrets, i18n.t('lib.workspaceEditor.minioAccessKey', { project: project.name }));
        if (accessReason) return accessReason;
        const secretReason = secretReferenceReason(profile.secret_key_secret_ref, secrets, i18n.t('lib.workspaceEditor.minioSecretKey', { project: project.name }));
        if (secretReason) return secretReason;
      }
      const outputNames =
        profile.kind === 'postgres'
          ? [profile.env_var]
          : [profile.endpoint_env, profile.access_key_env, profile.secret_key_env, profile.bucket_env];
      const conflict = outputNames.find((name) => service.environment.some((binding) => binding.name === name));
      if (conflict) return i18n.t('lib.workspaceEditor.outputConflict', { project: project.name, conflict });
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
      if (!command.id.trim() || !command.label.trim()) return i18n.t('lib.workspaceEditor.commandNameEmpty', { project: projectName });
      if (command.argv.length === 0 || !command.argv[0]?.trim())
        return i18n.t('lib.workspaceEditor.commandMissingExecutable', { project: projectName, label: command.label || i18n.t('lib.workspaceEditor.command') });
    }
  }
  return null;
}

export function invalidTargetReason(services: WorkspaceService[], projects: Map<string, ProjectSummary>) {
  for (const service of services) {
    const projectName = projects.get(service.project_id)?.name ?? service.project_id;
    const target = service.execution_target;
    if (target.kind === 'host' && (target.readiness_timeout < 1 || target.readiness_timeout > 900 || target.stop_timeout < 1 || target.stop_timeout > 300))
      return i18n.t('lib.workspaceEditor.hostTimeoutRange', { project: projectName });
    if (target.kind === 'compose' && (target.wait_timeout < 1 || target.wait_timeout > 900))
      return i18n.t('lib.workspaceEditor.composeWaitTimeoutRange', { project: projectName });
    if (target.kind === 'compose' && target.endpoints.length === 0)
      return i18n.t('lib.workspaceEditor.composeEndpointRequired', { project: projectName });
    const names = target.endpoints.map((endpoint) => endpoint.name);
    if (names.some((name) => !TARGET_NAME_PATTERN.test(name))) return i18n.t('lib.workspaceEditor.invalidEndpointName', { project: projectName });
    if (new Set(names).size !== names.length) return i18n.t('lib.workspaceEditor.duplicateEndpointName', { project: projectName });
    if (
      target.endpoints.some(
        (endpoint) =>
          endpoint.host_port < 1 ||
          endpoint.host_port > 65535 ||
          ('container_port' in endpoint && (endpoint.container_port < 1 || endpoint.container_port > 65535)),
      )
    )
      return i18n.t('lib.workspaceEditor.endpointPortRange', { project: projectName });
    if (target.kind === 'host') {
      const invalidInjection = target.endpoints.find(
        (endpoint) =>
          endpoint.injection.kind === 'environment'
            ? !ENVIRONMENT_NAME_PATTERN.test(endpoint.injection.name)
            : endpoint.injection.kind === 'argument' && !endpoint.injection.option.trim(),
      );
      if (invalidInjection) return i18n.t('lib.workspaceEditor.incompletePortInjection', { project: projectName, name: invalidInjection.name });
    } else if (target.source.kind === 'existing-compose') {
      if (target.source.compose_files.length === 0 || target.source.compose_files.some((path) => !relativePathValid(path)))
        return i18n.t('lib.workspaceEditor.invalidComposePath', { project: projectName });
      if (target.source.service_names.length === 0 || target.source.service_names.some((name) => !name.trim()))
        return i18n.t('lib.workspaceEditor.composeServiceNameRequired', { project: projectName });
    } else if (!relativePathValid(target.source.context) || !relativePathValid(target.source.dockerfile)) {
      return i18n.t('lib.workspaceEditor.dockerfileRelativePath', { project: projectName });
    }
    if (target.readiness && !names.includes(target.readiness.endpoint))
      return i18n.t('lib.workspaceEditor.readinessEndpointMissing', { project: projectName });
    if (target.readiness?.kind === 'http' && !target.readiness.path.startsWith('/'))
      return i18n.t('lib.workspaceEditor.httpReadinessPath', { project: projectName });
    if (target.application) {
      if (!target.endpoints.some((endpoint) => endpoint.name === target.application?.endpoint && endpoint.protocol === 'tcp'))
        return i18n.t('integration.application.missingEndpoint');
      const path = target.application.path ?? '/';
      if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\') || [...path].some((character) => character.charCodeAt(0) < 32))
        return i18n.t('integration.application.invalidPath');
    }
  }
  return null;
}
