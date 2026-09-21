"""The flows write the ledger sequences of the vocabulary (`rail.ledger`) around a target
that is faked here; the gates then read what the flows wrote."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.deploy import Artefact, DeployError, LiveVersion, Locked, Step, flow
from rail.gates.evidence import deployed
from rail.gates.evidence import drill as drill_gate
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from tests.helpers import conforming_tree, git

D1, D2 = "sha256:" + "1" * 64, "sha256:" + "2" * 64


class FakeTarget:
    """Applies instantly; `broken` digests fail their verification."""

    def __init__(self) -> None:
        self.domain = "red-probe.example.invalid"
        self.applied: list[str] = []
        self.broken: set[str] = set()
        self.locked = False

    def steps(self, artefact: Artefact) -> list[Step]:
        return [
            Step(
                f"ssh red-vps: release {artefact.version}",
                ("ssh", "red-vps", "bash", "-s"),
                "set -e",
            )
        ]

    def apply(self, artefact: Artefact) -> LiveVersion:
        if self.locked:
            raise Locked("another deployment holds the lock")
        self.applied.append(artefact.digest)
        if artefact.digest in self.broken:
            raise DeployError(f"{artefact.version}: not healthy within 120s (HTTP 503)")
        return LiveVersion("red-probe", artefact.version, artefact.sha, artefact.digest)


@pytest.fixture
def target(monkeypatch: pytest.MonkeyPatch) -> FakeTarget:
    fake = FakeTarget()
    monkeypatch.setattr(flow, "make_target", lambda repo, cfg, **kwargs: fake)
    return fake


def _repo(tmp_path: Path, *releases: tuple[str, str]) -> Path:
    repo = conforming_tree(tmp_path, "red-probe", "prod")
    head = git(repo, "rev-parse", "HEAD")
    ledger = FileLedger(repo / RECEIPTS_DIR)
    for version, digest in releases:
        ledger.attest(
            "red-probe",
            AttestationKind.RELEASED,
            {
                "version": version,
                "sha": head,
                "digest": digest,
                "image": f"ghcr.io/hawkixs/red-probe@{digest}",
                "tag": f"v{version}",
            },
            issuer="op",
            idempotency_key=f"released:{version}",
        )
    return repo


def _kinds(repo: Path) -> list[tuple[str, dict]]:
    return [
        (r.attestation.value, r.data)
        for r in FileLedger(repo / RECEIPTS_DIR).list("red-probe", kind=None)
        if r.attestation is not None and r.attestation is not AttestationKind.RELEASED
    ]


def test_forward_deploys_the_newest_release_and_attests_it(
    tmp_path: Path, target: FakeTarget
) -> None:
    repo = _repo(tmp_path, ("0.1.0", D1))
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--yes", "--json"])
    assert out.exit_code == 0, out.output
    assert target.applied == [D1]
    kinds = _kinds(repo)
    assert [k for k, _ in kinds] == ["deployed"]
    data = kinds[0][1]
    assert data["mode"] == "release" and data["digest"] == D1 and data["target"] == "vps-traefik"
    assert data["version"] == "0.1.0" and data["domain"] == "red-probe.example.invalid"
    assert data["previous_digest"] == ""
    assert json.loads(out.output)["live"]["image_digest"] == D1
    assert deployed(repo).passed


def test_a_failed_forward_rolls_back_and_writes_the_failure_sequence(
    tmp_path: Path, target: FakeTarget
) -> None:
    repo = _repo(tmp_path, ("0.1.0", D1), ("0.1.1", D2))
    CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--version", "0.1.0", "--yes"])
    target.broken.add(D2)
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--yes"])
    assert out.exit_code == 1, out.output
    assert "rolled back to 0.1.0" in out.output
    assert target.applied == [D1, D2, D1]
    kinds = _kinds(repo)
    assert [k for k, _ in kinds] == [
        "deployed",
        "incident_detected",
        "rolled_back",
        "deployed",
        "restored",
    ]
    incident, rollback, live, restored = kinds[1][1], kinds[2][1], kinds[3][1], kinds[4][1]
    assert incident["automatic"] is True and incident["digest"] == D2 and incident["drill"] is False
    assert (
        rollback["from_digest"] == D2
        and rollback["to_digest"] == D1
        and rollback["automatic"] is True
    )
    assert live["mode"] == "rollback" and live["digest"] == D1 and live["previous_digest"] == D2
    assert restored["drill"] is False and isinstance(restored["recovery_seconds"], int)
    assert not deployed(repo).passed  # the newest release (0.1.1) is not live


def test_rollback_and_drill_sequences(tmp_path: Path, target: FakeTarget) -> None:
    repo = _repo(tmp_path, ("0.1.0", D1), ("0.1.1", D2))
    runner = CliRunner()
    assert (
        runner.invoke(
            main, ["deploy", "--repo", str(repo), "--version", "0.1.0", "--yes"]
        ).exit_code
        == 0
    )
    assert runner.invoke(main, ["deploy", "--repo", str(repo), "--yes"]).exit_code == 0
    out = runner.invoke(main, ["drill", "--repo", str(repo), "--yes"])
    assert out.exit_code == 0, out.output
    assert "recovery" in out.output
    assert target.applied[-2:] == [D1, D2]  # back to 0.1.0, forward to 0.1.1 again
    kinds = [k for k, _ in _kinds(repo)]
    assert kinds == [
        "deployed",
        "deployed",
        "incident_detected",
        "rolled_back",
        "restored",
        "deployed",
    ]
    last = _kinds(repo)[-1][1]
    assert last["mode"] == "drill" and last["digest"] == D2
    assert all(d["drill"] is True for k, d in _kinds(repo)[2:5])
    assert deployed(repo).passed and drill_gate(repo).passed, drill_gate(repo).details
    out = runner.invoke(main, ["deploy", "--repo", str(repo), "--rollback", "--yes"])
    assert out.exit_code == 0, out.output
    kinds = [k for k, _ in _kinds(repo)]
    assert kinds[-3:] == ["rolled_back", "deployed", "restored"]
    rollback = _kinds(repo)[-3][1]
    assert rollback["drill"] is False and rollback["automatic"] is False
    assert rollback["from_digest"] == D2 and rollback["to_digest"] == D1
    assert _kinds(repo)[-2][1]["mode"] == "rollback"
    assert not deployed(repo).passed


def test_plan_prints_and_touches_nothing(tmp_path: Path, target: FakeTarget) -> None:
    repo = _repo(tmp_path, ("0.1.0", D1))
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--plan"])
    assert out.exit_code == 0, out.output
    assert "ssh red-vps: release 0.1.0" in out.output and target.applied == []
    assert _kinds(repo) == []


def test_refusals_and_exit_codes(tmp_path: Path, target: FakeTarget) -> None:
    repo = _repo(tmp_path)
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--yes"])
    assert out.exit_code == 1 and "no released attestation" in out.output
    repo = _repo(tmp_path / "b", ("0.1.0", D1))
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--rollback", "--yes"])
    assert out.exit_code == 1 and "nothing to roll back to" in out.output
    out = CliRunner().invoke(main, ["drill", "--repo", str(repo), "--yes"])
    assert out.exit_code == 1 and "two deployed" in out.output
    target.locked = True
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--yes"])
    assert out.exit_code == 3 and "lock" in out.output
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo)], input="n\n")
    assert out.exit_code == 1  # aborted at the confirmation


def test_an_unattested_step_exits_2_with_every_replay_command(
    tmp_path: Path, target: FakeTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail.ledger import Unattested

    repo = _repo(tmp_path, ("0.1.0", D1))
    ledger = FileLedger(repo / RECEIPTS_DIR)
    real_attest = ledger.attest

    def refusing(project, kind, data, **kwargs):
        record = real_attest(project, kind, data, **kwargs)
        raise Unattested(ledger.path_of(record), "unreachable")

    monkeypatch.setattr(ledger, "attest", refusing)
    monkeypatch.setattr("rail.commands.deploy.open_ledger", lambda repo: ledger)
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--yes"])
    assert out.exit_code == 2, out.output
    assert "replay with: rail attest deployed --from" in out.output
    assert target.applied == [D1]  # the service is live; only the ledger is behind


def test_a_failed_first_deploy_records_only_the_incident(
    tmp_path: Path, target: FakeTarget
) -> None:
    """No previous artefact: nothing is rolled back, so no `rolled_back` is claimed."""
    repo = _repo(tmp_path, ("0.1.0", D1))
    target.broken.add(D1)
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--yes"])
    assert out.exit_code == 1 and "nothing to roll back to" in out.output
    assert [k for k, _ in _kinds(repo)] == ["incident_detected"]


def test_a_failed_rollback_leaves_the_incident_open(tmp_path: Path, target: FakeTarget) -> None:
    repo = _repo(tmp_path, ("0.1.0", D1), ("0.1.1", D2))
    assert (
        CliRunner()
        .invoke(main, ["deploy", "--repo", str(repo), "--version", "0.1.0", "--yes"])
        .exit_code
        == 0
    )
    target.broken.update({D1, D2})
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--yes"])
    assert out.exit_code == 1 and "rollback to 0.1.0 failed too" in out.output
    assert [k for k, _ in _kinds(repo)] == ["deployed", "incident_detected"]


def test_a_lock_during_the_rollback_exits_3_after_the_incident(
    tmp_path: Path, target: FakeTarget
) -> None:
    repo = _repo(tmp_path, ("0.1.0", D1), ("0.1.1", D2))
    assert (
        CliRunner()
        .invoke(main, ["deploy", "--repo", str(repo), "--version", "0.1.0", "--yes"])
        .exit_code
        == 0
    )
    target.broken.add(D2)
    original = target.apply

    def apply(artefact: Artefact) -> LiveVersion:
        if artefact.digest == D1 and target.applied and target.applied[-1] == D2:
            raise Locked("another deployment holds the lock")
        return original(artefact)

    target.apply = apply  # type: ignore[method-assign]
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--yes"])
    assert out.exit_code == 3 and "while rolling back 0.1.1" in out.output
    assert [k for k, _ in _kinds(repo)] == ["deployed", "incident_detected"]


def test_a_failed_manual_rollback_records_an_incident(tmp_path: Path, target: FakeTarget) -> None:
    repo = _repo(tmp_path, ("0.1.0", D1), ("0.1.1", D2))
    runner = CliRunner()
    assert (
        runner.invoke(
            main, ["deploy", "--repo", str(repo), "--version", "0.1.0", "--yes"]
        ).exit_code
        == 0
    )
    assert runner.invoke(main, ["deploy", "--repo", str(repo), "--yes"]).exit_code == 0
    target.broken.add(D1)
    out = runner.invoke(main, ["deploy", "--repo", str(repo), "--rollback", "--yes"])
    assert out.exit_code == 1 and "rollback to 0.1.0 failed" in out.output
    kinds = _kinds(repo)
    assert [k for k, _ in kinds][-1] == "incident_detected"
    assert kinds[-1][1]["automatic"] is False and kinds[-1][1]["digest"] == D2


def test_a_failed_drill_rollback_aborts_the_drill(tmp_path: Path, target: FakeTarget) -> None:
    repo = _repo(tmp_path, ("0.1.0", D1), ("0.1.1", D2))
    runner = CliRunner()
    assert (
        runner.invoke(
            main, ["deploy", "--repo", str(repo), "--version", "0.1.0", "--yes"]
        ).exit_code
        == 0
    )
    assert runner.invoke(main, ["deploy", "--repo", str(repo), "--yes"]).exit_code == 0
    target.broken.add(D1)
    out = runner.invoke(main, ["drill", "--repo", str(repo), "--yes"])
    assert out.exit_code == 1 and "drill aborted" in out.output
    kinds = _kinds(repo)
    assert [k for k, _ in kinds] == [
        "deployed",
        "deployed",
        "incident_detected",
        "incident_detected",
    ]
    assert kinds[-2][1]["drill"] is True
    assert kinds[-1][1]["drill"] is False and "rollback to 0.1.0 failed" in kinds[-1][1]["reason"]


def test_a_ledger_refusal_mid_sequence_exits_2(
    tmp_path: Path, target: FakeTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail.ledger import IdempotencyConflict

    repo = _repo(tmp_path, ("0.1.0", D1))
    ledger = FileLedger(repo / RECEIPTS_DIR)

    def refusing(project, kind, data, **kwargs):
        raise IdempotencyConflict("key reused")

    monkeypatch.setattr(ledger, "attest", refusing)
    monkeypatch.setattr("rail.commands.deploy.open_ledger", lambda repo: ledger)
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--yes"])
    assert out.exit_code == 2 and "the ledger refused deployed: key reused" in out.output
    assert target.applied == [D1]


def test_the_flows_write_the_same_attestations_whatever_the_target_shape(
    tmp_path: Path, target: FakeTarget
) -> None:
    """The plan's parity requirement: forward, rollback and drill must not branch on the
    target. Driven here against a `private-compose` manifest — same fake target, same
    sequences — so a future flow that special-cases a shape shows up as a diff."""
    repo = _repo(tmp_path, ("0.1.0", D1), ("0.1.1", D2))
    manifest = (
        (repo / "rail.yaml")
        .read_text()
        .replace("  target: vps-traefik\n", "  target: private-compose\n")
    )
    manifest = "\n".join(
        "  healthcheck: http://10.100.0.4:9204/healthz"
        if line.strip().startswith("healthcheck:")
        else line
        for line in manifest.splitlines()
    )
    (repo / "rail.yaml").write_text(manifest + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "chore: move to the private target")

    runner = CliRunner()
    assert (
        runner.invoke(
            main, ["deploy", "--repo", str(repo), "--version", "0.1.0", "--yes"]
        ).exit_code
        == 0
    )
    assert runner.invoke(main, ["deploy", "--repo", str(repo), "--yes"]).exit_code == 0
    assert runner.invoke(main, ["drill", "--repo", str(repo), "--yes"]).exit_code == 0

    assert [k for k, _ in _kinds(repo)] == [
        "deployed",
        "deployed",
        "incident_detected",
        "rolled_back",
        "restored",
        "deployed",
    ]
    assert target.applied == [D1, D2, D1, D2]
