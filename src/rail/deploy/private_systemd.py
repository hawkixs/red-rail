"""Target `private-systemd`: a binary that systemd runs, on a host with no public route (spec
2026-09-24-private-systemd-target).

The artefact is the project's released image. The target copies the binary out of it by
digest, writes the project's unit (read at the released commit) and `release.env` next to
it, points `current` at the release, and restarts the unit through the two commands the
host's sudoers file allows. systemd loads the unit through a link to `current`, made once at
migration, so the unit on disk is always the live release's.

Before anything reaches the machine, the unit is refused when it would run as root, run
unbounded, or run something other than the release (decision 7). That guards against a
mistake, not against a compromised deploy account: that account is in the docker group, which
is root already.
"""

from __future__ import annotations

from collections.abc import Iterator

# `+`, `!` and `!!` run a command with full privileges whatever `User=` says; `@`, `-` and `:`
# change how it runs, not who runs it (systemd.service, "Command lines")
_EXEC_PREFIXES = frozenset("@-:+!")
_PRIVILEGED = frozenset("+!")
_TRUE = frozenset({"1", "yes", "true", "on"})


def _logical_lines(text: str) -> Iterator[str]:
    """Lines as systemd reads them: comments dropped, inside a continuation too, and a trailing
    backslash joining a line to the next with a space."""
    parts: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped[:1] in ("#", ";"):
            continue
        if stripped.endswith("\\"):
            parts.append(stripped[:-1].strip())
            continue
        parts.append(stripped)
        line = " ".join(part for part in parts if part)
        parts = []
        if line:
            yield line
    line = " ".join(part for part in parts if part)
    if line:
        yield line


def parse_unit(text: str) -> dict[str, dict[str, list[str]]]:
    """Section → key → values, in file order. Only what the refusals read is modelled: this is
    not a systemd parser and does not pretend to be one."""
    sections: dict[str, dict[str, list[str]]] = {}
    section: dict[str, list[str]] | None = None
    for line in _logical_lines(text):
        if line.startswith("[") and line.endswith("]"):
            section = sections.setdefault(line[1:-1].strip(), {})
        elif section is not None and "=" in line:
            key, _, value = line.partition("=")
            section.setdefault(key.strip(), []).append(value.strip())
    return sections


def _prefix(value: str) -> str:
    """The run-mode prefix characters at the start of an `Exec*=` value."""
    end = 0
    while end < len(value) and value[end] in _EXEC_PREFIXES:
        end += 1
    return value[:end]


def _program(value: str) -> str:
    words = value[len(_prefix(value)) :].split()
    return words[0] if words else ""


def unit_refusals(text: str, *, unit: str, current: str, binary_name: str) -> list[str]:
    """Every rule of spec decision 7 the unit breaks, one sentence each, each starting with the
    unit's name; empty means accepted. `current` is `<stack_root>/<project>/current`."""
    service = parse_unit(text).get("Service")
    if service is None:
        return [f"{unit} has no [Service] section"]

    def last(key: str) -> str | None:
        values = service.get(key)
        return values[-1] if values else None  # systemd keeps the last assignment

    refusals: list[str] = []
    user = last("User")
    if user in (None, "", "root", "0"):
        refusals.append(f"{unit}: User= must name a user other than root (got {user!r})")
    memory = last("MemoryMax")
    if memory in (None, "", "infinity"):
        refusals.append(
            f"{unit}: MemoryMax= must bound the service, the host has no swap (got {memory!r})"
        )
    stop = last("TimeoutStopSec")
    if stop in (None, "", "0", "infinity"):
        refusals.append(f"{unit}: TimeoutStopSec= must bound the stop (got {stop!r})")
    program = f"{current}/{binary_name}"
    starts = [value for value in service.get("ExecStart", []) if value]
    if len(starts) != 1 or _program(starts[0]) != program:
        refusals.append(f"{unit}: exactly one ExecStart= must run {program} (got {starts!r})")
    environment = f"{current}/release.env"
    if environment not in service.get("EnvironmentFile", []):
        refusals.append(
            f"{unit}: EnvironmentFile={environment} is missing, without the `-` prefix: "
            "the file must exist"
        )
    for key, values in service.items():
        if key.startswith("Exec"):
            refusals.extend(
                f"{unit}: {key}={value} runs with full privileges (prefix + or !)"
                for value in values
                if _PRIVILEGED & set(_prefix(value))
            )
    if (last("PermissionsStartOnly") or "").lower() in _TRUE:
        refusals.append(f"{unit}: PermissionsStartOnly= runs the Exec*Pre/Post commands as root")
    return refusals
