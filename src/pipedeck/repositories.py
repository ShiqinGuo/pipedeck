from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from uuid import uuid4

from pipedeck.contracts import (
    RepositoryCloneRequest,
    RepositoryImportRequest,
    RepositoryRecord,
)
from pipedeck.processes import CommandRunner, SubprocessRunner
from pipedeck.state_store import StateStore

_CLONE_TEMP_PREFIX = ".pipedeck-clone-"
_SCP_USERINFO_PATTERN = re.compile(r"^([^/\\:]+)@[^:]+:")


class RepositoryServiceError(Exception):
    code = "REPOSITORY_OPERATION_FAILED"
    detail = "仓库操作失败"

    def __init__(self) -> None:
        super().__init__()


class RepositoryNotFoundError(RepositoryServiceError):
    code = "REPOSITORY_NOT_FOUND"

    def __init__(self, repository_id: str) -> None:
        self.detail = f"Checkout 不存在：{repository_id}"
        super().__init__()


class RepositoryPathUnavailableError(RepositoryServiceError):
    code = "REPOSITORY_PATH_UNAVAILABLE"

    def __init__(self, path: str) -> None:
        self.detail = f"仓库路径不存在或不可访问：{path}"
        super().__init__()


class RepositoryNotGitError(RepositoryServiceError):
    code = "REPOSITORY_NOT_GIT"

    def __init__(self, path: str) -> None:
        self.detail = f"路径不是 Git working tree：{path}"
        super().__init__()


class RepositoryGitInspectError(RepositoryServiceError):
    code = "REPOSITORY_GIT_INSPECT_FAILED"

    def __init__(self, path: str) -> None:
        self.detail = f"无法读取 Git 状态：{path}"
        super().__init__()


class RepositoryUrlUserinfoError(RepositoryServiceError):
    code = "REPOSITORY_URL_USERINFO_FORBIDDEN"
    detail = "Git URL 不允许包含用户名、密码或 Token"


class RepositoryDestinationError(RepositoryServiceError):
    code = "REPOSITORY_DESTINATION_INVALID"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__()


class RepositoryDirectoryNameInferenceError(RepositoryServiceError):
    code = "REPOSITORY_DIRECTORY_NAME_UNRESOLVED"
    detail = "无法从 Git URL 推导目标目录名"


class RepositoryDirectoryNameInvalidError(RepositoryServiceError):
    code = "REPOSITORY_DIRECTORY_NAME_INVALID"
    detail = "目标目录名必须是单个安全目录名"


class RepositoryDestinationExistsError(RepositoryServiceError):
    code = "REPOSITORY_DESTINATION_EXISTS"

    def __init__(self, path: str) -> None:
        self.detail = f"克隆目标已存在且不属于已登记 Checkout：{path}"
        super().__init__()


class RepositoryCloneFailedError(RepositoryServiceError):
    code = "REPOSITORY_CLONE_FAILED"
    detail = "Git clone 失败；目标目录未被登记"


class RepositoryCloneBoundaryError(RepositoryServiceError):
    code = "REPOSITORY_CLONE_BOUNDARY_INVALID"
    detail = "克隆临时目录超出目标父目录，已拒绝清理或移动"


class RepositoryCheckoutFailedError(RepositoryServiceError):
    code = "REPOSITORY_CHECKOUT_FAILED"

    def __init__(self, ref: str, path: str) -> None:
        self.detail = f"无法切换到 {ref}：{path}"
        super().__init__()


class RepositoryDirtyError(RepositoryServiceError):
    code = "REPOSITORY_DIRTY"

    def __init__(self, path: str) -> None:
        self.detail = f"Checkout 有未提交修改，不能更新：{path}"
        super().__init__()


class RepositoryDetachedError(RepositoryServiceError):
    code = "REPOSITORY_DETACHED_HEAD"

    def __init__(self, path: str) -> None:
        self.detail = f"Checkout 处于 detached HEAD，不能更新：{path}"
        super().__init__()


class RepositoryNoUpstreamError(RepositoryServiceError):
    code = "REPOSITORY_UPSTREAM_MISSING"

    def __init__(self, path: str) -> None:
        self.detail = f"当前分支没有 upstream，不能更新：{path}"
        super().__init__()


class RepositoryNonFastForwardError(RepositoryServiceError):
    code = "REPOSITORY_NON_FAST_FORWARD"

    def __init__(self, path: str) -> None:
        self.detail = f"本地与 upstream 已分叉，只允许 fast-forward：{path}"
        super().__init__()


class RepositoryUpdateFailedError(RepositoryServiceError):
    code = "REPOSITORY_UPDATE_FAILED"
    detail = "Git pull --ff-only 失败，working tree 未被强制重置"


class RepositoryPipelineFileNotFoundError(RepositoryServiceError):
    code = "PIPELINE_FILE_NOT_FOUND"

    def __init__(self, path: str) -> None:
        self.detail = f"pipeline 文件不存在：{path}"
        super().__init__()


class RepositoryPipelineFileInvalidPathError(RepositoryServiceError):
    code = "PIPELINE_FILE_PATH_INVALID"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__()


class RepositoryService:
    def __init__(self, store: StateStore, command_runner: CommandRunner | None = None) -> None:
        self._store = store
        self._command_runner = command_runner or SubprocessRunner()

    def import_repository(self, request: RepositoryImportRequest) -> RepositoryRecord:
        requested_path = self._resolve_existing_directory(Path(request.path))
        root = self._repository_root(requested_path)
        return self._register_root(root, pipeline_file=request.pipeline_file)

    def clone_repository(self, request: RepositoryCloneRequest) -> RepositoryRecord:
        self._validate_remote_url(request.url)
        parent = self._resolve_existing_directory(Path(request.destination_parent))
        directory_name = request.directory_name or self._infer_directory_name(request.url)
        self._validate_directory_name(directory_name)
        destination = parent / directory_name
        existing = self._store.get_repository_by_path(destination)
        if existing is not None:
            return self._register_root(
                self._repository_root(destination), existing, request.pipeline_file
            )
        if destination.exists():
            raise RepositoryDestinationExistsError(str(destination))

        temporary = parent / f"{_CLONE_TEMP_PREFIX}{uuid4().hex}"
        self._assert_owned_temporary_path(temporary, parent)
        clone_arguments: tuple[str, ...] = ("git", "clone")
        if request.branch is not None:
            clone_arguments = (*clone_arguments, "--branch", request.branch)
        clone_arguments = (*clone_arguments, "--", request.url, temporary.name)
        try:
            result = self._command_runner.run(clone_arguments, cwd=parent)
            if result.return_code != 0:
                raise RepositoryCloneFailedError()
            root = self._repository_root(temporary)
            if root != temporary.resolve():
                raise RepositoryCloneBoundaryError()
            if destination.exists():
                raise RepositoryDestinationExistsError(str(destination))
            temporary.rename(destination)
        except Exception:
            self._remove_owned_temporary(temporary, parent)
            raise
        return self._register_root(destination.resolve())

    def update_repository(self, repository_id: str) -> RepositoryRecord:
        stored = self._store.get_repository(repository_id)
        if stored is None:
            raise RepositoryNotFoundError(repository_id)
        root = self._repository_root(self._resolve_existing_directory(Path(stored.path)))
        self._assert_update_preconditions(root)
        pull = self._command_runner.run(("git", "pull", "--ff-only"), cwd=root)
        if pull.return_code != 0:
            ancestry = self._command_runner.run(
                ("git", "merge-base", "--is-ancestor", "HEAD", "@{upstream}"), cwd=root
            )
            if ancestry.return_code != 0:
                raise RepositoryNonFastForwardError(str(root))
            raise RepositoryUpdateFailedError()
        return self._register_root(root, stored)

    def register_worktree(self, root: Path) -> RepositoryRecord:
        """把平台创建的 git worktree 注册为独立 checkout。"""
        resolved = self._repository_root(self._resolve_existing_directory(root))
        return self._register_root(resolved)

    def checkout_ref(self, repository_id: str, ref: str) -> RepositoryRecord:
        """切换 checkout 到目标 branch/tag：fetch 后 checkout；dirty worktree 阻断。"""
        stored = self._store.get_repository(repository_id)
        if stored is None:
            raise RepositoryNotFoundError(repository_id)
        root = self._repository_root(self._resolve_existing_directory(Path(stored.path)))
        self._assert_update_preconditions(root)
        fetch = self._command_runner.run(("git", "fetch", "--prune"), cwd=root)
        if fetch.return_code != 0:
            raise RepositoryUpdateFailedError()
        local_branch = self._command_runner.run(
            ("git", "show-ref", "--verify", "--quiet", f"refs/heads/{ref}"), cwd=root
        )
        if local_branch.return_code == 0:
            checkout = self._command_runner.run(("git", "checkout", ref), cwd=root)
        else:
            remote_branch = self._command_runner.run(
                ("git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{ref}"),
                cwd=root,
            )
            if remote_branch.return_code == 0:
                checkout = self._command_runner.run(
                    ("git", "checkout", "-B", ref, f"origin/{ref}"), cwd=root
                )
            else:
                checkout = self._command_runner.run(("git", "checkout", ref), cwd=root)
        if checkout.return_code != 0:
            raise RepositoryCheckoutFailedError(ref, str(root))
        return self._register_root(root, stored)

    # ---- pipeline 文件可配置化:仓库内任意 YAML 均可作为本地管道文件 ----

    def list_pipeline_files(self, repository_id: str) -> tuple[str, ...]:
        """扫描仓库内的 .yml/.yaml 候选文件(排除 .git 与常见依赖目录)。"""
        stored = self._store.get_repository(repository_id)
        if stored is None:
            raise RepositoryNotFoundError(repository_id)
        root = Path(stored.path)
        candidates: list[str] = []
        for candidate in root.rglob("*"):
            if candidate.is_dir() or candidate.suffix.lower() not in (".yml", ".yaml"):
                continue
            rel = candidate.relative_to(root).as_posix()
            if self._is_ignored_pipeline_path(rel):
                continue
            candidates.append(rel)
        return tuple(sorted(candidates))

    def read_pipeline_file(self, repository_id: str, path: str) -> str:
        stored = self._store.get_repository(repository_id)
        if stored is None:
            raise RepositoryNotFoundError(repository_id)
        target = self._resolve_pipeline_path(Path(stored.path), path)
        if not target.is_file():
            raise RepositoryPipelineFileNotFoundError(path)
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RepositoryPipelineFileInvalidPathError(f"无法读取 {path}:{exc}") from exc

    def write_pipeline_file(self, repository_id: str, path: str, content: str) -> RepositoryRecord:
        """把内容写回项目目录(可新建);同时把该文件设为当前 pipeline 文件。"""
        stored = self._store.get_repository(repository_id)
        if stored is None:
            raise RepositoryNotFoundError(repository_id)
        root = Path(stored.path)
        target = self._resolve_pipeline_path(root, path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise RepositoryPipelineFileInvalidPathError(f"无法写入 {path}:{exc}") from exc
        return self._register_root(root, stored, pipeline_file=path)

    def select_pipeline_file(self, repository_id: str, pipeline_file: str) -> RepositoryRecord:
        """切换当前 pipeline 文件(必须已存在于仓库内)。"""
        stored = self._store.get_repository(repository_id)
        if stored is None:
            raise RepositoryNotFoundError(repository_id)
        root = Path(stored.path)
        target = self._resolve_pipeline_path(root, pipeline_file)
        if not target.is_file():
            raise RepositoryPipelineFileNotFoundError(pipeline_file)
        return self._register_root(root, stored, pipeline_file=pipeline_file)

    @staticmethod
    def _resolve_pipeline_path(root: Path, rel: str) -> Path:
        """相对路径安全解析:拒绝绝对路径/跳出仓库/写入 .git。"""
        normalized = rel.replace("\\", "/")
        if normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
            raise RepositoryPipelineFileInvalidPathError(f"pipeline 文件必须是仓库内相对路径:{rel}")
        candidate = (root / normalized).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise RepositoryPipelineFileInvalidPathError(
                f"pipeline 文件不能超出仓库目录:{rel}"
            ) from exc
        if candidate == root.resolve() or ".git" in candidate.relative_to(root.resolve()).parts:
            raise RepositoryPipelineFileInvalidPathError(f"pipeline 文件不能位于 .git 下:{rel}")
        return candidate

    @staticmethod
    def _is_ignored_pipeline_path(rel: str) -> bool:
        first = rel.split("/", 1)[0]
        if first == ".git":
            return True
        ignored_dirs = {
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".tox",
            ".eggs",
            "dist",
            "build",
        }
        return first in ignored_dirs

    def _register_root(
        self,
        root: Path,
        existing: RepositoryRecord | None = None,
        pipeline_file: str | None = None,
    ) -> RepositoryRecord:
        current = existing or self._store.get_repository_by_path(root)
        record = self._describe(root, current, pipeline_file=pipeline_file)
        return self._store.upsert_repository(record)

    @staticmethod
    def _resolve_existing_directory(path: Path) -> Path:
        try:
            resolved = path.expanduser().resolve(strict=True)
        except OSError as error:
            raise RepositoryPathUnavailableError(str(path)) from error
        if not resolved.is_dir():
            raise RepositoryPathUnavailableError(str(resolved))
        return resolved

    def _repository_root(self, path: Path) -> Path:
        result = self._command_runner.run(("git", "rev-parse", "--show-toplevel"), cwd=path)
        if result.return_code != 0 or not result.stdout:
            raise RepositoryNotGitError(str(path))
        try:
            return Path(result.stdout).resolve(strict=True)
        except OSError as error:
            raise RepositoryGitInspectError(str(path)) from error

    def _describe(
        self, root: Path, existing: RepositoryRecord | None, pipeline_file: str | None = None
    ) -> RepositoryRecord:
        head = self._required_git_output(root, ("git", "rev-parse", "HEAD"))
        branch_result = self._command_runner.run(
            ("git", "symbolic-ref", "--quiet", "--short", "HEAD"), cwd=root
        )
        branch = branch_result.stdout if branch_result.return_code == 0 else "detached"
        status = self._command_runner.run(("git", "status", "--porcelain"), cwd=root)
        if status.return_code != 0:
            raise RepositoryGitInspectError(str(root))
        origin_result = self._command_runner.run(("git", "remote", "get-url", "origin"), cwd=root)
        origin_url = origin_result.stdout if origin_result.return_code == 0 else None
        if origin_url is not None:
            # 既有 checkout 的 origin 是用户既有配置:剥掉内嵌凭据后登记,
            # 既避免 Secret 进 SQLite/日志,也不因历史克隆方式阻断导入。
            origin_url = self._sanitize_origin(origin_url)
        upstream_result = self._command_runner.run(
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
            cwd=root,
        )
        upstream = upstream_result.stdout if upstream_result.return_code == 0 else None
        now = datetime.now(UTC)
        return RepositoryRecord(
            id=existing.id if existing is not None else uuid4().hex,
            name=root.name,
            path=str(root),
            pipeline_file=pipeline_file
            or (existing.pipeline_file if existing is not None else ".gitlab-ci.yml"),
            origin_url=origin_url,
            branch=branch,
            head_sha=head,
            upstream=upstream,
            dirty=bool(status.stdout),
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )

    def _required_git_output(self, root: Path, argv: tuple[str, ...]) -> str:
        result = self._command_runner.run(argv, cwd=root)
        if result.return_code != 0 or not result.stdout:
            raise RepositoryGitInspectError(str(root))
        return result.stdout

    def _assert_update_preconditions(self, root: Path) -> None:
        status = self._command_runner.run(("git", "status", "--porcelain"), cwd=root)
        if status.return_code != 0:
            raise RepositoryGitInspectError(str(root))
        if status.stdout:
            raise RepositoryDirtyError(str(root))
        branch = self._command_runner.run(
            ("git", "symbolic-ref", "--quiet", "--short", "HEAD"), cwd=root
        )
        if branch.return_code != 0 or not branch.stdout:
            raise RepositoryDetachedError(str(root))
        upstream = self._command_runner.run(
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
            cwd=root,
        )
        if upstream.return_code != 0 or not upstream.stdout:
            raise RepositoryNoUpstreamError(str(root))

    @staticmethod
    def _sanitize_origin(url: str) -> str:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(url)
        if parts.scheme in {"http", "https"} and "@" in (parts.netloc or ""):
            host = parts.netloc.rsplit("@", 1)[1]
            return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
        return url

    @staticmethod
    def _validate_remote_url(url: str) -> None:
        parsed = urlsplit(url)
        if parsed.password is not None:
            raise RepositoryUrlUserinfoError()
        if parsed.username is not None and not (
            parsed.scheme == "ssh" and parsed.username == "git"
        ):
            raise RepositoryUrlUserinfoError()
        scp_identity = _SCP_USERINFO_PATTERN.match(url)
        if scp_identity is not None and scp_identity.group(1) != "git":
            raise RepositoryUrlUserinfoError()

    @staticmethod
    def _infer_directory_name(url: str) -> str:
        parsed = urlsplit(url)
        raw_name = PurePosixPath(parsed.path).name if parsed.path else Path(url).name
        name = raw_name.removesuffix(".git")
        if not name:
            raise RepositoryDirectoryNameInferenceError()
        return name

    @staticmethod
    def _validate_directory_name(name: str) -> None:
        if name in {".", ".."} or "/" in name or "\\" in name or Path(name).name != name:
            raise RepositoryDirectoryNameInvalidError()

    @staticmethod
    def _assert_owned_temporary_path(temporary: Path, parent: Path) -> None:
        resolved_parent = parent.resolve()
        resolved_temporary = temporary.resolve()
        if resolved_temporary.parent != resolved_parent or not resolved_temporary.name.startswith(
            _CLONE_TEMP_PREFIX
        ):
            raise RepositoryCloneBoundaryError()

    @classmethod
    def _remove_owned_temporary(cls, temporary: Path, parent: Path) -> None:
        cls._assert_owned_temporary_path(temporary, parent)
        if temporary.exists():
            shutil.rmtree(temporary)
