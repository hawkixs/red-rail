"""What may never enter a delivery contract, refused where it is written.

The contract is the one artefact this rail makes immutable: mirrored in the repository and in
brain, correctable only by an amendment that leaves the faulty revision in history for ever.
It was also the least validated at write time — `hygiene.receipts` checks a receipt's digest
and filename, never its content, and `rail contract set` accepted any text at all. Every other
door the rail guards is reversible; this one is not, and it was the least watched.

Only FACTS are refused here, never judgements. "This objective is well phrased" is not
checkable and is not attempted — the day's lesson is that a control describing a string
instead of a property satisfies itself mechanically.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

# Four dotted decimal groups. The trailing guard rejects a FIFTH group (`10.0.0.1.5` is not an
# address) while allowing a full stop — a sentence ending in an address is the natural way to
# write one, and a guard a full stop bypasses guards nothing. The value is then parsed rather
# than pattern-matched, so `999.1.1.1` is four numbers and `1.26.8` is a version.
_DOTTED = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?!\.?\d)")

# Credential shapes that are unambiguous: each is a published prefix, not a guess about
# entropy. A heuristic on randomness would refuse legitimate text, which is worse than
# missing a secret here — gitleaks already covers the repository's history.
_CREDENTIALS = (
    ("ghp_", "GitHub personal access token"),
    ("gho_", "GitHub OAuth token"),
    ("ghu_", "GitHub user-to-server token"),
    ("ghs_", "GitHub server-to-server token"),
    ("ghr_", "GitHub refresh token"),
    ("github_pat_", "GitHub fine-grained token"),
    ("AKIA", "AWS access key id"),
    ("-----BEGIN ", "private key block"),
)


class Unwritable(Exception):
    """The text carries something a contract must never freeze."""


def _addresses(text: str) -> list[str]:
    """Literal IP addresses, excluding the documentation range RFC 5737 exists to provide.

    Refusing `192.0.2.0/24` would push an author to invent a real address instead, which is
    the opposite of the intent."""
    found = []
    for candidate in _DOTTED.findall(text):
        try:
            address = ipaddress.IPv4Address(candidate)
        except ValueError:
            continue  # `999.1.1.1` and friends: four groups, not an address
        if address in ipaddress.ip_network("192.0.2.0/24"):
            continue
        found.append(candidate)
    return found


def _credentials(text: str) -> list[str]:
    return [name for prefix, name in _CREDENTIALS if prefix in text]


def refuse_unwritable(
    objectives: Iterable[str],
    *,
    criteria: Iterable[str] | None = None,
    constraints: Iterable[str] | None = None,
) -> None:
    """Raise when a field carries an address or a credential; say which field and which text.

    A refusal that does not name what it found costs more than it saves — that is the whole
    complaint against the opaque `invalid_arguments` this rail used to relay."""
    fields: list[tuple[str, Iterable[str] | None]] = [
        ("objective", objectives),
        ("criterion", criteria),
        ("constraint", constraints),
    ]
    for label, values in fields:
        for value in values or ():
            text = str(value)
            if addresses := _addresses(text):
                raise Unwritable(
                    f"{label}: {', '.join(addresses)} is a literal address, and a contract "
                    "revision cannot be corrected — only amended, leaving this one in "
                    "history. Every literal address is refused, loopback and private ranges "
                    'included. Describe the target instead ("the loopback interface", '
                    '"the host\'s private address"), or use the documentation range '
                    "192.0.2.0/24"
                )
            if names := _credentials(text):
                raise Unwritable(
                    f"{label}: this looks like a {names[0]} — a credential written into a "
                    "contract is frozen in the repository and in brain. Remove it, and "
                    "rotate it if it is real"
                )
