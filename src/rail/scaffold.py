"""`rail new` and `rail upgrade`: the copier template (this repository, `copier.yml` at its
root) drives both. A new project is rendered, given its bootstrap contract and spec, committed,
checked at its tier, then published — the 15-step kickstart runbook `a050e6ec` as one command."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import copier
import yaml

from rail import remotes
from rail.gates import GateResult, run_gates
from rail.ledger import Contract, Deliverable, Record, RequiredCheck, ReviewPolicy, open_ledger
from rail.ledger.file import FileLedger
from rail.model import DeployTarget, LedgerBackend, Stack, Tier
from rail.policy import parameter, stages_for

TEMPLATE_SOURCE = "git@github.com:hawkixs/red-rail.git"
ANSWERS_FILE = ".copier-answers.yml"
# Rust at tier prod needs a release image and a service the template does not carry yet
# (spec 2026-09-24-rust-stack, decision 1). copier.yml's validator on `stack` says the same.
RUST_PROD_REFUSAL = (
    "rust at tier prod is not templated yet (no image, no service): scaffold at dev and "
    "promote when the rust prod template lands"
)
# the independent reviewer's check, and the CI job the template wires (job `rail` calling the
# reusable workflow's `make ci + rail check`) — each named with the App that publishes it
REVIEW_CHECK = RequiredCheck(name="red-rail/review", app_slug="red-rail-reviewer")
CI_CHECK = RequiredCheck(name="rail / make ci + rail check", app_slug="github-actions")


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
    rail_ref: str | None = None  # commit SHA the reusable CI workflow is called at
    healthcheck: str | None = None
    ledger: LedgerBackend = LedgerBackend.FILE
    ticket: str | None = None

    def __post_init__(self) -> None:
        # the description is spliced into a module docstring and a TOML string by the template:
        # a double quote, a backslash or a line break would break the rendered files
        if not self.description.strip() or any(c in self.description for c in '"\\\n\r'):
            raise ScaffoldError(
                "description: one line, without double quotes or backslashes (it is rendered "
                "into a docstring and pyproject.toml)"
            )

    @property
    def answers(self) -> dict[str, Any]:
        if self.stack is Stack.RUST and self.tier is Tier.PROD:
            raise ScaffoldError(RUST_PROD_REFUSAL)
        data: dict[str, Any] = {
            "project": self.slug,
            "description": self.description,
            "brain_key": self.brain_key,
            "tier": self.tier.value,
            "stack": self.stack.value,
        }
        if self.tier is Tier.PROD:
            data["deploy_target"] = self.deploy_target
            # A private target has no public route and no guessable default: the address is
            # something the operator states, never something the scaffold assumes — and no
            # machine address belongs in this repository.
            if self.deploy_target == DeployTarget.PRIVATE_COMPOSE and not self.healthcheck:
                raise ScaffoldError(
                    "target private-compose has no default healthcheck: pass --healthcheck "
                    "with the address the service answers on"
                )
            data["healthcheck"] = self.healthcheck or f"https://{self.slug[4:]}.hawkixs.com/healthz"
        if self.rail_ref:
            data["rail_ref"] = self.rail_ref
        data["ledger"] = self.ledger.value
        if self.ledger is LedgerBackend.BRAIN:
            if not self.ticket:
                raise ScaffoldError("ledger brain needs a ticket")
            data["ticket"] = self.ticket
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
| 1 | Tier `{tier}`, stack `{stack}`, ledger `{ledger}` (`rail.yaml`) |
| 2 | One remote, GitHub `hawkixs/{slug}` — ReD is GitHub only; a mirror is a declaration |

## 3. Non-goals

Nothing beyond the bootstrap: no feature is designed here.

## 4. Success criteria

`rail check` passes at tier `{tier}` on a fresh clone.
"""


SHA = re.compile(r"^[0-9a-f]{40}$")


def resolve_rail_ref(
    template: str, *, run: Callable[..., Any] = subprocess.run, branch: str = "main"
) -> str:
    """The commit SHA the reusable workflow should be called at.

    Never falls back to a branch: a pin that quietly becomes a moving ref is worse than no
    pin, because it reads as pinned. Anything that is not a 40-character SHA is refused —
    copier's own `_commit` is `git describe` output (`v0.4.0-44-g4c257be`), which git
    resolves locally but GitHub does not, being neither a tag nor a branch on the remote."""
    done = run(
        [GIT, "ls-remote", template, f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    first = (done.stdout or "").split("\t", 1)[0].strip() if done.returncode == 0 else ""
    if not SHA.match(first):
        detail = (done.stderr or done.stdout or "").strip()[:200] or "no matching ref"
        raise ScaffoldError(
            f"could not resolve {template}@{branch} to a commit SHA: {detail}. The CI "
            "workflow pin must be a SHA GitHub can use, never a branch or a describe"
        )
    return first


def render(project: NewProject, *, copy: Callable[..., Any] = copier.run_copy) -> Path:
    if project.dest.exists():
        raise ScaffoldError(f"{project.dest} already exists")
    # evaluated outside the try: `answers` raises its own bare ScaffoldError (rust at prod, a
    # private target with no healthcheck), and that refusal must reach the caller verbatim,
    # never re-wrapped in "copier could not render" — that wrapper is for copier's own failures
    answers = project.answers
    try:
        copy(
            project.template,
            project.dest,
            data=answers,
            defaults=True,
            quiet=True,
            unsafe=False,
            vcs_ref=project.template_ref,
        )
    except Exception as exc:  # copier raises its own hierarchy; the CLI needs one error type
        raise ScaffoldError(f"copier could not render {project.template}: {exc}") from exc
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
            ledger=project.ledger.value,
        )
    )
    return path


def bootstrap_contract(project: NewProject) -> Contract:
    """What the requester asks on day 0. From tier `dev` the review stage applies: the
    independent reviewer's check and approval are required (spec §4, ADR-0003)."""
    reviewed = project.tier is not Tier.BOOTSTRAP
    deliverable = Deliverable(
        key="main",
        repository=f"{remotes.CANONICAL_OWNER}/{project.slug}",
        required_checks=[REVIEW_CHECK] if reviewed else [],
        no_checks_reason=(
            None
            if reviewed
            else "tier bootstrap: no pull request is reviewed before the design stage"
        ),
        review=ReviewPolicy(
            required_approvals=1 if reviewed else 0,
            allowed_reviewers=["red-rail-reviewer[bot]"] if reviewed else [],
        ),
    )
    criteria = [f"`rail check` passes at tier {project.tier.value}"]
    if project.tier is Tier.PROD:
        # the route depends on the shape: a private target is reached over WireGuard, and
        # promising Traefik there would be a criterion nobody can meet
        route = (
            "over its private address"
            if project.deploy_target == DeployTarget.PRIVATE_COMPOSE
            else "behind Traefik"
        )
        criteria.append(
            f"the service answers /healthz, /version and /metrics {route} and "
            "/version equals the released digest"
        )
    return Contract(
        objective=project.description, acceptance_criteria=criteria, deliverables=[deliverable]
    )


def record_contract(
    project: NewProject, *, clock: Callable[[], datetime] | None = None, client: Any = None
) -> Record:
    ledger = open_ledger(project.dest, client=client)  # the backend the rendered manifest declares
    if clock is not None and isinstance(ledger, FileLedger):
        ledger = FileLedger(ledger.root, clock=clock)
    contract = bootstrap_contract(project)
    return ledger.contract_set(
        project.slug,
        contract,
        reason="bootstrap",
        issuer="rail new",
        idempotency_key=f"contract:{project.slug}:1",
    )


GIT = "git"


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run([GIT, *args], capture_output=True, text=True, check=False)
    except (FileNotFoundError, OSError) as exc:
        raise ScaffoldError(f"{GIT} is not available on this host: {exc}") from exc


def _identity_flags(dest: Path) -> list[str]:
    """`-c user.name/user.email` when either is unset, so a bare host can still commit."""
    for key in ("user.name", "user.email"):
        if _run_git(["-C", str(dest), "config", "--get", key]).returncode != 0:
            return ["-c", "user.name=rail", "-c", "user.email=rail@localhost"]
    return []


def _git(dest: Path, *args: str) -> str:
    done = _run_git([*_identity_flags(dest), "-C", str(dest), *args])
    if done.returncode != 0:
        raise ScaffoldError(f"git {' '.join(args)} failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout.strip()


def init_git(project: NewProject) -> str:
    done = _run_git(["init", "-q", "-b", "main", str(project.dest)])
    if done.returncode != 0:
        raise ScaffoldError(f"git init failed: {(done.stderr or done.stdout).strip()}")
    _git(project.dest, "add", "-A")
    _git(project.dest, "commit", "-q", "-m", f"chore: bootstrap {project.slug} with the ReD rail")
    return _git(project.dest, "rev-parse", "HEAD")


def verify(project: NewProject) -> list[GateResult]:
    """The floor a fresh tree can pass (hygiene, intent, design); the declared tier is what
    `rail check` demands next."""
    return run_gates(project.dest, stages=stages_for(Tier.BOOTSTRAP), ci=True)


def new_project(
    project: NewProject,
    *,
    publish: bool = True,
    copy: Callable[..., Any] = copier.run_copy,
    run: remotes.Runner = subprocess.run,
    clock: Callable[[], datetime] | None = None,
    client: Any = None,
    resolve: Callable[..., str] = resolve_rail_ref,
) -> list[GateResult]:
    # resolved before rendering and never after: a tree that exists with `@main` in it, even
    # briefly, is a tree someone can commit
    if not project.rail_ref:
        project = replace(project, rail_ref=resolve(project.template))
    render(project, copy=copy)
    write_bootstrap_spec(project)
    if project.ledger is LedgerBackend.FILE:
        record_contract(project, clock=clock, client=client)  # part of the bootstrap commit
    init_git(project)
    results = verify(project)
    failing = [r for r in results if not r.passed]
    if failing:
        detail = "; ".join(f"{r.gate_id}: {r.details}" for r in failing)
        raise ScaffoldError(f"the fresh scaffold fails its own gates — {detail}")
    mirror = parameter(project.dest, "hygiene.mirror_host")  # GitHub only unless declared
    if publish:
        remotes.publish(project.dest, project.slug, project.description, mirror=mirror, run=run)
    if project.ledger is LedgerBackend.BRAIN:
        # brain enriches the deliverable from its repository registry: the repository exists
        # first; the mirror is a second commit so the bootstrap commit stays what was published
        record_contract(project, clock=clock, client=client)
        _git(project.dest, "add", "-A", "docs/receipts")
        _git(project.dest, "commit", "-q", "-m", "chore(rail): mirror the delivery contract")
        if publish:
            remotes.push(project.dest, mirror=mirror, run=run)
    if publish:
        # last, once every direct push of `rail new` is done: from here main takes pull requests
        remotes.protect_main(project.slug, protected_checks(project), run=run)
    return results


def protected_checks(project: NewProject) -> list[tuple[str, str]]:
    """What `main` requires (decision a3846910): the CI job from day 0, and the independent
    reviewer's check wherever the contract requires it — the same tier line as
    `bootstrap_contract`."""
    checks = [CI_CHECK] if project.tier is Tier.BOOTSTRAP else [CI_CHECK, REVIEW_CHECK]
    return [(check.name, check.app_slug or "") for check in checks]


def _manifest(repo: Path) -> dict[str, Any] | str:
    """rail.yaml as a mapping, or why it cannot be read: the switch never guesses a stack."""
    path = repo / "rail.yaml"
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        return "rail.yaml is missing"
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return f"rail.yaml does not parse: {exc}"
    return data if isinstance(data, dict) else "rail.yaml is not a mapping"


def _switchable(answers: dict[str, Any], repo: Path, stack: Stack) -> None:
    """Refuse, before copier, any switch but one out of docs (spec 2026-09-24-rust-stack,
    decision 13): python→go or go→rust leaves build files only a diff review would catch."""
    manifest = _manifest(repo)
    if isinstance(manifest, str):
        raise ScaffoldError(f"{manifest}: the stack switch reads it before copier runs")
    current, declared = answers.get("stack"), manifest.get("stack")
    if current != declared:
        raise ScaffoldError(
            f"{ANSWERS_FILE} says stack {current} but rail.yaml says {declared}: the two "
            "disagree, reconcile them before switching"
        )
    if stack is Stack.DOCS:
        raise ScaffoldError("a project cannot switch to docs: that transition is not templated")
    if current != Stack.DOCS.value:
        raise ScaffoldError(
            f"--stack switches only out of docs (this project is {current}): python↔go and "
            "go→rust leave build files only a diff review would catch"
        )
    if stack is Stack.RUST and manifest.get("tier") == Tier.PROD.value:
        raise ScaffoldError(RUST_PROD_REFUSAL)


def upgrade(
    repo: Path,
    *,
    stack: Stack | None = None,
    update: Callable[..., Any] = copier.run_update,
    resolve: Callable[..., str] = resolve_rail_ref,
) -> str:
    """`copier update` towards the template's latest tag; returns the new `_commit`.

    Re-resolves the CI workflow pin at the same time, so a gate change reaches a repository
    as a reviewable line in this command's diff rather than as an effect of `@main` moving
    under it. With `stack`, the project leaves `docs` for that stack: copier takes the new
    answer, and its three-way merge is the only writer of rail.yaml."""
    answers = repo / ANSWERS_FILE
    if not answers.is_file():
        raise ScaffoldError(f"{ANSWERS_FILE} missing: not scaffolded by copier")
    data = yaml.safe_load(answers.read_text()) or {}
    if not data.get("_commit") or not data.get("_src_path"):
        raise ScaffoldError(
            f"{ANSWERS_FILE} has no _src_path/_commit: the template was not versioned; "
            "re-scaffold from a tagged red-rail before upgrading"
        )
    if data.get("stack") == Stack.RUST.value and data.get("tier") == Tier.PROD.value:
        # copier would drop this answer as invalid and, under defaults, re-render as python
        raise ScaffoldError(f"{ANSWERS_FILE} holds stack rust at tier prod: {RUST_PROD_REFUSAL}")
    if stack is not None:
        _switchable(data, repo, stack)
    answers_to_give: dict[str, Any] = {"rail_ref": resolve(str(data["_src_path"]))}
    if stack is not None:
        answers_to_give["stack"] = stack.value
    try:
        update(
            repo,
            data=answers_to_give,
            defaults=True,
            overwrite=True,
            skip_answered=True,
            quiet=True,
            unsafe=False,
        )
    except Exception as exc:  # copier raises its own hierarchy; the CLI needs one error type
        raise ScaffoldError(f"copier could not update {repo}: {exc}") from exc
    if stack is not None:
        manifest = _manifest(repo)
        if isinstance(manifest, str) or manifest.get("stack") != stack.value:
            raise ScaffoldError(
                f"copier did not carry `stack` into rail.yaml: resolve it to `stack: {stack.value}`"
            )
    return str((yaml.safe_load(answers.read_text()) or {}).get("_commit", ""))
