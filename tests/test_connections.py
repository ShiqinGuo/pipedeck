from datetime import UTC, datetime

from pipedeck.contracts import (
    ComposeEndpoint,
    ComposeTarget,
    DockerfileSource,
    EndpointProtocol,
    EnvironmentBinding,
    EnvironmentSource,
    MiddlewareBinding,
    MiddlewareKind,
    MinioConnectionProfile,
    PostgresConnectionProfile,
    ProjectKind,
    ProjectSummary,
    ResourceHealth,
    RunMode,
    RuntimeEndpoint,
    RuntimeResource,
    RuntimeResponse,
    TcpReadiness,
    WorkspaceRecord,
    WorkspaceService,
)
from pipedeck.planning import ConnectionPlanner


class FakeSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def is_readable(self, secret_id: str) -> bool:
        return secret_id in self.values

    def read(self, secret_id: str) -> str:
        return self.values[secret_id]


def resource(
    resource_id: str,
    kind: MiddlewareKind,
    container_port: int,
    host_port: int,
) -> RuntimeResource:
    return RuntimeResource(
        id=resource_id,
        name=resource_id,
        kind=kind,
        image=f"example/{kind.value}:latest",
        state="running",
        status_text="Up (healthy)",
        health=ResourceHealth.HEALTHY,
        managed=False,
        protected=True,
        ports=f"127.0.0.1:{host_port}->{container_port}/tcp",
        endpoints=(
            RuntimeEndpoint(
                protocol=EndpointProtocol.TCP,
                container_port=container_port,
                host_port=host_port,
            ),
        ),
    )


def project(*requirements: MiddlewareKind) -> ProjectSummary:
    return ProjectSummary(
        id="supplier",
        name="supplier-backend-v2",
        path="D:/code/supplier-backend-v2",
        kind=ProjectKind.PYTHON_UV,
        branch="main",
        dirty=False,
        commands=(),
        requirements=requirements,
        warnings=(),
    )


def workspace(
    service: WorkspaceService,
    *bindings: MiddlewareBinding,
) -> WorkspaceRecord:
    now = datetime.now(UTC)
    return WorkspaceRecord(
        id="workspace-1",
        revision=1,
        name="Supplier local",
        mode=RunMode.DEVELOPMENT,
        services=(service,),
        bindings=bindings,
        created_at=now,
        updated_at=now,
    )


def postgres_service(
    *environment: EnvironmentBinding,
) -> WorkspaceService:
    return WorkspaceService(
        project_id="supplier",
        environment=environment,
        connection_profiles=(
            PostgresConnectionProfile(
                kind=MiddlewareKind.POSTGRES,
                env_var="SUPPLIER_DATABASE_URL",
                scheme="postgresql+asyncpg",
                username="supplier user",
                database="supplier/db",
                secret_ref="pg-password",
            ),
        ),
    )


def test_postgres_binding_selects_actual_host_endpoint_and_encodes_url() -> None:
    pg_a = resource("pg-a", MiddlewareKind.POSTGRES, 5432, 15432)
    pg_b = resource("pg-b", MiddlewareKind.POSTGRES, 5432, 25432)
    runtime = RuntimeResponse(
        generated_at=datetime.now(UTC),
        docker_available=True,
        resources=(pg_a, pg_b),
    )
    planner = ConnectionPlanner(FakeSecrets({"pg-password": "p@ ss/word"}))
    service = postgres_service()

    selected_a = workspace(
        service,
        MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg-a"),
    )
    selected_b = workspace(
        service,
        MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg-b"),
    )

    first = planner.resolve(service, selected_a.bindings, runtime)[0]
    second = planner.resolve(service, selected_b.bindings, runtime)[0]
    preview = planner.analyze(selected_a, (project(MiddlewareKind.POSTGRES),), runtime)

    assert first.value == (
        "postgresql+asyncpg://supplier%20user:p%40%20ss%2Fword@127.0.0.1:15432/supplier%2Fdb"
    )
    assert second.value.endswith("@127.0.0.1:25432/supplier%2Fdb")
    assert "p@ ss/word" in first.redaction_values
    assert "p%40%20ss%2Fword" in first.redaction_values
    assert preview.blockers == ()
    assert preview.mappings[0].resource_id == "pg-a"
    assert preview.mappings[0].outputs[0].redacted_value == (
        "postgresql+asyncpg://supplier%20user:***@127.0.0.1:15432/supplier%2Fdb"
    )


def test_compose_consumer_uses_host_docker_internal_in_preview_and_execution() -> None:
    runtime = RuntimeResponse(
        generated_at=datetime.now(UTC),
        docker_available=True,
        resources=(resource("pg", MiddlewareKind.POSTGRES, 5432, 15432),),
    )
    service = postgres_service().model_copy(
        update={
            "execution_target": ComposeTarget(
                kind="compose",
                source=DockerfileSource(kind="dockerfile"),
                endpoints=(ComposeEndpoint(name="api", host_port=18000, container_port=8000),),
                readiness=TcpReadiness(kind="tcp", endpoint="api"),
            )
        }
    )
    binding = MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg")
    selected = workspace(service, binding)
    planner = ConnectionPlanner(FakeSecrets({"pg-password": "password"}))

    resolved = planner.resolve(
        service,
        selected.bindings,
        runtime,
        consumer_host="host.docker.internal",
    )
    preview = planner.analyze(selected, (project(MiddlewareKind.POSTGRES),), runtime)

    assert "@host.docker.internal:15432/" in resolved[0].value
    assert "@host.docker.internal:15432/" in preview.mappings[0].outputs[0].redacted_value


def test_minio_profile_generates_endpoint_credentials_and_bucket_outputs() -> None:
    service = WorkspaceService(
        project_id="supplier",
        connection_profiles=(
            MinioConnectionProfile(
                kind=MiddlewareKind.MINIO,
                endpoint_env="SUPPLIER_S3_ENDPOINT",
                access_key_env="SUPPLIER_S3_ACCESS_KEY",
                secret_key_env="SUPPLIER_S3_SECRET_KEY",
                bucket_env="SUPPLIER_S3_BUCKET",
                bucket="local-assets",
                access_key_secret_ref="minio-access",
                secret_key_secret_ref="minio-secret",
            ),
        ),
    )
    binding = MiddlewareBinding(kind=MiddlewareKind.MINIO, resource_id="minio")
    runtime = RuntimeResponse(
        generated_at=datetime.now(UTC),
        docker_available=True,
        resources=(resource("minio", MiddlewareKind.MINIO, 9000, 19000),),
    )
    planner = ConnectionPlanner(
        FakeSecrets({"minio-access": "local-access", "minio-secret": "local-secret"})
    )

    resolved = planner.resolve(service, (binding,), runtime)
    analyzed = planner.analyze(
        workspace(service, binding),
        (project(MiddlewareKind.MINIO),),
        runtime,
    )

    assert tuple((item.name, item.value) for item in resolved) == (
        ("SUPPLIER_S3_ENDPOINT", "http://127.0.0.1:19000"),
        ("SUPPLIER_S3_ACCESS_KEY", "local-access"),
        ("SUPPLIER_S3_SECRET_KEY", "local-secret"),
        ("SUPPLIER_S3_BUCKET", "local-assets"),
    )
    assert analyzed.blockers == ()
    assert tuple(output.redacted_value for output in analyzed.mappings[0].outputs) == (
        "http://127.0.0.1:19000",
        "***",
        "***",
        "local-assets",
    )


def test_connection_plan_blocks_conflict_missing_endpoint_and_missing_secret() -> None:
    explicit = EnvironmentBinding(
        name="SUPPLIER_DATABASE_URL",
        source=EnvironmentSource.HOST_ENV,
        reference="EXISTING_DATABASE_URL",
    )
    service = postgres_service(explicit)
    selected = workspace(
        service,
        MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg"),
    )
    runtime = RuntimeResponse(
        generated_at=datetime.now(UTC),
        docker_available=True,
        resources=(
            RuntimeResource(
                id="pg",
                name="pg",
                kind=MiddlewareKind.POSTGRES,
                image="postgres:18",
                state="running",
                status_text="Up (healthy)",
                health=ResourceHealth.HEALTHY,
                managed=False,
                protected=True,
                ports="",
            ),
        ),
    )

    result = ConnectionPlanner(FakeSecrets({})).analyze(
        selected,
        (project(MiddlewareKind.POSTGRES),),
        runtime,
    )

    codes = {issue.code for issue in result.blockers}
    assert "CONNECTION_ENV_CONFLICT" in codes
    assert "CONNECTION_ENDPOINT_MISSING" in codes
    assert result.mappings == ()


def test_connection_plan_blocks_unreadable_credential_after_endpoint_resolution() -> None:
    service = postgres_service()
    binding = MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg")
    runtime = RuntimeResponse(
        generated_at=datetime.now(UTC),
        docker_available=True,
        resources=(resource("pg", MiddlewareKind.POSTGRES, 5432, 15432),),
    )

    result = ConnectionPlanner(FakeSecrets({})).analyze(
        workspace(service, binding),
        (project(MiddlewareKind.POSTGRES),),
        runtime,
    )

    assert "CONNECTION_SECRET_MISSING" in {issue.code for issue in result.blockers}
    assert result.mappings == ()
