"""The loop's state, computed from receipts only (spec 2026-09-25, D5 and C1-C3)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from rail.ledger import Record, RecordKind
from rail.reviewer import carry, rounds
from rail.reviewer.verdict import Finding, PreviousAnswer

T0 = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
REPO = "hawkixs/red-alpha"


def _record(kind: str, data: dict, minutes: int) -> Record:
    return Record.build(
        kind=RecordKind.ATTESTATION,
        project="red-alpha",
        issuer="red-rail-reviewer",
        idempotency_key=f"{kind}:{minutes}",
        payload={"kind": kind, "data": data},
        recorded_at=T0 + timedelta(minutes=minutes),
    )


def _finding(n: int, klass: str = "blocker", status: str = "new", pr: int = 7, **over) -> dict:
    return {
        "id": f"F-{pr}-{n}",
        "class": klass,
        "severity": "blocking" if klass == "blocker" else "minor",
        "file": "src/x.py",
        "line": n,
        "title": f"t{n}",
        "status": status,
        "evidence": "e",
        **over,
    }


def verdict(
    minutes: int,
    round_,
    findings: list[dict] | None,
    decision="request_changes",
    pr: int = 7,
    artifact: str | None = "code",
    carry: dict | None = None,
) -> Record:
    data = {
        "sha": f"{minutes:040d}",
        "repository": REPO,
        "pr": pr,
        "verdict": decision,
        "round": round_,
        "artifact": artifact,
    }
    if findings is None:
        data["findings"] = 0  # a verdict recorded before the change: an integer count
    else:
        data["findings"] = findings
    if carry is not None:
        data["carry_forwards"] = carry
    return _record("review_verdict", data, minutes)


def ruling(minutes: int, finding: str, as_: str = "fix", pr: int = 7) -> Record:
    return _record(
        "review_ruling",
        {"repository": REPO, "pr": pr, "finding": finding, "ruling": as_, "decision": "do it"},
        minutes,
    )


# --- C1: the transitions ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdicts", "rulings", "expected"),
    [
        ([], [], ("round", 1)),
        ([verdict(1, 1, [_finding(1)])], [], ("round", 2)),
        (
            [verdict(1, 1, [_finding(1)]), verdict(2, 2, [_finding(1, status="still_open")])],
            [],
            ("round", 3),
        ),
        # round 3 left a blocker open: awaiting a ruling, no judge
        (
            [
                verdict(1, 1, [_finding(1)]),
                verdict(2, 2, [_finding(1)]),
                verdict(3, 3, [_finding(1, status="still_open")]),
            ],
            [],
            ("awaiting_ruling", None),
        ),
        # a new head while awaiting: still awaiting (the awaiting verdict is not judged)
        (
            [
                verdict(1, 1, [_finding(1)]),
                verdict(2, 2, [_finding(1)]),
                verdict(3, 3, [_finding(1, status="still_open")]),
                verdict(4, "awaiting_ruling", [_finding(1, status="still_open")]),
            ],
            [],
            ("awaiting_ruling", None),
        ),
        # every open blocker ruled after the last judged verdict: the closure check
        (
            [
                verdict(1, 1, [_finding(1)]),
                verdict(2, 2, [_finding(1)]),
                verdict(3, 3, [_finding(1, status="still_open")]),
            ],
            [ruling(5, "F-7-1")],
            ("closure", None),
        ),
        # a ruling recorded BEFORE the last judged verdict does not count
        (
            [
                verdict(1, 1, [_finding(1)]),
                verdict(2, 2, [_finding(1)]),
                verdict(4, 3, [_finding(1, status="still_open")]),
            ],
            [ruling(3, "F-7-1")],
            ("awaiting_ruling", None),
        ),
        # closure failed: awaiting again until a new ruling
        (
            [
                verdict(1, 1, [_finding(1)]),
                verdict(2, 2, [_finding(1)]),
                verdict(3, 3, [_finding(1, status="still_open")]),
                verdict(6, "closure", [_finding(1, status="still_open")]),
            ],
            [ruling(5, "F-7-1")],
            ("awaiting_ruling", None),
        ),
        # approved at round 3, then a new head: round 3 again, never 4 (Review Focus 1)
        (
            [
                verdict(1, 1, [_finding(1)]),
                verdict(2, 2, [_finding(1)]),
                verdict(3, 3, [_finding(1, status="fixed")], decision="approve"),
            ],
            [],
            ("round", 3),
        ),
        # verdicts recorded before the change count as judged rounds
        ([verdict(1, None, None), verdict(2, None, None)], [], ("round", 3)),
        # only a body-derived accounting gap open, no ruling on it: another judged round 3,
        # never a no-judge closure (Ruling 21, NB2)
        (
            [
                verdict(1, 1, [_finding(1)]),
                verdict(2, 2, [_finding(1)]),
                verdict(
                    3,
                    3,
                    [
                        {
                            "id": "F-7-2",
                            "class": "blocker",
                            "severity": "blocking",
                            "file": carry.WHERE,
                            "title": "CF-5-1 not accounted for",
                            "status": "still_open",
                            "evidence": "e",
                        }
                    ],
                ),
            ],
            [],
            ("round", 3),
        ),
        # a ruling recorded on the only open blocker, but it is body-derived: still no
        # no-judge closure (Ruling 23) — the ruling must land on a real, open blocker
        (
            [
                verdict(1, 1, [_finding(1)]),
                verdict(2, 2, [_finding(1)]),
                verdict(
                    3,
                    3,
                    [
                        {
                            "id": "F-7-2",
                            "class": "blocker",
                            "severity": "blocking",
                            "file": carry.WHERE,
                            "title": "CF-5-1 not accounted for",
                            "status": "still_open",
                            "evidence": "e",
                        }
                    ],
                ),
            ],
            [ruling(5, "F-7-2")],
            ("round", 3),
        ),
    ],
)
def test_next_step(verdicts, rulings, expected) -> None:
    assert rounds.next_step(rounds.loop_state(verdicts, rulings)) == expected


def test_old_verdicts_have_no_findings_list() -> None:
    state = rounds.loop_state([verdict(1, None, None)], [])
    assert state.judged == 1 and not state.has_findings_list and state.findings == ()


# --- D1: artifact type ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["docs/specs/2026-09-25-x.md"], "spec_plan"),
        (["docs/plans/p.md", "docs/receipts/r.json"], "spec_plan"),
        (["docs/specs/s.md", "src/rail/x.py"], "code"),
        (["README.md"], "code"),
        ([], "code"),
    ],
)
def test_artifact_of(paths, expected) -> None:
    assert rounds.artifact_of(paths, ("docs/receipts/*",)) == expected


# --- D2: classes, ids and statuses --------------------------------------------------------


def _f(**over) -> Finding:
    base = dict(severity="blocking", file="src/x.py", line=2, title="t", evidence="e")
    return Finding.model_validate({**base, **over})


@pytest.mark.parametrize(
    ("artifact", "klass", "severity", "expected"),
    [
        ("code", "carry_forward", "blocking", "blocker"),
        ("code", "carry_forward", "important", "note"),
        ("code", None, "blocking", "blocker"),
        ("code", None, "minor", "note"),
        ("spec_plan", None, "blocking", "blocker"),
        # Q83: only a real implementation gap the judge names becomes a carry-forward
        ("spec_plan", None, "important", "note"),
        ("spec_plan", "note", "minor", "note"),
        ("spec_plan", "carry_forward", "minor", "carry_forward"),
        ("spec_plan", "carry_forward", "blocking", "carry_forward"),
        ("code", "note", "blocking", "blocker"),
        ("spec_plan", "note", "blocking", "blocker"),
    ],
)
def test_enforce_class(artifact, klass, severity, expected) -> None:
    f = _f(severity=severity, **({"class": klass} if klass else {}))
    assert rounds.enforce_class(f, artifact).klass == expected


def test_assign_numbers_new_findings_and_statuses_old_ones() -> None:
    state = rounds.loop_state([verdict(1, 1, [_finding(1), _finding(2, klass="note")])], [])
    new = [_f(id="F-7-1", title="again"), _f(title="fresh"), _f(id="F-7-99", title="invented")]
    previous = [
        PreviousAnswer(id="F-7-2", status="fixed"),
        PreviousAnswer(id="F-9-1", status="fixed"),
    ]
    out = rounds.assign(new, previous, state, pr=7, artifact="code")
    by_id = {f.id: f for f in out}
    assert by_id["F-7-1"].status == "still_open"  # repeated by the judge
    assert by_id["F-7-2"].status == "fixed"  # answered fixed
    assert by_id["F-7-3"].title == "fresh" and by_id["F-7-3"].status == "new"
    assert by_id["F-7-4"].title == "invented"  # an unknown id is a new finding (Review Focus 4)
    assert len(out) == 4


def test_assign_floors_new_ids_above_a_mechanical_finding() -> None:  # C2
    state = rounds.loop_state([verdict(1, 1, [_finding(1)])], [])
    out = rounds.assign([_f(title="fresh")], [], state, pr=7, artifact="code", floor=2)
    assert [(f.id, f.title) for f in out if f.status == "new"] == [("F-7-3", "fresh")]


def test_unruled_ignores_a_mechanical_blocker() -> None:  # M8
    mech = {
        "id": "F-7-2",
        "class": "blocker",
        "severity": "blocking",
        "file": carry.WHERE,
        "title": "CF-5-1 not accounted for",
        "status": "new",
        "evidence": "e",
    }
    state = rounds.loop_state([verdict(1, 3, [_finding(1), mech])], [])
    assert [f.id for f in rounds.unruled(state)] == ["F-7-1"]


def test_unruled_keeps_a_not_addressed_blocker() -> None:  # NB1b
    accounting_gap = {
        "id": "F-7-2",
        "class": "blocker",
        "severity": "blocking",
        "file": carry.WHERE,
        "title": "CF-5-1 not accounted for",
        "status": "new",
        "evidence": "e",
    }
    not_addressed = {
        "id": "F-7-3",
        "class": "blocker",
        "severity": "blocking",
        "file": carry.WHERE,
        "title": "CF-5-1 not addressed",
        "status": "new",
        "evidence": "e",
    }
    state = rounds.loop_state([verdict(1, 3, [accounting_gap, not_addressed])], [])
    assert [f.id for f in rounds.unruled(state)] == ["F-7-3"]


def test_an_unanswered_open_finding_stays_still_open() -> None:  # C3
    state = rounds.loop_state([verdict(1, 1, [_finding(1)])], [])
    out = rounds.assign([], [], state, pr=7, artifact="code")
    assert [(f.id, f.status) for f in out] == [("F-7-1", "still_open")]
    assert not rounds.approves(out)


def test_append_new_leaves_the_list_and_numbers_the_extras() -> None:
    base = [_f(id="F-7-4", status="new")]
    out = rounds.append_new(base, [_f(title="x")], pr=7, artifact="code")
    assert [(f.id, f.status) for f in out] == [("F-7-4", "new"), ("F-7-5", "new")]


def test_apply_rulings_reclassifies_carry_forward_only() -> None:
    state = rounds.loop_state(
        [verdict(1, 3, [_finding(1), _finding(2)])],
        [ruling(5, "F-7-1", as_="carry_forward"), ruling(6, "F-7-2", as_="fix")],
    )
    out = rounds.apply_rulings(state.findings, state.rulings)
    assert [(f.id, f.klass, f.status) for f in out] == [
        ("F-7-1", "carry_forward", "ruled"),
        ("F-7-2", "blocker", "new"),
    ]


def test_a_fixed_finding_stays_fixed() -> None:
    state = rounds.loop_state([verdict(1, 1, [_finding(1, status="fixed")])], [])
    out = rounds.assign([], [], state, pr=7, artifact="code")
    assert out[0].status == "fixed" and rounds.approves(out)


def test_a_fixed_finding_regresses_when_the_judge_repeats_it() -> None:
    state = rounds.loop_state([verdict(1, 1, [_finding(1, status="fixed")])], [])
    new = [_f(id="F-7-1", title="again")]
    out = rounds.assign(new, [], state, pr=7, artifact="code")
    assert out[0].id == "F-7-1" and out[0].status == "still_open"
    assert not rounds.approves(out)


# --- C2: round 3 does not hunt -------------------------------------------------------------

DELTA = (
    "diff --git a/src/x.py b/src/x.py\n--- a/src/x.py\n+++ b/src/x.py\n"
    "@@ -10,2 +10,3 @@\n ctx\n+new line\n ctx\n"
)


RENAME_DELTA = (
    "diff --git a/src/old.py b/src/new.py\n--- a/src/old.py\n+++ b/src/new.py\n"
    "@@ -1,2 +1,3 @@\n ctx\n+new line\n ctx\n"
)


def test_changed_lines_reads_new_side_hunks() -> None:
    assert rounds.changed_lines(DELTA) == {"src/x.py": {10, 11, 12}}


def test_changed_lines_uses_the_new_side_path_on_a_rename() -> None:
    assert "src/new.py" in rounds.changed_lines(RENAME_DELTA)


def test_demote_outside_keeps_findings_in_the_delta_only() -> None:
    inside = _f(line=11, **{"class": "blocker"})
    outside = _f(line=40, **{"class": "blocker"})
    other_file = _f(file="src/y.py", line=1, **{"class": "blocker"})
    no_line_in_file = _f(line=None, **{"class": "blocker"})
    known = _f(id="F-7-1", line=40, **{"class": "blocker"}, status="still_open")
    invented = _f(id="F-7-99", line=40, **{"class": "blocker"})
    out = rounds.demote_outside(
        [inside, outside, other_file, no_line_in_file, known, invented],
        DELTA,
        "code",
        known={"F-7-1"},
    )
    assert [f.klass for f in out] == ["blocker", "note", "note", "blocker", "blocker", "note"]
    spec = rounds.demote_outside([outside], DELTA, "spec_plan")
    assert spec[0].klass == "carry_forward"


def test_demote_outside_without_a_delta_demotes_every_new_finding() -> None:  # Review Focus 2
    out = rounds.demote_outside([_f(line=11, **{"class": "blocker"})], None, "code")
    assert out[0].klass == "note"


def test_demote_outside_classifies_before_skipping() -> None:
    unclassified = _f(line=40)  # klass None, severity blocking: enforce_class makes it "blocker"
    out = rounds.demote_outside([unclassified], DELTA, "code")
    assert out[0].klass == "note"  # then demoted: line 40 is outside the delta


def test_demote_outside_skip_classifies_without_demoting() -> None:
    unclassified = _f(line=40)  # outside the delta, but skip=True bypasses demotion entirely
    out = rounds.demote_outside([unclassified], DELTA, "code", skip=True)
    assert out[0].klass == "blocker"


# --- D11: open carry-forwards --------------------------------------------------------------


def test_a_no_verdict_pass_moves_neither_the_round_nor_last_judged() -> None:  # I3
    verdicts = [
        verdict(1, 1, [_finding(1)]),
        verdict(2, "no_verdict", [_finding(1, status="still_open")]),
        verdict(3, "no_verdict", [_finding(1, status="still_open")]),
    ]
    state = rounds.loop_state(verdicts, [])
    assert state.judged == 1 and state.last_judged is verdicts[0]
    assert rounds.next_step(state) == ("round", 2)


def test_a_mechanical_finding_never_opens_a_carry_forward_of_its_own() -> None:  # C1
    gap = _finding(
        2, klass="carry_forward", status="ruled", file=carry.WHERE, title="CF-5-1 not addressed"
    )
    verdicts = [
        verdict(1, 1, [_finding(1, klass="carry_forward", pr=5)], "approve", 5, "spec_plan"),
        verdict(3, "closure", [gap], "approve", carry={"addressed": [], "deferred": ["CF-5-1"]}),
    ]
    rulings = [ruling(2, "F-7-2", as_="carry_forward")]
    assert rounds.open_carry_forwards(verdicts, rulings, repository=REPO) == ["CF-5-1"]


def test_confirmed_addressed_remembers_an_earlier_confirmation() -> None:  # I1
    confirmed = verdict(1, 1, [], carry={"addressed": ["CF-5-1"], "deferred": []})
    state = rounds.loop_state([confirmed], [])
    claimed = ["CF-5-1", "CF-5-2", "CF-5-3"]
    assert rounds.confirmed_addressed(state, claimed, {"CF-5-2": "fixed"}) == ["CF-5-1", "CF-5-2"]
    assert rounds.confirmed_addressed(state, claimed, {"CF-5-1": "still_open"}) == []


def test_new_ids_start_above_every_id_the_pull_request_ever_used() -> None:  # M4
    dropped = verdict(1, 1, [_finding(9, status="fixed")])
    state = rounds.loop_state([dropped, verdict(2, 2, [_finding(1, status="still_open")])], [])
    assert state.highest_id == 9
    base = [Finding.model_validate(_finding(1))]
    out = rounds.append_new(
        base,
        [Finding.model_validate(_finding(2, id=None))],
        pr=7,
        artifact="code",
        floor=state.highest_id,
    )
    assert out[-1].id == "F-7-10"


def test_open_carry_forwards_across_pull_requests() -> None:
    spec_pr = verdict(
        1,
        1,
        [_finding(1, klass="carry_forward", pr=5), _finding(2, klass="carry_forward", pr=5)],
        decision="approve",
        pr=5,
        artifact="spec_plan",
    )
    unapproved = verdict(
        2, 1, [_finding(1, klass="carry_forward", pr=6)], pr=6, artifact="spec_plan"
    )
    code_pr = verdict(
        3,
        1,
        [],
        decision="approve",
        pr=8,
        artifact="code",
        carry={"addressed": ["CF-5-1"], "deferred": ["CF-5-2"]},
    )
    ruled = ruling(4, "F-9-3", as_="carry_forward", pr=9)
    approved_9 = verdict(5, "closure", [], decision="approve", pr=9)
    fixed_cf = verdict(
        6,
        1,
        [_finding(1, klass="carry_forward", pr=10, status="fixed")],
        decision="approve",
        pr=10,
        artifact="spec_plan",
    )
    open_ = rounds.open_carry_forwards(
        [spec_pr, unapproved, code_pr, approved_9, fixed_cf], [ruled], repository=REPO
    )
    assert open_ == ["CF-5-2", "CF-9-3"]  # the fixed CF-10-1 contributes nothing
    assert rounds.open_carry_forwards(
        [spec_pr, unapproved, code_pr, approved_9, fixed_cf],
        [ruled],
        repository=REPO,
        excluding_pr=9,
    ) == ["CF-5-2"]


def test_carry_forwards_of_defaults_when_absent() -> None:
    v = verdict(1, 1, [], decision="approve", pr=5, artifact="code")
    cf = rounds.carry_forwards_of(v)
    assert cf.addressed == () and cf.deferred == ()


def test_carry_forwards_of_validates_the_shape() -> None:
    v = verdict(
        1, 1, [], decision="approve", pr=5, artifact="code", carry={"addressed": ["CF-5-1"]}
    )
    cf = rounds.carry_forwards_of(v)
    assert cf.addressed == ("CF-5-1",) and cf.deferred == ()

    not_a_mapping = verdict(2, 1, [], decision="approve", pr=8, artifact="code", carry="bogus")
    with pytest.raises(ValidationError):
        rounds.carry_forwards_of(not_a_mapping)

    addressed_as_a_string = verdict(
        3, 1, [], decision="approve", pr=9, artifact="code", carry={"addressed": "CF-5-1"}
    )
    with pytest.raises(ValidationError):
        rounds.carry_forwards_of(addressed_as_a_string)
