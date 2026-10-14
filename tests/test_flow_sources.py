"""Synthetic receipts and journals pin evidence rules without host or network reads."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rail.flow import FlowPull, HolderEvidence, ReviewFacts
from rail.flow_sources import (
    MAX_QUEUE_BYTES,
    QueueReadError,
    QueueTurn,
    holder_evidence,
    read_queue,
    review_facts,
)
from rail.ledger import AttestationKind, Record, RecordKind
from rail.reviewer.config import RepositoryConfig
from rail.reviewer.rounds import loop_state

BASE = datetime(2026, 10, 14, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return BASE + timedelta(minutes=minutes)


def _pull() -> FlowPull:
    return FlowPull("example/repo", 7, "a" * 40, "b" * 40, _at(0), _at(60))


def _receipt(minutes: int, **data: object) -> Record:
    """Build real digests so duplicate receipt evidence cannot inflate round counts."""
    return Record.build(
        kind=RecordKind.ATTESTATION,
        project="red-example",
        issuer="example-reviewer",
        idempotency_key=f"verdict:{minutes}",
        payload={
            "kind": AttestationKind.REVIEW_VERDICT.value,
            "data": {"repository": "example/repo", "pr": 7, "sha": "a" * 40, **data},
        },
        recorded_at=_at(minutes),
    )


def _entry(pr: int, sha: str, minutes: int, **extra: object) -> dict[str, object]:
    return {"pr": pr, "sha": sha, "merged_at": _at(minutes).isoformat(), **extra}


def _journal(tmp_path: Path, merged: list[dict[str, object]], **extra: object) -> Path:
    path = tmp_path / "journal.json"
    path.write_text(json.dumps({"merged": merged, **extra}), encoding="utf-8")
    return path


def test_rounds_match_loop_state_across_heads() -> None:
    records = [
        _receipt(i, round=round_, sha=str(i) * 40)
        for i, round_ in enumerate(
            (None, 1, 2, 3, 3, "mechanical", "closure", "awaiting_ruling", "no_verdict"),
            start=1,
        )
    ]
    records.append(_receipt(10, round=3, carried_from="c" * 40))
    assert loop_state(records, []).judged == 5
    assert review_facts(list(reversed(records)) + [records[3]], _pull()) == ReviewFacts(
        5, True, None
    )
    # Counting needs neither provider evidence nor valid findings to parse.
    assert review_facts([_receipt(1, findings="unparsed")], _pull()).rounds == 1


def test_receipts_match_repository_pr_head_and_pre_merge_time() -> None:
    records = [
        _receipt(1, round=1, sha="c" * 40, check_run_id=11),
        _receipt(2, round=2, check_run_id=12),
        _receipt(60, round=3, check_run_id="13", carried_from="c" * 40, source_check_run_id=999),
        _receipt(59, repository="example/other", check_run_id=100),
        _receipt(59, pr=8, check_run_id=101),
        _receipt(61, round=3, check_run_id=102),
        _receipt(62, round=3, carried_from="d" * 40, check_run_id=103),
    ]
    other_kind = Record.build(
        kind=RecordKind.ATTESTATION,
        project="red-example",
        issuer="example-reviewer",
        idempotency_key="tested:head",
        payload={"kind": AttestationKind.GATE_PASSED.value, "data": records[2].data},
        recorded_at=_at(60),
    )
    assert review_facts(list(reversed(records)) + [records[1], other_kind], _pull()) == (
        ReviewFacts(2, True, 13)
    )
    assert review_facts(records[:2], _pull()) == ReviewFacts(2, False, 12)
    assert review_facts(records[:1], _pull()) == ReviewFacts(1, None, None)
    # Equal recording times are resolved deterministically by digest.
    tied = [_receipt(3, check_run_id=14), _receipt(3, check_run_id=15)]
    winner = max(tied, key=lambda r: r.digest)
    assert review_facts(list(reversed(tied)), _pull()).check_run_id == winner.data["check_run_id"]


@pytest.mark.parametrize(
    ("check_id", "expected"),
    [
        (None, None),
        (True, None),
        (False, None),
        (0, None),
        (-1, None),
        (1.5, None),
        ("bad", None),
        ("0", None),
        ("-2", None),
        ("1.5", None),
        (" 2 ", None),
        (17, 17),
        ("18", 18),
    ],
)
def test_unread_ledger_is_not_empty_history(check_id: object, expected: int | None) -> None:
    assert review_facts(None, _pull()) == ReviewFacts(None, None, None)
    assert review_facts([], _pull()) == ReviewFacts(0, None, None)
    assert review_facts([_receipt(1)], _pull()) == ReviewFacts(1, False, None)
    assert review_facts(
        [
            _receipt(
                2, round=3, carried_from="c" * 40, source_check_run_id=999, check_run_id=check_id
            )
        ],
        _pull(),
    ) == ReviewFacts(0, True, expected)


def test_queue_prefers_holder_since_and_marks_previous_merge_fallback(tmp_path: Path) -> None:
    path = _journal(
        tmp_path,
        [
            _entry(6, "c" * 40, -60),
            _entry(7, "a" * 40, 61, holder_since=_at(40).isoformat()),
        ],
    )
    turns = read_queue(path)
    assert turns == (
        QueueTurn(6, "c" * 40, _at(-60), None),
        QueueTurn(7, "a" * 40, _at(61), _at(40)),
    )
    evidence = holder_evidence(turns, _pull())
    assert evidence == HolderEvidence(_at(40), _at(60), False)
    assert (evidence.merged_at - evidence.started_at).total_seconds() / 60 == 20
    path = _journal(tmp_path, [_entry(6, "c" * 40, -60), _entry(7, "a" * 40, 61)])
    fallback = holder_evidence(read_queue(path), _pull())
    assert fallback == HolderEvidence(_at(-60), _at(60), True)
    assert (fallback.merged_at - fallback.started_at).total_seconds() / 60 == 120
    # The latest matching turn wins, and its immediate predecessor supplies the fallback.
    path = _journal(
        tmp_path,
        [
            _entry(7, "a" * 40, 20, holder_since=_at(10).isoformat()),
            _entry(8, "d" * 40, 30),
            _entry(7, "a" * 40, 61),
        ],
    )
    assert holder_evidence(read_queue(path), _pull()) == HolderEvidence(_at(30), _at(60), True)
    path = _journal(tmp_path, [_entry(7, "a" * 40, 61, holder_since=_at(60).isoformat())])
    assert holder_evidence(read_queue(path), _pull()) == HolderEvidence(_at(60), _at(60), False)


def test_queue_first_turn_wrong_head_and_current_holder_are_unknown(tmp_path: Path) -> None:
    path = _journal(
        tmp_path,
        [_entry(7, "a" * 40, 60)],
        holder={"pr": 7, "sha": "a" * 40, "holder_since": _at(10).isoformat()},
    )
    assert holder_evidence(read_queue(path), _pull()) is None
    path = _journal(
        tmp_path,
        [
            _entry(6, "c" * 40, 20),
            _entry(7, "d" * 40, 60),
            _entry(8, "a" * 40, 61),
        ],
    )
    assert holder_evidence(read_queue(path), _pull()) is None
    assert holder_evidence((), _pull()) is None
    path = _journal(tmp_path, [_entry(7, "a" * 40, 62, holder_since=_at(61).isoformat())])
    assert holder_evidence(read_queue(path), _pull()) is None
    path = _journal(tmp_path, [_entry(6, "c" * 40, 61), _entry(7, "a" * 40, 62)])
    assert holder_evidence(read_queue(path), _pull()) is None
    assert read_queue(_journal(tmp_path, [], holder="ignored")) == ()


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "directory",
        "oversized",
        "utf8",
        "json",
        "top_list",
        "missing_merged",
        "merged_mapping",
        "entry_string",
        "missing_pr",
        "bool_pr",
        "zero_pr",
        "negative_pr",
        "string_pr",
        "missing_sha",
        "empty_sha",
        "numeric_sha",
        "missing_date",
        "bad_date",
        "naive_date",
        "non_utc_date",
        "bad_holder_date",
        "naive_holder_date",
        "non_utc_holder_date",
        "reverse_order",
    ],
)
def test_queue_bad_order_bad_dates_and_size_are_failed_reads(tmp_path: Path, case: str) -> None:
    assert MAX_QUEUE_BYTES == 8 * 1024 * 1024
    entry = _entry(7, "journal-marker", 60)
    obj: object = {"merged": [entry], "unrelated": "journal-marker"}
    bad_fields = {
        "bool_pr": ("pr", True),
        "zero_pr": ("pr", 0),
        "negative_pr": ("pr", -1),
        "string_pr": ("pr", "7"),
        "empty_sha": ("sha", ""),
        "numeric_sha": ("sha", 1),
        "bad_date": ("merged_at", "journal-marker"),
        "naive_date": ("merged_at", "2026-10-14T01:00:00"),
        "non_utc_date": ("merged_at", "2026-10-14T01:00:00+01:00"),
        "bad_holder_date": ("holder_since", "journal-marker"),
        "naive_holder_date": ("holder_since", "2026-10-14T00:00:00"),
        "non_utc_holder_date": ("holder_since", "2026-10-14T00:00:00+01:00"),
    }
    if case in bad_fields:
        key, value = bad_fields[case]
        entry[key] = value
    elif case in ("missing_pr", "missing_sha", "missing_date"):
        entry.pop({"missing_pr": "pr", "missing_sha": "sha", "missing_date": "merged_at"}[case])
    elif case == "top_list":
        obj = []
    elif case == "missing_merged":
        obj = {"unrelated": "journal-marker"}
    elif case == "merged_mapping":
        obj = {"merged": {}}
    elif case == "entry_string":
        obj = {"merged": ["journal-marker"]}
    elif case == "reverse_order":
        obj = {"merged": [entry, _entry(8, "journal-marker", 59)]}
    path = tmp_path / "private-journal-marker.json"
    raw = json.dumps(obj).encode("utf-8")
    if case == "oversized":
        raw += b" " * (MAX_QUEUE_BYTES + 1 - len(raw))
    elif case == "utf8":
        raw = b"journal-marker\xff"
    elif case == "json":
        raw = b"journal-marker{"
    if case == "directory":
        path.mkdir()
    elif case != "missing":
        path.write_bytes(raw)
    with pytest.raises(QueueReadError) as exc:
        read_queue(path)
    message = str(exc.value)
    assert str(path) not in message
    assert path.name not in message
    assert "journal-marker" not in message
    assert raw.decode("utf-8", errors="replace") not in message


def test_merge_queue_config_is_optional_and_expands_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = RepositoryConfig(slug="example/repo", path=tmp_path)
    assert legacy.merge_queue is None
    assert RepositoryConfig(slug="example/repo", path=tmp_path, merge_queue=None) == legacy

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("configuration must not open or resolve the queue")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    config = RepositoryConfig(slug="example/repo", path=tmp_path, merge_queue="~/journal.json")
    assert config.merge_queue == tmp_path / "journal.json"
    relative = RepositoryConfig(slug="example/repo", path=tmp_path, merge_queue="journal.json")
    assert relative.merge_queue == Path("journal.json")
