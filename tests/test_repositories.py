import subprocess
from pathlib import Path

import pytest

from pipedeck.contracts import RepositoryCloneRequest, RepositoryImportRequest
from pipedeck.processes import CommandResult
from pipedeck.repositories import (
    RepositoryCloneFailedError,
    RepositoryDetachedError,
    RepositoryDirtyError,
    RepositoryNonFastForwardError,
    RepositoryNotGitError,
    RepositoryNoUpstreamError,
    RepositoryService,
    RepositoryUrlUserinfoError,
)
from pipedeck.state_store import StateStore


def _git(cwd: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=cwd,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=20,
    )
    return completed.stdout.strip()


def _create_repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "--initial-branch=main")
    _git(path, "config", "user.email", "local-tests@example.com")
    _git(path, "config", "user.name", "Pipedeck Tests")
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "initial")
    return path


def _create_remote_pair(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    seed = _create_repository(tmp_path / "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "--set-upstream", "origin", "main")
    return origin, seed


def _clone(service: RepositoryService, origin: Path, parent: Path):
    return service.clone_repository(
        RepositoryCloneRequest(
            url=str(origin),
            destination_parent=str(parent),
            directory_name="checkout",
        )
    )


def test_import_is_idempotent_by_canonical_git_root(tmp_path: Path) -> None:
    repository = _create_repository(tmp_path / "repository")
    nested = repository / "src"
    nested.mkdir()
    store = StateStore(tmp_path / "state.db")
    service = RepositoryService(store)

    first = service.import_repository(RepositoryImportRequest(path=str(repository)))
    second = service.import_repository(RepositoryImportRequest(path=str(nested / ".")))

    assert second.id == first.id
    assert second.path == str(repository.resolve())
    assert second.head_sha == _git(repository, "rev-parse", "HEAD")
    assert store.list_repositories() == (second,)
    store.close()


def test_import_rejects_non_git_directory(tmp_path: Path) -> None:
    directory = tmp_path / "plain"
    directory.mkdir()
    store = StateStore(tmp_path / "state.db")
    service = RepositoryService(store)

    with pytest.raises(RepositoryNotGitError) as error:
        service.import_repository(RepositoryImportRequest(path=str(directory)))
    assert error.value.code == "REPOSITORY_NOT_GIT"
    store.close()


def test_clone_rejects_url_userinfo_before_creating_directory(tmp_path: Path) -> None:
    destination = tmp_path / "checkouts"
    destination.mkdir()
    store = StateStore(tmp_path / "state.db")
    service = RepositoryService(store)

    with pytest.raises(RepositoryUrlUserinfoError):
        service.clone_repository(
            RepositoryCloneRequest(
                url="https://user:token@example.com/team/repository.git",
                destination_parent=str(destination),
            )
        )
    assert tuple(destination.iterdir()) == ()
    store.close()


class FailedSshCloneRunner:
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult:
        assert argv[0:2] == ("git", "clone")
        return CommandResult(return_code=1, stdout="", stderr="unreachable")


def test_clone_allows_standard_git_ssh_identity(tmp_path: Path) -> None:
    destination = tmp_path / "checkouts"
    destination.mkdir()
    store = StateStore(tmp_path / "state.db")
    service = RepositoryService(store, FailedSshCloneRunner())

    with pytest.raises(RepositoryCloneFailedError):
        service.clone_repository(
            RepositoryCloneRequest(
                url="git@gitlab.example.test:pipedeck/supplier.git",
                destination_parent=str(destination),
            )
        )

    assert tuple(destination.iterdir()) == ()
    store.close()


def test_clone_moves_complete_checkout_and_cleans_temporary_directory(
    tmp_path: Path,
) -> None:
    origin, _ = _create_remote_pair(tmp_path)
    destination = tmp_path / "checkouts"
    destination.mkdir()
    store = StateStore(tmp_path / "state.db")
    service = RepositoryService(store)

    record = _clone(service, origin, destination)

    checkout = destination / "checkout"
    assert record.path == str(checkout.resolve())
    assert (checkout / ".git").is_dir()
    assert (checkout / "README.md").read_text(encoding="utf-8") == "initial\n"
    assert not any(path.name.startswith(".pipedeck-clone-") for path in destination.iterdir())
    assert _clone(service, origin, destination).id == record.id
    store.close()


def test_failed_clone_removes_only_owned_temporary_directory(tmp_path: Path) -> None:
    destination = tmp_path / "checkouts"
    destination.mkdir()
    keep = destination / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    store = StateStore(tmp_path / "state.db")
    service = RepositoryService(store)

    with pytest.raises(RepositoryCloneFailedError) as error:
        service.clone_repository(
            RepositoryCloneRequest(
                url=str(tmp_path / "missing.git"),
                destination_parent=str(destination),
                directory_name="checkout",
            )
        )
    assert error.value.code == "REPOSITORY_CLONE_FAILED"
    assert keep.read_text(encoding="utf-8") == "keep"
    assert not (destination / "checkout").exists()
    assert not any(path.name.startswith(".pipedeck-clone-") for path in destination.iterdir())
    store.close()


def test_update_fast_forwards_to_upstream(tmp_path: Path) -> None:
    origin, seed = _create_remote_pair(tmp_path)
    destination = tmp_path / "checkouts"
    destination.mkdir()
    store = StateStore(tmp_path / "state.db")
    service = RepositoryService(store)
    record = _clone(service, origin, destination)
    checkout = Path(record.path)
    (seed / "remote.txt").write_text("remote\n", encoding="utf-8")
    _git(seed, "add", "remote.txt")
    _git(seed, "commit", "-m", "remote change")
    _git(seed, "push")

    updated = service.update_repository(record.id)

    assert updated.head_sha == _git(seed, "rev-parse", "HEAD")
    assert (checkout / "remote.txt").read_text(encoding="utf-8") == "remote\n"
    store.close()


def test_update_blocks_dirty_checkout_without_modifying_files(tmp_path: Path) -> None:
    origin, _ = _create_remote_pair(tmp_path)
    destination = tmp_path / "checkouts"
    destination.mkdir()
    store = StateStore(tmp_path / "state.db")
    service = RepositoryService(store)
    record = _clone(service, origin, destination)
    checkout = Path(record.path)
    dirty = checkout / "local.txt"
    dirty.write_text("local\n", encoding="utf-8")

    with pytest.raises(RepositoryDirtyError):
        service.update_repository(record.id)
    assert dirty.read_text(encoding="utf-8") == "local\n"
    store.close()


def test_update_blocks_detached_checkout(tmp_path: Path) -> None:
    origin, _ = _create_remote_pair(tmp_path)
    destination = tmp_path / "checkouts"
    destination.mkdir()
    store = StateStore(tmp_path / "state.db")
    service = RepositoryService(store)
    record = _clone(service, origin, destination)
    checkout = Path(record.path)
    _git(checkout, "checkout", "--detach")

    with pytest.raises(RepositoryDetachedError):
        service.update_repository(record.id)
    store.close()


def test_update_blocks_branch_without_upstream(tmp_path: Path) -> None:
    repository = _create_repository(tmp_path / "repository")
    store = StateStore(tmp_path / "state.db")
    service = RepositoryService(store)
    record = service.import_repository(RepositoryImportRequest(path=str(repository)))

    with pytest.raises(RepositoryNoUpstreamError):
        service.update_repository(record.id)
    store.close()


def test_update_blocks_diverged_history_without_reset_or_clean(tmp_path: Path) -> None:
    origin, seed = _create_remote_pair(tmp_path)
    destination = tmp_path / "checkouts"
    destination.mkdir()
    store = StateStore(tmp_path / "state.db")
    service = RepositoryService(store)
    record = _clone(service, origin, destination)
    checkout = Path(record.path)
    _git(checkout, "config", "user.email", "local-tests@example.com")
    _git(checkout, "config", "user.name", "Pipedeck Tests")
    (checkout / "local.txt").write_text("local\n", encoding="utf-8")
    _git(checkout, "add", "local.txt")
    _git(checkout, "commit", "-m", "local change")
    local_head = _git(checkout, "rev-parse", "HEAD")
    (seed / "remote.txt").write_text("remote\n", encoding="utf-8")
    _git(seed, "add", "remote.txt")
    _git(seed, "commit", "-m", "remote change")
    _git(seed, "push")

    with pytest.raises(RepositoryNonFastForwardError):
        service.update_repository(record.id)

    assert _git(checkout, "rev-parse", "HEAD") == local_head
    assert (checkout / "local.txt").read_text(encoding="utf-8") == "local\n"
    assert _git(checkout, "status", "--porcelain") == ""
    store.close()
