"""`rail ledger list`: read the project's evidence back, chronologically."""

from __future__ import annotations

import json
from pathlib import Path

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.ledger import (
    AttestationKind,
    IdempotencyConflict,
    LedgerError,
    RecordKind,
    Unattested,
    open_ledger,
)
from rail.ledger.brain import BrainLedger
from rail.ledger.file import receipt_filename
from rail.model import LedgerBackend, load_rail_config


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


@command.command("replay")
@repo_option
@json_option
def replay(repo: Path, as_json: bool) -> None:
    """Replay every attestation waiting in this project's spool, oldest first. Exit 0: the
    spool is empty; 2: one still waits (brain refused or is down); 1: a conflict or an error."""
    try:
        cfg = load_rail_config(repo)
        if cfg.ledger is LedgerBackend.FILE:
            click.echo("file ledger: the receipts are the ledger, there is no spool")
            return
        ledger = open_ledger(repo)
        if not isinstance(ledger, BrainLedger):
            raise LedgerError("ledger: brain did not open a brain ledger")
        waiting = ledger.pending()
    except (FileNotFoundError, ValidationError, LedgerError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    lines: list[dict[str, str]] = []
    code = 0
    for record in waiting:
        try:
            outcome = ledger.replay(record)
        except Unattested as exc:
            outcome, code = f"unattested: {exc.cause}", code or 2
        except IdempotencyConflict as exc:
            outcome, code = f"conflict: {exc}", 1
        except LedgerError as exc:
            outcome, code = f"error: {exc}", 1
        lines.append({"receipt": receipt_filename(record), "outcome": outcome})
    if as_json:
        click.echo(json.dumps(lines, indent=2))
    else:
        for line in lines:
            click.echo(f"{line['receipt']}  {line['outcome']}")
        if not lines:
            click.echo("the spool is empty")
    if code:
        raise SystemExit(code)
