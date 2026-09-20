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
from rail.model import try_load_rail_config
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
    target = cfg.deploy.target.value if cfg is not None and cfg.deploy is not None else None

    def windowed(kind: AttestationKind) -> list[Record]:
        return [r for r in ledger.list(project, attestation=kind) if r.recorded_at >= since]

    # a deployment is a release-mode `deployed` on the manifest's target: a project without a
    # target has none, a rollback or a drill's roll-forward is not a change
    deployed = [
        r
        for r in windowed(AttestationKind.DEPLOYED)
        if _mode(r) == "release" and target is not None and r.data.get("target") == target
    ]
    rollbacks = [r for r in windowed(AttestationKind.ROLLED_BACK) if not r.data.get("drill")]
    incidents = [r for r in windowed(AttestationKind.INCIDENT_DETECTED) if not r.data.get("drill")]
    restores = [r for r in windowed(AttestationKind.RESTORED) if not r.data.get("drill")]
    drill_incidents = [
        r for r in windowed(AttestationKind.INCIDENT_DETECTED) if r.data.get("drill")
    ]
    drill_restores = [r for r in windowed(AttestationKind.RESTORED) if r.data.get("drill")]
    # the contract is the start of the lead time, however old it is: never windowed
    contracts = ledger.list(project, kind=RecordKind.CONTRACT)

    commit_leads: list[float] = []
    for d in deployed:
        sha = str(d.data.get("sha", ""))
        committed = gitrepo.commit_timestamp(repo, sha) if sha else None
        if committed is not None:
            commit_leads.append(_hours(d.recorded_at - committed))
    contract_leads = (
        [_hours(d.recorded_at - contracts[0].recorded_at) for d in deployed] if contracts else []
    )
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
        deployments=len(deployed),
        deployment_frequency_per_week=round(len(deployed) / (window_days / 7), 3),
        lead_time_commit_to_deploy_hours=median(commit_leads) if commit_leads else None,
        lead_time_contract_to_deploy_hours=median(contract_leads) if contract_leads else None,
        change_failure_rate=round(len(failed) / attempts, 3) if attempts else None,
        recovery_time_hours=median(recoveries) if recoveries else None,
        drill_recovery_time_minutes=median(drills) if drills else None,
        conformance=conformance,
    )
