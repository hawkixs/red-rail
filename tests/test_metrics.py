"""DORA from evidence timestamps, nothing else: no separate instrumentation."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail import monitor
from rail.cli import main
from rail.gates import build as build_gates
from rail.ledger import RECEIPTS_DIR, AttestationKind, Contract, Deliverable
from rail.ledger.file import FileLedger
from rail.metrics import compute_metrics, delivery_window
from tests.helpers import conforming_tree, git, write_roster

T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # == the fixture commit timestamp


@pytest.fixture(autouse=True)
def _no_real_gitleaks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (0, "no leaks found"))


@pytest.fixture(autouse=True)
def _no_real_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The conformance score runs the prod gates; `observe.visible` must never reach the mesh."""

    def unreachable(base_url: str, agent: str, **kwargs: object) -> monitor.AgentView:
        raise monitor.MonitorError("no red-monitor in the tests")

    monkeypatch.setattr(monitor, "read_agent", unreachable)


def _ledger_at(repo: Path, when: datetime) -> FileLedger:
    return FileLedger(repo / RECEIPTS_DIR, clock=lambda: when)


def _history(tmp_path: Path) -> Path:
    """contract T0; deployed T0+48h then rolled back (real) at +49h; deployed again at +72h;
    incident at +80h restored at +82h; a drill rollback at +90h that must not count."""
    repo = conforming_tree(tmp_path, "red-alpha", "prod")
    write_roster(tmp_path, ["red-alpha"])
    head = git(repo, "rev-parse", "HEAD")
    h = timedelta(hours=1)
    _ledger_at(repo, T0).contract_set(
        "red-alpha",
        Contract(
            objective="x",
            deliverables=[
                Deliverable(
                    key="m",
                    repository="hawkixs/red-alpha",
                    no_checks_reason="fixture: no check declared",
                )
            ],
        ),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c1",
    )
    _ledger_at(repo, T0 + 48 * h).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:a", "target": "vps-traefik"},
        issuer="op",
        idempotency_key="d1",
    )
    _ledger_at(repo, T0 + 49 * h).attest(
        "red-alpha",
        AttestationKind.ROLLED_BACK,
        {"drill": False, "target": "vps-traefik"},
        issuer="op",
        idempotency_key="rb1",
    )
    _ledger_at(repo, T0 + 72 * h).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:b", "target": "vps-traefik"},
        issuer="op",
        idempotency_key="d2",
    )
    _ledger_at(repo, T0 + 80 * h).attest(
        "red-alpha",
        AttestationKind.INCIDENT_DETECTED,
        {"target": "vps-traefik"},
        issuer="op",
        idempotency_key="inc1",
    )
    _ledger_at(repo, T0 + 82 * h).attest(
        "red-alpha",
        AttestationKind.RESTORED,
        {"drill": False, "target": "vps-traefik"},
        issuer="op",
        idempotency_key="rs1",
    )
    _ledger_at(repo, T0 + 90 * h).attest(
        "red-alpha",
        AttestationKind.ROLLED_BACK,
        {"drill": True, "target": "vps-traefik"},
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
    # one more applicable and passing gate than before `review.carry_forward` joined the
    # ledger-scoped evidence gates: this history has no carry-forward recorded.
    assert (m.conformance.passed, m.conformance.applicable, m.conformance.exceptions) == (17, 24, 0)


def test_metrics_history_dict_is_stable(tmp_path: Path) -> None:
    repo = _history(tmp_path)
    metrics = compute_metrics(
        FileLedger(repo / RECEIPTS_DIR),
        "red-alpha",
        repo,
        now=T0 + timedelta(hours=100),
    )
    assert metrics.to_dict() == {
        "project": "red-alpha",
        "window_days": 30,
        "since": "2026-08-20T12:00:00+00:00",
        "deployments": 2,
        "deployment_frequency_per_week": 0.467,
        "lead_time_commit_to_deploy_hours": 60.0,
        "lead_time_contract_to_deploy_hours": 60.0,
        "change_failure_rate": 0.5,
        "recovery_time_hours": 2.0,
        "drill_recovery_time_minutes": None,
        "conformance": {
            "passed": 17,
            "applicable": 24,
            "exceptions": 0,
            "tier": "prod",
            "stages": [
                "hygiene",
                "intent",
                "design",
                "plan",
                "build",
                "review",
                "integrate",
                "release",
                "deploy",
                "observe",
                "learn",
            ],
            "score": 0.708,
        },
    }


def test_delivery_window_matches_metrics_delivery_values(tmp_path: Path) -> None:
    repo = _history(tmp_path)
    ledger = FileLedger(repo / RECEIPTS_DIR)
    now = T0 + timedelta(hours=100)
    metrics = compute_metrics(ledger, "red-alpha", repo, now=now)
    window = delivery_window(ledger, "red-alpha", repo, now=now, window_days=30)
    assert (
        window.deployments,
        window.deployment_frequency_per_week,
        window.lead_time_commit_to_deploy_hours,
        window.change_failure_rate,
        window.recovery_time_hours,
        window.drill_recovery_time_minutes,
    ) == (
        metrics.deployments,
        metrics.deployment_frequency_per_week,
        metrics.lead_time_commit_to_deploy_hours,
        metrics.change_failure_rate,
        metrics.recovery_time_hours,
        metrics.drill_recovery_time_minutes,
    )


def test_delivery_window_does_not_run_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rail.metrics as metrics_module

    repo = _history(tmp_path)
    monkeypatch.setattr(
        metrics_module, "run_gates", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    result = delivery_window(
        FileLedger(repo / RECEIPTS_DIR),
        "red-alpha",
        repo,
        now=T0 + timedelta(hours=100),
        window_days=30,
    )
    assert result.deployments == 2


def test_delivery_window_counts_named_rollback_for_one_failed_deployment(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "prod")
    write_roster(tmp_path, ["red-alpha"])
    head = git(repo, "rev-parse", "HEAD")
    h = timedelta(hours=1)
    for offset, digest, key in ((1, "sha256:a", "d1"), (2, "sha256:a", "d2")):
        _ledger_at(repo, T0 + offset * h).attest(
            "red-alpha",
            AttestationKind.DEPLOYED,
            {"sha": head, "digest": digest, "target": "vps-traefik"},
            issuer="op",
            idempotency_key=key,
        )
    _ledger_at(repo, T0 + 3 * h).attest(
        "red-alpha",
        AttestationKind.ROLLED_BACK,
        {"drill": False, "from_digest": "sha256:a"},
        issuer="op",
        idempotency_key="rb",
    )
    result = delivery_window(
        FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0 + 4 * h, window_days=30
    )
    assert result.deployments == 2
    assert result.change_failure_rate == 0.5


def test_delivery_window_counts_named_rollback_of_old_artifact(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "prod")
    write_roster(tmp_path, ["red-alpha"])
    _ledger_at(repo, T0 + 40 * timedelta(days=1)).attest(
        "red-alpha",
        AttestationKind.ROLLED_BACK,
        {"drill": False, "from_digest": "sha256:old"},
        issuer="op",
        idempotency_key="rb-old",
    )
    result = delivery_window(
        FileLedger(repo / RECEIPTS_DIR),
        "red-alpha",
        repo,
        now=T0 + 41 * timedelta(days=1),
        window_days=30,
    )
    assert result.deployments == 0 and result.change_failure_rate == 1.0


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
    assert set(payload["conformance"]) == {
        "passed",
        "applicable",
        "exceptions",
        "score",
        "tier",
        "stages",
    }
    assert payload["conformance"]["tier"] == "prod"  # the history fixture declares prod
    assert payload["conformance"]["stages"][:3] == ["hygiene", "intent", "design"]
    assert len(payload["conformance"]["stages"]) == 11
    out = CliRunner().invoke(main, ["metrics", "--repo", str(repo)])
    assert out.exit_code == 0 and "change failure rate" in out.output


def test_one_rollback_counts_one_failure_for_the_latest_deployment(tmp_path: Path) -> None:
    """Review finding: a rollback belongs to the deployment it undoes, not to every
    deployment of the previous 24 hours."""
    repo = conforming_tree(tmp_path, "red-alpha", "prod")
    head = git(repo, "rev-parse", "HEAD")
    h = timedelta(hours=1)
    _ledger_at(repo, T0 + 1 * h).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:a", "target": "vps-traefik"},
        issuer="op",
        idempotency_key="d1",
    )
    _ledger_at(repo, T0 + 2 * h).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:b", "target": "vps-traefik"},
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


def test_only_release_mode_deployments_on_the_declared_target_count(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "prod")
    write_roster(tmp_path, ["red-alpha"])
    head = git(repo, "rev-parse", "HEAD")
    h = timedelta(hours=1)
    for offset, data, key in (
        (1, {"sha": head, "digest": "sha256:a", "target": "vps-traefik"}, "d1"),
        (2, {"sha": head, "digest": "sha256:b", "target": "proof"}, "d2"),
        (3, {"sha": head, "digest": "sha256:a", "target": "vps-traefik", "mode": "rollback"}, "d3"),
        (4, {"sha": head, "digest": "sha256:b", "target": "vps-traefik", "mode": "drill"}, "d4"),
    ):
        _ledger_at(repo, T0 + offset * h).attest(
            "red-alpha", AttestationKind.DEPLOYED, data, issuer="op", idempotency_key=key
        )
    m = compute_metrics(FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0 + 10 * h)
    assert m.deployments == 1


def test_a_project_without_a_deploy_target_has_no_deployments(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"])
    head = git(repo, "rev-parse", "HEAD")
    _ledger_at(repo, T0).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:proof", "target": "proof"},
        issuer="op",
        idempotency_key="canary",
    )
    m = compute_metrics(
        FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0 + timedelta(days=1)
    )
    assert m.deployments == 0 and m.change_failure_rate is None


def test_failures_are_attributed_by_from_digest_and_failed_candidates_are_attempts(
    tmp_path: Path,
) -> None:
    """One good deployment (a), one candidate (b) that never went live and was rolled back
    automatically: 1 failure over 2 attempts."""
    repo = conforming_tree(tmp_path, "red-alpha", "prod")
    write_roster(tmp_path, ["red-alpha"])
    head = git(repo, "rev-parse", "HEAD")
    h = timedelta(hours=1)
    _ledger_at(repo, T0 + 1 * h).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:a", "target": "vps-traefik"},
        issuer="op",
        idempotency_key="d1",
    )
    _ledger_at(repo, T0 + 2 * h).attest(
        "red-alpha",
        AttestationKind.ROLLED_BACK,
        {"drill": False, "automatic": True, "from_digest": "sha256:b", "to_digest": "sha256:a"},
        issuer="op",
        idempotency_key="rb1",
    )
    _ledger_at(repo, T0 + 2 * h + timedelta(minutes=1)).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:a", "target": "vps-traefik", "mode": "rollback"},
        issuer="op",
        idempotency_key="d2",
    )
    m = compute_metrics(FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0 + 10 * h)
    assert m.deployments == 1 and m.change_failure_rate == 0.5


def test_drill_recovery_time_is_measured_apart(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "prod")
    write_roster(tmp_path, ["red-alpha"])
    head = git(repo, "rev-parse", "HEAD")
    h = timedelta(hours=1)
    _ledger_at(repo, T0 + 1 * h).attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"sha": head, "digest": "sha256:b", "target": "vps-traefik"},
        issuer="op",
        idempotency_key="d1",
    )
    _ledger_at(repo, T0 + 2 * h).attest(
        "red-alpha",
        AttestationKind.INCIDENT_DETECTED,
        {"drill": True, "digest": "sha256:b", "target": "vps-traefik"},
        issuer="op",
        idempotency_key="inc1",
    )
    _ledger_at(repo, T0 + 2 * h + timedelta(minutes=3)).attest(
        "red-alpha",
        AttestationKind.RESTORED,
        {"drill": True, "digest": "sha256:a", "recovery_seconds": 150, "target": "vps-traefik"},
        issuer="op",
        idempotency_key="rs1",
    )
    m = compute_metrics(FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0 + 10 * h)
    assert m.drill_recovery_time_minutes == 2.5
    assert m.recovery_time_hours is None and m.change_failure_rate == 0.0
    out = CliRunner().invoke(main, ["metrics", "--repo", str(repo), "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["drill_recovery_time_minutes"] == 2.5


def test_metrics_json_answers_json_on_an_error(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["metrics", "--repo", str(tmp_path), "--json"])
    assert out.exit_code == 1
    assert json.loads(out.output)["error"].startswith("rail.yaml is missing")


def test_drill_recovery_falls_back_to_the_timestamps(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "prod")
    write_roster(tmp_path, ["red-alpha"])
    h = timedelta(hours=1)
    _ledger_at(repo, T0 + 2 * h).attest(
        "red-alpha",
        AttestationKind.INCIDENT_DETECTED,
        {"drill": True, "digest": "sha256:b", "target": "vps-traefik"},
        issuer="op",
        idempotency_key="inc1",
    )
    _ledger_at(repo, T0 + 2 * h + timedelta(minutes=4)).attest(
        "red-alpha",
        AttestationKind.RESTORED,
        {"drill": True, "digest": "sha256:a", "target": "vps-traefik"},  # no recovery_seconds
        issuer="op",
        idempotency_key="rs1",
    )
    m = compute_metrics(FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0 + 10 * h)
    assert m.drill_recovery_time_minutes == 4.0


def test_an_aborted_drill_never_borrows_the_next_drills_restore(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "prod")
    write_roster(tmp_path, ["red-alpha"])
    h = timedelta(hours=1)
    for offset, kind, key in (
        (1, AttestationKind.INCIDENT_DETECTED, "inc-aborted"),
        (3, AttestationKind.INCIDENT_DETECTED, "inc-second"),
    ):
        _ledger_at(repo, T0 + offset * h).attest(
            "red-alpha",
            kind,
            {"drill": True, "target": "vps-traefik"},
            issuer="op",
            idempotency_key=key,
        )
    _ledger_at(repo, T0 + 3 * h + timedelta(minutes=2)).attest(
        "red-alpha",
        AttestationKind.RESTORED,
        {"drill": True, "target": "vps-traefik", "recovery_seconds": 120},
        issuer="op",
        idempotency_key="rs-second",
    )
    m = compute_metrics(FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0 + 10 * h)
    assert m.drill_recovery_time_minutes == 2.0  # one pair, not two hours from the aborted one
