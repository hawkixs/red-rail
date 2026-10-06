"""Allow-list rules for the `private-timers` units (spec 2026-10-06-private-timers-target).

The unit syntax is deliberately read through `private_systemd`'s small parser and helpers.
Values the rail cannot establish as safe are refused before any remote operation.
"""

import posixpath

from rail.deploy.private_systemd import (
    _COMMAND_KEYS,
    _DURATION,
    _PRIVILEGED,
    _UNSAFE_IN_COMMAND,
    _USERNAME,
    _effective,
    _invisible_character_refusal,
    _memory_ok,
    _prefix,
    _program,
    _words,
    parse_unit,
)


def _inside(program: str, directory: str) -> bool:
    """A prefix test alone admits `<app>/../..`, and systemd runs what the path resolves to
    (commit security review): the program must already be normalised and strictly below the
    payload directory."""
    return (
        program.startswith(directory)
        and posixpath.normpath(program) == program
        and len(program) > len(directory)
    )


TIMER_TRIGGERS = (
    "OnCalendar",
    "OnBootSec",
    "OnUnitActiveSec",
    "OnUnitInactiveSec",
    "OnActiveSec",
    "OnStartupSec",
)


def service_refusals(text: str, *, unit: str, current: str) -> list[str]:
    """Return unit-named refusals for an unsafe oneshot service; an empty list accepts it."""
    invisible = _invisible_character_refusal(text, unit)
    if invisible is not None:
        return [invisible]

    service = parse_unit(text).get("Service")
    if service is None:
        return [f"{unit} has no [Service] section"]

    refusals: list[str] = []
    types = service.get("Type", [])
    if not types or any(value != "oneshot" for value in types):
        refusals.append(f"{unit}: Type=oneshot is required, a timer fires a run (got {types!r})")

    users = service.get("User", [])
    if not users:
        refusals.append(f"{unit}: User= must name a user other than root (no value set)")
    refusals.extend(
        f"{unit}: User={value} must be a plain username other than root (got {value!r})"
        for value in users
        if value == "root" or not _USERNAME.fullmatch(value)
    )

    memories = service.get("MemoryMax", [])
    if not memories:
        refusals.append(f"{unit}: MemoryMax= must bound the service (no value set)")
    refusals.extend(
        f"{unit}: MemoryMax={value} must be a bounded byte count (got {value!r})"
        for value in memories
        if not _memory_ok(value)
    )

    timeouts = [
        (key, value) for key in ("TimeoutStartSec", "TimeoutSec") for value in service.get(key, [])
    ]
    if not timeouts:
        refusals.append(
            f"{unit}: TimeoutStartSec= or TimeoutSec= must be set to a positive duration"
        )
    refusals.extend(
        f"{unit}: {key}={value} must be a positive duration (got {value!r})"
        for key, value in timeouts
        if not _DURATION.fullmatch(value)
    )

    starts = _effective(service.get("ExecStart", []))
    if not starts:
        refusals.append(f"{unit}: at least one ExecStart= must be set")

    app = f"{current}/app/"
    for key in _COMMAND_KEYS:
        for value in _effective(service.get(key, [])):
            if _UNSAFE_IN_COMMAND & set(value) or ";" in _words(value):
                refusals.append(f"{unit}: {key}={value} contains an unreadable command separator")
                continue
            if _PRIVILEGED & set(_prefix(value)):
                refusals.append(f"{unit}: {key}={value} runs with full privileges (prefix + or !)")
                continue
            if not _inside(_program(value), app):
                refusals.append(f"{unit}: {key} must run a program under {app} (got {value!r})")

    environment = f"{current}/release.env"
    files = _effective(service.get("EnvironmentFile", []))
    if environment not in files:
        refusals.append(f"{unit}: EnvironmentFile={environment} is required without the `-` prefix")

    if "PermissionsStartOnly" in service:
        refusals.append(f"{unit}: PermissionsStartOnly= is not allowed")
    return refusals


def timer_refusals(text: str, *, unit: str, declared: frozenset[str]) -> list[str]:
    """Return unit-named refusals for a timer that does not trigger its declared service."""
    invisible = _invisible_character_refusal(text, unit)
    if invisible is not None:
        return [invisible]

    timer = parse_unit(text).get("Timer")
    if timer is None:
        return [f"{unit} has no [Timer] section"]

    refusals: list[str] = []
    if not any(_effective(timer.get(key, [])) for key in TIMER_TRIGGERS):
        refusals.append(f"{unit}: at least one timer trigger must have a non-empty value")
    if "Unit" in timer:
        refusals.append(f"{unit}: Unit= is not allowed; a timer triggers its matching service")

    service = unit[: -len(".timer")] + ".service" if unit.endswith(".timer") else ""
    if service not in declared:
        refusals.append(f"{unit}: matching service {service or '(unknown)'} is not declared")
    return refusals
