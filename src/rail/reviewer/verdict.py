"""The verdict is evidence: enum-valued, closed, no free-form executable text."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["blocking", "important", "minor"]
Decision = Literal["approve", "request_changes"]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: Severity
    file: str = Field(min_length=1, max_length=500)
    line: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=200)
    evidence: str = Field(min_length=1, max_length=2000)


class ReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Decision
    summary: str = Field(min_length=1, max_length=4000)
    findings: list[Finding] = Field(default_factory=list, max_length=100)
    mode: Literal["light", "deep", "incremental", "budget"] = "light"
    providers: tuple[str, ...] = ()
    diff_truncated: bool = False

    @property
    def blocking(self) -> bool:
        return any(f.severity == "blocking" for f in self.findings)

    @property
    def important(self) -> bool:
        return any(f.severity in ("blocking", "important") for f in self.findings)

    def as_attestation_data(self, *, sha: str, check_run_id: int, repository: str, pr: int) -> dict:
        """The `review_verdict` payload: no float, identifiers as text."""
        return {
            "sha": sha,
            "independent": True,
            "verdict": self.verdict,
            "check_run_id": check_run_id,
            "repository": repository,
            "pr": pr,
            "mode": self.mode,
            "providers": list(self.providers),
            "findings": len(self.findings),
            "blocking": self.blocking,
            "diff_truncated": self.diff_truncated,
        }
