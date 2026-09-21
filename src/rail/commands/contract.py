"""`rail contract set`: the intent stage — a delivery contract for this repository, shaped
like brain-v42's `brain_delivery_contract_set` so phase 2 maps 1:1."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import UUID

import click
from pydantic import ValidationError

from rail import gitrepo
from rail.commands._options import json_option, repo_option
from rail.commands.attest import echo_record
from rail.contract_guard import Unwritable, refuse_unwritable
from rail.ledger import (
    Contract,
    Deliverable,
    LedgerError,
    RequiredCheck,
    ReviewPolicy,
    open_ledger,
)
from rail.model import load_rail_config

_GITHUB_SLUG = re.compile(r"github\.com[:/](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?$")
_CHECK = re.compile(
    r"^(?P<kind>check_run|commit_status):(?P<name>[^@#]+)(?:@(?P<app>[^#]+)|#(?P<provider>\d+))$"
)


def canonical_slug(repo: Path) -> str | None:
    """`owner/name` of the GitHub remote, whatever its name."""
    for url in gitrepo.remotes(repo).values():
        match = _GITHUB_SLUG.search(url)
        if match:
            return match.group("slug")
    return None


def parse_required_check(spec: str) -> RequiredCheck:
    """`check_run:NAME@APP_SLUG` or `commit_status:NAME#PROVIDER_ID` (ADR-0001 am. 4)."""
    match = _CHECK.match(spec)
    if not match:
        raise click.BadParameter(f"{spec!r}: expected KIND:NAME@APP_SLUG or KIND:NAME#PROVIDER_ID")
    return RequiredCheck(
        kind=match["kind"],
        name=match["name"],
        app_slug=match["app"],
        provider_id=int(match["provider"]) if match["provider"] else None,
    )


def parse_deliverable(
    spec: str,
    *,
    required_checks: list[RequiredCheck] | None = None,
    review: ReviewPolicy | None = None,
    no_checks_reason: str | None = None,
) -> Deliverable:
    """`owner/name[:key]` → Deliverable on `main`, with the checks and the review policy every
    deliverable of this contract shares."""
    repository, _, key = spec.partition(":")
    return Deliverable(
        key=key or "main",
        repository=repository,
        required_checks=list(required_checks or []),
        no_checks_reason=no_checks_reason,
        review=review or ReviewPolicy(),
    )


def next_contract_key(ledger: Any, project: str, ticket: UUID | str | None) -> str:
    """`contract:<ticket or project>:<n>` — n follows the highest revision the ledger lists,
    so an amendment never reuses a key (brain answers a reused key with the stored revision)."""
    from rail.ledger import RecordKind

    subject = str(ticket) if ticket else project
    numbers = []
    for record in ledger.list(project, kind=RecordKind.CONTRACT):
        _, _, tail = record.idempotency_key.rpartition(":")
        if record.idempotency_key.startswith("contract:") and tail.isdigit():
            numbers.append(int(tail))
    return f"contract:{subject}:{max(numbers, default=0) + 1}"


def _preview(
    objective: str, criteria: tuple[str, ...], constraints: tuple[str, ...], reason: str
) -> str:
    """What is about to be frozen, shown before it is — one keystroke against a text nobody
    will be able to correct, only amend."""
    lines = [
        "",
        "This contract revision cannot be corrected once written, only amended — the text",
        "below stays in the repository's history and in brain.",
        "",
        f"  objective:  {objective}",
    ]
    lines += [f"  criterion:  {c}" for c in criteria]
    lines += [f"  constraint: {c}" for c in constraints]
    lines += [f"  reason:     {reason}", ""]
    return "\n".join(lines)


@click.group("contract")
def command() -> None:
    """Delivery contracts (stage 1, intent)."""


@command.command("set")
@repo_option
@click.option("--objective", required=True)
@click.option("--criterion", "criteria", multiple=True, help="Acceptance criterion (repeatable).")
@click.option("--constraint", "constraints", multiple=True, help="Constraint (repeatable).")
@click.option(
    "--deliverable",
    "deliverables",
    multiple=True,
    help="owner/name[:key] (repeatable; default: the GitHub remote, key `main`).",
)
@click.option("--reason", required=True, help="Why this contract (or this amendment) exists.")
@click.option("--issuer", default="operator", show_default=True)
@click.option("--key", "idempotency_key", help="Idempotency key (default: contract:<project>:<n>).")
@click.option(
    "--required-check",
    "required_checks",
    multiple=True,
    help="KIND:NAME@APP_SLUG or KIND:NAME#PROVIDER_ID (repeatable), e.g. "
    "check_run:red-rail/review@red-rail-reviewer.",
)
@click.option(
    "--no-checks-reason",
    help="Why this contract requires no check — mandatory when --required-check is absent.",
)
@click.option(
    "--allowed-reviewer",
    "allowed_reviewers",
    multiple=True,
    help="GitHub login whose approval counts (repeatable), e.g. red-rail-reviewer[bot].",
)
@click.option("--required-approvals", type=click.IntRange(0, 100), default=1, show_default=True)
@click.option("--priority", type=click.IntRange(0, 10000), default=0, show_default=True)
@click.option(
    "--acceptance-mode",
    type=click.Choice(["automatic", "explicit"]),
    default="explicit",
    show_default=True,
    help=(
        "How `fulfilled` is reached: an explicit brain_delivery_accept (default) or automatically."
    ),
)
@click.option(
    "--yes", is_flag=True, help="Skip the confirmation (a script; a person should read it)."
)
@json_option
def set_(
    repo: Path,
    objective: str,
    criteria: tuple[str, ...],
    constraints: tuple[str, ...],
    deliverables: tuple[str, ...],
    reason: str,
    issuer: str,
    idempotency_key: str | None,
    required_checks: tuple[str, ...],
    no_checks_reason: str | None,
    allowed_reviewers: tuple[str, ...],
    required_approvals: int,
    priority: int,
    acceptance_mode: str,
    yes: bool,
    as_json: bool,
) -> None:
    """Create or amend the project's delivery contract in the ledger."""
    checks = [parse_required_check(spec) for spec in required_checks]
    # blank is absent: `--no-checks-reason "   "` satisfied the guard by truthiness alone and
    # stored a reason that explains nothing, in an append-only ledger. Normalising here also
    # keeps `--no-checks-reason ""` from reaching pydantic and exiting 1 with a raw
    # ValidationError, where every sibling misuse in this command exits 2 as a UsageError.
    no_checks_reason = (no_checks_reason or "").strip() or None
    if not checks and not no_checks_reason:
        # brain refuses this contract with `invalid_arguments` without naming the field;
        # stop before the ledger and name the flag instead (decision bc679157: a command
        # tells a repository what awaits it).
        raise click.UsageError(
            "a deliverable with no required check must say why: pass --no-checks-reason "
            '"<reason>", or declare a check with --required-check KIND:NAME@APP_SLUG '
            "(e.g. check_run:red-rail/review@red-rail-reviewer)."
        )
    if checks and no_checks_reason:
        raise click.UsageError(
            "--no-checks-reason contradicts --required-check: pass one or the other."
        )
    # Refused before the write, not in a later audit: a contract revision cannot be
    # corrected, only amended, and the faulty one stays in history — in this repository and
    # in brain.
    try:
        refuse_unwritable([objective], criteria=criteria, constraints=constraints)
    except Unwritable as exc:
        raise click.UsageError(str(exc)) from exc
    review = ReviewPolicy(
        required_approvals=required_approvals, allowed_reviewers=list(allowed_reviewers)
    )
    if not deliverables:
        slug = canonical_slug(repo)
        if slug is None:
            raise click.UsageError("no GitHub remote found; pass --deliverable owner/name[:key]")
        deliverables = (slug,)
    if not yes and not as_json:
        click.echo(_preview(objective, criteria, constraints, reason))
        click.confirm("Write this contract?", abort=True)
    try:
        cfg = load_rail_config(repo)
        ledger = open_ledger(repo)
        contract = Contract(
            objective=objective,
            acceptance_criteria=list(criteria),
            constraints=list(constraints),
            deliverables=[
                parse_deliverable(
                    d, required_checks=checks, review=review, no_checks_reason=no_checks_reason
                )
                for d in deliverables
            ],
            priority=priority,
            acceptance_mode=acceptance_mode,
        )
        key = idempotency_key or next_contract_key(ledger, cfg.project, cfg.ticket)
        record = ledger.contract_set(
            cfg.project, contract, reason=reason, issuer=issuer, idempotency_key=key
        )
    except (FileNotFoundError, ValidationError, LedgerError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    echo_record(record, repo, as_json)
