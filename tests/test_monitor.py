"""red-monitor as the rail reads it: one `GET /api/latest`, one agent, one stack."""

import json
from datetime import UTC, datetime

import pytest

from rail.http import HttpError
from rail.monitor import MonitorError, Unit, find_unit, image_digest, read_agent, stack_containers

LATEST = {
    "updated_at": "2026-09-19T22:07:27.848307277+02:00",
    "agents": {
        "vps": {
            "status": "up",
            "last_seen": "2026-09-19T20:07:25Z",
            "errors": [],
            "docker": {
                "containers": [
                    {
                        "name": "red-probe-app-1",
                        "stack": "red-probe",
                        "image": "ghcr.io/hawkixs/red-probe@sha256:" + "a" * 64,
                        "state": "running",
                        "health": "healthy",
                        "cpu_percent": 0.1,
                    },
                    {
                        "name": "pls_traefik",
                        "stack": "pls_project",
                        "image": "traefik:v2.11.51",
                        "state": "running",
                        "health": "",
                    },
                ]
            },
        },
        "pc-gpu": {"status": "down", "last_seen": None, "errors": ["timeout"], "docker": None},
    },
}


def _http(status: int = 200, body: object = LATEST):
    calls: list[str] = []

    def fetch(url: str, timeout: float) -> tuple[int, bytes]:
        calls.append(url)
        return status, json.dumps(body).encode()

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_read_agent_reduces_the_snapshot_to_one_agent() -> None:
    http = _http()
    view = read_agent("http://192.0.2.2:8081", "vps", http=http)
    assert http.calls == ["http://192.0.2.2:8081/api/latest"]
    assert view.agent == "vps" and view.status == "up"
    assert view.last_seen == datetime(2026, 9, 19, 20, 7, 25, tzinfo=UTC)
    names = [c.name for c in view.containers]
    assert names == ["red-probe-app-1", "pls_traefik"]
    probe = stack_containers(view, "red-probe")
    assert len(probe) == 1 and probe[0].state == "running" and probe[0].health == "healthy"
    assert stack_containers(view, "red-nothing") == []


def test_a_down_agent_and_a_missing_docker_block_are_readable() -> None:
    view = read_agent("http://192.0.2.2:8081", "pc-gpu", http=_http())
    assert view.status == "down" and view.last_seen is None and view.containers == ()


def test_errors_are_monitor_errors() -> None:
    with pytest.raises(MonitorError, match="unknown agent"):
        read_agent("http://192.0.2.2:8081", "moon", http=_http())
    with pytest.raises(MonitorError, match="HTTP 503"):
        read_agent("http://192.0.2.2:8081", "vps", http=_http(status=503))
    with pytest.raises(MonitorError, match="not JSON"):
        read_agent("http://192.0.2.2:8081", "vps", http=lambda u, t: (200, b"<html>"))

    def refused(url: str, timeout: float) -> tuple[int, bytes]:
        raise HttpError(f"{url}: connection refused")

    with pytest.raises(MonitorError, match="refused"):
        read_agent("http://192.0.2.2:8081", "vps", http=refused)


def test_image_digest_reads_a_pinned_reference_only() -> None:
    assert image_digest("ghcr.io/hawkixs/red-probe@sha256:" + "a" * 64) == "sha256:" + "a" * 64
    assert image_digest("ghcr.io/hawkixs/red-probe:0.1.0") is None
    assert image_digest("traefik:v2.11.51") is None


def test_read_agent_reads_the_systemd_units_too() -> None:
    snapshot = json.loads(json.dumps(LATEST))
    snapshot["agents"]["vps"]["systemd"] = [
        {
            "name": "red-agent.service",
            "load_state": "loaded",
            "active_state": "active",
            "sub_state": "running",
        },
        "not a row",
    ]
    view = read_agent("http://192.0.2.2:8081", "vps", http=_http(body=snapshot))
    running = Unit(name="red-agent.service", active_state="active", sub_state="running")
    assert view.units == (running,)
    assert find_unit(view, "red-agent.service") == view.units[0]
    assert find_unit(view, "other.service") is None


def test_an_agent_without_systemd_rows_has_no_units() -> None:
    assert read_agent("http://192.0.2.2:8081", "vps", http=_http()).units == ()
