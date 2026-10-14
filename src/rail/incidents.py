"""Pure recovery rules shared by deployment flows and delivery metrics."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from rail.ledger import Record


def match_recoveries(
    incidents: Sequence[Record], restores: Sequence[Record]
) -> tuple[list[tuple[Record, Record]], list[Record]]:
    """Close named incidents or the oldest earlier open incident for legacy recoveries.

    Inputs are non-drill records of one project in ledger order. An unknown or already
    closed named incident never falls back to another one, so evidence cannot close it twice.
    """
    open_incidents = {incident.digest: incident for incident in incidents}
    closed: dict[str, Record] = {}
    for restored in restores:
        if "incident" in restored.data:
            reference = restored.data["incident"]
            incident = open_incidents.get(reference) if isinstance(reference, str) else None
        else:
            incident = next(
                (r for r in open_incidents.values() if r.recorded_at < restored.recorded_at),
                None,
            )
        if incident is not None:
            closed[incident.digest] = restored
            del open_incidents[incident.digest]
    return (
        [
            (incident, closed[incident.digest])
            for incident in incidents
            if incident.digest in closed
        ],
        list(open_incidents.values()),
    )


def restored_time(incident: Record, restored: Record) -> datetime:
    """Use the UTC event time only within the incident and recovery record's bounds.

    Legacy or invalid evidence uses the record time rather than inventing a recovery time.
    """
    value = restored.data.get("restored_at")
    if isinstance(value, str):
        try:
            event_time = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if (
                event_time.utcoffset() == timedelta(0)
                and incident.recorded_at <= event_time <= restored.recorded_at
            ):
                return event_time
    return restored.recorded_at
