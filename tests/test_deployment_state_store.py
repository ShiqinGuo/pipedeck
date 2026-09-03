from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pipedeck.compose_deployment import (
    DeploymentIntent,
    DeploymentRevision,
    DeploymentStatus,
    DeploymentStore,
    HttpProbe,
    TargetDeploymentConflictError,
    TcpProbe,
)
from pipedeck.contracts import (
    DeploymentEnvironmentSnapshot,
    EnvironmentBinding,
    EnvironmentSource,
    MiddlewareBinding,
    MiddlewareKind,
    PostgresConnectionProfile,
)
from pipedeck.state_store import (
    DeploymentImmutableFieldError,
    DeploymentTransitionConflictError,
    StateStore,
)


def _intent(
    tmp_path: Path,
    revision_id: str,
    *,
    target_id: str = "supplier-api",
    tcp: bool = False,
) -> DeploymentIntent:
    probe = (
        TcpProbe(host="127.0.0.1", port=18000, timeout_seconds=3)
        if tcp
        else HttpProbe(url="http://127.0.0.1:18000/ready", timeout_seconds=3)
    )
    return DeploymentIntent(
        revision_id=revision_id,
        workspace_id="workspace-1",
        target_id=target_id,
        workspace_revision=3,
        source_fingerprint="source-fingerprint",
        target_config_fingerprint="target-config-fingerprint",
        checkout_path=tmp_path / "checkout",
        frozen_compose_path=tmp_path / "state" / f"{revision_id}.json",
        services=("api", "worker"),
        immutable_images=("supplier@sha256:" + "a" * 64,),
        wait_timeout_seconds=90,
        probe=probe,
        environment_spec=DeploymentEnvironmentSnapshot(),
    )


def _environment_spec() -> DeploymentEnvironmentSnapshot:
    return DeploymentEnvironmentSnapshot(
        environment=(
            EnvironmentBinding(
                name="LOG_LEVEL",
                source=EnvironmentSource.LITERAL,
                value="debug",
            ),
        ),
        connection_profiles=(
            PostgresConnectionProfile(
                kind=MiddlewareKind.POSTGRES,
                env_var="SUPPLIER_DATABASE_URL",
                username="supplier",
                database="supplier",
                secret_ref="postgres-password",
            ),
        ),
        bindings=(
            MiddlewareBinding(
                kind=MiddlewareKind.POSTGRES,
                resource_id="postgres-local",
            ),
        ),
    )


def _revision(intent: DeploymentIntent) -> DeploymentRevision:
    now = datetime.now(UTC)
    return DeploymentRevision(
        intent=intent,
        project_name="tgl-workspace-1-supplier-api",
        previous_revision_id=None,
        status=DeploymentStatus.PLANNED,
        created_at=now,
        updated_at=now,
    )


def test_deployment_revision_roundtrips_paths_and_discriminated_probes_after_reopen(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    concrete = StateStore(database)
    store: DeploymentStore = concrete
    http_intent = replace(
        _intent(tmp_path, "revision-http"),
        environment_spec=_environment_spec(),
    )
    http = store.begin(_revision(http_intent))
    tcp = store.begin(
        _revision(
            _intent(
                tmp_path,
                "revision-tcp",
                target_id="supplier-worker",
                tcp=True,
            )
        )
    )
    building = replace(
        http,
        status=DeploymentStatus.BUILDING,
        updated_at=http.updated_at + timedelta(seconds=1),
    )
    assert store.update_revision(building, DeploymentStatus.PLANNED) == building
    concrete.close()

    reopened = StateStore(database)

    assert reopened.get_revision(building.intent.revision_id) == building
    assert reopened.get_revision(tcp.intent.revision_id) == tcp
    assert reopened.list_pending_revisions() == (building, tcp)
    assert isinstance(reopened.get_revision("revision-http").intent.probe, HttpProbe)  # type: ignore[union-attr]
    assert isinstance(reopened.get_revision("revision-tcp").intent.probe, TcpProbe)  # type: ignore[union-attr]
    reopened.close()

    with sqlite3.connect(database) as connection:
        payloads = tuple(
            row[0]
            for row in connection.execute(
                "SELECT payload FROM deployment_revisions ORDER BY rowid"
            ).fetchall()
        )
    assert '"kind":"http"' in payloads[0]
    assert '"kind":"tcp"' in payloads[1]
    assert '"secret_ref":"postgres-password"' in payloads[0]
    assert str(building.intent.checkout_path).replace("\\", "\\\\") in payloads[0]


def test_environment_snapshot_is_immutable_after_begin(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    created = store.begin(_revision(_intent(tmp_path, "revision-immutable")))
    modified = replace(
        created,
        intent=replace(created.intent, environment_spec=_environment_spec()),
        status=DeploymentStatus.BUILDING,
        updated_at=created.updated_at + timedelta(seconds=1),
    )

    with pytest.raises(DeploymentImmutableFieldError):
        store.update_revision(modified, DeploymentStatus.PLANNED)

    assert store.get_revision(created.intent.revision_id) == created
    store.close()


def test_begin_rejects_same_target_pending_but_allows_another_target(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.db")
    first = store.begin(_revision(_intent(tmp_path, "revision-one")))

    with pytest.raises(TargetDeploymentConflictError):
        store.begin(_revision(_intent(tmp_path, "revision-two")))

    other = store.begin(_revision(_intent(tmp_path, "revision-other", target_id="supplier-worker")))
    assert store.get_revision(first.intent.revision_id) == first
    assert store.get_revision("revision-two") is None
    assert store.get_revision(other.intent.revision_id) == other
    store.close()


def test_list_deployment_revisions_filters_workspace_and_target_in_reverse_order(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.db")
    first = store.begin(_revision(_intent(tmp_path, "revision-one")))
    second = store.begin(_revision(_intent(tmp_path, "revision-two", target_id="supplier-worker")))
    third_intent = replace(
        _intent(tmp_path, "revision-three"),
        workspace_id="workspace-2",
    )
    third = store.begin(_revision(third_intent))

    assert store.list_deployment_revisions() == (third, second, first)
    assert store.list_deployment_revisions(workspace_id="workspace-1") == (
        second,
        first,
    )
    assert store.list_deployment_revisions(target_id="supplier-api") == (
        third,
        first,
    )
    assert store.list_deployment_revisions(
        workspace_id="workspace-1",
        target_id="supplier-api",
    ) == (first,)
    store.close()


def test_activation_cas_is_atomic_and_supersedes_bound_previous_active(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.db")
    first = store.begin(_revision(_intent(tmp_path, "revision-active")))
    first_active = replace(
        first,
        status=DeploymentStatus.ACTIVE,
        updated_at=first.updated_at + timedelta(seconds=1),
    )
    assert store.activate_revision(first_active, DeploymentStatus.PLANNED) == first_active

    second = store.begin(_revision(_intent(tmp_path, "revision-next")))
    assert second.previous_revision_id == first.intent.revision_id
    verifying = replace(
        second,
        status=DeploymentStatus.VERIFYING,
        updated_at=second.updated_at + timedelta(seconds=1),
    )
    store.update_revision(verifying, DeploymentStatus.PLANNED)
    proposed_active = replace(
        verifying,
        status=DeploymentStatus.ACTIVE,
        updated_at=verifying.updated_at + timedelta(seconds=1),
    )

    with pytest.raises(DeploymentTransitionConflictError):
        store.activate_revision(proposed_active, DeploymentStatus.APPLYING)

    assert store.get_revision(first.intent.revision_id) == first_active
    assert store.get_revision(verifying.intent.revision_id) == verifying

    activated = store.activate_revision(proposed_active, DeploymentStatus.VERIFYING)
    previous = store.get_revision(first.intent.revision_id)

    assert activated.status is DeploymentStatus.ACTIVE
    assert previous is not None
    assert previous.status is DeploymentStatus.SUPERSEDED
    assert previous.updated_at == activated.updated_at
    assert store.list_pending_revisions() == ()
    store.close()


def test_v3_migration_adds_deployment_store_without_rewriting_existing_payloads(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    workspace_payload = '{"legacy":"workspace"}'
    plan_payload = '{"legacy":"plan"}'
    run_payload = '{"legacy":"run"}'
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE plans (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                workspace_revision INTEGER NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE run_events (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence)
            );
            PRAGMA user_version = 3;
            """
        )
        connection.execute(
            "INSERT INTO workspaces (id, revision, payload) VALUES ('workspace', 1, ?)",
            (workspace_payload,),
        )
        connection.execute(
            """
            INSERT INTO plans (id, workspace_id, workspace_revision, payload)
            VALUES ('plan', 'workspace', 1, ?)
            """,
            (plan_payload,),
        )
        connection.execute(
            "INSERT INTO runs (id, status, payload) VALUES ('run', 'succeeded', ?)",
            (run_payload,),
        )

    store = StateStore(database)
    store.close()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (6,)
        assert connection.execute(
            "SELECT payload FROM workspaces WHERE id = 'workspace'"
        ).fetchone() == (workspace_payload,)
        assert connection.execute("SELECT payload FROM plans WHERE id = 'plan'").fetchone() == (
            plan_payload,
        )
        assert connection.execute("SELECT payload FROM runs WHERE id = 'run'").fetchone() == (
            run_payload,
        )
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'deployment_revisions'"
        ).fetchone() == ("deployment_revisions",)
