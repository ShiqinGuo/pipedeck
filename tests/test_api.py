# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import json
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from pipedeck.api import create_app
from pipedeck.contracts import (
    CatalogResponse,
    DeploymentRevisionListResponse,
    RepositoryRecord,
    RunEventListResponse,
    RunRecord,
    RuntimeProcessListResponse,
    WorkspacePlanResponse,
    WorkspaceRecord,
)
from pipedeck.managed_middleware import ManagedResourceListResponse
from pipedeck.settings import LocalSettings


def test_catalog_and_plan_contract(tmp_path: Path) -> None:
    repository = tmp_path / "supplier-admin"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "package.json").write_text(
        json.dumps(
            {
                "name": "supplier-admin",
                "scripts": {"dev": "vite", "build": "vite build"},
                "dependencies": {"react": "^19.0.0"},
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        LocalSettings(
            scan_roots=(tmp_path,),
            scan_max_depth=2,
            state_db_path=tmp_path / "state.db",
        )
    )

    with TestClient(app) as client:
        catalog_response = client.get("/api/v1/catalog/projects")
        assert catalog_response.status_code == 200
        project = CatalogResponse.model_validate(catalog_response.json()).projects[0]

        plan_response = client.post(
            "/api/v1/workspace-plans",
            json={"project_ids": [project.id], "mode": "development", "bindings": []},
        )

    assert plan_response.status_code == 200
    payload = WorkspacePlanResponse.model_validate(plan_response.json())
    assert payload.ready is True
    assert payload.projects[0].kind == "vite-react"
    start_step = next(step for step in payload.steps if step.id == "start")
    assert len(start_step.commands) == 1
    start_command = start_step.commands[0]
    assert start_command.project_id == project.id
    assert start_command.project_name == "supplier-admin"
    assert start_command.command_id == "dev"
    assert start_command.label == "Start dev"
    assert start_command.cwd == str(repository)
    assert start_command.argv == ("corepack", "pnpm", "dev")


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=repository,
        capture_output=True,
        check=True,
        text=True,
    )


def test_authenticated_repository_workspace_and_run_flow(tmp_path: Path) -> None:
    repository = tmp_path / "local-service"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "pipedeck@example.test")
    _git(repository, "config", "user.name", "Pipedeck Test")
    (repository / "README.md").write_text("local service\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "initial")

    token = "local-api-test-token"
    app = create_app(
        LocalSettings(
            scan_roots=(tmp_path,),
            scan_max_depth=2,
            state_db_path=tmp_path / "control.db",
            api_token=SecretStr(token),
        )
    )
    headers = {"x-pipedeck-token": token}

    with TestClient(app) as client:
        processes = RuntimeProcessListResponse.model_validate(
            client.get("/api/v1/runtime/processes").json()
        )
        assert processes.processes == ()

        deployments = DeploymentRevisionListResponse.model_validate(
            client.get("/api/v1/deployments").json()
        )
        assert deployments.deployments == ()

        unauthorized_reconcile = client.post(
            "/api/v1/deployments/missing/reconcile",
        )
        assert unauthorized_reconcile.status_code == 401
        assert unauthorized_reconcile.json()["detail"]["code"] == "WRITE_AUTH_REQUIRED"

        missing_reconcile = client.post(
            "/api/v1/deployments/missing/reconcile",
            headers=headers,
        )
        assert missing_reconcile.status_code == 404
        assert missing_reconcile.json()["detail"]["code"] == "DEPLOYMENT_REVISION_NOT_FOUND"

        unauthorized = client.post(
            "/api/v1/repositories/import",
            json={"path": str(repository)},
        )
        assert unauthorized.status_code == 401

        imported = client.post(
            "/api/v1/repositories/import",
            headers=headers,
            json={"path": str(repository)},
        )
        assert imported.status_code == 201
        checkout_id = RepositoryRecord.model_validate(imported.json()).id

        catalog = CatalogResponse.model_validate(client.get("/api/v1/catalog/projects").json())
        assert catalog.projects[0].id == checkout_id

        workspace_response = client.post(
            "/api/v1/workspaces",
            headers=headers,
            json={
                "name": "Local API smoke",
                "mode": "development",
                "services": [
                    {
                        "project_id": checkout_id,
                        "commands": [
                            {
                                "id": "smoke-start",
                                "label": "Run smoke command",
                                "kind": "start",
                                "argv": [sys.executable, "-c", "print('run-ok')"],
                                "long_running": False,
                            }
                        ],
                        "environment": [],
                    }
                ],
                "bindings": [],
            },
        )
        assert workspace_response.status_code == 201
        workspace = WorkspaceRecord.model_validate(workspace_response.json())

        managed_resources = ManagedResourceListResponse.model_validate(
            client.get(f"/api/v1/managed-middleware?workspace_id={workspace.id}").json()
        )
        assert managed_resources.resources == ()

        managed_payload = {
            "workspace_id": workspace.id,
            "kind": "minio",
            "host_port": 45432,
            "username": "supplier",
            "database": "supplier",
            "password_secret_ref": "not-used-for-minio",
        }
        unauthorized_managed = client.post(
            "/api/v1/managed-middleware",
            json=managed_payload,
        )
        assert unauthorized_managed.status_code == 401

        unsupported_managed = client.post(
            "/api/v1/managed-middleware",
            headers=headers,
            json=managed_payload,
        )
        assert unsupported_managed.status_code == 400
        assert unsupported_managed.json()["detail"]["code"] == "MANAGED_MIDDLEWARE_KIND_UNSUPPORTED"

        missing_managed_reconcile = client.post(
            "/api/v1/managed-middleware/missing/reconcile",
            headers=headers,
        )
        assert missing_managed_reconcile.status_code == 404
        assert missing_managed_reconcile.json()["detail"]["code"] == "MANAGED_MIDDLEWARE_NOT_FOUND"
        missing_managed_delete = client.delete(
            "/api/v1/managed-middleware/missing",
            headers=headers,
        )
        assert missing_managed_delete.status_code == 404

        plan_response = client.post(
            f"/api/v1/workspaces/{workspace.id}/plans",
            headers=headers,
            json={"expected_revision": workspace.revision},
        )
        assert plan_response.status_code == 201
        plan = WorkspacePlanResponse.model_validate(plan_response.json())
        assert plan.ready is True
        assert plan.plan_id is not None

        create_payload = {
            "plan_id": plan.plan_id,
            "idempotency_key": "api-smoke-run-0001",
        }
        run_response = client.post("/api/v1/runs", headers=headers, json=create_payload)
        assert run_response.status_code == 201
        run_id = RunRecord.model_validate(run_response.json()).id
        duplicate = client.post("/api/v1/runs", headers=headers, json=create_payload)
        assert duplicate.status_code == 201
        assert RunRecord.model_validate(duplicate.json()).id == run_id

        status_value = "queued"
        for _ in range(100):
            run = RunRecord.model_validate(client.get(f"/api/v1/runs/{run_id}").json())
            status_value = run.status.value
            if status_value not in {"queued", "running"}:
                break
            time.sleep(0.03)

        assert status_value == "succeeded"
        events = RunEventListResponse.model_validate(
            client.get(f"/api/v1/runs/{run_id}/events?after=0").json()
        )
        assert any(event.message == "run-ok" for event in events.events)
