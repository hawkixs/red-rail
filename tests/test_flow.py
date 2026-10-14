"""Synthetic UTC evidence pins final-head durations without reading external state."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta

import pytest

from rail.flow import (
    SEGMENTS,
    FlowDiscovery,
    FlowPull,
    FlowTimeline,
    GitHubEvidence,
    HolderEvidence,
    ReviewFacts,
    RunEvidence,
    Segments,
    StatusEvidence,
    SuiteEvidence,
    compute_timeline,
    judge_facts,
    push_proxy,
    union_minutes,
)

BASE = datetime(2026, 10, 14, 12, tzinfo=UTC)
REVIEWER_APP = 42


def at(minutes: float) -> datetime:
    """Keep fixture times relative to a fixed push, including fractional minutes."""
    return BASE + timedelta(minutes=minutes)


def pull() -> FlowPull:
    """Use distinct final-head and merge SHAs to prevent accidental substitution."""
    return FlowPull("example/repo", 7, "a" * 40, "b" * 40, at(-10), at(60))


def run(
    id: int,
    start: float | None,
    end: float | None,
    *,
    reviewer: bool = False,
    app_id: int | None = None,
) -> RunEvidence:
    """Build CI or same-named reviewer attempts with explicit missing timestamps."""
    return RunEvidence(
        id,
        "red-rail/review" if reviewer else "ci",
        app_id if app_id is not None else REVIEWER_APP if reviewer else 1,
        "example-reviewer" if reviewer else "github-actions",
        "completed" if end is not None else "in_progress",
        at(start) if start is not None else None,
        at(end) if end is not None else None,
    )


def status(id: int, minute: float, description: str) -> StatusEvidence:
    """Status descriptions contain synthetic run ids, never execution logs."""
    return StatusEvidence(id, "independent-review", "success", description, at(minute))


def evidence(
    runs: tuple[RunEvidence, ...] | None = (),
    statuses: tuple[StatusEvidence, ...] | None = (),
) -> GitHubEvidence:
    """A CI suite establishes the push independently of run-list completeness."""
    return GitHubEvidence(pull(), (SuiteEvidence(1, 1, "github-actions", at(0)),), runs, statuses)


def timeline(
    facts: GitHubEvidence,
    review: ReviewFacts | None = None,
    holder: HolderEvidence | None = None,
) -> FlowTimeline:
    """Require inclusion before inspecting nullable segment observations."""
    result = compute_timeline(
        facts, review or ReviewFacts(0, False, None), holder, reviewer_app_id=REVIEWER_APP
    )
    assert result is not None
    return result


def test_push_proxy_selects_ci_suites_and_clamps_to_creation() -> None:
    """Only pre-merge CI suites establish a push; reused heads cannot predate creation."""
    pr = pull()
    foreign = SuiteEvidence(1, REVIEWER_APP, "example-reviewer", at(-20))
    late = SuiteEvidence(2, 1, "github-actions", at(61))
    suites = (foreign, late, SuiteEvidence(3, 1, "github-actions", at(5)))
    assert push_proxy(pr, suites) == at(5)
    reused = suites + (SuiteEvidence(4, 1, "github-actions", at(-30)),)
    assert push_proxy(pr, reused) == at(-10)
    assert push_proxy(pr, (SuiteEvidence(5, 1, "github-actions", at(60)),)) == at(60)
    for absent in (None, (), (foreign,), (late,), (foreign, late)):
        assert push_proxy(pr, absent) is None
        assert (
            compute_timeline(
                GitHubEvidence(pr, absent, (), ()),
                ReviewFacts(None, None, None),
                None,
                reviewer_app_id=REVIEWER_APP,
            )
            is None
        )
    assert push_proxy(replace(pr, created_at=at(61)), reused) is None
    discovery = FlowDiscovery((pr,), 2)
    assert discovery.pulls == (pr,)
    assert discovery.unread_prs == 2
    assert pr.head_sha != pr.merge_sha
    with pytest.raises(FrozenInstanceError):
        discovery.unread_prs = 0  # type: ignore[misc]
    assert not hasattr(discovery, "__dict__")


def test_segments_include_every_attempt_and_union_overlaps() -> None:
    """CI wall time includes gaps; idle subtracts occupied time only once."""
    facts = evidence(
        (run(1, 2, 12), run(2, 20, 35), run(3, 8, 25, reviewer=True)),
        (status(1, 30, "Approved via example (ha 20261014T121500-abc123)"),),
    )
    result = timeline(facts, ReviewFacts(2, False, 3))
    # CI spans [2, 35], longest is [20, 35]. The union also spans [2, 35].
    assert result.segments == Segments(60.0, 2.0, 33.0, 15.0, 8.0, 17.0, 30.0, 15.0, 27.0, None)
    assert (result.pull, result.push_at, result.rounds, result.carried) == (pull(), at(0), 2, False)
    assert (result.judge_carried, result.judge_provider, result.holder_approx) == (
        False,
        "example",
        None,
    )
    assert SEGMENTS == (
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
    assert tuple(field.name for field in fields(Segments)) == SEGMENTS
    gap = timeline(evidence((run(1, 2, 12), run(2, 20, 35))))
    assert (gap.segments.ci, gap.segments.ci_longest, gap.segments.idle) == (33.0, 15.0, 35.0)
    # Clipped [0, 5] touches [5, 10]; [8, 15] overlaps; [55, 60] adds five.
    intervals = (
        (at(55), at(70)),
        (at(8), at(15)),
        (at(-5), at(5)),
        (at(5), at(10)),
        (at(25), at(20)),
        (at(-10), at(-1)),
        (at(61), at(65)),
    )
    assert union_minutes(intervals, start=at(0), end=at(60)) == 20.0
    assert union_minutes((), start=at(0), end=at(60)) == 0.0
    assert union_minutes(intervals, start=at(60), end=at(0)) == 0.0


def test_runs_are_clipped_and_post_merge_evidence_is_ignored() -> None:
    """Active runs end at merge, while starts and verdicts after merge have no effect."""
    for completion in (None, 90):
        facts = evidence(
            (run(1, -5, completion), run(2, 10, 80, reviewer=True)),
            (status(1, 50, "Approved via example (ha 20261014T115500-abc)"),),
        )
        expected = Segments(60.0, 0.0, 60.0, 60.0, 10.0, 50.0, 50.0, 50.0, 0.0, None)
        result = timeline(facts, ReviewFacts(1, False, 2))
        assert result.segments == expected
        later = replace(
            facts,
            runs=facts.runs + (run(3, 61, 70), run(4, 61, 70, reviewer=True)),
            statuses=facts.statuses
            + (status(2, 61, "Approved via later (ha 20261014T130000-def)"),),
        )
        assert timeline(later, ReviewFacts(1, False, 2)) == result
    reversed_runs = timeline(
        evidence((run(1, 20, 10), run(2, 30, 25, reviewer=True))), ReviewFacts(1, False, 2)
    )
    assert reversed_runs.segments == Segments(
        60.0, None, None, None, None, None, None, None, 60.0, None
    )
    before_push = timeline(
        evidence((), (status(1, -1, "Approved via example (ha 20261014T115000-abc)"),))
    )
    assert before_push.segments.judge_wait is None
    assert before_push.segments.judge is None
    assert before_push.segments.idle == 60.0
    fractional = timeline(evidence((run(1, 0.25, 0.75),)))
    assert (fractional.segments.ci_start, fractional.segments.ci) == (0.25, 0.5)


def test_review_uses_receipt_check_id_and_app_identity() -> None:
    """The attested run wins over retries; same names from another App prove nothing."""
    facts = evidence(
        (
            run(1, 10, 20, reviewer=True),
            run(2, 30, 50, reviewer=True),
            run(3, 1, 59, reviewer=True, app_id=99),
            replace(run(4, 0, 60, reviewer=True), name="unrelated-check"),
        )
    )
    selected = timeline(facts, ReviewFacts(2, False, 1))
    assert (selected.segments.review_start, selected.segments.review) == (10.0, 10.0)
    assert selected.segments.idle == 30.0
    for check_id in (None, 3, 4, 999):
        missing = timeline(facts, ReviewFacts(2, False, check_id))
        assert (missing.segments.review_start, missing.segments.review) == (None, None)
        assert missing.segments.idle == 30.0
    missing_time = timeline(evidence((run(1, None, 20, reviewer=True),)), ReviewFacts(1, False, 1))
    assert (missing_time.segments.review_start, missing_time.segments.review) == (None, None)
    assert missing_time.segments.idle is None
    other_missing = timeline(
        evidence((run(1, 10, 20, reviewer=True), run(2, None, 30, reviewer=True))),
        ReviewFacts(1, False, 1),
    )
    assert (other_missing.segments.review_start, other_missing.segments.review) == (10.0, 10.0)
    assert other_missing.segments.idle is None
    post_merge = timeline(evidence((run(1, 61, 70, reviewer=True),)), ReviewFacts(1, False, 1))
    assert (post_merge.segments.review_start, post_merge.segments.review) == (None, None)


def test_carried_reviewer_and_judge_do_not_reuse_source_execution() -> None:
    """Carried verdicts retain their wait but never import source-head execution."""
    carried = status(1, 40, "Carried from abcdef via example (ha 20261013T120000-source)")
    assert judge_facts((carried,), push=at(0), merged_at=at(60)) == (at(40), None, True, None)
    facts = evidence((run(1, 5, 15), run(2, 20, 22, reviewer=True)), (carried,))
    result = timeline(facts, ReviewFacts(0, True, 2))
    assert result.segments == Segments(60.0, 5.0, 10.0, 10.0, 20.0, 0.0, 40.0, None, 48.0, None)
    assert (result.carried, result.judge_carried, result.judge_provider) == (True, True, None)
    for runs in ((), None, (run(2, None, None, reviewer=True),)):
        missing = timeline(evidence(runs, (carried,)), ReviewFacts(0, True, 2))
        assert missing.segments.review == 0.0
        assert missing.segments.review_start is None
        assert missing.segments.judge is None
    no_id = timeline(facts, ReviewFacts(0, True, None))
    assert (no_id.segments.review, no_id.segments.review_start) == (0.0, None)


def test_missing_evidence_is_unknown_and_true_zero_is_preserved() -> None:
    """Read failures differ from successful empty evidence and measured zero durations."""
    failed_statuses = timeline(evidence((run(1, 0, 10),), None))
    assert failed_statuses.segments == Segments(
        60.0, 0.0, 10.0, 10.0, None, None, None, None, None, None
    )
    assert (failed_statuses.judge_carried, failed_statuses.judge_provider) == (None, None)
    zero = timeline(
        evidence(
            (run(1, 0, 0), run(2, 0, 0, reviewer=True)),
            (status(1, 0, "Approved via example (ha 20261014T120000-abc)"),),
        ),
        ReviewFacts(1, False, 2),
    )
    assert zero.segments == Segments(60.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 60.0, None)
    empty = timeline(evidence())
    assert empty.segments == Segments(60.0, None, None, None, None, None, None, None, 60.0, None)
    assert empty.judge_carried is None
    unread_runs = timeline(evidence(None))
    assert unread_runs.segments == Segments(
        60.0, None, None, None, None, None, None, None, None, None
    )
    missing_start = timeline(evidence((run(1, 0, 10), run(2, None, 20))))
    assert (missing_start.segments.ci_start, missing_start.segments.ci) == (None, None)
    assert (missing_start.segments.ci_longest, missing_start.segments.idle) == (None, None)
    unrelated = replace(run(1, None, None), app_slug="other-app")
    assert timeline(evidence((unrelated,))).segments.idle == 60.0
    unknown_description = timeline(evidence((), (status(1, 40, "Approved"),)))
    assert (unknown_description.segments.judge_wait, unknown_description.segments.judge) == (
        40.0,
        None,
    )
    assert unknown_description.segments.idle == 60.0
    unread_ledger = timeline(
        evidence((run(1, 10, 20, reviewer=True),)), ReviewFacts(None, None, None)
    )
    assert (unread_ledger.rounds, unread_ledger.carried) == (None, None)
    assert (unread_ledger.segments.review, unread_ledger.segments.review_start) == (None, None)
    assert unread_ledger.segments.idle == 50.0


def test_judge_run_timestamp_and_provider_are_bounded() -> None:
    """Only a bounded ASCII provider and a valid run timestamp establish execution."""
    cases = (
        ("Approved via example_2-A (ha 20261014T121530-abc123)", at(15.5), "example_2-A"),
        ("x" * 32 + " (ha 20261014T121500-a)", at(15), "x" * 32),
        ("example (ha 20260230T121500-abc)", None, None),
        ("example (ha 20261014T251500-abc)", None, None),
        ("Approved", None, None),
        ("bad/provider (ha 20261014T121500-abc)", None, None),
        ("exámple (ha 20261014T121500-abc)", None, None),
        ("x" * 33 + " (ha 20261014T121500-abc)", None, None),
        ("example (ha 20261014T125000-abc)", None, None),
        ("example (ha 20261014T121500-)", None, None),
        ("example (ha 20261014T121500-abc_def)", None, None),
    )
    for description, start, provider in cases:
        assert judge_facts((status(1, 40, description),), push=at(0), merged_at=at(60)) == (
            at(40),
            start,
            False,
            provider,
        )
    older = status(1, 20, "old (ha 20261014T121000-abc)")
    winner = status(3, 40, "chosen (ha 20261014T121500-abc)")
    history = (
        winner,
        status(2, 40, "lower (ha 20261014T121000-abc)"),
        older,
        replace(status(4, 50, "Other context"), context="unrelated"),
        replace(status(5, 55, "Not successful"), state="pending"),
        status(6, 61, "late (ha 20261014T130000-abc)"),
    )
    assert judge_facts(history, push=at(0), merged_at=at(60)) == (
        at(40),
        at(15),
        False,
        "chosen",
    )
    for absent in (None, (), history[3:]):
        assert judge_facts(absent, push=at(0), merged_at=at(60)) == (None, None, None, None)
    at_merge = status(7, 60, "example (ha 20261014T130000-abc)")
    assert judge_facts((at_merge,), push=at(0), merged_at=at(60)) == (
        at(60),
        at(60),
        False,
        "example",
    )


def test_judge_facts_reads_the_posted_status_form() -> None:
    """Posted statuses identify the provider separately from the bounded model token."""
    cases = (
        (
            "APPROVE: codex gpt-6.1-sol (ha 20261014T121530-abc123) exact head",
            at(15.5),
            False,
            "codex",
        ),
        (
            "APPROVE: opencode-go deepseek-v4.1-flash (ha 20261014T122000-abc123) exact head",
            at(20),
            False,
            "opencode-go",
        ),
        (
            "APPROVE: agy gemini-3.8-flash-high (ha 20261014T122530-abc123), "
            "plan text identical to the judged head",
            at(25.5),
            False,
            "agy",
        ),
        ("APPROVE: codex model-1 (ha 20261014T121530-abc123)", at(15.5), False, "codex"),
        (
            "APPROVE: " + "x" * 32 + " " + "A0._-/:x" * 8 + " (ha 20261014T121530-a)",
            at(15.5),
            False,
            "x" * 32,
        ),
        ("APPROVE: bad/provider model-1 (ha 20261014T121530-abc123)", None, False, None),
        ("APPROVE: " + "x" * 33 + " model-1 (ha 20261014T121530-a)", None, False, None),
        ("APPROVE: codex " + "x" * 65 + " (ha 20261014T121530-abc123)", None, False, None),
        ("APPROVE: codex model extra (ha 20261014T121530-abc123)", None, False, None),
        ("approve: codex model-1 (ha 20261014T121530-abc123)", None, False, None),
        ("prefix APPROVE: codex model-1 (ha 20261014T121530-abc123)", None, False, None),
        ("prefix approve: codex model-1 (ha 20261014T121530-abc123)", None, False, None),
        ("APPROVE: codex model-1 (ha 20260230T121530-abc123)", None, False, None),
        ("APPROVE: codex model-1 (ha 20261014T125000-abc123)", None, False, None),
        ("APPROVE: codex model-1 (ha 20261014T121530-)", None, False, None),
        ("APPROVE: codex model-1 (ha 20261014T121530-abc_def)", None, False, None),
        (
            "Carried from old head: APPROVE: codex gpt-6.1-sol (ha 20261014T121530-abc123)",
            None,
            True,
            None,
        ),
    )
    for description, start, carried, provider in cases:
        assert judge_facts((status(1, 40, description),), push=at(0), merged_at=at(60)) == (
            at(40),
            start,
            carried,
            provider,
        ), description


def test_holder_can_begin_before_push_but_not_after_merge() -> None:
    """Queue time has independent bounds, so a pre-push holder is not shortened."""
    for approximate in (False, True):
        result = timeline(evidence(), holder=HolderEvidence(at(-20), at(60), approximate))
        assert result.segments.holder == 80.0
        assert result.holder_approx is approximate
        assert (result.segments.flow, result.segments.idle) == (60.0, 60.0)
    zero = timeline(evidence(), holder=HolderEvidence(at(60), at(60), False))
    assert (zero.segments.holder, zero.holder_approx) == (0.0, False)
    invalid = timeline(evidence(), holder=HolderEvidence(at(61), at(60), True))
    assert (invalid.segments.holder, invalid.holder_approx) == (None, None)
    missing = timeline(evidence())
    assert (missing.segments.holder, missing.holder_approx) == (None, None)
