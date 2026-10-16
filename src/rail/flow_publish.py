"""Collect flow independently so unread local delivery inputs cannot hide merged pulls."""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from rail.flow import (
    SEGMENTS,
    FlowTimeline,
    RepositorySummary,
    Segment,
    compute_timeline,
    recent_timelines,
    summarize_repository,
)
from rail.flow_cache import FlowCache
from rail.flow_sources import QueueReadError, QueueTurn, holder_evidence, read_queue, review_facts
from rail.ledger import LedgerError, Record
from rail.metrics_contract import (
    FlowBlock,
    FlowCoverage,
    FlowPercentiles,
    FlowRecent,
    FlowRepository,
    FlowSegments,
)
from rail.reviewer.config import RepositoryConfig, ReviewerConfig
from rail.reviewer.github import GitHubApp, GitHubError


@dataclass(frozen=True, slots=True)
class FlowCollection:
    """Retain complete summaries and bounded recent timelines before public conversion."""

    repositories: tuple[RepositorySummary, ...]
    recent: tuple[FlowTimeline, ...]
    coverage: FlowCoverage


def collect_flow(
    config: ReviewerConfig,
    *,
    now: datetime,
    window_days: int,
    github: GitHubApp,
    cache: FlowCache,
    records_for: Callable[[RepositoryConfig], list[Record] | None],
) -> FlowCollection:
    """Discover main independently and count unknown segments once per retained pull."""
    missing: list[str] = []
    unread_prs: dict[str, int] = {}
    unread_segments: dict[str, dict[Segment | Literal["ledger"], int]] = {}
    summaries: list[RepositorySummary] = []
    timelines: list[FlowTimeline] = []
    queues: dict[Path, tuple[QueueTurn, ...] | None] = {}
    for repository in config.repositories:
        slug = repository.slug
        try:
            discovery = github.flow_merged_pulls(
                slug, base="main", since=now - timedelta(days=window_days), until=now
            )
        except GitHubError as error:
            missing.append(slug)
            print(slug, "pulls", type(error).__name__, file=sys.stderr)
            continue
        try:
            records = records_for(repository)
        except (LedgerError, OSError) as error:
            print(slug, "ledger", type(error).__name__, file=sys.stderr)
            records = None
        queue = repository.merge_queue
        if queue is not None and queue not in queues:
            try:
                queues[queue] = read_queue(queue)
            except QueueReadError as error:
                print(slug, "queue", type(error).__name__, file=sys.stderr)
                queues[queue] = None
        turns = queues.get(queue)
        kept: list[FlowTimeline] = []
        unread = discovery.unread_prs
        counts: Counter[Segment | Literal["ledger"]] = Counter()
        for pull in discovery.pulls:
            evidence = cache.get_or_read(pull, github=github, now=now)
            timeline = compute_timeline(
                evidence,
                review_facts(records, pull),
                holder_evidence(turns, pull) if turns is not None else None,
                reviewer_app_id=config.app_id,
            )
            if timeline is None:
                unread += 1
                continue
            kept.append(timeline)
            for segment in SEGMENTS:
                if segment == "holder" and queue is None:
                    continue
                if segment == "judge" and timeline.judge_carried is True:
                    continue
                if getattr(timeline.segments, segment) is None:
                    counts[segment] += 1
            if records is None:
                counts["ledger"] += 1
        if unread:
            unread_prs[slug] = unread
        if counts:
            unread_segments[slug] = dict(counts)
        summaries.append(summarize_repository(slug, tuple(kept)))
        timelines.extend(kept)
    return FlowCollection(
        tuple(sorted(summaries, key=lambda summary: summary.repository)),
        recent_timelines(tuple(timelines)),
        FlowCoverage(
            missing=tuple(sorted(missing)), unread_prs=unread_prs, unread_segments=unread_segments
        ),
    )


def _minutes(value: float | None) -> float | None:
    """Round measured minutes only at the public contract boundary."""
    return round(value, 2) if value is not None else None


def flow_block(
    collection: FlowCollection, *, generated_at: datetime, now: datetime, window_days: int
) -> FlowBlock:
    """Select public fields explicitly so private evidence cannot enter the answer."""
    return FlowBlock(
        version="rail-flow/v1",
        generated_at=generated_at,
        window_days=window_days,
        window_start=now - timedelta(days=window_days),
        repositories=tuple(
            FlowRepository(
                repository=summary.repository,
                merged_prs=summary.merged_prs,
                carry_rate=round(summary.carry_rate, 3) if summary.carry_rate is not None else None,
                segments={
                    segment: FlowPercentiles(
                        median=_minutes(summary.segments[segment].median),
                        p90=_minutes(summary.segments[segment].p90),
                        n=summary.segments[segment].n,
                    )
                    for segment in SEGMENTS
                },
            )
            for summary in sorted(collection.repositories, key=lambda summary: summary.repository)
        ),
        recent=tuple(
            FlowRecent(
                repository=timeline.pull.repository,
                number=timeline.pull.number,
                head_sha=timeline.pull.head_sha[:12],
                merged_at=timeline.pull.merged_at,
                segments=FlowSegments(
                    **{
                        segment: _minutes(getattr(timeline.segments, segment))
                        for segment in SEGMENTS
                    }
                ),
                rounds=timeline.rounds,
                carried=timeline.carried,
                judge_carried=timeline.judge_carried,
                judge_provider=timeline.judge_provider,
                holder_approx=timeline.holder_approx,
            )
            for timeline in recent_timelines(collection.recent)
        ),
        coverage=collection.coverage,
    )
