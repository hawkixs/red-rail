"""An agent's fix ruling stands on an operator delegation recorded in brain (decision
`f38ce645`): the agent names the decision, the rail reads it back and accepts only an active
delegation. Anything it cannot read for certain is a refusal, never a pass."""

from __future__ import annotations

import re

# brain_get renders a decision as markdown. The record's OWN header is its first two lines, in
# this order — the title with the id, then the status — and nothing else counts: a delegation
# quoted further down, or a status line injected later, never authorises (codex, red-rail#96)
_HEADER = re.compile(
    r"\A## Decision: (?P<title>[^\n]+) \(id:(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})\)\n\*\*Status\*\*: (?P<status>[a-z_]+)\n"
)
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
    # a rendered decision carries exactly one id line and one status line; a second one is a
    # forged header inside the title or a quoted record, and the reply is refused, not guessed
    if text.count("(id:") != 1 or text.count("**Status**:") != 1:
        raise DelegationRefused(f"decision {decision_id}: ambiguous record in brain's reply")
    header = _HEADER.match(text)
    # a delegation is recorded under a title that says so first ("Operator delegation …",
    # d47ac557 and its successions); a decision ABOUT delegations, such as f38ce645 itself,
    # mentions the word without granting anything
    if header is None or not header.group("title").lower().startswith("operator delegation"):
        raise DelegationRefused(f"decision {decision_id} is not a delegation decision")
    if not header.group("id").replace("-", "").startswith(decision_id.lower().replace("-", "")):
        raise DelegationRefused(f"brain answered for another decision than {decision_id}")
    if header.group("status") != "active":
        raise DelegationRefused(f"decision {decision_id} is not active")
    return header.group("id")


# what a proof may not carry, beyond the contract guard's IPv4 and credential prefixes: a URL
# (it names a host and may carry userinfo), an `@`, and an IPv6 address in any of its forms,
# trailing compression included. Candidates are runs of hex digits, dots and colons that are
# not glued to a name, validated by `ipaddress` itself; a pytest node id's `::` sits between
# names, so it is never a candidate.
_URL = re.compile(r"[a-z][a-z0-9+.-]*://", re.I)
_IPV6_CANDIDATE = re.compile(r"(?<![\w./])[0-9a-f.:%]*:[0-9a-f.:%]*(?![\w/])", re.I)


def _is_ipv6(candidate: str) -> bool:
    import ipaddress

    try:
        return ipaddress.ip_address(candidate.split("%", 1)[0]).version == 6
    except ValueError:
        return False


def unsafe_evidence(proof: str) -> str | None:
    """Why `proof` cannot be published with a ruling, or None."""
    if _URL.search(proof):
        return "a URL"
    if "@" in proof:
        return "an @ (userinfo or an address)"
    for match in _IPV6_CANDIDATE.finditer(proof):
        candidate = match.group(0).strip(".")
        # `[::1]:8765` and `fe80::1:` both leave a trailing port or colon to try without
        if any(_is_ipv6(c) for c in (candidate, candidate.rsplit(":", 1)[0])):
            return "an IPv6 literal"
    return None
