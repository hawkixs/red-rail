"""`rail ledger list`: read the project's evidence back, chronologically."""

from __future__ import annotations

import json
from pathlib import Path

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.ledger import AttestationKind, LedgerError, RecordKind, open_ledger
from rail.model import load_rail_config


@click.group("ledger")
def command() -> None:
    """Read the ledger."""


@command.command("list")
@repo_option
@click.option("--kind", type=click.Choice([k.value for k in RecordKind]))
@click.option("--attestation", type=click.Choice([k.value for k in AttestationKind]))
@json_option
def list_(repo: Path, kind: str | None, attestation: str | None, as_json: bool) -> None:
    """List the records of this project, oldest first."""
    try:
        cfg = load_rail_config(repo)
        records = open_ledger(repo).list(
            cfg.project,
            kind=RecordKind(kind) if kind else None,
            attestation=AttestationKind(attestation) if attestation else None,
        )
    except (FileNotFoundError, ValidationError, LedgerError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    if as_json:
        click.echo(json.dumps([r.model_dump(mode="json") for r in records], indent=2))
        return
    for r in records:
        label = r.attestation.value if r.attestation else r.kind.value
        summary = json.dumps(r.data, sort_keys=True)[:60]
        click.echo(f"{r.recorded_at.isoformat()}  {label:<18} {r.idempotency_key:<28} {summary}")
