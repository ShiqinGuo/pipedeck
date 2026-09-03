from __future__ import annotations

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from pipedeck.api import create_app
from pipedeck.contracts import EnvironmentBinding, EnvironmentSource, SecretMetadata
from pipedeck.settings import LocalSettings


class FakeCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def write(self, secret_id: str, value: str) -> None:
        self.values[secret_id] = value

    def read(self, secret_id: str) -> str:
        return self.values[secret_id]

    def delete(self, secret_id: str) -> None:
        del self.values[secret_id]


def test_secret_store_reference_accepts_digit_prefixed_opaque_id() -> None:
    binding = EnvironmentBinding(
        name="APP_SECRET",
        source=EnvironmentSource.SECRET_STORE,
        reference="0f12-secret-id",
    )

    assert binding.reference == "0f12-secret-id"


def test_secret_api_never_returns_or_persists_value_and_blocks_referenced_delete(
    tmp_path: Path,
) -> None:
    token = "local-write-token"
    database = tmp_path / "state.db"
    credentials = FakeCredentialStore()
    app = create_app(
        LocalSettings(
            scan_roots=(tmp_path,),
            state_db_path=database,
            api_token=SecretStr(token),
        ),
        secret_store=credentials,
    )
    headers = {"x-pipedeck-token": token}
    raw_value = "p@ss word/with-sensitive-parts"

    with TestClient(app) as client:
        unauthorized = client.post(
            "/api/v1/secrets",
            json={"name": "Supplier database", "value": raw_value},
        )
        created = client.post(
            "/api/v1/secrets",
            headers=headers,
            json={"name": "Supplier database", "value": raw_value},
        )

        assert unauthorized.status_code == 401
        assert created.status_code == 201
        secret_id = SecretMetadata.model_validate(created.json()).id
        assert "value" not in created.text
        assert raw_value not in created.text
        assert credentials.values[secret_id] == raw_value

        listed = client.get("/api/v1/secrets")
        assert listed.status_code == 200
        assert "value" not in listed.text
        assert raw_value not in listed.text

        updated_value = "new/@ encoded password"
        updated = client.put(
            f"/api/v1/secrets/{secret_id}",
            headers=headers,
            json={"expected_version": 1, "value": updated_value},
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2
        assert updated_value not in updated.text

        workspace = client.post(
            "/api/v1/workspaces",
            headers=headers,
            json={
                "name": "Secret consumer",
                "mode": "development",
                "services": [
                    {
                        "project_id": "supplier",
                        "environment": [
                            {
                                "name": "APP_SECRET",
                                "source": "secret-store",
                                "reference": secret_id,
                            }
                        ],
                    }
                ],
            },
        )
        assert workspace.status_code == 201

        blocked = client.delete(
            f"/api/v1/secrets/{secret_id}?expected_version=2",
            headers=headers,
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "SECRET_IN_USE"
        assert credentials.values[secret_id] == updated_value

        disposable = client.post(
            "/api/v1/secrets",
            headers=headers,
            json={"name": "Disposable", "value": "temporary-value"},
        )
        disposable_id = disposable.json()["id"]
        deleted = client.delete(
            f"/api/v1/secrets/{disposable_id}?expected_version=1",
            headers=headers,
        )
        assert deleted.status_code == 204
        assert disposable_id not in credentials.values

    persisted_files = (database, database.with_name(f"{database.name}-wal"))
    persisted = b"".join(path.read_bytes() for path in persisted_files if path.exists())
    assert raw_value.encode() not in persisted
    assert updated_value.encode() not in persisted
