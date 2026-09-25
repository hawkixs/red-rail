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
    r"^\s*(?:[-*]\s+)?(?P<id>CF-\d+-\d+)\s*:\s*"
    r"(?:(?P<addressed>addressed)|(?P<deferred>deferred)(?:\s*:\s*(?P<reason>.*?))?)\s*$",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^(```|~~~)")
WHERE = "(pull request description)"
_NOT_ADDRESSED = " not addressed"


@dataclass(frozen=True)
class Accounting:
    status: Literal["addressed", "deferred"]
    reason: str


def _strip_fences(body: str) -> str:
    """Drop every line between a pair of ``` or ~~~ fences: a heading or a carry-forward line
    inside a fenced code block is not part of the pull request's accounting."""
    out: list[str] = []
    fenced = False
    for line in body.splitlines():
        if _FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


def parse_section(body: str) -> dict[str, Accounting]:
    body = _strip_fences(body or "")
    heading = _HEADING.search(body)
    if heading is None:
        return {}
    rest = body[heading.end() :]
    end = _NEXT.search(rest)
    section = rest[: end.start()] if end else rest
    found: dict[str, Accounting] = {}
    for line in section.splitlines():
        match = _LINE.match(line)
        if match:
            status = "addressed" if match["addressed"] else "deferred"
            found[match["id"].upper()] = Accounting(status, (match["reason"] or "").strip())
    return found


def _blocker(title: str, evidence: str) -> Finding:
    return Finding.model_validate(
        {
            "severity": "blocking",
            "file": WHERE,
            "title": title,
            "evidence": evidence,
            "class": "blocker",
        }
    )


def not_addressed(cf: str) -> Finding:
    """A carry-forward the body claims addressed that no judge confirmed (D11)."""
    return _blocker(f"{cf}{_NOT_ADDRESSED}", "the judge did not confirm it is addressed")


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


_BODY_DERIVED_SUFFIXES = ("not accounted for", "deferred without a reason")


def is_body_derived(f: Finding) -> bool:
    """A mechanical blocker computed purely from the body's own shape — nothing not accounted
    for, or a deferral with no reason (Ruling 19). It never needs a ruling: it is recomputed,
    and resolves itself, as soon as the body changes. A "not addressed" blocker is different:
    it records that the judge never confirmed the body's own claim, so it needs a ruling like
    any other blocker."""
    return is_mechanical(f) and f.title.endswith(_BODY_DERIVED_SUFFIXES)


def ruled_deferred(findings: Sequence[Finding]) -> list[str]:
    """The carry-forwards whose "not addressed" gap the operator ruled carry_forward: the
    ruling defers the carry-forward itself (Ruling 28)."""
    return [
        f.title.removesuffix(_NOT_ADDRESSED)
        for f in findings
        if is_mechanical(f) and f.status == "ruled" and f.title.endswith(_NOT_ADDRESSED)
    ]


def reconcile(
    old: Sequence[Finding], current: Sequence[Finding]
) -> tuple[list[Finding], list[Finding]]:
    """Mechanical blockers are not judged: they are recomputed from the body every round. A
    ruled finding is never touched; any other old finding is re-derived solely from whether its
    title still appears in `current` — a fixed one can reopen as `still_open`."""
    titles = {f.title for f in current}
    kept = [
        f
        if f.status == "ruled"
        else f.model_copy(update={"status": "still_open" if f.title in titles else "fixed"})
        for f in old
    ]
    seen = {f.title for f in old}
    return kept, [f for f in current if f.title not in seen]


def addressed(open_ids: Sequence[str], section: dict[str, Accounting]) -> list[str]:
    return [i for i in open_ids if section.get(i, Accounting("deferred", "")).status == "addressed"]


def deferred(open_ids: Sequence[str], section: dict[str, Accounting]) -> list[str]:
    return [
        i
        for i in open_ids
        if (e := section.get(i)) is not None and e.status == "deferred" and e.reason
    ]


def strangers(open_ids: Sequence[str], section: dict[str, Accounting]) -> list[str]:
    return [i for i in section if i not in open_ids]
