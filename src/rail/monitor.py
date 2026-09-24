"""red-monitor as the rail reads it (spec §6 step 8): one `GET /api/latest` — the server's
JSON of every agent's last snapshot — reduced to one agent and one stack. Read-only, from
the host over the mesh; the HTTP call is injectable so no test needs a server."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rail.http import Http, HttpError, http_get


class MonitorError(Exception):
    """red-monitor did not answer, or answered something the rail cannot read."""


@dataclass(frozen=True, slots=True)
class Container:
    name: str
    stack: str
    image: str  # the reference the container was created from, `repo@sha256:…` when pinned
    state: str
    health: str


@dataclass(frozen=True, slots=True)
class Unit:
    name: str
    active_state: str
    sub_state: str


@dataclass(frozen=True, slots=True)
class AgentView:
    agent: str
    status: str
    last_seen: datetime | None
    containers: tuple[Container, ...]
    units: tuple[Unit, ...] = ()  # systemd units, for a target that runs no container


def _instant(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def read_agent(
    base_url: str, agent: str, *, http: Http = http_get, timeout: float = 5.0
) -> AgentView:
    url = base_url.rstrip("/") + "/api/latest"
    try:
        status, body = http(url, timeout)
    except HttpError as exc:
        raise MonitorError(str(exc)) from exc
    if status != 200:
        raise MonitorError(f"{url}: HTTP {status}")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise MonitorError(f"{url}: not JSON") from exc
    agents = payload.get("agents") if isinstance(payload, dict) else None
    if not isinstance(agents, dict) or agent not in agents:
        known = ", ".join(sorted(agents)) if isinstance(agents, dict) else "none"
        raise MonitorError(f"unknown agent {agent!r} (known: {known})")
    data = agents[agent] or {}
    docker = data.get("docker") or {}
    rows = docker.get("containers") if isinstance(docker, dict) else None
    containers = tuple(
        Container(
            name=str(row.get("name", "")),
            stack=str(row.get("stack", "")),
            image=str(row.get("image", "")),
            state=str(row.get("state", "")),
            health=str(row.get("health", "")),
        )
        for row in (rows or [])
        if isinstance(row, dict)
    )
    unit_rows = data.get("systemd")
    units = tuple(
        Unit(
            name=str(row.get("name", "")),
            active_state=str(row.get("active_state", "")),
            sub_state=str(row.get("sub_state", "")),
        )
        for row in (unit_rows if isinstance(unit_rows, list) else [])
        if isinstance(row, dict)
    )
    return AgentView(
        agent=agent,
        status=str(data.get("status", "")),
        last_seen=_instant(data.get("last_seen")),
        containers=containers,
        units=units,
    )


def stack_containers(view: AgentView, stack: str) -> list[Container]:
    return [c for c in view.containers if c.stack == stack]


def find_unit(view: AgentView, name: str) -> Unit | None:
    return next((unit for unit in view.units if unit.name == name), None)


def image_digest(reference: str) -> str | None:
    """`sha256:…` of a digest-pinned reference, None for a tag."""
    _, sep, digest = reference.partition("@")
    return digest if sep and digest.startswith("sha256:") else None
