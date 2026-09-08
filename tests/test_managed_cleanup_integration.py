# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from pipedeck.api import create_app
from pipedeck.contracts import (
    CleanupApplyResponse,
    CleanupPreviewResponse,
    MiddlewareKind,
    SecretMetadata,
    WorkspaceRecord,
)
from pipedeck.managed_middleware import (
    ManagedResourceListResponse,
    ManagedResourceRecord,
    ManagedResourceStatus,
)
from pipedeck.settings import LocalSettings


def _docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("docker", *arguments),
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _workspace(client: TestClient, headers: dict[str, str], name: str) -> WorkspaceRecord:
    response = client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={
            "name": name,
            "mode": "development",
            "services": [{"project_id": f"{name}-service"}],
            "bindings": [],
        },
    )
    assert response.status_code == 201
    return WorkspaceRecord.model_validate(response.json())


def _wait_active(
    client: TestClient,
    headers: dict[str, str],
    record: ManagedResourceRecord,
) -> ManagedResourceRecord:
    deadline = time.monotonic() + 60
    current = record
    while time.monotonic() < deadline:
        if current.status is ManagedResourceStatus.ACTIVE:
            return current
        time.sleep(0.5)
        response = client.post(
            f"/api/v1/managed-middleware/{record.id}/reconcile",
            headers=headers,
        )
        assert response.status_code == 202
        current = ManagedResourceRecord.model_validate(response.json())
    raise AssertionError(f"Managed PostgreSQL did not become active: {current.failure_code}")


def test_real_managed_postgres_cleanup_keeps_one_and_never_selects_external_or_minio(
    tmp_path: Path,
) -> None:
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker daemon is unavailable")

    initial_containers = set(
        _docker("container", "ls", "-a", "--format", "{{.ID}}").stdout.splitlines()
    )
    token = "managed-cleanup-integration-token"
    app = create_app(
        LocalSettings(
            scan_roots=(tmp_path,),
            state_db_path=tmp_path / "state.db",
            api_token=SecretStr(token),
        )
    )
    headers = {"x-pipedeck-token": token}
    workspace_ids: list[str] = []
    disposable_volumes: list[str] = []
    with TestClient(app) as client:
        try:
            secret_response = client.post(
                "/api/v1/secrets",
                headers=headers,
                json={"name": "Managed PostgreSQL test", "value": "managed-test-password"},
            )
            assert secret_response.status_code == 201
            secret = SecretMetadata.model_validate(secret_response.json())
            workspaces = (
                _workspace(client, headers, "managed-a"),
                _workspace(client, headers, "managed-b"),
            )
            workspace_ids.extend(workspace.id for workspace in workspaces)
            records: list[ManagedResourceRecord] = []
            for workspace in workspaces:
                provisioned = client.post(
                    "/api/v1/managed-middleware",
                    headers=headers,
                    json={
                        "workspace_id": workspace.id,
                        "kind": "postgres",
                        "host_port": _available_port(),
                        "username": "pipedeck",
                        "database": "pipedeck",
                        "password_secret_ref": secret.id,
                    },
                )
                assert provisioned.status_code == 202
                active = _wait_active(
                    client,
                    headers,
                    ManagedResourceRecord.model_validate(provisioned.json()),
                )
                assert active.runtime_id is not None
                disposable_volumes.extend(
                    item
                    for item in _docker(
                        "inspect",
                        "--format",
                        '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\\n"}}{{end}}{{end}}',
                        active.runtime_id,
                    ).stdout.splitlines()
                    if item
                )
                records.append(active)

            blocked_secret = client.delete(
                f"/api/v1/secrets/{secret.id}?expected_version={secret.version}",
                headers=headers,
            )
            assert blocked_secret.status_code == 409
            assert blocked_secret.json()["detail"]["code"] == "SECRET_IN_USE"

            preview_response = client.post(
                "/api/v1/runtime/cleanup-previews",
                headers=headers,
            )
            assert preview_response.status_code == 201
            preview = CleanupPreviewResponse.model_validate(preview_response.json())
            first_runtime_id = records[0].runtime_id
            assert first_runtime_id is not None
            first_item = next(
                item for item in preview.items if first_runtime_id.startswith(item.resource.id)
            )
            assert first_item.eligible is True
            minio_items = tuple(
                item for item in preview.items if item.resource.kind is MiddlewareKind.MINIO
            )
            # MinIO may be absent on a clean machine; any existing instance stays protected.
            assert all(
                not item.eligible and item.reason_code == "MINIO_ALWAYS_PROTECTED"
                for item in minio_items
            )
            assert all(not item.eligible for item in preview.items if not item.resource.managed)

            applied_response = client.post(
                f"/api/v1/runtime/cleanup-previews/{preview.id}/apply",
                headers=headers,
                json={"preview_id": preview.id, "resource_ids": [first_item.resource.id]},
            )
            assert applied_response.status_code == 200
            applied = CleanupApplyResponse.model_validate(applied_response.json())
            assert applied.results[0].status == "removed"

            managed = ManagedResourceListResponse.model_validate(
                client.get("/api/v1/managed-middleware").json()
            ).resources
            states = {record.id: record.status for record in managed}
            assert states[records[0].id] is ManagedResourceStatus.REMOVED
            assert states[records[1].id] is ManagedResourceStatus.ACTIVE
            assert (
                _docker("inspect", records[0].runtime_id or "missing", check=False).returncode != 0
            )
            assert _docker("inspect", records[1].runtime_id or "missing").returncode == 0

            removed_second = client.delete(
                f"/api/v1/managed-middleware/{records[1].id}",
                headers=headers,
            )
            assert removed_second.status_code == 200
            assert (
                ManagedResourceRecord.model_validate(removed_second.json()).status
                is ManagedResourceStatus.REMOVED
            )
            deleted_secret = client.delete(
                f"/api/v1/secrets/{secret.id}?expected_version={secret.version}",
                headers=headers,
            )
            assert deleted_secret.status_code == 204
        finally:
            for workspace_id in workspace_ids:
                leftovers = _docker(
                    "container",
                    "ls",
                    "-a",
                    "--filter",
                    f"label=tripguru.local/workspace={workspace_id}",
                    "--format",
                    "{{.ID}}",
                    check=False,
                ).stdout.splitlines()
                for runtime_id in leftovers:
                    _docker("container", "rm", "--force", runtime_id, check=False)
            for volume_name in disposable_volumes:
                _docker("volume", "rm", volume_name, check=False)

    final_containers = set(
        _docker("container", "ls", "-a", "--format", "{{.ID}}").stdout.splitlines()
    )
    assert final_containers == initial_containers
