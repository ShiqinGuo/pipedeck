from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tripguru_local.compose_deployment import (
    DeploymentIntent,
    HttpProbe,
    TcpProbe,
    derive_compose_project_name,
)
from tripguru_local.contracts import (
    ComposeEndpoint,
    ComposeTarget,
    DeploymentEnvironmentSnapshot,
    DockerfileSource,
    EndpointProtocol,
    ExistingComposeSource,
    PostgresConnectionProfile,
    ProjectSummary,
    WorkspaceRecord,
)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ComposeConfigResult:
    return_code: int
    stdout: str = ""
    stderr: str = ""


class ComposeConfigRunner(Protocol):
    def run(self, *, argv: tuple[str, ...], cwd: Path) -> ComposeConfigResult: ...


class LocalComposeConfigRunner:
    def run(self, *, argv: tuple[str, ...], cwd: Path) -> ComposeConfigResult:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
        )
        return ComposeConfigResult(
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class ComposeCompilationProblem(StrEnum):
    FINGERPRINT_INVALID = "source/config fingerprint 必须是 SHA-256"
    PROJECT_NOT_IN_WORKSPACE = "project 不属于该 Workspace"
    TARGET_CONFIG_MISMATCH = "Compose target 与冻结 Workspace 配置不一致"
    CHECKOUT_INVALID = "project checkout 不存在或不是目录"
    PATH_OUTSIDE_CHECKOUT = "Compose source 引用了 checkout 外路径"
    SOURCE_FILE_MISSING = "Compose source 文件不存在"
    COMPOSE_CONFIG_FAILED = "Docker Compose config 失败"
    COMPOSE_CONFIG_INVALID = "Docker Compose config 没有返回合法 JSON"
    SERVICE_MISSING = "Compose service_names 引用了不存在的 service"
    SERVICE_DUPLICATED = "Compose service_names 不能重复"
    SERVICE_NOT_BUILDABLE = "部署 service 必须包含可本地构建的 build 配置"
    ENV_FILE_UNSUPPORTED = "冻结 Compose 不允许未解析的 env_file"
    SOURCE_PLACEHOLDER_UNSUPPORTED = "仓库 Compose 的动态 placeholder 必须由 Workspace 配置拥有"
    UNSAFE_PLACEHOLDER = "冻结 Compose 只允许受控的 TGL required placeholder"
    ENVIRONMENT_ALIAS_CONFLICT = "环境变量映射到相同的 Compose parent alias"
    SENSITIVE_LITERAL = "冻结 Compose 不允许敏感字面量或外部 Secret source"
    ENDPOINT_INVALID = "Compose endpoint 必须唯一且引用有效端口"
    READINESS_ENDPOINT_MISSING = "readiness 引用了不存在的 endpoint"
    READINESS_PROTOCOL_UNSUPPORTED = "readiness 不支持 UDP endpoint"
    ARTIFACT_CONFLICT = "同一 revision 的冻结 Compose 工件内容冲突"


class ComposeCompilationError(ValueError):
    def __init__(
        self,
        problem: ComposeCompilationProblem,
        subject: str | None = None,
    ) -> None:
        self.problem = problem
        self.subject = subject
        detail = problem.value if subject is None else f"{problem.value}: {subject}"
        super().__init__(detail)


class _CanonicalBuild(BaseModel):
    model_config = ConfigDict(extra="allow")

    context: str
    dockerfile: str = "Dockerfile"


class _CanonicalEnvironmentFile(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    required: bool = True


class _CanonicalPort(BaseModel):
    model_config = ConfigDict(extra="allow")

    target: int
    published: str | None = None
    protocol: str = "tcp"
    mode: str = "ingress"
    host_ip: str | None = None


class _CanonicalVolume(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    source: str | None = None
    target: str


class _CanonicalService(BaseModel):
    model_config = ConfigDict(extra="allow")

    build: _CanonicalBuild | None = None
    image: str | None = None
    container_name: str | None = None
    environment: Annotated[
        # tripguru-ast: ignore[TG-DS001] - Docker Compose owns dynamic environment keys.
        dict[str, JsonScalar],
        Field(),
    ] = dict()  # tripguru-ast: ignore[TG-DS001] - Pydantic copies the typed default.
    env_file: tuple[_CanonicalEnvironmentFile | str, ...] = ()
    labels: Annotated[
        # tripguru-ast: ignore[TG-DS001] - Docker Compose owns dynamic label keys.
        dict[str, str],
        Field(),
    ] = dict()  # tripguru-ast: ignore[TG-DS001] - Pydantic copies the typed default.
    ports: tuple[_CanonicalPort, ...] = ()
    volumes: tuple[_CanonicalVolume, ...] = ()


class _CanonicalDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    # tripguru-ast: ignore[TG-DS001] - Docker Compose owns dynamic service names.
    services: dict[str, _CanonicalService]


_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_IMAGE_TOKEN = re.compile(r"[^a-z0-9._-]+")
_PLACEHOLDER = re.compile(r"\$(?:\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*)")
_SAFE_PLACEHOLDER = re.compile(r"^\$\{TGL_[A-Z][A-Z0-9_]*:\?\}$")
_SENSITIVE_KEY_TOKENS = (
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "API_KEY",
    "PRIVATE_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "DSN",
    "CREDENTIAL",
    "ACCESS_KEY",
)


def deployment_parent_variable(name: str) -> str:
    return f"TGL_{name.upper()}"


class ComposeCompiler:
    def __init__(
        self,
        artifact_root: Path,
        runner: ComposeConfigRunner | None = None,
    ) -> None:
        self._artifact_root = artifact_root.resolve()
        self._runner = runner or LocalComposeConfigRunner()

    def compile(
        self,
        *,
        workspace: WorkspaceRecord,
        project: ProjectSummary,
        target: ComposeTarget,
        source_fingerprint: str,
        target_config_fingerprint: str,
        revision_nonce: str | None = None,
    ) -> DeploymentIntent:
        self._validate_fingerprint(source_fingerprint)
        self._validate_fingerprint(target_config_fingerprint)
        self._validate_workspace_target(workspace, project, target)
        checkout = self._resolve_checkout(project.path)
        project_name = derive_compose_project_name(workspace.id, project.id)
        revision_id = self._revision_id(
            workspace,
            project,
            source_fingerprint.lower(),
            target_config_fingerprint.lower(),
            revision_nonce,
        )

        if target.source.kind == "existing-compose":
            document, services = self._compile_existing(
                checkout=checkout,
                project_name=project_name,
                source=target.source,
            )
        else:
            document, services = self._compile_dockerfile(
                checkout=checkout,
                project=project,
                project_name=project_name,
                source=target.source,
            )

        images = self._apply_deployment_overrides(
            document=document,
            services=services,
            workspace=workspace,
            project=project,
            revision_id=revision_id,
            source_fingerprint=source_fingerprint.lower(),
            target_config_fingerprint=target_config_fingerprint.lower(),
            endpoints=target.endpoints,
        )
        payload = self._serialize(document)
        self._validate_frozen_payload(payload)
        frozen_path = self._write_frozen_artifact(revision_id, payload)
        workspace_service = next(
            service for service in workspace.services if service.project_id == project.id
        )

        return DeploymentIntent(
            revision_id=revision_id,
            workspace_id=workspace.id,
            target_id=project.id,
            workspace_revision=workspace.revision,
            source_fingerprint=source_fingerprint.lower(),
            target_config_fingerprint=target_config_fingerprint.lower(),
            checkout_path=checkout,
            frozen_compose_path=frozen_path,
            services=services,
            immutable_images=images,
            wait_timeout_seconds=target.wait_timeout,
            probe=self._probe(target),
            environment_spec=DeploymentEnvironmentSnapshot(
                environment=workspace_service.environment,
                connection_profiles=workspace_service.connection_profiles,
                bindings=workspace.bindings,
            ),
        )

    @staticmethod
    def _validate_fingerprint(value: str) -> None:
        if _SHA256.fullmatch(value) is None:
            raise ComposeCompilationError(ComposeCompilationProblem.FINGERPRINT_INVALID)

    @staticmethod
    def _validate_workspace_target(
        workspace: WorkspaceRecord,
        project: ProjectSummary,
        target: ComposeTarget,
    ) -> None:
        service = next(
            (candidate for candidate in workspace.services if candidate.project_id == project.id),
            None,
        )
        if service is None:
            raise ComposeCompilationError(ComposeCompilationProblem.PROJECT_NOT_IN_WORKSPACE)
        if service.execution_target != target:
            raise ComposeCompilationError(ComposeCompilationProblem.TARGET_CONFIG_MISMATCH)

    @staticmethod
    def _resolve_checkout(raw_path: str) -> Path:
        try:
            checkout = Path(raw_path).resolve(strict=True)
        except OSError as error:
            raise ComposeCompilationError(ComposeCompilationProblem.CHECKOUT_INVALID) from error
        if not checkout.is_dir():
            raise ComposeCompilationError(ComposeCompilationProblem.CHECKOUT_INVALID)
        return checkout

    @staticmethod
    def _resolve_source_path(
        checkout: Path,
        raw_path: str,
        *,
        base: Path | None = None,
        expected: str = "file",
    ) -> Path:
        try:
            resolved = ((base or checkout) / raw_path).resolve(strict=True)
        except OSError as error:
            raise ComposeCompilationError(
                ComposeCompilationProblem.SOURCE_FILE_MISSING,
                raw_path,
            ) from error
        if not resolved.is_relative_to(checkout):
            raise ComposeCompilationError(
                ComposeCompilationProblem.PATH_OUTSIDE_CHECKOUT,
                raw_path,
            )
        if (expected == "directory" and not resolved.is_dir()) or (
            expected == "file" and not resolved.is_file()
        ):
            raise ComposeCompilationError(
                ComposeCompilationProblem.SOURCE_FILE_MISSING,
                raw_path,
            )
        return resolved

    def _compile_existing(
        self,
        *,
        checkout: Path,
        project_name: str,
        source: ExistingComposeSource,
    ) -> tuple[_CanonicalDocument, tuple[str, ...]]:
        if len(source.service_names) != len(set(source.service_names)):
            raise ComposeCompilationError(ComposeCompilationProblem.SERVICE_DUPLICATED)
        compose_files = tuple(
            self._resolve_source_path(checkout, compose_file)
            for compose_file in source.compose_files
        )
        argv_parts: list[str] = ["docker", "compose"]
        for compose_file in compose_files:
            argv_parts.extend(("-f", str(compose_file)))
        for profile in source.profiles:
            argv_parts.extend(("--profile", profile))
        argv_parts.extend(
            (
                "-p",
                project_name,
                "config",
                "--no-interpolate",
                "--no-env-resolution",
                "--format",
                "json",
            )
        )
        try:
            result = self._runner.run(argv=tuple(argv_parts), cwd=checkout)
        except OSError as error:
            raise ComposeCompilationError(
                ComposeCompilationProblem.COMPOSE_CONFIG_FAILED
            ) from error
        if result.return_code != 0:
            raise ComposeCompilationError(ComposeCompilationProblem.COMPOSE_CONFIG_FAILED)
        try:
            document = _CanonicalDocument.model_validate_json(result.stdout)
        except ValidationError as error:
            raise ComposeCompilationError(
                ComposeCompilationProblem.COMPOSE_CONFIG_INVALID
            ) from error

        document.name = project_name
        self._validate_document_sources(document, checkout)
        self._validate_existing_secrets(document)
        for service_name in source.service_names:
            try:
                service = document.services[service_name]
            except KeyError as error:
                raise ComposeCompilationError(
                    ComposeCompilationProblem.SERVICE_MISSING,
                    service_name,
                ) from error
            if service.build is None:
                raise ComposeCompilationError(
                    ComposeCompilationProblem.SERVICE_NOT_BUILDABLE,
                    service_name,
                )
        return document, source.service_names

    def _compile_dockerfile(
        self,
        *,
        checkout: Path,
        project: ProjectSummary,
        project_name: str,
        source: DockerfileSource,
    ) -> tuple[_CanonicalDocument, tuple[str, ...]]:
        context = self._resolve_source_path(checkout, source.context, expected="directory")
        dockerfile = self._resolve_source_path(
            checkout,
            source.dockerfile,
            base=context,
        )
        service_name = self._service_name(project.id)
        document = _CanonicalDocument(
            name=project_name,
            services={  # tripguru-ast: ignore[TG-DS001] - service identity is project-derived.
                service_name: _CanonicalService(
                    build=_CanonicalBuild(
                        context=str(context),
                        dockerfile=dockerfile.relative_to(context).as_posix(),
                    )
                )
            },
        )
        return document, (service_name,)

    def _validate_document_sources(
        self,
        document: _CanonicalDocument,
        checkout: Path,
    ) -> None:
        for service in document.services.values():
            if service.build is not None:
                context = self._resolve_source_path(
                    checkout,
                    service.build.context,
                    expected="directory",
                )
                self._resolve_source_path(
                    checkout,
                    service.build.dockerfile,
                    base=context,
                )
            for volume in service.volumes:
                if volume.type != "bind" or volume.source is None:
                    continue
                self._resolve_source_path(checkout, volume.source, expected="any")

    @staticmethod
    def _validate_existing_secrets(document: _CanonicalDocument) -> None:
        if any(service.env_file for service in document.services.values()):
            raise ComposeCompilationError(ComposeCompilationProblem.ENV_FILE_UNSUPPORTED)
        dumped = cast(
            JsonValue,
            document.model_dump(mode="json", exclude_none=True),
        )
        ComposeCompiler._validate_json_value(dumped)
        serialized = json.dumps(dumped, ensure_ascii=True, sort_keys=True)
        if _PLACEHOLDER.search(serialized) is not None:
            raise ComposeCompilationError(ComposeCompilationProblem.SOURCE_PLACEHOLDER_UNSUPPORTED)

    @staticmethod
    def _validate_json_value(value: JsonValue, owner_key: str | None = None) -> None:
        if (
            owner_key is not None
            and ComposeCompiler._is_sensitive_key(owner_key)
            and not ComposeCompiler._is_safe_placeholder_value(value)
        ):
            raise ComposeCompilationError(ComposeCompilationProblem.SENSITIVE_LITERAL)
        if isinstance(value, str):
            for placeholder in _PLACEHOLDER.findall(value):
                if _SAFE_PLACEHOLDER.fullmatch(placeholder) is None:
                    raise ComposeCompilationError(ComposeCompilationProblem.UNSAFE_PLACEHOLDER)
            return
        if isinstance(value, list):
            for item in value:
                ComposeCompiler._validate_json_value(item, owner_key)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                ComposeCompiler._validate_json_value(item, key)

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        normalized = key.upper().replace("-", "_").replace(".", "_")
        return any(token in normalized for token in _SENSITIVE_KEY_TOKENS)

    @staticmethod
    def _is_safe_placeholder_value(value: JsonValue) -> bool:
        if not isinstance(value, str):
            return False
        placeholders = _PLACEHOLDER.findall(value)
        return bool(placeholders) and all(
            _SAFE_PLACEHOLDER.fullmatch(placeholder) is not None for placeholder in placeholders
        )

    def _apply_deployment_overrides(
        self,
        *,
        document: _CanonicalDocument,
        services: tuple[str, ...],
        workspace: WorkspaceRecord,
        project: ProjectSummary,
        revision_id: str,
        source_fingerprint: str,
        target_config_fingerprint: str,
        endpoints: tuple[ComposeEndpoint, ...],
    ) -> tuple[str, ...]:
        self._validate_endpoints(endpoints)
        labels = {  # tripguru-ast: ignore[TG-DS001] - Compose owns dynamic label keys.
            "tripguru.local/managed": "true",
            "tripguru.local/workspace": workspace.id,
            "tripguru.local/project": project.id,
            "tripguru.local/target": project.id,
            "tripguru.local/revision": revision_id,
            "tripguru.local/source-fingerprint": source_fingerprint,
            "tripguru.local/config-fingerprint": target_config_fingerprint,
        }
        environment_names = self._deployment_environment_names(workspace, project.id)
        aliases = tuple(deployment_parent_variable(name) for name in environment_names)
        if len(aliases) != len(set(aliases)):
            raise ComposeCompilationError(ComposeCompilationProblem.ENVIRONMENT_ALIAS_CONFLICT)
        # tripguru-ast: ignore[TG-DS001] - env keys are validated Workspace names.
        injected_environment = {
            name: f"${{{deployment_parent_variable(name)}:?}}" for name in environment_names
        }
        images: list[str] = []
        for index, service_name in enumerate(services):
            service = document.services[service_name]
            image = self._image_name(
                workspace=workspace,
                project=project,
                service_name=service_name,
                revision_id=revision_id,
                source_fingerprint=source_fingerprint,
                target_config_fingerprint=target_config_fingerprint,
            )
            service.image = image
            service.container_name = None
            service.labels = {  # tripguru-ast: ignore[TG-DS001] - typed label merge.
                **service.labels,
                **labels,
            }
            service.environment = {  # tripguru-ast: ignore[TG-DS001] - typed environment merge.
                **service.environment,
                **injected_environment,
            }
            service.ports = self._ports(endpoints) if index == 0 else ()
            images.append(image)
        for service in document.services.values():
            service.container_name = None
        return tuple(images)

    @staticmethod
    def _deployment_environment_names(
        workspace: WorkspaceRecord,
        project_id: str,
    ) -> tuple[str, ...]:
        service = next(item for item in workspace.services if item.project_id == project_id)
        names = [binding.name for binding in service.environment]
        for profile in service.connection_profiles:
            if isinstance(profile, PostgresConnectionProfile):
                names.append(profile.env_var)
            else:
                names.extend(
                    (
                        profile.endpoint_env,
                        profile.access_key_env,
                        profile.secret_key_env,
                        profile.bucket_env,
                    )
                )
        return tuple(names)

    @staticmethod
    def _validate_endpoints(endpoints: tuple[ComposeEndpoint, ...]) -> None:
        names = tuple(endpoint.name for endpoint in endpoints)
        published = tuple((endpoint.protocol, endpoint.host_port) for endpoint in endpoints)
        targets = tuple((endpoint.protocol, endpoint.container_port) for endpoint in endpoints)
        if (
            len(names) != len(set(names))
            or len(published) != len(set(published))
            or len(targets) != len(set(targets))
        ):
            raise ComposeCompilationError(ComposeCompilationProblem.ENDPOINT_INVALID)

    @staticmethod
    def _ports(endpoints: tuple[ComposeEndpoint, ...]) -> tuple[_CanonicalPort, ...]:
        return tuple(
            _CanonicalPort(
                target=endpoint.container_port,
                published=str(endpoint.host_port),
                protocol=endpoint.protocol.value,
                mode="ingress",
                host_ip="127.0.0.1",
            )
            for endpoint in endpoints
        )

    @staticmethod
    def _probe(target: ComposeTarget) -> HttpProbe | TcpProbe:
        endpoint = next(
            (
                candidate
                for candidate in target.endpoints
                if candidate.name == target.readiness.endpoint
            ),
            None,
        )
        if endpoint is None:
            raise ComposeCompilationError(ComposeCompilationProblem.READINESS_ENDPOINT_MISSING)
        if endpoint.protocol is EndpointProtocol.UDP:
            raise ComposeCompilationError(ComposeCompilationProblem.READINESS_PROTOCOL_UNSUPPORTED)
        if target.readiness.kind == "http":
            return HttpProbe(url=f"http://127.0.0.1:{endpoint.host_port}{target.readiness.path}")
        return TcpProbe(host="127.0.0.1", port=endpoint.host_port)

    @staticmethod
    def _revision_id(
        workspace: WorkspaceRecord,
        project: ProjectSummary,
        source_fingerprint: str,
        target_config_fingerprint: str,
        revision_nonce: str | None,
    ) -> str:
        identity = "\0".join(
            (
                workspace.id,
                project.id,
                str(workspace.revision),
                source_fingerprint,
                target_config_fingerprint,
                revision_nonce or "",
            )
        )
        return f"rev-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"

    @staticmethod
    def _service_name(project_id: str) -> str:
        slug = _IMAGE_TOKEN.sub("-", project_id.lower()).strip("-._") or "app"
        digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:8]
        return f"app-{slug[:40]}-{digest}"

    @staticmethod
    def _image_name(
        *,
        workspace: WorkspaceRecord,
        project: ProjectSummary,
        service_name: str,
        revision_id: str,
        source_fingerprint: str,
        target_config_fingerprint: str,
    ) -> str:
        project_name = derive_compose_project_name(workspace.id, project.id)
        service_slug = _IMAGE_TOKEN.sub("-", service_name.lower()).strip("-._") or "app"
        service_digest = hashlib.sha256(service_name.encode("utf-8")).hexdigest()[:8]
        tag = (
            f"r{workspace.revision}-s{source_fingerprint[:16]}-"
            f"c{target_config_fingerprint[:16]}-{revision_id.removeprefix('rev-')}"
        )
        return f"tripguru.local/{project_name}/{service_slug[:40]}-{service_digest}:{tag}"

    @staticmethod
    def _serialize(document: _CanonicalDocument) -> str:
        payload = document.model_dump(mode="json", exclude_none=True)
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _validate_frozen_payload(payload: str) -> None:
        for placeholder in _PLACEHOLDER.findall(payload):
            if _SAFE_PLACEHOLDER.fullmatch(placeholder) is None:
                raise ComposeCompilationError(ComposeCompilationProblem.UNSAFE_PLACEHOLDER)

    def _write_frozen_artifact(self, revision_id: str, payload: str) -> Path:
        revision_directory = self._artifact_root / revision_id
        revision_directory.mkdir(parents=True, exist_ok=True)
        frozen_path = revision_directory / "compose.json"
        try:
            with frozen_path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.write("\n")
        except FileExistsError as error:
            if frozen_path.read_text(encoding="utf-8") != f"{payload}\n":
                raise ComposeCompilationError(
                    ComposeCompilationProblem.ARTIFACT_CONFLICT
                ) from error
        return frozen_path
