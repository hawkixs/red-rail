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
    Record,
    RecordKind,
    Unattested,
    idempotency_key_for,
)
from rail.ledger.file import receipt_filename
from rail.reviewer.github import GitHubError, PullRequest
from rail.reviewer.judges import JudgeReply, judge
from rail.reviewer.policy import ReviewPolicy, producer_provider
from rail.reviewer.split import oversized, split_diff
from rail.reviewer.verdict import Finding, ReviewVerdict

REVIEWER_IDENTITY = "red-rail-reviewer"
_DIFF_HEADER = re.compile(r"^diff --git a/(?P<path>\S+) b/", re.MULTILINE)
RunJudge = Callable[..., JudgeReply]


class GitHubLike(Protocol):
    def diff(self, repository: str, number: int) -> str: ...
    def compare_diff(self, repository: str, base_sha: str, head_sha: str) -> str: ...
    def check_run_text(self, repository: str, check_id: int) -> str: ...
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
    # any check of ours on this head — completed or still running — means the review exists
    # (found by the independent reviewer on PR #3: a second process could start it twice)
    return not github.check_runs(pr.repository, pr.head_sha, name=policy.check_name)


def pending_reviews(github: Any, repository: str, policy: ReviewPolicy) -> list[PullRequest]:
    """The open pull requests that need a review, loaded in full: the list endpoint carries
    no additions/deletions, and the mode (light/deep) depends on them."""
    return [
        github.pull(repository, summary.number)
        for summary in github.open_pulls(repository)
        if needs_review(summary, github=github, policy=policy)
    ]


def docs_only(diff: str, policy: ReviewPolicy) -> bool:
    paths = _DIFF_HEADER.findall(diff)
    return bool(paths) and all(any(fnmatch.fnmatch(p, g) for g in policy.docs_globs) for p in paths)


def _criteria(ledger: Ledger, project: str) -> list[str]:
    contracts = ledger.list(project, kind=RecordKind.CONTRACT)
    if not contracts:
        return []
    return [str(c) for c in contracts[-1].data.get("contract", {}).get("acceptance_criteria", [])]


def previous_verdicts(ledger: Ledger, project: str, pr: PullRequest) -> list[Record]:
    """This pull request's earlier verdicts, oldest first — the ledger is the pass counter."""
    return [
        r
        for r in ledger.list(project, attestation=AttestationKind.REVIEW_VERDICT)
        if r.data.get("repository") == pr.repository and r.data.get("pr") == pr.number
    ]


def changed_lines(diff: str) -> int:
    return sum(
        1
        for line in diff.splitlines()
        if (line.startswith("+") or line.startswith("-")) and not line.startswith(("+++", "---"))
    )


def _delta(
    pr: PullRequest, previous: Record | None, *, github: GitHubLike, policy: ReviewPolicy
) -> str | None:
    """The diff since the last verdict's head, or None when the whole PR must be judged
    again: no earlier verdict, incremental off, the rerun label, the same head, a base
    GitHub no longer knows (rebase / force-push)."""
    if previous is None or not policy.incremental or policy.rerun_label in pr.labels:
        return None
    base = str(previous.data.get("sha") or "")
    if not base or base == pr.head_sha:
        return None
    try:
        delta = github.compare_diff(pr.repository, base, pr.head_sha)
    except GitHubError:
        return None
    return delta if delta.strip() else None


def _notes(pr: PullRequest, previous: Record, *, github: GitHubLike) -> str:
    check_id = previous.data.get("check_run_id")
    earlier = ""
    if check_id:
        try:
            earlier = github.check_run_text(pr.repository, int(check_id))
        except GitHubError:
            earlier = ""  # a purged check run: the verdict stands, its text is gone
    return (
        f"This pull request was reviewed before at {previous.data.get('sha')} with the verdict "
        f"{previous.data.get('verdict')}. The earlier review said:\n"
        f"{earlier or '(no text kept)'}\n\n"
        "The diff below is only what changed since that review. Approve only if every earlier "
        "finding is addressed by these changes and they introduce nothing blocking or important; "
        "a finding about code outside this delta must quote the earlier review."
    )


def _merge(replies: list[JudgeReply], mode: str, truncated: bool) -> ReviewVerdict:
    """`truncated` is only what the caller knows BEFORE judging: a file whose own patch cannot
    be bounded. The judges know the rest — `judge()` bounds its prompt in UTF-8 bytes and
    shrinks the diff again when a provider takes it in argv, so a piece that fitted the split
    budget in characters can still reach the model cut. Only its reply records that. A merged
    verdict is truncated when ANY piece was, or the receipt claims a whole change was read."""
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
        diff_truncated=truncated or any(v.diff_truncated for v in verdicts),
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
    pr,
    diff,
    policy,
    chain,
    *,
    tier,
    criteria,
    run_judge,
    root,
    failures,
    wanted: int,
    notes: str = "",
) -> list[JudgeReply]:
    """Walk the chain until `wanted` verdicts are in hand; a failure is logged, never fatal."""
    replies: list[JudgeReply] = []
    for provider in chain:
        reply = run_judge(
            pr,
            diff,
            policy,
            provider=provider,
            tier=tier,
            criteria=criteria,
            root=root,
            **({"notes": notes} if notes else {}),
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
    try:
        return _review_started(
            pr,
            check,
            github=github,
            policy=policy,
            ledger=ledger,
            project=project,
            repo_path=repo_path,
            run_judge=run_judge,
            root=root,
        )
    except Exception as exc:  # never leave a check in progress: fail it, then surface the crash
        github.complete_check(
            pr.repository,
            check.id,
            conclusion="failure",
            title="reviewer error",
            summary=f"{type(exc).__name__}: {exc}"[:65535],
            text="The reviewer crashed before a verdict; re-run it (label rail-review:rerun).",
        )
        raise


def _publish(
    pr: PullRequest,
    check: Any,
    verdict: ReviewVerdict,
    title: str,
    *,
    github: GitHubLike,
    policy: ReviewPolicy,
    ledger: Ledger,
    project: str,
    repo_path: Path | None,
    failures: list[str],
) -> ReviewOutcome:
    # Attest FIRST: GitHub must never show an approval the ledger does not hold (found by the
    # independent reviewer on PR #3). The check run exists already (in progress), so its id is
    # part of the attestation; the conclusion and the PR review follow the ledger's answer.
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
        replay = f"rail attest review_verdict --from {exc.receipt}"
        failures.append(f"unattested ({exc.cause}): replay with {replay}")
        outcome.receipt = exc.receipt
        note = (
            f"The judges said {verdict.verdict}, but the verdict could not be attested in the "
            f"ledger ({exc.cause}). Replay it with `{replay}`, then re-run the review "
            f"(label {policy.rerun_label})."
        )
        github.complete_check(
            pr.repository,
            check.id,
            conclusion="failure",
            title="verdict not attested",
            summary=note,
            text=_render(verdict),
        )
        github.review(
            pr.repository,
            pr.number,
            commit_id=pr.head_sha,
            event="REQUEST_CHANGES",
            body=note + "\n\n" + _render(verdict),
        )
        return outcome
    outcome.attested = True
    if repo_path is not None:
        outcome.receipt = repo_path / RECEIPTS_DIR / receipt_filename(record)
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
    return outcome


def _review_started(
    pr: PullRequest,
    check: Any,
    *,
    github: GitHubLike,
    policy: ReviewPolicy,
    ledger: Ledger,
    project: str,
    repo_path: Path | None,
    run_judge: RunJudge,
    root: Path | None,
) -> ReviewOutcome:
    history = previous_verdicts(ledger, project, pr)
    forced = policy.rerun_label in pr.labels
    if len(history) >= policy.max_passes_per_pr and not forced:
        verdict = ReviewVerdict(
            verdict="request_changes",
            summary=(
                f"review budget exhausted: {len(history)} passes on this pull request "
                f"(max {policy.max_passes_per_pr}); squash the fix-ups, then add the label "
                f"{policy.rerun_label} for one more review"
            ),
            findings=[],
            mode="budget",
            providers=(),
            diff_truncated=False,
        )
        return _publish(
            pr,
            check,
            verdict,
            "review budget exhausted",
            github=github,
            policy=policy,
            ledger=ledger,
            project=project,
            repo_path=repo_path,
            failures=[],
        )
    failures: list[str] = []
    previous = history[-1] if history else None
    delta = _delta(pr, previous, github=github, policy=policy)
    if delta is not None:
        assert previous is not None
        diff, notes = delta, _notes(pr, previous, github=github)
        light = changed_lines(delta) <= policy.light_max_changed_lines
    else:
        diff, notes = github.diff(pr.repository, pr.number), ""
        light = policy.mode_for(pr, docs_only=docs_only(diff, policy)) == "light"
    producer = producer_provider(github.commit_messages(pr.repository, pr.number))
    chain = policy.chain_for(producer=producer)
    criteria = _criteria(ledger, project)
    common = dict(criteria=criteria, run_judge=run_judge, root=root, failures=failures, notes=notes)
    # One depth for the whole review, computed once from the change. The chunked path below
    # used to hardcode `deep` and never read `light`, so a docs-only pull request big enough
    # to be split woke the deep models on every piece — the cost the light tier exists to
    # avoid — and attested a depth the review never had.
    tier = "light" if light else "deep"
    mode = "incremental" if delta is not None else tier

    # A change larger than one judge can hold is read in bounded pieces, cut only between
    # files. The old behaviour handed over `diff[:budget]` and recorded that it had: measured
    # on the first external pull request, 21% of the change, ruled `approve`. Only a file too
    # large to bound on its own still counts as truncated.
    chunks = split_diff(diff, budget=policy.max_diff_chars)
    unbounded = oversized(chunks, budget=policy.max_diff_chars)
    truncated = bool(unbounded)
    if len(chunks) > 1:
        replies = []
        for index, chunk in enumerate(chunks, start=1):
            part = f"{notes}\n\n_Part {index} of {len(chunks)} of this change._".strip()
            replies.extend(
                _judge_chain(
                    pr,
                    chunk,
                    policy,
                    chain,
                    tier=tier,
                    wanted=1,
                    **{**common, "notes": part},
                )
            )
        verdict = (
            _merge(replies, mode, truncated)
            if any(r.verdict for r in replies)
            else ReviewVerdict(
                verdict="request_changes",
                summary=f"no verdict: {'; '.join(failures) or 'no judge available'}",
                findings=[],
                mode=mode,
                providers=(),
                diff_truncated=truncated,
            )
        )
        return _publish(
            pr,
            check,
            verdict,
            verdict.verdict,
            github=github,
            policy=policy,
            ledger=ledger,
            project=project,
            repo_path=repo_path,
            failures=failures,
        )
    if light:
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
    return _publish(
        pr,
        check,
        verdict,
        title,
        github=github,
        policy=policy,
        ledger=ledger,
        project=project,
        repo_path=repo_path,
        failures=failures,
    )
