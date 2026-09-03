"""`pipedeck serve`：启动本地控制面并写 cli-token，供 CLI 与桌面端互信。"""

import os
import secrets as secrets_module
from pathlib import Path

import uvicorn

from pipedeck.settings import LocalSettings


def serve(host: str | None = None, port: int | None = None) -> None:
    settings = LocalSettings()
    token = settings.api_token.get_secret_value() if settings.api_token else None
    if not token:
        token = secrets_module.token_hex(24)
    _write_cli_token(settings.state_db_path.parent, token)
    os.environ.setdefault("PIPEDECK_API_TOKEN", token)
    from pipedeck.api import create_app

    uvicorn.run(
        create_app(settings),
        host=host or settings.api_host,
        port=port or settings.api_port,
        log_level="info",
    )


def _write_cli_token(state_dir: Path, token: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "cli-token"
    path.write_text(token, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
