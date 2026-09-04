from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from threading import Lock
from time import monotonic

from pydantic import TypeAdapter, ValidationError

from pipedeck.contracts import (
    DockerCliRow,
    DockerInspectRow,
    EndpointProtocol,
    MiddlewareKind,
    ResourceHealth,
    RuntimeEndpoint,
    RuntimeResource,
    RuntimeResponse,
)
from pipedeck.processes import CommandRunner


class DockerRuntime:
    _loopback_host_ips = {"", "0.0.0.0", "127.0.0.1", "::", "::1"}

    def __init__(self, command_runner: CommandRunner) -> None:
        self._command_runner = command_runner
        self._cache_lock = Lock()
        self._cached_at = 0.0
        self._cached_response: RuntimeResponse | None = None

    def snapshot(self) -> RuntimeResponse:
        with self._cache_lock:
            if self._cached_response is not None and monotonic() - self._cached_at < 5:
                return self._cached_response
            response = self._snapshot_uncached()
            self._cached_response = response
            self._cached_at = monotonic()
            return response

    def invalidate(self) -> None:
        with self._cache_lock:
            self._cached_at = 0.0
            self._cached_response = None

    def _snapshot_uncached(self) -> RuntimeResponse:
        result = self._command_runner.run(
            ("docker", "container", "ls", "-a", "--format", "{{json .}}")
        )
        if result.return_code != 0:
            return RuntimeResponse(
                generated_at=datetime.now(UTC),
                docker_available=False,
                resources=(),
                error_code="DOCKER_UNAVAILABLE",
                recovery="Start Docker Desktop and retry",
            )

        classified: list[tuple[DockerCliRow, MiddlewareKind]] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            try:
                # pipedeck-ast: ignore[TG-DS001] - Docker JSON is validated immediately.
                row = DockerCliRow.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValidationError):
                continue
            kind = self._classify(row)
            if kind is None:
                continue
            classified.append((row, kind))

        inspected = self._inspect(tuple(row.id for row, _ in classified))
        resources = [
            self._resource(row, kind, self._endpoints_for(row.id, inspected))
            for row, kind in classified
        ]

        resources = self._apply_protection(resources)

        resources.sort(key=lambda resource: (resource.kind.value, resource.name.casefold()))
        return RuntimeResponse(
            generated_at=datetime.now(UTC),
            docker_available=True,
            resources=tuple(resources),
        )

    @staticmethod
    def _classify(row: DockerCliRow) -> MiddlewareKind | None:
        identity = f"{row.image} {row.names}".casefold()
        if "postgres" in identity:
            return MiddlewareKind.POSTGRES
        if "redis" in identity:
            return MiddlewareKind.REDIS
        if "elasticsearch" in identity or "elastic" in identity:
            return MiddlewareKind.ELASTICSEARCH
        if "minio" in identity:
            return MiddlewareKind.MINIO
        return None

    def _inspect(self, resource_ids: tuple[str, ...]) -> tuple[DockerInspectRow, ...]:
        if not resource_ids:
            return ()
        result = self._command_runner.run(("docker", "container", "inspect", *resource_ids))
        if result.return_code != 0:
            return ()
        try:
            # pipedeck-ast: ignore[TG-DS001] - Docker JSON is validated immediately.
            payload = json.loads(result.stdout)
            return TypeAdapter(tuple[DockerInspectRow, ...]).validate_python(payload)
        except (json.JSONDecodeError, ValidationError):
            return ()

    @classmethod
    def _endpoints_for(
        cls,
        resource_id: str,
        inspected: tuple[DockerInspectRow, ...],
    ) -> tuple[RuntimeEndpoint, ...]:
        row = next((item for item in inspected if item.id.startswith(resource_id)), None)
        if row is None:
            return ()
        endpoints: list[RuntimeEndpoint] = []
        seen: set[tuple[EndpointProtocol, int, int]] = set()
        # Docker owns the dynamic "container-port/protocol" keys at this adapter boundary.
        for container_identity, bindings in row.network_settings.ports.items():
            container_text, separator, protocol_text = container_identity.partition("/")
            if separator != "/" or not container_text.isdigit():
                continue
            try:
                protocol = EndpointProtocol(protocol_text)
            except ValueError:
                continue
            if bindings is None:
                continue
            container_port = int(container_text)
            for binding in bindings:
                if binding.host_ip not in cls._loopback_host_ips or not binding.host_port.isdigit():
                    continue
                host_port = int(binding.host_port)
                identity = (protocol, container_port, host_port)
                if identity in seen:
                    continue
                seen.add(identity)
                endpoints.append(
                    RuntimeEndpoint(
                        protocol=protocol,
                        container_port=container_port,
                        host_port=host_port,
                    )
                )
        return tuple(
            sorted(
                endpoints,
                key=lambda item: (item.container_port, item.protocol.value, item.host_port),
            )
        )

    @staticmethod
    def _resource(
        row: DockerCliRow,
        kind: MiddlewareKind,
        endpoints: tuple[RuntimeEndpoint, ...],
    ) -> RuntimeResource:
        status = row.status.casefold()
        if row.state.casefold() != "running":
            health = ResourceHealth.STOPPED
        elif "unhealthy" in status:
            health = ResourceHealth.UNHEALTHY
        elif "healthy" in status:
            health = ResourceHealth.HEALTHY
        else:
            health = ResourceHealth.RUNNING

        owner_match = re.search(
            r"(?:^|,)tripguru.local/workspace=([^,]+)",
            row.labels,
        )

        return RuntimeResource(
            id=row.id,
            name=row.names,
            kind=kind,
            image=row.image,
            state=row.state,
            status_text=row.status,
            health=health,
            managed="tripguru.local/managed=true" in row.labels,
            protected=True,
            ports=row.ports,
            endpoints=endpoints,
            owner_workspace_id=owner_match.group(1) if owner_match is not None else None,
        )

    @staticmethod
    def _apply_protection(resources: list[RuntimeResource]) -> list[RuntimeResource]:
        healthy_resources = tuple(
            resource for resource in resources if resource.health is ResourceHealth.HEALTHY
        )
        return [
            RuntimeResource(
                id=resource.id,
                name=resource.name,
                kind=resource.kind,
                image=resource.image,
                state=resource.state,
                status_text=resource.status_text,
                health=resource.health,
                managed=resource.managed,
                protected=(
                    not resource.managed
                    or resource.kind is MiddlewareKind.MINIO
                    or not any(
                        healthy.id != resource.id and healthy.kind is resource.kind
                        for healthy in healthy_resources
                    )
                ),
                ports=resource.ports,
                endpoints=resource.endpoints,
                owner_workspace_id=resource.owner_workspace_id,
            )
            for resource in resources
        ]
