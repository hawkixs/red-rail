"""DORA from evidence timestamps, nothing else: no separate instrumentation."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.gates import build as build_gates
from rail.ledger import RECEIPTS_DIR, AttestationKind, Contract, Deliverable
from rail.ledger.file import FileLedger
from rail.metrics import compute_metrics
from tests.helpers import conforming_tree, git, write_roster

T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # == the fixture commit timestamp


@pytest.fixture(autouse=True)
def _no_real_gitleaks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (0, "no leaks found"))


def _ledger_at(repo: Path, when: datetime) -> FileLedger:
    return FileLedger(repo / RECEIPTS_DIR, clock=lambda: when)


def _history(tmp_path: Path) -> Path:
    """contract T0; deployed T0+48h then rolled back (real) at +49h; deployed again at +72h;
    incident at +80h restored at +82h; a drill rollback at +90h that must not count."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"])
    head = git(repo, "rev-parse", "HEAD")
    h = timedelta(hours=1)
    _ledger_at(repo, T0).contract_set(
        "red-alpha",
        Contract(
            objective="x", deliverables=[Deliverable(key="m", repository="hawkixs/red-alpha")]
        ),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c1",
    )
    _ledger_at(repo, T0 + 48 * h).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:a"},
        issuer="op",
        idempotency_key="d1",
    )
    _ledger_at(repo, T0 + 49 * h).attest(
        "red-alpha",
        AttestationKind.ROLLED_BACK,
        {"drill": False},
        issuer="op",
        idempotency_key="rb1",
    )
    _ledger_at(repo, T0 + 72 * h).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:b"},
        issuer="op",
        idempotency_key="d2",
    )
    _ledger_at(repo, T0 + 80 * h).attest(
        "red-alpha", AttestationKind.INCIDENT_DETECTED, {}, issuer="op", idempotency_key="inc1"
    )
    _ledger_at(repo, T0 + 82 * h).attest(
        "red-alpha", AttestationKind.RESTORED, {"drill": False}, issuer="op", idempotency_key="rs1"
    )
    _ledger_at(repo, T0 + 90 * h).attest(
        "red-alpha",
        AttestationKind.ROLLED_BACK,
        {"drill": True},
        issuer="op",
        idempotency_key="rb2",
    )
    return repo


def test_metrics_from_the_history(tmp_path: Path) -> None:
    repo = _history(tmp_path)
    m = compute_metrics(
        FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0 + timedelta(hours=100)
    )
    assert m.deployments == 2
    assert m.deployment_frequency_per_week == pytest.approx(
        2 / (30 / 7), abs=1e-3
    )  # rounded to 3 places
    assert m.lead_time_commit_to_deploy_hours == 60.0  # median of 48 and 72
    assert m.lead_time_contract_to_deploy_hours == 60.0
    assert m.change_failure_rate == 0.5  # the drill does not count
    assert m.recovery_time_hours == 2.0
    assert (m.conformance.passed, m.conformance.applicable, m.conformance.exceptions) == (10, 10, 0)


def test_metrics_window_excludes_old_deployments(tmp_path: Path) -> None:
    repo = _history(tmp_path)
    m = compute_metrics(
        FileLedger(repo / RECEIPTS_DIR),
        "red-alpha",
        repo,
        now=T0 + timedelta(days=60),
        window_days=30,
    )
    assert m.deployments == 0 and m.deployment_frequency_per_week == 0.0
    assert m.lead_time_commit_to_deploy_hours is None and m.change_failure_rate is None


def test_metrics_without_evidence_are_explicit(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    m = compute_metrics(FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0)
    assert m.deployments == 0 and m.recovery_time_hours is None
    assert m.conformance.passed < m.conformance.applicable  # no contract yet


def test_cli_metrics(tmp_path: Path) -> None:
    repo = _history(tmp_path)
    out = CliRunner().invoke(main, ["metrics", "--repo", str(repo), "--json"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.output)
    assert payload["project"] == "red-alpha" and payload["deployments"] == 2
    assert set(payload["conformance"]) == {"passed", "applicable", "exceptions", "score"}
    out = CliRunner().invoke(main, ["metrics", "--repo", str(repo)])
    assert out.exit_code == 0 and "change failure rate" in out.output


def test_one_rollback_counts_one_failure_for_the_latest_deployment(tmp_path: Path) -> None:
    """Review finding: a rollback belongs to the deployment it undoes, not to every
    deployment of the previous 24 hours."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    head = git(repo, "rev-parse", "HEAD")
    h = timedelta(hours=1)
    _ledger_at(repo, T0 + 1 * h).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:a"},
        issuer="op",
        idempotency_key="d1",
    )
    _ledger_at(repo, T0 + 2 * h).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:b"},
        issuer="op",
        idempotency_key="d2",
    )
    _ledger_at(repo, T0 + 4 * h).attest(
        "red-alpha",
        AttestationKind.ROLLED_BACK,
        {"drill": False},
        issuer="op",
        idempotency_key="rb1",
    )
    m = compute_metrics(FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0 + 10 * h)
    assert m.deployments == 2 and m.change_failure_rate == 0.5


def test_cli_metrics_rejects_a_zero_window(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["metrics", "--repo", str(_history(tmp_path)), "--window", "0"])
    assert out.exit_code == 2 and "window" in out.output


def test_window_scopes_incidents_and_rollbacks_too(tmp_path: Path) -> None:
    """Sweep finding: --window applies to every metric, not only to the deployment count."""
    repo = _history(tmp_path)
    m = compute_metrics(
        FileLedger(repo / RECEIPTS_DIR),
        "red-alpha",
        repo,
        now=T0 + timedelta(days=60),
        window_days=30,
    )
    assert m.recovery_time_hours is None  # the incident at T0+80h is outside the window
