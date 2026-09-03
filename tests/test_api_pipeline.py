"""pipeline 端点契约：预览、计划生成、鉴权与错误语义。"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from pipedeck.api import create_app
from pipedeck.contracts import RepositoryRecord
from pipedeck.settings import LocalSettings

_YML = """
stages: [build, test]
build_job:
  stage: build
  image: alpine
  script: ["echo build"]
test_job:
  stage: test
  image: alpine
  script: ["echo test"]
  needs: [build_job]
"""


def _client(tmp_path: Path, yml: str | None, token: str = "test-token") -> TestClient:
    settings = LocalSettings(
        state_db_path=tmp_path / "state.db",
        api_token=token,  # type: ignore[arg-type]
        scan_roots=(tmp_path,),
    )
    app = create_app(settings)
    record = RepositoryRecord(
        id="repo-1",
        name="demo-repo",
        path=str(tmp_path / "demo-repo"),
        origin_url=None,
        branch="main",
        head_sha="a" * 40,
        upstream=None,
        dirty=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    app.state.control_store.upsert_repository(record)
    repo_dir = tmp_path / "demo-repo"
    repo_dir.mkdir(exist_ok=True)
    if yml is not None:
        (repo_dir / ".gitlab-ci.yml").write_text(yml, encoding="utf-8")
    return TestClient(app)


def test_pipeline_preview_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path, _YML)
    response = client.get("/api/v1/repositories/repo-1/pipeline")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert [job["name"] for job in payload["jobs"]] == ["build_job", "test_job"]
    assert payload["jobs"][1]["needs"][0]["job"] == "build_job"


def test_pipeline_plan_endpoint_requires_token(tmp_path: Path) -> None:
    client = _client(tmp_path, _YML, token="secret")
    response = client.post("/api/v1/repositories/repo-1/pipeline/plan", json={})
    assert response.status_code == 401


def test_pipeline_plan_endpoint_creates_saved_plan(tmp_path: Path) -> None:
    client = _client(tmp_path, _YML)
    response = client.post(
        "/api/v1/repositories/repo-1/pipeline/plan",
        json={},
        headers={"x-pipedeck-token": "test-token"},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["ready"] is True
    assert [step["id"] for step in payload["steps"]] == ["job:build_job", "job:test_job"]
    assert payload["workspace_id"] is None
    assert payload["steps"][0]["pipeline_job"]["job"]["name"] == "build_job"


def test_pipeline_preview_missing_yml_is_problem(tmp_path: Path) -> None:
    client = _client(tmp_path, yml=None)
    response = client.get("/api/v1/repositories/repo-1/pipeline")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "GITLAB_CI_FILE_MISSING"


def test_pipeline_preview_unknown_repository(tmp_path: Path) -> None:
    client = _client(tmp_path, _YML)
    response = client.get("/api/v1/repositories/missing/pipeline")
    assert response.status_code == 404
