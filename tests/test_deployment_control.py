from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pipedeck.compose_deployment import (
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
from pipedeck.contracts import (
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
from pipedeck.deployment_control import (
    DockerDeploymentRuntimeInspector,
    LocalComposeDeploymentRunner,
    SnapshotDeploymentEnvironmentResolver,
    WorkspaceDeploymentExecutor,
)
from pipedeck.planning import ConnectionPlanner


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
        cancellation: DeploymentCancellation | None = None,
        timeout_seconds: float | None = None,
    ) -> DeploymentCommandResult:
        del argv, cwd, environment, cancellation, timeout_seconds
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


class EventCancellation:
    def __init__(self) -> None:
        self.requested = threading.Event()

    def is_cancelled(self) -> bool:
        return self.requested.is_set()


def process_exists(pid: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ("tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until(predicate: Callable[[], bool], timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was not met before timeout")


def test_local_compose_runner_cancel_terminates_real_process_tree(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    script = (
        "import pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid), encoding='utf-8'); "
        "time.sleep(120)"
    )
    cancellation = EventCancellation()
    results: list[DeploymentCommandResult] = []

    worker = threading.Thread(
        target=lambda: results.append(
            LocalComposeDeploymentRunner().run(
                argv=(sys.executable, "-c", script),
                cwd=tmp_path,
                environment=(),
                cancellation=cancellation,
                timeout_seconds=30,
            )
        )
    )
    worker.start()
    try:
        wait_until(child_pid_path.exists)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        assert process_exists(child_pid)

        cancellation.requested.set()
        worker.join(timeout=10)
        wait_until(lambda: not process_exists(child_pid))

        assert not worker.is_alive()
        assert results[0].cancelled is True
        assert results[0].timed_out is False
    finally:
        cancellation.requested.set()
        worker.join(timeout=10)


def test_local_compose_runner_timeout_stops_blocking_command(tmp_path: Path) -> None:
    started = time.monotonic()

    result = LocalComposeDeploymentRunner().run(
        argv=(sys.executable, "-c", "import time; time.sleep(120)"),
        cwd=tmp_path,
        environment=(),
        timeout_seconds=0.2,
    )

    assert result.timed_out is True
    assert result.cancelled is False
    assert time.monotonic() - started < 5


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
    def __init__(self, status: DeploymentStatus = DeploymentStatus.ACTIVE) -> None:
        self.status = status
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
            status=self.status,
            created_at=now,
            updated_at=now,
            failure_code=(
                "DEPLOYMENT_CANCELLED" if self.status is not DeploymentStatus.ACTIVE else None
            ),
            recovery_detail=(
                "rollback complete" if self.status is DeploymentStatus.ROLLED_BACK else None
            ),
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
    assert result.deployment_status is DeploymentStatus.ACTIVE
    assert workflow.intents[0].environment_spec == _environment_spec()


def test_workspace_executor_returns_final_revision_status_for_run_settlement() -> None:
    workflow = FakeWorkflow(DeploymentStatus.ROLLED_BACK)
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

    result = executor.execute(plan, lambda: True)

    assert result.succeeded is False
    assert result.code == "DEPLOYMENT_CANCELLED"
    assert result.deployment_status is DeploymentStatus.ROLLED_BACK
