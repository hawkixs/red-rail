"""`rail metrics`: the four DORA metrics and the conformance score, computed by red-rail from
the ledger (brain never computes a delivery metric — ADR-0001). The timestamps are those of
the evidence; a deployment is a release on the manifest's deploy target; a rollback marked
`drill` never counts as a failure, and the drill's own recovery time is reported apart."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from rail import gitrepo
from rail.gates import run_gates
from rail.ledger import AttestationKind, Ledger, Record, RecordKind
from rail.model import RailConfig, try_load_rail_config
from rail.policy import applicable_stages

FAILURE_WINDOW = timedelta(hours=24)


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 2)


@dataclass(frozen=True, slots=True)
class Conformance:
    """Scored against the declared tier (spec §4): `stages` names the denominator, so the
    count is never confused with `rail check --all`'s (red-arena, 2026-09-20)."""

    passed: int
    applicable: int
    exceptions: int
    tier: str = "bootstrap"
    stages: tuple[str, ...] = ()

    @property
    def score(self) -> float | None:
        return round(self.passed / self.applicable, 3) if self.applicable else None


@dataclass(frozen=True, slots=True)
class DeliveryWindow:
    """Delivery metrics from ledger evidence in one time window, without gate evaluation."""

    deployments: int
    deployment_frequency_per_week: float
    lead_time_commit_to_deploy_hours: float | None
    change_failure_rate: float | None
    recovery_time_hours: float | None
    drill_recovery_time_minutes: float | None


@dataclass(frozen=True, slots=True)
class Metrics:
    project: str
    window_days: int
    since: str
    deployments: int
    deployment_frequency_per_week: float
    lead_time_commit_to_deploy_hours: float | None
    lead_time_contract_to_deploy_hours: float | None
    change_failure_rate: float | None
    recovery_time_hours: float | None
    drill_recovery_time_minutes: float | None  # the rollback drill, measured apart
    conformance: Conformance

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["conformance"]["score"] = self.conformance.score
        data["conformance"]["stages"] = list(self.conformance.stages)
        return data


def _mode(record: Record) -> str:
    return str(record.data.get("mode") or "release")


def _digest(record: Record) -> str:
    return str(record.data.get("digest") or "")


def _release_deployments(
    ledger: Ledger, project: str, since: datetime, target: str | None
) -> list[Record]:
    return [
        record
        for record in ledger.list(project, attestation=AttestationKind.DEPLOYED)
        if record.recorded_at >= since
        and _mode(record) == "release"
        and target is not None
        and record.data.get("target") == target
    ]


def compute_metrics(
    ledger: Ledger,
    project: str,
    repo: Path,
    *,
    now: datetime,
    window_days: int = 30,
    ci: bool = False,
) -> Metrics:
    since = now - timedelta(days=window_days)
    cfg = try_load_rail_config(repo)
    delivery = delivery_window(ledger, project, repo, now=now, window_days=window_days)

    contracts = ledger.list(project, kind=RecordKind.CONTRACT)
    target = cfg.deploy.target.value if cfg is not None and cfg.deploy is not None else None
    deployed = _release_deployments(ledger, project, since, target)
    contract_leads = (
        [_hours(d.recorded_at - contracts[0].recorded_at) for d in deployed] if contracts else []
    )

    stages = applicable_stages(repo)
    results = [r for r in run_gates(repo, stages=stages, ci=ci) if not r.skipped]
    conformance = Conformance(
        passed=sum(1 for r in results if r.passed),
        applicable=len(results),
        exceptions=sum(1 for r in results if r.exception),
        tier=(cfg.tier.value if cfg is not None else "bootstrap"),
        stages=tuple(s.value for s in stages),
    )
    return Metrics(
        project=project,
        window_days=window_days,
        since=since.isoformat(),
        deployments=delivery.deployments,
        deployment_frequency_per_week=delivery.deployment_frequency_per_week,
        lead_time_commit_to_deploy_hours=delivery.lead_time_commit_to_deploy_hours,
        lead_time_contract_to_deploy_hours=median(contract_leads) if contract_leads else None,
        change_failure_rate=delivery.change_failure_rate,
        recovery_time_hours=delivery.recovery_time_hours,
        drill_recovery_time_minutes=delivery.drill_recovery_time_minutes,
        conformance=conformance,
    )


def delivery_window(
    ledger: Ledger,
    project: str,
    repo: Path,
    *,
    now: datetime,
    window_days: int,
    config: RailConfig | None = None,
) -> DeliveryWindow:
    """Use the supplied manifest with its evidence, or read the checkout for local metrics."""
    since = now - timedelta(days=window_days)
    cfg = config if config is not None else try_load_rail_config(repo)
    target = cfg.deploy.target.value if cfg is not None and cfg.deploy is not None else None

    def windowed(kind: AttestationKind) -> list[Record]:
        return [r for r in ledger.list(project, attestation=kind) if r.recorded_at >= since]

    # a deployment is a release-mode `deployed` on the manifest's target: a project without a
    # target has none, a rollback or a drill's roll-forward is not a change
    deployed = _release_deployments(ledger, project, since, target)
    rollbacks = [r for r in windowed(AttestationKind.ROLLED_BACK) if not r.data.get("drill")]
    incidents = [r for r in windowed(AttestationKind.INCIDENT_DETECTED) if not r.data.get("drill")]
    restores = [r for r in windowed(AttestationKind.RESTORED) if not r.data.get("drill")]
    drill_incidents = [
        r for r in windowed(AttestationKind.INCIDENT_DETECTED) if r.data.get("drill")
    ]
    drill_restores = [r for r in windowed(AttestationKind.RESTORED) if r.data.get("drill")]
    commit_leads: list[float] = []
    for d in deployed:
        sha = str(d.data.get("sha", ""))
        committed = gitrepo.commit_timestamp(repo, sha) if sha else None
        if committed is not None:
            commit_leads.append(_hours(d.recorded_at - committed))
    # change failure rate: a rollback names the artefact that failed (`from_digest`), else it
    # undoes the newest deployment within 24 h before it; a candidate that never went live
    # is still an attempt
    went_live = {_digest(d) for d in deployed}
    failed: set[str] = set()
    for rollback in rollbacks:
        named = str(rollback.data.get("from_digest") or "")
        if named:
            failed.add(named)
            continue
        before = [d for d in deployed if d.recorded_at < rollback.recorded_at]
        if before and rollback.recorded_at <= before[-1].recorded_at + FAILURE_WINDOW:
            # the artefact digest names the failed change; a deployment record without one
            # is keyed by the record's own digest (Record.digest, always present)
            failed.add(_digest(before[-1]) or f"record:{before[-1].digest}")
    attempts = len(deployed) + len(failed - went_live)
    recoveries: list[float] = []
    for incident in incidents:
        after = [r for r in restores if r.recorded_at > incident.recorded_at]
        if after:
            recoveries.append(_hours(after[0].recorded_at - incident.recorded_at))
    drills: list[float] = []
    for index, incident in enumerate(drill_incidents):
        # a drill's restore is the first one after its incident and before the next drill:
        # an aborted drill never borrows the restore of a later one
        following = drill_incidents[index + 1 :]
        bound = following[0].recorded_at if following else None
        after = [
            r
            for r in drill_restores
            if r.recorded_at > incident.recorded_at and (bound is None or r.recorded_at < bound)
        ]
        if not after:
            continue
        seconds = after[0].data.get("recovery_seconds")
        if isinstance(seconds, int) and not isinstance(seconds, bool):
            drills.append(round(seconds / 60, 2))
        else:
            drills.append(
                round((after[0].recorded_at - incident.recorded_at).total_seconds() / 60, 2)
            )

    return DeliveryWindow(
        deployments=len(deployed),
        deployment_frequency_per_week=round(len(deployed) / (window_days / 7), 3),
        lead_time_commit_to_deploy_hours=median(commit_leads) if commit_leads else None,
        change_failure_rate=round(len(failed) / attempts, 3) if attempts else None,
        recovery_time_hours=median(recoveries) if recoveries else None,
        drill_recovery_time_minutes=median(drills) if drills else None,
    )
