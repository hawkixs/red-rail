"""`rail contract set`: the intent stage — a delivery contract for this repository, shaped
like brain-v42's `brain_delivery_contract_set` so phase 2 maps 1:1."""

from __future__ import annotations

import re
from pathlib import Path

import click
from pydantic import ValidationError

from rail import gitrepo
from rail.commands._options import json_option, repo_option
from rail.commands.attest import echo_record
from rail.ledger import Contract, Deliverable, LedgerError, open_ledger
from rail.model import load_rail_config

_GITHUB_SLUG = re.compile(r"github\.com[:/](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?$")


def canonical_slug(repo: Path) -> str | None:
    """`owner/name` of the GitHub remote, whatever its name."""
    for url in gitrepo.remotes(repo).values():
        match = _GITHUB_SLUG.search(url)
        if match:
            return match.group("slug")
    return None


def parse_deliverable(spec: str) -> Deliverable:
    """`owner/name[:key]` → Deliverable on `main`."""
    repository, _, key = spec.partition(":")
    return Deliverable(key=key or "main", repository=repository)


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
    as_json: bool,
) -> None:
    """Create or amend the project's delivery contract in the ledger."""
    if not deliverables:
        slug = canonical_slug(repo)
        if slug is None:
            raise click.UsageError("no GitHub remote found; pass --deliverable owner/name[:key]")
        deliverables = (slug,)
    try:
        cfg = load_rail_config(repo)
        ledger = open_ledger(repo)
        contract = Contract(
            objective=objective,
            acceptance_criteria=list(criteria),
            constraints=list(constraints),
            deliverables=[parse_deliverable(d) for d in deliverables],
        )
        from rail.ledger import RecordKind

        existing = len(ledger.list(cfg.project, kind=RecordKind.CONTRACT))
        key = idempotency_key or f"contract:{cfg.project}:{existing + 1}"
        record = ledger.contract_set(
            cfg.project, contract, reason=reason, issuer=issuer, idempotency_key=key
        )
    except (FileNotFoundError, ValidationError, LedgerError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    echo_record(record, repo, as_json)
