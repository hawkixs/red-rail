"""Pull-mode reviewer: for each watched repository, every open non-draft PR whose head SHA
has no completed `red-rail/review` check of ours (or carries the rerun label) gets exactly
one review. Fail-closed: no verdict → failure + REQUEST_CHANGES, never neutral."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from rail.ledger import (
    RECEIPTS_DIR,
    AttestationKind,
    Ledger,
    Record,
    RecordKind,
    Unattested,
    bindings_of,
    idempotency_key_for,
)
from rail.ledger.file import receipt_filename
from rail.reviewer import carry, rounds
from rail.reviewer.github import GitHubError, PullRequest
from rail.reviewer.judges import JudgeReply, diff_budget, judge, round_instructions
from rail.reviewer.policy import ReviewPolicy, producer_provider
from rail.reviewer.split import oversized, split_diff
from rail.reviewer.verdict import CarryForwards, Finding, PreviousAnswer, ReviewVerdict

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


def _criteria(ledger: Ledger, project: str, pr: PullRequest) -> list[str] | None:
    """The contract's acceptance criteria when `rail bind` tied this pull request to it; None
    when nothing did. The contract is the manifest ticket's, which may describe other work:
    measured on red-rail#46 and #47, both blocked for "not meeting" the criteria of an accepted
    phase they were never part of (ticket 155d3d67)."""
    if not bindings_of(ledger, project, pr.repository, pr.number):
        return None
    contracts = ledger.list(project, kind=RecordKind.CONTRACT)
    if not contracts:
        return []
    return [str(c) for c in contracts[-1].data.get("contract", {}).get("acceptance_criteria", [])]


def _files(diff: str) -> list[str]:
    return _DIFF_HEADER.findall(diff)


# Bytes of other parts' file names a part's notes may list: with the fixed sentence around
# them, the part marker stays inside PROMPT_MARGIN, which the split budget leaves free.
_OTHER_FILES_BYTES = 480


def _part_notes(notes: str, index: int, chunks: list[str]) -> str:
    """What a judge of one part must know about the others: their files exist and are judged
    separately, so nothing it cannot see is absent from the change."""
    others = sorted({f for i, c in enumerate(chunks, start=1) if i != index for f in _files(c)})
    listed: list[str] = []
    for name in others:
        if len(", ".join([*listed, name]).encode("utf-8")) > _OTHER_FILES_BYTES:
            break
        listed.append(name)
    left_out = len(others) - len(listed)
    names = ", ".join(listed) + (f" and {left_out} more" if left_out else "")
    part = (
        f"_Part {index} of {len(chunks)} of this change._ Other parts, judged separately, touch: "
        f"{names or '(no file)'}. You read one part only: never conclude that "
        "something is absent from the change, and never block on a file you did not read."
    )
    return f"{notes}\n\n{part}".strip()


def _path(name: str) -> str:
    """A judge's spelling of a path, as the diff header writes it."""
    for prefix in ("./", "a/", "b/"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _within_part(reply: JudgeReply, files: list[str]) -> JudgeReply:
    """A judge that read one part cannot block on a file it did not read (measured on #46: three
    "missing" findings about code that sat in other parts). Such a finding stays, as important;
    a reply left with no blocking finding approves."""
    if reply.verdict is None:
        return reply
    moved = False
    findings: list[Finding] = []
    for f in reply.verdict.findings:
        if f.severity == "blocking" and _path(f.file) not in files:
            moved = True
            f = f.model_copy(
                update={
                    "severity": "important",
                    "evidence": f"{f.evidence} [not in the part this judge read]",
                }
            )
        findings.append(f)
    if not moved:
        return reply
    verdict = reply.verdict.model_copy(update={"findings": findings})
    if not any(f.severity == "blocking" for f in findings):
        verdict = verdict.model_copy(update={"verdict": "approve"})
    return replace(reply, verdict=verdict)


def previous_verdicts(ledger: Ledger, project: str, pr: PullRequest) -> list[Record]:
    """This pull request's earlier verdicts, oldest first — the ledger is the pass counter."""
    return [
        r
        for r in ledger.list(project, attestation=AttestationKind.REVIEW_VERDICT)
        if r.data.get("repository") == pr.repository and r.data.get("pr") == pr.number
    ]


def rulings_of(ledger: Ledger, project: str, pr: PullRequest) -> list[Record]:
    """This pull request's operator rulings, oldest first (spec 2026-09-25, D9)."""
    return [
        r
        for r in ledger.list(project, attestation=AttestationKind.REVIEW_RULING)
        if r.data.get("repository") == pr.repository and r.data.get("pr") == pr.number
    ]


def changed_lines(diff: str) -> int:
    return sum(
        1
        for line in diff.splitlines()
        if (line.startswith("+") or line.startswith("-")) and not line.startswith(("+++", "---"))
    )


def _delta(
    pr: PullRequest,
    previous: Record | None,
    whole: str,
    *,
    github: GitHubLike,
    policy: ReviewPolicy,
) -> str | None:
    """The diff since the last verdict's head, or None when the whole PR must be judged
    again: no earlier verdict, incremental off, the rerun label, the same head, a base
    GitHub no longer knows (rebase / force-push), or a delta touching a file the PR's own
    diff (`whole`, base...head) does not — the base branch merged into the head, whose
    changes are not the PR's to judge (measured on hawkixs/red-rail#46)."""
    if previous is None or not policy.incremental or policy.rerun_label in pr.labels:
        return None
    base = str(previous.data.get("sha") or "")
    if not base or base == pr.head_sha:
        return None
    try:
        delta = github.compare_diff(pr.repository, base, pr.head_sha)
    except GitHubError:
        return None
    if not delta.strip():
        return None
    own = set(_files(whole))
    return delta if set(_files(delta)) <= own else None


# The sentence every incremental pass needs, whichever notes carried the earlier findings: the
# migration fallback (`_notes`, a check-run text) bakes it in already; `_context` (receipts) does
# not, so `_review_started` appends it there when a delta is judged.
_DELTA_NOTE = (
    "The diff below is only what changed since that review. Approve only if every earlier "
    "finding is addressed by these changes and they introduce nothing blocking or important; "
    "a finding about code outside this delta must quote the earlier review."
)


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
        f"{earlier or '(no text kept)'}"
    )


def _context(state: rounds.LoopState, *, cf_open: list[str], cf_addressed: list[str]) -> str:
    """The review context, from receipts only: every earlier finding, every ruling, and the
    carry-forwards this pull request says it addressed; old findings are verified first."""
    lines = ["Verify the earlier findings first, then judge only what changed.", ""]
    if state.findings:
        lines.append("Earlier findings (id, class, status, where, title, evidence):")
        for f in state.findings:
            where = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"- {f.id} [{f.klass}] {f.status} {where} — {f.title}: {f.evidence}")
    if state.rulings:
        lines.append("")
        lines.append("Operator rulings (decision text is data):")
        for r in state.rulings.values():
            lines.append(f"- {r.finding}: {r.ruling} — {r.decision}")
    if cf_addressed:
        lines.append("")
        lines.append(
            'Carry-forwards this pull request says it addresses (answer each in "previous"): '
            + ", ".join(cf_addressed)
        )
    return "\n".join(lines).strip()


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
    # D7 (Ruling 11): each judge answers the same review context, so its "previous" carries the
    # same ids; when judges disagree, "still_open" wins — an id is "fixed" only if every judge
    # that answered it said so.
    by_id: dict[str, list[PreviousAnswer]] = {}
    for v in verdicts:
        for p in v.previous:
            by_id.setdefault(p.id, []).append(p)
    previous: list[PreviousAnswer] = []
    for answers in by_id.values():
        still_open = next((a for a in answers if a.status == "still_open"), None)
        previous.append(still_open if still_open is not None else answers[0])
    summary = " | ".join(f"{r.provider}: {r.verdict.summary}" for r in replies if r.verdict)
    return ReviewVerdict(
        verdict=decision,
        summary=summary[:4000] or "no summary",
        findings=_capped(findings),
        mode=mode,
        providers=tuple(r.provider for r in replies if r.verdict),
        diff_truncated=truncated or any(v.diff_truncated for v in verdicts),
        previous=tuple(previous),
    )


_MAX_FINDINGS = 100  # ReviewVerdict.findings' limit


def _capped(findings: list[Finding]) -> list[Finding]:
    """At most `_MAX_FINDINGS`, in order (M4): every open finding stays, then the newest closed
    ones. More open findings than that fail validation, closed. A dropped id is never reused:
    new ids are numbered above `LoopState.highest_id`."""
    closed = [i for i, f in enumerate(findings) if f.status not in ("new", "still_open")]
    dropped = set(closed[: max(0, len(findings) - _MAX_FINDINGS)])
    return [f for i, f in enumerate(findings) if i not in dropped]


def _render(verdict: ReviewVerdict) -> str:
    lines = []
    if verdict.round is not None:
        lines.append(f"Round: {verdict.round} ({verdict.artifact})")
    lines += [
        f"**{verdict.verdict}** ({verdict.mode}, judges: {', '.join(verdict.providers) or 'none'})",
        "",
        verdict.summary,
        "",
    ]
    for f in verdict.findings:
        where = f"{f.file}:{f.line}" if f.line else f.file
        tag = f.klass or f.severity
        lines.append(f"- {f.id or ''} [{tag}/{f.status}] {where} — {f.title}: {f.evidence}")
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
    instructions: str = "",
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
            **({"instructions": instructions} if instructions else {}),
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


def _awaiting(pr: PullRequest, state: rounds.LoopState, artifact) -> ReviewVerdict:
    """D5: after round 3, an open blocker with no ruling stops the loop; no fourth round."""
    waiting = rounds.unruled(state)
    commands = "\n".join(
        f"rail reviewer rule --repository {pr.repository} --pr {pr.number} --finding {f.id} "
        '--as fix|carry-forward --decision "…"'
        for f in waiting
    )
    return ReviewVerdict(
        verdict="request_changes",
        summary=(
            f"awaiting the operator's ruling after round 3: {len(waiting)} blocker(s) still open "
            f"({', '.join(f.id or '?' for f in waiting)}). No fourth round: rule on each with\n"
            f"{commands}"
        )[:4000],
        findings=_capped(list(state.findings)),
        mode="awaiting_ruling",
        providers=(),
        round="awaiting_ruling",
        artifact=artifact,
    )


def _finish(
    merged: ReviewVerdict,
    *,
    pr: PullRequest,
    state: rounds.LoopState,
    step: str,
    round_: int | None,
    artifact,
    delta: str | None,
    cf_open: list[str],
    section: dict,
    delta_bound: bool = False,
) -> ReviewVerdict:
    """Number, classify and status the findings; apply round 3's demotion and the closure
    check's limits; recompute the carry-forward blockers; decide by D3.

    `delta_bound`: a closure with no `fix` ruling, judged only because the head moved since
    the last judged verdict (Ruling 29). A new finding inside the delta keeps its class, as in
    round 3; anywhere else it is demoted.

    Mechanical carry-forward findings are recomputed from the pull request's body every round
    (`carry.reconcile`), never judged: `judged_state` leaves them out of `known`/`assign` so a
    judge's reply can never claim one by id. The closure check records any new finding as a
    `note` (D10); `rounds.assign` re-derives class from severity for a genuinely new finding, so
    that demotion is applied after `assign`, to the items it just gave `status: "new"` — not to
    the earlier ones, which stay governed by their ruling or their `fixed`/`still_open` answer.
    """
    judged_state = replace(
        state, findings=tuple(f for f in state.findings if not carry.is_mechanical(f))
    )
    known = {f.id for f in judged_state.findings if f.id}
    # I2 (Ruling 31): a judge's finding on the description is judged like any other, so it
    # must not carry the mechanical location, or `reconcile` would close it the next round
    new = [
        rounds.enforce_class(
            f.model_copy(update={"file": carry.JUDGE_WHERE}) if carry.is_mechanical(f) else f,
            artifact,
        )
        for f in merged.findings
    ]
    # C2 (Ruling 12), M4: a new id is numbered above the highest id ANY verdict of this pull
    # request ever held, mechanical blockers and capped-out findings included, not only
    # `judged_state`'s — otherwise a fresh finding could reuse an id already on record.
    findings = rounds.assign(
        new,
        merged.previous,
        judged_state,
        pr=pr.number,
        artifact=artifact,
        floor=state.highest_id,
    )
    if round_ == 3 or delta_bound:
        # after `assign`, which re-derives a new finding's class from its severity: demoted
        # before, a code finding outside the delta came back a blocker
        fresh = iter(
            rounds.demote_outside(
                [f for f in findings if f.status == "new"],
                delta,
                artifact,
                known=known,
                skip=not state.has_findings_list,
            )
        )
        findings = [next(fresh) if f.status == "new" else f for f in findings]
    if step == "closure":
        if not delta_bound:
            findings = [
                f.model_copy(update={"klass": "note"}) if f.status == "new" else f for f in findings
            ]
        findings = rounds.apply_rulings(findings, state.rulings)
    carry_forwards = None
    strangers: list[str] = []
    if artifact == "code":
        answers = {a.id: a.status for a in merged.previous}
        claimed = carry.addressed(cf_open, section)
        confirmed = rounds.confirmed_addressed(state, claimed, answers)
        current = carry.mechanical_blockers(cf_open, section) + [
            carry.not_addressed(i) for i in claimed if i not in confirmed
        ]
        old = [f for f in state.findings if carry.is_mechanical(f)]
        if step == "closure":
            # Ruling 22: a ruling on a mechanical blocker (e.g. a "not addressed" one) applies
            # here too, or `reconcile` would re-derive its status from `current` alone and lose
            # the operator's carry_forward call the moment this closure also has a fix ruling
            # on something else.
            old = rounds.apply_rulings(old, state.rulings)
        kept, fresh = carry.reconcile(old, current)
        findings = rounds.append_new(
            findings + kept, fresh, pr=pr.number, artifact=artifact, floor=state.highest_id
        )
        carry_forwards = CarryForwards(
            addressed=tuple(confirmed), deferred=_deferred(cf_open, section, findings)
        )
        strangers = carry.strangers(cf_open, section)
    findings = _capped(findings)
    decision = "approve" if rounds.approves(findings) else "request_changes"
    summary = merged.summary
    if strangers:
        summary = (summary + " | ids not open: " + ", ".join(strangers))[:4000]
    return merged.model_copy(
        update={
            "verdict": decision,
            "summary": summary,
            "findings": findings,
            "round": "closure" if step == "closure" else round_,
            "artifact": artifact,
            "carry_forwards": carry_forwards,
            "mode": "closure" if step == "closure" else merged.mode,
        }
    )


def _deferred(cf_open: list[str], section: dict, findings: list[Finding]) -> tuple[str, ...]:
    """The body's reasoned deferrals, plus every open carry-forward whose "not addressed" gap
    the operator ruled carry_forward: the ruling's decision is the reason (Ruling 28)."""
    ruled = [i for i in carry.ruled_deferred(findings) if i in cf_open]
    return tuple(dict.fromkeys(carry.deferred(cf_open, section) + ruled))


def _mechanical_recomputed(
    findings: list[Finding],
    *,
    pr: PullRequest,
    artifact,
    state: rounds.LoopState,
    cf_open: list[str],
    section: dict,
) -> list[Finding]:
    """A code pull request's mechanical carry-forward blockers, recomputed from its current
    body (D11): the operator may have updated it since the last pass, with no judge involved
    (Ruling 14, Ruling 16).

    A body claim of "addressed" is not enough on its own (Ruling 18): with no judge running
    here, the only confirmation on record is an earlier judged verdict's
    `carry_forwards.addressed` (Ruling 30) — an id the body claims but no such record confirmed
    keeps its "not addressed" blocker open, the same shape `_finish` builds when a judge is the
    one checking."""
    if artifact != "code":
        return list(findings)
    claimed = carry.addressed(cf_open, section)
    confirmed = rounds.confirmed_addressed(state, claimed, {})
    current = carry.mechanical_blockers(cf_open, section) + [
        carry.not_addressed(i) for i in claimed if i not in confirmed
    ]
    old = [f for f in findings if carry.is_mechanical(f)]
    non_mechanical = [f for f in findings if not carry.is_mechanical(f)]
    kept, fresh = carry.reconcile(old, current)
    return rounds.append_new(
        non_mechanical + kept, fresh, pr=pr.number, artifact=artifact, floor=state.highest_id
    )


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
    rulings = rulings_of(ledger, project, pr)
    state = rounds.loop_state(history, rulings)
    step, round_ = rounds.next_step(state)
    whole = github.diff(pr.repository, pr.number)
    artifact = rounds.artifact_of(_files(whole), policy.records_globs)
    cf_open: list[str] = []
    section: dict = {}
    if artifact == "code":
        every = ledger.list(project, attestation=AttestationKind.REVIEW_VERDICT)
        every_rulings = ledger.list(project, attestation=AttestationKind.REVIEW_RULING)
        cf_open = rounds.open_carry_forwards(
            every, every_rulings, repository=pr.repository, excluding_pr=pr.number
        )
        section = carry.parse_section(pr.body)
    recomputed = dict(pr=pr, artifact=artifact, state=state, cf_open=cf_open, section=section)
    if step == "awaiting_ruling":
        # M8 (Ruling 16): a mechanical blocker never needs a ruling; the body may already have
        # resolved it, so it is recomputed before telling the operator what is still unruled.
        state = replace(
            state,
            findings=tuple(_mechanical_recomputed(list(state.findings), **recomputed)),
        )
        verdict = _awaiting(pr, state, artifact)
        return _publish(
            pr,
            check,
            verdict,
            "awaiting ruling",
            github=github,
            policy=policy,
            ledger=ledger,
            project=project,
            repo_path=repo_path,
            failures=[],
        )
    fix_rulings = {k: r for k, r in state.rulings.items() if r.ruling == "fix"}
    same_head = state.last_judged is not None and pr.head_sha == state.last_judged.data.get("sha")
    if step == "closure" and not fix_rulings and same_head:
        # every open blocker was ruled carry_forward and no judge has anything new to read: the
        # loop closes without a judge (D9). A moved head is judged instead (Ruling 29). A
        # mechanical blocker still gets one last recompute from the body (Ruling 16); the last
        # judged verdict's `addressed` survives (Ruling 14), the deferrals are the body's own
        # plus the ruled ones (M1, Ruling 28).
        findings = rounds.apply_rulings(list(state.findings), state.rulings)
        findings = _mechanical_recomputed(findings, **recomputed)
        carry_forwards = None
        if artifact == "code":
            addressed = (
                rounds.carry_forwards_of(state.last_judged).addressed
                if state.last_judged is not None
                else ()
            )
            carry_forwards = CarryForwards(
                addressed=addressed, deferred=_deferred(cf_open, section, findings)
            )
        verdict = ReviewVerdict(
            verdict="approve" if rounds.approves(findings) else "request_changes",
            summary="closure: every open blocker ruled carry_forward by the operator",
            findings=_capped(findings),
            mode="closure",
            providers=(),
            round="closure",
            artifact=artifact,
            carry_forwards=carry_forwards,
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
            failures=[],
        )

    failures: list[str] = []
    cf_addressed = carry.addressed(cf_open, section)

    # I3: the context is built from the judged state, mechanical findings removed — they are
    # never the judge's to answer. M11 (D10): a closure check verifies the fix-ruled findings
    # only, with their ruling's decision text, not every earlier finding.
    judged_state = replace(
        state, findings=tuple(f for f in state.findings if not carry.is_mechanical(f))
    )
    if step == "closure":
        context_state = replace(
            judged_state,
            findings=tuple(f for f in judged_state.findings if f.id in fix_rulings),
            rulings=fix_rulings,
        )
    else:
        context_state = judged_state

    delta = _delta(pr, state.last_judged, whole, github=github, policy=policy)
    if context_state.findings or context_state.rulings or cf_addressed:
        notes = _context(context_state, cf_open=cf_open, cf_addressed=cf_addressed)
    elif not state.has_findings_list and state.last_judged is not None:
        # M7: a verdict recorded before this change carries no findings list — the check-run
        # fallback of D6 covers only that migration case, never an explicit empty list.
        notes = _notes(pr, state.last_judged, github=github)
    else:
        notes = ""
    if delta is not None and _DELTA_NOTE not in notes:
        notes = f"{notes}\n\n{_DELTA_NOTE}".strip()
    if delta is not None:
        diff = delta
        light = changed_lines(delta) <= policy.light_max_changed_lines
    else:
        diff = whole
        light = policy.mode_for(pr, docs_only=docs_only(diff, policy)) == "light"
    producer = producer_provider(github.commit_messages(pr.repository, pr.number))
    chain = policy.chain_for(producer=producer)
    criteria = _criteria(ledger, project, pr)
    instructions = round_instructions("closure" if step == "closure" else "round", round_, artifact)
    common = dict(
        criteria=criteria,
        run_judge=run_judge,
        root=root,
        failures=failures,
        notes=notes,
        instructions=instructions,
    )
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
    budget = diff_budget(
        pr, policy, chain, criteria=criteria, notes=notes, instructions=instructions
    )
    chunks = split_diff(diff, budget=budget)
    unbounded = oversized(chunks, budget=budget)
    truncated = bool(unbounded)
    if len(chunks) > 1:
        replies = []
        for index, chunk in enumerate(chunks, start=1):
            part = _part_notes(notes, index, chunks)
            replies.extend(
                _within_part(reply, _files(chunk))
                for reply in _judge_chain(
                    pr,
                    chunk,
                    policy,
                    chain,
                    tier=tier,
                    wanted=1,
                    **{**common, "notes": part},
                )
            )
        merged = _merge(replies, mode, truncated) if any(r.verdict for r in replies) else None
    else:
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
        merged = _merge(replies, mode, truncated) if any(r.verdict for r in replies) else None

    if merged is None:
        # No verdict at all: fail closed. An outage is not a judged round (Ruling 32): it is
        # recorded as "no_verdict", which moves neither the round counter nor `last_judged`, so
        # an outage never burns a round and never ages the rulings of a closure check out of
        # `state.rulings`. The open findings stay open until a real judge answers them.
        findings = [
            f.model_copy(update={"status": "still_open"}) if f.open_blocker else f
            for f in state.findings
        ]
        verdict = ReviewVerdict(
            verdict="request_changes",
            summary=f"no verdict: {'; '.join(failures) or 'no judge available'}",
            findings=_capped(findings),
            mode=mode,
            providers=(),
            diff_truncated=truncated,
            round="no_verdict",
            artifact=artifact,
        )
        title = "no verdict"
    else:
        verdict = _finish(
            merged,
            pr=pr,
            state=state,
            step=step,
            round_=round_,
            artifact=artifact,
            delta=delta,
            cf_open=cf_open,
            section=section,
            delta_bound=step == "closure" and not fix_rulings,
        )
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
