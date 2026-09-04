from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipedeck.cleanup import CleanupPreviewNotFoundError, CleanupService
from pipedeck.contracts import (
    CleanupItem,
    CleanupPreviewResponse,
    MiddlewareKind,
    ResourceHealth,
    RuntimeResource,
    RuntimeResponse,
)
from pipedeck.processes import CommandResult


class FakeRuntime:
    def __init__(self, *snapshots: RuntimeResponse) -> None:
        self._snapshots = list(snapshots)
        self._index = 0

    def snapshot(self) -> RuntimeResponse:
        snapshot = self._snapshots[min(self._index, len(self._snapshots) - 1)]
        self._index += 1
        return snapshot


class FakeVerifier:
    def __init__(self, matching_ids: set[str]) -> None:
        self._matching_ids = matching_ids

    def matches(self, resource: RuntimeResource) -> bool:
        return resource.id in self._matching_ids


class FakeRunner:
    def __init__(self, failing_ids: set[str] | None = None) -> None:
        self.failing_ids = failing_ids or set()
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult:
        del cwd
        self.calls.append(argv)
        resource_id = argv[-1]
        if resource_id in self.failing_ids:
            return CommandResult(return_code=1, stdout="", stderr="remove failed")
        return CommandResult(return_code=0, stdout=resource_id, stderr="")


class FakeManagedRemover:
    def __init__(self) -> None:
        self.calls: list[tuple[str, MiddlewareKind, str]] = []

    def remove_managed_runtime(
        self,
        *,
        runtime_id: str,
        kind: MiddlewareKind,
        workspace_id: str,
    ) -> bool:
        self.calls.append((runtime_id, kind, workspace_id))
        return True


def resource(
    resource_id: str,
    kind: MiddlewareKind,
    health: ResourceHealth,
    *,
    managed: bool = True,
) -> RuntimeResource:
    return RuntimeResource(
        id=resource_id,
        name=f"container-{resource_id}",
        kind=kind,
        image=f"example/{kind.value}:latest",
        state="running" if health is not ResourceHealth.STOPPED else "exited",
        status_text=health.value,
        health=health,
        managed=managed,
        protected=False,
        ports="",
    )


def snapshot(*resources: RuntimeResource, available: bool = True) -> RuntimeResponse:
    return RuntimeResponse(
        generated_at=datetime.now(UTC),
        docker_available=available,
        resources=resources,
    )


def item_by_id(service_preview: CleanupPreviewResponse, resource_id: str) -> CleanupItem:
    return next(item for item in service_preview.items if item.resource.id == resource_id)


@pytest.mark.parametrize(
    ("kind", "resource_id"),
    (
        (MiddlewareKind.POSTGRES, "pg-one"),
        (MiddlewareKind.REDIS, "redis-one"),
        (MiddlewareKind.ELASTICSEARCH, "es-one"),
    ),
)
def test_preview_requires_another_healthy_instance_not_merely_running(
    kind: MiddlewareKind,
    resource_id: str,
) -> None:
    target = resource(resource_id, kind, ResourceHealth.HEALTHY)
    running = resource(f"{resource_id}-running", kind, ResourceHealth.RUNNING)
    service = CleanupService(
        FakeRuntime(snapshot(target, running)),
        FakeRunner(),
        FakeVerifier({target.id, running.id}),
    )

    preview = service.preview()

    assert item_by_id(preview, target.id).reason_code == "LAST_HEALTHY_INSTANCE"
    assert item_by_id(preview, running.id).reason_code is None


def test_preview_requires_managed_label_local_record_and_always_protects_minio() -> None:
    healthy_pg = resource("pg-healthy", MiddlewareKind.POSTGRES, ResourceHealth.HEALTHY)
    unmanaged = resource(
        "pg-unmanaged",
        MiddlewareKind.POSTGRES,
        ResourceHealth.STOPPED,
        managed=False,
    )
    unknown_managed = resource("pg-unknown", MiddlewareKind.POSTGRES, ResourceHealth.STOPPED)
    minio = resource("minio", MiddlewareKind.MINIO, ResourceHealth.STOPPED)
    service = CleanupService(
        FakeRuntime(snapshot(healthy_pg, unmanaged, unknown_managed, minio)),
        FakeRunner(),
        FakeVerifier({healthy_pg.id, minio.id}),
    )

    preview = service.preview()

    assert item_by_id(preview, unmanaged.id).reason_code == "RESOURCE_NOT_MANAGED"
    assert item_by_id(preview, unknown_managed.id).reason_code == "LOCAL_MANAGED_RECORD_MISMATCH"
    minio_item = item_by_id(preview, minio.id)
    assert minio_item.eligible is False
    assert minio_item.reason_code == "MINIO_ALWAYS_PROTECTED"


def test_apply_uses_exact_docker_command_and_preserves_one_healthy_in_batch() -> None:
    first = resource("pg-first", MiddlewareKind.POSTGRES, ResourceHealth.HEALTHY)
    second = resource("pg-second", MiddlewareKind.POSTGRES, ResourceHealth.HEALTHY)
    stopped = resource("pg-stopped", MiddlewareKind.POSTGRES, ResourceHealth.STOPPED)
    current = snapshot(first, second, stopped)
    runner = FakeRunner()
    service = CleanupService(
        FakeRuntime(current, current),
        runner,
        FakeVerifier({first.id, second.id, stopped.id}),
    )
    preview = service.preview()

    result = service.apply(preview.id, (stopped.id, first.id, second.id))

    assert runner.calls == [
        ("docker", "container", "rm", "-f", stopped.id),
        ("docker", "container", "rm", "-f", first.id),
    ]
    assert [item.status for item in result.results] == ["removed", "removed", "skipped"]
    assert result.results[-1].reason_code == "LAST_HEALTHY_INSTANCE"


def test_apply_rejects_changed_runtime_before_any_remove() -> None:
    first = resource("redis-first", MiddlewareKind.REDIS, ResourceHealth.HEALTHY)
    second = resource("redis-second", MiddlewareKind.REDIS, ResourceHealth.HEALTHY)
    changed_second = second.model_copy(update={"health": ResourceHealth.RUNNING})
    runner = FakeRunner()
    service = CleanupService(
        FakeRuntime(snapshot(first, second), snapshot(first, changed_second)),
        runner,
        FakeVerifier({first.id, second.id}),
    )
    preview = service.preview()

    result = service.apply(preview.id, (first.id,))

    assert runner.calls == []
    assert result.results[0].status == "skipped"
    assert result.results[0].reason_code == "RUNTIME_CHANGED"


def test_apply_reports_each_command_failure_and_never_removes_minio() -> None:
    first = resource("es-first", MiddlewareKind.ELASTICSEARCH, ResourceHealth.HEALTHY)
    second = resource("es-second", MiddlewareKind.ELASTICSEARCH, ResourceHealth.HEALTHY)
    minio = resource("minio", MiddlewareKind.MINIO, ResourceHealth.HEALTHY)
    current = snapshot(first, second, minio)
    runner = FakeRunner({first.id})
    service = CleanupService(
        FakeRuntime(current, current),
        runner,
        FakeVerifier({first.id, second.id, minio.id}),
    )
    preview = service.preview()

    result = service.apply(preview.id, (first.id, minio.id))

    assert runner.calls == [("docker", "container", "rm", "-f", first.id)]
    assert result.results[0].status == "failed"
    assert result.results[0].reason_code == "DOCKER_REMOVE_FAILED"
    assert result.results[1].reason_code == "MINIO_ALWAYS_PROTECTED"


def test_apply_delegates_owned_resource_removal_to_lifecycle_service() -> None:
    first = resource("a" * 12, MiddlewareKind.POSTGRES, ResourceHealth.HEALTHY).model_copy(
        update={"owner_workspace_id": "workspace-a"}
    )
    second = resource("b" * 12, MiddlewareKind.POSTGRES, ResourceHealth.HEALTHY).model_copy(
        update={"owner_workspace_id": "workspace-b"}
    )
    current = snapshot(first, second)
    runner = FakeRunner()
    remover = FakeManagedRemover()
    service = CleanupService(
        FakeRuntime(current, current),
        runner,
        FakeVerifier({first.id, second.id}),
        remover,
    )
    preview = service.preview()

    result = service.apply(preview.id, (first.id,))

    assert result.results[0].status == "removed"
    assert remover.calls == [(first.id, MiddlewareKind.POSTGRES, "workspace-a")]
    assert runner.calls == []


def test_apply_rejects_unknown_preview() -> None:
    service = CleanupService(FakeRuntime(snapshot()), FakeRunner(), FakeVerifier(set()))

    with pytest.raises(CleanupPreviewNotFoundError, match="Cleanup preview not found or expired"):
        service.apply("missing", ("pg",))
