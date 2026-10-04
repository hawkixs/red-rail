"""Who runs a rail gesture, resolved once and never defaulted (spec 2026-10-03-actor-identity).

The threat is error and honesty: a harness declares itself, the rail never records an agent as
the operator by default. A deliberately lying agent is out of scope and declared as such."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from rail.policy import ACTOR_VARIABLE, AGENT_MARKERS, AGENT_NAME_VARIABLE

MAX_LABEL = 64
_DECLARED = re.compile(r"operator|agent:[a-z0-9-]+(?::[A-Za-z0-9._-]+)?|service:[a-z0-9-]+")


class ActorKind(StrEnum):
    OPERATOR = "operator"
    AGENT = "agent"
    SERVICE = "service"


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    label: str


class ActorRefused(ValueError):
    """The rail cannot tell who runs the gesture: nothing has been written."""


def _kind(label: str) -> ActorKind:
    return ActorKind(label.split(":", 1)[0])


def _marked(environ: Mapping[str, str]) -> Actor | None:
    for marker in AGENT_MARKERS:
        if any(environ.get(name) for name in marker.presence):
            session = environ.get(marker.session or "", "")
            if not session:
                return Actor(ActorKind.AGENT, f"agent:{marker.harness}")
            prefix = f"agent:{marker.harness}:"
            safe = re.sub(r"[^A-Za-z0-9._-]", "-", session)
            return Actor(ActorKind.AGENT, (prefix + safe)[:MAX_LABEL])
    named = environ.get(AGENT_NAME_VARIABLE)
    if named:
        name = re.sub(r"[^a-z0-9-]+", "-", named.lower()).strip("-") or "unknown"
        return Actor(ActorKind.AGENT, f"agent:{name}"[:MAX_LABEL])
    return None


def resolve_actor(environ: Mapping[str, str], stdin_is_tty: bool) -> Actor:
    marked = _marked(environ)
    declared = environ.get(ACTOR_VARIABLE, "")
    if declared:
        if len(declared) > MAX_LABEL or not _DECLARED.fullmatch(declared):
            raise ActorRefused(
                f"{ACTOR_VARIABLE}={declared!r} is not an actor label: "
                "operator, agent:<harness>[:<session>] or service:<name>, 64 characters at most"
            )
        if declared == "operator" and marked is not None:
            raise ActorRefused(
                f"{ACTOR_VARIABLE}=operator while {marked.label} is running: an operator "
                "variable inherited by an agent. Unset it for the agent, or name the agent"
            )
        return Actor(_kind(declared), declared)
    if marked is not None:
        return marked
    if stdin_is_tty:
        return Actor(ActorKind.OPERATOR, "operator")
    raise ActorRefused(
        "cannot tell who runs this gesture (no agent marker, no terminal): set "
        f"{ACTOR_VARIABLE}=agent:<name> or {ACTOR_VARIABLE}=service:<name>"
    )


def stdin_is_tty() -> bool:
    """A detached process has no stdin, or a closed one: that is no terminal, not a crash."""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def current_actor() -> Actor:
    return resolve_actor(os.environ, stdin_is_tty())
