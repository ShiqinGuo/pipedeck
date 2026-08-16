from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Protocol
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from tripguru_local.compose_compiler import deployment_parent_variable
from tripguru_local.compose_deployment import (
    ComposeDeploymentRunner,
    DeploymentAction,
    DeploymentCancellation,
    DeploymentCommandResult,
    DeploymentEnvironmentResolver,
    DeploymentIntent,
    DeploymentProbe,
    DeploymentProbeResult,
    DeploymentRevision,
    DeploymentStatus,
    HttpProbe,
    ResolvedDeploymentVariable,
    RuntimeTargetState,
    TcpProbe,
)
from tripguru_local.contracts import (
    ComposeDeploymentPlan,
    EnvironmentSource,
    HttpDeploymentProbeSpec,
    RuntimeResponse,
    WorkspaceService,
)
from tripguru_local.execution import DeploymentExecutionResult
from tripguru_local.planning import ConnectionPlanner


class DeploymentRecordStore(Protocol):
    def get_revision(self, revision_id: str) -> DeploymentRevision | None: ...


class DeploymentSecretReader(Protocol):
    def read(self, secret_id: str) -> str: ...


class DeploymentRuntimeReader(Protocol):
    def invalidate(self) -> None: ...

    def snapshot(self) -> RuntimeResponse: ...


class DeploymentProbeVerifier(Protocol):
    def verify(
        self,
        probe: DeploymentProbe,
        environment: tuple[ResolvedDeploymentVariable, ...],
    ) -> DeploymentProbeResult: ...


class DeploymentWorkflow(Protocol):
    def deploy(
        self,
        intent: DeploymentIntent,
        cancellation: DeploymentCancellation,
    ) -> DeploymentRevision: ...


class SnapshotDeploymentEnvironmentResolver(DeploymentEnvironmentResolver):
    def __init__(
        self,
        runtime: DeploymentRuntimeReader,
        secrets: DeploymentSecretReader,
        connections: ConnectionPlanner,
    ) -> None:
        self._runtime = runtime
        self._secrets = secrets
        self._connections = connections

    def resolve(
        self,
        revision: DeploymentRevision,
        action: DeploymentAction,
    ) -> tuple[ResolvedDeploymentVariable, ...]:
        del action
        spec = revision.intent.environment_spec
        resolved: list[ResolvedDeploymentVariable] = []
        names: set[str] = set()
        for binding in spec.environment:
            value: str
            sensitive = False
            redaction_values: tuple[str, ...] = ()
            if binding.source is EnvironmentSource.HOST_ENV:
                if binding.reference is None or binding.reference not in os.environ:
                    raise DeploymentEnvironmentReferenceError(binding.reference or binding.name)
                value = os.environ[binding.reference]
                sensitive = True
                redaction_values = (value, quote(value, safe=""))
            elif binding.source is EnvironmentSource.SECRET_STORE:
                if binding.reference is None:
                    raise DeploymentEnvironmentReferenceError(binding.name)
                value = self._secrets.read(binding.reference)
                sensitive = True
                redaction_values = (value, quote(value, safe=""))
            elif binding.value is not None:
                value = binding.value
            else:
                raise DeploymentEnvironmentReferenceError(binding.name)
            self._append(
                resolved,
                names,
                binding.name,
                value,
                sensitive,
                redaction_values,
            )

        if spec.connection_profiles:
            self._runtime.invalidate()
            runtime = self._runtime.snapshot()
            service = WorkspaceService(
                project_id=revision.intent.target_id,
                connection_profiles=spec.connection_profiles,
            )
            for connection_value in self._connections.resolve(
                service,
                spec.bindings,
                runtime,
                consumer_host="host.docker.internal",
            ):
                self._append(
                    resolved,
                    names,
                    connection_value.name,
                    connection_value.value,
                    connection_value.sensitive,
                    connection_value.redaction_values,
                )
        return tuple(resolved)

    @staticmethod
    def _append(
        resolved: list[ResolvedDeploymentVariable],
        names: set[str],
        output_name: str,
        value: str,
        sensitive: bool,
        redaction_values: tuple[str, ...],
    ) -> None:
        parent_name = deployment_parent_variable(output_name)
        if parent_name in names:
            raise DeploymentEnvironmentConflictError(parent_name)
        names.add(parent_name)
        resolved.append(
            ResolvedDeploymentVariable(
                name=parent_name,
                value=value,
                sensitive=sensitive,
                redaction_values=redaction_values,
            )
        )


class DeploymentEnvironmentReferenceError(RuntimeError):
    def __init__(self, reference: str) -> None:
        self.reference = reference
        super().__init__()


class DeploymentEnvironmentConflictError(RuntimeError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__()


class LocalComposeDeploymentRunner(ComposeDeploymentRunner):
    def run(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        environment: tuple[ResolvedDeploymentVariable, ...],
    ) -> DeploymentCommandResult:
        # tripguru-ast: ignore[TG-DS001] - subprocess requires a concrete environment mapping.
        process_environment = os.environ.copy()
        for variable in environment:
            process_environment[variable.name] = variable.value
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=process_environment,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return DeploymentCommandResult(
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class _DockerHealth(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Annotated[str, Field(alias="Status")]


class _DockerState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    running: Annotated[bool, Field(alias="Running")]
    health: Annotated[_DockerHealth | None, Field(alias="Health")] = None


class _DockerLabels(BaseModel):
    model_config = ConfigDict(extra="ignore")

    revision: Annotated[str | None, Field(alias="tripguru.local/revision")] = None


class _DockerConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    labels: Annotated[_DockerLabels, Field(alias="Labels")]


class _DockerInspection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    config: Annotated[_DockerConfig, Field(alias="Config")]
    state: Annotated[_DockerState, Field(alias="State")]


class DockerDeploymentRuntimeInspector:
    def __init__(
        self,
        store: DeploymentRecordStore,
        runner: ComposeDeploymentRunner,
        environment_resolver: DeploymentEnvironmentResolver,
        probe_runner: DeploymentProbeVerifier,
    ) -> None:
        self._store = store
        self._runner = runner
        self._environment_resolver = environment_resolver
        self._probe_runner = probe_runner

    def inspect(self, workspace_id: str, target_id: str) -> RuntimeTargetState:
        listed = self._runner.run(
            argv=(
                "docker",
                "container",
                "ls",
                "-a",
                "--filter",
                f"label=tripguru.local/workspace={workspace_id}",
                "--filter",
                f"label=tripguru.local/target={target_id}",
                "--format",
                "{{.ID}}",
            ),
            cwd=Path.cwd(),
            environment=(),
        )
        container_ids = tuple(item.strip() for item in listed.stdout.splitlines() if item.strip())
        if listed.return_code != 0 or not container_ids:
            return RuntimeTargetState(False, None, False)
        inspected = self._runner.run(
            argv=("docker", "container", "inspect", *container_ids),
            cwd=Path.cwd(),
            environment=(),
        )
        if inspected.return_code != 0:
            return RuntimeTargetState(True, None, False)
        try:
            # tripguru-ast: ignore[TG-DS001] - Docker JSON is validated immediately.
            payload = json.loads(inspected.stdout)
            rows = TypeAdapter(tuple[_DockerInspection, ...]).validate_python(payload)
        except (json.JSONDecodeError, ValidationError):
            return RuntimeTargetState(True, None, False)
        revisions = {row.config.labels.revision for row in rows if row.config.labels.revision}
        revision_id = next(iter(revisions)) if len(revisions) == 1 else None
        containers_ready = all(
            row.state.running and (row.state.health is None or row.state.health.status == "healthy")
            for row in rows
        )
        if revision_id is None or not containers_ready:
            return RuntimeTargetState(True, revision_id, False)
        revision = self._store.get_revision(revision_id)
        if revision is None:
            return RuntimeTargetState(True, revision_id, False)
        environment = self._environment_resolver.resolve(revision, DeploymentAction.VERIFY)
        probe_result = self._probe_runner.verify(revision.intent.probe, environment)
        return RuntimeTargetState(True, revision_id, probe_result.ready)


class _CallableCancellation:
    def __init__(self, cancelled: Callable[[], bool]) -> None:
        self._cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self._cancelled()


class WorkspaceDeploymentExecutor:
    def __init__(
        self,
        store: DeploymentRecordStore,
        workflow: DeploymentWorkflow,
    ) -> None:
        self._store = store
        self._workflow = workflow

    def execute(
        self,
        deployment: ComposeDeploymentPlan,
        cancelled: Callable[[], bool],
    ) -> DeploymentExecutionResult:
        existing = self._store.get_revision(deployment.revision_id)
        if existing is not None:
            if existing.status is DeploymentStatus.ACTIVE:
                return DeploymentExecutionResult(True)
            return DeploymentExecutionResult(
                False,
                "DEPLOYMENT_REVISION_ALREADY_EXECUTED",
                f"DeploymentRevision 已处于 {existing.status.value}",
            )
        revision = self._workflow.deploy(
            self._intent(deployment),
            _CallableCancellation(cancelled),
        )
        if revision.status is DeploymentStatus.ACTIVE:
            return DeploymentExecutionResult(True)
        return DeploymentExecutionResult(
            False,
            revision.failure_code or f"DEPLOYMENT_{revision.status.value.upper()}",
            revision.recovery_detail or revision.failure_detail or "Compose deployment 未激活",
        )

    @staticmethod
    def _intent(deployment: ComposeDeploymentPlan) -> DeploymentIntent:
        probe: DeploymentProbe
        if isinstance(deployment.probe, HttpDeploymentProbeSpec):
            probe = HttpProbe(
                url=deployment.probe.url,
                timeout_seconds=deployment.probe.timeout_seconds,
            )
        else:
            probe = TcpProbe(
                host=deployment.probe.host,
                port=deployment.probe.port,
                timeout_seconds=deployment.probe.timeout_seconds,
            )
        return DeploymentIntent(
            revision_id=deployment.revision_id,
            workspace_id=deployment.workspace_id,
            target_id=deployment.project_id,
            workspace_revision=deployment.workspace_revision,
            source_fingerprint=deployment.source_fingerprint,
            target_config_fingerprint=deployment.target_config_fingerprint,
            checkout_path=Path(deployment.checkout_path),
            frozen_compose_path=Path(deployment.frozen_compose_path),
            services=deployment.services,
            immutable_images=deployment.immutable_images,
            wait_timeout_seconds=deployment.wait_timeout_seconds,
            probe=probe,
            environment_spec=deployment.environment_spec,
        )
