"""`rail new` and `rail upgrade`: the copier template (this repository, `copier.yml` at its
root) drives both. A new project is rendered, given its bootstrap contract and spec, committed,
checked at its tier, then published — the 15-step kickstart runbook `a050e6ec` as one command."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import copier
import yaml

from rail import remotes
from rail.gates import GateResult, run_gates
from rail.ledger import RECEIPTS_DIR, Contract, Deliverable, Record
from rail.ledger.file import FileLedger
from rail.model import Stack, Tier
from rail.policy import stages_for

TEMPLATE_SOURCE = "git@github.com:hawkixs/red-rail.git"
ANSWERS_FILE = ".copier-answers.yml"


class ScaffoldError(Exception):
    """The scaffold could not be completed; the tree is left in place for inspection."""


@dataclass(frozen=True, slots=True)
class NewProject:
    slug: str
    description: str
    tier: Tier
    stack: Stack
    brain_key: str
    dest: Path
    template: str = TEMPLATE_SOURCE
    template_ref: str | None = None
    deploy_target: str = "vps-traefik"
    healthcheck: str | None = None

    @property
    def answers(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "project": self.slug,
            "description": self.description,
            "brain_key": self.brain_key,
            "tier": self.tier.value,
            "stack": self.stack.value,
        }
        if self.tier is Tier.PROD:
            data["deploy_target"] = self.deploy_target
            data["healthcheck"] = self.healthcheck or f"https://{self.slug[4:]}.hawkixs.com/healthz"
        return data


BOOTSTRAP_SPEC = """# {slug} — Bootstrap design

- **Date**: {today}
- **Status**: bootstrap, written by `rail new` — replace it with the real design before
  leaving tier `bootstrap`

## 1. Problem

{description}

## 2. Decisions

| # | Decision |
|---|---|
| 1 | Tier `{tier}`, stack `{stack}`, ledger `file` (`rail.yaml`) |
| 2 | Canonical remote GitHub `hawkixs/{slug}`, mirror GitLab `hawkixs_project/red/{slug}` |

## 3. Non-goals

Nothing beyond the bootstrap: no feature is designed here.

## 4. Success criteria

`rail check` passes at tier `{tier}` on a fresh clone.
"""


def render(project: NewProject, *, copy: Callable[..., Any] = copier.run_copy) -> Path:
    if project.dest.exists():
        raise ScaffoldError(f"{project.dest} already exists")
    copy(
        project.template,
        project.dest,
        data=project.answers,
        defaults=True,
        quiet=True,
        unsafe=False,
        vcs_ref=project.template_ref,
    )
    return project.dest


def write_bootstrap_spec(project: NewProject, *, today: date | None = None) -> Path:
    today = today or datetime.now(UTC).date()
    path = (
        project.dest / "docs" / "specs" / f"{today.isoformat()}-{project.slug}-bootstrap-design.md"
    )
    path.write_text(
        BOOTSTRAP_SPEC.format(
            slug=project.slug,
            today=today.isoformat(),
            description=project.description,
            tier=project.tier.value,
            stack=project.stack.value,
        )
    )
    return path


def record_contract(project: NewProject, *, clock: Callable[[], datetime] | None = None) -> Record:
    ledger = FileLedger(project.dest / RECEIPTS_DIR, clock=clock)
    contract = Contract(
        objective=project.description,
        acceptance_criteria=[f"`rail check` passes at tier {project.tier.value}"],
        deliverables=[
            Deliverable(key="main", repository=f"{remotes.CANONICAL_OWNER}/{project.slug}")
        ],
    )
    return ledger.contract_set(
        project.slug,
        contract,
        reason="bootstrap",
        issuer="rail new",
        idempotency_key=f"contract:{project.slug}:1",
    )


def _git(dest: Path, *args: str) -> str:
    identity: list[str] = []
    probe = subprocess.run(
        ["git", "-C", str(dest), "config", "--get", "user.email"], capture_output=True, text=True
    )
    if probe.returncode != 0:
        identity = ["-c", "user.name=rail", "-c", "user.email=rail@localhost"]
    done = subprocess.run(
        ["git", *identity, "-C", str(dest), *args], capture_output=True, text=True
    )
    if done.returncode != 0:
        raise ScaffoldError(f"git {' '.join(args)} failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout.strip()


def init_git(project: NewProject) -> str:
    subprocess.run(["git", "init", "-q", "-b", "main", str(project.dest)], check=True)
    _git(project.dest, "add", "-A")
    _git(project.dest, "commit", "-q", "-m", f"chore: bootstrap {project.slug} with the ReD rail")
    return _git(project.dest, "rev-parse", "HEAD")


def verify(project: NewProject) -> list[GateResult]:
    """The gates of the declared tier, CI scope (no remotes yet, no roster from a fresh tree)."""
    return run_gates(project.dest, stages=stages_for(project.tier), ci=True)


def new_project(
    project: NewProject,
    *,
    publish: bool = True,
    copy: Callable[..., Any] = copier.run_copy,
    run: remotes.Runner = subprocess.run,
    clock: Callable[[], datetime] | None = None,
) -> list[GateResult]:
    render(project, copy=copy)
    write_bootstrap_spec(project)
    record_contract(project, clock=clock)
    init_git(project)
    results = verify(project)
    failing = [r for r in results if not r.passed]
    if failing:
        detail = "; ".join(f"{r.gate_id}: {r.details}" for r in failing)
        raise ScaffoldError(f"the fresh scaffold fails its own gates — {detail}")
    if publish:
        remotes.publish(project.dest, project.slug, project.description, run=run)
    return results


def upgrade(repo: Path, *, update: Callable[..., Any] = copier.run_update) -> str:
    """`copier update` towards the template's latest tag; returns the new `_commit`."""
    answers = repo / ANSWERS_FILE
    if not answers.is_file():
        raise ScaffoldError(f"{ANSWERS_FILE} missing: not scaffolded by copier")
    data = yaml.safe_load(answers.read_text()) or {}
    if not data.get("_commit") or not data.get("_src_path"):
        raise ScaffoldError(
            f"{ANSWERS_FILE} has no _src_path/_commit: the template was not versioned; "
            "re-scaffold from a tagged red-rail before upgrading"
        )
    update(repo, defaults=True, overwrite=True, skip_answered=True, quiet=True, unsafe=False)
    return str((yaml.safe_load(answers.read_text()) or {}).get("_commit", ""))
