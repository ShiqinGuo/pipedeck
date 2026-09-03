from __future__ import annotations

import socket
import time
from collections.abc import Callable
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from pipedeck.compose_deployment import (
    DeploymentProbe,
    DeploymentProbeResult,
    HttpProbe,
    ResolvedDeploymentVariable,
)


class ReadinessTransport(Protocol):
    def http_ready(self, url: str, timeout_seconds: float) -> bool: ...

    def tcp_ready(self, host: str, port: int, timeout_seconds: float) -> bool: ...


class ReadinessConfigurationError(ValueError):
    detail = "readiness retry interval 必须大于 0"

    def __init__(self) -> None:
        super().__init__(self.detail)


class LocalReadinessTransport:
    def http_ready(self, url: str, timeout_seconds: float) -> bool:
        try:
            with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310
                return 200 <= response.status < 400
        except (HTTPError, URLError, TimeoutError, OSError):
            return False

    def tcp_ready(self, host: str, port: int, timeout_seconds: float) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout_seconds):
                return True
        except (TimeoutError, OSError):
            return False


class ReadinessProbeRunner:
    def __init__(
        self,
        transport: ReadinessTransport | None = None,
        *,
        retry_interval_seconds: float = 0.2,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if retry_interval_seconds <= 0:
            raise ReadinessConfigurationError()
        self._transport = transport or LocalReadinessTransport()
        self._retry_interval_seconds = retry_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep

    def verify(
        self,
        probe: DeploymentProbe,
        environment: tuple[ResolvedDeploymentVariable, ...] = (),
    ) -> DeploymentProbeResult:
        del environment
        return self.wait(probe)

    def wait(
        self,
        probe: DeploymentProbe,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        target_alive: Callable[[], bool] = lambda: True,
    ) -> DeploymentProbeResult:
        deadline = self._monotonic() + probe.timeout_seconds
        while True:
            if cancelled():
                return DeploymentProbeResult(False, "readiness 验证已取消")
            if not target_alive():
                return DeploymentProbeResult(False, "应用在 readiness 验证期间退出")

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return DeploymentProbeResult(False, "显式 readiness probe 超时")
            attempt_timeout = min(1.0, remaining)
            if self._ready(probe, attempt_timeout):
                return DeploymentProbeResult(True)
            self._sleep(min(self._retry_interval_seconds, max(remaining, 0.0)))

    def _ready(self, probe: DeploymentProbe, timeout_seconds: float) -> bool:
        if isinstance(probe, HttpProbe):
            return self._transport.http_ready(probe.url, timeout_seconds)
        return self._transport.tcp_ready(probe.host, probe.port, timeout_seconds)
