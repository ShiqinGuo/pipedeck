from pathlib import Path

from pytest import MonkeyPatch

from tripguru_local.settings import LocalSettings


def test_scan_roots_accepts_semicolon_delimited_environment_value(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TRIPGURU_LOCAL_SCAN_ROOTS",
        "D:\\code;D:\\worktrees",
    )

    settings = LocalSettings()

    assert settings.scan_roots == (Path("D:/code"), Path("D:/worktrees"))
