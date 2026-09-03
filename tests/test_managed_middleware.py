from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipedeck.contracts import MiddlewareKind, RunMode, WorkspaceInput, WorkspaceService
from pipedeck.managed_middleware import (
    DockerCliManagedMiddleware,
    DockerCommandResult,
    DockerContainerInspection,
    DockerEnvironmentVariable,
    DockerLabel,
    DockerPublishedPort,
    ManagedMiddlewareIntent,
    ManagedMiddlewareNotFoundError,
    ManagedMiddlewareOwnershipError,
    ManagedMiddlewareProvisionRequest,
    ManagedMiddlewareService,
    ManagedResourceRecord,
    ManagedResourceStatus,
    UnsupportedManagedMiddlewareKindError,
    derive_managed_postgres_name,
    derive_managed_resource_id,
)
from pipedeck.state_store import ManagedResourceIntentConflictError, StateStore

RUNTIME_ID = "a" * 64
PASSWORD = "do-not-persist-this-password"


class FakeSecrets:
    def __init__(self, value: str = PASSWORD) -> None:
        self.value = value
        self.reads: list[str] = []

    def read(self, secret_id: str) -> str:
        self.reads.append(secret_id)
        return self.value


class FakeDocker:
    def __init__(
        self,
        store: StateStore,
        *,
        port_available: bool = True,
        health: str | None = "healthy",
        running: bool = True,
    ) -> None:
        self.store = store
        self.port_available = port_available
        self.health = health
        self.running = running
        self.runtime_id: str | None = RUNTIME_ID
        self.publish_inspection = True
        self.inspections: dict[str, DockerContainerInspection] = {}
        self.calls: list[tuple[str, str]] = []
        self.passwords: list[str] = []
        self.removed: list[str] = []

    def host_port_available(self, host_port: int) -> bool:
        self.calls.append(("port", str(host_port)))
        records = self.store.list_managed_resources()
        assert records[-1].status is ManagedResourceStatus.PROVISIONING
        return self.port_available

    def run_postgres(self, record: ManagedResourceRecord, password: str) -> str | None:
        persisted = self.store.get_managed_resource(record.id)
        assert persisted is not None
        assert persisted.status is ManagedResourceStatus.PROVISIONING
        self.calls.append(("run", record.name))
        self.passwords.append(password)
        if self.runtime_id is None:
            return None
        if self.publish_inspection:
            inspection = owned_inspection(
                record,
                runtime_id=self.runtime_id,
                health=self.health,
                running=self.running,
            )
            self.inspections[record.name] = inspection
            self.inspections[self.runtime_id] = inspection
        return self.runtime_id

    def inspect(self, identifier: str) -> DockerContainerInspection | None:
        self.calls.append(("inspect", identifier))
        return self.inspections.get(identifier)

    def remove(self, runtime_id: str) -> bool:
        self.calls.append(("remove", runtime_id))
        self.removed.append(runtime_id)
        return True


@dataclass
class FakeCommandRunner:
    results: list[DockerCommandResult]

    def __post_init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], tuple[DockerEnvironmentVariable, ...]]] = []

    def run(
        self,
        argv: tuple[str, ...],
        environment: tuple[DockerEnvironmentVariable, ...] = (),
    ) -> DockerCommandResult:
        self.calls.append((argv, environment))
        return self.results.pop(0)


def workspace_input() -> WorkspaceInput:
    return WorkspaceInput(
        name="Managed middleware",
        mode=RunMode.INTEGRATED,
        services=(WorkspaceService(project_id="supplier-api"),),
    )


def provision_request(
    *,
    kind: MiddlewareKind = MiddlewareKind.POSTGRES,
    host_port: int = 45432,
) -> ManagedMiddlewareProvisionRequest:
    return ManagedMiddlewareProvisionRequest(
        workspace_id="workspace-1",
        kind=kind,
        host_port=host_port,
        username="supplier",
        database="supplier_local",
        password_secret_ref="postgres-password",
    )


def owned_inspection(
    record: ManagedResourceRecord,
    *,
    runtime_id: str = RUNTIME_ID,
    health: str | None = "healthy",
    running: bool = True,
    managed_label: str = "true",
) -> DockerContainerInspection:
    assert record.intent is not None
    return DockerContainerInspection(
        runtime_id=runtime_id,
        name=record.name,
        image=record.intent.image,
        running=running,
        health=health,
        labels=(
            DockerLabel("tripguru.local/managed", managed_label),
            DockerLabel("tripguru.local/workspace", record.workspace_id),
            DockerLabel("tripguru.local/kind", record.kind.value),
            DockerLabel("tripguru.local/resource", record.id),
        ),
        published_ports=(
            DockerPublishedPort(
                host_ip="127.0.0.1",
                host_port=record.intent.host_port,
                container_port=record.intent.container_port,
                protocol="tcp",
            ),
        ),
    )


def service_fixture(
    tmp_path: Path,
    *,
    port_available: bool = True,
    health: str | None = "healthy",
    running: bool = True,
) -> tuple[StateStore, FakeDocker, FakeSecrets, ManagedMiddlewareService]:
    store = StateStore(tmp_path / "state.db")
    store.create_workspace(workspace_input(), workspace_id="workspace-1")
    docker = FakeDocker(
        store,
        port_available=port_available,
        health=health,
        running=running,
    )
    secrets = FakeSecrets()
    return store, docker, secrets, ManagedMiddlewareService(store, docker, secrets)


def test_provision_persists_intent_before_docker_and_only_activates_healthy(
    tmp_path: Path,
) -> None:
    store, docker, secrets, service = service_fixture(tmp_path)

    active = service.provision(provision_request())

    assert active.status is ManagedResourceStatus.ACTIVE
    assert active.runtime_id == RUNTIME_ID
    assert active.name == derive_managed_postgres_name("workspace-1")
    assert active.id == derive_managed_resource_id("workspace-1", MiddlewareKind.POSTGRES)
    assert active.intent is not None
    assert active.intent.image == "postgres:18"
    assert active.intent.password_secret_ref == "postgres-password"
    assert secrets.reads == ["postgres-password"]
    assert docker.passwords == [PASSWORD]
    assert docker.calls[:2] == [("inspect", active.name), ("port", "45432")]
    assert active.runtime_id is not None
    assert store.get_managed_resource_by_runtime_id(active.runtime_id[:12]) == active
    assert store.matches_managed_resource(
        resource_id=active.id,
        runtime_id=active.runtime_id[:12],
        kind=MiddlewareKind.POSTGRES,
    )

    store.close()
    assert PASSWORD not in (tmp_path / "state.db").read_bytes().decode("utf-8", errors="ignore")
    reopened = StateStore(tmp_path / "state.db")
    assert reopened.get_managed_resource(active.id) == active
    reopened.close()


def test_port_conflict_is_persisted_and_blocks_secret_and_docker_run(tmp_path: Path) -> None:
    store, docker, secrets, service = service_fixture(tmp_path, port_available=False)

    failed = service.provision(provision_request(host_port=45433))

    assert failed.status is ManagedResourceStatus.FAILED
    assert failed.failure_code == "MANAGED_HOST_PORT_IN_USE"
    assert failed.runtime_id is None
    assert secrets.reads == []
    assert all(action != "run" for action, _ in docker.calls)
    store.close()


def test_starting_container_stays_provisioning_until_reconcile_is_healthy(
    tmp_path: Path,
) -> None:
    store, docker, _, service = service_fixture(tmp_path, health="starting")

    provisioning = service.provision(provision_request())

    assert provisioning.status is ManagedResourceStatus.PROVISIONING
    assert provisioning.runtime_id == RUNTIME_ID
    healthy = owned_inspection(provisioning)
    docker.inspections[RUNTIME_ID] = healthy
    docker.inspections[provisioning.name] = healthy

    active = service.reconcile(provisioning.id)

    assert active.status is ManagedResourceStatus.ACTIVE
    assert [action for action, _ in docker.calls].count("run") == 1
    store.close()


def test_reconcile_recovers_run_with_known_runtime_after_initial_inspect_failure(
    tmp_path: Path,
) -> None:
    store, docker, _, service = service_fixture(tmp_path)
    docker.publish_inspection = False

    failed = service.provision(provision_request())

    assert failed.status is ManagedResourceStatus.FAILED
    assert failed.runtime_id == RUNTIME_ID
    healthy = owned_inspection(failed)
    docker.inspections[RUNTIME_ID] = healthy

    active = service.reconcile(failed.id)

    assert active.status is ManagedResourceStatus.ACTIVE
    assert [action for action, _ in docker.calls].count("run") == 1
    store.close()


def test_external_name_collision_is_never_adopted_or_removed(tmp_path: Path) -> None:
    store, docker, _, service = service_fixture(tmp_path)
    request = provision_request()
    resource_id = derive_managed_resource_id(request.workspace_id, request.kind)
    docker.runtime_id = None
    external = DockerContainerInspection(
        runtime_id="b" * 64,
        name=derive_managed_postgres_name(request.workspace_id),
        image="postgres:18",
        running=True,
        health="healthy",
        labels=(),
        published_ports=(DockerPublishedPort("127.0.0.1", request.host_port, 5432, "tcp"),),
    )
    docker.inspections[external.name] = external

    failed = service.provision(request)

    assert failed.id == resource_id
    assert failed.status is ManagedResourceStatus.FAILED
    assert failed.failure_code == "MANAGED_OWNERSHIP_MISMATCH"
    assert failed.runtime_id is None
    with pytest.raises(ManagedMiddlewareOwnershipError):
        service.delete(failed.id)
    assert docker.removed == []
    store.close()


def test_minio_and_unknown_records_are_rejected_before_any_docker_mutation(
    tmp_path: Path,
) -> None:
    store, docker, _, service = service_fixture(tmp_path)

    with pytest.raises(UnsupportedManagedMiddlewareKindError):
        service.provision(provision_request(kind=MiddlewareKind.MINIO))
    with pytest.raises(ManagedMiddlewareNotFoundError):
        service.delete("external-postgres")

    minio = ManagedResourceRecord(
        id="managed-minio",
        runtime_id="c" * 64,
        name="external-minio",
        kind=MiddlewareKind.MINIO,
        workspace_id="workspace-1",
        created_at=datetime.now(UTC),
    )
    store.upsert_managed_resource(minio)
    with pytest.raises(UnsupportedManagedMiddlewareKindError):
        service.delete(minio.id)

    assert docker.calls == []
    store.close()


def test_delete_revalidates_ownership_and_removes_only_exact_runtime_id(tmp_path: Path) -> None:
    store, docker, _, service = service_fixture(tmp_path)
    active = service.provision(provision_request())

    removed = service.delete(active.id)

    assert removed.status is ManagedResourceStatus.REMOVED
    assert removed.runtime_id == RUNTIME_ID
    assert docker.removed == [RUNTIME_ID]
    assert (
        store.matches_managed_resource(
            resource_id=active.id,
            runtime_id=RUNTIME_ID,
            kind=MiddlewareKind.POSTGRES,
        )
        is False
    )
    assert service.delete(active.id) == removed
    assert docker.removed == [RUNTIME_ID]
    store.close()


def test_active_managed_intent_blocks_secret_deletion_until_removed(tmp_path: Path) -> None:
    store, _, _, service = service_fixture(tmp_path)

    active = service.provision(provision_request())

    assert store.secret_reference_users("postgres-password") == ("workspace-1",)
    service.delete(active.id)
    assert store.secret_reference_users("postgres-password") == ()
    store.close()


def test_docker_cli_uses_labels_child_environment_and_typed_inspect() -> None:
    inspection_json = json.dumps(
        [
            {
                "Id": RUNTIME_ID,
                "Name": "/pipedeck-pg-workspace-1",
                "Config": {
                    "Image": "postgres:18",
                    "Labels": {
                        "tripguru.local/managed": "true",
                        "tripguru.local/workspace": "workspace-1",
                        "tripguru.local/kind": "postgres",
                        "tripguru.local/resource": "managed-postgres",
                    },
                },
                "State": {"Running": True, "Health": {"Status": "healthy"}},
                "HostConfig": {
                    "PortBindings": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "45432"}]}
                },
            }
        ]
    )
    runner = FakeCommandRunner(
        [
            DockerCommandResult(0, RUNTIME_ID, ""),
            DockerCommandResult(0, inspection_json, ""),
            DockerCommandResult(0, RUNTIME_ID, ""),
        ]
    )
    docker = DockerCliManagedMiddleware(runner)
    record = ManagedResourceRecord(
        id="managed-postgres",
        name="pipedeck-pg-workspace-1",
        kind=MiddlewareKind.POSTGRES,
        workspace_id="workspace-1",
        created_at=datetime.now(UTC),
        status=ManagedResourceStatus.PROVISIONING,
        intent=ManagedMiddlewareIntent(
            kind=MiddlewareKind.POSTGRES,
            host_port=45432,
            username="supplier",
            database="supplier_local",
            password_secret_ref="postgres-password",
        ),
    )

    runtime_id = docker.run_postgres(record, PASSWORD)
    inspection = docker.inspect(RUNTIME_ID)
    removed = docker.remove(RUNTIME_ID)

    assert runtime_id == RUNTIME_ID
    run_argv, environment = runner.calls[0]
    assert PASSWORD not in run_argv
    assert (
        run_argv[run_argv.index("--label")],
        run_argv[run_argv.index("--label") + 1],
    ) == ("--label", "tripguru.local/managed=true")
    assert DockerEnvironmentVariable("POSTGRES_PASSWORD", PASSWORD, sensitive=True) in environment
    assert inspection is not None
    assert inspection.runtime_id == RUNTIME_ID
    assert inspection.health == "healthy"
    assert inspection.published_ports[0].host_port == 45432
    assert runner.calls[-1][0] == ("docker", "container", "rm", "--force", RUNTIME_ID)
    assert removed is True


def test_state_store_migrates_v4_records_and_rejects_intent_mutation(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    store = StateStore(database)
    store.create_workspace(workspace_input(), workspace_id="workspace-1")
    legacy = ManagedResourceRecord(
        id="legacy-postgres",
        runtime_id="d" * 64,
        name="legacy-postgres",
        kind=MiddlewareKind.POSTGRES,
        workspace_id="workspace-1",
        created_at=datetime.now(UTC),
    )
    store.upsert_managed_resource(legacy)
    store.close()

    with sqlite3.connect(database) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload FROM managed_resources WHERE id = ?", (legacy.id,)
            ).fetchone()[0]
        )
        for field in ("updated_at", "status", "intent", "failure_code"):
            payload.pop(field, None)
        connection.executescript(
            """
            ALTER TABLE managed_resources RENAME TO managed_resources_v5;
            CREATE TABLE managed_resources (
                id TEXT PRIMARY KEY,
                runtime_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
                payload TEXT NOT NULL
            );
            INSERT INTO managed_resources (id, runtime_id, workspace_id, payload)
            SELECT id, runtime_id, workspace_id, payload FROM managed_resources_v5;
            DROP TABLE managed_resources_v5;
            PRAGMA user_version = 4;
            """
        )
        connection.execute(
            "UPDATE managed_resources SET payload = ? WHERE id = ?",
            (json.dumps(payload), legacy.id),
        )

    migrated = StateStore(database)
    assert migrated.get_managed_resource(legacy.id) == legacy
    assert not migrated.matches_managed_resource(
        resource_id=legacy.id,
        runtime_id=legacy.runtime_id or "",
        kind=MiddlewareKind.POSTGRES,
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (5,)
        columns = connection.execute("PRAGMA table_info(managed_resources)").fetchall()
    runtime_column = next(column for column in columns if column[1] == "runtime_id")
    assert runtime_column[3] == 0
    assert any(column[1] == "status" for column in columns)

    planned = ManagedResourceRecord(
        id=derive_managed_resource_id("workspace-1", MiddlewareKind.POSTGRES),
        name=derive_managed_postgres_name("workspace-1"),
        kind=MiddlewareKind.POSTGRES,
        workspace_id="workspace-1",
        created_at=datetime.now(UTC),
        status=ManagedResourceStatus.PLANNED,
        intent=ManagedMiddlewareIntent(
            kind=MiddlewareKind.POSTGRES,
            host_port=45432,
            password_secret_ref="postgres-password",
        ),
    )
    persisted = migrated.begin_managed_resource(planned)
    changed = ManagedResourceRecord(
        id=persisted.id,
        name=persisted.name,
        kind=persisted.kind,
        workspace_id=persisted.workspace_id,
        created_at=persisted.created_at,
        updated_at=datetime.now(UTC),
        status=ManagedResourceStatus.PROVISIONING,
        intent=ManagedMiddlewareIntent(
            kind=MiddlewareKind.POSTGRES,
            host_port=45433,
            password_secret_ref="postgres-password",
        ),
    )
    with pytest.raises(ManagedResourceIntentConflictError):
        migrated.transition_managed_resource(changed, ManagedResourceStatus.PLANNED)
    migrated.close()
