"""Pure final-head observations preserve unknown evidence and unrounded durations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from statistics import median
from typing import Literal

Segment = Literal[
    "flow",
    "ci_start",
    "ci",
    "ci_longest",
    "review_start",
    "review",
    "judge_wait",
    "judge",
    "idle",
    "holder",
]
SEGMENTS: tuple[Segment, ...] = (
    "flow",
    "ci_start",
    "ci",
    "ci_longest",
    "review_start",
    "review",
    "judge_wait",
    "judge",
    "idle",
    "holder",
)
_JUDGE_RUN = re.compile(r"(?<!\S)([A-Za-z0-9_-]{1,32})\s+\(ha ([0-9]{8}T[0-9]{6})-[A-Za-z0-9]+\)")
_POSTED_JUDGE_RUN = re.compile(
    r"\A[A-Z]+:\s+([A-Za-z0-9_-]{1,32})\s+[A-Za-z0-9._/:-]{1,64}\s+"
    r"\(ha ([0-9]{8}T[0-9]{6})-[A-Za-z0-9]+\)"
)


@dataclass(frozen=True, slots=True)
class FlowPull:
    """Keep the final head separate from the merge commit whose checks are irrelevant."""

    repository: str
    number: int
    head_sha: str
    merge_sha: str
    created_at: datetime
    merged_at: datetime


@dataclass(frozen=True, slots=True)
class FlowDiscovery:
    """Unread pulls remain counted rather than disappearing from coverage."""

    pulls: tuple[FlowPull, ...]
    unread_prs: int


@dataclass(frozen=True, slots=True)
class SuiteEvidence:
    """CI App identity and creation establish the final-head push proxy."""

    id: int
    app_id: int | None
    app_slug: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RunEvidence:
    """Retain every attempt and actual completion for computation and cache completeness."""

    id: int
    name: str
    app_id: int | None
    app_slug: str | None
    status: str
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class StatusEvidence:
    """Private descriptions are parsing inputs only, never timeline output fields."""

    id: int
    context: str
    state: str
    description: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GitHubEvidence:
    """None denotes a failed read; an empty tuple proves a successful empty read."""

    pull: FlowPull
    suites: tuple[SuiteEvidence, ...] | None
    runs: tuple[RunEvidence, ...] | None
    statuses: tuple[StatusEvidence, ...] | None


@dataclass(frozen=True, slots=True)
class ReviewFacts:
    """Receipt identity selects the final review without guessing from run order."""

    rounds: int | None
    carried: bool | None
    check_run_id: int | None


@dataclass(frozen=True, slots=True)
class HolderEvidence:
    """The queue interval is independent of push and retains its approximation flag."""

    started_at: datetime
    merged_at: datetime
    approximate: bool


@dataclass(frozen=True, slots=True)
class Segments:
    """Durations stay unrounded; missing evidence is distinct from measured zero."""

    flow: float | None
    ci_start: float | None
    ci: float | None
    ci_longest: float | None
    review_start: float | None
    review: float | None
    judge_wait: float | None
    judge: float | None
    idle: float | None
    holder: float | None


@dataclass(frozen=True, slots=True)
class FlowTimeline:
    """One final head yields one timeline, with nullable receipt and queue facts."""

    pull: FlowPull
    push_at: datetime
    segments: Segments
    rounds: int | None
    carried: bool | None
    judge_carried: bool | None
    judge_provider: str | None
    holder_approx: bool | None


def push_proxy(pull: FlowPull, suites: tuple[SuiteEvidence, ...] | None) -> datetime | None:
    """Use only pre-merge CI suites, clamping reused heads to pull creation."""
    candidates = [
        suite.created_at
        for suite in suites or ()
        if suite.app_slug == "github-actions" and suite.created_at <= pull.merged_at
    ]
    if not candidates:
        return None
    push = max(min(candidates), pull.created_at)
    return push if push <= pull.merged_at else None


def judge_facts(
    statuses: tuple[StatusEvidence, ...] | None,
    *,
    push: datetime,
    merged_at: datetime,
) -> tuple[datetime | None, datetime | None, bool | None, str | None]:
    """Read the latest pre-merge success; carried statuses never supply source execution."""
    candidates = [
        status
        for status in statuses or ()
        if status.context == "independent-review"
        and status.state == "success"
        and status.created_at <= merged_at
    ]
    if not candidates:
        return None, None, None, None
    verdict = max(candidates, key=lambda status: (status.created_at, status.id))
    if verdict.description.startswith("Carried from "):
        return verdict.created_at, None, True, None
    match = _POSTED_JUDGE_RUN.match(verdict.description)
    if match is None:
        match = _JUDGE_RUN.search(verdict.description)
        # A malformed posted status must not reinterpret its model as a provider.
        if match is not None and re.search(r"\S+:\s+\S+\s", verdict.description[: match.start()]):
            match = None
    if match is None:
        return verdict.created_at, None, False, None
    try:
        started_at = datetime.strptime(match[2], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return verdict.created_at, None, False, None
    if started_at > verdict.created_at:
        return verdict.created_at, None, False, None
    return verdict.created_at, started_at, False, match[1]


def union_minutes(
    intervals: tuple[tuple[datetime, datetime], ...], *, start: datetime, end: datetime
) -> float:
    """Clip and merge occupied intervals so overlapping attempts never count twice."""
    clipped = sorted(
        (max(left, start), min(right, end))
        for left, right in intervals
        if left <= right and max(left, start) <= min(right, end)
    )
    if not clipped:
        return 0.0
    left, right = clipped[0]
    seconds = 0.0
    for next_left, next_right in clipped[1:]:
        if next_left <= right:
            right = max(right, next_right)
        else:
            seconds += (right - left).total_seconds()
            left, right = next_left, next_right
    return (seconds + (right - left).total_seconds()) / 60


def _run_interval(
    run: RunEvidence, *, push: datetime, merged_at: datetime
) -> tuple[datetime, datetime] | None:
    """Ignore reversed or post-merge attempts and bound active runs by the merge."""
    if run.started_at is None or run.started_at >= merged_at:
        return None
    start = max(run.started_at, push)
    end = min(run.completed_at or merged_at, merged_at)
    return (start, end) if start <= end else None


def _minutes(start: datetime, end: datetime) -> float:
    """Preserve second precision until the contract assembles published values."""
    return (end - start).total_seconds() / 60


def compute_timeline(
    evidence: GitHubEvidence,
    review: ReviewFacts,
    holder: HolderEvidence | None,
    *,
    reviewer_app_id: int,
) -> FlowTimeline | None:
    """Measure only final-head execution, keeping failed reads unknown rather than zero."""
    push = push_proxy(evidence.pull, evidence.suites)
    if push is None:
        return None
    merged_at = evidence.pull.merged_at
    flow = _minutes(push, merged_at)
    ci_runs = tuple(run for run in evidence.runs or () if run.app_slug == "github-actions")
    reviewer_runs = tuple(
        run
        for run in evidence.runs or ()
        if run.app_id == reviewer_app_id and run.name == "red-rail/review"
    )
    ci_intervals = tuple(
        interval
        for run in ci_runs
        if (interval := _run_interval(run, push=push, merged_at=merged_at)) is not None
    )
    reviewer_intervals = tuple(
        interval
        for run in reviewer_runs
        if (interval := _run_interval(run, push=push, merged_at=merged_at)) is not None
    )
    ci_start = ci = ci_longest = None
    if ci_intervals and not any(run.started_at is None for run in ci_runs):
        first = min(start for start, end in ci_intervals)
        last = max(end for start, end in ci_intervals)
        ci_start = _minutes(push, first)
        ci = _minutes(first, last)
        ci_longest = max(_minutes(start, end) for start, end in ci_intervals)

    review_start = review_duration = None
    for run in reviewer_runs:
        if run.id == review.check_run_id:
            interval = _run_interval(run, push=push, merged_at=merged_at)
            if interval is not None:
                review_start = _minutes(push, interval[0])
                review_duration = _minutes(*interval)
            break
    if review.carried is True:
        review_duration = 0.0

    verdict, judge_start, judge_carried, provider = judge_facts(
        evidence.statuses, push=push, merged_at=merged_at
    )
    judge_wait = judge_duration = None
    judge_intervals: tuple[tuple[datetime, datetime], ...] = ()
    if verdict is not None and verdict >= push:
        judge_wait = _minutes(push, verdict)
        if judge_start is not None:
            interval = (max(judge_start, push), verdict)
            judge_duration = _minutes(*interval)
            judge_intervals = (interval,)

    idle = None
    if (
        evidence.runs is not None
        and evidence.statuses is not None
        and not any(run.started_at is None for run in ci_runs + reviewer_runs)
    ):
        idle = flow - union_minutes(
            ci_intervals + reviewer_intervals + judge_intervals, start=push, end=merged_at
        )

    holder_duration = holder_approx = None
    if holder is not None:
        holder_end = min(holder.merged_at, merged_at)
        if holder.started_at <= holder_end:
            holder_duration = _minutes(holder.started_at, holder_end)
            holder_approx = holder.approximate

    return FlowTimeline(
        evidence.pull,
        push,
        Segments(
            flow,
            ci_start,
            ci,
            ci_longest,
            review_start,
            review_duration,
            judge_wait,
            judge_duration,
            idle,
            holder_duration,
        ),
        review.rounds,
        review.carried,
        judge_carried,
        provider,
        holder_approx,
    )


@dataclass(frozen=True, slots=True)
class Percentiles:
    """Keep unrounded observations and their count so absent samples remain unknown."""

    median: float | None
    p90: float | None
    n: int


@dataclass(frozen=True, slots=True)
class RepositorySummary:
    """Keep every segment for one repository, including a successfully read empty window."""

    repository: str
    merged_prs: int
    carry_rate: float | None
    segments: dict[Segment, Percentiles]


def segment_percentiles(values: tuple[float, ...]) -> Percentiles:
    """Use the median and nearest-rank p90 without inventing values for empty samples."""
    if not values:
        return Percentiles(None, None, 0)
    ordered = sorted(values)
    n = len(ordered)
    return Percentiles(median(ordered), ordered[ceil(0.9 * n) - 1], n)


def summarize_repository(repository: str, timelines: tuple[FlowTimeline, ...]) -> RepositorySummary:
    """Summarize the full repository window; carry excludes only its own duration sample."""
    kept = tuple(timeline for timeline in timelines if timeline.pull.repository == repository)
    segments = {
        segment: segment_percentiles(
            tuple(
                value
                for timeline in kept
                if not (segment == "review" and timeline.carried is True)
                and not (segment == "judge" and timeline.judge_carried is True)
                if (value := getattr(timeline.segments, segment)) is not None
            )
        )
        for segment in SEGMENTS
    }
    known = tuple(timeline for timeline in kept if timeline.carried is not None)
    carry_rate = sum(timeline.carried is True for timeline in known) / len(known) if known else None
    return RepositorySummary(repository, len(kept), carry_rate, segments)


def recent_timelines(
    timelines: tuple[FlowTimeline, ...], *, limit: int = 20
) -> tuple[FlowTimeline, ...]:
    """Bound recent merges globally, using repository and PR order to make ties stable."""
    if not 0 <= limit <= 20:
        raise ValueError("recent timeline limit must be between 0 and 20")
    ties = sorted(timelines, key=lambda timeline: (timeline.pull.repository, timeline.pull.number))
    return tuple(sorted(ties, key=lambda timeline: timeline.pull.merged_at, reverse=True)[:limit])
