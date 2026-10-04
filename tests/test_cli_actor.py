from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from rail.brain.client import BrainClient
from rail.cli import main
from rail.commands import accept, attest, bind, contract
from rail.commands._actor import no_issuer_option, resolve_or_exit
from rail.ledger import AttestationKind
from rail.ledger.brain import BrainLedger
from rail.ledger.file import FileLedger, receipt_filename
from rail.ledger.spool import spool_directory
from tests.fake_brain import FakeBrain
from tests.helpers import conforming_tree, git
from tests.test_ledger_brain import CONTRACT, T0, _clock


@click.command()
@no_issuer_option
def probe() -> None:
    click.echo(resolve_or_exit())


def test_the_resolved_label_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "service:probe")
    out = CliRunner().invoke(probe, [])
    assert out.exit_code == 0 and out.output.strip() == "service:probe"


def test_a_refusal_exits_2_and_names_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAIL_ACTOR")
    out = CliRunner().invoke(probe, [])
    assert out.exit_code == 2 and "RAIL_ACTOR=agent:<name>" in out.output


def test_issuer_is_a_usage_error_naming_the_variable() -> None:
    out = CliRunner().invoke(probe, ["--issuer", "operator"])
    assert out.exit_code == 2 and "RAIL_ACTOR" in out.output


AGENT = "agent:claude-code:s1"
HEAD = "a" * 40
GESTURES = {
    "bind": ["bind", "--pr", "3", "--head", HEAD],
    "accept": ["accept", "--rationale", "the probe answers"],
    "attest": ["attest", "deployed", "--data", "digest=sha256:abc"],
    "contract": [
        *("contract", "set", "--objective", "ship it", "--reason", "r"),
        *("--no-checks-reason", "fixture: no check", "--yes"),
    ],
}


@dataclass
class World:
    repo: Path
    brain: FakeBrain
    labels: list[str]  # the X-Brain-Agent label of every call brain received


def _world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """A brain ledger with a contract and an integration, behind all four gestures."""
    repo = conforming_tree(tmp_path, "red-probe", "dev")
    brain = FakeBrain(agent="operator")
    ticket = brain.add_ticket("red", "red-probe")
    brain.register_repository("red-probe", 4242, "hawkixs/red-probe")
    labels: list[str] = []

    def transport(label: str) -> Any:
        labels.append(label)
        brain.agent = label
        return brain.server

    ledger = BrainLedger(
        BrainClient(transport, "operator"),
        ticket=ticket,
        project="red-probe",
        spool_dir=spool_directory("red-probe"),
        clock=_clock(),
        repository_id=lambda slug: 4242,
    )
    ledger.contract_set("red-probe", CONTRACT, reason="r", issuer="red", idempotency_key="c1")
    brain.integrate(ticket, git(repo, "rev-parse", "HEAD"), issued_at=T0)
    for module in (accept, attest, bind, contract):
        monkeypatch.setattr(module, "open_ledger", lambda repo: ledger)
    return World(repo, brain, labels)


@pytest.mark.parametrize("gesture", GESTURES)
def test_a_gesture_sends_the_resolved_actor_to_brain(
    gesture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path, monkeypatch)
    monkeypatch.setenv("RAIL_ACTOR", AGENT)
    world.labels.clear()
    out = CliRunner().invoke(main, [*GESTURES[gesture], "--repo", str(world.repo)])
    assert out.exit_code == 0, out.output
    assert AGENT in world.labels
    if gesture == "attest":
        assert world.brain.attestations[-1]["issuer_identity"] == AGENT


@pytest.mark.parametrize("gesture", GESTURES)
def test_a_gesture_without_an_actor_exits_2_before_any_effect(
    gesture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path, monkeypatch)
    monkeypatch.delenv("RAIL_ACTOR")
    monkeypatch.setattr("rail.actor._stdin_is_tty", lambda: False)
    calls = len(world.brain.calls)
    out = CliRunner().invoke(main, [*GESTURES[gesture], "--repo", str(world.repo)])
    assert out.exit_code == 2 and "RAIL_ACTOR" in out.output
    assert len(world.brain.calls) == calls
    assert not list(spool_directory("red-probe").glob("*"))


@pytest.mark.parametrize("gesture", GESTURES)
def test_issuer_is_refused_naming_the_variable(gesture: str, tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-probe", "dev")
    out = CliRunner().invoke(main, [*GESTURES[gesture], "--repo", str(repo), "--issuer", "x"])
    assert out.exit_code == 2 and "RAIL_ACTOR" in out.output


def test_attest_from_keeps_the_receipts_own_issuer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path, monkeypatch)
    monkeypatch.setenv("RAIL_ACTOR", AGENT)
    source = FileLedger(tmp_path / "elsewhere").attest(
        "red-probe",
        AttestationKind.DEPLOYED,
        {"sha": "c" * 40},
        issuer="service:old",
        idempotency_key="d2",
    )
    receipt = tmp_path / "elsewhere" / receipt_filename(source)
    out = CliRunner().invoke(
        main, ["attest", "deployed", "--repo", str(world.repo), "--from", str(receipt)]
    )
    assert out.exit_code == 0, out.output
    assert world.brain.attestations[-1]["issuer_identity"] == "service:old"
