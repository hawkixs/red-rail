"""An agent's fix ruling stands on an operator delegation recorded in brain (decision
`f38ce645`): the agent names the decision, the rail reads it back and accepts only an active
delegation. Anything it cannot read for certain is a refusal, never a pass."""

from __future__ import annotations

import re

# brain_get renders a decision as markdown: the title line and the status line are the two
# facts this check needs, read strictly
_TITLE = re.compile(r"^## Decision: (?P<title>.+) \(id:(?P<id>[0-9a-f-]{36})\)$", re.M)
_STATUS = re.compile(r"^\*\*Status\*\*: (?P<status>\S+)$", re.M)
_DECISION_ID = re.compile(r"^[0-9a-f]{8}(?:-?[0-9a-f]{4}){0,3}(?:-?[0-9a-f]{12})?$")


class DelegationRefused(ValueError):
    """The named decision is not an active operator delegation, or it could not be read."""


def fetch_decision(decision_id: str, *, agent: str) -> str:
    """The decision as brain renders it, read as the agent that rules."""
    from rail.brain.client import BrainClient
    from rail.brain.settings import BrainSettings

    client = BrainClient.from_settings(BrainSettings.from_environment(), agent=agent)
    reply = client.call("brain_get", {"entity_type": "decision", "entity_id": decision_id})
    return str(reply.get("result", ""))


def verify_delegation(decision_id: str, *, agent: str) -> str:
    """The full id of `decision_id` when it is an ACTIVE operator delegation; otherwise
    `DelegationRefused`, naming why."""
    if not _DECISION_ID.fullmatch(decision_id.lower()):
        raise DelegationRefused(f"{decision_id!r} is not a brain decision id")
    try:
        text = fetch_decision(decision_id, agent=agent)
    except Exception as exc:  # unreachable brain, refused call: never a pass
        raise DelegationRefused(
            f"cannot verify decision {decision_id} in brain ({type(exc).__name__})"
        ) from exc
    title = _TITLE.search(text)
    # a delegation is recorded under a title that says so first ("Operator delegation …",
    # d47ac557 and its successions); a decision ABOUT delegations, such as f38ce645 itself,
    # mentions the word without granting anything
    if title is None or not title.group("title").lower().startswith("operator delegation"):
        raise DelegationRefused(f"decision {decision_id} is not a delegation decision")
    status = _STATUS.search(text)
    if status is None or status.group("status") != "active":
        raise DelegationRefused(f"decision {decision_id} is not active")
    return title.group("id")
