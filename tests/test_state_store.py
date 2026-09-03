import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipedeck.contracts import (
    CommandOwnedPortInjection,
    EnvironmentBinding,
    EnvironmentSource,
    HostEndpoint,
    HostTarget,
    MiddlewareKind,
    MinioConnectionProfile,
    PlanCommand,
    PlanStep,
    PlanStepKind,
    PostgresConnectionProfile,
    ProjectCommand,
    ProjectKind,
    ProjectSummary,
    RepositoryRecord,
    RunEventKind,
    RunMode,
    RunRecord,
    RunStatus,
    SecretMetadata,
    ServiceCommand,
    TcpReadiness,
    WorkspaceInput,
    WorkspacePlanResponse,
    WorkspaceRecord,
    WorkspaceService,
    WorkspaceUpdateRequest,
)
from pipedeck.managed_middleware import ManagedMiddlewareIntent
from pipedeck.state_store import (
    ManagedResourceRecord,
    ManagedResourceRuntimeConflictError,
    PlanConflictError,
    RepositoryPathConflictError,
    RunTransitionError,
    SecretInUseError,
    StateStore,
    WorkspaceInUseError,
    WorkspaceRevisionConflictError,
)


def _workspace_input(name: str = "Supplier local") -> WorkspaceInput:
    return WorkspaceInput(
        name=name,
        mode=RunMode.DEVELOPMENT,
        services=(
            WorkspaceService(
                project_id="supplier-backend",
                commands=(
                    ServiceCommand(
                        id="start",
                        label="启动后端",
                        kind=PlanStepKind.START,
                        argv=("uv", "run", "uvicorn", "app.main:app", "--port", "8000"),
                        long_running=True,
                    ),
                ),
                environment=(
                    EnvironmentBinding(
                        name="SUPPLIER_DATABASE_URL",
                        source=EnvironmentSource.HOST_ENV,
                        reference="SUPPLIER_LOCAL_DATABASE_URL",
                    ),
                ),
                execution_target=HostTarget(
                    endpoints=(
                        HostEndpoint(
                            name="api",
                            host_port=8000,
                            injection=CommandOwnedPortInjection(kind="command-owned"),
                        ),
                    ),
                    readiness=TcpReadiness(kind="tcp", endpoint="api"),
                ),
            ),
        ),
    )


def _plan(workspace_id: str, revision: int, plan_id: str = "plan-1") -> WorkspacePlanResponse:
    project = ProjectSummary(
        id="supplier-backend",
        name="supplier-platform",
        path="D:/code/supplier-backend-v2",
        kind=ProjectKind.PYTHON_UV,
        branch="main",
        dirty=False,
        commands=(
            ProjectCommand(
                id="start",
                label="启动后端",
                argv=("uv", "run", "uvicorn", "app.main:app"),
                long_running=True,
            ),
        ),
        requirements=(),
        warnings=(),
    )
    command = PlanCommand(
        project_id=project.id,
        project_name=project.name,
        command_id="start",
        label="启动后端",
        cwd=project.path,
        argv=("uv", "run", "uvicorn", "app.main:app"),
        long_running=True,
    )
    return WorkspacePlanResponse(
        generated_at=datetime.now(UTC),
        ready=True,
        mode=RunMode.DEVELOPMENT,
        projects=(project,),
        steps=(
            PlanStep(
                id="start",
                kind=PlanStepKind.START,
                title="启动项目",
                detail="执行已声明命令",
                commands=(command,),
            ),
        ),
        blockers=(),
        warnings=(),
        plan_id=plan_id,
        workspace_id=workspace_id,
        workspace_revision=revision,
        config_fingerprint="config-1",
        source_fingerprint="source-1",
    )


def _run(
    workspace_id: str,
    revision: int,
    *,
    run_id: str = "run-1",
    status: RunStatus = RunStatus.QUEUED,
    plan_id: str = "plan-1",
    created_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> RunRecord:
    return RunRecord(
        id=run_id,
        workspace_id=workspace_id,
        workspace_name="Supplier local",
        workspace_revision=revision,
        plan_id=plan_id,
        mode=RunMode.DEVELOPMENT,
        status=status,
        current_step="start" if status is RunStatus.RUNNING else None,
        config_fingerprint="config-1",
        source_fingerprint="source-1",
        retry_of=None,
        created_at=created_at or datetime.now(UTC),
        started_at=started_at,
        finished_at=finished_at,
        failure_code=None,
        failure_detail=None,
    )


def test_workspace_revision_crud_and_restart_persistence(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    store = StateStore(database)
    created = store.create_workspace(_workspace_input(), workspace_id="workspace-1")

    updated = store.update_workspace(
        created.id,
        WorkspaceUpdateRequest(
            expected_revision=1,
            name="Supplier integration",
            mode=RunMode.INTEGRATED,
            services=created.services,
            bindings=created.bindings,
        ),
    )

    assert updated.revision == 2
    assert updated.created_at == created.created_at
    with pytest.raises(WorkspaceRevisionConflictError) as error:
        store.update_workspace(
            created.id,
            WorkspaceUpdateRequest(
                expected_revision=1,
                name="stale",
                mode=RunMode.DEVELOPMENT,
                services=created.services,
                bindings=created.bindings,
            ),
        )
    assert error.value.code == "WORKSPACE_REVISION_CONFLICT"
    store.close()

    reopened = StateStore(database)
    assert reopened.get_workspace(created.id) == updated
    assert reopened.list_workspaces() == (updated,)
    reopened.delete_workspace(created.id, expected_revision=2)
    assert reopened.get_workspace(created.id) is None
    reopened.close()


def test_repository_upsert_uses_canonical_path_identity(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    now = datetime.now(UTC)
    store = StateStore(tmp_path / "state.db")
    first = RepositoryRecord(
        id="repo-1",
        name="repo",
        path=str(repository),
        origin_url=None,
        branch="main",
        head_sha="a" * 40,
        upstream=None,
        dirty=False,
        created_at=now,
        updated_at=now,
    )
    store.upsert_repository(first)

    assert store.get_repository("repo-1") == first
    assert store.get_repository_by_path(repository / ".") == first
    assert store.list_repositories() == (first,)

    conflicting = RepositoryRecord(
        id="repo-2",
        name="repo",
        path=str(repository.resolve()),
        origin_url=None,
        branch="main",
        head_sha="b" * 40,
        upstream=None,
        dirty=False,
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(RepositoryPathConflictError):
        store.upsert_repository(conflicting)
    store.close()


def test_managed_resource_requires_local_identity_and_runtime_match(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    workspace = store.create_workspace(_workspace_input(), workspace_id="workspace-1")
    record = ManagedResourceRecord(
        id="managed-postgres-1",
        runtime_id="container-abc",
        name="supplier-postgres",
        kind=MiddlewareKind.POSTGRES,
        workspace_id=workspace.id,
        created_at=datetime.now(UTC),
        intent=ManagedMiddlewareIntent(
            kind=MiddlewareKind.POSTGRES,
            host_port=45432,
            password_secret_ref="postgres-password",
        ),
    )

    stored = store.upsert_managed_resource(record)
    runtime_id = record.runtime_id
    assert runtime_id is not None

    assert store.get_managed_resource(record.id) == stored
    assert store.get_managed_resource_by_runtime_id(runtime_id) == stored
    assert store.list_managed_resources(workspace.id) == (stored,)
    assert store.matches_managed_resource(
        resource_id=record.id,
        runtime_id=runtime_id,
        kind=MiddlewareKind.POSTGRES,
    )
    assert not store.matches_managed_resource(
        resource_id=record.id,
        runtime_id="different-container",
        kind=MiddlewareKind.POSTGRES,
    )
    assert not store.matches_managed_resource(
        resource_id=record.id,
        runtime_id=runtime_id,
        kind=MiddlewareKind.REDIS,
    )

    conflict = ManagedResourceRecord(
        id="managed-postgres-2",
        runtime_id=record.runtime_id,
        name="another-postgres",
        kind=MiddlewareKind.POSTGRES,
        workspace_id=workspace.id,
        created_at=datetime.now(UTC),
        intent=record.intent,
    )
    with pytest.raises(ManagedResourceRuntimeConflictError):
        store.upsert_managed_resource(conflict)
    store.close()


def test_plan_run_idempotency_events_and_transitions(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    workspace = store.create_workspace(_workspace_input(), workspace_id="workspace-1")
    plan = store.save_plan(_plan(workspace.id, workspace.revision))
    created_at = datetime.now(UTC)
    first = store.create_run(
        _run(workspace.id, workspace.revision, created_at=created_at),
        "request-key-1",
    )
    duplicate = store.create_run(
        _run(
            workspace.id,
            workspace.revision,
            run_id="run-duplicate",
            created_at=created_at,
        ),
        "request-key-1",
    )

    assert duplicate == first
    assert store.get_plan(plan.plan_id or "") == plan
    assert store.get_run(first.id) == first
    assert store.list_runs(workspace.id) == (first,)

    event_one = store.append_event(
        run_id=first.id,
        kind=RunEventKind.STATUS,
        message="Run queued",
    )
    event_two = store.append_event(
        run_id=first.id,
        kind=RunEventKind.STDOUT,
        step_id="start",
        project_id="supplier-backend",
        message="server ready",
    )
    assert (event_one.sequence, event_two.sequence) == (1, 2)
    assert store.list_events(first.id, after=1).events == (event_two,)
    assert store.list_events(first.id, after=2).next_after == 2

    running = _run(
        workspace.id,
        workspace.revision,
        status=RunStatus.RUNNING,
        created_at=first.created_at,
        started_at=datetime.now(UTC),
    )
    assert store.update_run(running, expected_status=RunStatus.QUEUED) == running
    succeeded = _run(
        workspace.id,
        workspace.revision,
        status=RunStatus.SUCCEEDED,
        created_at=first.created_at,
        started_at=running.started_at,
        finished_at=datetime.now(UTC),
    )
    assert store.update_run(succeeded, expected_status=RunStatus.RUNNING) == succeeded
    with pytest.raises(RunTransitionError):
        store.update_run(running, expected_status=RunStatus.SUCCEEDED)
    store.close()


def test_plan_is_immutable_and_workspace_with_history_cannot_be_deleted(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.db")
    workspace = store.create_workspace(_workspace_input(), workspace_id="workspace-1")
    original = _plan(workspace.id, workspace.revision)
    store.save_plan(original)
    changed = WorkspacePlanResponse(
        generated_at=original.generated_at,
        ready=False,
        mode=original.mode,
        projects=original.projects,
        steps=original.steps,
        blockers=original.blockers,
        warnings=original.warnings,
        plan_id=original.plan_id,
        workspace_id=original.workspace_id,
        workspace_revision=original.workspace_revision,
        config_fingerprint=original.config_fingerprint,
        source_fingerprint=original.source_fingerprint,
    )

    with pytest.raises(PlanConflictError):
        store.save_plan(changed)
    with pytest.raises(WorkspaceInUseError) as error:
        store.delete_workspace(workspace.id)
    assert error.value.code == "WORKSPACE_IN_USE"
    store.close()


def test_reopening_store_interrupts_active_runs_and_appends_event(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    store = StateStore(database)
    workspace = store.create_workspace(_workspace_input(), workspace_id="workspace-1")
    store.save_plan(_plan(workspace.id, workspace.revision))
    run = store.create_run(_run(workspace.id, workspace.revision), "request-key-1")
    store.close()

    reopened = StateStore(database)
    interrupted = reopened.get_run(run.id)
    assert interrupted is not None
    assert interrupted.status is RunStatus.INTERRUPTED
    assert interrupted.finished_at is not None
    assert interrupted.failure_code == "SIDECAR_RESTARTED"
    events = reopened.list_events(run.id)
    assert len(events.events) == 1
    assert events.events[0].kind is RunEventKind.STATUS
    assert "interrupted" in events.events[0].message
    reopened.close()


def test_workspace_stores_host_environment_reference_without_secret_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_value = "postgresql://user:password@127.0.0.1/supplier"
    monkeypatch.setenv("SUPPLIER_LOCAL_DATABASE_URL", secret_value)
    database = tmp_path / "state.db"
    store = StateStore(database)
    workspace = store.create_workspace(_workspace_input(), workspace_id="workspace-1")

    assert workspace.services[0].environment[0].reference == "SUPPLIER_LOCAL_DATABASE_URL"
    store.close()
    persisted_bytes = b"".join(
        path.read_bytes()
        for path in (database, database.with_name(f"{database.name}-wal"))
        if path.exists()
    )
    assert secret_value.encode() not in persisted_bytes


def test_invalid_json_fails_pydantic_read_boundary(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    store = StateStore(database)
    workspace = store.create_workspace(_workspace_input(), workspace_id="workspace-1")
    store.close()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE workspaces SET payload = ? WHERE id = ?", ("{}", workspace.id))

    with pytest.raises(ValidationError):
        StateStore(database).get_workspace(workspace.id)


def test_secret_reference_users_cover_environment_postgres_and_minio_profiles(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    store = StateStore(tmp_path / "state.db")
    service = WorkspaceService(
        project_id="supplier-backend",
        environment=(
            EnvironmentBinding(
                name="APP_SECRET",
                source=EnvironmentSource.SECRET_STORE,
                reference="direct-secret",
            ),
        ),
        connection_profiles=(
            PostgresConnectionProfile(
                kind=MiddlewareKind.POSTGRES,
                env_var="SUPPLIER_DATABASE_URL",
                username="supplier",
                database="supplier",
                secret_ref="postgres-secret",
            ),
            MinioConnectionProfile(
                kind=MiddlewareKind.MINIO,
                endpoint_env="SUPPLIER_S3_ENDPOINT",
                access_key_env="SUPPLIER_S3_ACCESS_KEY",
                secret_key_env="SUPPLIER_S3_SECRET_KEY",
                bucket_env="SUPPLIER_S3_BUCKET",
                bucket="local-assets",
                access_key_secret_ref="minio-access",
                secret_key_secret_ref="minio-secret",
            ),
        ),
    )
    workspace = store.create_workspace(
        WorkspaceInput(
            name="All secret consumers",
            mode=RunMode.DEVELOPMENT,
            services=(service,),
        ),
        workspace_id="workspace-secrets",
    )
    for secret_id in (
        "direct-secret",
        "postgres-secret",
        "minio-access",
        "minio-secret",
    ):
        store.create_secret_metadata(
            SecretMetadata(
                id=secret_id,
                name=secret_id,
                present=True,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        assert store.secret_reference_users(secret_id) == (workspace.id,)
        with pytest.raises(SecretInUseError) as error:
            store.delete_secret_metadata(secret_id, expected_version=1)
        assert error.value.code == "SECRET_IN_USE"
    store.close()


def test_v2_schema_migrates_ports_to_host_execution_target(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    now = datetime.now(UTC)
    current_workspace = WorkspaceRecord(
        id="workspace-v1",
        revision=1,
        created_at=now,
        updated_at=now,
        **_workspace_input().model_dump(),
    )
    old_payload = json.loads(current_workspace.model_dump_json())
    for service in old_payload["services"]:
        target = service.pop("execution_target")
        service["ports"] = [endpoint["host_port"] for endpoint in target["endpoints"]]
    current_plan = _plan(current_workspace.id, current_workspace.revision)
    old_plan_payload = json.loads(current_plan.model_dump_json())
    for project in old_plan_payload["projects"]:
        project.pop("container_capabilities")
    for step in old_plan_payload["steps"]:
        for command in step["commands"]:
            command.pop("environment")
    current_run = _run(
        current_workspace.id,
        current_workspace.revision,
        status=RunStatus.SUCCEEDED,
        finished_at=now,
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE plans (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                workspace_revision INTEGER NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE run_events (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence)
            );
            CREATE TABLE secrets (
                id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                payload TEXT NOT NULL
            );
            PRAGMA user_version = 2;
            """
        )
        connection.execute(
            "INSERT INTO workspaces (id, revision, payload) VALUES (?, ?, ?)",
            (current_workspace.id, current_workspace.revision, json.dumps(old_payload)),
        )
        connection.execute(
            """
            INSERT INTO plans (id, workspace_id, workspace_revision, payload)
            VALUES (?, ?, ?, ?)
            """,
            (
                current_plan.plan_id,
                current_workspace.id,
                current_workspace.revision,
                json.dumps(old_plan_payload),
            ),
        )
        connection.execute(
            "INSERT INTO runs (id, status, payload) VALUES (?, ?, ?)",
            (current_run.id, current_run.status.value, current_run.model_dump_json()),
        )

    store = StateStore(database)
    migrated = store.get_workspace(current_workspace.id)

    assert migrated is not None
    assert migrated.services[0].connection_profiles == ()
    target = migrated.services[0].execution_target
    assert isinstance(target, HostTarget)
    assert target.readiness is None
    assert target.endpoints[0].name == "port-8000"
    assert target.endpoints[0].host_port == 8000
    assert isinstance(target.endpoints[0].injection, CommandOwnedPortInjection)
    assert store.get_plan(current_plan.plan_id or "") == current_plan
    assert store.get_run(current_run.id) == current_run
    store.close()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (6,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'secrets'"
        ).fetchone() == ("secrets",)
