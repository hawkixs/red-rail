"""Delivery v2 counts artefacts and attributes evidence over the whole ledger."""

from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from rail import gitrepo, metrics
from rail.ledger import AttestationKind, Record
from rail.ledger.file import FileLedger
from rail.metrics_contract import (
    Answer,
    DeliveryBlockV2,
    DeliveryProjectV2,
    DeliveryWindowV2Model,
    encode,
)
from rail.model import RailConfig
from tests.helpers import GIT_ENV

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)
TARGET = "private-compose"
CONFIG = RailConfig.model_validate(
    {
        "rail": 1,
        "project": "red-probe",
        "brain_key": "red-probe",
        "tier": "prod",
        "stack": "python",
        "deploy": {"target": TARGET, "healthcheck": "http://127.0.0.1/health"},
    }
)


def emit(repo: Path, kind: str, at: datetime, **data: Any) -> Record:
    ledger = FileLedger(repo / "docs/receipts", clock=lambda: at)
    return ledger.attest(
        "red-probe",
        AttestationKind(kind),
        data,
        issuer="operator",
        idempotency_key=f"{kind}:{at.isoformat()}:{data}",
    )


def deployed(repo: Path, at: datetime, digest: str, **data: Any) -> Record:
    return emit(repo, "deployed", at, digest=digest, target=TARGET, **data)


def window(repo: Path, days: int = 7) -> metrics.DeliveryWindowV2:
    return metrics.delivery_window_v2(
        FileLedger(repo / "docs/receipts"),
        "red-probe",
        repo,
        now=NOW,
        window_days=days,
        config=CONFIG,
    )


def test_changes_are_first_deployments_even_after_another_digest_went_live(tmp_path: Path) -> None:
    deployed(tmp_path, NOW - timedelta(days=20), "a")
    deployed(tmp_path, NOW - timedelta(days=3), "b")
    deployed(tmp_path, NOW - timedelta(days=2), "a")
    deployed(tmp_path, NOW - timedelta(days=1), "b")
    result = window(tmp_path)
    assert (result.changes, result.redeployments) == (1, 2)
    assert result.deployment_frequency_per_week == 1.0
    result30 = window(tmp_path, 30)
    assert (result30.changes, result30.redeployments) == (2, 2)
    assert result30.deployment_frequency_per_week == 0.467
    with pytest.raises(FrozenInstanceError):
        result.changes = 99  # type: ignore[misc]


def test_target_and_release_mode_are_required(tmp_path: Path) -> None:
    at = NOW - timedelta(days=1)
    deployed(tmp_path, at, "a")
    deployed(tmp_path, at, "b", mode="rollback")
    deployed(tmp_path, at, "c", mode="drill")
    emit(tmp_path, "deployed", at, digest="d", target="vps-traefik")
    assert window(tmp_path).changes == 1


def git_at(repo: Path, at: datetime, *args: str) -> str:
    env = {**GIT_ENV, "GIT_COMMITTER_DATE": at.isoformat()}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


@pytest.fixture
def commits(tmp_path: Path) -> tuple[Path, list[str]]:
    git_at(tmp_path, NOW, "init", "-q", "-b", "main")
    git_at(tmp_path, NOW, "config", "commit.gpgsign", "false")
    shas = []
    for index in range(3):
        at = NOW - timedelta(days=5 - index)
        git_at(tmp_path, at, "commit", "--allow-empty", "-m", f"Change {index}")
        shas.append(git_at(tmp_path, at, "rev-parse", "HEAD"))
    return tmp_path, shas


def test_lead_starts_at_oldest_included_committer_date_and_release_delay_is_separate(
    commits: tuple[Path, list[str]],
) -> None:
    repo, shas = commits
    deployed(repo, NOW - timedelta(days=4), "a", sha=shas[0])
    emit(repo, "released", NOW - timedelta(days=2), digest="b", sha=shas[2])
    deployed(repo, NOW - timedelta(days=1), "b", sha=shas[2])
    result = window(repo)
    assert result.lead_time_for_changes_hours == 72.0
    assert result.release_to_deploy_hours == 24.0


def test_first_change_has_no_lead_time(commits: tuple[Path, list[str]]) -> None:
    repo, shas = commits
    deployed(repo, NOW - timedelta(days=1), "a", sha=shas[2])
    assert window(repo).lead_time_for_changes_hours is None


@pytest.mark.parametrize("skew_seconds", [1, 3600])
def test_negative_lead_has_no_sample_and_publication_validates(
    commits: tuple[Path, list[str]], skew_seconds: int
) -> None:
    repo, shas = commits
    deployed(repo, NOW - timedelta(days=5), "a", sha=shas[0])
    at = NOW - timedelta(days=4, seconds=skew_seconds)
    emit(repo, "released", at - timedelta(hours=1), digest="b")
    deployed(repo, at, "b", sha=shas[2])
    windows: dict[int, DeliveryWindowV2Model] = {}
    for days in (7, 30):
        result = window(repo, days)
        assert result.changes == 2
        assert result.lead_time_for_changes_hours is None
        assert result.release_to_deploy_hours == 1.0
        windows[days] = DeliveryWindowV2Model.model_validate(asdict(result))
    answer = Answer(
        delivery_v2=DeliveryBlockV2(
            version="rail-delivery/v2",
            generated_at=NOW,
            windows_days=(7, 30),
            projects=(
                DeliveryProjectV2(
                    project="red-probe",
                    status="measured",
                    target=TARGET,
                    d7=windows[7],
                    d30=windows[30],
                ),
            ),
        )
    )
    assert Answer.model_validate_json(encode(answer)) == answer


def test_negative_lead_is_excluded_from_median(commits: tuple[Path, list[str]]) -> None:
    repo, shas = commits
    deployed(repo, NOW - timedelta(days=5), "a", sha=shas[0])
    deployed(repo, NOW - timedelta(days=4, hours=1), "b", sha=shas[1])
    deployed(repo, NOW - timedelta(days=1), "c", sha=shas[2])
    assert window(repo).lead_time_for_changes_hours == 48.0


def test_zero_lead_is_a_sample(commits: tuple[Path, list[str]]) -> None:
    repo, shas = commits
    deployed(repo, NOW - timedelta(days=5), "a", sha=shas[0])
    deployed(repo, NOW - timedelta(days=4), "b", sha=shas[2])
    assert window(repo).lead_time_for_changes_hours == 0.0


@pytest.mark.parametrize("release_hours", [-1, 0, 1])
def test_release_delay_uses_only_releases_at_or_before_deployment(
    tmp_path: Path, release_hours: int
) -> None:
    at = NOW - timedelta(days=1)
    emit(tmp_path, "released", at + timedelta(hours=release_hours), digest="a")
    deployed(tmp_path, at, "a")
    expected = float(-release_hours) if release_hours <= 0 else None
    assert window(tmp_path).release_to_deploy_hours == expected


@pytest.mark.parametrize("failure", ["unreadable", "empty", "not_ancestor"])
def test_unknown_commit_range_has_no_lead_time(
    commits: tuple[Path, list[str]], monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    repo, shas = commits
    deployed(repo, NOW - timedelta(days=4), "a", sha=shas[0])
    deployed(repo, NOW - timedelta(days=1), "b", sha=shas[2])
    if failure == "not_ancestor":
        monkeypatch.setattr(gitrepo, "is_ancestor", lambda *args: False)
    else:
        monkeypatch.setattr(
            gitrepo, "commit_times_between", lambda *args: None if failure == "unreadable" else []
        )
    assert window(repo).lead_time_for_changes_hours is None


def test_previous_change_outside_window_is_used_and_redeployment_is_not_a_change(
    commits: tuple[Path, list[str]],
) -> None:
    repo, shas = commits
    deployed(repo, NOW - timedelta(days=10), "a", sha=shas[0])
    deployed(repo, NOW - timedelta(days=2), "a", sha=shas[1])
    deployed(repo, NOW - timedelta(days=1), "b", sha=shas[2])
    assert window(repo).lead_time_for_changes_hours == 72.0


@pytest.mark.parametrize(
    "kind,key", [("incident_detected", "digest"), ("rolled_back", "from_digest")]
)
def test_named_failure_before_first_deployment_fails_the_later_change(
    tmp_path: Path, kind: str, key: str
) -> None:
    emit(tmp_path, kind, NOW - timedelta(days=9), **{key: "a"})
    deployed(tmp_path, NOW - timedelta(days=1), "a")
    result = window(tmp_path)
    assert result.changes == result.failed_changes == 1
    assert result.change_failure_rate == 1.0


def test_failure_after_window_still_fails_that_windows_change(tmp_path: Path) -> None:
    deployed(tmp_path, NOW - timedelta(days=20), "a")
    emit(tmp_path, "incident_detected", NOW - timedelta(days=1), digest="a")
    assert window(tmp_path).failed_changes == 0
    assert window(tmp_path).change_failure_rate is None
    assert window(tmp_path, 30).change_failure_rate == 1.0


@pytest.mark.parametrize("kind", ["incident_detected", "rolled_back"])
@pytest.mark.parametrize("hours,expected", [(24, 0.5), (25, 0.0)])
def test_digestless_failure_is_attributed_to_only_newest_release_deployment_within_24h(
    tmp_path: Path, kind: str, hours: int, expected: float
) -> None:
    at = NOW - timedelta(days=3)
    deployed(tmp_path, at - timedelta(days=1), "a")
    deployed(tmp_path, at, "b")
    emit(tmp_path, kind, at + timedelta(hours=hours))
    assert window(tmp_path).change_failure_rate == expected


def test_digestless_failure_during_redeployment_fails_original_change_only(tmp_path: Path) -> None:
    deployed(tmp_path, NOW - timedelta(days=20), "a")
    deployed(tmp_path, NOW - timedelta(days=2), "b")
    deployed(tmp_path, NOW - timedelta(hours=2), "a")
    emit(tmp_path, "incident_detected", NOW - timedelta(hours=1))
    assert window(tmp_path).change_failure_rate == 0.0
    assert window(tmp_path, 30).change_failure_rate == 0.5


def test_failed_candidates_are_attempts_counted_once_by_first_failure(tmp_path: Path) -> None:
    deployed(tmp_path, NOW - timedelta(days=2), "a")
    emit(tmp_path, "incident_detected", NOW - timedelta(days=1), digest="b")
    emit(tmp_path, "rolled_back", NOW - timedelta(hours=3), from_digest="b")
    emit(tmp_path, "incident_detected", NOW - timedelta(days=10), digest="old")
    emit(tmp_path, "rolled_back", NOW - timedelta(hours=2), from_digest="old")
    result = window(tmp_path)
    assert result.changes == result.failed_changes == 1
    assert result.change_failure_rate == 0.5
    assert window(tmp_path, 30).change_failure_rate == 0.667


def test_candidate_alone_has_failure_rate_and_empty_ledger_has_none(tmp_path: Path) -> None:
    assert window(tmp_path).change_failure_rate is None
    emit(tmp_path, "incident_detected", NOW - timedelta(hours=1), digest="candidate")
    assert window(tmp_path).change_failure_rate == 1.0
    assert window(tmp_path).failed_changes == 1


@pytest.mark.parametrize("event_minutes,expected", [(1.18, 1.18), (-1, 60.0), (61, 60.0)])
def test_named_restore_uses_bounded_event_time_and_counts_open_incidents(
    tmp_path: Path, event_minutes: float, expected: float
) -> None:
    at = NOW - timedelta(days=1)
    emit(tmp_path, "incident_detected", at - timedelta(minutes=1))
    incident = emit(tmp_path, "incident_detected", at)
    emit(
        tmp_path,
        "restored",
        at + timedelta(hours=1),
        incident=incident.digest,
        restored_at=(at + timedelta(minutes=event_minutes)).isoformat(),
    )
    result = window(tmp_path)
    assert result.time_to_restore_minutes == expected
    assert result.open_incidents == 1


def test_matching_uses_old_incidents_and_counts_only_windows_incidents(tmp_path: Path) -> None:
    emit(tmp_path, "incident_detected", NOW - timedelta(days=10))
    emit(tmp_path, "incident_detected", NOW - timedelta(days=1))
    emit(tmp_path, "restored", NOW - timedelta(hours=12))
    result = window(tmp_path)
    assert result.time_to_restore_minutes is None
    assert result.open_incidents == 1


def test_future_evidence_does_not_close_incidents_at_publication_time(tmp_path: Path) -> None:
    deployed(tmp_path, NOW + timedelta(hours=1), "future")
    incident = emit(tmp_path, "incident_detected", NOW - timedelta(hours=1))
    emit(tmp_path, "restored", NOW + timedelta(hours=1), incident=incident.digest)
    result = window(tmp_path)
    assert result.changes == 0
    assert result.open_incidents == 1
    assert result.time_to_restore_minutes is None


def test_drill_recovery_is_separate_and_keeps_v1_rule(tmp_path: Path) -> None:
    at = NOW - timedelta(days=1)
    emit(tmp_path, "incident_detected", at, drill=True, digest="a")
    emit(tmp_path, "restored", at + timedelta(minutes=3), drill=True, recovery_seconds=150)
    emit(tmp_path, "rolled_back", at, drill=True, from_digest="a")
    result = window(tmp_path)
    assert result.drill_recovery_time_minutes == 2.5
    assert result.change_failure_rate is None
    assert result.time_to_restore_minutes is None
    assert result.open_incidents == 0
