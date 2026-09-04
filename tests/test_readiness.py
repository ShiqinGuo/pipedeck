from __future__ import annotations

from dataclasses import dataclass

from pipedeck.compose_deployment import HttpProbe, TcpProbe
from pipedeck.readiness import ReadinessProbeRunner


@dataclass
class FakeClock:
    now: float = 0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeTransport:
    def __init__(self, outcomes: tuple[bool, ...]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, str, int | None, float]] = []

    def _next(self) -> bool:
        return self._outcomes.pop(0) if self._outcomes else False

    def http_ready(self, url: str, timeout_seconds: float) -> bool:
        self.calls.append(("http", url, None, timeout_seconds))
        return self._next()

    def tcp_ready(self, host: str, port: int, timeout_seconds: float) -> bool:
        self.calls.append(("tcp", host, port, timeout_seconds))
        return self._next()


def test_http_probe_retries_until_ready() -> None:
    clock = FakeClock()
    transport = FakeTransport((False, False, True))
    runner = ReadinessProbeRunner(
        transport,
        retry_interval_seconds=0.25,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = runner.wait(HttpProbe("http://127.0.0.1:8100/ready", timeout_seconds=2))

    assert result.ready is True
    assert [call[0] for call in transport.calls] == ["http", "http", "http"]


def test_tcp_probe_reports_timeout_without_exposing_transport_error() -> None:
    clock = FakeClock()
    transport = FakeTransport(())
    runner = ReadinessProbeRunner(
        transport,
        retry_interval_seconds=0.4,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = runner.wait(TcpProbe("127.0.0.1", 8101, timeout_seconds=1))

    assert result.ready is False
    assert result.detail == "Explicit readiness probe timed out"
    assert transport.calls[0][:3] == ("tcp", "127.0.0.1", 8101)


def test_probe_stops_when_target_exits() -> None:
    clock = FakeClock()
    transport = FakeTransport((False,))
    alive_checks = iter((True, False))
    runner = ReadinessProbeRunner(
        transport,
        retry_interval_seconds=0.2,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = runner.wait(
        TcpProbe("127.0.0.1", 8102, timeout_seconds=2),
        target_alive=lambda: next(alive_checks),
    )

    assert result.ready is False
    assert result.detail == "Application exited during readiness verification"


def test_probe_honors_cancellation_before_attempt() -> None:
    transport = FakeTransport((True,))
    runner = ReadinessProbeRunner(transport)

    result = runner.wait(
        HttpProbe("http://127.0.0.1:8103/health", timeout_seconds=1),
        cancelled=lambda: True,
    )

    assert result.ready is False
    assert result.detail == "Readiness verification cancelled"
    assert transport.calls == []
