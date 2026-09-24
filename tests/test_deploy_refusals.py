"""A refusal is not a failed deployment (spec 2026-09-24-private-systemd-target, success
criterion 2: a refusal fails before the first ssh). A target refuses what it will not ship, a
unit that would run as root or a compose file that publishes outside the private address,
while it builds its remote script. Every flow builds the scripts it is about to run before its
first side effect, so such a refusal leaves no trace: no ssh, no attestation, no incident, no
rollback. Addresses are RFC 5737 documentation addresses only."""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from rail.deploy import Artefact, DeployError, flow
from rail.deploy.private_compose import PrivateCompose
from rail.deploy.private_systemd import PrivateSystemd
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from rail.model import load_rail_config
from tests import test_deploy_private_compose as compose_case
from tests import test_deploy_private_systemd as systemd_case
from tests.helpers import commit_all

D1, D2 = "sha256:" + "1" * 64, "sha256:" + "2" * 64


@dataclass(frozen=True)
class Case:
    """One repository whose history holds a file the target ships (`good`) and one it
    refuses (`bad`), and the way to build its target on a recording host."""

    repo: Path
    project: str
    good: str
    bad: str
    refusal: str  # what the refusal names
    build: Callable[[systemd_case.RecordingHost], flow.Target]


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _offline() -> dict[str, object]:
    """Never the network, never a real wait: if a flow ever gets as far as verifying, the
    health poll fails at once instead of reaching a documentation address."""
    now = [0.0]
    return {
        "http": lambda url, timeout: (503, b"down"),
        "clock": lambda: now[0],
        "sleep": lambda seconds: now.__setitem__(0, now[0] + seconds),
    }


def _systemd(tmp_path: Path) -> Case:
    repo = systemd_case._systemd_repo(tmp_path / "repo")
    good = _head(repo)
    unit = systemd_case.GOOD.replace("User=red-monitor\n", "User=root\n")
    (repo / "deploy" / "red-agent.service").write_text(unit)
    commit_all(repo, "fix: the unit runs as root")
    sites = systemd_case._host(tmp_path)

    def build(host: systemd_case.RecordingHost) -> flow.Target:
        cfg = load_rail_config(repo)
        return PrivateSystemd(repo, cfg, sites=sites, run=host, **_offline())

    return Case(repo, "red-monitor", good, _head(repo), "User=", build)


def _compose(tmp_path: Path) -> Case:
    repo = compose_case._private_repo(tmp_path / "repo", compose_case.SAFE)
    good = _head(repo)
    (repo / "deploy" / "compose.yaml").write_text(compose_case.PUBLIC)
    commit_all(repo, "fix: publish on every interface")

    def build(host: systemd_case.RecordingHost) -> flow.Target:
        cfg = load_rail_config(repo)
        return PrivateCompose(repo, cfg, run=host, **_offline())

    return Case(repo, "red-alerts", good, _head(repo), "9100:9100", build)


SHAPES = {"private-systemd": _systemd, "private-compose": _compose}


def _artefact(case: Case, version: str, sha: str, digest: str) -> Artefact:
    return Artefact(version, sha, digest, f"ghcr.io/hawkixs/{case.project}@{digest}")


def _ledger(case: Case) -> FileLedger:
    return FileLedger(case.repo / RECEIPTS_DIR)


def _released(case: Case, artefact: Artefact) -> None:
    _ledger(case).attest(
        case.project,
        AttestationKind.RELEASED,
        {
            "version": artefact.version,
            "sha": artefact.sha,
            "digest": artefact.digest,
            "image": artefact.image,
            "tag": f"v{artefact.version}",
        },
        issuer="op",
        idempotency_key=f"released:{artefact.version}",
    )


def _deployed(case: Case, artefact: Artefact) -> None:
    _ledger(case).attest(
        case.project,
        AttestationKind.DEPLOYED,
        flow.deployed_data(artefact, mode="release", domain="private-1", previous=None),
        issuer="op",
        idempotency_key=f"deployed:{artefact.version}",
    )


def _history(case: Case) -> list[str]:
    return [
        r.attestation.value for r in _ledger(case).list(case.project) if r.attestation is not None
    ]


def _ssh(host: systemd_case.RecordingHost) -> list[list[str]]:
    return [argv for argv in host.argv if argv[0] == "ssh"]


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_refused_release_is_neither_an_incident_nor_a_rollback(
    tmp_path: Path, shape: str
) -> None:
    """With a previous artefact live, a failed forward deployment rolls back over ssh — on
    `private-systemd` that restarts the live service. A refusal is not a failure: it must
    stop before anything reaches the machine or the ledger."""
    case = SHAPES[shape](tmp_path)
    live = _artefact(case, "0.1.0", case.good, D1)
    _deployed(case, live)
    _released(case, _artefact(case, "0.1.1", case.bad, D2))
    before = _history(case)
    host = systemd_case.RecordingHost()
    with pytest.raises(DeployError, match=case.refusal):
        flow.forward(case.repo, load_rail_config(case.repo), _ledger(case), target=case.build(host))
    assert _ssh(host) == [], "nothing may reach the machine"
    assert _history(case) == before, "a refusal writes nothing to the ledger"


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_refused_first_release_leaves_no_open_incident(tmp_path: Path, shape: str) -> None:
    """Without a previous artefact a failure leaves the incident open; a refusal is not one."""
    case = SHAPES[shape](tmp_path)
    _released(case, _artefact(case, "0.1.0", case.bad, D1))
    host = systemd_case.RecordingHost()
    with pytest.raises(DeployError, match=case.refusal):
        flow.forward(case.repo, load_rail_config(case.repo), _ledger(case), target=case.build(host))
    assert _ssh(host) == []
    assert _history(case) == ["released"]


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_rollback_to_a_refused_artefact_records_nothing(tmp_path: Path, shape: str) -> None:
    """The previous release carries a file the target now refuses: the rollback is refused
    before it starts, and the live artefact is left alone without a recorded incident."""
    case = SHAPES[shape](tmp_path)
    _deployed(case, _artefact(case, "0.1.0", case.bad, D1))
    _deployed(case, _artefact(case, "0.1.1", case.good, D2))
    before = _history(case)
    host = systemd_case.RecordingHost()
    with pytest.raises(DeployError, match=case.refusal):
        flow.rollback(
            case.repo, load_rail_config(case.repo), _ledger(case), target=case.build(host)
        )
    assert _ssh(host) == []
    assert _history(case) == before


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("refused", ["previous", "live"])
def test_a_drill_that_would_apply_a_refused_artefact_never_starts(
    tmp_path: Path, shape: str, refused: str
) -> None:
    """A drill applies both artefacts: the previous one, then the live one again. When
    either is refused, the drill stops before its simulated incident — in particular it never
    rolls back over ssh only to find that it cannot roll forward."""
    case = SHAPES[shape](tmp_path)
    previous_sha, live_sha = (
        (case.bad, case.good) if refused == "previous" else (case.good, case.bad)
    )
    _deployed(case, _artefact(case, "0.1.0", previous_sha, D1))
    _deployed(case, _artefact(case, "0.1.1", live_sha, D2))
    before = _history(case)
    host = systemd_case.RecordingHost()
    with pytest.raises(DeployError, match=case.refusal):
        flow.drill(case.repo, load_rail_config(case.repo), _ledger(case), target=case.build(host))
    assert _ssh(host) == []
    assert _history(case) == before
