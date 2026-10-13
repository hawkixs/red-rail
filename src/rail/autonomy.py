"""Autonomy observations stay unrounded so totals preserve the D4 population."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from statistics import median
from typing import Literal

from rail.ledger import AttestationKind, Record

SINCE = datetime(2026, 10, 4, tzinfo=UTC)
_CORE = frozenset(
    {
        AttestationKind.RELEASED,
        AttestationKind.DEPLOYED,
        AttestationKind.ROLLED_BACK,
        AttestationKind.RESTORED,
    }
)
_GESTURES = _CORE | {AttestationKind.REVIEW_RULING}


@dataclass(frozen=True)
class MergedPull:
    """Readiness is the last ready-for-review event, or creation for an undrafted pull."""

    number: int
    merged_at: datetime
    ready_at: datetime


@dataclass(frozen=True)
class ProjectFacts:
    """A project's ledger can span repositories, so review facts also need the slug."""

    project: str
    repository: str
    records: list[Record]
    merged: list[MergedPull]
    reverts: list[datetime]


@dataclass(frozen=True)
class Samples:
    """Raw window observations let totals recompute ratios and distributions once."""

    releases: int = 0
    operator_gestures: int = 0
    operator_core: int = 0
    agent_core: int = 0
    merged_prs: int = 0
    reviewed_prs: int = 0
    rulings: int = 0
    agent_rulings: int = 0
    awaiting_ruling_passes: int = 0
    rollbacks: int = 0
    reverts: int = 0
    incidents: int = 0
    rounds: tuple[int, ...] = ()
    first_pass: tuple[bool, ...] = ()
    ready_to_merged_hours: tuple[float, ...] = ()


def issuer_class(issuer: str) -> Literal["operator", "agent", "other"]:
    """Only explicit operator and agent labels count; services do not imply either."""
    if issuer == "operator":
        return "operator"
    if issuer.startswith("agent:"):
        return "agent"
    return "other"


def window_start(now: datetime, days: int) -> datetime:
    """Actor coverage begins at SINCE, identically for a project and for the total."""
    return max(now - timedelta(days=days), SINCE)


def window_samples(facts: ProjectFacts, *, now: datetime, days: int) -> Samples:
    """Select merges by window, then keep their review history since actor coverage began."""
    start = window_start(now, days)
    records = [record for record in facts.records if record.recorded_at >= start]
    merged = [pull for pull in facts.merged if pull.merged_at >= start]
    reviews: dict[int, list[Record]] = {}
    rulings: dict[int, list[Record]] = {}
    for record in sorted(facts.records, key=lambda record: record.recorded_at):
        if record.data.get("repository") != facts.repository:
            continue
        pr = record.data.get("pr")
        if record.attestation == AttestationKind.REVIEW_VERDICT:
            reviews.setdefault(pr, []).append(record)
        elif record.attestation == AttestationKind.REVIEW_RULING and record.recorded_at >= SINCE:
            rulings.setdefault(pr, []).append(record)

    rounds: list[int] = []
    first_pass: list[bool] = []
    selected_rulings: list[Record] = []
    awaiting = 0
    for pull in merged:
        history = reviews.get(pull.number, [])
        if not history or history[0].recorded_at < SINCE:
            continue
        judged = [
            record
            for record in history
            if record.data.get("round") in (1, 2, 3, "closure") and record.data.get("providers")
        ]
        rounds.append(len(judged))
        if judged:
            first_pass.append(judged[0].data.get("verdict") == "approve")
        awaiting += sum(record.data.get("round") == "awaiting_ruling" for record in history)
        selected_rulings.extend(rulings.get(pull.number, []))

    return Samples(
        releases=sum(record.attestation == AttestationKind.RELEASED for record in records),
        operator_gestures=sum(
            record.attestation in _GESTURES and issuer_class(record.issuer) == "operator"
            for record in records
        ),
        operator_core=sum(
            record.attestation in _CORE and issuer_class(record.issuer) == "operator"
            for record in records
        ),
        agent_core=sum(
            record.attestation in _CORE and issuer_class(record.issuer) == "agent"
            for record in records
        ),
        merged_prs=len(merged),
        reviewed_prs=len(rounds),
        rulings=len(selected_rulings),
        agent_rulings=sum(issuer_class(record.issuer) == "agent" for record in selected_rulings),
        awaiting_ruling_passes=awaiting,
        rollbacks=sum(
            record.attestation == AttestationKind.ROLLED_BACK and not record.data.get("drill")
            for record in records
        ),
        reverts=sum(at >= start for at in facts.reverts),
        incidents=sum(
            record.attestation == AttestationKind.INCIDENT_DETECTED and not record.data.get("drill")
            for record in records
        ),
        rounds=tuple(rounds),
        first_pass=tuple(first_pass),
        ready_to_merged_hours=tuple(
            (pull.merged_at - pull.ready_at).total_seconds() / 3600 for pull in merged
        ),
    )


def merge(*samples: Samples) -> Samples:
    """Counts sum and observations concatenate; averaging published ratios loses weight."""
    return Samples(
        releases=sum(sample.releases for sample in samples),
        operator_gestures=sum(sample.operator_gestures for sample in samples),
        operator_core=sum(sample.operator_core for sample in samples),
        agent_core=sum(sample.agent_core for sample in samples),
        merged_prs=sum(sample.merged_prs for sample in samples),
        reviewed_prs=sum(sample.reviewed_prs for sample in samples),
        rulings=sum(sample.rulings for sample in samples),
        agent_rulings=sum(sample.agent_rulings for sample in samples),
        awaiting_ruling_passes=sum(sample.awaiting_ruling_passes for sample in samples),
        rollbacks=sum(sample.rollbacks for sample in samples),
        reverts=sum(sample.reverts for sample in samples),
        incidents=sum(sample.incidents for sample in samples),
        rounds=tuple(value for sample in samples for value in sample.rounds),
        first_pass=tuple(value for sample in samples for value in sample.first_pass),
        ready_to_merged_hours=tuple(
            value for sample in samples for value in sample.ready_to_merged_hours
        ),
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    """Round only at publication; an empty denominator is unknown rather than zero."""
    return round(numerator / denominator, 3) if denominator else None


def window_values(samples: Samples, *, start: datetime) -> dict[str, object]:
    """Publish the contract fields, with review distributions over the whole population."""
    return {
        "window_start": start,
        "releases": samples.releases,
        "operator_gestures": samples.operator_gestures,
        "operator_gestures_per_release": _ratio(samples.operator_gestures, samples.releases),
        "agent_gesture_ratio": _ratio(
            samples.agent_core, samples.agent_core + samples.operator_core
        ),
        "merged_prs": samples.merged_prs,
        "reviewed_prs": samples.reviewed_prs,
        "rulings_per_reviewed_pr": _ratio(samples.rulings, samples.reviewed_prs),
        "agent_rulings": samples.agent_rulings,
        "review_rounds_median": float(median(samples.rounds)) if samples.rounds else None,
        "review_rounds_p90": (
            sorted(samples.rounds)[ceil(0.9 * len(samples.rounds)) - 1] if samples.rounds else None
        ),
        "first_pass_approval_ratio": _ratio(sum(samples.first_pass), len(samples.first_pass)),
        "awaiting_ruling_passes": samples.awaiting_ruling_passes,
        "ready_to_merged_hours_median": (
            round(median(samples.ready_to_merged_hours), 2)
            if samples.ready_to_merged_hours
            else None
        ),
        "rollbacks": samples.rollbacks,
        "reverts": samples.reverts,
        "incidents": samples.incidents,
    }


def active(samples: Samples) -> bool:
    """Only counted activity keeps a project visible in the publication."""
    return any(
        (
            samples.releases,
            samples.operator_gestures,
            samples.agent_core,
            samples.merged_prs,
            samples.rulings,
            samples.rollbacks,
            samples.reverts,
            samples.incidents,
        )
    )
