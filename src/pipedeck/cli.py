"""Pipedeck CLI：控制 API 的 headless 消费者（GUI=CLI 壳原则的 CLI 侧）。

- `pipedeck serve` 启动本地控制面，并写入 `cli-token` 供其余子命令互信（Netlify 模式）。
- `pipedeck run <repository_id> --wait` 完成"预览→计划→运行→跟随日志"的完整闭环，
  退出码与 pipeline 结果一致（本地非零退出 = CI 失败同语义）。
- Secret 值只从 stdin 读取，永不进入 argv/shell history。
"""

# CLI 消费开放 JSON（控制 API 的响应仅在此展示层解引用），未知类型传播在本层降级。
# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

API_VERSION = "api/v1"
DEFAULT_BASE_URL = "http://127.0.0.1:7421"


def cli_token_path(state_dir: Path) -> Path:
    return state_dir / "cli-token"


def read_cli_token(state_dir: Path) -> str | None:
    path = cli_token_path(state_dir)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


class ApiClient:
    """极简 loopback HTTP 客户端：标准库实现，读写请求自动携带 cli-token。"""

    def __init__(self, base_url: str, token: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{API_VERSION}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)  # noqa: S310
        request.add_header("accept", "application/json")
        if data is not None:
            request.add_header("content-type", "application/json")
        if self._token:
            request.add_header("x-pipedeck-token", self._token)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            payload: Any = None
            try:
                payload = json.loads(error.read().decode("utf-8"))
            except (ValueError, OSError):
                payload = None
            raise CliApiError(error.code, payload) from error

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, body if body is not None else {})

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    def put(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("PUT", path, body)


class CliApiError(RuntimeError):
    def __init__(self, status: int, payload: Any) -> None:
        detail = "API request failed"
        recovery = ""
        if isinstance(payload, dict) and isinstance(payload.get("detail"), dict):
            detail = str(
                payload["detail"].get("detail") or payload["detail"].get("title") or detail
            )
            recovery = str(payload["detail"].get("recovery") or "")
        super().__init__(f"HTTP {status}: {detail}" + (f" ({recovery})" if recovery else ""))
        self.status = status


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _follow_run(client: ApiClient, run_id: str, seen: set[int]) -> int:
    while True:
        run = client.get(f"/runs/{run_id}")
        events = client.get(f"/runs/{run_id}/events")
        for event in events.get("events", []):
            sequence = int(event["sequence"])
            if sequence in seen:
                continue
            seen.add(sequence)
            print(f"[{event.get('step_id') or 'run'}] {event.get('message', '')}")
        status = run.get("status")
        if status in {"succeeded", "failed", "cancelled", "interrupted"}:
            return 0 if status == "succeeded" else 1
        time.sleep(1.0)


def cmd_run(client: ApiClient, args: argparse.Namespace) -> int:
    if getattr(args, "ref", None):
        client.post(f"/repositories/{args.repository}/checkout", {"ref": args.ref})
        if not args.quiet:
            print(f"Switched checkout to {args.ref}")
    if getattr(args, "pipeline_file", None):
        client.put(
            f"/repositories/{args.repository}/pipeline-file/selection",
            {"pipeline_file": args.pipeline_file},
        )
        if not args.quiet:
            print(f"Switched pipeline file to {args.pipeline_file}")
    body: dict[str, Any] = {"fetch_includes": args.refresh}
    if getattr(args, "job", None):
        body["only_job"] = args.job
    plan = client.post(f"/repositories/{args.repository}/pipeline/plan", body)
    plan_id = plan["plan_id"]
    if not args.quiet:
        print(f"Plan generated: {plan_id}")
        for step in plan.get("steps", []):
            job = (step.get("pipeline_job") or {}).get("job", {})
            print(f"  - {step.get('title')}  stage={job.get('stage')}")
    run = client.post("/runs", {"plan_id": plan_id, "idempotency_key": f"cli-{uuid4().hex}"})
    run_id = run["id"]
    if not args.wait:
        print(f"Run created: {run_id} (use --wait to follow until completion)")
        return 0
    return _follow_run(client, run_id, set())


def _deploy_environment(client: ApiClient, args: argparse.Namespace) -> int:
    environment = _find_environment(client, args.environment)
    workspace_id = str(environment.get("workspace_id"))
    workspace = client.get(f"/workspaces/{workspace_id}")
    workspace = client.post(
        f"/workspaces/{workspace_id}/environments/{args.environment}/apply",
        {"expected_revision": workspace.get("revision", 1)},
    )
    plan = client.post(
        f"/workspaces/{workspace_id}/plans", {"expected_revision": workspace.get("revision", 1)}
    )
    if not args.quiet:
        print(f"Deployment plan generated: {plan.get('plan_id')} (ref={environment.get('ref')})")
        for step in plan.get("steps", []):
            if step.get("deployments") or step.get("pipeline_job"):
                print(f"  - {step.get('title')}")
    run = client.post(
        "/runs", {"plan_id": plan.get("plan_id"), "idempotency_key": f"cli-{uuid4().hex}"}
    )
    if not args.wait:
        print(f"Deployment run created: {run.get('id')} (use --wait to follow until completion)")
        return 0
    return _follow_run(client, str(run.get("id")), set())


def _find_environment(client: ApiClient, environment_id: str) -> dict[str, Any]:
    workspaces = client.get("/workspaces").get("workspaces", [])
    for workspace in workspaces:
        path = f"/workspaces/{workspace.get('id')}/environments"
        for env in client.get(path).get("environments", []):
            if env.get("id") == environment_id:
                return env
    raise CliApiError(
        404,
        {
            "detail": {
                "detail": f"Environment not found: {environment_id}",
                "recovery": "Confirm with pipedeck env list",
            }
        },
    )


def cmd_deploy_env(client: ApiClient, args: argparse.Namespace) -> int:
    return _deploy_environment(client, args)


def cmd_logs(client: ApiClient, args: argparse.Namespace) -> int:
    events = client.get(f"/runs/{args.run_id}/events")
    for event in events.get("events", []):
        prefix = event.get("step_id") or "run"
        print(f"{event.get('sequence', 0):>5} [{prefix}] {event.get('message', '')}")
    return 0


def cmd_status(client: ApiClient, args: argparse.Namespace) -> int:
    _print(client.get("/session"))
    return 0


def cmd_repos_list(client: ApiClient, args: argparse.Namespace) -> int:
    _print(client.get("/repositories").get("repositories", []))
    return 0


def cmd_repos_scan(client: ApiClient, args: argparse.Namespace) -> int:
    projects = client.get("/catalog/projects").get("projects", [])
    for project in projects:
        print(f"{project.get('id', ''):36} {project.get('kind', ''):12} {project.get('path', '')}")
    if not projects:
        print(
            "No projects found under the scan root; check the scan root configuration of pipedeck serve",  # noqa: E501
            file=sys.stderr,
        )
        return 1
    print(f"Found {len(projects)} project(s); register with pipedeck repos add <path>")
    return 0


def cmd_repos_add(client: ApiClient, args: argparse.Namespace) -> int:
    _print(client.post("/repositories/import", {"path": args.path}))
    return 0


def cmd_pipeline_list(client: ApiClient, args: argparse.Namespace) -> int:
    preview = client.get(
        f"/repositories/{args.repository}/pipeline" + ("?refresh=true" if args.refresh else "")
    )
    for job in preview.get("jobs", []):
        needs = ",".join(n.get("job", "") for n in (job.get("needs") or []))
        state = "blocked" if job.get("unsupported") else job.get("when", "on_success")
        print(f"{job.get('name', ''):40} stage={job.get('stage', ''):10} needs={needs:20} {state}")
    for issue in preview.get("blockers", []):
        print(f"Blocked by {issue.get('code')}: {issue.get('detail')}", file=sys.stderr)
    return 0 if preview.get("ready") else 1


def cmd_env_add(client: ApiClient, args: argparse.Namespace) -> int:
    payload = {"ref": args.ref}
    if args.repository:
        payload["repository_id"] = args.repository
    _print(client.post(f"/workspaces/{args.workspace}/environments", payload))
    return 0


def cmd_env_list(client: ApiClient, args: argparse.Namespace) -> int:
    _print(client.get(f"/workspaces/{args.workspace}/environments").get("environments", []))
    return 0


def cmd_env_remove(client: ApiClient, args: argparse.Namespace) -> int:
    _print(client.delete(f"/environments/{args.environment}"))
    return 0


def cmd_secrets_set(client: ApiClient, args: argparse.Namespace) -> int:
    value = sys.stdin.readline().rstrip("\r\n")
    if not value:
        print("No Secret value read from stdin", file=sys.stderr)
        return 2
    try:
        existing = client.get("/secrets").get("secrets", [])
    except CliApiError:
        existing = []
    matched = next((s for s in existing if s.get("name") == args.name), None)
    if matched is None:
        _print(client.post("/secrets", {"name": args.name, "value": value}))
    else:
        _print(
            client.request(
                "PUT",
                f"/secrets/{matched['id']}",
                {"expected_version": matched["version"], "value": value},
            )
        )
    return 0


def cmd_secrets_delete(client: ApiClient, args: argparse.Namespace) -> int:
    secrets = client.get("/secrets").get("secrets", [])
    matched = next((s for s in secrets if s.get("name") == args.name), None)
    if matched is None:
        print(f"Credential not found: {args.name}", file=sys.stderr)
        return 2
    client.request("DELETE", f"/secrets/{matched['id']}?expected_version={matched['version']}")
    print(f"Credential deleted: {args.name}")
    return 0


def cmd_secrets_list(client: ApiClient, args: argparse.Namespace) -> int:
    _print(client.get("/secrets").get("secrets", []))
    return 0


def cmd_doctor(client: ApiClient, args: argparse.Namespace) -> int:
    checks = 0
    session = client.get("/session")
    print(f"Control plane: v{session.get('version')} write={session.get('write_enabled')}")
    checks += 1
    print(f"Repositories: {len(client.get('/repositories').get('repositories', []))} registered")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipedeck", description="Pipedeck local CI/CD workbench CLI"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Control API base URL")
    parser.add_argument(
        "--state-dir", default=None, help="State directory (default %%LOCALAPPDATA%%/Pipedeck)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the local control plane")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    status = sub.add_parser("status", help="Show session and control plane status")
    status.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", help="Run dependency and connectivity checks")
    doctor.set_defaults(func=cmd_doctor)

    repos = sub.add_parser("repos", help="Manage repositories")
    repos_sub = repos.add_subparsers(dest="repos_command", required=True)
    repos_list = repos_sub.add_parser("list", help="List registered repositories")
    repos_list.set_defaults(func=cmd_repos_list)
    repos_add = repos_sub.add_parser("add", help="Import a local repository directory")
    repos_add.add_argument("path")
    repos_add.set_defaults(func=cmd_repos_add)
    repos_scan = repos_sub.add_parser("scan", help="Scan local project directories")
    repos_scan.set_defaults(func=cmd_repos_scan)

    deploy = sub.add_parser("deploy", help="Deploy the workspace of a worktree environment")
    deploy.add_argument("environment")
    deploy.add_argument(
        "--wait", action="store_true", help="Follow the deployment run until completion"
    )
    deploy.add_argument("--quiet", action="store_true")
    deploy.set_defaults(func=cmd_deploy_env)

    pipeline = sub.add_parser("pipeline", help="GitLab CI pipeline")
    pipeline_sub = pipeline.add_subparsers(dest="pipeline_command", required=True)
    pipeline_list = pipeline_sub.add_parser("list", help="Preview pipeline jobs")
    pipeline_list.add_argument("repository")
    pipeline_list.add_argument(
        "--refresh", action="store_true", help="Force refresh of include cache"
    )
    pipeline_list.set_defaults(func=cmd_pipeline_list)

    run = sub.add_parser("run", help="Generate a plan and run the repository pipeline")
    run.add_argument("repository")
    run.add_argument("--refresh", action="store_true")
    run.add_argument(
        "--job", default=None, help="Run only the specified job and its needs dependency chain"
    )
    run.add_argument(
        "--ref", default=None, help="Switch checkout to this branch/tag before running"
    )
    run.add_argument(
        "--pipeline-file",
        default=None,
        help="Use this relative path as the pipeline definition (default: .gitlab-ci.yml)",
    )
    run.add_argument("--wait", action="store_true", help="Follow the run until completion")
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(func=cmd_run)

    logs = sub.add_parser("logs", help="View run logs")
    logs.add_argument("run_id")
    logs.set_defaults(func=cmd_logs)

    env = sub.add_parser("env", help="Worktree co-existing environments")
    env_sub = env.add_subparsers(dest="env_command", required=True)
    env_add = env_sub.add_parser("add", help="Create a ref environment for a workspace")
    env_add.add_argument("workspace")
    env_add.add_argument("ref")
    env_add.add_argument(
        "--repository", help="Project checkout ID (required for multi-project workspaces)"
    )
    env_add.set_defaults(func=cmd_env_add)
    env_list = env_sub.add_parser("list", help="List workspace environments")
    env_list.add_argument("workspace")
    env_list.set_defaults(func=cmd_env_list)
    env_remove = env_sub.add_parser(
        "remove", help="Remove an environment (blocked when the worktree has dirty changes)"
    )
    env_remove.add_argument("environment")
    env_remove.set_defaults(func=cmd_env_remove)

    secrets = sub.add_parser("secrets", help="Manage credentials")
    secrets_sub = secrets.add_subparsers(dest="secrets_command", required=True)
    secrets_set = secrets_sub.add_parser(
        "set", help="Write/update a credential (value read from stdin)"
    )
    secrets_set.add_argument("name")
    secrets_set.set_defaults(func=cmd_secrets_set)
    secrets_list = secrets_sub.add_parser("list", help="List credentials (presence only)")
    secrets_list.set_defaults(func=cmd_secrets_list)
    secrets_delete = secrets_sub.add_parser("delete", help="Delete a credential")
    secrets_delete.add_argument("name")
    secrets_delete.set_defaults(func=cmd_secrets_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        from pipedeck.__main__ import serve

        serve(host=args.host, port=args.port)
        return 0
    state_dir = Path(args.state_dir) if args.state_dir else _default_state_dir()
    token = read_cli_token(state_dir)
    client = ApiClient(args.base_url, token)
    try:
        return int(args.func(client, args))
    except CliApiError as error:
        print(str(error), file=sys.stderr)
        return 2
    except urllib.error.URLError as error:
        print(
            f"Cannot connect to control plane ({args.base_url}): {error.reason}; run pipedeck serve first",  # noqa: E501
            file=sys.stderr,
        )
        return 2


def _default_state_dir() -> Path:
    settings = _load_settings()
    return settings.state_db_path.parent


def _load_settings():
    from pipedeck.settings import LocalSettings

    return LocalSettings()


if __name__ == "__main__":
    raise SystemExit(main())
