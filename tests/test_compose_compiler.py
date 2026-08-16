from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from tripguru_local.compose_compiler import (
    ComposeCompilationError,
    ComposeCompilationProblem,
    ComposeCompiler,
    ComposeConfigResult,
)
from tripguru_local.compose_deployment import HttpProbe, TcpProbe, derive_compose_project_name
from tripguru_local.contracts import (
    ComposeEndpoint,
    ComposeTarget,
    DockerfileSource,
    EndpointProtocol,
    EnvironmentBinding,
    EnvironmentSource,
    ExistingComposeSource,
    HttpReadiness,
    ProjectKind,
    ProjectSummary,
    RunMode,
    TcpReadiness,
    WorkspaceRecord,
    WorkspaceService,
)

type JsonObject = dict[str, object]


SOURCE_FINGERPRINT = "a" * 64
CONFIG_FINGERPRINT = "b" * 64


@dataclass(frozen=True, slots=True)
class RunnerCall:
    argv: tuple[str, ...]
    cwd: Path


class FakeRunner:
    def __init__(self, result: ComposeConfigResult) -> None:
        self.result = result
        self.calls: list[RunnerCall] = []

    def run(self, *, argv: tuple[str, ...], cwd: Path) -> ComposeConfigResult:
        self.calls.append(RunnerCall(argv=argv, cwd=cwd))
        return self.result


def _project(checkout: Path, project_id: str = "supplier-api") -> ProjectSummary:
    return ProjectSummary(
        id=project_id,
        name="Supplier API",
        path=str(checkout),
        kind=ProjectKind.PYTHON_UV,
        branch="main",
        dirty=False,
        commands=(),
        requirements=(),
        warnings=(),
    )


def _workspace(
    project_id: str,
    target: ComposeTarget,
    environment: tuple[EnvironmentBinding, ...] = (),
) -> WorkspaceRecord:
    now = datetime.now(UTC)
    return WorkspaceRecord(
        id="workspace-main",
        revision=7,
        name="Main",
        mode=RunMode.INTEGRATED,
        services=(
            WorkspaceService(
                project_id=project_id,
                environment=environment,
                execution_target=target,
            ),
        ),
        created_at=now,
        updated_at=now,
    )


def _dockerfile_target(*, tcp: bool = False) -> ComposeTarget:
    endpoint = ComposeEndpoint(
        name="api",
        protocol=EndpointProtocol.TCP,
        host_port=43101,
        container_port=8000,
    )
    readiness = (
        TcpReadiness(kind="tcp", endpoint="api")
        if tcp
        else HttpReadiness(kind="http", endpoint="api", path="/ready")
    )
    return ComposeTarget(
        kind="compose",
        source=DockerfileSource(kind="dockerfile"),
        endpoints=(endpoint,),
        readiness=readiness,
        wait_timeout=45,
    )


def _existing_target(*, services: tuple[str, ...] = ("api", "worker")) -> ComposeTarget:
    return ComposeTarget(
        kind="compose",
        source=ExistingComposeSource(
            kind="existing-compose",
            compose_files=("compose.yml",),
            profiles=("local",),
            service_names=services,
        ),
        endpoints=(
            ComposeEndpoint(name="api", host_port=43101, container_port=8000),
            ComposeEndpoint(name="metrics", host_port=43102, container_port=9000),
        ),
        readiness=HttpReadiness(kind="http", endpoint="api", path="/ready"),
        wait_timeout=60,
    )


def _compile(
    tmp_path: Path,
    checkout: Path,
    target: ComposeTarget,
    runner: FakeRunner | None = None,
    environment: tuple[EnvironmentBinding, ...] = (),
):
    project = _project(checkout)
    return ComposeCompiler(tmp_path / "artifacts", runner).compile(
        workspace=_workspace(project.id, target, environment),
        project=project,
        target=target,
        source_fingerprint=SOURCE_FINGERPRINT,
        target_config_fingerprint=CONFIG_FINGERPRINT,
    )


def _load_frozen(path: Path) -> JsonObject:
    return cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))


def _services(document: JsonObject) -> JsonObject:
    return cast(JsonObject, document["services"])


def _service(document: JsonObject, name: str) -> JsonObject:
    return cast(JsonObject, _services(document)[name])


def test_dockerfile_compiles_single_service_with_local_ports_labels_and_http_probe(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    target = _dockerfile_target()

    intent = _compile(tmp_path, checkout, target)

    document = _load_frozen(intent.frozen_compose_path)
    service = _service(document, intent.services[0])
    build = cast(JsonObject, service["build"])
    labels = cast(JsonObject, service["labels"])
    ports = cast(list[JsonObject], service["ports"])
    assert document["name"] == derive_compose_project_name("workspace-main", "supplier-api")
    assert build == {"context": str(checkout.resolve()), "dockerfile": "Dockerfile"}
    assert "container_name" not in service
    assert labels == {
        "tripguru.local/config-fingerprint": CONFIG_FINGERPRINT,
        "tripguru.local/managed": "true",
        "tripguru.local/project": "supplier-api",
        "tripguru.local/revision": intent.revision_id,
        "tripguru.local/source-fingerprint": SOURCE_FINGERPRINT,
        "tripguru.local/target": "supplier-api",
        "tripguru.local/workspace": "workspace-main",
    }
    assert ports == [
        {
            "host_ip": "127.0.0.1",
            "mode": "ingress",
            "protocol": "tcp",
            "published": "43101",
            "target": 8000,
        }
    ]
    assert intent.target_id == "supplier-api"
    assert intent.services == (next(iter(_services(document))),)
    assert intent.immutable_images == (service["image"],)
    assert f"s{SOURCE_FINGERPRINT[:16]}" in intent.immutable_images[0]
    assert f"c{CONFIG_FINGERPRINT[:16]}" in intent.immutable_images[0]
    assert intent.revision_id.removeprefix("rev-") in intent.immutable_images[0]
    assert intent.probe == HttpProbe(url="http://127.0.0.1:43101/ready")
    assert intent.wait_timeout_seconds == 45


def test_existing_compose_uses_canonical_command_and_overrides_selected_services(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    (checkout / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    canonical = {
        "name": "ignored",
        "services": {
            "api": {
                "build": {"context": str(checkout), "dockerfile": "Dockerfile"},
                "container_name": "fixed-api",
                "environment": {"LOG_LEVEL": "debug"},
                "labels": {"owner": "project"},
                "ports": [{"target": 7000, "published": "47000"}],
            },
            "worker": {
                "build": {"context": str(checkout), "dockerfile": "Dockerfile"},
                "container_name": "fixed-worker",
            },
            "aux": {"container_name": "fixed-aux", "image": "busybox:latest"},
        },
    }
    runner = FakeRunner(ComposeConfigResult(return_code=0, stdout=json.dumps(canonical)))
    target = _existing_target()

    intent = _compile(tmp_path, checkout, target, runner)

    project_name = derive_compose_project_name("workspace-main", "supplier-api")
    assert runner.calls == [
        RunnerCall(
            argv=(
                "docker",
                "compose",
                "-f",
                str((checkout / "compose.yml").resolve()),
                "--profile",
                "local",
                "-p",
                project_name,
                "config",
                "--no-interpolate",
                "--no-env-resolution",
                "--format",
                "json",
            ),
            cwd=checkout.resolve(),
        )
    ]
    document = _load_frozen(intent.frozen_compose_path)
    api = _service(document, "api")
    worker = _service(document, "worker")
    aux = _service(document, "aux")
    assert intent.services == ("api", "worker")
    assert len(set(intent.immutable_images)) == 2
    assert "container_name" not in api
    assert "container_name" not in worker
    assert "container_name" not in aux
    assert len(cast(list[object], api["ports"])) == 2
    assert worker["ports"] == []
    assert cast(JsonObject, api["environment"])["LOG_LEVEL"] == "debug"
    assert cast(JsonObject, api["labels"])["owner"] == "project"


def test_existing_compose_rejects_missing_or_non_buildable_selected_service(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    canonical = {"services": {"api": {"image": "registry.example/api:latest"}}}
    runner = FakeRunner(ComposeConfigResult(return_code=0, stdout=json.dumps(canonical)))

    with pytest.raises(ComposeCompilationError) as missing:
        _compile(tmp_path, checkout, _existing_target(services=("missing",)), runner)
    assert missing.value.problem is ComposeCompilationProblem.SERVICE_MISSING

    with pytest.raises(ComposeCompilationError) as not_buildable:
        _compile(tmp_path, checkout, _existing_target(services=("api",)), runner)
    assert not_buildable.value.problem is ComposeCompilationProblem.SERVICE_NOT_BUILDABLE


def test_existing_compose_rejects_build_context_outside_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    outside = tmp_path / "outside"
    checkout.mkdir()
    outside.mkdir()
    (checkout / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    (outside / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    canonical = {
        "services": {"api": {"build": {"context": str(outside), "dockerfile": "Dockerfile"}}}
    }
    runner = FakeRunner(ComposeConfigResult(return_code=0, stdout=json.dumps(canonical)))

    with pytest.raises(ComposeCompilationError) as captured:
        _compile(tmp_path, checkout, _existing_target(services=("api",)), runner)

    assert captured.value.problem is ComposeCompilationProblem.PATH_OUTSIDE_CHECKOUT


@pytest.mark.parametrize(
    ("service_fragment", "problem"),
    [
        (
            {"env_file": [{"path": ".env", "required": True}]},
            ComposeCompilationProblem.ENV_FILE_UNSUPPORTED,
        ),
        (
            {"environment": {"PUBLIC_URL": "${HOST_URL:-http://localhost}"}},
            ComposeCompilationProblem.UNSAFE_PLACEHOLDER,
        ),
        (
            {"environment": {"DATABASE_URL": "${TGL_DATABASE_URL:?}"}},
            ComposeCompilationProblem.SOURCE_PLACEHOLDER_UNSUPPORTED,
        ),
        (
            {"environment": {"DATABASE_URL": "postgresql://user:plain@db/app"}},
            ComposeCompilationProblem.SENSITIVE_LITERAL,
        ),
    ],
)
def test_existing_compose_blocks_unresolved_or_sensitive_configuration(
    tmp_path: Path,
    service_fragment: JsonObject,
    problem: ComposeCompilationProblem,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    (checkout / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    service: JsonObject = {
        "build": {"context": str(checkout), "dockerfile": "Dockerfile"},
        **service_fragment,
    }
    runner = FakeRunner(
        ComposeConfigResult(return_code=0, stdout=json.dumps({"services": {"api": service}}))
    )

    with pytest.raises(ComposeCompilationError) as captured:
        _compile(tmp_path, checkout, _existing_target(services=("api",)), runner)

    assert captured.value.problem is problem


def test_compose_config_failure_does_not_expose_runner_output(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    secret = "must-not-leak"
    runner = FakeRunner(
        ComposeConfigResult(return_code=1, stdout=secret, stderr=f"password={secret}")
    )

    with pytest.raises(ComposeCompilationError) as captured:
        _compile(tmp_path, checkout, _existing_target(services=("api",)), runner)

    assert captured.value.problem is ComposeCompilationProblem.COMPOSE_CONFIG_FAILED
    assert secret not in str(captured.value)


def test_workspace_environment_is_injected_as_required_parent_placeholder(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    environment = (
        EnvironmentBinding(
            name="APP_MODE",
            source=EnvironmentSource.LITERAL,
            value="local",
        ),
    )

    intent = _compile(tmp_path, checkout, _dockerfile_target(), environment=environment)
    document = _load_frozen(intent.frozen_compose_path)

    assert _service(document, intent.services[0])["environment"] == {
        "APP_MODE": "${TGL_APP_MODE:?}"
    }
    assert '"APP_MODE":"local"' not in intent.frozen_compose_path.read_text(encoding="utf-8")


def test_tcp_readiness_and_existing_artifact_immutability(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    target = _dockerfile_target(tcp=True)

    intent = _compile(tmp_path, checkout, target)
    assert intent.probe == TcpProbe(host="127.0.0.1", port=43101)

    intent.frozen_compose_path.write_text("conflicting-content\n", encoding="utf-8")
    with pytest.raises(ComposeCompilationError) as captured:
        _compile(tmp_path, checkout, target)
    assert captured.value.problem is ComposeCompilationProblem.ARTIFACT_CONFLICT


def test_invalid_fingerprint_is_rejected_before_artifact_creation(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    target = _dockerfile_target()
    project = _project(checkout)
    compiler = ComposeCompiler(tmp_path / "artifacts")

    with pytest.raises(ComposeCompilationError) as captured:
        compiler.compile(
            workspace=_workspace(project.id, target),
            project=project,
            target=target,
            source_fingerprint="not-a-fingerprint",
            target_config_fingerprint=CONFIG_FINGERPRINT,
        )

    assert captured.value.problem is ComposeCompilationProblem.FINGERPRINT_INVALID
    assert not (tmp_path / "artifacts").exists()
