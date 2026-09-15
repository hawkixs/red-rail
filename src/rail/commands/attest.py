"""`rail attest KIND`: append one attestation to the ledger. Idempotent by key; a receipt can
be replayed with `--from` after a failed attestation (spec §7)."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.ledger import AttestationKind, LedgerError, Record, RecordKind, open_ledger
from rail.ledger.file import load_receipt, receipt_filename

_INT = re.compile(r"-?\d{1,12}")
_FLOAT = re.compile(r"-?\d+\.\d+")


def coerce(value: str) -> Any:
    """`key=value` data: true/false, short integers and decimals become typed; anything else
    (a version, a sha, a digest) stays text."""
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if _INT.fullmatch(value):
        return int(value)
    if _FLOAT.fullmatch(value):
        return float(value)
    return value


def parse_data(pairs: tuple[str, ...], data_json: str | None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if data_json:
        loaded = json.loads(data_json)
        if not isinstance(loaded, dict):
            raise click.UsageError("--data-json must be a JSON object")
        data.update(loaded)
    for pair in pairs:
        if "=" not in pair:
            raise click.UsageError(f"--data expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        data[key] = coerce(value)
    return data


def echo_record(record: Record, repo: Path, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(record.model_dump(mode="json"), indent=2))
        return
    label = record.attestation.value if record.attestation else record.kind.value
    click.echo(
        f"{label}  {record.idempotency_key}  {record.digest}  "
        f"docs/receipts/{receipt_filename(record)}"
    )


@click.command("attest")
@click.argument("kind", type=click.Choice([k.value for k in AttestationKind]))
@repo_option
@click.option("--data", "pairs", multiple=True, help="key=value (repeatable).")
@click.option("--data-json", help="JSON object merged before --data pairs.")
@click.option("--issuer", default="operator", show_default=True)
@click.option(
    "--key", "idempotency_key", help="Idempotency key (default: KIND:<sha> or KIND:<uuid>)."
)
@click.option(
    "--from",
    "replay",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Replay a receipt file: same key, same payload, same issuer.",
)
@json_option
def command(
    kind: str,
    repo: Path,
    pairs: tuple[str, ...],
    data_json: str | None,
    issuer: str,
    idempotency_key: str | None,
    replay: Path | None,
    as_json: bool,
) -> None:
    """Record an attestation (released, deployed, rolled_back, …) in the project's ledger."""
    attestation = AttestationKind(kind)
    try:
        ledger = open_ledger(repo)
        from rail.model import load_rail_config

        project = load_rail_config(repo).project
        if replay is not None:
            source = load_receipt(replay)
            if source.kind is not RecordKind.ATTESTATION or source.attestation is not attestation:
                raise click.UsageError(f"{replay.name} is not a {kind} attestation")
            record = ledger.attest(
                project,
                attestation,
                source.data,
                issuer=source.issuer,
                idempotency_key=source.idempotency_key,
            )
        else:
            data = parse_data(pairs, data_json)
            key = idempotency_key or (
                f"{kind}:{data['sha']}" if data.get("sha") else f"{kind}:{uuid.uuid4()}"
            )
            record = ledger.attest(project, attestation, data, issuer=issuer, idempotency_key=key)
    except (FileNotFoundError, ValidationError, LedgerError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    echo_record(record, repo, as_json)
