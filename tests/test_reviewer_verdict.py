"""The verdict as evidence: ids, classes, statuses, rounds, and a payload that always fits."""

import json

import pytest
from pydantic import ValidationError

from rail.reviewer.verdict import (
    RECEIPT_BUDGET,
    CarryForwards,
    Finding,
    PreviousAnswer,
    ReviewVerdict,
    receipt_findings,
)


def _finding(**over) -> Finding:
    base = dict(severity="blocking", file="src/x.py", line=3, title="bug", evidence="e")
    return Finding.model_validate({**base, **over})


def _canonical(value) -> int:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return len(canonical.encode())


def test_a_finding_reads_its_class_from_the_json_key_class() -> None:
    f = Finding.model_validate(
        {"severity": "minor", "file": "a", "title": "t", "evidence": "e", "class": "note"}
    )
    assert f.klass == "note" and f.status == "new" and f.id is None


def test_finding_ids_follow_the_pattern() -> None:
    assert _finding(id="F-7-1").id == "F-7-1"
    with pytest.raises(ValidationError):
        _finding(id="X-7-1")


def test_an_open_blocker_is_a_blocker_class_or_an_unclassified_blocking_severity() -> None:
    verdict = ReviewVerdict(
        verdict="request_changes",
        summary="s",
        findings=[
            _finding(id="F-7-1", klass="blocker"),
            _finding(id="F-7-2", klass="blocker", status="fixed"),
            _finding(id="F-7-3", klass="note", severity="blocking"),
            _finding(id="F-7-4"),  # unclassified, blocking
        ],
    )
    assert [f.id for f in verdict.open_blockers] == ["F-7-1", "F-7-4"]
    assert verdict.blocking


def test_the_attestation_carries_round_artifact_and_every_finding() -> None:
    verdict = ReviewVerdict(
        verdict="request_changes",
        summary="s",
        findings=[_finding(id="F-7-1", klass="blocker")],
        mode="deep",
        providers=("agy",),
        round=2,
        artifact="code",
        carry_forwards=CarryForwards(addressed=("CF-5-1",), deferred=("CF-5-2",)),
    )
    data = verdict.as_attestation_data(sha="a" * 40, check_run_id=9, repository="o/r", pr=7)
    assert data["round"] == 2 and data["artifact"] == "code"
    assert data["finding_count"] == 1 and data["blocking"] is True
    assert data["findings"] == [
        {
            "id": "F-7-1",
            "class": "blocker",
            "severity": "blocking",
            "file": "src/x.py",
            "line": 3,
            "title": "bug",
            "status": "new",
            "evidence": "e",
        }
    ]
    assert data["carry_forwards"] == {"addressed": ["CF-5-1"], "deferred": ["CF-5-2"]}


def test_a_hundred_maximal_findings_fit_and_none_is_dropped() -> None:
    findings = [
        _finding(
            id=f"F-7-{n}",
            klass=("blocker", "carry_forward", "note")[n % 3],
            file="d/" * 250,
            title="t" * 200,
            evidence="é" * 2000,
        )
        for n in range(1, 101)
    ]
    kept = receipt_findings(findings)
    assert len(kept) == 100
    assert _canonical(kept) <= RECEIPT_BUDGET
    assert all(len(k["file"]) <= 200 for k in kept)
    assert kept[0]["file"].startswith("…")


def test_trimming_drops_evidence_notes_first() -> None:
    findings = [
        _finding(id="F-7-1", klass="blocker", evidence="b" * 300),
        _finding(id="F-7-2", klass="note", evidence="n" * 300),
    ]
    kept = receipt_findings(findings, budget=_canonical(receipt_findings(findings)) - 1)
    assert kept[1]["evidence"] == "" and kept[0]["evidence"] == "b" * 300


def test_a_previous_answer_names_a_finding_or_a_carry_forward() -> None:
    assert PreviousAnswer(id="CF-5-1", status="fixed").id == "CF-5-1"
    with pytest.raises(ValidationError):
        PreviousAnswer(id="F-5", status="fixed")


def test_budget_mode_is_gone() -> None:
    with pytest.raises(ValidationError):
        ReviewVerdict(verdict="approve", summary="s", mode="budget")
