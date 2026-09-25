"""How a code pull request accounts for the open carry-forwards: a `## Carry-forwards` section
in its body, one line per id (spec 2026-09-25-review-loop-closure, D11). Pure."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from rail.reviewer.verdict import Finding

LINE_FORMAT = "CF-<pr>-<n>: addressed | CF-<pr>-<n>: deferred: <reason>"
_HEADING = re.compile(r"^##\s+Carry-forwards\s*$", re.IGNORECASE | re.MULTILINE)
_NEXT = re.compile(r"^##\s", re.MULTILINE)
_LINE = re.compile(
    r"^\s*(?:[-*]\s+)?(?P<id>CF-\d+-\d+)\s*:\s*(?P<status>addressed|deferred)\s*"
    r"(?::\s*(?P<reason>.*?))?\s*$",
    re.IGNORECASE,
)
WHERE = "(pull request description)"


@dataclass(frozen=True)
class Accounting:
    status: Literal["addressed", "deferred"]
    reason: str


def parse_section(body: str) -> dict[str, Accounting]:
    heading = _HEADING.search(body or "")
    if heading is None:
        return {}
    rest = body[heading.end() :]
    end = _NEXT.search(rest)
    section = rest[: end.start()] if end else rest
    found: dict[str, Accounting] = {}
    for line in section.splitlines():
        match = _LINE.match(line)
        if match:
            status = match["status"].lower()
            found[match["id"].upper()] = Accounting(status, (match["reason"] or "").strip())
    return found


def _blocker(title: str, evidence: str) -> Finding:
    return Finding.model_validate(
        {"severity": "blocking", "file": WHERE, "title": title, "evidence": evidence,
         "class": "blocker"}
    )


def mechanical_blockers(open_ids: Sequence[str], section: dict[str, Accounting]) -> list[Finding]:
    out: list[Finding] = []
    for cf in open_ids:
        entry = section.get(cf)
        if entry is None:
            out.append(
                _blocker(
                    f"{cf} not accounted for",
                    f"{cf} is an open carry-forward: list it under `## Carry-forwards` as "
                    f"one line, {LINE_FORMAT}",
                )
            )
        elif entry.status == "deferred" and not entry.reason:
            out.append(
                _blocker(
                    f"{cf} deferred without a reason",
                    f"a deferral needs its reason: {LINE_FORMAT}",
                )
            )
    return out


def is_mechanical(f: Finding) -> bool:
    return f.file == WHERE


def reconcile(
    old: Sequence[Finding], current: Sequence[Finding]
) -> tuple[list[Finding], list[Finding]]:
    """Mechanical blockers are not judged: they are recomputed from the body every round."""
    titles = {f.title for f in current}
    kept = [
        f.model_copy(update={"status": "still_open" if f.title in titles else "fixed"})
        if f.status in ("new", "still_open")
        else f
        for f in old
    ]
    seen = {f.title for f in old}
    return kept, [f for f in current if f.title not in seen]


def addressed(open_ids: Sequence[str], section: dict[str, Accounting]) -> list[str]:
    return [i for i in open_ids if section.get(i, Accounting("deferred", "")).status == "addressed"]


def deferred(open_ids: Sequence[str], section: dict[str, Accounting]) -> list[str]:
    return [
        i for i in open_ids
        if (e := section.get(i)) is not None and e.status == "deferred" and e.reason
    ]


def strangers(open_ids: Sequence[str], section: dict[str, Accounting]) -> list[str]:
    return [i for i in section if i not in open_ids]
