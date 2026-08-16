from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CommandResult:
    return_code: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult: ...


class SubprocessRunner:
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult:
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                check=False,
                creationflags=creation_flags,
                encoding="utf-8",
                errors="replace",
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return CommandResult(
                return_code=1,
                stdout="",
                stderr=type(error).__name__,
            )
        return CommandResult(
            return_code=completed.returncode,
            stdout=(completed.stdout or "").strip(),
            stderr=(completed.stderr or "").strip(),
        )
