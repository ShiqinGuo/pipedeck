import os
from pathlib import Path
from unittest.mock import patch

import pytest

from pipedeck.__main__ import serve


def test_serve_starts_uvicorn_with_app_and_writes_cli_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "cli-token"

    def _fake_write(_state_dir: Path, token: str) -> None:
        token_file.write_text(token, encoding="utf-8")

    monkeypatch.delenv("PIPEDECK_API_TOKEN", raising=False)
    with (
        patch("pipedeck.__main__.uvicorn.run") as run,
        patch("pipedeck.__main__.LocalSettings") as settings_cls,
        patch("pipedeck.__main__._write_cli_token", side_effect=_fake_write) as write_spy,
    ):
        settings = settings_cls.return_value
        settings.api_token = None
        settings.state_db_path = tmp_path / "state.db"
        settings.api_host = "127.0.0.1"
        settings.api_port = 7421

        serve(host=None, port=None)

    assert run.call_count == 1
    app_arg = run.call_args.args[0]
    assert app_arg.title == "Pipedeck API"
    write_spy.assert_called_once()
    token = token_file.read_text(encoding="utf-8").strip()
    assert len(token) >= 24
    assert os.environ.get("PIPEDECK_API_TOKEN") == token
    monkeypatch.delenv("PIPEDECK_API_TOKEN", raising=False)
