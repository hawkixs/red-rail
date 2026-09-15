"""Hygiene gates: the floor every tier stands on, starting with `bootstrap`.

`remotes` and `roster_entry` are workstation-scoped: they read the operator's clone (two
remotes, the ReD root roster two directories up) and are reported as skipped under `--ci`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from rail import gitrepo, markdown
from rail.gates import GateResult, GateSpec, Stage
from rail.ledger import RECEIPTS_DIR, LedgerError
from rail.ledger.file import load_receipt, receipt_filename
from rail.model import MANIFEST_NAME, RailConfig, load_rail_config
from rail.policy import effective

DOCS_DIRS = ("docs/specs", "docs/plans", "docs/adr")
ROSTER_MARKER = "| Projet |"
_MAKE_TARGET = re.compile(r"^([A-Za-z0-9_./-]+)\s*:(?!=)", re.MULTILINE)


def _config(repo: Path) -> RailConfig | None:
    try:
        return load_rail_config(repo)
    except (FileNotFoundError, ValidationError):
        return None


def _project_name(repo: Path) -> str:
    cfg = _config(repo)
    return cfg.project if cfg else repo.absolute().name


def make_targets(repo: Path) -> set[str]:
    makefile = repo / "Makefile"
    if not makefile.is_file():
        return set()
    found = {m.group(1) for m in _MAKE_TARGET.finditer(makefile.read_text())}
    return {t for t in found if not t.startswith(".")}


def find_roster(repo: Path) -> Path | None:
    """The ReD root `CLAUDE.md` (two levels up: `<root>/projects/<repo>`), if it holds
    the roster."""
    candidate = repo.absolute().parent.parent / "CLAUDE.md"
    if candidate.is_file() and ROSTER_MARKER in candidate.read_text():
        return candidate
    return None


def rail_config(repo: Path) -> GateResult:
    try:
        cfg = load_rail_config(repo)
    except FileNotFoundError:
        return GateResult(Stage.HYGIENE, "rail_config", False, f"{MANIFEST_NAME} is missing")
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        return GateResult(Stage.HYGIENE, "rail_config", False, f"{location}: {first['msg']}")
    return GateResult(Stage.HYGIENE, "rail_config", True, f"tier={cfg.tier.value}")


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
    cfg = _config(repo)
    if cfg is None:
        problems.append(f"{MANIFEST_NAME} unreadable, brain key not verifiable")
    elif f"`{cfg.brain_key}`" not in text:
        problems.append(f"brain key `{cfg.brain_key}` not mentioned")
    targets = make_targets(repo)
    commands = 0
    for block in markdown.fenced_blocks(text, "bash"):
        for line in markdown.command_lines(block):
            commands += 1
            words = line.split()
            if words[0] == "make" and len(words) > 1 and words[1] not in targets:
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
    missing = [
        host for host in (canonical, mirror) if not any(host in url for url in urls.values())
    ]
    if missing:
        names = ", ".join(sorted(urls)) or "none"
        return GateResult(
            Stage.HYGIENE, "remotes", False, f"no remote on {', '.join(missing)} (remotes: {names})"
        )
    return GateResult(Stage.HYGIENE, "remotes", True, f"{canonical} + {mirror}")


def roster_entry(repo: Path) -> GateResult:
    roster = find_roster(repo)
    if roster is None:
        return GateResult(
            Stage.HYGIENE, "roster_entry", True, "no roster in scope (standalone repository)"
        )
    name = _project_name(repo)
    if re.search(rf"^\|\s*{re.escape(name)}\s*\|", roster.read_text(), re.MULTILINE):
        return GateResult(Stage.HYGIENE, "roster_entry", True, f"listed in {roster}")
    return GateResult(Stage.HYGIENE, "roster_entry", False, f"{name} has no row in {roster}")


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


GATES = [
    GateSpec(Stage.HYGIENE, "rail_config", rail_config),
    GateSpec(Stage.HYGIENE, "docs_layout", docs_layout),
    GateSpec(Stage.HYGIENE, "claude_md", claude_md),
    GateSpec(Stage.HYGIENE, "task_runner", task_runner),
    GateSpec(Stage.HYGIENE, "settings", settings),
    GateSpec(Stage.HYGIENE, "remotes", remotes, scope="workstation"),
    GateSpec(Stage.HYGIENE, "roster_entry", roster_entry, scope="workstation"),
    GateSpec(Stage.HYGIENE, "receipts", receipts),
]
