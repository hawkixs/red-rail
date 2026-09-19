"""`rail accept`: stage 10 from the command line — a `fulfilled` attestation on the file
ledger, `brain_delivery_accept` as the requester on the shared ledger."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.brain.client import BrainClient
from rail.cli import main
from rail.commands import accept as accept_command
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.brain import BrainLedger
from rail.ledger.file import FileLedger
from tests.fake_brain import FakeBrain
from tests.helpers import commit_all, conforming_tree, git, write_manifest
from tests.test_ledger_brain import CONTRACT, T0, _clock


def test_accept_on_the_file_ledger_writes_fulfilled(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "prod")
    out = CliRunner().invoke(
        main, ["accept", "--repo", str(repo), "--rationale", "proof observed", "--json"]
    )
    assert out.exit_code == 0, out.output
    record = json.loads(out.output)
    assert record["payload"] == {
        "kind": "fulfilled",
        "data": {"rationale": "proof observed", "sha": git(repo, "rev-parse", "HEAD")},
    }
    fulfilled = FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.FULFILLED
    )
    assert len(fulfilled) == 1


def test_accept_on_the_brain_ledger_is_the_requesters_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = conforming_tree(tmp_path, "red-probe", "prod")
    brain = FakeBrain(agent="red-root")
    ticket = brain.add_ticket("red", "red-probe")
    brain.register_repository("red-probe", 4242, "hawkixs/red-probe")
    write_manifest(repo, project="red-probe", tier="prod", deploy=True)
    text = (
        (repo / "rail.yaml")
        .read_text()
        .replace("ledger: file\n", f"ledger: brain\nticket: {ticket}\n")
    )
    (repo / "rail.yaml").write_text(text)
    commit_all(repo, "chore: brain ledger")
    ledger = BrainLedger(
        BrainClient.in_memory(brain, agent="red-root"),
        ticket=ticket,
        project="red-probe",
        receipts_dir=repo / RECEIPTS_DIR,
        clock=_clock(),
        repository_id=lambda slug: 4242,
    )
    monkeypatch.setattr(accept_command, "open_ledger", lambda repo: ledger)
    ledger.contract_set("red-probe", CONTRACT, reason="r", issuer="red", idempotency_key="c1")
    out = CliRunner().invoke(main, ["accept", "--repo", str(repo), "--rationale", "too early"])
    assert out.exit_code == 1 and "not integrated" in out.output
    brain.integrate(ticket, "b" * 40, issued_at=T0)
    out = CliRunner().invoke(
        main,
        [
            "accept",
            "--repo",
            str(repo),
            "--rationale",
            "the probe answers",
            "--issuer",
            "red-root",
        ],
    )
    assert out.exit_code == 0, out.output
    assert out.output.startswith("fulfilled  fulfilled:" + "b" * 40)
    assert brain.tickets[ticket].fulfillment_receipt is not None
