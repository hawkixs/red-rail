"""`FileLedger`: `docs/receipts/*.json` are the ledger — append-only, digested, idempotent."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from rail.ledger import (
    RECEIPTS_DIR,
    AttestationKind,
    Ledger,
    LedgerError,
    LedgerUnavailable,
    RequiredCheck,
    open_ledger,
)
from rail.ledger.file import FileLedger, load_receipt, receipt_filename
from tests.ledger_contract import LedgerContract

T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
MANIFEST = "rail: 1\nproject: red-probe\nbrain_key: red-probe\ntier: dev\nstack: python\n"


def _clock(start: datetime = T0):
    ticks = [start + timedelta(seconds=i) for i in range(100)]
    return lambda: ticks.pop(0)


class TestFileLedgerContract(LedgerContract):
    def make_ledger(self, tmp_path: Path) -> Ledger:
        return FileLedger(tmp_path / RECEIPTS_DIR, clock=_clock())


def test_receipt_filename_is_timestamp_kind_digest(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path, clock=_clock())
    record = ledger.attest(
        "red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1"
    )
    name = receipt_filename(record)
    assert name == f"20260915T080000Z-deployed-{record.digest[7:19]}.json"
    assert (tmp_path / name).is_file()


def test_receipts_are_append_only(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path, clock=_clock())
    first = ledger.attest(
        "red-probe",
        AttestationKind.RELEASED,
        {"version": "1.0.0"},
        issuer="op",
        idempotency_key="r1",
    )
    before = (tmp_path / receipt_filename(first)).read_bytes()
    ledger.attest(
        "red-probe",
        AttestationKind.DEPLOYED,
        {"digest": "sha256:x"},
        issuer="op",
        idempotency_key="d1",
    )
    assert (tmp_path / receipt_filename(first)).read_bytes() == before
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_receipt_round_trips_through_json(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path, clock=_clock())
    record = ledger.attest(
        "red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1"
    )
    loaded = load_receipt(tmp_path / receipt_filename(record))
    assert loaded == record and loaded.verify()
    raw = json.loads((tmp_path / receipt_filename(record)).read_text())
    assert set(raw) == {
        "schema_version",
        "kind",
        "project",
        "recorded_at",
        "issuer",
        "idempotency_key",
        "payload",
        "digest",
    }


def test_a_tampered_receipt_fails_verification(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path, clock=_clock())
    record = ledger.attest(
        "red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1"
    )
    path = tmp_path / receipt_filename(record)
    raw = json.loads(path.read_text())
    raw["payload"]["data"]["sha"] = "c" * 40
    path.write_text(json.dumps(raw))
    assert load_receipt(path).verify() is False


def test_a_tampered_receipt_stops_every_read(tmp_path: Path) -> None:
    """Fail closed: a digest mismatch is a broken ledger, not a record to reason about."""
    ledger = FileLedger(tmp_path, clock=_clock())
    record = ledger.attest(
        "red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1"
    )
    path = tmp_path / receipt_filename(record)
    raw = json.loads(path.read_text())
    raw["payload"]["data"]["sha"] = "c" * 40
    path.write_text(json.dumps(raw))
    with pytest.raises(LedgerError, match="tampered"):
        ledger.list("red-probe")
    with pytest.raises(LedgerError, match="tampered"):
        ledger.attest("red-probe", AttestationKind.FULFILLED, {}, issuer="op", idempotency_key="f1")


def test_a_malformed_receipt_is_a_broken_ledger(tmp_path: Path) -> None:
    (tmp_path / "junk.json").write_text("{not json")
    with pytest.raises(LedgerError, match="junk.json"):
        FileLedger(tmp_path).list("red-probe")


def test_open_ledger_reads_the_manifest(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text(MANIFEST)
    ledger = open_ledger(tmp_path)
    assert isinstance(ledger, FileLedger)
    assert ledger.root == tmp_path / RECEIPTS_DIR


def test_required_check_names_its_publisher() -> None:
    check = RequiredCheck(name="red-rail/review", app_slug="red-rail-reviewer")
    assert check.kind == "check_run" and check.provider_id is None
    assert RequiredCheck(kind="commit_status", name="ci", provider_id=42).app_slug is None
    with pytest.raises(ValidationError, match="publisher"):
        RequiredCheck(name="anonymous")


def test_open_ledger_refuses_brain_until_phase_2(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text(MANIFEST + "ledger: brain\n")
    with pytest.raises(LedgerUnavailable, match="phase 2"):
        open_ledger(tmp_path)
