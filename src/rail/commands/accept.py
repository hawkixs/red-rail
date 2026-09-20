"""`rail accept`: stage 10 — the requester accepts the integrated delivery. On the shared
ledger this is `brain_delivery_accept` as `red` against the exact integration evidence;
on the file ledger it is a `fulfilled` attestation keyed by HEAD."""

from __future__ import annotations

from pathlib import Path

import click
from pydantic import ValidationError

from rail import gitrepo
from rail.commands._options import json_option, repo_option
from rail.commands.attest import echo_record
from rail.ledger import AttestationKind, LedgerError, open_ledger
from rail.model import load_rail_config


@click.command("accept")
@repo_option
@click.option("--rationale", required=True, help="Why the delivery is accepted (recorded).")
@click.option("--issuer", default="operator", show_default=True, help="X-Brain-Agent label.")
@json_option
def command(repo: Path, rationale: str, issuer: str, as_json: bool) -> None:
    """Accept the integrated delivery (stage 10): `fulfilled` in the ledger."""
    try:
        project = load_rail_config(repo).project
        ledger = open_ledger(repo)
        # stage 10 follows stage 6 on both ledgers: brain refuses without its integration
        # receipt; the file ledger holds the operator to the same rule (red-arena, 2026-09-20)
        integrated = [
            r
            for r in ledger.list(project, attestation=AttestationKind.INTEGRATED)
            if r.data.get("sha") and gitrepo.is_ancestor(repo, str(r.data["sha"]))
        ]
        if not integrated:
            raise LedgerError(
                "no integration evidence on HEAD's history to accept: the delivery is not "
                "integrated (brain observes the merge; on the file ledger, `rail attest "
                "integrated` first)"
            )
        record = ledger.accept(
            project, rationale=rationale, issuer=issuer, sha=gitrepo.head_sha(repo)
        )
    except (FileNotFoundError, ValidationError, LedgerError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    echo_record(record, repo, as_json)
