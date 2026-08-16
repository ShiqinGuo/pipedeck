from datetime import UTC, datetime
from pathlib import Path

from tripguru_local.catalog import ProjectCatalog
from tripguru_local.contracts import MiddlewareKind, ProjectKind, RepositoryRecord
from tripguru_local.processes import CommandResult


class GitRunner:
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult:
        if argv == ("git", "branch", "--show-current"):
            return CommandResult(return_code=0, stdout="main", stderr="")
        if argv == ("git", "status", "--porcelain"):
            return CommandResult(return_code=0, stdout=" M src/app.py", stderr="")
        raise AssertionError(f"Unexpected command: {argv}")


def test_catalog_detects_python_repository_and_requirements(tmp_path: Path) -> None:
    repository = tmp_path / "order-svc"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        """
[project]
name = "order-svc"
dependencies = ["asyncpg>=0.30", "redis>=7", "elasticsearch>=9"]
""".strip(),
        encoding="utf-8",
    )
    (repository / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")

    response = ProjectCatalog((tmp_path,), 3, GitRunner()).scan()

    assert response.errors == ()
    assert len(response.projects) == 1
    project = response.projects[0]
    assert project.name == "order-svc"
    assert project.kind is ProjectKind.PYTHON_UV
    assert project.dirty is True
    assert project.requirements == (
        MiddlewareKind.POSTGRES,
        MiddlewareKind.REDIS,
        MiddlewareKind.ELASTICSEARCH,
    )
    assert project.container_capabilities.dockerfile == "Dockerfile"
    assert project.container_capabilities.compose_files == ("compose.yaml",)


def test_catalog_reports_missing_scan_root(tmp_path: Path) -> None:
    response = ProjectCatalog((tmp_path / "missing",), 3, GitRunner()).scan()

    assert response.projects == ()
    assert response.errors == (f"扫描目录不可用：{tmp_path / 'missing'}",)


def test_catalog_uses_exact_discovered_compose_file_for_commands(tmp_path: Path) -> None:
    repository = tmp_path / "local-stack"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")

    response = ProjectCatalog((tmp_path,), 3, GitRunner()).scan()

    project = response.projects[0]
    assert project.kind is ProjectKind.COMPOSE
    assert [(command.id, command.argv) for command in project.commands] == [
        ("build", ("docker", "compose", "-f", "compose.yaml", "build")),
        (
            "start",
            ("docker", "compose", "-f", "compose.yaml", "up", "-d", "--wait"),
        ),
    ]


def test_catalog_uses_registered_checkout_id_outside_scan_root(tmp_path: Path) -> None:
    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    repository = tmp_path / "imported"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "package.json").write_text(
        '{"name":"imported","scripts":{"dev":"vite"},"dependencies":{"react":"19"}}',
        encoding="utf-8",
    )
    now = datetime.now(UTC)
    registered = RepositoryRecord(
        id="checkout-1",
        name="imported",
        path=str(repository),
        origin_url=None,
        branch="main",
        head_sha="abc",
        upstream=None,
        dirty=False,
        created_at=now,
        updated_at=now,
    )

    response = ProjectCatalog(
        (scan_root,),
        3,
        GitRunner(),
        registered_repositories=lambda: (registered,),
    ).scan()

    assert len(response.projects) == 1
    assert response.projects[0].id == "checkout-1"
    assert response.projects[0].path == str(repository)
