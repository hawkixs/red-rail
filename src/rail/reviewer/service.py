"""Pull-mode reviewer: for each watched repository, every open non-draft PR whose head SHA
has no completed `red-rail/review` check of ours (or carries the rerun label) gets exactly
one review. Fail-closed: no verdict → failure + REQUEST_CHANGES, never neutral."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from rail.ledger import (
    RECEIPTS_DIR,
    AttestationKind,
    Ledger,
    RecordKind,
    Unattested,
    idempotency_key_for,
)
from rail.ledger.file import receipt_filename
from rail.reviewer.github import PullRequest
from rail.reviewer.judges import JudgeReply, judge
from rail.reviewer.policy import ReviewPolicy, producer_provider
from rail.reviewer.verdict import Finding, ReviewVerdict

REVIEWER_IDENTITY = "red-rail-reviewer"
_DIFF_HEADER = re.compile(r"^diff --git a/(?P<path>\S+) b/", re.MULTILINE)
RunJudge = Callable[..., JudgeReply]


class GitHubLike(Protocol):
    def diff(self, repository: str, number: int) -> str: ...
    def commit_messages(self, repository: str, number: int) -> list[str]: ...
    def check_runs(self, repository: str, sha: str, *, name: str) -> list[Any]: ...
    def start_check(self, repository: str, head_sha: str, *, name: str) -> Any: ...
    def complete_check(
        self,
        repository: str,
        check_id: int,
        *,
        conclusion: str,
        title: str,
        summary: str,
        text: str = "",
    ) -> Any: ...
    def review(
        self, repository: str, number: int, *, commit_id: str, event: str, body: str
    ) -> None: ...
    def remove_label(self, repository: str, number: int, label: str) -> None: ...


@dataclass
class ReviewOutcome:
    repository: str
    number: int
    head_sha: str
    verdict: ReviewVerdict
    check_run_id: int
    attested: bool = False
    receipt: Path | None = None
    failures: list[str] = field(default_factory=list)


def needs_review(pr: PullRequest, *, github: GitHubLike, policy: ReviewPolicy) -> bool:
    if pr.draft:
        return False
    if policy.rerun_label in pr.labels:
        return True
    done = [
        c
        for c in github.check_runs(pr.repository, pr.head_sha, name=policy.check_name)
        if c.status == "completed"
    ]
    return not done


def docs_only(diff: str, policy: ReviewPolicy) -> bool:
    paths = _DIFF_HEADER.findall(diff)
    return bool(paths) and all(any(fnmatch.fnmatch(p, g) for g in policy.docs_globs) for p in paths)


def _criteria(ledger: Ledger, project: str) -> list[str]:
    contracts = ledger.list(project, kind=RecordKind.CONTRACT)
    if not contracts:
        return []
    return [str(c) for c in contracts[-1].data.get("contract", {}).get("acceptance_criteria", [])]


def _merge(replies: list[JudgeReply], mode: str, truncated: bool) -> ReviewVerdict:
    verdicts = [r.verdict for r in replies if r.verdict is not None]
    decision = (
        "request_changes" if any(v.verdict == "request_changes" for v in verdicts) else "approve"
    )
    findings: list[Finding] = []
    for v in verdicts:
        findings.extend(f for f in v.findings if f not in findings)
    summary = " | ".join(f"{r.provider}: {r.verdict.summary}" for r in replies if r.verdict)
    return ReviewVerdict(
        verdict=decision,
        summary=summary[:4000] or "no summary",
        findings=findings[:100],
        mode=mode,
        providers=tuple(r.provider for r in replies if r.verdict),
        diff_truncated=truncated,
    )


def _render(verdict: ReviewVerdict) -> str:
    lines = [
        f"**{verdict.verdict}** ({verdict.mode}, judges: {', '.join(verdict.providers) or 'none'})",
        "",
        verdict.summary,
        "",
    ]
    for f in verdict.findings:
        where = f"{f.file}:{f.line}" if f.line else f.file
        lines.append(f"- [{f.severity}] {where} — {f.title}: {f.evidence}")
    if verdict.diff_truncated:
        lines.append(
            "\n_The diff was truncated by the reviewer; the verdict covers the first part only._"
        )
    return "\n".join(lines)


def _judge_chain(
    pr, diff, policy, chain, *, tier, criteria, run_judge, root, failures, wanted: int
) -> list[JudgeReply]:
    """Walk the chain until `wanted` verdicts are in hand; a failure is logged, never fatal."""
    replies: list[JudgeReply] = []
    for provider in chain:
        reply = run_judge(
            pr, diff, policy, provider=provider, tier=tier, criteria=criteria, root=root
        )
        if reply.verdict is None:
            failures.append(f"{provider}/{tier}: {reply.failure}")
            continue
        replies.append(reply)
        if len(replies) == wanted:
            break
    return replies


def review_pull(
    pr: PullRequest,
    *,
    github: GitHubLike,
    policy: ReviewPolicy,
    ledger: Ledger,
    project: str,
    repo_path: Path | None = None,
    run_judge: RunJudge = judge,
    root: Path | None = None,
) -> ReviewOutcome:
    check = github.start_check(pr.repository, pr.head_sha, name=policy.check_name)
    failures: list[str] = []
    diff = github.diff(pr.repository, pr.number)
    truncated = len(diff) > policy.max_diff_chars
    producer = producer_provider(github.commit_messages(pr.repository, pr.number))
    chain = policy.chain_for(producer=producer)
    mode = policy.mode_for(pr, docs_only=docs_only(diff, policy))
    criteria = _criteria(ledger, project)
    common = dict(criteria=criteria, run_judge=run_judge, root=root, failures=failures)
    if mode == "light":
        replies = _judge_chain(pr, diff, policy, chain, tier="light", wanted=1, **common)
    else:
        replies = _judge_chain(pr, diff, policy, chain, tier="light", wanted=2, **common)
        decisions = {r.verdict.verdict for r in replies if r.verdict}
        escalate = len(decisions) > 1 or any(r.verdict.important for r in replies if r.verdict)
        if escalate:
            used = {r.provider for r in replies}
            deep_chain = tuple(p for p in chain if p not in used) or chain
            deep = _judge_chain(pr, diff, policy, deep_chain, tier="deep", wanted=1, **common)
            replies = deep or replies  # the deep judge's verdict wins
    if not any(r.verdict for r in replies):
        verdict = ReviewVerdict(
            verdict="request_changes",
            summary=f"no verdict: {'; '.join(failures) or 'no judge available'}",
            findings=[],
            mode=mode,
            providers=(),
            diff_truncated=truncated,
        )
        title = "no verdict"
    else:
        verdict = _merge(replies, mode, truncated)
        title = verdict.verdict
    conclusion = "success" if verdict.verdict == "approve" else "failure"
    github.complete_check(
        pr.repository,
        check.id,
        conclusion=conclusion,
        title=title,
        summary=verdict.summary,
        text=_render(verdict),
    )
    github.review(
        pr.repository,
        pr.number,
        commit_id=pr.head_sha,
        event="APPROVE" if conclusion == "success" else "REQUEST_CHANGES",
        body=_render(verdict),
    )
    if policy.rerun_label in pr.labels:
        github.remove_label(pr.repository, pr.number, policy.rerun_label)
    outcome = ReviewOutcome(
        pr.repository, pr.number, pr.head_sha, verdict, check.id, failures=failures
    )
    data = verdict.as_attestation_data(
        sha=pr.head_sha, check_run_id=check.id, repository=pr.repository, pr=pr.number
    )
    key = idempotency_key_for(AttestationKind.REVIEW_VERDICT, data)
    try:
        record = ledger.attest(
            project,
            AttestationKind.REVIEW_VERDICT,
            data,
            issuer=REVIEWER_IDENTITY,
            idempotency_key=key,
        )
    except Unattested as exc:
        failures.append(
            f"unattested ({exc.cause}): replay with rail attest review_verdict --from {exc.receipt}"
        )
        outcome.receipt = exc.receipt
        return outcome
    outcome.attested = True
    if repo_path is not None:
        outcome.receipt = repo_path / RECEIPTS_DIR / receipt_filename(record)
    return outcome
