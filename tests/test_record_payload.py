"""A valid digest cannot make a malformed attestation safe to consume."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from rail.ledger import AttestationKind, LedgerError, Record, RecordKind, compute_digest
from rail.ledger.file import FileLedger, load_receipt

MALFORMED_PAYLOADS = [
    pytest.param({}, id="missing-kind"),
    pytest.param({"kind": "unknown"}, id="unknown-kind"),
    pytest.param({"kind": "released", "data": []}, id="list-data"),
    pytest.param({"kind": "released", "data": "invalid"}, id="string-data"),
    pytest.param({"kind": "released", "data": 42}, id="number-data"),
    pytest.param({"kind": "released", "data": None}, id="null-data"),
    pytest.param({"kind": "released", "data": [["sha", "a" * 40]]}, id="pairs-data"),
]


def receipt_fields(payload: dict[str, Any], project: str = "red-probe") -> dict[str, Any]:
    """Recompute the digest so these failures concern payload shape, not tampering."""
    fields = Record.build(
        kind=RecordKind.ATTESTATION,
        project=project,
        issuer="operator",
        idempotency_key="release:1",
        payload={"kind": "released", "data": {}},
        recorded_at=datetime(2026, 10, 13, tzinfo=UTC),
    ).digest_fields()
    fields["payload"] = payload
    return {**fields, "digest": compute_digest(fields)}


@pytest.mark.parametrize("payload", MALFORMED_PAYLOADS)
def test_load_receipt_rejects_malformed_attestation(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    fields = receipt_fields(payload)
    assert fields["digest"] == compute_digest({k: v for k, v in fields.items() if k != "digest"})
    path = tmp_path / "malformed.json"
    path.write_text(json.dumps(fields))
    with pytest.raises(LedgerError, match="unreadable receipt malformed.json"):
        load_receipt(path)


@pytest.mark.parametrize("payload", MALFORMED_PAYLOADS)
def test_file_ledger_fails_closed_on_malformed_attestation(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    (tmp_path / "malformed.json").write_text(json.dumps(receipt_fields(payload)))
    with pytest.raises(LedgerError, match="unreadable receipt malformed.json"):
        FileLedger(tmp_path).list("red-probe")


@pytest.mark.parametrize("payload", [{"kind": "released"}, {"kind": "released", "data": {}}])
def test_attestation_data_remains_optional(payload: dict[str, Any]) -> None:
    record = Record.model_validate(receipt_fields(payload))
    assert record.verify()
    assert record.attestation is AttestationKind.RELEASED
    assert record.data == {}
    assert record.payload == payload


@pytest.mark.parametrize("kind", [RecordKind.CONTRACT, RecordKind.BINDING])
def test_other_record_payloads_are_not_attestations(kind: RecordKind) -> None:
    record = Record.build(
        kind=kind,
        project="red-probe",
        issuer="operator",
        idempotency_key="record:1",
        payload={"kind": "unrelated", "data": "unrelated"},
        recorded_at=datetime(2026, 10, 13, tzinfo=UTC),
    )
    assert record.verify()
    assert record.attestation is None
    assert record.data == record.payload
