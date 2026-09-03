from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_state_db_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "Pipedeck" / "state.db"


class LocalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PIPEDECK_",
        extra="ignore",
        enable_decoding=False,
    )

    scan_roots: Annotated[tuple[Path, ...], Field(min_length=1)] = (Path("D:/code"),)
    api_host: str = "127.0.0.1"
    api_port: Annotated[int, Field(ge=1024, le=65535)] = 7421
    scan_max_depth: Annotated[int, Field(ge=1, le=8)] = 5
    state_db_path: Path = _default_state_db_path()
    api_token: SecretStr | None = None

    @field_validator("scan_roots", mode="before")
    @classmethod
    def parse_scan_roots(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(Path(item.strip()) for item in value.split(";") if item.strip())
        return value
