"""The review loop's state, from receipts only (spec 2026-09-25-review-loop-closure).

Pure: no GitHub, no ledger I/O, no judge. `service.py` hands it the pull request's
`review_verdict` and `review_ruling` records and a delta; it answers what to do next, which
findings are open, how new ones are numbered and classified, and which carry-forwards a code
pull request must account for."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from rail.ledger import Record
from rail.reviewer import carry
from rail.reviewer.verdict import Artifact, Finding, PreviousAnswer

Step = Literal["round", "awaiting_ruling", "closure"]
JUDGED_ROUNDS = (1, 2, 3, None)  # None: a verdict recorded before this change
SPEC_PLAN_DIRS = ("docs/specs/", "docs/plans/")
_PLUS_FILE = re.compile(r"^\+\+\+ (?P<path>\S+)", re.MULTILINE)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@", re.MULTILINE)


@dataclass(frozen=True)
class Ruling:
    finding: str
    ruling: Literal["fix", "carry_forward"]
    decision: str
    recorded_at: datetime


@dataclass(frozen=True)
class LoopState:
    judged: int
    last_judged: Record | None
    findings: tuple[Finding, ...]
    rulings: dict[str, Ruling]
    awaiting: bool
    has_findings_list: bool


def cf_id(finding_id: str) -> str:
    return "CF-" + finding_id.removeprefix("F-")


def f_id(cf: str) -> str:
    return "F-" + cf.removeprefix("CF-")


def artifact_of(paths: Iterable[str], records_globs: Sequence[str]) -> Artifact:
    """D1: spec/plan when every file is under docs/specs/ or docs/plans/, records ignored."""
    own = [p for p in paths if not any(fnmatch.fnmatch(p, g) for g in records_globs)]
    if own and all(p.startswith(SPEC_PLAN_DIRS) for p in own):
        return "spec_plan"
    return "code"


def _findings(record: Record) -> tuple[Finding, ...] | None:
    raw = record.data.get("findings")
    if not isinstance(raw, list):
        return None  # an integer count: recorded before the change
    return tuple(
        Finding.model_validate({**item, "evidence": item.get("evidence") or "(trimmed)"})
        for item in raw
    )


def _judged(record: Record) -> bool:
    return record.data.get("round") in JUDGED_ROUNDS


def loop_state(verdicts: Sequence[Record], rulings: Sequence[Record]) -> LoopState:
    judged = [v for v in verdicts if _judged(v)]
    judging = [v for v in verdicts if _judged(v) or v.data.get("round") == "closure"]
    last_judged = judging[-1] if judging else None
    latest = verdicts[-1] if verdicts else None
    latest_findings = _findings(latest) if latest is not None else None
    findings = latest_findings or ()
    has_list = latest_findings is not None
    after = last_judged.recorded_at if last_judged is not None else None
    ruled: dict[str, Ruling] = {}
    for r in rulings:
        if after is not None and r.recorded_at <= after:
            continue
        ruled[str(r.data["finding"])] = Ruling(
            finding=str(r.data["finding"]),
            ruling=r.data["ruling"],
            decision=str(r.data.get("decision", "")),
            recorded_at=r.recorded_at,
        )
    open_ = any(f.open_blocker for f in findings)
    last_round = last_judged.data.get("round") if last_judged is not None else None
    awaiting = open_ and (last_round == "closure" or (last_round == 3))
    return LoopState(
        judged=len(judged),
        last_judged=last_judged,
        findings=tuple(findings),
        rulings=ruled,
        awaiting=awaiting,
        has_findings_list=has_list,
    )


def open_blockers(state: LoopState) -> list[Finding]:
    return [f for f in state.findings if f.open_blocker]


def unruled(state: LoopState) -> list[Finding]:
    """D9. A body-derived mechanical blocker (Ruling 19) never needs a ruling: it is
    recomputed from the pull request's body every round, not judged. A "not addressed"
    mechanical blocker is different — it records a gap in the judge's confirmation, not in the
    body's own shape, so it needs a ruling like any other blocker."""
    return [
        f for f in open_blockers(state)
        if f.id not in state.rulings and not carry.is_body_derived(f)
    ]


def next_step(state: LoopState) -> tuple[Step, int | None]:
    if state.awaiting:
        if not unruled(state):
            # Ruling 21/23: with nothing left to rule on, close only if the operator actually
            # ruled on an open, non-body-derived blocker; a body-derived gap resolving on its
            # own, or a ruling that lands on something else, is not a closing ruling — another
            # judged round 3 instead of a no-judge closure.
            closing = any(
                f.id in state.rulings and not carry.is_body_derived(f)
                for f in open_blockers(state)
            )
            if closing:
                return "closure", None
            return "round", 3
        return "awaiting_ruling", None
    return "round", min(state.judged + 1, 3)


def changed_lines(delta: str) -> dict[str, set[int]]:
    """New-side line numbers each hunk covers, per file — keyed by the `+++ b/<path>` line
    (a rename's findings cite the new path, not the `diff --git a/` header); `+++ /dev/null`
    (a deletion) is skipped."""
    lines: dict[str, set[int]] = {}
    for block in re.split(r"(?=^diff --git )", delta, flags=re.MULTILINE):
        match = _PLUS_FILE.search(block)
        if not match or match["path"] == "/dev/null":
            continue
        path = match["path"].removeprefix("b/")
        covered = lines.setdefault(path, set())
        for hunk in _HUNK.finditer(block):
            start = int(hunk["start"])
            count = int(hunk["count"]) if hunk["count"] is not None else 1
            covered.update(range(start, start + count))
    return lines


def _demoted(artifact: Artifact) -> str:
    return "carry_forward" if artifact == "spec_plan" else "note"


def enforce_class(f: Finding, artifact: Artifact) -> Finding:
    """D2: the judge proposes, the table decides.

    On `code`, severity alone decides — a judge's "note" no longer downgrades a blocking
    finding. On `spec_plan`, a proposed "blocker" or "carry_forward" is kept; anything else
    (None or "note") falls back on severity: blocking -> "blocker", otherwise ->
    "carry_forward"."""
    if artifact == "code":
        klass = "blocker" if f.severity == "blocking" else "note"
    else:
        if f.klass in ("blocker", "carry_forward"):
            klass = f.klass
        else:
            klass = "blocker" if f.severity == "blocking" else "carry_forward"
    return f if f.klass == klass else f.model_copy(update={"klass": klass})


def demote_outside(
    findings: Sequence[Finding],
    delta: str | None,
    artifact: Artifact,
    *,
    known: Collection[str] = (),
    skip: bool = False,
) -> list[Finding]:
    """D5 round 3: a NEW finding survives only on a line the delta changed; with no delta
    (a rebase, a lost base) every new finding is demoted. Every finding is classified with
    `enforce_class` before the skip test, so an unclassified (klass None) blocking finding
    is demoted too. A finding whose id is in `known` is an earlier one and is untouched; an
    id the judge invented is new (Review Focus 4).

    `skip`: True when the caller has no findings list to compare against (rollout: a PR's
    round-3 verdict recorded before this change). Demotion needs that history; without it,
    every finding is classified and returned unchanged rather than wrongly demoted."""
    if skip:
        return [enforce_class(f, artifact) for f in findings]
    covered = changed_lines(delta) if delta else {}
    out: list[Finding] = []
    for f in findings:
        f = enforce_class(f, artifact)
        if (f.id is not None and f.id in known) or f.klass != "blocker":
            out.append(f)
            continue
        lines = covered.get(f.file)
        inside = lines is not None and (f.line is None or f.line in lines)
        out.append(f if inside else f.model_copy(update={"klass": _demoted(artifact)}))
    return out


def _number(identifier: str) -> int:
    return int(identifier.rsplit("-", 1)[1])


def assign(
    new: Sequence[Finding],
    previous: Sequence[PreviousAnswer],
    state: LoopState,
    *,
    pr: int,
    artifact: Artifact,
    floor: int = 0,
) -> list[Finding]:
    """The verdict's full findings list: every earlier finding with its new status, then the
    new ones numbered after the highest id so far (D2, D7).

    `floor`: the highest id number already in use across every finding this pull request
    holds, mechanical carry-forward blockers included — `state` here is the judged subset
    only, so its own highest id can undercount and collide with one a mechanical blocker
    already claimed (Ruling 12)."""
    known = {f.id: f for f in state.findings if f.id}
    answers = {a.id: a.status for a in previous if a.id in known}
    repeated = {f.id for f in new if f.id in known}
    out: list[Finding] = []
    for fid, f in known.items():
        if f.status == "ruled":
            out.append(f)
        elif f.status == "fixed":
            # sticky, unless the judge reports it again: a regression (Review Focus 3)
            out.append(f if fid not in repeated else f.model_copy(update={"status": "still_open"}))
        elif answers.get(fid) == "fixed" and fid not in repeated:
            out.append(f.model_copy(update={"status": "fixed"}))
        else:
            out.append(f.model_copy(update={"status": "still_open"}))
    highest = max((_number(i) for i in known), default=0)
    highest = max(highest, floor)
    for f in new:
        if f.id in known:
            continue
        highest += 1
        fresh = f.model_copy(update={"id": f"F-{pr}-{highest}", "status": "new"})
        out.append(enforce_class(fresh, artifact))
    return out


def approves(findings: Sequence[Finding]) -> bool:
    """D3: no open blocker."""
    return not any(f.open_blocker for f in findings)


def append_new(
    findings: Sequence[Finding], extra: Sequence[Finding], *, pr: int, artifact: Artifact
) -> list[Finding]:
    """`findings` unchanged, then `extra` numbered after the highest id among them."""
    highest = max((_number(f.id) for f in findings if f.id), default=0)
    out = list(findings)
    for f in extra:
        highest += 1
        out.append(
            enforce_class(
                f.model_copy(update={"id": f"F-{pr}-{highest}", "status": "new"}), artifact
            )
        )
    return out


def apply_rulings(findings: Sequence[Finding], rulings: dict[str, Ruling]) -> list[Finding]:
    """D9: a carry_forward ruling reclassifies its finding without a judge."""
    return [
        f.model_copy(update={"klass": "carry_forward", "status": "ruled"})
        if f.id in rulings and rulings[f.id].ruling == "carry_forward"
        else f
        for f in findings
    ]


def open_carry_forwards(
    verdicts: Sequence[Record],
    rulings: Sequence[Record],
    *,
    repository: str,
    excluding_pr: int | None = None,
) -> list[str]:
    """D11: CF- of approving verdicts, plus carry_forward rulings of pull requests that were
    later approved, minus every id an approving code verdict recorded as addressed."""
    mine = [v for v in verdicts if v.data.get("repository") == repository]
    approved_prs = {v.data.get("pr") for v in mine if v.data.get("verdict") == "approve"}
    opened: dict[str, None] = {}
    addressed: set[str] = set()
    for v in mine:
        if v.data.get("verdict") != "approve":
            continue
        for f in _findings(v) or ():
            if f.klass == "carry_forward" and f.id and f.status != "fixed":
                opened.setdefault(cf_id(f.id), None)
        carry = v.data.get("carry_forwards") or {}
        addressed.update(carry.get("addressed", []))
    for r in rulings:
        if r.data.get("repository") != repository or r.data.get("ruling") != "carry_forward":
            continue
        if r.data.get("pr") in approved_prs:
            opened.setdefault(cf_id(str(r.data["finding"])), None)
    excluded = f"CF-{excluding_pr}-" if excluding_pr is not None else None
    return sorted(
        (i for i in opened if i not in addressed and not (excluded and i.startswith(excluded))),
        key=lambda i: (int(i.split("-")[1]), _number(i)),
    )
