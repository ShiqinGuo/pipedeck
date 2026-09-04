from pathlib import Path

from pipedeck.contracts import EndpointProtocol, MiddlewareKind, ResourceHealth
from pipedeck.processes import CommandResult
from pipedeck.runtime import DockerRuntime


class DockerRunner:
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult:
        if argv[:3] == ("docker", "container", "inspect"):
            return CommandResult(
                return_code=0,
                stdout=(
                    '[{"Id":"abcdef","NetworkSettings":{"Ports":'
                    '{"9000/tcp":[{"HostIp":"0.0.0.0","HostPort":"19000"},'
                    '{"HostIp":"::","HostPort":"19000"}],'
                    '"9001/tcp":[{"HostIp":"127.0.0.1","HostPort":"19001"}]}}}]'
                ),
                stderr="",
            )
        assert argv == ("docker", "container", "ls", "-a", "--format", "{{json .}}")
        return CommandResult(
            return_code=0,
            stdout=(
                '{"ID":"abc","Image":"minio/minio:latest",'
                '"Labels":"tripguru.local/managed=true","Names":"local-minio",'
                '"Ports":"0.0.0.0:9000->9000/tcp","State":"running",'
                '"Status":"Up 5 minutes (healthy)"}'
            ),
            stderr="",
        )


def test_runtime_classifies_and_protects_minio() -> None:
    response = DockerRuntime(DockerRunner()).snapshot()

    assert response.docker_available is True
    assert len(response.resources) == 1
    resource = response.resources[0]
    assert resource.kind is MiddlewareKind.MINIO
    assert resource.health is ResourceHealth.HEALTHY
    assert resource.managed is True
    assert resource.protected is True
    assert len(resource.endpoints) == 2
    assert resource.endpoints[0].protocol is EndpointProtocol.TCP
    assert resource.endpoints[0].container_port == 9000
    assert resource.endpoints[0].host == "127.0.0.1"
    assert resource.endpoints[0].host_port == 19000


class SpecificHostRunner(DockerRunner):
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult:
        if argv[:3] == ("docker", "container", "inspect"):
            return CommandResult(
                return_code=0,
                stdout=(
                    '[{"Id":"abcdef","NetworkSettings":{"Ports":'
                    '{"9000/tcp":[{"HostIp":"192.168.50.4","HostPort":"19000"}]}}}]'
                ),
                stderr="",
            )
        return super().run(argv, cwd)


def test_runtime_does_not_project_specific_host_binding_to_loopback() -> None:
    response = DockerRuntime(SpecificHostRunner()).snapshot()

    assert response.resources[0].endpoints == ()


class RedundantPostgresRunner:
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult:
        if argv[:3] == ("docker", "container", "inspect"):
            return CommandResult(return_code=0, stdout="[]", stderr="")
        rows = (
            '{"ID":"pg-a","Image":"postgres:18","Labels":"tripguru.local/managed=true",'
            '"Names":"pg-a","Ports":"","State":"running","Status":"Up (healthy)"}',
            '{"ID":"pg-b","Image":"postgres:18","Labels":"tripguru.local/managed=true",'
            '"Names":"pg-b","Ports":"","State":"running","Status":"Up (healthy)"}',
            '{"ID":"redis-a","Image":"redis:8","Labels":"tripguru.local/managed=true",'
            '"Names":"redis-a","Ports":"","State":"running","Status":"Up (healthy)"}',
        )
        return CommandResult(return_code=0, stdout="\n".join(rows), stderr="")


def test_runtime_only_unprotects_resources_with_another_healthy_instance() -> None:
    response = DockerRuntime(RedundantPostgresRunner()).snapshot()

    protected = {resource.name: resource.protected for resource in response.resources}
    assert protected == {"pg-a": False, "pg-b": False, "redis-a": True}


class FailedDockerRunner:
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult:
        return CommandResult(return_code=1, stdout="", stderr="daemon unavailable")


def test_runtime_returns_recoverable_unavailable_state() -> None:
    response = DockerRuntime(FailedDockerRunner()).snapshot()

    assert response.docker_available is False
    assert response.error_code == "DOCKER_UNAVAILABLE"
    assert response.recovery == "Start Docker Desktop and retry"
