"""`rail metrics`: the four DORA metrics and the conformance score, computed by red-rail from
the ledger (brain never computes a delivery metric — ADR-0001). The timestamps are those of
the evidence; a rollback marked `drill` never counts as a failure."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from rail import gitrepo
from rail.gates import run_gates
from rail.ledger import AttestationKind, Ledger, RecordKind
from rail.policy import applicable_stages

FAILURE_WINDOW = timedelta(hours=24)


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 2)


@dataclass(frozen=True, slots=True)
class Conformance:
    passed: int
    applicable: int
    exceptions: int

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
    conformance: Conformance

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["conformance"]["score"] = self.conformance.score
        return data


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
    deployed = [
        r
        for r in ledger.list(project, attestation=AttestationKind.DEPLOYED)
        if r.recorded_at >= since
    ]
    rollbacks = [
        r
        for r in ledger.list(project, attestation=AttestationKind.ROLLED_BACK)
        if not r.data.get("drill")
    ]
    incidents = ledger.list(project, attestation=AttestationKind.INCIDENT_DETECTED)
    restores = [
        r
        for r in ledger.list(project, attestation=AttestationKind.RESTORED)
        if not r.data.get("drill")
    ]
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
    failures = sum(
        1
        for d in deployed
        if any(d.recorded_at < r.recorded_at <= d.recorded_at + FAILURE_WINDOW for r in rollbacks)
    )
    recoveries: list[float] = []
    for incident in incidents:
        after = [r for r in restores if r.recorded_at > incident.recorded_at]
        if after:
            recoveries.append(_hours(after[0].recorded_at - incident.recorded_at))

    results = [r for r in run_gates(repo, stages=applicable_stages(repo), ci=ci) if not r.skipped]
    conformance = Conformance(
        passed=sum(1 for r in results if r.passed),
        applicable=len(results),
        exceptions=sum(1 for r in results if r.exception),
    )
    return Metrics(
        project=project,
        window_days=window_days,
        since=since.isoformat(),
        deployments=len(deployed),
        deployment_frequency_per_week=round(len(deployed) / (window_days / 7), 3),
        lead_time_commit_to_deploy_hours=median(commit_leads) if commit_leads else None,
        lead_time_contract_to_deploy_hours=median(contract_leads) if contract_leads else None,
        change_failure_rate=round(failures / len(deployed), 3) if deployed else None,
        recovery_time_hours=median(recoveries) if recoveries else None,
        conformance=conformance,
    )
