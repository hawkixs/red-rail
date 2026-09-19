"""Deterministic fixture repositories for the gate, audit and metrics tests.

Fixed author and dates make commit SHAs stable across runs and machines, which keeps the
golden audit matrix diffable.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

GITHUB_URL = "git@github.com:hawkixs/{name}.git"
MIRROR_URL = "ssh://git@gitlab.hawkixs.local:2222/hawkixs_project/red/{name}.git"
FIXED_DATE = "2026-09-15T08:00:00+00:00"
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "rail",
    "GIT_AUTHOR_EMAIL": "rail@example.invalid",
    "GIT_COMMITTER_NAME": "rail",
    "GIT_COMMITTER_EMAIL": "rail@example.invalid",
    "GIT_AUTHOR_DATE": FIXED_DATE,
    "GIT_COMMITTER_DATE": FIXED_DATE,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}

MINIMAL_MANIFEST = (
    "rail: 1\nproject: {project}\nbrain_key: {project}\ntier: {tier}\nstack: {stack}\n"
    "ledger: file\n"
)

SPEC = """# {project} — Design

## 1. Problem
Why this project exists.

## 2. Decisions
| # | Decision |
|---|---|
| 1 | Keep it small |

## 3. Non-goals
Nothing beyond the fixture.

## 4. Success criteria
`rail check` passes at tier {tier}.
"""

PLAN = """# {project} — Implementation plan

Spec: docs/specs/2026-09-15-{project}-design.md

### Task 1.1: Smoke test
- [ ] Run `make test`, expect PASS
"""

FENCE = "`" * 3  # built at run time so this file never contains a Markdown fence
CLAUDE_MD = (
    "# {project}\n\n- **Brain MCP project key**: `{project}`\n\n## Commands\n\n"
    f"{FENCE}bash\nmake ci        # lint, test, check\nmake test\n{FENCE}\n"
)

MAKEFILE = (
    ".PHONY: lint test check ci\nlint:\n\t@true\ntest:\n\t@true\n"
    "check:\n\trail check\nci: lint test check\n"
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=GIT_ENV
    ).stdout.strip()


def init_repo(path: Path, *, remotes: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=GIT_ENV)
    git(path, "config", "commit.gpgsign", "false")
    if remotes:
        git(path, "remote", "add", "origin", GITHUB_URL.format(name=path.name))
        git(path, "remote", "add", "gitlab", MIRROR_URL.format(name=path.name))
    return path


def commit_all(repo: Path, subject: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


def write_manifest(
    repo: Path,
    *,
    project: str,
    tier: str,
    stack: str = "python",
    gates: dict[str, tuple[object, str]] | None = None,
    deploy: bool = False,
) -> None:
    body = MINIMAL_MANIFEST.format(project=project, tier=tier, stack=stack)
    if deploy:
        body += "deploy:\n  target: vps-traefik\n"
        body += f"  healthcheck: https://{project}.example.invalid/healthz\n"
    if gates:
        body += "gates:\n"
        for key, (value, reason) in gates.items():
            body += f"  {key}:\n    value: {json.dumps(value)}\n    reason: {reason}\n"
    (repo / "rail.yaml").write_text(body)


def write_roster(root: Path, names: list[str]) -> None:
    rows = "\n".join(f"| {n} | Infra | fixture | OK | `{n}` |" for n in names)
    header = "| Projet | Domaine | Statut reel | Sante | Cle brain |\n|---|---|---|---|---|\n"
    (root / "CLAUDE.md").write_text("# ReD\n\n" + header + rows + "\n")


def conforming_tree(root: Path, name: str, tier: str, *, stack: str = "python") -> Path:
    """`<root>/projects/<name>`: everything the structural gates want at `tier` (no receipts —
    evidence is written by the tests that need it, through `FileLedger`)."""
    repo = init_repo(root / "projects" / name)
    write_manifest(repo, project=name, tier=tier, stack=stack, deploy=(tier == "prod"))
    for sub in ("specs", "plans", "adr", "receipts"):
        (repo / "docs" / sub).mkdir(parents=True)
        (repo / "docs" / sub / ".gitkeep").write_text("")
    (repo / "docs" / "specs" / f"2026-09-15-{name}-design.md").write_text(
        SPEC.format(project=name, tier=tier)
    )
    (repo / "docs" / "plans" / f"2026-09-15-{name}-plan.md").write_text(PLAN.format(project=name))
    (repo / "CLAUDE.md").write_text(CLAUDE_MD.format(project=name))
    (repo / "README.md").write_text(f"# {name}\n")
    (repo / "Makefile").write_text(MAKEFILE)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(make:*)"]}}, indent=2) + "\n"
    )
    if stack == "python":
        (repo / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.1.0"\n\n[tool.ruff]\nline-length = 100\n'
        )
        (repo / "tests").mkdir()
        (repo / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n")
    elif stack == "go":
        (repo / "go.mod").write_text(f"module example.invalid/{name}\n\ngo 1.22\n")
        (repo / "main_test.go").write_text("package main\n")
    commit_all(repo, "chore: bootstrap the fixture")
    return repo


def with_evidence(repo: Path, *, through: str, clock: Callable[[], datetime] | None = None) -> None:
    """Write the file-ledger evidence a conforming project has at a given stage: `design`
    (contract), `integrate` (+ independent approving verdict and integration receipt on HEAD)
    or `learn` (+ release, deployment, rollback drill and fulfilment)."""
    from rail.ledger import RECEIPTS_DIR, AttestationKind, Contract, Deliverable
    from rail.ledger.file import FileLedger

    name = repo.name
    head = git(repo, "rev-parse", "HEAD")
    ticks = [datetime(2026, 9, 15, 9, 0, tzinfo=UTC) + timedelta(minutes=i) for i in range(50)]
    ledger = FileLedger(repo / RECEIPTS_DIR, clock=clock or (lambda: ticks.pop(0)))
    contract = Contract(
        objective=f"ship {name}",
        acceptance_criteria=["rail check passes"],
        deliverables=[Deliverable(key="main", repository=f"hawkixs/{name}")],
    )
    ledger.contract_set(name, contract, reason="bootstrap", issuer="op", idempotency_key="c1")
    if through == "design":
        return
    ledger.attest(
        name,
        AttestationKind.REVIEW_VERDICT,
        {"sha": head, "independent": True, "verdict": "approve"},
        issuer="red-rail-reviewer",
        idempotency_key="v1",
    )
    ledger.attest(
        name, AttestationKind.INTEGRATED, {"sha": head}, issuer="op", idempotency_key="i1"
    )
    if through == "integrate":
        return
    ledger.attest(
        name,
        AttestationKind.RELEASED,
        {"sha": head, "version": "1.0.0", "digest": "sha256:aaa"},
        issuer="op",
        idempotency_key="r1",
    )
    ledger.attest(
        name,
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:aaa"},
        issuer="op",
        idempotency_key="d1",
    )
    ledger.attest(
        name, AttestationKind.ROLLED_BACK, {"drill": True}, issuer="op", idempotency_key="rb1"
    )
    ledger.attest(
        name, AttestationKind.RESTORED, {"drill": True}, issuer="op", idempotency_key="rs1"
    )
    ledger.attest(name, AttestationKind.FULFILLED, {}, issuer="op", idempotency_key="f1")
