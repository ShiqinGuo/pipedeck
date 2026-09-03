"""CLI 端到端：真实控制面（uvicorn 线程）+ cli-token 互信 + run --wait 闭环。"""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr

from pipedeck.cli import ApiClient, CliApiError, cli_token_path, read_cli_token
from pipedeck.contracts import RepositoryRecord
from pipedeck.settings import LocalSettings

_YML = 'stages: [build]\nbuild_job:\n  stage: build\n  script: ["echo cli-e2e"]\n'


class _UvicornHarness:
    def __init__(self, app: FastAPI) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        while not self.server.started:
            threading.Event().wait(0.05)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def __exit__(self, *args: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture()
def running_api(tmp_path: Path) -> Iterator[tuple[ApiClient, Path, Path]]:
    state_db = tmp_path / "state.db"
    repo_dir = tmp_path / "demo-repo"
    repo_dir.mkdir()
    (repo_dir / ".gitlab-ci.yml").write_text(_YML, encoding="utf-8")
    settings = LocalSettings(
        state_db_path=state_db,
        scan_roots=(tmp_path,),
        api_token=SecretStr("unit-token-1234"),
    )
    from pipedeck.api import create_app

    app = create_app(settings)
    app.state.control_store.upsert_repository(
        RepositoryRecord(
            id="repo-1",
            name="demo-repo",
            path=str(repo_dir),
            origin_url=None,
            branch="main",
            head_sha="0" * 40,
            upstream=None,
            dirty=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    with _UvicornHarness(app) as base_url:
        token = "unit-token-1234"
        state_dir = state_db.parent
        cli_token_path(state_dir).write_text(token, encoding="utf-8")
        yield ApiClient(base_url, token), state_dir, repo_dir
    app.state.control_store.close()


def test_cli_token_roundtrip(tmp_path: Path) -> None:
    assert read_cli_token(tmp_path) is None
    cli_token_path(tmp_path).write_text("tok-abc\n", encoding="utf-8")
    assert read_cli_token(tmp_path) == "tok-abc"


def test_api_client_requires_token_for_writes(
    running_api: tuple[ApiClient, Path, Path],
) -> None:
    client, _state_dir, _repo = running_api
    anonymous = ApiClient(client.base_url, None)
    with pytest.raises(CliApiError) as exc:
        anonymous.post("/repositories/repo-1/pipeline/plan", {})
    assert exc.value.status == 401


def _wait_terminal(client: ApiClient, run_id: str, timeout: float = 30) -> str:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = str(client.get(f"/runs/{run_id}").get("status"))
        if status in {"succeeded", "failed", "cancelled", "interrupted"}:
            return status
        time.sleep(0.2)
    raise AssertionError(f"运行 {run_id} 在 {timeout}s 内未到达终态")


def test_run_wait_reaches_terminal_status(
    running_api: tuple[ApiClient, Path, Path],
) -> None:
    client, _state_dir, _repo = running_api
    plan = client.post("/repositories/repo-1/pipeline/plan", {})
    assert plan["ready"] is True
    run = client.post(
        "/runs", {"plan_id": plan["plan_id"], "idempotency_key": f"cli-{uuid4().hex}"}
    )
    status = _wait_terminal(client, str(run["id"]))
    assert status == "succeeded"
    events = client.get(f"/runs/{run['id']}/events")
    assert any("cli-e2e" in str(event.get("message", "")) for event in events.get("events", []))


def test_pipeline_preview_via_api(running_api: tuple[ApiClient, Path, Path]) -> None:
    client, _state_dir, _repo = running_api
    preview = client.get("/repositories/repo-1/pipeline")
    assert preview["ready"] is True
    assert preview["jobs"][0]["name"] == "build_job"
