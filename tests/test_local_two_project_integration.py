from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from urllib.request import urlopen

from pipedeck.catalog import ProjectCatalog
from pipedeck.contracts import (
    ArgumentPortInjection,
    HostEndpoint,
    HostTarget,
    HttpReadiness,
    PlanStepKind,
    RepositoryCloneRequest,
    RunMode,
    RunStatus,
    ServiceCommand,
    WorkspaceInput,
    WorkspaceService,
)
from pipedeck.control_plane import (
    ConnectionAwareWorkspacePlanner,
    CurrentPlanFreshnessValidator,
    SecretService,
    StorePlanLoader,
    WorkspaceEnvironmentResolver,
    WorkspaceReadinessResolver,
)
from pipedeck.execution import ExecutionEngine
from pipedeck.planning import ConnectionPlanner
from pipedeck.processes import CommandResult, SubprocessRunner
from pipedeck.readiness import ReadinessProbeRunner
from pipedeck.repositories import RepositoryService
from pipedeck.runtime import DockerRuntime
from pipedeck.state_store import StateStore
from pipedeck.workspace_planning import SavedWorkspacePlanner

_SERVER_SOURCE = """\
import argparse
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("--token", required=True)
parser.add_argument("--port", required=True, type=int)
arguments = parser.parse_args()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = arguments.token.encode("utf-8")
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message, *values):
        print(f"HTTP {arguments.token} {message % values}", flush=True)


child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
print(f"SERVER {arguments.token} {os.getpid()} {child.pid}", flush=True)
server = ThreadingHTTPServer(("127.0.0.1", arguments.port), Handler)
try:
    server.serve_forever()
finally:
    child.terminate()
"""


class DockerUnavailableRunner:
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult:
        del cwd
        assert argv[:2] == ("docker", "container")
        return CommandResult(return_code=1, stdout="", stderr="Docker intentionally unavailable")


class UnusedCredentialStore:
    def write(self, secret_id: str, value: str) -> None:
        del secret_id, value
        raise AssertionError("credential store must not be used")

    def read(self, secret_id: str) -> str:
        del secret_id
        raise AssertionError("credential store must not be used")

    def delete(self, secret_id: str) -> None:
        del secret_id
        raise AssertionError("credential store must not be used")


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


def _create_remote(tmp_path: Path, name: str) -> Path:
    remote = tmp_path / f"{name}.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "--initial-branch=main")

    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    _git(seed, "config", "user.email", "local-integration@example.com")
    _git(seed, "config", "user.name", "Pipedeck Integration")
    (seed / "server.py").write_text(_SERVER_SOURCE, encoding="utf-8")
    (seed / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    _git(seed, "add", "server.py", "README.md")
    _git(seed, "commit", "-m", "seed local HTTP service")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "--set-upstream", "origin", "main")
    return remote


def _free_ports(count: int) -> tuple[int, ...]:
    reservations: list[socket.socket] = []
    try:
        for _ in range(count):
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reservation.bind(("127.0.0.1", 0))
            reservations.append(reservation)
        return tuple(int(item.getsockname()[1]) for item in reservations)
    finally:
        for reservation in reservations:
            reservation.close()


def _workspace_service(
    project_id: str,
    token: str,
    port: int,
) -> WorkspaceService:
    return WorkspaceService(
        project_id=project_id,
        commands=(
            ServiceCommand(
                id="serve",
                label=f"Start {token}",
                kind=PlanStepKind.START,
                argv=(sys.executable, "server.py", "--token", token),
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
            readiness_timeout=10,
            stop_timeout=3,
        ),
    )


def _wait_until(predicate: Callable[[], bool], timeout: float = 12) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition was not met before timeout")


def _process_exists(pid: int) -> bool:
    if sys.platform == "win32":
        completed = subprocess.run(
            ("tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return f'"{pid}"' in completed.stdout
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        with suppress(OSError, IndexError):
            if proc_stat.read_text(encoding="utf-8").split()[2] == "Z":
                return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _tcp_connects(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _force_cleanup_process_tree(parent_pid: int) -> None:
    if not _process_exists(parent_pid):
        return
    if sys.platform == "win32":
        subprocess.run(
            ("taskkill", "/PID", str(parent_pid), "/T", "/F"),
            check=False,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return
    with suppress(ProcessLookupError):
        os.killpg(os.getpgid(parent_pid), signal.SIGKILL)


def _server_processes(store: StateStore, run_id: str) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for event in store.list_events(run_id).events:
        if not event.message.startswith("SERVER "):
            continue
        _, token, parent_pid, child_pid = event.message.split()
        result[token] = (int(parent_pid), int(child_pid))
    return result


def test_real_git_clone_two_project_workspace_run_and_persisted_cancel(tmp_path: Path) -> None:
    state_path = tmp_path / "state.db"
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()
    backend_remote = _create_remote(tmp_path, "backend")
    frontend_remote = _create_remote(tmp_path, "frontend")

    store = StateStore(state_path)
    engine: ExecutionEngine | None = None
    run_id: str | None = None
    observed_processes: dict[str, tuple[int, int]] = {}
    reopened: StateStore | None = None
    try:
        repositories = RepositoryService(store)
        backend = repositories.clone_repository(
            RepositoryCloneRequest(
                url=str(backend_remote),
                destination_parent=str(checkouts),
                directory_name="backend",
            )
        )
        frontend = repositories.clone_repository(
            RepositoryCloneRequest(
                url=str(frontend_remote),
                destination_parent=str(checkouts),
                directory_name="frontend",
            )
        )
        assert Path(backend.path).parent == checkouts.resolve()
        assert Path(frontend.path).parent == checkouts.resolve()
        assert (Path(backend.path) / ".git").is_dir()
        assert (Path(frontend.path) / ".git").is_dir()

        backend_port, frontend_port = _free_ports(2)
        workspace = store.create_workspace(
            WorkspaceInput(
                name="Backend and frontend local integration",
                mode=RunMode.DEVELOPMENT,
                services=(
                    _workspace_service(backend.id, "backend-token", backend_port),
                    _workspace_service(frontend.id, "frontend-token", frontend_port),
                ),
            ),
            workspace_id="two-project-workspace",
        )
        command_runner = SubprocessRunner()
        catalog = ProjectCatalog(
            roots=(),
            max_depth=0,
            command_runner=command_runner,
            registered_repositories=store.list_repositories,
        )
        runtime = DockerRuntime(DockerUnavailableRunner())
        secrets = SecretService(store, UnusedCredentialStore())
        connections = ConnectionPlanner(secrets)
        planner = ConnectionAwareWorkspacePlanner(
            SavedWorkspacePlanner(command_runner),
            connections,
            secrets,
        )
        plan = planner.create(workspace, catalog.scan(), runtime.snapshot())
        assert plan.ready, tuple((issue.code, issue.detail) for issue in plan.blockers)
        assert {project.id for project in plan.projects} == {backend.id, frontend.id}
        start_commands = next(step for step in plan.steps if step.id == "start").commands
        assert {command.argv[-2:] for command in start_commands} == {
            ("--port", str(backend_port)),
            ("--port", str(frontend_port)),
        }
        persisted_plan = store.save_plan(plan)
        assert persisted_plan.plan_id is not None

        engine = ExecutionEngine(
            store=store,
            environment_resolver=WorkspaceEnvironmentResolver(
                store,
                runtime,
                secrets,
                connections,
            ),
            plan_loader=StorePlanLoader(store),
            freshness_validator=CurrentPlanFreshnessValidator(
                store,
                catalog,
                runtime,
                planner,
            ),
            readiness_resolver=WorkspaceReadinessResolver(store),
            readiness_waiter=ReadinessProbeRunner(retry_interval_seconds=0.05),
        )
        created = engine.start(persisted_plan.plan_id, "two-project-run-key")
        run_id = created.id

        def both_services_are_running() -> bool:
            current = store.get_run(created.id)
            processes = engine.list_processes().processes
            ready_projects = {
                event.project_id
                for event in store.list_events(created.id).events
                if event.message == "Readiness probe passed"
            }
            return (
                current is not None
                and current.status is RunStatus.RUNNING
                and {process.project_id for process in processes} == {backend.id, frontend.id}
                and ready_projects == {backend.id, frontend.id}
            )

        _wait_until(both_services_are_running)
        _wait_until(lambda: len(_server_processes(store, created.id)) == 2)
        observed_processes = _server_processes(store, created.id)
        runtime_processes = engine.list_processes().processes
        assert {process.project_id for process in runtime_processes} == {backend.id, frontend.id}
        assert {process.project_name for process in runtime_processes} == {"backend", "frontend"}
        assert all(process.long_running for process in runtime_processes)
        for process in runtime_processes:
            assert "--token" in process.argv
            assert "--port" in process.argv

        for token, port in (
            ("backend-token", backend_port),
            ("frontend-token", frontend_port),
        ):
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                assert response.status == 200
                assert response.read().decode("utf-8") == token

        running = store.get_run(created.id)
        assert running is not None and running.status is RunStatus.RUNNING
        running_events = store.list_events(created.id).events
        assert len(running_events) > 0
        assert {
            event.project_id for event in running_events if event.message.startswith("SERVER ")
        } == {
            backend.id,
            frontend.id,
        }

        cancelled = engine.cancel(created.id)
        assert cancelled.status is RunStatus.CANCELLED
        assert engine.wait(created.id, timeout=10).status is RunStatus.CANCELLED
        _wait_until(
            lambda: all(
                not _process_exists(pid) for pair in observed_processes.values() for pid in pair
            )
        )
        _wait_until(lambda: not _tcp_connects(backend_port) and not _tcp_connects(frontend_port))
        assert engine.list_processes().processes == ()
        persisted_events = store.list_events(created.id).events
        assert persisted_events[-1].message == "Run cancelled"

        store.close()
        reopened = StateStore(state_path)
        restored = reopened.get_run(created.id)
        assert restored is not None and restored.status is RunStatus.CANCELLED
        restored_events = reopened.list_events(created.id).events
        assert restored_events == persisted_events
        assert {
            event.project_id for event in restored_events if event.message.startswith("SERVER ")
        } == {
            backend.id,
            frontend.id,
        }
    finally:
        if engine is not None and run_id is not None:
            with suppress(Exception):
                engine.cancel(run_id)
            with suppress(Exception):
                engine.wait(run_id, timeout=10)
        for parent_pid, _ in observed_processes.values():
            _force_cleanup_process_tree(parent_pid)
        if reopened is not None:
            reopened.close()
        with suppress(Exception):
            store.close()
