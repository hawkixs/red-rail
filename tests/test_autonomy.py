"""D4 autonomy rules use raw facts so windows and totals preserve their population."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from rail.autonomy import (
    SINCE,
    MergedPull,
    ProjectFacts,
    Samples,
    active,
    issuer_class,
    merge,
    window_samples,
    window_start,
    window_values,
)
from rail.ledger import AttestationKind, Record, RecordKind

NOW = datetime(2026, 10, 13, tzinfo=UTC)
REPOSITORY = "example/red-alpha"


def _date(day: int) -> datetime:
    return datetime(2026, 10, day, tzinfo=UTC)


def _record(
    kind: AttestationKind,
    at: datetime,
    *,
    issuer: str = "operator",
    data: dict[str, object] | None = None,
    project: str = "red-alpha",
) -> Record:
    return Record.build(
        kind=RecordKind.ATTESTATION,
        project=project,
        issuer=issuer,
        idempotency_key=f"{kind}:{at.isoformat()}",
        payload={"kind": kind.value, "data": data or {}},
        recorded_at=at,
    )


def _verdict(
    pr: int,
    at: datetime,
    *,
    repository: str = REPOSITORY,
    round_label: int | str = 1,
    providers: tuple[str, ...] = ("reviewer",),
    verdict: str = "approve",
) -> Record:
    return _record(
        AttestationKind.REVIEW_VERDICT,
        at,
        issuer="red-rail-reviewer",
        data={
            "repository": repository,
            "pr": pr,
            "round": round_label,
            "providers": list(providers),
            "verdict": verdict,
        },
    )


def _pull(number: int, *, at: datetime | None = None, hours: float = 2.0) -> MergedPull:
    merged_at = at or _date(10)
    return MergedPull(number, merged_at, merged_at - timedelta(hours=hours))


def _facts(
    records: list[Record],
    merged: list[MergedPull] | None = None,
    reverts: list[datetime] | None = None,
    *,
    project: str = "red-alpha",
    repository: str = REPOSITORY,
) -> ProjectFacts:
    return ProjectFacts(project, repository, records, merged or [], reverts or [])


def _values(samples: Samples) -> dict[str, object]:
    return window_values(samples, start=window_start(NOW, 7))


def test_since_cuts_every_source_at_the_actor_boundary() -> None:
    assert SINCE == _date(4)
    assert window_start(NOW, 30) == SINCE
    assert window_start(NOW, 7) == _date(6)
    before = SINCE - timedelta(microseconds=1)
    records = [
        _record(kind, at)
        for at in (before, SINCE)
        for kind in (
            AttestationKind.RELEASED,
            AttestationKind.DEPLOYED,
            AttestationKind.ROLLED_BACK,
            AttestationKind.RESTORED,
            AttestationKind.INCIDENT_DETECTED,
        )
    ]
    records.extend([_verdict(1, before), _verdict(2, SINCE)])
    samples = window_samples(
        _facts(records, [_pull(1, at=before), _pull(2, at=SINCE)], [before, SINCE]),
        now=NOW,
        days=30,
    )
    assert samples.releases == 1
    assert samples.operator_gestures == samples.operator_core == 4
    assert samples.rollbacks == samples.incidents == samples.reverts == 1
    assert samples.merged_prs == samples.reviewed_prs == 1
    assert samples.rounds == (1,)


def test_review_history_before_the_window_follows_the_merged_pull() -> None:
    records = [
        _verdict(1, _date(5), verdict="request_changes"),
        _record(
            AttestationKind.REVIEW_RULING,
            _date(5),
            data={"repository": REPOSITORY, "pr": 1},
        ),
        _verdict(1, _date(8), round_label="awaiting_ruling"),
        _verdict(1, _date(9), round_label=2),
    ]
    samples = window_samples(_facts(records, [_pull(1)]), now=NOW, days=7)
    assert samples.reviewed_prs == 1
    assert samples.rounds == (2,)
    assert samples.first_pass == (False,)
    assert samples.rulings == samples.awaiting_ruling_passes == 1
    assert samples.operator_gestures == 0


def test_first_verdict_before_since_excludes_review_but_keeps_merge() -> None:
    records = [_verdict(1, _date(7)), _verdict(1, _date(3), verdict="request_changes")]
    samples = window_samples(_facts(records, [_pull(1, hours=4)]), now=NOW, days=7)
    assert samples.merged_prs == 1
    assert samples.reviewed_prs == 0
    assert samples.rounds == samples.first_pass == ()
    assert samples.ready_to_merged_hours == (4.0,)


def test_repository_and_pull_number_both_select_review_history() -> None:
    other = "example/red-beta"
    records = [
        _verdict(1, _date(3), repository=other),
        _verdict(1, _date(7), verdict="request_changes"),
        _verdict(1, _date(8), repository=other),
        _verdict(2, _date(8)),
        _record(
            AttestationKind.REVIEW_RULING,
            _date(8),
            issuer="agent:test:s1",
            data={"repository": other, "pr": 1},
        ),
        _record(
            AttestationKind.REVIEW_RULING,
            _date(8),
            issuer="agent:test:s1",
            data={"repository": REPOSITORY, "pr": 2},
        ),
    ]
    alpha = window_samples(_facts(records, [_pull(1)]), now=NOW, days=7)
    beta = window_samples(_facts(records, [_pull(1)], repository=other), now=NOW, days=7)
    assert alpha.reviewed_prs == 1
    assert alpha.rounds == (1,)
    assert alpha.first_pass == (False,)
    assert alpha.rulings == alpha.agent_rulings == 0
    assert beta.merged_prs == 1
    assert beta.reviewed_prs == beta.rulings == beta.agent_rulings == 0


@pytest.mark.parametrize(
    ("round_label", "providers"),
    [
        ("mechanical", ("reviewer",)),
        ("no_verdict", ("reviewer",)),
        ("awaiting_ruling", ("reviewer",)),
        ("closure", ()),
        (1, ()),
    ],
)
def test_unjudged_verdicts_keep_zero_rounds_in_the_population(
    round_label: int | str, providers: tuple[str, ...]
) -> None:
    samples = window_samples(
        _facts([_verdict(1, _date(7), round_label=round_label, providers=providers)], [_pull(1)]),
        now=NOW,
        days=7,
    )
    assert samples.reviewed_prs == 1
    assert samples.rounds == (0,)
    assert samples.first_pass == ()
    assert samples.awaiting_ruling_passes == int(round_label == "awaiting_ruling")


@pytest.mark.parametrize("round_label", [1, 2, 3, "closure"])
def test_judged_round_labels_all_count(round_label: int | str) -> None:
    samples = window_samples(
        _facts([_verdict(1, _date(7), round_label=round_label)], [_pull(1)]),
        now=NOW,
        days=7,
    )
    assert samples.rounds == (1,)
    assert samples.first_pass == (True,)


def test_drills_cost_gestures_without_counting_as_failures() -> None:
    records = [
        _record(kind, _date(8), data={"drill": drill})
        for drill in (True, False)
        for kind in (
            AttestationKind.ROLLED_BACK,
            AttestationKind.RESTORED,
            AttestationKind.INCIDENT_DETECTED,
        )
    ]
    samples = window_samples(_facts(records), now=NOW, days=7)
    assert samples.operator_gestures == samples.operator_core == 4
    assert samples.rollbacks == samples.incidents == 1


def test_operator_ruling_is_a_gesture_but_not_a_core_gesture() -> None:
    records = [
        _verdict(1, _date(7)),
        _record(
            AttestationKind.REVIEW_RULING,
            _date(8),
            data={"repository": REPOSITORY, "pr": 1},
        ),
    ]
    samples = window_samples(_facts(records, [_pull(1)]), now=NOW, days=7)
    assert samples.operator_gestures == samples.rulings == 1
    assert samples.operator_core == samples.agent_core == samples.agent_rulings == 0
    assert _values(samples)["agent_gesture_ratio"] is None


@pytest.mark.parametrize(
    ("issuer", "expected"),
    [
        ("operator", "operator"),
        ("red-rail-reviewer", "other"),
        ("service:x", "other"),
        ("red-alpha", "other"),
        ("agent:claude-code:s1", "agent"),
        ("agent:test", "agent"),
    ],
)
def test_issuer_classes_are_independent_of_service_and_project_names(
    issuer: str, expected: str
) -> None:
    assert issuer_class(issuer) == expected


def test_p90_uses_nearest_rank() -> None:
    values = _values(Samples(rounds=(1, 2, 2, 3)))
    assert values["review_rounds_median"] == 2.0
    assert values["review_rounds_p90"] == 3


def test_zero_round_pull_contributes_to_median_but_not_first_pass_ratio() -> None:
    records = [
        _verdict(1, _date(9), round_label=2),
        _verdict(1, _date(7), verdict="request_changes"),
        _verdict(2, _date(7), round_label="mechanical"),
    ]
    samples = window_samples(_facts(records, [_pull(1), _pull(2)]), now=NOW, days=7)
    assert sorted(samples.rounds) == [0, 2]
    assert samples.first_pass == (False,)
    values = _values(samples)
    assert values["review_rounds_median"] == 1.0
    assert values["first_pass_approval_ratio"] == 0.0


def test_unjudged_population_has_zero_median_and_unknown_first_pass_ratio() -> None:
    samples = window_samples(
        _facts(
            [
                _verdict(1, _date(7), round_label="mechanical"),
                _verdict(2, _date(8), round_label="closure", providers=()),
            ],
            [_pull(1), _pull(2)],
        ),
        now=NOW,
        days=7,
    )
    assert samples.rounds == (0, 0)
    values = _values(samples)
    assert values["review_rounds_median"] == 0.0
    assert values["review_rounds_p90"] == 0
    assert values["first_pass_approval_ratio"] is None


def test_empty_population_has_unknown_derived_values_and_zero_counts() -> None:
    samples = window_samples(_facts([]), now=NOW, days=7)
    assert not active(samples)
    assert _values(samples) == {
        "window_start": _date(6),
        "releases": 0,
        "operator_gestures": 0,
        "operator_gestures_per_release": None,
        "agent_gesture_ratio": None,
        "merged_prs": 0,
        "reviewed_prs": 0,
        "rulings_per_reviewed_pr": None,
        "agent_rulings": 0,
        "review_rounds_median": None,
        "review_rounds_p90": None,
        "first_pass_approval_ratio": None,
        "awaiting_ruling_passes": 0,
        "ready_to_merged_hours_median": None,
        "rollbacks": 0,
        "reverts": 0,
        "incidents": 0,
    }
    assert merge() == samples


def test_total_recomputes_from_raw_samples_instead_of_project_values() -> None:
    alpha = Samples(
        releases=3,
        operator_gestures=1,
        operator_core=1,
        agent_core=2,
        merged_prs=1,
        reviewed_prs=1,
        rulings=1,
        agent_rulings=1,
        rounds=(1,),
        first_pass=(True,),
        ready_to_merged_hours=(1.234,),
        rollbacks=1,
        reverts=2,
        incidents=1,
    )
    beta = Samples(
        releases=7,
        operator_gestures=2,
        operator_core=2,
        agent_core=5,
        merged_prs=3,
        reviewed_prs=3,
        rulings=2,
        awaiting_ruling_passes=2,
        rounds=(3, 3, 3),
        first_pass=(False, False, False),
        ready_to_merged_hours=(10.0, 10.0, 10.0),
        reverts=1,
        incidents=2,
    )
    combined = merge(alpha, beta)
    assert combined == Samples(
        releases=10,
        operator_gestures=3,
        operator_core=3,
        agent_core=7,
        merged_prs=4,
        reviewed_prs=4,
        rulings=3,
        agent_rulings=1,
        awaiting_ruling_passes=2,
        rollbacks=1,
        reverts=3,
        incidents=3,
        rounds=(1, 3, 3, 3),
        first_pass=(True, False, False, False),
        ready_to_merged_hours=(1.234, 10.0, 10.0, 10.0),
    )
    total = _values(combined)
    assert total["review_rounds_median"] == total["review_rounds_p90"] == 3
    assert total["ready_to_merged_hours_median"] == 10.0
    assert total["operator_gestures_per_release"] == 0.3
    assert total["agent_gesture_ratio"] == 0.7
    assert total["rulings_per_reviewed_pr"] == 0.75
    assert total["first_pass_approval_ratio"] == 0.25
    assert _values(alpha)["review_rounds_median"] == 1
    assert _values(beta)["review_rounds_median"] == 3
    assert merge(combined, Samples()) == combined


def test_records_merges_and_reverts_at_the_window_start_are_included() -> None:
    start = window_start(NOW, 7)
    before = start - timedelta(microseconds=1)
    records = [_record(AttestationKind.RELEASED, at) for at in (before, start)]
    records.extend([_verdict(1, _date(5)), _verdict(2, _date(5))])
    samples = window_samples(
        _facts(records, [_pull(1, at=before), _pull(2, at=start)], [before, start]),
        now=NOW,
        days=7,
    )
    assert samples.releases == samples.operator_gestures == samples.reverts == 1
    assert samples.merged_prs == samples.reviewed_prs == 1


def test_merge_without_verdict_is_measured_only_in_merge_metrics() -> None:
    samples = window_samples(_facts([], [_pull(1, hours=1.236)]), now=NOW, days=7)
    assert samples.merged_prs == 1
    assert samples.reviewed_prs == 0
    assert samples.ready_to_merged_hours == pytest.approx((1.236,))
    assert _values(samples)["ready_to_merged_hours_median"] == 1.24


def test_ratios_round_only_when_values_are_published() -> None:
    values = _values(
        Samples(
            releases=3,
            operator_gestures=1,
            operator_core=2,
            agent_core=1,
            reviewed_prs=3,
            rulings=2,
            first_pass=(True, False, False),
        )
    )
    assert values["operator_gestures_per_release"] == 0.333
    assert values["agent_gesture_ratio"] == 0.333
    assert values["rulings_per_reviewed_pr"] == 0.667
    assert values["first_pass_approval_ratio"] == 0.333


def test_rulings_are_selected_by_review_population_and_actor() -> None:
    records = [_verdict(1, _date(7))]
    records.extend(
        _record(
            AttestationKind.REVIEW_RULING,
            _date(5),
            issuer=issuer,
            data={"repository": REPOSITORY, "pr": pr},
        )
        for issuer, pr in [("agent:test:s1", 1), ("operator", 1), ("agent:test:s1", 2)]
    )
    samples = window_samples(_facts(records, [_pull(1)]), now=NOW, days=7)
    assert samples.rulings == 2
    assert samples.agent_rulings == 1
    assert samples.operator_gestures == 0


def test_only_named_gestures_count_toward_actor_metrics() -> None:
    records = [
        _record(AttestationKind.RELEASED, _date(7), issuer="service:x"),
        _record(AttestationKind.DEPLOYED, _date(7), issuer="agent:test:s1"),
        _record(AttestationKind.RESTORED, _date(7)),
        _record(AttestationKind.FULFILLED, _date(7)),
        _record(AttestationKind.INTEGRATED, _date(7)),
        _record(AttestationKind.GATE_PASSED, _date(7)),
        Record.build(
            kind=RecordKind.CONTRACT,
            project="red-alpha",
            issuer="operator",
            idempotency_key="contract",
            payload={},
            recorded_at=_date(7),
        ),
    ]
    samples = window_samples(_facts(records), now=NOW, days=7)
    assert samples.releases == 1
    assert samples.operator_gestures == samples.operator_core == samples.agent_core == 1
    assert _values(samples)["agent_gesture_ratio"] == 0.5


@pytest.mark.parametrize(
    "field",
    [
        "releases",
        "operator_gestures",
        "agent_core",
        "merged_prs",
        "rulings",
        "rollbacks",
        "reverts",
        "incidents",
    ],
)
def test_each_activity_kind_keeps_a_project_visible(field: str) -> None:
    assert active(Samples(**{field: 1}))


def test_samples_are_frozen_so_published_values_cannot_change_the_observations() -> None:
    samples = Samples(releases=1)
    with pytest.raises(FrozenInstanceError):
        samples.releases = 2
