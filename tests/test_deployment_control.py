from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tripguru_local.compose_deployment import (
    DeploymentAction,
    DeploymentCancellation,
    DeploymentCommandResult,
    DeploymentIntent,
    DeploymentProbe,
    DeploymentProbeResult,
    DeploymentRevision,
    DeploymentStatus,
    ResolvedDeploymentVariable,
    TcpProbe,
)
from tripguru_local.contracts import (
    ComposeDeploymentPlan,
    DeploymentEnvironmentSnapshot,
    EndpointProtocol,
    EnvironmentBinding,
    EnvironmentSource,
    MiddlewareBinding,
    MiddlewareKind,
    PostgresConnectionProfile,
    ResourceHealth,
    RuntimeEndpoint,
    RuntimeResource,
    RuntimeResponse,
    TcpDeploymentProbeSpec,
)
from tripguru_local.deployment_control import (
    DockerDeploymentRuntimeInspector,
    SnapshotDeploymentEnvironmentResolver,
    WorkspaceDeploymentExecutor,
)
from tripguru_local.planning import ConnectionPlanner


class FakeSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def is_readable(self, secret_id: str) -> bool:
        return secret_id in self.values

    def read(self, secret_id: str) -> str:
        return self.values[secret_id]


class FakeRuntime:
    def __init__(self, response: RuntimeResponse) -> None:
        self.response = response
        self.invalidations = 0

    def invalidate(self) -> None:
        self.invalidations += 1

    def snapshot(self) -> RuntimeResponse:
        return self.response


def _environment_spec() -> DeploymentEnvironmentSnapshot:
    return DeploymentEnvironmentSnapshot(
        environment=(
            EnvironmentBinding(
                name="APP_MODE",
                source=EnvironmentSource.LITERAL,
                value="local",
            ),
            EnvironmentBinding(
                name="APP_TOKEN",
                source=EnvironmentSource.SECRET_STORE,
                reference="app-token",
            ),
        ),
        connection_profiles=(
            PostgresConnectionProfile(
                kind=MiddlewareKind.POSTGRES,
                env_var="DATABASE_URL",
                username="supplier",
                database="supplier",
                secret_ref="pg-password",
            ),
        ),
        bindings=(MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg-a"),),
    )


def _intent() -> DeploymentIntent:
    return DeploymentIntent(
        revision_id="revision-1",
        workspace_id="workspace-1",
        target_id="project-1",
        workspace_revision=3,
        source_fingerprint="a" * 64,
        target_config_fingerprint="b" * 64,
        checkout_path=Path("D:/code/project"),
        frozen_compose_path=Path("D:/state/revision-1/compose.json"),
        services=("api",),
        immutable_images=("tripguru.local/api:immutable",),
        wait_timeout_seconds=30,
        probe=TcpProbe("127.0.0.1", 18000),
        environment_spec=_environment_spec(),
    )


def _revision(status: DeploymentStatus = DeploymentStatus.PLANNED) -> DeploymentRevision:
    now = datetime.now(UTC)
    return DeploymentRevision(
        intent=_intent(),
        project_name="tgl-workspace-project",
        previous_revision_id=None,
        status=status,
        created_at=now,
        updated_at=now,
    )


def test_snapshot_environment_resolves_parent_aliases_and_compose_connection_host() -> None:
    secrets = FakeSecrets(
        {
            "app-token": "token value",
            "pg-password": "p@ss word",
        }
    )
    runtime = FakeRuntime(
        RuntimeResponse(
            generated_at=datetime.now(UTC),
            docker_available=True,
            resources=(
                RuntimeResource(
                    id="pg-a",
                    name="pg-a",
                    kind=MiddlewareKind.POSTGRES,
                    image="postgres:18",
                    state="running",
                    status_text="Up (healthy)",
                    health=ResourceHealth.HEALTHY,
                    managed=False,
                    protected=True,
                    ports="127.0.0.1:15432->5432/tcp",
                    endpoints=(
                        RuntimeEndpoint(
                            protocol=EndpointProtocol.TCP,
                            container_port=5432,
                            host_port=15432,
                        ),
                    ),
                ),
            ),
        )
    )
    resolver = SnapshotDeploymentEnvironmentResolver(
        runtime,
        secrets,
        ConnectionPlanner(secrets),
    )

    resolved = resolver.resolve(_revision(), DeploymentAction.APPLY)
    values = {item.name: item.value for item in resolved}
    database = next(item for item in resolved if item.name == "TGL_DATABASE_URL")

    assert values["TGL_APP_MODE"] == "local"
    assert values["TGL_APP_TOKEN"] == "token value"
    assert "@host.docker.internal:15432/" in values["TGL_DATABASE_URL"]
    assert "p@ss word" in database.redaction_values
    assert "p%40ss%20word" in database.redaction_values
    assert runtime.invalidations == 1


class FakeRecordStore:
    def __init__(self, revision: DeploymentRevision | None) -> None:
        self.revision = revision

    def get_revision(self, revision_id: str) -> DeploymentRevision | None:
        if self.revision is not None and self.revision.intent.revision_id == revision_id:
            return self.revision
        return None


class FakeComposeRunner:
    def __init__(self, results: tuple[DeploymentCommandResult, ...]) -> None:
        self.results = list(results)

    def run(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        environment: tuple[ResolvedDeploymentVariable, ...],
    ) -> DeploymentCommandResult:
        del argv, cwd, environment
        return self.results.pop(0)


class FakeEnvironmentResolver:
    def resolve(
        self,
        revision: DeploymentRevision,
        action: DeploymentAction,
    ) -> tuple[ResolvedDeploymentVariable, ...]:
        del revision, action
        return ()


class FakeProbeRunner:
    def verify(
        self,
        probe: DeploymentProbe,
        environment: tuple[ResolvedDeploymentVariable, ...],
    ) -> DeploymentProbeResult:
        del probe, environment
        return DeploymentProbeResult(True)


def test_runtime_inspector_requires_consistent_revision_labels_and_explicit_probe() -> None:
    revision = _revision(DeploymentStatus.ACTIVE)
    inspect_payload = [
        {
            "Config": {
                "Labels": {"tripguru.local/revision": revision.intent.revision_id},
            },
            "State": {"Running": True, "Health": {"Status": "healthy"}},
        }
    ]
    runner = FakeComposeRunner(
        (
            DeploymentCommandResult(0, stdout="container-id\n"),
            DeploymentCommandResult(0, stdout=json.dumps(inspect_payload)),
        )
    )
    inspector = DockerDeploymentRuntimeInspector(
        FakeRecordStore(revision),
        runner,
        FakeEnvironmentResolver(),
        FakeProbeRunner(),
    )

    state = inspector.inspect("workspace-1", "project-1")

    assert state.target_present is True
    assert state.revision_id == "revision-1"
    assert state.probe_ready is True


class FakeWorkflow:
    def __init__(self) -> None:
        self.intents: list[DeploymentIntent] = []

    def deploy(
        self,
        intent: DeploymentIntent,
        cancellation: DeploymentCancellation,
    ) -> DeploymentRevision:
        del cancellation
        self.intents.append(intent)
        now = datetime.now(UTC)
        return DeploymentRevision(
            intent=intent,
            project_name="tgl-workspace-project",
            previous_revision_id=None,
            status=DeploymentStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )


def test_workspace_executor_preserves_frozen_environment_spec() -> None:
    workflow = FakeWorkflow()
    executor = WorkspaceDeploymentExecutor(FakeRecordStore(None), workflow)
    intent = _intent()
    plan = ComposeDeploymentPlan(
        revision_id=intent.revision_id,
        workspace_id=intent.workspace_id,
        project_id=intent.target_id,
        workspace_revision=intent.workspace_revision,
        source_fingerprint=intent.source_fingerprint,
        target_config_fingerprint=intent.target_config_fingerprint,
        checkout_path=str(intent.checkout_path),
        frozen_compose_path=str(intent.frozen_compose_path),
        services=intent.services,
        immutable_images=intent.immutable_images,
        wait_timeout_seconds=intent.wait_timeout_seconds,
        probe=TcpDeploymentProbeSpec(
            host="127.0.0.1",
            port=18000,
            timeout_seconds=10,
        ),
        environment_spec=intent.environment_spec,
    )

    result = executor.execute(plan, lambda: False)

    assert result.succeeded is True
    assert workflow.intents[0].environment_spec == _environment_spec()
