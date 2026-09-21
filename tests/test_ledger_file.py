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


def test_open_ledger_brain_needs_a_ticket_in_the_manifest(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text(MANIFEST + "ledger: brain\n")
    with pytest.raises(ValidationError, match="ticket"):
        open_ledger(tmp_path)


def test_brain_digest_reproduces_every_published_vector() -> None:
    from rail.contracts import ATTESTATION_CONTRACT
    from rail.ledger import brain_digest

    for vector in ATTESTATION_CONTRACT["digest"]["vectors"]:
        assert brain_digest(vector["payload"]) == vector["digest"], vector
    negative = ATTESTATION_CONTRACT["digest"]["negative_example"]
    with pytest.raises(ValueError, match="float"):
        brain_digest(negative["payload"])


def test_brain_digest_refuses_nested_floats_and_non_string_keys() -> None:
    from rail.ledger import brain_digest

    with pytest.raises(ValueError, match="float"):
        brain_digest({"nested": [{"x": 2.0}]})
    with pytest.raises(ValueError, match="string keys"):
        brain_digest({1: "one"})  # type: ignore[dict-item]


def test_idempotency_keys_are_deterministic_per_event() -> None:
    from rail.ledger import idempotency_key_for

    when = datetime(2026, 9, 18, 10, 0, 5, tzinfo=UTC)
    sha = "b" * 40
    assert (
        idempotency_key_for(AttestationKind.GATE_PASSED, {"sha": sha, "gate": "design.spec"})
        == f"gate_passed:{sha}:design.spec"
    )
    assert (
        idempotency_key_for(
            AttestationKind.REVIEW_VERDICT, {"sha": sha, "check_run_id": 42}, emitted_at=when
        )
        == f"review_verdict:{sha}:42"
    )
    assert idempotency_key_for(AttestationKind.INTEGRATED, {"sha": sha}) == f"integrated:{sha}"
    assert (
        idempotency_key_for(AttestationKind.RELEASED, {"sha": sha, "version": "0.3.0"})
        == "released:0.3.0"
    )
    assert (
        idempotency_key_for(
            AttestationKind.DEPLOYED,
            {"target": "vps-traefik", "digest": "sha256:abc", "sha": sha},
            emitted_at=when,
        )
        == "deployed:vps-traefik:sha256:abc:20260918T100005Z"
    )
    assert (
        idempotency_key_for(AttestationKind.INCIDENT_DETECTED, {}, emitted_at=when)
        == "incident_detected:-:-:20260918T100005Z"
    )
    random_key = idempotency_key_for(AttestationKind.GATE_PASSED, {})
    assert random_key.startswith("gate_passed:") and len(random_key) > len("gate_passed:") + 30


def test_unattested_carries_the_receipt_and_the_cause(tmp_path: Path) -> None:
    from rail.ledger import Unattested

    error = Unattested(tmp_path / "r.json", "delivery_disabled")
    assert isinstance(error, LedgerError)
    assert error.receipt == tmp_path / "r.json" and error.cause == "delivery_disabled"
    assert "rail attest" in str(error) and "--from" in str(error)


def test_contract_and_deliverable_carry_the_brain_fields() -> None:
    from rail.ledger import Contract, Deliverable

    contract = Contract(
        objective="x",
        deliverables=[
            Deliverable(key="k", repository="a/b", no_checks_reason="fixture: no check declared")
        ],
    )
    dumped = contract.model_dump(mode="json")
    assert dumped["priority"] == 0 and dumped["acceptance_mode"] == "explicit"
    assert dumped["deliverables"][0]["repository_id"] is None
    assert dumped["deliverables"][0]["no_checks_reason"] == "fixture: no check declared"
    with pytest.raises(ValidationError):
        Contract.model_validate({**dumped, "acceptance_mode": "later"})


def test_open_ledger_brain_without_the_extra_is_unavailable_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found by the independent reviewer (PR #3, fifth pass): the client imports fastmcp
    lazily, so the ImportError guard around the rail modules never fired."""
    import importlib.util

    (tmp_path / "rail.yaml").write_text(
        MANIFEST + "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n"
    )
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "fastmcp" else real_find_spec(name, *a, **k),
    )
    with pytest.raises(LedgerUnavailable, match="uv sync --extra brain"):
        open_ledger(tmp_path)


def test_deliverable_without_a_required_check_must_say_why() -> None:
    """brain-v42 refuses a deliverable that has neither a required check nor a reason
    (`models/delivery.py`: "no_checks_reason is required when required_checks is empty").
    The mirror enforces it too, so a `file` ledger never stores a contract `brain` would
    refuse — the wall must not wait for the switch to `ledger: brain`."""
    from rail.ledger import Deliverable, RequiredCheck

    with pytest.raises(ValidationError, match="no_checks_reason"):
        Deliverable(key="main", repository="hawkixs/red-alpha")

    declared = Deliverable(
        key="main", repository="hawkixs/red-alpha", no_checks_reason="tier bootstrap: no PR yet"
    )
    assert declared.no_checks_reason == "tier bootstrap: no PR yet"

    checked = Deliverable(
        key="main",
        repository="hawkixs/red-alpha",
        required_checks=[RequiredCheck(name="red-rail/review", app_slug="red-rail-reviewer")],
    )
    assert checked.no_checks_reason is None


def test_deliverable_refuses_the_same_required_check_twice() -> None:
    """The second half of brain-v42's `_explicit_check_policy`, which the mirror first
    omitted: two identical selectors are a contract brain refuses."""
    from rail.ledger import Deliverable, RequiredCheck

    check = RequiredCheck(name="ci", app_slug="red-rail-reviewer")
    with pytest.raises(ValidationError, match="duplicate required check selector"):
        Deliverable(key="main", repository="hawkixs/red-alpha", required_checks=[check, check])

    # a different publisher for the same name is a different selector, and stays legal
    other = RequiredCheck(name="ci", app_slug="someone-else")
    assert (
        len(
            Deliverable(
                key="main", repository="hawkixs/red-alpha", required_checks=[check, other]
            ).required_checks
        )
        == 2
    )
