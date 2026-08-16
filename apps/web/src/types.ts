export type ProjectKind = 'python-uv' | 'vite-react' | 'vite-vue' | 'nuxt' | 'compose' | 'unknown';
export type MiddlewareKind = 'postgres' | 'redis' | 'elasticsearch' | 'minio';
export type ResourceHealth = 'healthy' | 'running' | 'unhealthy' | 'stopped';
export type RunMode = 'development' | 'integrated';
export type EnvironmentSource = 'literal' | 'host-env' | 'secret-store';
export type EndpointProtocol = 'tcp' | 'udp';
export type PlanStepKind = 'inspect' | 'dependencies' | 'quality' | 'build' | 'deploy' | 'start';
export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';
export type RunEventKind = 'status' | 'stage' | 'stdout' | 'stderr' | 'system';

export interface ProjectCommand {
  id: string;
  label: string;
  argv: string[];
  kind: PlanStepKind | null;
  long_running: boolean;
}

export interface ContainerCapabilities {
  dockerfile: string | null;
  compose_files: string[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  path: string;
  kind: ProjectKind;
  branch: string;
  dirty: boolean;
  commands: ProjectCommand[];
  requirements: MiddlewareKind[];
  warnings: string[];
  container_capabilities: ContainerCapabilities;
}

export interface CatalogResponse {
  generated_at: string;
  roots: string[];
  projects: ProjectSummary[];
  errors: string[];
}

export interface RuntimeEndpoint {
  protocol: EndpointProtocol;
  container_port: number;
  host: '127.0.0.1';
  host_port: number;
}

export interface RuntimeResource {
  id: string;
  name: string;
  kind: MiddlewareKind;
  image: string;
  state: string;
  status_text: string;
  health: ResourceHealth;
  managed: boolean;
  protected: boolean;
  ports: string;
  endpoints: RuntimeEndpoint[];
  owner_workspace_id: string | null;
}

export interface RuntimeResponse {
  generated_at: string;
  docker_available: boolean;
  resources: RuntimeResource[];
  error_code: string | null;
  recovery: string | null;
}

export interface RuntimeProcessRecord {
  id: string;
  pid: number;
  run_id: string;
  project_id: string;
  project_name: string;
  command_id: string;
  label: string;
  cwd: string;
  argv: string[];
  long_running: boolean;
  started_at: string;
}

export interface RuntimeProcessListResponse {
  generated_at: string;
  processes: RuntimeProcessRecord[];
}

export interface OverviewResponse {
  generated_at: string;
  api_status: string;
  docker_available: boolean;
  project_count: number;
  dirty_project_count: number;
  middleware_count: number;
  protected_kinds: MiddlewareKind[];
}

export interface MiddlewareBinding {
  kind: MiddlewareKind;
  resource_id: string;
}

export interface EnvironmentBinding {
  name: string;
  source: EnvironmentSource;
  value: string | null;
  reference: string | null;
}

export interface ServiceCommand {
  id: string;
  label: string;
  kind: PlanStepKind;
  argv: string[];
  long_running: boolean;
}

export type PortInjection =
  | { kind: 'command-owned' }
  | { kind: 'environment'; name: string }
  | { kind: 'argument'; option: string };

export interface HostEndpoint {
  name: string;
  protocol: EndpointProtocol;
  host_port: number;
  injection: PortInjection;
}

export interface ComposeEndpoint {
  name: string;
  protocol: EndpointProtocol;
  host_port: number;
  container_port: number;
}

export type ReadinessCheck =
  | { kind: 'http'; endpoint: string; path: string }
  | { kind: 'tcp'; endpoint: string };

export interface ExistingComposeSource {
  kind: 'existing-compose';
  compose_files: string[];
  profiles: string[];
  service_names: string[];
}

export interface DockerfileSource {
  kind: 'dockerfile';
  context: string;
  dockerfile: string;
}

export type ComposeSource = ExistingComposeSource | DockerfileSource;

export interface HostTarget {
  kind: 'host';
  endpoints: HostEndpoint[];
  readiness: ReadinessCheck | null;
  readiness_timeout: number;
  stop_timeout: number;
}

export interface ComposeTarget {
  kind: 'compose';
  source: ComposeSource;
  endpoints: ComposeEndpoint[];
  readiness: ReadinessCheck;
  wait_timeout: number;
}

export type ExecutionTarget = HostTarget | ComposeTarget;

export interface PostgresConnectionProfile {
  kind: 'postgres';
  env_var: string;
  scheme: string;
  username: string;
  database: string;
  secret_ref: string;
}

export interface MinioConnectionProfile {
  kind: 'minio';
  endpoint_env: string;
  access_key_env: string;
  secret_key_env: string;
  bucket_env: string;
  bucket: string;
  access_key_secret_ref: string;
  secret_key_secret_ref: string;
  secure: boolean;
}

export type ConnectionProfile = PostgresConnectionProfile | MinioConnectionProfile;

export interface WorkspaceService {
  project_id: string;
  commands: ServiceCommand[];
  environment: EnvironmentBinding[];
  connection_profiles: ConnectionProfile[];
  execution_target: ExecutionTarget;
}

export interface WorkspaceInput {
  name: string;
  mode: RunMode;
  services: WorkspaceService[];
  bindings: MiddlewareBinding[];
}

export interface WorkspaceUpdateRequest extends WorkspaceInput {
  expected_revision: number;
}

export interface WorkspaceRecord extends WorkspaceInput {
  id: string;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceListResponse {
  workspaces: WorkspaceRecord[];
}

export interface RepositoryImportRequest { path: string }

export interface RepositoryCloneRequest {
  url: string;
  destination_parent: string;
  directory_name: string | null;
  branch: string | null;
}

export interface RepositoryRecord {
  id: string;
  name: string;
  path: string;
  origin_url: string | null;
  branch: string;
  head_sha: string;
  upstream: string | null;
  dirty: boolean;
  created_at: string;
  updated_at: string;
}

export interface RepositoryListResponse { repositories: RepositoryRecord[] }
export interface WorkspacePlanRequest { expected_revision: number }

export interface PlanIssue {
  code: string;
  title: string;
  detail: string;
  recovery: string;
}

export interface PlanCommand {
  project_id: string;
  project_name: string;
  command_id: string;
  label: string;
  cwd: string;
  argv: string[];
  environment: { name: string; value: string }[];
  long_running: boolean;
}

export type DeploymentProbeSpec =
  | { kind: 'http'; url: string; timeout_seconds: number }
  | { kind: 'tcp'; host: string; port: number; timeout_seconds: number };

export interface DeploymentEnvironmentSnapshot {
  environment: EnvironmentBinding[];
  connection_profiles: ConnectionProfile[];
  bindings: MiddlewareBinding[];
}

export interface ComposeDeploymentPlan {
  revision_id: string;
  workspace_id: string;
  project_id: string;
  workspace_revision: number;
  source_fingerprint: string;
  target_config_fingerprint: string;
  checkout_path: string;
  frozen_compose_path: string;
  services: string[];
  immutable_images: string[];
  wait_timeout_seconds: number;
  probe: DeploymentProbeSpec;
  environment_spec: DeploymentEnvironmentSnapshot;
}

export interface PlanStep {
  id: string;
  kind: PlanStepKind;
  title: string;
  detail: string;
  commands: PlanCommand[];
  deployments: ComposeDeploymentPlan[];
}

export interface ConnectionOutputPreview {
  name: string;
  redacted_value: string;
  sensitive: boolean;
}

export interface ConnectionMappingPreview {
  project_id: string;
  kind: MiddlewareKind;
  resource_id: string;
  resource_name: string;
  outputs: ConnectionOutputPreview[];
}

export interface WorkspacePlanResponse {
  generated_at: string;
  ready: boolean;
  mode: RunMode;
  projects: ProjectSummary[];
  steps: PlanStep[];
  blockers: PlanIssue[];
  warnings: PlanIssue[];
  connection_mappings: ConnectionMappingPreview[];
  plan_id: string | null;
  workspace_id: string | null;
  workspace_revision: number | null;
  config_fingerprint: string | null;
  source_fingerprint: string | null;
}

export interface RunCreateRequest {
  plan_id: string;
  idempotency_key: string;
}

export interface RunRecord {
  id: string;
  workspace_id: string;
  workspace_name: string;
  workspace_revision: number;
  plan_id: string;
  mode: RunMode;
  status: RunStatus;
  current_step: string | null;
  config_fingerprint: string;
  source_fingerprint: string;
  retry_of: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  failure_code: string | null;
  failure_detail: string | null;
}

export interface RunListResponse { runs: RunRecord[] }

export interface RunEvent {
  sequence: number;
  run_id: string;
  created_at: string;
  kind: RunEventKind;
  step_id: string | null;
  project_id: string | null;
  message: string;
}

export interface RunEventListResponse {
  events: RunEvent[];
  next_after: number;
}

export type DeploymentRevisionStatus =
  | 'planned'
  | 'building'
  | 'applying'
  | 'verifying'
  | 'active'
  | 'superseded'
  | 'failed'
  | 'recovering'
  | 'rolled_back'
  | 'degraded';

export interface DeploymentRevisionView {
  revision_id: string;
  workspace_id: string;
  project_id: string;
  workspace_revision: number;
  project_name: string;
  previous_revision_id: string | null;
  status: DeploymentRevisionStatus;
  source_fingerprint: string;
  target_config_fingerprint: string;
  services: string[];
  immutable_images: string[];
  created_at: string;
  updated_at: string;
  failure_code: string | null;
  failure_detail: string | null;
  recovery_detail: string | null;
}

export interface DeploymentRevisionListResponse {
  deployments: DeploymentRevisionView[];
}

export type ManagedResourceStatus = 'planned' | 'provisioning' | 'active' | 'failed' | 'removed';

export interface ManagedPostgresIntent {
  kind: 'postgres';
  image: 'postgres:18';
  host_port: number;
  container_port: 5432;
  username: string;
  database: string;
  password_secret_ref: string;
}

export interface ManagedResourceRecord {
  id: string;
  runtime_id: string | null;
  name: string;
  kind: MiddlewareKind;
  workspace_id: string;
  created_at: string;
  updated_at: string | null;
  status: ManagedResourceStatus;
  intent: ManagedPostgresIntent | null;
  failure_code: string | null;
}

export interface ManagedResourceListResponse {
  resources: ManagedResourceRecord[];
}

export interface ManagedMiddlewareProvisionRequest {
  workspace_id: string;
  kind: 'postgres';
  host_port: number;
  username: string;
  database: string;
  password_secret_ref: string;
}

export interface CleanupItem {
  resource: RuntimeResource;
  eligible: boolean;
  reason_code: string | null;
  reason: string | null;
}

export interface CleanupPreviewResponse {
  id: string;
  generated_at: string;
  runtime_fingerprint: string;
  items: CleanupItem[];
}

export interface CleanupApplyRequest {
  preview_id: string;
  resource_ids: string[];
}

export type CleanupResultStatus = 'removed' | 'skipped' | 'failed';

export interface CleanupResultItem {
  resource_id: string;
  resource_name: string;
  status: CleanupResultStatus;
  reason_code: string | null;
  detail: string | null;
}

export interface CleanupApplyResponse {
  preview_id: string;
  results: CleanupResultItem[];
}

export interface SecretMetadata {
  id: string;
  name: string;
  present: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface SecretListResponse {
  secrets: SecretMetadata[];
}

export interface SecretCreateRequest {
  name: string;
  value: string;
}

export interface SecretUpdateRequest {
  expected_version: number;
  value: string;
}
