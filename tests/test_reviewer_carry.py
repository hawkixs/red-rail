"""A code pull request's accounting of open carry-forwards (spec 2026-09-25, D11)."""

from rail.reviewer import carry

BODY = """## What
x

## Carry-forwards

- CF-50-2: addressed
- CF-50-3: deferred: needs the brain registry first
CF-50-4: deferred:
CF-50-5 addressed
- CF-60-1: addressed

## Evidence
y
"""


def test_parse_section_reads_only_its_own_section() -> None:
    section = carry.parse_section(BODY)
    assert section["CF-50-2"].status == "addressed"
    assert section["CF-50-3"] == carry.Accounting("deferred", "needs the brain registry first")
    assert section["CF-50-4"] == carry.Accounting("deferred", "")
    assert "CF-50-5" not in section  # no colon: not a line of the format
    assert carry.parse_section("## What\nCF-1-1: addressed\n") == {}


def test_mechanical_blockers_name_the_expected_format() -> None:  # Review Focus 3
    section = carry.parse_section(BODY)
    blockers = carry.mechanical_blockers(["CF-50-2", "CF-50-4", "CF-50-5", "CF-50-9"], section)
    titles = [b.title for b in blockers]
    assert titles == [
        "CF-50-4 deferred without a reason",
        "CF-50-5 not accounted for",
        "CF-50-9 not accounted for",
    ]
    assert all(b.klass == "blocker" and b.severity == "blocking" for b in blockers)
    assert carry.LINE_FORMAT in blockers[1].evidence


def test_addressed_deferred_and_strangers() -> None:
    section = carry.parse_section(BODY)
    open_ids = ["CF-50-2", "CF-50-3", "CF-50-4"]
    assert carry.addressed(open_ids, section) == ["CF-50-2"]
    assert carry.deferred(open_ids, section) == ["CF-50-3"]
    assert carry.strangers(open_ids, section) == ["CF-60-1"]


def test_no_open_carry_forward_needs_no_section() -> None:
    assert carry.mechanical_blockers([], {}) == []


def test_mechanical_blockers_are_recomputed_each_round() -> None:
    old = [
        b.model_copy(update={"id": f"F-8-{n}"})
        for n, b in enumerate(carry.mechanical_blockers(["CF-5-1", "CF-5-2"], {}), start=1)
    ]
    now = carry.mechanical_blockers(["CF-5-2", "CF-5-3"], {})
    kept, fresh = carry.reconcile(old, now)
    assert [(f.id, f.status) for f in kept] == [("F-8-1", "fixed"), ("F-8-2", "still_open")]
    assert [f.title for f in fresh] == ["CF-5-3 not accounted for"]
    assert all(carry.is_mechanical(f) for f in kept + fresh)
