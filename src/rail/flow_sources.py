"""Read local flow evidence without treating missing history as proof of absence.

Queue owners should persist holder_since in each merged entry: the predecessor fallback
includes any empty-queue interval. This reader never writes the journal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rail.flow import FlowPull, HolderEvidence, ReviewFacts
from rail.ledger import AttestationKind, Record
from rail.reviewer import rounds

MAX_QUEUE_BYTES = 8 * 1024 * 1024


def review_facts(records: list[Record] | None, pull: FlowPull) -> ReviewFacts:
    """Count judged receipts across heads; final-head metadata stops at the merge."""
    if records is None:
        return ReviewFacts(None, None, None)
    unique = {
        record.digest: record
        for record in records
        if record.attestation is AttestationKind.REVIEW_VERDICT
        and record.data.get("repository") == pull.repository
        and record.data.get("pr") == pull.number
        and record.recorded_at <= pull.merged_at
    }
    ordered = sorted(unique.values(), key=lambda record: (record.recorded_at, record.digest))
    judged = sum(
        record.data.get("round") in rounds.JUDGED_ROUNDS and not record.data.get("carried_from")
        for record in ordered
    )
    final = next(
        (record for record in reversed(ordered) if record.data.get("sha") == pull.head_sha),
        None,
    )
    if final is None:
        return ReviewFacts(judged, None, None)
    raw_id = final.data.get("check_run_id")
    check_id = None
    if isinstance(raw_id, int) and not isinstance(raw_id, bool) and raw_id > 0:
        check_id = raw_id
    elif isinstance(raw_id, str) and raw_id.isascii() and raw_id.isdecimal():
        try:
            parsed = int(raw_id)
        except ValueError:
            pass  # An unbounded legacy id cannot erase the receipt's other evidence.
        else:
            if parsed > 0:
                check_id = parsed
    return ReviewFacts(judged, bool(final.data.get("carried_from")), check_id)


@dataclass(frozen=True, slots=True)
class QueueTurn:
    """Retain ordered historical turns rather than reconstructing them from current state."""

    pr: int
    sha: str
    merged_at: datetime
    holder_since: datetime | None


class QueueReadError(ValueError):
    """A failed journal read carries no private path or journal contents."""


def _queue_date(value: object) -> datetime:
    """Require explicit UTC timestamps so journal ordering is unambiguous."""
    if not isinstance(value, str):
        raise ValueError("invalid queue timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("invalid queue timestamp")
    return parsed.astimezone(UTC)


def _reject_constant(value: str) -> None:
    """Nonfinite constants are not JSON and must not turn a corrupt read into evidence."""
    raise ValueError("invalid queue JSON")


def read_queue(path: Path) -> tuple[QueueTurn, ...]:
    """Read the entire bounded journal once; corrupt or partial evidence fails closed."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_QUEUE_BYTES + 1)
        if len(raw) > MAX_QUEUE_BYTES:
            raise ValueError("queue too large")
        journal = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        if not isinstance(journal, dict) or not isinstance(journal.get("merged"), list):
            raise ValueError("invalid queue shape")
        turns: list[QueueTurn] = []
        for entry in journal["merged"]:
            if not isinstance(entry, dict):
                raise ValueError("invalid queue turn")
            pr, sha = entry["pr"], entry["sha"]
            if not isinstance(pr, int) or isinstance(pr, bool) or pr <= 0:
                raise ValueError("invalid queue PR")
            if not isinstance(sha, str) or not sha:
                raise ValueError("invalid queue SHA")
            merged_at = _queue_date(entry["merged_at"])
            holder_since = entry.get("holder_since")
            turn = QueueTurn(
                pr,
                sha,
                merged_at,
                _queue_date(holder_since) if holder_since is not None else None,
            )
            if turns and merged_at < turns[-1].merged_at:
                raise ValueError("invalid queue order")
            turns.append(turn)
        return tuple(turns)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise QueueReadError("queue journal could not be read") from None


def holder_evidence(turns: tuple[QueueTurn, ...], pull: FlowPull) -> HolderEvidence | None:
    """Use the latest matching turn's start and the GitHub merge as the endpoint."""
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if turn.pr != pull.number or turn.sha != pull.head_sha:
            continue
        started_at = turn.holder_since
        approximate = started_at is None
        if started_at is None and index > 0:
            started_at = turns[index - 1].merged_at
        if started_at is None or started_at > pull.merged_at:
            return None
        return HolderEvidence(started_at, pull.merged_at, approximate)
    return None
