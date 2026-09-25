"""`rail attest KIND`: append one attestation to the ledger. Idempotent by key; a receipt can
be replayed with `--from` after a failed attestation (spec §7)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.ledger import (
    AttestationKind,
    LedgerError,
    Record,
    RecordKind,
    Unattested,
    idempotency_key_for,
    open_ledger,
)
from rail.ledger.file import load_receipt, receipt_filename
from rail.model import load_rail_config

_INT = re.compile(r"-?(0|[1-9]\d{0,11})")


def coerce(value: str) -> Any:
    """`key=value` data: `true`/`false` and plain integers (no leading zero) become typed;
    anything else — a version like `1.20`, a sha prefix like `0123…`, a decimal — stays text,
    because re-typing an identifier corrupts it. Use `--data-json` for other types."""
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if _INT.fullmatch(value):
        return int(value)
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
    "--key",
    "idempotency_key",
    help="Idempotency key (default: <kind>:<subject>[:<occurrence>], "
    "see rail.ledger.idempotency_key_for).",
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
    if kind == AttestationKind.REVIEW_RULING.value and replay is None:
        # a ruling is the operator's decision on one open blocker (spec 2026-09-25, D9): only
        # `rail reviewer rule` checks that the finding awaits one; a replay of its mirror is fine
        raise click.UsageError(
            "a review_ruling is written by `rail reviewer rule`, which checks the finding "
            "awaits a ruling; `rail attest review_ruling --from <receipt>` only replays one"
        )
    try:
        ledger = open_ledger(repo)
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
                emitted_at=source.recorded_at,
            )
        else:
            data = parse_data(pairs, data_json)
            emitted_at = datetime.now(UTC)
            key = idempotency_key or idempotency_key_for(attestation, data, emitted_at=emitted_at)
            record = ledger.attest(
                project,
                attestation,
                data,
                issuer=issuer,
                idempotency_key=key,
                emitted_at=emitted_at,
            )
    except Unattested as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(2) from exc
    except (FileNotFoundError, ValidationError, LedgerError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    echo_record(record, repo, as_json)
