from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pipedeck.contracts import (
    CleanupApplyResponse,
    CleanupItem,
    CleanupPreviewResponse,
    CleanupResultItem,
    MiddlewareKind,
    ResourceHealth,
    RuntimeResource,
    RuntimeResponse,
)
from pipedeck.processes import CommandResult


class RuntimeSnapshotProvider(Protocol):
    def snapshot(self) -> RuntimeResponse: ...


class CleanupCommandRunner(Protocol):
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult: ...


class ManagedResourceVerifier(Protocol):
    def matches(self, resource: RuntimeResource) -> bool: ...


class ManagedResourceRemover(Protocol):
    def remove_managed_runtime(
        self,
        *,
        runtime_id: str,
        kind: MiddlewareKind,
        workspace_id: str,
    ) -> bool: ...


class CleanupError(RuntimeError):
    code = "CLEANUP_ERROR"
    detail = "Cleanup operation failed"

    def __init__(self) -> None:
        super().__init__(self.detail)


class CleanupPreviewNotFoundError(CleanupError):
    code = "CLEANUP_PREVIEW_NOT_FOUND"
    detail = "Cleanup preview not found or expired"


class CleanupService:
    def __init__(
        self,
        runtime: RuntimeSnapshotProvider,
        command_runner: CleanupCommandRunner,
        managed_resource_verifier: ManagedResourceVerifier,
        managed_resource_remover: ManagedResourceRemover | None = None,
    ) -> None:
        self._runtime = runtime
        self._command_runner = command_runner
        self._managed_resource_verifier = managed_resource_verifier
        self._managed_resource_remover = managed_resource_remover
        self._previews: list[CleanupPreviewResponse] = []

    def preview(self) -> CleanupPreviewResponse:
        snapshot = self._runtime.snapshot()
        response = CleanupPreviewResponse(
            id=str(uuid4()),
            generated_at=datetime.now(UTC),
            runtime_fingerprint=self._fingerprint(snapshot),
            items=tuple(
                self._classify(resource, snapshot.resources, snapshot.docker_available)
                for resource in snapshot.resources
            ),
        )
        self._previews.append(response)
        return response

    def apply(
        self,
        preview_id: str,
        resource_ids: tuple[str, ...],
    ) -> CleanupApplyResponse:
        preview = self._find_preview(preview_id)
        snapshot = self._runtime.snapshot()
        if self._fingerprint(snapshot) != preview.runtime_fingerprint:
            return CleanupApplyResponse(
                preview_id=preview_id,
                results=tuple(
                    self._runtime_changed_result(preview, resource_id)
                    for resource_id in resource_ids
                ),
            )

        remaining = list(snapshot.resources)
        results: list[CleanupResultItem] = []
        for resource_id in resource_ids:
            original = next(
                (item for item in preview.items if item.resource.id == resource_id),
                None,
            )
            current = next(
                (resource for resource in remaining if resource.id == resource_id),
                None,
            )
            if original is None or current is None:
                results.append(
                    CleanupResultItem(
                        resource_id=resource_id,
                        resource_name=(
                            original.resource.name if original is not None else resource_id
                        ),
                        status="skipped",
                        reason_code="RESOURCE_NOT_IN_PREVIEW",
                        detail="Resource is not in the current cleanup preview",
                    )
                )
                continue
            if not original.eligible:
                results.append(self._blocked_result(original))
                continue

            current_item = self._classify(current, tuple(remaining), snapshot.docker_available)
            if not current_item.eligible:
                results.append(self._blocked_result(current_item))
                continue
            removed, failure_detail = self._remove(current)
            if not removed:
                results.append(
                    CleanupResultItem(
                        resource_id=current.id,
                        resource_name=current.name,
                        status="failed",
                        reason_code="DOCKER_REMOVE_FAILED",
                        detail=failure_detail or "Failed to remove Docker container",
                    )
                )
                continue
            results.append(
                CleanupResultItem(
                    resource_id=current.id,
                    resource_name=current.name,
                    status="removed",
                )
            )
            remaining.remove(current)
        return CleanupApplyResponse(preview_id=preview_id, results=tuple(results))

    def _remove(self, resource: RuntimeResource) -> tuple[bool, str | None]:
        if self._managed_resource_remover is None:
            result = self._command_runner.run(("docker", "container", "rm", "-f", resource.id))
            return result.return_code == 0, result.stderr or None
        if resource.owner_workspace_id is None:
            return False, "Managed resource is missing workspace ownership"
        try:
            removed = self._managed_resource_remover.remove_managed_runtime(
                runtime_id=resource.id,
                kind=resource.kind,
                workspace_id=resource.owner_workspace_id,
            )
        except Exception:
            return False, "Managed resource ownership or lifecycle re-check failed"
        return removed, None

    def _classify(
        self,
        resource: RuntimeResource,
        resources: tuple[RuntimeResource, ...],
        docker_available: bool,
    ) -> CleanupItem:
        if resource.kind is MiddlewareKind.MINIO:
            return self._blocked(
                resource, "MINIO_ALWAYS_PROTECTED", "MinIO is always protected from cleanup"
            )
        if not docker_available:
            return self._blocked(resource, "DOCKER_UNAVAILABLE", "Docker is currently unavailable")
        if not resource.managed:
            return self._blocked(
                resource,
                "RESOURCE_NOT_MANAGED",
                "Only containers with the Pipedeck managed label can be cleaned",
            )
        if not self._managed_resource_verifier.matches(resource):
            return self._blocked(
                resource,
                "LOCAL_MANAGED_RECORD_MISMATCH",
                "Local managed records do not match the runtime container",
            )
        has_other_healthy = any(
            other.id != resource.id
            and other.kind is resource.kind
            and other.health is ResourceHealth.HEALTHY
            for other in resources
        )
        if not has_other_healthy:
            return self._blocked(
                resource,
                "LAST_HEALTHY_INSTANCE",
                "Another healthy instance of the same middleware kind must be kept",
            )
        return CleanupItem(resource=resource, eligible=True, reason_code=None, reason=None)

    @staticmethod
    def _blocked(resource: RuntimeResource, code: str, reason: str) -> CleanupItem:
        return CleanupItem(
            resource=resource,
            eligible=False,
            reason_code=code,
            reason=reason,
        )

    @staticmethod
    def _blocked_result(item: CleanupItem) -> CleanupResultItem:
        return CleanupResultItem(
            resource_id=item.resource.id,
            resource_name=item.resource.name,
            status="skipped",
            reason_code=item.reason_code,
            detail=item.reason,
        )

    @staticmethod
    def _runtime_changed_result(
        preview: CleanupPreviewResponse,
        resource_id: str,
    ) -> CleanupResultItem:
        original = next(
            (item for item in preview.items if item.resource.id == resource_id),
            None,
        )
        return CleanupResultItem(
            resource_id=resource_id,
            resource_name=original.resource.name if original is not None else resource_id,
            status="skipped",
            reason_code="RUNTIME_CHANGED",
            detail="Docker runtime changed; please regenerate the cleanup preview",
        )

    def _find_preview(self, preview_id: str) -> CleanupPreviewResponse:
        preview = next((item for item in self._previews if item.id == preview_id), None)
        if preview is None:
            raise CleanupPreviewNotFoundError()
        return preview

    @staticmethod
    def _fingerprint(snapshot: RuntimeResponse) -> str:
        resource_rows = tuple(
            "\x1f".join(
                (
                    resource.id,
                    resource.name,
                    resource.kind.value,
                    resource.image,
                    resource.state,
                    resource.health.value,
                    str(resource.managed),
                    str(resource.protected),
                    resource.ports,
                )
            )
            for resource in sorted(snapshot.resources, key=lambda item: item.id)
        )
        payload = "\x1e".join((str(snapshot.docker_available), *resource_rows))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
