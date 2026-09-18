"""Hygiene gates: the floor every tier stands on, starting with `bootstrap`.

`remotes` and `roster_entry` are workstation-scoped: they read the operator's clone (two
remotes, the ReD root roster two directories up) and are reported as skipped under `--ci`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from pydantic import ValidationError

from rail import gitrepo, markdown
from rail.gates import GateResult, GateSpec, Stage
from rail.ledger import RECEIPTS_DIR, LedgerError
from rail.ledger.file import load_receipt, receipt_filename
from rail.model import MANIFEST_NAME, load_rail_config, try_load_rail_config
from rail.policy import effective

DOCS_DIRS = ("docs/specs", "docs/plans", "docs/adr")
ROSTER_MARKER = "| Projet |"
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


def find_roster(repo: Path) -> Path | None:
    """The ReD root `CLAUDE.md` (two levels up: `<root>/projects/<repo>`), if it holds
    the roster."""
    # normpath folds `..` lexically (`rail audit ..` hands us `<repo>/../<project>`) without
    # resolving symlinks, so a symlinked project still points at the ReD root
    candidate = Path(os.path.normpath(repo.absolute())).parent.parent / "CLAUDE.md"
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
        problems.append(f"{MANIFEST_NAME} unreadable, brain key not verifiable")
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
        return GateResult(Stage.HYGIENE, "roster_entry", True, f"listed in {_short(roster)}")
    return GateResult(
        Stage.HYGIENE, "roster_entry", False, f"{name} has no row in {_short(roster)}"
    )


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
