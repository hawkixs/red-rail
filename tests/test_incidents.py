"""Recovery matching preserves ledger order and uses only bounded UTC event times."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from rail.incidents import match_recoveries, restored_time
from rail.ledger import AttestationKind, Record, RecordKind

T0 = datetime(2026, 10, 14, tzinfo=UTC)


def _record(kind: AttestationKind, minute: int, **data: Any) -> Record:
    return Record.build(
        kind=RecordKind.ATTESTATION,
        project="red-example",
        issuer="operator",
        idempotency_key=f"{kind.value}:{minute}",
        payload={"kind": kind.value, "data": {"drill": False, **data}},
        recorded_at=T0 + timedelta(minutes=minute),
    )


def test_named_recoveries_close_only_the_named_incident_in_incident_order() -> None:
    first = _record(AttestationKind.INCIDENT_DETECTED, 1)
    second = _record(AttestationKind.INCIDENT_DETECTED, 2)
    second_restore = _record(AttestationKind.RESTORED, 3, incident=second.digest)
    first_restore = _record(AttestationKind.RESTORED, 4, incident=first.digest)

    assert match_recoveries([first, second], [second_restore, first_restore]) == (
        [(first, first_restore), (second, second_restore)],
        [],
    )


def test_legacy_recovery_closes_the_oldest_still_open_incident() -> None:
    first = _record(AttestationKind.INCIDENT_DETECTED, 1)
    second = _record(AttestationKind.INCIDENT_DETECTED, 2)
    third = _record(AttestationKind.INCIDENT_DETECTED, 3)
    named = _record(AttestationKind.RESTORED, 4, incident=second.digest)
    legacy = _record(AttestationKind.RESTORED, 5)

    assert match_recoveries([first, second, third], [named, legacy]) == (
        [(first, legacy), (second, named)],
        [third],
    )


def test_unmatched_recoveries_never_close_another_incident() -> None:
    first = _record(AttestationKind.INCIDENT_DETECTED, 1)
    second = _record(AttestationKind.INCIDENT_DETECTED, 2)
    named = _record(AttestationKind.RESTORED, 3, incident=first.digest)
    unknown = _record(AttestationKind.RESTORED, 4, incident="sha256:unknown")
    duplicate = _record(AttestationKind.RESTORED, 5, incident=first.digest)

    assert match_recoveries([first, second], [named, unknown, duplicate]) == (
        [(first, named)],
        [second],
    )


def test_legacy_recovery_cannot_close_an_incident_recorded_at_or_after_it() -> None:
    early = _record(AttestationKind.RESTORED, 0)
    incident = _record(AttestationKind.INCIDENT_DETECTED, 1)
    simultaneous = _record(AttestationKind.RESTORED, 1)

    assert match_recoveries([incident], [early, simultaneous]) == ([], [incident])
    assert match_recoveries([], [early]) == ([], [])


def test_multiple_incidents_remain_open_in_ledger_order() -> None:
    incidents = [_record(AttestationKind.INCIDENT_DETECTED, i) for i in range(3)]

    assert match_recoveries(incidents, []) == ([], incidents)


@pytest.mark.parametrize("minute", [1, 2, 3])
@pytest.mark.parametrize("suffix", ["+00:00", "Z"])
def test_restored_time_uses_a_utc_event_time_inside_the_closed_interval(
    minute: int, suffix: str
) -> None:
    incident = _record(AttestationKind.INCIDENT_DETECTED, 1)
    event_time = T0 + timedelta(minutes=minute, seconds=0 if minute != 2 else 11)
    restored = _record(
        AttestationKind.RESTORED,
        3,
        restored_at=event_time.isoformat().replace("+00:00", suffix),
    )

    assert restored_time(incident, restored) == event_time


@pytest.mark.parametrize(
    "value",
    [
        (T0 + timedelta(seconds=59)).isoformat(),
        (T0 + timedelta(minutes=3, seconds=1)).isoformat(),
        "not-a-timestamp",
        "2026-10-14T00:02:00",
        "2026-10-14T01:02:00+01:00",
        None,
        42,
    ],
)
def test_restored_time_falls_back_when_the_event_time_is_invalid(value: Any) -> None:
    incident = _record(AttestationKind.INCIDENT_DETECTED, 1)
    restored = _record(AttestationKind.RESTORED, 3, restored_at=value)

    assert restored_time(incident, restored) == restored.recorded_at


def test_restored_time_falls_back_for_a_legacy_record() -> None:
    incident = _record(AttestationKind.INCIDENT_DETECTED, 1)
    restored = _record(AttestationKind.RESTORED, 3)

    assert restored_time(incident, restored) == restored.recorded_at
