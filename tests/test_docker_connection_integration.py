from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from tripguru_local.contracts import (
    MiddlewareBinding,
    MiddlewareKind,
    PlanCommand,
    PostgresConnectionProfile,
    RunMode,
    WorkspaceInput,
    WorkspacePlanResponse,
    WorkspaceService,
    WorkspaceUpdateRequest,
)
from tripguru_local.control_plane import (
    SecretService,
    WindowsCredentialStore,
    WorkspaceEnvironmentResolver,
)
from tripguru_local.planning import ConnectionPlanner
from tripguru_local.processes import SubprocessRunner
from tripguru_local.runtime import DockerRuntime
from tripguru_local.state_store import StateStore


def _docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("docker", *arguments),
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _start_postgres(name: str, sentinel: str, psql: str) -> tuple[str, int]:
    password = "tripguru-binding-password"
    container_id = _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "--label",
        "tripguru.local/acceptance=connection-switch",
        "--tmpfs",
        "/var/lib/postgresql",
        "--health-cmd",
        "pg_isready -U tripguru -d tripguru",
        "--health-interval",
        "1s",
        "--health-timeout",
        "2s",
        "--health-retries",
        "30",
        "-e",
        "POSTGRES_USER=tripguru",
        "-e",
        f"POSTGRES_PASSWORD={password}",
        "-e",
        "POSTGRES_DB=tripguru",
        "-p",
        "127.0.0.1::5432",
        "postgres:18",
    ).stdout.strip()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = _docker(
            "inspect",
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            container_id,
            check=False,
        ).stdout.strip()
        if status == "healthy":
            break
        time.sleep(0.5)
    else:
        raise AssertionError(f"PostgreSQL acceptance container did not become healthy: {name}")

    port = int(_docker("port", container_id, "5432/tcp").stdout.strip().rsplit(":", maxsplit=1)[1])
    environment = os.environ.copy()
    environment["PGPASSWORD"] = password
    subprocess.run(
        (
            psql,
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            "tripguru",
            "-d",
            "tripguru",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "CREATE TABLE binding_sentinel(value text NOT NULL); "
            f"INSERT INTO binding_sentinel VALUES ('{sentinel}');",
        ),
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return container_id, port


def _probe_with_resolved_environment(
    psql: str,
    resolver: WorkspaceEnvironmentResolver,
    plan: WorkspacePlanResponse,
    command: PlanCommand,
) -> str:
    environment = os.environ.copy()
    for variable in resolver.resolve(plan, command):
        environment[variable.name] = variable.value
    completed = subprocess.run(
        (
            psql,
            "-t",
            "-A",
            "-c",
            "SELECT value FROM binding_sentinel",
            environment["DATABASE_URL"],
        ),
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_same_host_command_connects_to_selected_postgres_binding(tmp_path: Path) -> None:
    psql = shutil.which("psql")
    if psql is None or _docker("info", check=False).returncode != 0:
        pytest.skip("Docker and psql are required for the real binding acceptance")

    suffix = uuid4().hex[:10]
    container_ids: list[str] = []
    store = StateStore(tmp_path / "state.db")
    secrets = SecretService(store, WindowsCredentialStore())
    workspace_id: str | None = None
    secret_id: str | None = None
    try:
        postgres_a, _ = _start_postgres(f"tgl-binding-a-{suffix}", "database-a", psql)
        container_ids.append(postgres_a)
        postgres_b, _ = _start_postgres(f"tgl-binding-b-{suffix}", "database-b", psql)
        container_ids.append(postgres_b)
        runtime = DockerRuntime(SubprocessRunner())
        snapshot = runtime.snapshot()
        resource_a = next(
            resource for resource in snapshot.resources if resource.id == postgres_a[:12]
        )
        resource_b = next(
            resource for resource in snapshot.resources if resource.id == postgres_b[:12]
        )

        secret = secrets.create("PostgreSQL acceptance password", "tripguru-binding-password")
        secret_id = secret.id
        service = WorkspaceService(
            project_id="binding-probe",
            connection_profiles=(
                PostgresConnectionProfile(
                    kind=MiddlewareKind.POSTGRES,
                    env_var="DATABASE_URL",
                    username="tripguru",
                    database="tripguru",
                    secret_ref=secret.id,
                ),
            ),
        )
        workspace = store.create_workspace(
            WorkspaceInput(
                name="PostgreSQL binding acceptance",
                mode=RunMode.DEVELOPMENT,
                services=(service,),
                bindings=(
                    MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id=resource_a.id),
                ),
            )
        )
        workspace_id = workspace.id
        resolver = WorkspaceEnvironmentResolver(
            store,
            runtime,
            secrets,
            ConnectionPlanner(secrets),
        )
        command = PlanCommand(
            project_id="binding-probe",
            project_name="Binding probe",
            command_id="probe",
            label="Probe selected PostgreSQL",
            cwd=str(tmp_path),
            argv=(psql,),
        )
        base_plan = WorkspacePlanResponse(
            generated_at=datetime.now(UTC),
            ready=True,
            mode=RunMode.DEVELOPMENT,
            projects=(),
            steps=(),
            blockers=(),
            warnings=(),
            plan_id="binding-plan-a",
            workspace_id=workspace.id,
            workspace_revision=workspace.revision,
            config_fingerprint="1" * 64,
            source_fingerprint="2" * 64,
        )
        assert _probe_with_resolved_environment(psql, resolver, base_plan, command) == "database-a"

        updated = store.update_workspace(
            workspace.id,
            WorkspaceUpdateRequest(
                expected_revision=workspace.revision,
                name=workspace.name,
                mode=workspace.mode,
                services=workspace.services,
                bindings=(
                    MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id=resource_b.id),
                ),
            ),
        )
        second_plan = base_plan.model_copy(
            update={"plan_id": "binding-plan-b", "workspace_revision": updated.revision}
        )
        assert (
            _probe_with_resolved_environment(psql, resolver, second_plan, command) == "database-b"
        )
    finally:
        if workspace_id is not None:
            current = store.get_workspace(workspace_id)
            if current is not None:
                store.delete_workspace(workspace_id, current.revision)
        if secret_id is not None:
            metadata = store.get_secret_metadata(secret_id)
            if metadata is not None:
                secrets.delete(secret_id, metadata.version)
        store.close()
        for container_id in container_ids:
            _docker("container", "rm", "--force", container_id, check=False)
