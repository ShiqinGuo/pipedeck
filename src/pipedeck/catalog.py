from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import monotonic

from pydantic import ValidationError

from pipedeck.contracts import (
    CatalogResponse,
    ContainerCapabilities,
    MiddlewareKind,
    PackageManifest,
    PlanStepKind,
    ProjectCommand,
    ProjectKind,
    ProjectSummary,
    PyProjectManifest,
    RepositoryRecord,
)
from pipedeck.processes import CommandRunner

_COMPOSE_FILE_NAMES = (
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
)

_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".idea",
        ".next",
        ".nuxt",
        ".venv",
        "__pycache__",
        "dist",
        "node_modules",
        "target",
    }
)


def _empty_registered_repositories() -> tuple[RepositoryRecord, ...]:
    return ()


class ProjectCatalog:
    def __init__(
        self,
        roots: tuple[Path, ...],
        max_depth: int,
        command_runner: CommandRunner,
        registered_repositories: Callable[[], tuple[RepositoryRecord, ...]] | None = None,
    ) -> None:
        self._roots = roots
        self._max_depth = max_depth
        self._command_runner = command_runner
        self._registered_repositories = registered_repositories or _empty_registered_repositories
        self._cache_lock = Lock()
        self._cached_at = 0.0
        self._cached_response: CatalogResponse | None = None

    def scan(self) -> CatalogResponse:
        with self._cache_lock:
            if self._cached_response is not None and monotonic() - self._cached_at < 8:
                return self._cached_response
            response = self._scan_uncached()
            self._cached_response = response
            self._cached_at = monotonic()
            return response

    def invalidate(self) -> None:
        with self._cache_lock:
            self._cached_at = 0.0
            self._cached_response = None

    def _scan_uncached(self) -> CatalogResponse:
        projects: list[ProjectSummary] = []
        errors: list[str] = []
        seen_paths: set[Path] = set()
        registered = self._registered_repositories()

        for root in self._roots:
            resolved_root = root.expanduser().resolve()
            if not resolved_root.is_dir():
                errors.append(f"扫描目录不可用：{resolved_root}")
                continue
            for repository in self._repository_paths(resolved_root):
                if repository in seen_paths:
                    continue
                seen_paths.add(repository)
                try:
                    project = self._describe(repository)
                    registered_record = self._registered_for_path(registered, repository)
                    if registered_record is not None:
                        project = self._with_id(project, registered_record.id)
                    projects.append(project)
                except (OSError, ValidationError, tomllib.TOMLDecodeError) as error:
                    errors.append(f"无法读取 {repository.name}：{type(error).__name__}")

        for registered_record in registered:
            repository = Path(registered_record.path).expanduser().resolve()
            if repository in seen_paths:
                continue
            if not repository.is_dir():
                errors.append(f"已登记仓库不可用：{repository}")
                continue
            try:
                project = self._describe(repository)
                projects.append(self._with_id(project, registered_record.id))
            except (OSError, ValidationError, tomllib.TOMLDecodeError) as error:
                errors.append(f"无法读取 {repository.name}：{type(error).__name__}")

        projects.sort(key=lambda project: (project.kind.value, project.name.casefold()))
        return CatalogResponse(
            generated_at=datetime.now(UTC),
            roots=tuple(str(root.expanduser().resolve()) for root in self._roots),
            projects=tuple(projects),
            errors=tuple(errors),
        )

    @staticmethod
    def _registered_for_path(
        records: tuple[RepositoryRecord, ...],
        path: Path,
    ) -> RepositoryRecord | None:
        return next(
            (record for record in records if Path(record.path).expanduser().resolve() == path),
            None,
        )

    @staticmethod
    def _with_id(project: ProjectSummary, project_id: str) -> ProjectSummary:
        return ProjectSummary(
            id=project_id,
            name=project.name,
            path=project.path,
            kind=project.kind,
            branch=project.branch,
            dirty=project.dirty,
            commands=project.commands,
            requirements=project.requirements,
            warnings=project.warnings,
            container_capabilities=project.container_capabilities,
        )

    def _repository_paths(self, root: Path) -> Iterable[Path]:
        for current, directory_names, file_names in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            if depth > self._max_depth:
                directory_names[:] = []
                continue

            has_git_metadata = ".git" in directory_names or ".git" in file_names
            directory_names[:] = [
                name for name in directory_names if name not in _IGNORED_DIRECTORIES
            ]
            if has_git_metadata:
                directory_names[:] = []
                yield current_path.resolve()

    def _describe(self, path: Path) -> ProjectSummary:
        package_manifest = self._read_package_manifest(path)
        pyproject_manifest = self._read_pyproject_manifest(path)
        kind = self._detect_kind(path, package_manifest, pyproject_manifest)
        name = self._project_name(path, package_manifest, pyproject_manifest)
        branch = self._git_output(path, ("git", "branch", "--show-current")) or "detached"
        dirty = bool(self._git_output(path, ("git", "status", "--porcelain")))
        commands = self._commands(path, kind, package_manifest)
        requirements = self._requirements(pyproject_manifest)
        warnings = ("工作区包含未提交修改",) if dirty else ()
        container_capabilities = self._container_capabilities(path)
        project_id = hashlib.sha256(str(path).casefold().encode()).hexdigest()[:12]

        return ProjectSummary(
            id=project_id,
            name=name,
            path=str(path),
            kind=kind,
            branch=branch,
            dirty=dirty,
            commands=commands,
            requirements=requirements,
            warnings=warnings,
            container_capabilities=container_capabilities,
        )

    def _git_output(self, path: Path, argv: tuple[str, ...]) -> str:
        result = self._command_runner.run(argv, cwd=path)
        return result.stdout if result.return_code == 0 else ""

    @staticmethod
    def _read_package_manifest(path: Path) -> PackageManifest | None:
        manifest_path = path / "package.json"
        if not manifest_path.is_file():
            return None
        with manifest_path.open(encoding="utf-8") as file:
            # pipedeck-ast: ignore[TG-DS001] - JSON is validated at this file boundary.
            payload = json.load(file)
        return PackageManifest.model_validate(payload)

    @staticmethod
    def _read_pyproject_manifest(path: Path) -> PyProjectManifest | None:
        manifest_path = path / "pyproject.toml"
        if not manifest_path.is_file():
            return None
        with manifest_path.open("rb") as file:
            # pipedeck-ast: ignore[TG-DS001] - TOML is validated at this file boundary.
            payload = tomllib.load(file)
        return PyProjectManifest.model_validate(payload)

    @staticmethod
    def _detect_kind(
        path: Path,
        package_manifest: PackageManifest | None,
        pyproject_manifest: PyProjectManifest | None,
    ) -> ProjectKind:
        if package_manifest is not None:
            dependencies = package_manifest.dependencies
            dev_dependencies = package_manifest.dev_dependencies
            if dependencies.nuxt is not None or dev_dependencies.nuxt is not None:
                return ProjectKind.NUXT
            if dependencies.react is not None or dev_dependencies.react is not None:
                return ProjectKind.VITE_REACT
            if dependencies.vue is not None or dev_dependencies.vue is not None:
                return ProjectKind.VITE_VUE
        if pyproject_manifest is not None:
            return ProjectKind.PYTHON_UV
        if any((path / name).is_file() for name in _COMPOSE_FILE_NAMES):
            return ProjectKind.COMPOSE
        return ProjectKind.UNKNOWN

    @staticmethod
    def _project_name(
        path: Path,
        package_manifest: PackageManifest | None,
        pyproject_manifest: PyProjectManifest | None,
    ) -> str:
        if package_manifest is not None and package_manifest.name:
            return package_manifest.name
        if pyproject_manifest is not None and pyproject_manifest.project is not None:
            return pyproject_manifest.project.name or path.name
        return path.name

    @staticmethod
    def _commands(
        path: Path, kind: ProjectKind, package_manifest: PackageManifest | None
    ) -> tuple[ProjectCommand, ...]:
        if kind is ProjectKind.PYTHON_UV:
            return (
                ProjectCommand(
                    id="install",
                    label="安装依赖",
                    argv=("uv", "sync"),
                    kind=PlanStepKind.DEPENDENCIES,
                ),
                ProjectCommand(
                    id="test",
                    label="运行测试",
                    argv=("uv", "run", "pytest"),
                    kind=PlanStepKind.QUALITY,
                ),
            )
        if kind is ProjectKind.COMPOSE:
            compose_file = next(
                (name for name in _COMPOSE_FILE_NAMES if (path / name).is_file()),
                None,
            )
            if compose_file is None:
                return ()
            compose_prefix = ("docker", "compose", "-f", compose_file)
            return (
                ProjectCommand(
                    id="build",
                    label="构建 Compose 服务",
                    argv=(*compose_prefix, "build"),
                    kind=PlanStepKind.BUILD,
                ),
                ProjectCommand(
                    id="start",
                    label="启动 Compose 服务",
                    argv=(*compose_prefix, "up", "-d", "--wait"),
                    kind=PlanStepKind.START,
                ),
            )
        if package_manifest is not None:
            scripts = package_manifest.scripts
            commands: list[ProjectCommand] = [
                ProjectCommand(
                    id="install",
                    label="安装依赖",
                    argv=("corepack", "pnpm", "install", "--frozen-lockfile"),
                    kind=PlanStepKind.DEPENDENCIES,
                )
            ]
            if scripts.typecheck:
                commands.append(
                    ProjectCommand(
                        id="typecheck",
                        label="类型检查",
                        argv=("corepack", "pnpm", "typecheck"),
                        kind=PlanStepKind.QUALITY,
                    )
                )
            if scripts.lint:
                commands.append(
                    ProjectCommand(
                        id="lint",
                        label="代码检查",
                        argv=("corepack", "pnpm", "lint"),
                        kind=PlanStepKind.QUALITY,
                    )
                )
            if scripts.test_ci or scripts.test:
                commands.append(
                    ProjectCommand(
                        id="test",
                        label="运行测试",
                        argv=("corepack", "pnpm", "test:ci" if scripts.test_ci else "test"),
                        kind=PlanStepKind.QUALITY,
                    )
                )
            if scripts.dev:
                commands.append(
                    ProjectCommand(
                        id="dev",
                        label="开发启动",
                        argv=("corepack", "pnpm", "dev"),
                        kind=PlanStepKind.START,
                        long_running=True,
                    )
                )
            if scripts.build:
                commands.append(
                    ProjectCommand(
                        id="build",
                        label="生产构建",
                        argv=("corepack", "pnpm", "build"),
                        kind=PlanStepKind.BUILD,
                    )
                )
            return tuple(commands)
        return ()

    @staticmethod
    def _container_capabilities(path: Path) -> ContainerCapabilities:
        return ContainerCapabilities(
            dockerfile="Dockerfile" if (path / "Dockerfile").is_file() else None,
            compose_files=tuple(name for name in _COMPOSE_FILE_NAMES if (path / name).is_file()),
        )

    @staticmethod
    def _requirements(manifest: PyProjectManifest | None) -> tuple[MiddlewareKind, ...]:
        if manifest is None or manifest.project is None:
            return ()
        dependencies = " ".join(manifest.project.dependencies).casefold()
        requirements: list[MiddlewareKind] = []
        if any(name in dependencies for name in ("asyncpg", "psycopg", "sqlalchemy")):
            requirements.append(MiddlewareKind.POSTGRES)
        if "redis" in dependencies:
            requirements.append(MiddlewareKind.REDIS)
        if "elasticsearch" in dependencies:
            requirements.append(MiddlewareKind.ELASTICSEARCH)
        if any(name in dependencies for name in ("boto3", "aioboto3", "minio")):
            requirements.append(MiddlewareKind.MINIO)
        return tuple(requirements)
