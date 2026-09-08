import pytest
from pydantic import ValidationError

from pipedeck.contracts import ApplicationEntry, HostTarget, WorkspaceService


@pytest.mark.parametrize(
    "path", ("https://example.com", "//example.com", "/\\example.com", "/app\n")
)
def test_application_entry_cannot_change_the_declared_local_origin(path: str) -> None:
    with pytest.raises(ValidationError):
        ApplicationEntry(endpoint="http", path=path)


def test_application_entry_rejects_missing_endpoint() -> None:
    with pytest.raises(ValidationError, match="declared TCP endpoint"):
        WorkspaceService(
            project_id="api",
            execution_target=HostTarget(
                application=ApplicationEntry(endpoint="missing", path="/docs"),
            ),
        )
