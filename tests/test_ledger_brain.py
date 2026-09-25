"""`BrainLedger`: the same contract as the file ledger, on brain-v42 (fake, in memory), plus
what only the shared ledger has — a pending attestation spooled first, replay after a refusal,
milestones read from the ticket, digests cross-checked, one subject per ticket."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rail.brain.client import BrainClient
from rail.contracts import ATTESTATION_CONTRACT, TOOL_ERROR_CODES
from rail.ledger import (
    AttestationKind,
    Contract,
    Deliverable,
    Ledger,
    LedgerError,
    Record,
    RecordKind,
    Unattested,
    brain_digest,
    open_ledger,
)
from rail.ledger.brain import KNOWN_REFUSALS, BrainLedger, record_from_row
from rail.ledger.file import load_receipt
from rail.ledger.spool import spool_directory
from tests.fake_brain import FakeBrain
from tests.helpers import conforming_tree, write_manifest
from tests.ledger_contract import LedgerContract

T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
CONTRACT = Contract(
    objective="ship the probe",
    acceptance_criteria=["/healthz answers 200"],
    deliverables=[
        Deliverable(
            key="probe",
            repository="hawkixs/red-probe",
            repository_id=4242,
            no_checks_reason="fixture: no check declared",
        )
    ],
)


def _clock(start: datetime = T0):
    ticks = [start + timedelta(seconds=i) for i in range(200)]
    return lambda: ticks.pop(0)


def _ledger(tmp_path: Path, *, agent: str = "op") -> tuple[BrainLedger, FakeBrain, str]:
    brain = FakeBrain(agent=agent)
    ticket = brain.add_ticket("red", "red-probe")
    brain.register_repository("red-probe", 4242, "hawkixs/red-probe")
    client = BrainClient.in_memory(brain, agent=agent)
    ledger = BrainLedger(
        client,
        ticket=ticket,
        project="red-probe",
        spool_dir=tmp_path / "spool" / "red-probe",
        clock=_clock(),
        repository_id=lambda slug: 4242,  # never `gh` in a test
    )
    return ledger, brain, ticket


class TestBrainLedgerContract(LedgerContract):
    """Every test of the shared suite, on brain. `make_ledger` also opens the ticket's
    delivery workflow, because brain refuses an attestation before any contract."""

    def make_ledger(self, tmp_path: Path) -> Ledger:
        ledger, self.brain, self.ticket = _ledger(tmp_path)
        ledger.contract_set(
            "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0"
        )
        # `test_contract_set_and_bind_are_records_of_their_kind` sets a second revision: fine.
        return _OtherProjectsAreEmpty(ledger)

    def integrate(self, ledger: Ledger, tmp_path: Path) -> None:
        self.brain.integrate(self.ticket, "a" * 40, issued_at=T0)

    # The four overrides below adapt assertions that assume a ledger starts empty, or that
    # a fresh milestone kind can be attested directly — both false for a brain-backed ledger
    # bound to a ticket (the bootstrap contract in `make_ledger` is itself a record, and
    # `integrated`/`fulfilled` are brain milestones, never attested — see
    # `test_milestones_are_read_from_the_ticket_never_attested`). Every other test of the
    # shared suite runs unmodified.

    def test_replay_with_same_key_and_content_is_idempotent(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        before = len(ledger.list("red-probe"))
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
        assert len(ledger.list("red-probe")) == before + 1

    def test_a_float_in_the_data_is_refused_before_any_write(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        before = ledger.list("red-probe")
        with pytest.raises(LedgerError, match="float"):
            ledger.attest(
                "red-probe",
                AttestationKind.DEPLOYED,
                {"ratio": 1.5},
                issuer="op",
                idempotency_key="d1",
            )
        assert ledger.list("red-probe") == before

    def test_get_by_digest(self, tmp_path: Path) -> None:
        # `fulfilled` (used by the base test) is a brain milestone: substitute `released`.
        ledger = self.make_ledger(tmp_path)
        record = ledger.attest(
            "red-probe",
            AttestationKind.RELEASED,
            {"version": "1.0.0"},
            issuer="op",
            idempotency_key="f1",
        )
        assert ledger.get("red-probe", record.digest) == record
        assert ledger.get("red-probe", "sha256:" + "0" * 64) is None

    def test_list_is_chronological_and_filters(self, tmp_path: Path) -> None:
        # The contract's `created_at` is brain's wall clock (Batch-2 `FakeBrain`), not the
        # test's fixed clock, so a mixed-kind chronological comparison against it would be
        # flaky; the attestation-only ordering and the kind/attestation filters are checked.
        ledger = self.make_ledger(tmp_path)
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
        everything = ledger.list("red-probe")
        assert RecordKind.CONTRACT in [r.kind for r in everything]
        assert [r.attestation for r in ledger.list("red-probe", kind=RecordKind.ATTESTATION)] == [
            AttestationKind.RELEASED,
            AttestationKind.DEPLOYED,
        ]
        assert len(ledger.list("red-probe", attestation=AttestationKind.DEPLOYED)) == 1


class _OtherProjectsAreEmpty:
    """The protocol is per project; a brain ledger is bound to one. `list()` of another
    project is empty rather than a refusal, like the file ledger."""

    def __init__(self, ledger: BrainLedger) -> None:
        self._ledger = ledger

    def __getattr__(self, name: str):
        return getattr(self._ledger, name)

    def list(self, project: str, **kwargs):
        return self._ledger.list(project, **kwargs) if project == self._ledger.project else []


def test_attest_spools_first_then_drains_once_brain_recorded(tmp_path: Path) -> None:
    ledger, brain, _ = _ledger(tmp_path)
    ledger.contract_set(
        "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0"
    )
    # loaded inside the spy, at call time: the drain (after a successful call) removes the
    # file before this function returns, so the receipt must be read while it still exists.
    seen: list[list[Record]] = []
    real = ledger.client.call

    def spy(name, arguments, *, agent=None):
        if name == "brain_delivery_attest":
            seen.append([load_receipt(p) for p in sorted(ledger.spool.root.glob("*.json"))])
        return real(name, arguments, agent=agent)

    ledger.client.call = spy
    record = ledger.attest(
        "red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1"
    )
    assert seen[0] == [record], "written before the call"
    assert not list(ledger.spool.root.glob("*.json")), "gone once brain recorded it"
    assert ledger.spool.root.stat().st_mode & 0o777 == 0o700, "the project's spool is private"
    assert datetime.fromisoformat(brain.attestations[-1]["emitted_at"]) == record.recorded_at
    assert ledger.list("red-probe", attestation=AttestationKind.DEPLOYED) == [record]


def test_a_refusal_leaves_the_receipt_in_the_spool_and_a_replay_drains_it(tmp_path: Path) -> None:
    ledger, brain, _ = _ledger(tmp_path)
    ledger.contract_set(
        "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0"
    )
    brain.enabled = False
    with pytest.raises(Unattested) as exc:
        ledger.attest(
            "red-probe",
            AttestationKind.DEPLOYED,
            {"sha": "b" * 40},
            issuer="op",
            idempotency_key="d1",
        )
    assert exc.value.cause == "delivery_disabled"
    assert exc.value.receipt.parent == ledger.spool.root and exc.value.receipt.is_file()
    assert "rail ledger replay" in str(exc.value)
    brain.enabled = True
    waiting = load_receipt(exc.value.receipt)
    again = ledger.attest(
        "red-probe",
        AttestationKind.DEPLOYED,
        waiting.data,
        issuer=waiting.issuer,
        idempotency_key=waiting.idempotency_key,
        emitted_at=waiting.recorded_at,
    )
    assert again == waiting and len(brain.attestations) == 1
    assert not exc.value.receipt.exists()


def test_the_spool_belongs_to_the_project_not_to_a_checkout() -> None:
    assert spool_directory("red-probe", {"RAIL_SPOOL_DIR": "/s"}) == Path("/s/red-probe")
    assert spool_directory("red-probe", {}) == (
        Path("~/.local/state/red-rail/spool").expanduser() / "red-probe"
    )


def test_an_unreachable_brain_is_unattested_too(tmp_path: Path) -> None:
    ledger, brain, ticket = _ledger(tmp_path)
    ledger.contract_set(
        "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0"
    )

    def down(agent: str) -> object:
        raise OSError("down")

    ledger.client.transport_factory = down
    with pytest.raises(Unattested) as exc:
        ledger.attest(
            "red-probe",
            AttestationKind.DEPLOYED,
            {"sha": "b" * 40},
            issuer="op",
            idempotency_key="d1",
        )
    assert exc.value.cause == "unreachable"


def test_milestones_are_read_from_the_ticket_never_attested(tmp_path: Path) -> None:
    ledger, brain, ticket = _ledger(tmp_path)
    ledger.contract_set(
        "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0"
    )
    with pytest.raises(LedgerError, match="brain milestone"):
        ledger.attest(
            "red-probe",
            AttestationKind.INTEGRATED,
            {"sha": "d" * 40},
            issuer="op",
            idempotency_key="i",
        )
    brain.integrate(ticket, "d" * 40, issued_at=T0 + timedelta(hours=1))
    brain.fulfil(ticket, issued_at=T0 + timedelta(hours=2))
    integrated = ledger.list("red-probe", attestation=AttestationKind.INTEGRATED)
    fulfilled = ledger.list("red-probe", attestation=AttestationKind.FULFILLED)
    assert len(integrated) == 1 and integrated[0].data["sha"] == "d" * 40
    assert (
        integrated[0].issuer == "brain-v42"
        and integrated[0].idempotency_key == f"integrated:{'d' * 40}"
    )
    assert integrated[0].recorded_at == T0 + timedelta(hours=1)
    assert len(fulfilled) == 1 and fulfilled[0].recorded_at == T0 + timedelta(hours=2)
    assert ledger.get("red-probe", integrated[0].digest) == integrated[0]


def test_contract_set_uses_cas_and_returns_the_revision(tmp_path: Path) -> None:
    ledger, brain, ticket = _ledger(tmp_path)
    first = ledger.contract_set(
        "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0"
    )
    amended = CONTRACT.model_copy(update={"objective": "ship the probe with metrics"})
    second = ledger.contract_set(
        "red-probe", amended, reason="metrics", issuer="op", idempotency_key="c1"
    )
    assert [r["contract_revision"] for r in brain.tickets[ticket].revisions] == [1, 2]
    assert first.payload["contract"]["objective"] == "ship the probe"
    assert second.payload["contract"]["objective"] == "ship the probe with metrics"
    assert not list(tmp_path.rglob("*.json")), "contract_set writes no file of its own"
    contracts = ledger.list("red-probe", kind=RecordKind.CONTRACT)
    assert contracts[-1].payload["contract"]["objective"] == "ship the probe with metrics"
    assert contracts[-1].data["contract"]["priority"] == 0


def test_bind_reads_the_view_and_binds_by_repository_id(tmp_path: Path) -> None:
    from rail.ledger import PullRequestRef

    ledger, brain, ticket = _ledger(tmp_path)
    ledger.contract_set(
        "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0"
    )
    record = ledger.bind(
        "red-probe",
        PullRequestRef(repository="hawkixs/red-probe", number=7, head_sha="a" * 40),
        issuer="op",
        idempotency_key="b1",
    )
    binding = brain.tickets[ticket].bindings[0]
    assert binding["repository_id"] == 4242 and binding["deliverable_key"] == "probe"
    assert record.payload["number"] == 7 and record.payload["binding_id"] == binding["id"]
    # Not an exact `== [record]`: brain's assessment (`FakeBrain._view`) stamps `assessed_at`
    # with the wall clock on every read, so a binding's reconstructed `recorded_at` (and thus
    # its digest) drifts between the write and this read by construction.
    listed = ledger.list("red-probe", kind=RecordKind.BINDING)
    assert len(listed) == 1
    assert listed[0].kind is RecordKind.BINDING
    assert listed[0].payload["number"] == 7 and listed[0].payload["binding_id"] == binding["id"]


def test_rows_of_unknown_kinds_are_ignored_and_a_foreign_digest_is_refused(tmp_path: Path) -> None:
    ledger, brain, ticket = _ledger(tmp_path)
    ledger.contract_set(
        "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0"
    )
    brain.attestations.append(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "ticket_id": ticket,
            "contract_revision": None,
            "kind": "custom_thing",
            "payload": {"x": 1},
            "digest": brain_digest({"x": 1}),
            "issuer_project": "red-probe",
            "issuer_identity": "someone",
            "idempotency_key": "custom:1",
            "emitted_at": T0.isoformat(),
            "recorded_at": T0.isoformat(),
        }
    )
    assert ledger.list("red-probe", kind=RecordKind.ATTESTATION) == []
    brain.attestations[-1].update({"kind": "deployed", "digest": "0" * 64})
    with pytest.raises(LedgerError, match="digest"):
        ledger.list("red-probe")


def test_every_published_tool_code_maps_to_a_known_refusal() -> None:
    assert TOOL_ERROR_CODES <= KNOWN_REFUSALS
    assert set(ATTESTATION_CONTRACT["error_codes"]["transport"]) <= KNOWN_REFUSALS


def test_record_from_row_is_the_documented_mapping() -> None:
    row = {
        "id": "x",
        "ticket_id": "t",
        "contract_revision": 1,
        "kind": "released",
        "payload": {"version": "1.0.0"},
        "digest": brain_digest({"version": "1.0.0"}),
        "issuer_project": "red-probe",
        "issuer_identity": "operator",
        "idempotency_key": "released:1.0.0",
        "emitted_at": "2026-09-18T12:00:00+00:00",
        "recorded_at": "2026-09-18T12:00:03.000001+00:00",
    }
    record = record_from_row("red-probe", row)
    assert record is not None
    assert record.attestation is AttestationKind.RELEASED and record.issuer == "operator"
    assert record.recorded_at == T0 and record.data == {"version": "1.0.0"}
    assert record.verify()


def test_open_ledger_builds_the_brain_backend_from_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = conforming_tree(tmp_path, "red-probe", "dev")
    write_manifest(repo, project="red-probe", tier="dev")
    manifest = (repo / "rail.yaml").read_text()
    assert "ledger: file\n" in manifest
    (repo / "rail.yaml").write_text(
        manifest.replace(
            "ledger: file\n", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n"
        )
    )
    brain = FakeBrain(agent="operator")
    brain.add_ticket("red", "red-probe", "04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f")
    ledger = open_ledger(repo, client=BrainClient.in_memory(brain, agent="operator"))
    assert (
        isinstance(ledger, BrainLedger)
        and str(ledger.ticket) == "04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f"
    )
    monkeypatch.setenv("RAIL_BRAIN_TOKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(LedgerError, match="token"):
        open_ledger(repo)


def test_the_contract_is_set_by_the_requester_never_by_the_executor(tmp_path: Path) -> None:
    """brain: "only the requester may set a delivery contract" — the canonical ticket is
    `red → <project>`, so the rail sets the contract as `red` and attests as the project."""
    ledger, brain, ticket = _ledger(tmp_path)
    ledger.contract_set(
        "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0"
    )
    call = next(c for c in brain.calls if c[0] == "brain_delivery_contract_set")
    assert call[1]["actor"] == "red" and ledger.requester == "red"
    ledger.attest(
        "red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1"
    )
    assert brain.attestations[-1]["issuer_project"] == "red-probe"
    executor = BrainLedger(
        ledger.client,
        ticket=ticket,
        project="red-probe",
        spool_dir=tmp_path / "other" / "spool" / "red-probe",
        requester="red-probe",
    )
    amended = CONTRACT.model_copy(update={"objective": "an amendment the executor may not set"})
    with pytest.raises(LedgerError, match="not_allowed"):
        executor.contract_set("red-probe", amended, reason="x", issuer="op", idempotency_key="c9")


def test_contract_set_is_idempotent_on_content_and_keyed_by_ticket_and_revision(
    tmp_path: Path,
) -> None:
    """Measured on the live brain (2026-09-19): revision 1 landed, then the mirror collided with
    the phase-1 file-ledger key `contract:<project>:1`. The record is keyed by ticket and
    revision, carries the requester as issuer (what brain records), and a re-run with the same
    content creates no new revision."""
    ledger, brain, ticket = _ledger(tmp_path)
    first = ledger.contract_set(
        "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0"
    )
    again = ledger.contract_set(
        "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c1"
    )
    assert again == first
    assert first.idempotency_key == f"contract:{ticket}:1" and first.issuer == "red"
    assert len(brain.tickets[ticket].revisions) == 1
    assert not list(tmp_path.rglob("*.json")), "contract_set writes no file of its own"
    listed = ledger.list("red-probe", kind=RecordKind.CONTRACT)
    assert listed == [first], "the row read back from brain rebuilds the same record, same digest"
    amended = CONTRACT.model_copy(update={"objective": "ship the probe with metrics"})
    second = ledger.contract_set(
        "red-probe", amended, reason="metrics", issuer="op", idempotency_key="c2"
    )
    assert second.idempotency_key == f"contract:{ticket}:2"
    assert len(brain.tickets[ticket].revisions) == 2


def test_server_enriched_fields_do_not_defeat_content_idempotency(tmp_path: Path) -> None:
    """Measured on the live brain (2026-09-19): brain fills `repository_id` from its registry,
    so a re-run comparing our `None` with its id created a duplicate revision 2."""
    ledger, brain, ticket = _ledger(tmp_path)
    bare = Contract(
        objective="ship the probe",
        deliverables=[
            Deliverable(
                key="probe",
                repository="hawkixs/red-probe",
                no_checks_reason="fixture: no check declared",
            )
        ],
    )
    first = ledger.contract_set(
        "red-probe", bare, reason="bootstrap", issuer="op", idempotency_key="c0"
    )
    assert brain.tickets[ticket].revisions[-1]["deliverables"][0]["repository_id"] == 4242
    again = ledger.contract_set(
        "red-probe", bare, reason="bootstrap", issuer="op", idempotency_key="c1"
    )
    assert again == first and len(brain.tickets[ticket].revisions) == 1


def test_attestations_are_listed_in_the_issuer_scope(tmp_path: Path) -> None:
    """A project's attestations span its tickets (`test_the_ledger_of_a_project_spans_its_tickets`
    below): the server is asked in the issuer scope, not restricted to one ticket."""
    ledger, brain, ticket = _ledger(tmp_path)
    ledger.contract_set(
        "red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0"
    )
    ledger.attest(
        "red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1"
    )
    brain.calls.clear()
    assert len(ledger.list("red-probe", attestation=AttestationKind.DEPLOYED)) == 1
    listing = [c for c in brain.calls if c[0] == "brain_delivery_attestation_list"]
    assert listing and listing[0][1].get("ticket_id") is None


def test_accept_calls_brain_as_the_requester_and_returns_the_receipt(tmp_path: Path) -> None:
    ledger, brain, ticket = _ledger(tmp_path)
    ledger.contract_set("red-probe", CONTRACT, reason="r", issuer="red", idempotency_key="c1")
    with pytest.raises(LedgerError, match="not integrated"):
        ledger.accept("red-probe", rationale="too early", issuer="op")
    brain.integrate(ticket, "b" * 40, issued_at=T0 + timedelta(hours=1))
    record = ledger.accept("red-probe", rationale="the probe answers", issuer="red-root")
    call = next(a for n, a in brain.calls if n == "brain_delivery_accept")
    assert call["ticket_id"] == ticket
    assert brain.tickets[ticket].fulfillment_receipt is not None
    assert brain.tickets[ticket].fulfillment_receipt["explicit_acceptance"] == {
        "requester_project": "red",
        "rationale": "the probe answers",
    }
    assert record.attestation is AttestationKind.FULFILLED and record.issuer == "brain-v42"
    assert record.data["sha"] == "b" * 40 and record.data["rationale"] == "the probe answers"
    assert not list(tmp_path.rglob("*.json")), "accept writes no file of its own"
    # idempotent: a second acceptance returns the same receipt, no second call
    again = ledger.accept("red-probe", rationale="the probe answers", issuer="red-root")
    assert again.digest == record.digest


def test_the_ledger_of_a_project_spans_its_tickets(tmp_path: Path) -> None:
    """One ticket per delivery, one ledger per project: attestations of an earlier ticket
    (phase 2) stay visible when the manifest moves to the next one (phase 3)."""
    ledger, brain, ticket = _ledger(tmp_path)
    ledger.contract_set("red-probe", CONTRACT, reason="r", issuer="red", idempotency_key="c1")
    ledger.attest(
        "red-probe",
        AttestationKind.RELEASED,
        {"version": "0.1.0", "sha": "c" * 40, "digest": "sha256:c"},
        issuer="op",
        idempotency_key="released:0.1.0",
    )
    later = brain.add_ticket("red", "red-probe")
    moved = BrainLedger(
        ledger.client,
        ticket=later,
        project="red-probe",
        spool_dir=tmp_path / "spool" / "red-probe",
        clock=_clock(T0 + timedelta(days=1)),
        repository_id=lambda slug: 4242,
    )
    moved.contract_set("red-probe", CONTRACT, reason="phase 3", issuer="red", idempotency_key="c2")
    versions = [
        r.data["version"] for r in moved.list("red-probe", attestation=AttestationKind.RELEASED)
    ]
    assert versions == ["0.1.0"]
    call = next(a for n, a in brain.calls if n == "brain_delivery_attestation_list")
    assert call["ticket_id"] is None


# -- a terminal ticket is not an intention ------------------------------------------------


def _brain_repo(tmp_path: Path, brain: FakeBrain, ticket: str) -> Path:
    """A repository whose manifest declares `ledger: brain` and that ticket."""
    repo = conforming_tree(tmp_path, "red-probe", "bootstrap")
    write_manifest(repo, project="red-probe", tier="bootstrap")
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text() + f"ledger: brain\nticket: {ticket}\n"
    )
    return repo


def test_intent_refuses_a_contract_whose_ticket_is_closed(tmp_path: Path) -> None:
    """A contract record that exists proves it existed, not that it still covers this work.
    Measured on the real ledger: a fulfilled ticket reads `coordination_status: closed`,
    `acceptance_state: accepted`. Six pull requests once shipped under a closed contract
    while `rail check` stayed green, because the gate only asked whether a record existed."""
    from rail.gates.intent import contract as intent_contract

    brain = FakeBrain(agent="op")
    ticket = brain.add_ticket("red", "red-probe")
    brain.register_repository("red-probe", 4242, "hawkixs/red-probe")
    repo = _brain_repo(tmp_path, brain, ticket)
    client = BrainClient.in_memory(brain, agent="op")
    BrainLedger(
        client,
        ticket=ticket,
        project="red-probe",
        spool_dir=repo / "spool" / "red-probe",
        clock=_clock(),
        repository_id=lambda slug: 4242,
    ).contract_set("red-probe", CONTRACT, reason="r", issuer="op", idempotency_key="c1")

    import rail.ledger as ledger_module

    original = ledger_module.open_ledger

    def _open(repo_path: Path, **kwargs: object) -> Ledger:
        return BrainLedger(
            client,
            ticket=ticket,
            project="red-probe",
            spool_dir=repo_path / "spool" / "red-probe",
            clock=_clock(),
            repository_id=lambda slug: 4242,
        )

    import rail.gates.intent as intent_module

    intent_module.open_ledger = _open  # type: ignore[assignment]
    try:
        assert intent_contract(repo).passed, "an open ticket is an intention"

        # brain's raw ticket status, not a boolean: `wontfix` and `acked` are terminal for
        # delivery evidence too (pg_delivery_evidence._TERMINAL_STATUSES), and matching
        # `closed` alone left the gate green on both — the exact failure it exists to close
        for terminal in ("closed", "wontfix", "acked"):
            brain.tickets[ticket].status = terminal
            result = intent_contract(repo)
            assert not result.passed, f"{terminal}: {result.details}"
            assert terminal in result.details, "name the status, do not assert acceptance"
            assert ticket[:8] in result.details, "name the ticket that is no longer open"
            assert "rail contract set" not in result.details, "a new contract needs a ticket"

        # a ticket being worked on is not terminal
        for live in ("open", "in_progress", "resolved"):
            brain.tickets[ticket].status = live
            assert intent_contract(repo).passed, live
    finally:
        intent_module.open_ledger = original  # type: ignore[assignment]
