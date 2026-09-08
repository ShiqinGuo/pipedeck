from __future__ import annotations

import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import cast
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient
from httpx import Client
from pydantic import SecretStr

from pipedeck.api import create_app
from pipedeck.contracts import (
    ApplicationEntry,
    ArgumentPortInjection,
    ComposeEndpoint,
    ComposeTarget,
    DockerfileSource,
    HostEndpoint,
    HostTarget,
    HttpReadiness,
    PlanStepKind,
    RepositoryRecord,
    RunMode,
    ServiceCommand,
    WorkspaceInput,
    WorkspacePlanResponse,
    WorkspaceRecord,
    WorkspaceRuntimeResponse,
    WorkspaceService,
)
from pipedeck.settings import LocalSettings


def _port() -> int:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return int(reservation.getsockname()[1])


def _repository(root: Path, name: str) -> Path:
    path = root / name
    shutil.copytree(Path(__file__).resolve().parents[1] / "examples/local-integration" / name, path)
    (path / ".gitignore").write_text("__pycache__/\n*.db\n", encoding="utf-8")
    for command in (
        ("init", "-b", "main"),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Integration"),
        ("add", "."),
        ("commit", "-m", "fixture"),
    ):
        subprocess.run(("git", *command), cwd=path, check=True, capture_output=True)
    return path


def _service(
    project_id: str,
    port: int,
    arguments: tuple[str, ...],
    depends_on: tuple[str, ...] = (),
    application_path: str | None = None,
) -> WorkspaceService:
    return WorkspaceService(
        project_id=project_id,
        depends_on=depends_on,
        commands=(
            ServiceCommand(
                id="build",
                label="Compile application",
                kind=PlanStepKind.BUILD,
                argv=(sys.executable, "-m", "py_compile", "app.py"),
            ),
            ServiceCommand(
                id="start",
                label="Start application",
                kind=PlanStepKind.START,
                argv=(sys.executable, "app.py", *arguments),
                long_running=True,
            ),
        ),
        execution_target=HostTarget(
            endpoints=(
                HostEndpoint(
                    name="http",
                    host_port=port,
                    injection=ArgumentPortInjection(kind="argument", option="--port"),
                ),
            ),
            readiness=HttpReadiness(kind="http", endpoint="http", path="/health"),
            application=ApplicationEntry(endpoint="http", path=application_path)
            if application_path
            else None,
            readiness_timeout=5,
        ),
    )


@pytest.mark.parametrize("backend_entry", (None, "/notes", "/notes?view=中文 空格"))
def test_real_multi_project_build_start_business_write_and_runtime_snapshot(
    tmp_path: Path,
    backend_entry: str | None,
) -> None:
    headers = {"x-pipedeck-token": "integration-test"}
    app = create_app(
        LocalSettings(
            scan_roots=(tmp_path,),
            scan_max_depth=1,
            state_db_path=tmp_path / "state.db",
            api_token=SecretStr("integration-test"),
        )
    )
    backend_port, frontend_port = _port(), _port()
    database_path = tmp_path / "notes.db"
    with TestClient(app) as raw_client:
        client = cast(Client, raw_client)
        repositories: dict[str, RepositoryRecord] = {}
        for name in ("backend", "frontend"):
            response = client.post(
                "/api/v1/repositories/import",
                headers=headers,
                json={"path": str(_repository(tmp_path, name))},
            )
            assert response.status_code == 201, response.text
            repositories[name] = RepositoryRecord.model_validate(response.json())
        definition = WorkspaceInput(
            name="Local feature acceptance",
            mode=RunMode.INTEGRATED,
            services=(
                # Frontend comes first; dependency readiness must reverse startup order.
                _service(
                    repositories["frontend"].id,
                    frontend_port,
                    ("--backend", f"http://127.0.0.1:{backend_port}"),
                    (repositories["backend"].id,),
                ),
                _service(
                    repositories["backend"].id,
                    backend_port,
                    ("--database", str(database_path)),
                    application_path=backend_entry,
                ),
            ),
        )
        workspace = WorkspaceRecord.model_validate(
            client.post(
                "/api/v1/workspaces", headers=headers, json=definition.model_dump(mode="json")
            ).json()
        )
        runtime_url = f"/api/v1/workspaces/{workspace.id}/runtime"
        assert client.get(runtime_url).json()["ready"] is False
        plan_response = client.post(
            f"/api/v1/workspaces/{workspace.id}/plans",
            headers=headers,
            json={"expected_revision": workspace.revision},
        )
        assert plan_response.status_code == 201, plan_response.text
        plan = WorkspacePlanResponse.model_validate(plan_response.json())
        assert plan.ready, plan.blockers
        assert [
            c.project_id for s in plan.steps if s.kind is PlanStepKind.START for c in s.commands
        ] == [repositories["backend"].id, repositories["frontend"].id]
        run_response = client.post(
            "/api/v1/runs",
            headers=headers,
            json={"plan_id": plan.plan_id, "idempotency_key": "integration-business"},
        )
        assert run_response.status_code == 201, run_response.text
        run_id = run_response.json()["id"]
        duplicate = client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "plan_id": plan.plan_id,
                "idempotency_key": "another-click",
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"]["code"] == "WORKSPACE_RUN_ACTIVE"
        assert run_id in duplicate.text
        deadline = time.monotonic() + 20
        while True:
            observed = WorkspaceRuntimeResponse.model_validate(client.get(runtime_url).json())
            if observed.ready or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        assert observed.ready and observed.ready_count == 2, observed
        assert all(
            service.head == repositories[service.name].head_sha for service in observed.services
        )
        assert next(s for s in observed.services if s.name == "frontend").url
        # API health does not imply it serves an application at the origin root.
        backend_url = (
            f"http://127.0.0.1:{backend_port}{quote(backend_entry, safe='/%?=&:#')}"
            if backend_entry
            else None
        )
        assert next(s for s in observed.services if s.name == "backend").url == backend_url
        request = Request(
            f"http://127.0.0.1:{frontend_port}/api/notes",
            data=json.dumps({"text": "local integration works"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=3) as response:
            assert response.status == 201
        with urlopen(f"http://127.0.0.1:{frontend_port}/api/notes", timeout=3) as response:
            assert json.loads(response.read())[0]["text"] == "local integration works"
        with sqlite3.connect(database_path) as database:
            assert database.execute("SELECT text FROM notes").fetchone() == (
                "local integration works",
            )
        # A config edit must not relabel already running services or link to an unrelated port.
        changed = definition.model_dump(mode="json")
        changed["expected_revision"] = workspace.revision
        changed["name"] = "Changed config"
        changed["services"][0]["execution_target"] = ComposeTarget(
            kind="compose",
            source=DockerfileSource(kind="dockerfile"),
            endpoints=(ComposeEndpoint(name="http", host_port=frontend_port, container_port=8080),),
            readiness=HttpReadiness(kind="http", endpoint="http"),
        ).model_dump(mode="json")
        changed["services"][1]["execution_target"]["application"] = {
            "endpoint": "http",
            "path": "/missing",
        }
        assert (
            client.put(
                f"/api/v1/workspaces/{workspace.id}", headers=headers, json=changed
            ).status_code
            == 200
        )
        observed = WorkspaceRuntimeResponse.model_validate(client.get(runtime_url).json())
        assert observed.ready and all(s.workspace_revision == 1 for s in observed.services)
        assert all(s.target == "host" for s in observed.services)
        assert next(s for s in observed.services if s.name == "backend").url == backend_url
        changed["expected_revision"] = 2
        changed["services"] = changed["services"][1:]
        assert (
            client.put(
                f"/api/v1/workspaces/{workspace.id}", headers=headers, json=changed
            ).status_code
            == 200
        )
        observed = WorkspaceRuntimeResponse.model_validate(client.get(runtime_url).json())
        assert not observed.ready
        removed = next(s for s in observed.services if s.name == "frontend")
        assert not removed.configured and removed.status == "ready" and removed.run_id == run_id
        client.post(f"/api/v1/runs/{run_id}/cancel", headers=headers)
        assert not client.get(runtime_url).json()["ready"]
        assert all(item["url"] is None for item in client.get(runtime_url).json()["services"])
