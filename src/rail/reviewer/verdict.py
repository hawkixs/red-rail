"""The verdict is evidence: enum-valued, closed, no free-form executable text. Since the
review-loop closure (spec 2026-09-25) a finding carries a stable id, a class and a status, and
the receipt records every finding of the pull request so far, trimmed to fit the ledger."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["blocking", "important", "minor"]
Decision = Literal["approve", "request_changes"]
FindingClass = Literal["blocker", "carry_forward", "note"]
FindingStatus = Literal["new", "still_open", "fixed", "ruled"]
RoundLabel = Literal[1, 2, 3, "awaiting_ruling", "closure", "no_verdict", "mechanical"]
Artifact = Literal["spec_plan", "code", "records"]
FINDING_ID = r"^F-\d+-\d+$"
ANY_ID = r"^(F|CF)-\d+-\d+$"
# bytes of canonical JSON the findings list may take in a review_verdict payload: brain refuses
# a payload over 65536 (delivery_attestations.json, max_canonical_bytes); the rest of the payload
# is well under 1 KiB
RECEIPT_BUDGET = 60_000
_FILE_MAX = 200
_EVIDENCE_MAX = 300
_TITLE_SHORT = 80


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    severity: Severity
    file: str = Field(min_length=1, max_length=500)
    line: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=200)
    evidence: str = Field(min_length=1, max_length=2000)
    id: str | None = Field(default=None, pattern=FINDING_ID)
    klass: FindingClass | None = Field(default=None, alias="class")
    status: FindingStatus = "new"

    @property
    def open_blocker(self) -> bool:
        if self.status not in ("new", "still_open"):
            return False
        if self.klass is None:
            return self.severity == "blocking"
        return self.klass == "blocker"


class PreviousAnswer(BaseModel):
    """The judge's answer on one finding listed in the review context (D7)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=ANY_ID)
    status: Literal["fixed", "still_open"]
    evidence: str = Field(default="", max_length=2000)


class CarryForwards(BaseModel):
    """How a code pull request accounted for the open carry-forwards (D11)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    addressed: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()


class ReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Decision
    summary: str = Field(min_length=1, max_length=4000)
    findings: list[Finding] = Field(default_factory=list, max_length=100)
    mode: Literal["light", "deep", "incremental", "awaiting_ruling", "closure", "mechanical"] = (
        "light"
    )
    providers: tuple[str, ...] = ()
    diff_truncated: bool = False
    round: RoundLabel | None = None
    artifact: Artifact | None = None
    previous: tuple[PreviousAnswer, ...] = ()
    carry_forwards: CarryForwards | None = None

    @property
    def open_blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.open_blocker]

    @property
    def blocking(self) -> bool:
        return bool(self.open_blockers)

    @property
    def important(self) -> bool:
        return any(f.severity in ("blocking", "important") for f in self.findings)

    def as_attestation_data(self, *, sha: str, check_run_id: int, repository: str, pr: int) -> dict:
        """The `review_verdict` payload: no float, identifiers as text (D4)."""
        data: dict[str, Any] = {
            "sha": sha,
            "independent": True,
            "verdict": self.verdict,
            "check_run_id": check_run_id,
            "repository": repository,
            "pr": pr,
            "mode": self.mode,
            "providers": list(self.providers),
            "finding_count": len(self.findings),
            "blocking": self.blocking,
            "diff_truncated": self.diff_truncated,
            "round": self.round,
            "artifact": self.artifact,
            "findings": receipt_findings(self.findings),
        }
        if self.carry_forwards is not None:
            data["carry_forwards"] = {
                "addressed": list(self.carry_forwards.addressed),
                "deferred": list(self.carry_forwards.deferred),
            }
        return data


def _size(value: Any) -> int:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return len(canonical.encode())


def _receipt_form(f: Finding) -> dict[str, Any]:
    file = f.file if len(f.file) <= _FILE_MAX else "…" + f.file[-(_FILE_MAX - 1) :]
    return {
        "id": f.id,
        "class": f.klass,
        "severity": f.severity,
        "file": file,
        "line": f.line,
        "title": f.title,
        "status": f.status,
        "evidence": f.evidence[:_EVIDENCE_MAX],
    }


def receipt_findings(findings: Sequence[Finding], *, budget: int = RECEIPT_BUDGET) -> list[dict]:
    """Every finding in its receipt form, under `budget` bytes: the evidence of notes goes
    first, then of carry-forwards, then of blockers; then every title shrinks to 80 characters.
    A finding is never dropped (D4)."""
    kept = [_receipt_form(f) for f in findings]
    for klass in ("note", "carry_forward", "blocker", None):
        if _size(kept) <= budget:
            return kept
        for item in kept:
            if item["class"] == klass:
                item["evidence"] = ""
    if _size(kept) > budget:
        for item in kept:
            item["title"] = item["title"][:_TITLE_SHORT]
    return kept
