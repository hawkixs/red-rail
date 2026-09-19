"""The contract every ledger backend must satisfy. Phase 2 runs it against `BrainLedger`."""

from pathlib import Path

import pytest

from rail.ledger import (
    AttestationKind,
    Contract,
    Deliverable,
    IdempotencyConflict,
    Ledger,
    PullRequestRef,
    RecordKind,
)

CONTRACT = Contract(
    objective="ship the probe",
    acceptance_criteria=["/healthz answers 200"],
    deliverables=[Deliverable(key="probe", repository="hawkixs/red-probe")],
)
PR = PullRequestRef(repository="hawkixs/red-probe", number=7, head_sha="a" * 40)


class LedgerContract:
    """Subclass, implement `make_ledger`, inherit every test."""

    def make_ledger(self, tmp_path: Path) -> Ledger:
        raise NotImplementedError

    def test_attest_returns_a_verified_record(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        record = ledger.attest(
            "red-probe",
            AttestationKind.DEPLOYED,
            {"sha": "b" * 40},
            issuer="op",
            idempotency_key="d1",
        )
        assert record.kind is RecordKind.ATTESTATION
        assert record.attestation is AttestationKind.DEPLOYED
        assert record.data == {"sha": "b" * 40}
        assert record.digest.startswith("sha256:") and record.verify()

    def test_replay_with_same_key_and_content_is_idempotent(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        first = ledger.attest(
            "red-probe",
            AttestationKind.DEPLOYED,
            {"sha": "b" * 40},
            issuer="op",
            idempotency_key="d1",
        )
        again = ledger.attest(
            "red-probe",
            AttestationKind.DEPLOYED,
            {"sha": "b" * 40},
            issuer="op",
            idempotency_key="d1",
        )
        assert again.digest == first.digest
        assert len(ledger.list("red-probe")) == 1

    def test_same_key_with_different_content_is_a_conflict(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        ledger.attest(
            "red-probe",
            AttestationKind.DEPLOYED,
            {"sha": "b" * 40},
            issuer="op",
            idempotency_key="d1",
        )
        with pytest.raises(IdempotencyConflict):
            ledger.attest(
                "red-probe",
                AttestationKind.DEPLOYED,
                {"sha": "c" * 40},
                issuer="op",
                idempotency_key="d1",
            )

    def test_contract_set_and_bind_are_records_of_their_kind(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        contract = ledger.contract_set(
            "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c1"
        )
        binding = ledger.bind("red-probe", PR, issuer="op", idempotency_key="b1")
        assert contract.kind is RecordKind.CONTRACT
        assert contract.payload["contract"]["objective"] == "ship the probe"
        assert contract.payload["reason"] == "bootstrap"
        assert binding.kind is RecordKind.BINDING
        assert binding.payload["number"] == 7

    def test_list_is_chronological_and_filters(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        ledger.contract_set(
            "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c1"
        )
        ledger.attest(
            "red-probe",
            AttestationKind.RELEASED,
            {"version": "1.0.0"},
            issuer="op",
            idempotency_key="r1",
        )
        ledger.attest(
            "red-probe",
            AttestationKind.DEPLOYED,
            {"digest": "sha256:x"},
            issuer="op",
            idempotency_key="d1",
        )
        kinds = [r.kind for r in ledger.list("red-probe")]
        assert kinds == [RecordKind.CONTRACT, RecordKind.ATTESTATION, RecordKind.ATTESTATION]
        assert [r.attestation for r in ledger.list("red-probe", kind=RecordKind.ATTESTATION)] == [
            AttestationKind.RELEASED,
            AttestationKind.DEPLOYED,
        ]
        assert len(ledger.list("red-probe", attestation=AttestationKind.DEPLOYED)) == 1

    def test_get_by_digest(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        record = ledger.attest(
            "red-probe", AttestationKind.FULFILLED, {}, issuer="op", idempotency_key="f1"
        )
        assert ledger.get("red-probe", record.digest) == record
        assert ledger.get("red-probe", "sha256:" + "0" * 64) is None

    def test_projects_are_isolated(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        ledger.attest("red-probe", AttestationKind.DEPLOYED, {}, issuer="op", idempotency_key="d1")
        assert ledger.list("red-other") == []

    def test_emitted_at_fixes_the_record_time(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        ledger = self.make_ledger(tmp_path)
        when = datetime(2026, 9, 18, 10, 0, 0, 123456, tzinfo=UTC)
        record = ledger.attest(
            "red-probe",
            AttestationKind.DEPLOYED,
            {"sha": "b" * 40},
            issuer="op",
            idempotency_key="d1",
            emitted_at=when,
        )
        assert record.recorded_at == when
        again = ledger.attest(
            "red-probe",
            AttestationKind.DEPLOYED,
            {"sha": "b" * 40},
            issuer="op",
            idempotency_key="d1",
            emitted_at=when,
        )
        assert again == record

    def test_a_float_in_the_data_is_refused_before_any_write(self, tmp_path: Path) -> None:
        from rail.ledger import LedgerError

        ledger = self.make_ledger(tmp_path)
        with pytest.raises(LedgerError, match="float"):
            ledger.attest(
                "red-probe",
                AttestationKind.DEPLOYED,
                {"ratio": 1.5},
                issuer="op",
                idempotency_key="d1",
            )
        assert ledger.list("red-probe") == []
