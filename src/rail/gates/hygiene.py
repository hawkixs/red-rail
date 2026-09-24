"""Hygiene gates: the floor every tier stands on, starting with `bootstrap`.

`remotes` and `roster_entry` are workstation-scoped: they read the operator's clone (the GitHub
remote — a mirror only where the manifest declares one — and the ReD root roster, found by
walking up to the first `CLAUDE.md` holding its identity header) and are reported as skipped
under `--ci`.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from rail import gitrepo, markdown
from rail.gates import GateResult, GateSpec, Stage
from rail.ledger import BRAIN_MILESTONES, RECEIPTS_DIR, LedgerError, RecordKind, open_ledger
from rail.ledger.file import FileLedger, load_receipt, receipt_filename
from rail.model import (
    MISSING_HINT,
    LedgerBackend,
    declarations,
    load_rail_config,
    manifest_problem,
    try_load_rail_config,
)
from rail.policy import effective

DOCS_DIRS = ("docs/specs", "docs/plans", "docs/adr")
# The ReD root's identity table is recognised by its whole header: a bare `| Project |` prefix
# also opens every "Related projects" table (spec 2026-09-24-template-alignment, decision 2).
ROSTER_HEADER = ("Project", "Domain", "What it is", "Brain key")
DOMAIN_PLACEHOLDER = "<domain>"  # the cell `rail new` leaves to the operator's classification
PROJECTS_DIR = "projects"  # a ReD root keeps its sub-projects here
_DOMAIN = ROSTER_HEADER.index("Domain")
_SEPARATOR = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+$")
_CELL_BOUNDARY = re.compile(r"(?<!\\)\|")
_MAKE_TARGET = re.compile(r"^([A-Za-z0-9_./ \t-]+?)\s*:(?!=)", re.MULTILINE)  # `a b: deps` = two


def _project_name(repo: Path) -> str:
    cfg = try_load_rail_config(repo)
    return cfg.project if cfg else repo.absolute().name


def make_targets(repo: Path) -> set[str]:
    makefile = repo / "Makefile"
    if not makefile.is_file():
        return set()
    found = {t for m in _MAKE_TARGET.finditer(makefile.read_text()) for t in m.group(1).split()}
    return {t for t in found if not t.startswith(".")}


def _short(path: Path) -> str:
    """`<parent>/<name>`: enough to identify the roster, no absolute path in a receipt or audit."""
    return f"{path.parent.name}/{path.name}"


def table_row(cells: Iterable[str]) -> str:
    """One Markdown table line; a `|` inside a cell is escaped, so the row keeps its width."""
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"


def table_cells(line: str) -> tuple[str, ...] | None:
    """The stripped cells of a Markdown table line, or None when the line is not one. An
    escaped `\\|` stays inside its cell."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    inner = stripped[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return tuple(cell.strip() for cell in _CELL_BOUNDARY.split(inner))


def roster_header() -> str:
    """The roster's header line and its separator, as the ReD root writes them."""
    return table_row(ROSTER_HEADER) + "\n|" + "---|" * len(ROSTER_HEADER)


def roster_rows(text: str) -> list[tuple[str, ...]] | None:
    """The rows of the identity table in `text`: the header, its separator, then consecutive
    table lines. None when no header line is followed by a separator."""
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        separator = _SEPARATOR.match(lines[index + 1].strip())
        if table_cells(line) != ROSTER_HEADER or not separator:
            continue
        rows: list[tuple[str, ...]] = []
        for row in lines[index + 2 :]:
            cells = table_cells(row)
            if cells is None:
                break
            rows.append(cells)
        return rows
    return None


_MAKE_VALUE_FLAGS = {"-C", "-f", "-j", "-I", "-o", "-W"}


def make_target(line: str) -> str | None:
    """The target a documented `make …` line invokes: the first word that is neither a flag
    (`-j4`), a flag value (`-C dir`) nor a `VAR=value` assignment. None when not `make`."""
    words = line.split()
    if not words or words[0] != "make":
        return None
    skip_next = False
    for word in words[1:]:
        if skip_next:
            skip_next = False
            continue
        if word in _MAKE_VALUE_FLAGS:
            skip_next = True
            continue
        if word.startswith("-") or "=" in word:
            continue
        return word
    return None


@dataclass(frozen=True, slots=True)
class RosterSearch:
    """What the walk up from a repository met (decisions 3 and 4)."""

    roster: Path | None = None  # the first CLAUDE.md holding the identity header
    rows: tuple[tuple[str, ...], ...] = ()  # the rows of its identity table
    red_root: Path | None = None  # the ReD position: parent of the nearest `projects` ancestor
    red_claude_md: bool = False  # a CLAUDE.md sits at the ReD position
    unreadable: str | None = None  # why the ReD position's CLAUDE.md could not be read


def _text(path: Path) -> str | None:
    """`path` as UTF-8 text, or None when there is no such file. Raises OSError (a stat or a
    read refused) or UnicodeDecodeError on a file that is there and cannot be read."""
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def find_roster(repo: Path) -> RosterSearch:
    """Walk up from `repo` to the first `CLAUDE.md` holding the identity header.

    The walk is lexical: `normpath` folds `..` (`rail audit ..` hands us `<repo>/../<x>`) and
    no symlink is resolved. It covers `projects/x`, a worktree nested in it
    (`projects/x/.claude/worktrees/w`, `projects/x/.worktrees/w`) and an audit from a
    sibling, without git. Only the ReD position — the parent of the nearest `projects`
    ancestor — may fail the gate on a read error; any other unreadable `CLAUDE.md` is passed
    over, so an unrelated ancestor never fails a repository."""
    start = Path(os.path.normpath(repo.absolute()))
    red_root: Path | None = None
    red_claude_md = False
    below: Path | None = None  # the repository itself is not an ancestor
    for ancestor in start.parents:
        red_position = red_root is None and below is not None and below.name == PROJECTS_DIR
        below = ancestor
        if red_position:
            red_root = ancestor
        candidate = ancestor / "CLAUDE.md"
        try:
            text = _text(candidate)
        except (OSError, UnicodeDecodeError) as exc:
            if red_position:
                return RosterSearch(
                    red_root=ancestor,
                    unreadable=f"cannot read {_short(candidate)} ({type(exc).__name__})",
                )
            continue
        if red_position:
            red_claude_md = text is not None
        rows = roster_rows(text) if text is not None else None
        if rows is not None:
            return RosterSearch(roster=candidate, rows=tuple(rows), red_root=red_root)
    return RosterSearch(red_root=red_root, red_claude_md=red_claude_md)


def rail_config(repo: Path) -> GateResult:
    try:
        cfg = load_rail_config(repo)
    except FileNotFoundError:
        return GateResult(Stage.HYGIENE, "rail_config", False, MISSING_HINT)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        return GateResult(Stage.HYGIENE, "rail_config", False, f"{location}: {first['msg']}")
    unknown = sorted(set(cfg.gates) - known_gate_keys())
    if unknown:
        return GateResult(
            Stage.HYGIENE,
            "rail_config",
            False,
            f"gates: unknown key(s) {', '.join(unknown)} — a typo disables nothing, it is an error",
        )
    return GateResult(Stage.HYGIENE, "rail_config", True, f"tier={cfg.tier.value}")


def known_gate_keys() -> set[str]:
    """Every gate id in the registry plus every typed parameter in the policy defaults."""
    from rail.gates import registry
    from rail.policy import GATE_DEFAULTS

    return {spec.gate_id for spec in registry()} | set(GATE_DEFAULTS)


def docs_layout(repo: Path) -> GateResult:
    missing = [d for d in DOCS_DIRS if not (repo / d).is_dir()]
    if missing:
        return GateResult(Stage.HYGIENE, "docs_layout", False, "missing: " + ", ".join(missing))
    return GateResult(Stage.HYGIENE, "docs_layout", True, "docs/{specs,plans,adr} present")


def claude_md(repo: Path) -> GateResult:
    path = repo / "CLAUDE.md"
    if not path.is_file():
        return GateResult(Stage.HYGIENE, "claude_md", False, "CLAUDE.md is missing")
    text = path.read_text()
    problems: list[str] = []
    cfg = try_load_rail_config(repo)
    if cfg is None:
        problems.append(f"{manifest_problem(repo)}; brain key not verifiable")
    elif f"`{cfg.brain_key}`" not in text:
        problems.append(f"brain key `{cfg.brain_key}` not mentioned")
    targets = make_targets(repo)
    commands = 0
    for block in markdown.fenced_blocks(text, "bash"):
        for line in markdown.command_lines(block):
            commands += 1
            target = make_target(line)
            if target is not None and target not in targets:
                problems.append(f"`{line}` names a Makefile target that does not exist")
    if problems:
        return GateResult(Stage.HYGIENE, "claude_md", False, "; ".join(problems))
    return GateResult(
        Stage.HYGIENE, "claude_md", True, f"brain key present, {commands} command(s) resolvable"
    )


def task_runner(repo: Path) -> GateResult:
    if not (repo / "Makefile").is_file():
        return GateResult(Stage.HYGIENE, "task_runner", False, "Makefile is missing")
    targets = make_targets(repo)
    if "ci" not in targets:
        return GateResult(Stage.HYGIENE, "task_runner", False, "Makefile has no `ci` target")
    return GateResult(
        Stage.HYGIENE, "task_runner", True, f"Makefile with `ci` ({len(targets)} targets)"
    )


def settings(repo: Path) -> GateResult:
    path = repo / ".claude" / "settings.json"
    if not path.is_file():
        return GateResult(Stage.HYGIENE, "settings", False, ".claude/settings.json is missing")
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        return GateResult(Stage.HYGIENE, "settings", False, f"invalid JSON: {exc}")
    allow = data.get("permissions", {}).get("allow") if isinstance(data, dict) else None
    if not isinstance(allow, list) or not allow:
        return GateResult(Stage.HYGIENE, "settings", False, "permissions.allow is missing or empty")
    return GateResult(Stage.HYGIENE, "settings", True, f"{len(allow)} allowed pattern(s)")


def remotes(repo: Path) -> GateResult:
    canonical, _ = effective(repo, "hygiene.canonical_host")
    mirror, _ = effective(repo, "hygiene.mirror_host")
    if not gitrepo.is_git_repo(repo):
        return GateResult(Stage.HYGIENE, "remotes", False, "not a git repository")
    urls = gitrepo.remotes(repo)
    wanted = [canonical] + ([mirror] if mirror else [])  # a mirror only where declared
    hosts = {gitrepo.url_host(url) for url in urls.values()}
    missing = [host for host in wanted if str(host).lower() not in hosts]
    if missing:
        names = ", ".join(sorted(urls)) or "none"
        return GateResult(
            Stage.HYGIENE, "remotes", False, f"no remote on {', '.join(missing)} (remotes: {names})"
        )
    return GateResult(Stage.HYGIENE, "remotes", True, " + ".join(wanted))


def roster_entry(repo: Path) -> GateResult:
    """Three outcomes (decision 4). The roster is found: the row is judged. No roster, but a
    `CLAUDE.md` sits at the ReD position: its header drifted or it cannot be read, a FAIL.
    Otherwise the repository is standalone, and the message says why."""

    def result(passed: bool, details: str) -> GateResult:
        return GateResult(Stage.HYGIENE, "roster_entry", passed, details)

    search = find_roster(repo)
    if search.unreadable is not None:
        return result(False, f"{search.unreadable}: the roster cannot be checked")
    if search.roster is None:
        if search.red_root is None:
            return result(True, f"standalone: no `{PROJECTS_DIR}` ancestor")
        if not search.red_claude_md:
            return result(True, f"standalone: no CLAUDE.md in {search.red_root.name}")
        drifted = _short(search.red_root / "CLAUDE.md")
        return result(False, f"roster header not found in {drifted}: the root format drifted")
    name = _project_name(repo)
    where = _short(search.roster)
    row = next((cells for cells in search.rows if cells[0] == name), None)
    if row is None:
        return result(False, f"{name} has no row in {where}")
    if len(row) > _DOMAIN and row[_DOMAIN] == DOMAIN_PLACEHOLDER:
        return result(
            False,
            f"{name}'s row in {where} still holds `{DOMAIN_PLACEHOLDER}`: fill in its domain",
        )
    return result(True, f"listed in {where}")


def receipts(repo: Path) -> GateResult:
    directory = repo / RECEIPTS_DIR
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        return GateResult(Stage.HYGIENE, "receipts", True, "no receipts")
    problems: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for path in files:
        try:
            record = load_receipt(path)
        except LedgerError as exc:
            problems.append(str(exc))
            continue
        if not record.verify():
            problems.append(f"{path.name}: digest does not match its content")
        expected = receipt_filename(record)
        if path.name != expected:
            problems.append(f"{path.name}: expected name {expected}")
        key = (record.project, record.idempotency_key)
        if key in seen:
            problems.append(
                f"{path.name}: idempotency key {record.idempotency_key!r} "
                f"already used by {seen[key]}"
            )
        seen.setdefault(key, path.name)
    if problems:
        shown = "; ".join(problems[:5])
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        return GateResult(Stage.HYGIENE, "receipts", False, shown + more)
    return GateResult(Stage.HYGIENE, "receipts", True, f"{len(files)} well-formed receipt(s)")


def mirrors(repo: Path) -> GateResult:
    """`ledger: brain`: every attestation receipt in the checkout is a mirror of a row in
    the shared ledger — matched by record digest (same fields, same digest). A mirror
    without its attestation is drift (ADR-0002), the phase-2 proof line. Milestone receipts
    (`integrated`, `fulfilled`) are brain's own receipts, never attested by the rail: the ones
    kept from a file-ledger past are history, not drift."""
    decl = declarations(repo)
    if isinstance(decl, str):
        return GateResult(Stage.HYGIENE, "mirrors", False, decl)
    if decl.ledger is LedgerBackend.FILE:
        where = "file ledger (default)" if decl.cfg is None else "file ledger"
        return GateResult(Stage.HYGIENE, "mirrors", True, f"{where}: the receipts are the ledger")
    cfg = decl.cfg
    assert cfg is not None  # a brain ledger is only ever declared by a manifest
    local = FileLedger(repo / RECEIPTS_DIR)
    try:
        kept = local.list(cfg.project, kind=RecordKind.ATTESTATION)
        shared = {
            r.digest for r in open_ledger(repo).list(cfg.project, kind=RecordKind.ATTESTATION)
        }
    except LedgerError as exc:
        return GateResult(Stage.HYGIENE, "mirrors", False, str(exc))
    milestones = [r for r in kept if r.payload.get("kind") in BRAIN_MILESTONES]
    mirrors_ = [r for r in kept if r.payload.get("kind") not in BRAIN_MILESTONES]
    missing = [receipt_filename(r) for r in mirrors_ if r.digest not in shared]
    if missing:
        shown = ", ".join(missing[:3]) + (
            f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""
        )
        return GateResult(Stage.HYGIENE, "mirrors", False, f"mirror without attestation: {shown}")
    history = f", {len(milestones)} milestone receipt(s) kept as history" if milestones else ""
    return GateResult(
        Stage.HYGIENE, "mirrors", True, f"{len(mirrors_)} mirror(s) attested in brain{history}"
    )


GATES = [
    GateSpec(Stage.HYGIENE, "rail_config", rail_config),
    GateSpec(Stage.HYGIENE, "docs_layout", docs_layout),
    GateSpec(Stage.HYGIENE, "claude_md", claude_md),
    GateSpec(Stage.HYGIENE, "task_runner", task_runner),
    GateSpec(Stage.HYGIENE, "settings", settings),
    GateSpec(Stage.HYGIENE, "remotes", remotes, scope="workstation"),
    GateSpec(Stage.HYGIENE, "roster_entry", roster_entry, scope="workstation"),
    GateSpec(Stage.HYGIENE, "receipts", receipts),
    GateSpec(Stage.HYGIENE, "mirrors", mirrors, scope="ledger"),
]
