"""An agent's fix ruling stands on an operator delegation recorded in brain (decision
`f38ce645`): the agent names the decision, the rail reads it back and accepts only an active
delegation. Anything it cannot read for certain is a refusal, never a pass."""

from __future__ import annotations

import re
from collections.abc import Callable

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


# Evidence is published with the ruling, so it is an ALLOW-LIST of code references, never prose:
# directories without a dot (one leading dot allowed, `.github`), a file name with one known
# extension, then optionally `:line` or `::test` (a pytest parameter id in brackets), several
# references separated by commas or spaces. A deny-list of addresses kept missing forms (IPv6
# compressions, decimal and octal IPv4, host names); this grammar has nothing for one to hide in,
# and a host name (`host.example.com`) has no file extension in the list.
_EXTENSIONS = (
    "py|pyi|md|rst|txt|toml|yaml|yml|json|jsonl|sql|sh|bash|ts|tsx|js|mjs|go|rs|cfg|ini|"
    "service|timer|lock|html|css|j2"
)
_REFERENCE = re.compile(
    rf"(?:\.?[A-Za-z0-9_-]+/)*\.?[A-Za-z0-9_-]+\.(?:{_EXTENSIONS})"
    r"(?::[0-9]{1,6}|::[A-Za-z_][A-Za-z0-9_]*(?:\[[A-Za-z0-9_-]+\])?)?(?![A-Za-z0-9_.])"
)
_REFERENCES = re.compile(rf"{_REFERENCE.pattern}(?:[ ,]+{_REFERENCE.pattern})*")


def unsafe_evidence(proof: str) -> str | None:
    """Why `proof` cannot be published with a ruling, or None."""
    if not _REFERENCES.fullmatch(proof):
        return (
            "something other than code references (path.ext, path.ext:line or "
            "path.ext::test, separated by commas)"
        )
    return None


_PARSE = re.compile(
    r"(?P<path>[^:, ]+)(?::(?P<line>[0-9]{1,6})|::(?P<test>[A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?)?"
)


def evidence_refusal(
    commits: list[str], read: Callable[[str], str | None], sha: str, proof: str
) -> str | None:
    """Why the evidence does not hold at `sha`, or None. The grammar alone still leaves names
    free (codex, red-rail#96: `tests/sk_live_<key>.py`), and a commit of the host's checkout or
    of any branch can be made to hold them (the background security review). So `sha` must be
    one of the pull request's OWN commits (`commits`, read from GitHub), and every reference must
    name what that commit holds (`read(path)`, GitHub's file at `sha`): the file exists, a
    `::test` is defined in it, a `:line` is within it. A reference then names only what the
    pull request already publishes, and the ruling discloses nothing new."""
    full = [c for c in commits if c.startswith(sha)]
    if len(full) != 1:
        return f"--evidence-sha {sha} is not a commit of this pull request"
    for reference in re.split(r"[ ,]+", proof.strip()):
        parsed = _PARSE.fullmatch(reference)
        if parsed is None:
            return f"--evidence {reference!r} is not a code reference"
        text = read(parsed.group("path"))
        if text is None:
            return f"--evidence names {parsed.group('path')}, which is not in {sha[:12]}"
        if (line := parsed.group("line")) and int(line) > len(text.splitlines()):
            return f"--evidence line {line} is beyond {parsed.group('path')} at {sha[:12]}"
        test = parsed.group("test")
        if test and not re.search(rf"^\s*(?:async\s+)?def {re.escape(test)}\(", text, re.M):
            return f"--evidence test {test} is not defined in {parsed.group('path')} at {sha[:12]}"
    return None
