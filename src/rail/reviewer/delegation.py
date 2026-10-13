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
# (it names a host and may carry userinfo), an `@`, and an IPv6 address in any of its forms. Every
# maximal run of hex digits, dots, colons and `%` holding two colons or more is a candidate,
# whatever surrounds it (a `/` before it or a `/64` after it hid one), and `ipaddress` decides.
# The one exemption is a pytest node id: a run that starts with `::` right after a name.
_URL = re.compile(r"[a-z][a-z0-9+.-]*://", re.I)
_HEX_RUN = re.compile(r"[0-9a-f.:%]+", re.I)


def _is_ipv6(candidate: str) -> bool:
    import ipaddress

    try:
        return ipaddress.ip_address(candidate.split("%", 1)[0]).version == 6
    except ValueError:
        return False


def _ipv6_in(proof: str) -> bool:
    for match in _HEX_RUN.finditer(proof):
        run = match.group(0)
        if run.count(":") < 2:
            continue
        glued = match.start() > 0 and (
            proof[match.start() - 1].isalnum() or proof[match.start() - 1] in "._"
        )
        if run.startswith("::") and glued:
            # `module.py::name` is a test id; what follows the `::` is still checked, so
            # `module.py::fe80::1` cannot hide an address behind the exemption
            run = run[2:]
            if run.count(":") < 1:
                continue
        trimmed = run.strip(".:")
        candidates = {run, trimmed, run.rstrip(".")}
        # a trailing `:port` or a leading `x.` glued in the run
        candidates |= {c.rsplit(":", 1)[0] for c in list(candidates)}
        candidates |= {c.split(".", 1)[1] for c in list(candidates) if "." in c}
        if any(_is_ipv6(c) for c in candidates if c):
            return True
    return False


def unsafe_evidence(proof: str) -> str | None:
    """Why `proof` cannot be published with a ruling, or None."""
    if _URL.search(proof):
        return "a URL"
    if "@" in proof:
        return "an @ (userinfo or an address)"
    if _ipv6_in(proof):
        return "an IPv6 literal"
    return None
