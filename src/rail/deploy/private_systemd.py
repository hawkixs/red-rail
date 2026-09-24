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

The check cannot always tell what systemd will actually do with a value: systemd ignores a
value it cannot parse and silently keeps the previous or default one, it normalises what it
does accept (zero-length time spans, `%` specifiers, boolean spellings, quoting, C-style
escapes, `;` command separators), and it splits lines differently from a naive reader. So
every rule below is an ALLOW-list, never a deny-list: a value this check cannot read with
certainty is refused, even when the unit would in fact have been safe. A false refusal of an
unusual-but-safe unit is an accepted cost; a false acceptance of a unit that would run as root
or unbounded is not.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# `+`, `!` and `!!` run a command with full privileges whatever `User=` says; `@`, `-` and `:`
# change how it runs, not who runs it (systemd.service, "Command lines").
_EXEC_PREFIXES = frozenset("@-:+!")
_PRIVILEGED = frozenset("+!")

# The directives that name a command systemd runs. Deliberately not every `Exec*=` key:
# `ExecPaths=` and `ExecSearchPath=` name search paths, not a command, and must not be read
# as one.
_COMMAND_KEYS = (
    "ExecCondition",
    "ExecStartPre",
    "ExecStart",
    "ExecStartPost",
    "ExecReload",
    "ExecStop",
    "ExecStopPost",
)

# Quoting or a backslash can change how systemd splits or unescapes a command line (a `\x2b`
# escape can turn into a `+` prefix after this check has already decided the value looks
# unprivileged); a standalone `;` word starts a second command that systemd runs and this
# check would otherwise never see. All three make a value unreadable with certainty.
_UNSAFE_IN_COMMAND = frozenset("\"'\\")

# Plain POSIX usernames only: refuses `%u`/`%U` specifiers, numeric ids and empty values.
_USERNAME = re.compile(r"^[a-z_][a-z0-9_-]*$")
# A byte count with at most one K/M/G/T suffix: refuses `infinity`, `max`, `100%`, and a
# multi-letter or lower-case suffix systemd does not recognise (`64MB`, `512m`). The bound
# below catches a count that fullmatches this but is too large for systemd to accept.
_MEMORY = re.compile(r"^(?P<digits>[1-9][0-9]*)(?P<suffix>[KMGT])?$")
_MEMORY_MULTIPLIER = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
_MEMORY_MAX_BYTES = 2**62
# A single positive duration token: refuses `0`, any zero-length span, `infinity`, and a
# multi-part span systemd would otherwise accept (`1min 30s`).
_DURATION = re.compile(r"^[1-9][0-9]*(?:ms|s|sec|m|min)?$")

_LINE_BREAK = re.compile(r"\r\n|\r|\n")
# Command-line words as systemd splits them: runs of ASCII space and tab only.
_WORD_BREAK = re.compile(r"[ \t]+")


def _logical_lines(text: str) -> Iterator[str]:
    """Lines as systemd reads them: split on a bare CR, LF or CRLF only — never the wider set
    `str.splitlines()` breaks on (`\\v`, `\\f`, ...). A physical line continues only behind an
    ODD number of trailing backslashes on the RAW line (one behind trailing whitespace, or an
    even count, ends the line for systemd, same as no backslash at all); the final backslash is
    then dropped and any others are kept. A comment is a line whose content, stripped of spaces
    and tabs only, starts with `#` or `;` — dropped even inside a continuation, without ending
    it."""
    parts: list[str] = []
    for raw in _LINE_BREAK.split(text):
        trailing = len(raw) - len(raw.rstrip("\\"))
        continues = trailing % 2 == 1
        body = raw[:-1] if continues else raw
        content = body.strip(" \t")
        if content[:1] in ("#", ";"):
            continue
        parts.append(content)
        if continues:
            continue
        line = " ".join(part for part in parts if part)
        parts = []
        if line:
            yield line
    line = " ".join(part for part in parts if part)
    if line:
        yield line


def parse_unit(text: str) -> dict[str, dict[str, list[str]]]:
    """Section → key → values, in file order. Only what the refusals read is modelled: this is
    not a systemd parser and does not pretend to be one. A section name is taken verbatim, with
    no stripping — `[ Service ]` is a different, unknown section, not `[Service]` with cosmetic
    padding, matching systemd's own exact-name lookup. A key and a value are stripped of spaces
    and tabs only — the same set `_logical_lines` strips content with, never `str.strip()` with
    no argument: systemd itself only ever strips ASCII space and tab from an lvalue or rvalue, so
    a key or value carrying any other whitespace (a NO-BREAK SPACE, a form feed, a vertical tab)
    must be kept verbatim. `User\\xa0` is then an unknown key, not `User` with cosmetic padding
    (so `User=` reads as missing); a lone `\\xa0` value is non-empty, not the empty value that
    resets a list."""
    sections: dict[str, dict[str, list[str]]] = {}
    section: dict[str, list[str]] | None = None
    for line in _logical_lines(text):
        if line.startswith("[") and line.endswith("]"):
            section = sections.setdefault(line[1:-1], {})
        elif section is not None and "=" in line:
            key, _, value = line.partition("=")
            section.setdefault(key.strip(" \t"), []).append(value.strip(" \t"))
    return sections


def _effective(values: list[str]) -> list[str]:
    """Only the values assigned after the last empty one: systemd resets a list-valued
    directive to empty on a bare `Key=`, so nothing assigned before that reset survives."""
    for index in range(len(values) - 1, -1, -1):
        if values[index] == "":
            return values[index + 1 :]
    return list(values)


def _prefix(value: str) -> str:
    """The run-mode prefix characters at the start of an `Exec*=` value."""
    end = 0
    while end < len(value) and value[end] in _EXEC_PREFIXES:
        end += 1
    return value[:end]


def _words(value: str) -> list[str]:
    """Command-line words as systemd splits them: runs of ASCII space and tab only — never
    `str.split()` with no argument, whose wider Unicode definition also breaks on `\\xa0`,
    `\\x0b`, .... A word boundary this check sees but systemd does not would let a value that is
    really one unbroken (and most likely non-existent) path read as if it were the release
    binary followed by an argument, or hide a `;`-separated second command inside what systemd
    reads as a single word."""
    return [word for word in _WORD_BREAK.split(value) if word]


def _program(value: str) -> str:
    words = _words(value[len(_prefix(value)) :])
    return words[0] if words else ""


def _memory_ok(value: str) -> bool:
    """Whether `value` is a byte count systemd would accept for `MemoryMax=`: digits with an
    optional K/M/G/T suffix, no larger than `_MEMORY_MAX_BYTES` bytes."""
    match = _MEMORY.fullmatch(value)
    if match is None:
        return False
    suffix = match.group("suffix") or ""
    return int(match.group("digits")) * _MEMORY_MULTIPLIER[suffix] <= _MEMORY_MAX_BYTES


def unit_refusals(text: str, *, unit: str, current: str, binary_name: str) -> list[str]:
    """Every rule of spec decision 7 the unit breaks, one sentence each, each starting with the
    unit's name; empty means accepted. `current` is `<stack_root>/<project>/current`."""
    service = parse_unit(text).get("Service")
    if service is None:
        return [f"{unit} has no [Service] section"]

    refusals: list[str] = []

    users = service.get("User", [])
    if not users:
        refusals.append(f"{unit}: User= must name a user other than root (no value set)")
    refusals.extend(
        f"{unit}: User={value} must be a plain username other than root, e.g. red-monitor "
        f"(got {value!r})"
        for value in users
        if value == "root" or not _USERNAME.fullmatch(value)
    )

    memory_values = service.get("MemoryMax", [])
    if not memory_values:
        refusals.append(
            f"{unit}: MemoryMax= must bound the service, the host has no swap (no value set)"
        )
    refusals.extend(
        f"{unit}: MemoryMax={value} must be a byte count with an optional K/M/G/T suffix, at "
        f"most {_MEMORY_MAX_BYTES} bytes, e.g. 64M (got {value!r})"
        for value in memory_values
        if not _memory_ok(value)
    )

    # `TimeoutSec=` is a shorthand that sets the start AND the stop timeout, so a bad value
    # there is refused exactly like a bad `TimeoutStopSec=`.
    timeouts = [("TimeoutStopSec", value) for value in service.get("TimeoutStopSec", [])] + [
        ("TimeoutSec", value) for value in service.get("TimeoutSec", [])
    ]
    if not timeouts:
        refusals.append(f"{unit}: TimeoutStopSec= must bound the stop (no value set)")
    refusals.extend(
        f"{unit}: {key}={value} must be a positive duration such as 15 or 15s (got {value!r})"
        for key, value in timeouts
        if not _DURATION.fullmatch(value)
    )

    program = f"{current}/{binary_name}"
    starts = _effective(service.get("ExecStart", []))
    if len(starts) != 1 or _program(starts[0]) != program:
        refusals.append(f"{unit}: exactly one ExecStart= must run {program} (got {starts!r})")

    environment = f"{current}/release.env"
    files = _effective(service.get("EnvironmentFile", []))
    if environment not in files:
        refusals.append(
            f"{unit}: EnvironmentFile={environment} is missing, without the `-` prefix: "
            "the file must exist"
        )

    for key in _COMMAND_KEYS:
        for value in _effective(service.get(key, [])):
            if _UNSAFE_IN_COMMAND & set(value) or ";" in _words(value):
                refusals.append(
                    f"{unit}: {key}={value} systemd would read a different command than this "
                    "check sees (quoting, a backslash or a `;` separator)"
                )
            elif _PRIVILEGED & set(_prefix(value)):
                refusals.append(f"{unit}: {key}={value} runs with full privileges (prefix + or !)")

    if "PermissionsStartOnly" in service:
        refusals.append(f"{unit}: PermissionsStartOnly= runs the Exec*Pre/Post commands as root")

    return refusals
