from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from rail.brain.client import BrainClient
from rail.cli import main
from rail.commands import accept, attest, bind, contract
from rail.commands import deploy as deploy_command
from rail.commands import drill as drill_command
from rail.commands import release as release_command
from rail.commands._actor import no_issuer_option, resolve_or_exit
from rail.deploy import flow
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.brain import BrainLedger
from rail.ledger.file import FileLedger, receipt_filename
from rail.ledger.spool import spool_directory
from tests.fake_brain import FakeBrain
from tests.helpers import conforming_tree, git
from tests.test_cli_deploy import D1, D2, FakeTarget
from tests.test_cli_deploy import _repo as deploy_repo_with
from tests.test_ledger_brain import CONTRACT, T0, _clock
from tests.test_release import FakeHost
from tests.test_release import _repo as release_repo


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


@pytest.mark.parametrize("args", [["--issuer", "operator"], ["--issuer"]])
def test_issuer_is_a_usage_error_naming_the_variable(args: list[str]) -> None:
    out = CliRunner().invoke(probe, args)
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
    monkeypatch.setattr("rail.actor.stdin_is_tty", lambda: False)
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


# release, deploy and drill: the host gestures of stages 7 to 9 (a file ledger, a faked host)


@pytest.fixture
def faked(monkeypatch: pytest.MonkeyPatch) -> FakeTarget:
    fake = FakeTarget()
    monkeypatch.setattr(flow, "make_target", lambda repo, cfg, **kwargs: fake)
    return fake


def _unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAIL_ACTOR")
    monkeypatch.setattr("rail.actor.stdin_is_tty", lambda: False)


def _release_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, FakeHost]:
    host = FakeHost()
    monkeypatch.setattr(release_command, "RUN", host)
    return release_repo(tmp_path), host


def _issuers(repo: Path) -> set[str]:
    return {r.issuer for r in FileLedger(repo / RECEIPTS_DIR).list("red-probe", kind=None)}


class _ForbiddenLedger:
    """A ledger that fails on first touch: a refused actor must never reach it."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the ledger was touched ({name}) before the actor was resolved")


def _forbid_ledger(monkeypatch: pytest.MonkeyPatch, *modules: Any) -> None:
    for module in modules:
        monkeypatch.setattr(module, "open_ledger", lambda repo: _ForbiddenLedger())


def test_release_plan_and_deploy_plan_need_no_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, faked: FakeTarget
) -> None:
    repo, _ = _release_world(tmp_path / "release", monkeypatch)
    _unresolvable(monkeypatch)
    out = CliRunner().invoke(main, ["release", "--repo", str(repo), "--version", "0.1.0", "--plan"])
    assert out.exit_code == 0, out.output
    deploy_repo = deploy_repo_with(tmp_path / "deploy", ("0.1.0", D1))
    out = CliRunner().invoke(main, ["deploy", "--repo", str(deploy_repo), "--plan"])
    assert out.exit_code == 0, out.output


def test_release_without_an_actor_exits_2_before_any_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, host = _release_world(tmp_path, monkeypatch)
    before = _issuers(repo)
    _unresolvable(monkeypatch)
    _forbid_ledger(monkeypatch, release_command)
    out = CliRunner().invoke(
        main, ["release", "--repo", str(repo), "--version", "0.1.0", "--yes", "--json"]
    )
    assert out.exit_code == 2 and "RAIL_ACTOR" in out.output
    assert host.calls == [], "not even a fetch or an ls-remote"
    assert _issuers(repo) == before
    assert not list(spool_directory("red-probe").glob("*"))


@pytest.mark.parametrize("flags", [[], ["--rollback"]])
def test_deploy_without_an_actor_exits_2_before_any_effect(
    flags: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, faked: FakeTarget
) -> None:
    repo = deploy_repo_with(tmp_path, ("0.1.0", D1), ("0.1.1", D2))
    if flags:
        runner = CliRunner()
        for version in ("0.1.0", "0.1.1"):
            args = ["deploy", "--repo", str(repo), "--version", version, "--yes"]
            assert runner.invoke(main, args).exit_code == 0
    applied, before = list(faked.applied), _issuers(repo)
    receipts = sorted((repo / RECEIPTS_DIR).glob("*.json"))
    _unresolvable(monkeypatch)
    _forbid_ledger(monkeypatch, deploy_command)
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), *flags, "--yes"])
    assert out.exit_code == 2 and "RAIL_ACTOR" in out.output
    assert faked.applied == applied and _issuers(repo) == before
    assert sorted((repo / RECEIPTS_DIR).glob("*.json")) == receipts
    assert not list(spool_directory("red-probe").glob("*"))


def test_drill_without_an_actor_exits_2_before_any_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, faked: FakeTarget
) -> None:
    repo = deploy_repo_with(tmp_path, ("0.1.0", D1), ("0.1.1", D2))
    runner = CliRunner()
    for version in ("0.1.0", "0.1.1"):
        args = ["deploy", "--repo", str(repo), "--version", version, "--yes"]
        assert runner.invoke(main, args).exit_code == 0
    applied = list(faked.applied)
    receipts = sorted((repo / RECEIPTS_DIR).glob("*.json"))
    _unresolvable(monkeypatch)
    _forbid_ledger(monkeypatch, drill_command)
    out = runner.invoke(main, ["drill", "--repo", str(repo), "--yes"])
    assert out.exit_code == 2 and "RAIL_ACTOR" in out.output
    assert faked.applied == applied
    assert sorted((repo / RECEIPTS_DIR).glob("*.json")) == receipts
    assert not list(spool_directory("red-probe").glob("*"))


def test_release_deploy_rollback_and_drill_record_the_resolved_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, faked: FakeTarget
) -> None:
    monkeypatch.setenv("RAIL_ACTOR", AGENT)
    repo, _ = _release_world(tmp_path / "release", monkeypatch)
    out = CliRunner().invoke(main, ["release", "--repo", str(repo), "--version", "0.1.0", "--yes"])
    assert out.exit_code == 0, out.output
    released = FileLedger(repo / RECEIPTS_DIR).list("red-probe", kind=None)[-1]
    assert released.attestation is AttestationKind.RELEASED and released.issuer == AGENT

    deploy_repo = deploy_repo_with(tmp_path / "deploy", ("0.1.0", D1), ("0.1.1", D2))
    runner = CliRunner()
    for args in (
        ["--version", "0.1.0"],
        [],
        ["--rollback"],
    ):
        assert (
            runner.invoke(main, ["deploy", "--repo", str(deploy_repo), *args, "--yes"]).exit_code
            == 0
        )
    assert runner.invoke(main, ["deploy", "--repo", str(deploy_repo), "--yes"]).exit_code == 0
    assert runner.invoke(main, ["drill", "--repo", str(deploy_repo), "--yes"]).exit_code == 0
    written = [
        r
        for r in FileLedger(deploy_repo / RECEIPTS_DIR).list("red-probe", kind=None)
        if r.attestation is not AttestationKind.RELEASED
    ]
    assert len(written) >= 10 and {r.issuer for r in written} == {AGENT}


@pytest.mark.parametrize("command", [["release", "--version", "0.1.0"], ["deploy"], ["drill"]])
def test_issuer_is_refused_by_release_deploy_and_drill(command: list[str], tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-probe", "prod")
    out = CliRunner().invoke(main, [*command, "--repo", str(repo), "--issuer", "x"])
    assert out.exit_code == 2 and "RAIL_ACTOR" in out.output


def _rule_world(tmp_path: Path):
    from tests.test_cli_reviewer import _awaiting_repo, _rule

    repo, config = _awaiting_repo(tmp_path)
    return repo, lambda: _rule(config)


def _rulings(repo: Path) -> list:
    return FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.REVIEW_RULING
    )


def test_a_ruling_is_refused_for_an_agent_behind_a_pseudo_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("RAIL_ACTOR")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr("rail.actor.stdin_is_tty", lambda: True)
    repo, rule = _rule_world(tmp_path)
    receipts = sorted((repo / RECEIPTS_DIR).glob("*.json"))
    out = rule()
    assert out.exit_code == 2 and "operator's gesture" in out.output
    assert sorted((repo / RECEIPTS_DIR).glob("*.json")) == receipts and not _rulings(repo)
    assert not list(spool_directory("red-alpha").glob("*"))


def test_a_ruling_is_refused_when_the_actor_cannot_be_told(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("RAIL_ACTOR")
    monkeypatch.setattr("rail.actor.stdin_is_tty", lambda: False)
    repo, rule = _rule_world(tmp_path)
    out = rule()
    assert out.exit_code == 2 and "cannot tell who runs" in out.output
    assert not _rulings(repo)


def test_a_ruling_is_refused_for_a_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("RAIL_ACTOR", "service:cron")
    repo, rule = _rule_world(tmp_path)
    out = rule()
    assert out.exit_code == 2 and "operator's gesture" in out.output
    assert not _rulings(repo)


def test_a_ruling_is_refused_when_the_operator_label_is_piped_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RAIL_ACTOR=operator inherited by an unmarked harness, no terminal: not a typed gesture."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("RAIL_ACTOR", "operator")
    monkeypatch.setattr("rail.actor.stdin_is_tty", lambda: False)
    repo, rule = _rule_world(tmp_path)
    receipts = sorted((repo / RECEIPTS_DIR).glob("*.json"))
    out = rule()
    assert out.exit_code == 2 and "typed at a terminal" in out.output
    assert sorted((repo / RECEIPTS_DIR).glob("*.json")) == receipts and not _rulings(repo)
    assert not list(spool_directory("red-alpha").glob("*"))


def test_a_ruling_is_attested_as_the_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("rail.actor.stdin_is_tty", lambda: True)
    repo, rule = _rule_world(tmp_path)
    out = rule()
    assert out.exit_code == 0, out.output
    assert [r.issuer for r in _rulings(repo)] == ["operator"]
