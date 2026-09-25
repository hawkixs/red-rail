"""One review: check run started → judges → verdict → check completed + PR review →
`review_verdict` attested through the project's own ledger. Fail-closed on every gap."""

from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from rail.ledger import RECEIPTS_DIR, AttestationKind, Contract, Deliverable, PullRequestRef
from rail.ledger.file import FileLedger
from rail.reviewer.github import CheckRun, PullRequest
from rail.reviewer.judges import JudgeReply, build_prompt
from rail.reviewer.policy import default_policy
from rail.reviewer.service import _DELTA_NOTE, docs_only, needs_review, review_pull
from rail.reviewer.verdict import Finding, PreviousAnswer, ReviewVerdict
from tests.helpers import conforming_tree

PR = PullRequest(
    repository="hawkixs/red-alpha",
    number=7,
    title="feat: x",
    body="",
    draft=False,
    author="hawkixs",
    head_sha="a" * 40,
    base_sha="b" * 40,
    labels=(),
    additions=20,
    deletions=2,
    changed_files=1,
)
DIFF = "diff --git a/src/x.py b/src/x.py\n+print(1)\n"


@dataclass
class FakeGitHub:
    diff_text: str = DIFF
    messages: list[str] = field(
        default_factory=lambda: ["feat: x\n\nCo-Authored-By: Claude Opus 5 <n@a>"]
    )
    existing_checks: list[CheckRun] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)
    compare_text: str = "diff --git a/src/x.py b/src/x.py\n+print(2)\n"
    compare_error: bool = False
    check_texts: dict[int, str] = field(default_factory=dict)

    def diff(self, repository, number):
        return self.diff_text

    def commit_messages(self, repository, number):
        return self.messages

    def check_runs(self, repository, sha, *, name):
        return self.existing_checks

    def start_check(self, repository, head_sha, *, name):
        self.calls.append(("start", name, head_sha))
        return CheckRun(id=99, status="in_progress", conclusion=None)

    def complete_check(self, repository, check_id, *, conclusion, title, summary, text=""):
        self.calls.append(("complete", check_id, conclusion, title))
        return CheckRun(id=check_id, status="completed", conclusion=conclusion)

    def review(self, repository, number, *, commit_id, event, body):
        self.calls.append(("review", number, event))

    def remove_label(self, repository, number, label):
        self.calls.append(("unlabel", number, label))

    def compare_diff(self, repository, base_sha, head_sha):
        self.calls.append(("compare", base_sha, head_sha))
        if self.compare_error:
            from rail.reviewer.github import GitHubError

            raise GitHubError("404 no common ancestor")
        return self.compare_text

    def check_run_text(self, repository, check_id):
        return self.check_texts.get(check_id, "")


def approve(provider: str, tier: str = "light") -> JudgeReply:
    verdict = ReviewVerdict(
        verdict="approve", summary="clean", findings=[], mode=tier, providers=(provider,)
    )
    return JudgeReply(
        provider=provider, tier=tier, model="m", verdict=verdict, failure=None, raw=""
    )


def block(provider: str, tier: str = "light") -> JudgeReply:
    finding = Finding(severity="blocking", file="src/x.py", line=1, title="bug", evidence="e")
    verdict = ReviewVerdict(
        verdict="request_changes",
        summary="no",
        findings=[finding],
        mode=tier,
        providers=(provider,),
    )
    return JudgeReply(
        provider=provider, tier=tier, model="m", verdict=verdict, failure=None, raw=""
    )


def fail(provider: str, tier: str = "light") -> JudgeReply:
    return JudgeReply(
        provider=provider, tier=tier, model="m", verdict=None, failure="timeout", raw=""
    )


def _repo(tmp_path: Path) -> tuple[Path, FileLedger]:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    ledger = FileLedger(repo / RECEIPTS_DIR)
    ledger.contract_set(
        "red-alpha",
        Contract(
            objective="x",
            acceptance_criteria=["tests pass"],
            deliverables=[
                Deliverable(
                    key="main",
                    repository="hawkixs/red-alpha",
                    no_checks_reason="fixture: no check declared",
                )
            ],
        ),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c1",
    )
    return repo, ledger


def test_needs_review_once_per_head_sha_unless_relabelled() -> None:
    policy = default_policy()
    github = FakeGitHub()
    assert needs_review(PR, github=github, policy=policy)
    github.existing_checks = [CheckRun(id=1, status="completed", conclusion="success")]
    assert not needs_review(PR, github=github, policy=policy)
    relabelled = replace(PR, labels=("rail-review:rerun",))
    assert needs_review(relabelled, github=github, policy=policy)
    draft = replace(PR, draft=True)
    github.existing_checks = []
    assert not needs_review(draft, github=github, policy=policy)


def test_docs_only_reads_the_diff_headers() -> None:
    assert docs_only(
        "diff --git a/docs/x.md b/docs/x.md\n+x\ndiff --git a/README.md b/README.md\n",
        default_policy(),
    )
    assert not docs_only(
        "diff --git a/docs/x.md b/docs/x.md\ndiff --git a/src/a.py b/src/a.py\n", default_policy()
    )


def _bind(ledger: FileLedger, pr: PullRequest = PR) -> None:
    ledger.bind(
        "red-alpha",
        PullRequestRef(repository=pr.repository, number=pr.number, head_sha=pr.head_sha),
        issuer="op",
        idempotency_key=f"bind:{pr.repository}:{pr.number}",
    )


def test_light_review_approves_publishes_and_attests(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    _bind(ledger)
    github = FakeGitHub()
    seen = []

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None, instructions=""):
        seen.append((provider, tier, tuple(criteria)))
        return approve(provider, tier)

    outcome = review_pull(
        PR,
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        repo_path=repo,
        run_judge=run_judge,
        root=tmp_path,
    )
    assert seen == [("agy", "light", ("tests pass",))]  # never the producer (claude), light mode
    assert outcome.verdict.verdict == "approve" and outcome.check_run_id == 99
    assert ("complete", 99, "success", "approve") in github.calls
    assert ("review", 7, "APPROVE") in github.calls
    records = ledger.list("red-alpha", attestation=AttestationKind.REVIEW_VERDICT)
    assert len(records) == 1 and records[0].issuer == "red-rail-reviewer"
    assert records[0].idempotency_key == f"review_verdict:{'a' * 40}:99"
    assert records[0].data["independent"] is True and records[0].data["verdict"] == "approve"
    assert outcome.attested and outcome.receipt is not None and outcome.receipt.is_file()


def test_deep_review_escalates_on_disagreement_and_the_deep_judge_wins(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    big = replace(PR, additions=900)
    github = FakeGitHub(messages=["chore: plain"])
    seen = []

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None, instructions=""):
        seen.append((provider, tier))
        if tier == "deep":
            return block(provider, tier)
        return approve(provider, tier) if provider == "agy" else block(provider, tier)

    outcome = review_pull(
        big,
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        repo_path=repo,
        run_judge=run_judge,
        root=tmp_path,
    )
    # unknown producer → claude excluded; the deep judge falls back to the head of the chain
    assert seen == [("agy", "light"), ("codex", "light"), ("agy", "deep")]
    assert outcome.verdict.verdict == "request_changes" and outcome.verdict.mode == "deep"
    assert ("complete", 99, "failure", "request_changes") in github.calls
    assert ("review", 7, "REQUEST_CHANGES") in github.calls


def test_a_failed_judge_walks_the_chain(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    github = FakeGitHub(messages=["chore: plain"])
    seen = []

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None, instructions=""):
        seen.append(provider)
        return fail(provider) if provider == "agy" else approve(provider)

    outcome = review_pull(
        PR,
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        repo_path=repo,
        run_judge=run_judge,
        root=tmp_path,
    )
    assert seen == ["agy", "codex"] and outcome.verdict.verdict == "approve"
    assert outcome.verdict.providers == ("codex",) and "timeout" in outcome.failures[0]


def test_no_verdict_at_all_fails_closed(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    github = FakeGitHub(messages=["chore: plain"])
    outcome = review_pull(
        PR,
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        repo_path=repo,
        run_judge=lambda pr, diff, policy, *, provider, tier, criteria, **_: fail(
            provider, tier
        ),
        root=tmp_path,
    )
    assert outcome.verdict.verdict == "request_changes" and "no verdict" in outcome.verdict.summary
    assert ("complete", 99, "failure", "no verdict") in github.calls
    assert ("review", 7, "REQUEST_CHANGES") in github.calls
    assert (
        ledger.list("red-alpha", attestation=AttestationKind.REVIEW_VERDICT)[0].data["verdict"]
        == "request_changes"
    )


def test_the_rerun_label_is_removed_after_the_review(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    relabelled = replace(PR, labels=("rail-review:rerun",))
    github = FakeGitHub(messages=["chore: plain"])
    review_pull(
        relabelled,
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        repo_path=repo,
        run_judge=lambda pr, diff, policy, *, provider, tier, criteria, **_: approve(
            provider, tier
        ),
        root=tmp_path,
    )
    assert ("unlabel", 7, "rail-review:rerun") in github.calls


def test_an_unattested_verdict_is_reported_not_fatal(tmp_path: Path) -> None:
    from rail.ledger import Unattested

    repo, ledger = _repo(tmp_path)
    github = FakeGitHub(messages=["chore: plain"])

    class RefusingLedger:
        def list(self, project, **kwargs):
            return ledger.list(project, **kwargs)

        def attest(self, project, kind, data, *, issuer, idempotency_key, emitted_at=None):
            raise Unattested(
                tmp_path / "docs" / "receipts" / "x-review_verdict-y.json", "delivery_disabled"
            )

    outcome = review_pull(
        PR,
        github=github,
        policy=default_policy(),
        ledger=RefusingLedger(),
        project="red-alpha",
        repo_path=repo,
        run_judge=lambda pr, diff, policy, *, provider, tier, criteria, **_: approve(
            provider, tier
        ),
        root=tmp_path,
    )
    assert not outcome.attested and "delivery_disabled" in outcome.failures[-1]
    # found by the independent reviewer (PR #3, third pass): never an approval GitHub shows
    # that the ledger does not hold — the check fails and no APPROVE is posted
    assert ("complete", 99, "failure", "verdict not attested") in github.calls
    assert ("review", 7, "REQUEST_CHANGES") in github.calls
    assert ("review", 7, "APPROVE") not in github.calls


def test_a_crash_after_the_check_started_completes_it_as_failure(tmp_path: Path) -> None:
    """Found by the independent reviewer on its first run (PR #3, codex judge): an exception
    after `start_check` left the check in progress forever."""
    import pytest

    repo, ledger = _repo(tmp_path)
    github = FakeGitHub(messages=["chore: plain"])

    def exploding_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, instructions=""
    ):
        raise RuntimeError("judge exploded")

    with pytest.raises(RuntimeError, match="judge exploded"):
        review_pull(
            PR,
            github=github,
            policy=default_policy(),
            ledger=ledger,
            project="red-alpha",
            repo_path=repo,
            run_judge=exploding_judge,
            root=tmp_path,
        )
    assert ("complete", 99, "failure", "reviewer error") in github.calls


def test_a_review_in_progress_is_not_started_again() -> None:
    """Found by the independent reviewer (PR #3, sixth pass): only completed checks counted, so
    a second reviewer process (or a crashed one) could start the same review twice."""
    policy = default_policy()
    github = FakeGitHub()
    github.existing_checks = [CheckRun(id=1, status="in_progress", conclusion=None)]
    assert not needs_review(PR, github=github, policy=policy)
    relabelled = replace(PR, labels=("rail-review:rerun",))
    assert needs_review(relabelled, github=github, policy=policy)


def test_pending_reviews_are_loaded_in_full_before_judging() -> None:
    """Measured on PR #3 (eighth pass): GitHub's list endpoint carries no additions/deletions,
    so a 12k-line PR reached the judges as a 0-line one and got a light review."""
    from rail.reviewer.service import pending_reviews

    policy = default_policy()
    summary = replace(PR, additions=0, deletions=0, changed_files=0)
    full = replace(PR, additions=900)

    class ListingGitHub(FakeGitHub):
        def open_pulls(self, repository):
            return [summary, replace(summary, number=8, draft=True)]

        def pull(self, repository, number):
            assert number == 7
            return full

    github = ListingGitHub()
    loaded = pending_reviews(github, "hawkixs/red-alpha", policy)
    assert loaded == [full]
    assert policy.mode_for(loaded[0], docs_only=False) == "deep"


def _earlier_verdict(
    ledger: FileLedger, *, sha: str, check_run_id: int, verdict: str = "request_changes"
) -> None:
    """A verdict recorded before this change: `findings` is a bare count, not a list — its
    findings survive only in the check-run text (D6's migration fallback, M7)."""
    data = {
        "sha": sha, "independent": True, "verdict": verdict, "check_run_id": check_run_id,
        "repository": PR.repository, "pr": PR.number, "mode": "deep", "providers": ["codex"],
        "finding_count": 0, "blocking": False, "diff_truncated": False, "round": None,
        "artifact": None, "findings": 0,
    }
    ledger.attest(
        "red-alpha",
        AttestationKind.REVIEW_VERDICT,
        data,
        issuer="red-rail-reviewer",
        idempotency_key=f"review_verdict:{sha}:{check_run_id}",
    )


def test_a_second_pass_judges_the_delta_with_the_earlier_findings(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    _earlier_verdict(ledger, sha="0" * 40, check_run_id=11)
    github = FakeGitHub(check_texts={11: "- [important] src/x.py:1 — bug: e"})
    seen: list[dict] = []

    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        seen.append({"diff": diff, "tier": tier, "notes": notes})
        return approve(provider, tier)

    outcome = review_pull(
        PR,
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        run_judge=run_judge,
    )
    assert outcome.verdict.mode == "incremental" and outcome.verdict.verdict == "approve"
    assert ("compare", "0" * 40, PR.head_sha) in github.calls
    assert len(seen) == 1 and seen[0]["tier"] == "light"
    assert seen[0]["diff"] == github.compare_text  # the delta, not the whole PR
    assert "0" * 40 in seen[0]["notes"] and "bug: e" in seen[0]["notes"]
    assert ("complete", 99, "success", "approve") in github.calls


def test_a_rebased_head_or_the_label_gets_a_full_review_again(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    _earlier_verdict(ledger, sha="0" * 40, check_run_id=11)
    github = FakeGitHub(compare_error=True)
    seen: list[str] = []

    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        seen.append(diff)
        return approve(provider, tier)

    outcome = review_pull(
        PR,
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        run_judge=run_judge,
    )
    assert outcome.verdict.mode == "light" and seen == [DIFF]
    github = FakeGitHub()
    seen.clear()
    review_pull(
        replace(PR, labels=("rail-review:rerun",), head_sha="c" * 40),
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        run_judge=run_judge,
    )
    assert seen == [DIFF] and not any(c[0] == "compare" for c in github.calls)


def test_a_delta_reaching_outside_the_pull_request_gets_a_full_review(tmp_path: Path) -> None:
    # Measured on hawkixs/red-rail#46: the base branch merged into the head brought another
    # feature's files into compare(previous, head), and the delta pass judged them as the PR's.
    repo, ledger = _repo(tmp_path)
    _earlier_verdict(ledger, sha="0" * 40, check_run_id=11)
    github = FakeGitHub(
        compare_text=(
            "diff --git a/src/x.py b/src/x.py\n+print(2)\n"
            "diff --git a/docs/other.md b/docs/other.md\n+from the base branch\n"
        )
    )
    seen: list[str] = []

    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        seen.append(diff)
        return approve(provider, tier)

    outcome = review_pull(
        PR,
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        run_judge=run_judge,
    )
    assert outcome.verdict.mode != "incremental"
    assert seen == [DIFF]  # the whole PR diff (base...head), not the delta


def test_a_change_larger_than_the_budget_is_read_in_pieces_not_cut(tmp_path: Path) -> None:
    """Measured on the first external pull request: 972 686 characters of diff against a
    200 000 budget, so the judge ruled `approve` on 21% of the change and the gate went
    green. Every file now reaches a judge, and nothing claims to be truncated when it is
    not — raising the budget would have removed the flag without adding the reading."""
    repo, ledger = _repo(tmp_path)

    def one(name: str, lines: int) -> str:
        return f"diff --git a/{name} b/{name}\n" + "".join(f"+l{i}\n" for i in range(lines))

    whole = one("a.go", 400) + one("b.go", 400) + one("c.go", 400)
    github = FakeGitHub(diff_text=whole, messages=["chore: plain"])
    policy = default_policy().model_copy(update={"max_diff_chars": len(one("a.go", 400)) + 20})
    seen: list[str] = []

    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        seen.append(diff)
        return approve(provider, tier)

    outcome = review_pull(
        replace(PR, additions=1200),
        github=github,
        policy=policy,
        ledger=ledger,
        project="red-alpha",
        repo_path=repo,
        run_judge=run_judge,
        root=tmp_path,
    )

    assert len(seen) == 3, "one judge per bounded piece"
    assert "".join(seen) == whole, "every byte of the change reached a judge"
    assert outcome.verdict.diff_truncated is False, "nothing was cut, so nothing is truncated"
    assert outcome.verdict.verdict == "approve"

    records = ledger.list("red-alpha", attestation=AttestationKind.REVIEW_VERDICT)
    assert records[-1].data["diff_truncated"] is False


def test_a_judge_that_had_to_cut_its_piece_makes_the_merged_verdict_truncated(
    tmp_path: Path,
) -> None:
    """Splitting bounds a piece in CHARACTERS; `judge()` bounds the prompt in UTF-8 BYTES and
    shrinks the diff again when a provider takes its prompt in argv. So a piece that fitted
    the split budget can still reach the model cut in half, and only the judge knows it. The
    merged verdict has to take the judges' word, not the caller's arithmetic — otherwise the
    receipt says `diff_truncated: false` about a review of half the code, which is the defect
    the split was written to remove."""
    repo, ledger = _repo(tmp_path)

    def one(name: str, lines: int) -> str:
        return f"diff --git a/{name} b/{name}\n" + "".join(f"+l{i}\n" for i in range(lines))

    whole = one("a.go", 400) + one("b.go", 400) + one("c.go", 400)
    github = FakeGitHub(diff_text=whole, messages=["chore: plain"])
    policy = default_policy().model_copy(update={"max_diff_chars": len(one("a.go", 400)) + 20})
    seen: list[str] = []

    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        seen.append(diff)
        reply = approve(provider, tier)
        if len(seen) != 2:  # the second piece is the one its judge could not hold
            return reply
        return replace(reply, verdict=reply.verdict.model_copy(update={"diff_truncated": True}))

    outcome = review_pull(
        replace(PR, additions=1200),
        github=github,
        policy=policy,
        ledger=ledger,
        project="red-alpha",
        repo_path=repo,
        run_judge=run_judge,
        root=tmp_path,
    )

    assert len(seen) == 3, "the change was still read in three pieces"
    assert outcome.verdict.diff_truncated is True, "one judge cut its piece: the verdict is cut"

    # The receipt is what `review.verdict` reads: a truncated one is refused by the gate
    # (tests/test_gates_evidence.py), which is the whole point of not lying here.
    records = ledger.list("red-alpha", attestation=AttestationKind.REVIEW_VERDICT)
    assert records[-1].data["diff_truncated"] is True


def test_a_large_docs_only_change_is_still_read_light_when_it_is_split(tmp_path: Path) -> None:
    """`light` is computed once, from the change; the chunked path used to hardcode
    `tier="deep"` and `mode="deep"` and never look at it. A docs-only pull request big enough
    to be split therefore woke the deep models on every piece — the exact cost the light tier
    exists to avoid — and the receipt claimed a depth the review never had."""
    repo, ledger = _repo(tmp_path)

    def one(name: str, lines: int) -> str:
        return f"diff --git a/{name} b/{name}\n" + "".join(f"+l{i}\n" for i in range(lines))

    whole = one("docs/a.md", 400) + one("docs/b.md", 400) + one("docs/c.md", 400)
    github = FakeGitHub(diff_text=whole, messages=["docs: plain"])
    policy = default_policy().model_copy(update={"max_diff_chars": len(one("docs/a.md", 400)) + 20})
    tiers: list[str] = []

    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        tiers.append(tier)
        return approve(provider, tier)

    outcome = review_pull(
        replace(PR, additions=1200),
        github=github,
        policy=policy,
        ledger=ledger,
        project="red-alpha",
        repo_path=repo,
        run_judge=run_judge,
        root=tmp_path,
    )

    assert len(tiers) == 3, "still one judge per bounded piece"
    assert set(tiers) == {"light"}, "a docs-only change does not wake the deep models"
    assert outcome.verdict.mode == "light", "the receipt must state the depth actually used"


def test_the_split_uses_the_budget_the_judge_will_actually_enforce(tmp_path: Path) -> None:
    """Two budgets in two units, and the split read the wrong one. `split_diff` measured
    CHARACTERS against `max_diff_chars`; what actually bounds a judge is `prompt_limits`, in
    BYTES, over the WHOLE prompt — rubric, criteria, body and notes included. A change under
    `max_diff_chars` was therefore never split, and `judge()` cut it instead.

    Measured on red-alerts#2 (2026-09-22): a 108 173-character incremental delta, one single
    piece, truncated by the judge — the very failure the split was written to remove, one
    layer up. Every piece must now fit the prompt the judge will build from it."""
    repo, ledger = _repo(tmp_path)

    def one(name: str, lines: int) -> str:
        return f"diff --git a/{name} b/{name}\n" + "".join(f"+line {i}\n" for i in range(lines))

    whole = "".join(one(f"src/f{n}.go", 300) for n in range(6))
    assert len(whole) < default_policy().max_diff_chars, "under the old budget: never split"

    github = FakeGitHub(diff_text=whole, messages=["chore: plain"])
    policy = default_policy().model_copy(update={"prompt_limits": {"agy": 12_000}})
    seen: list[str] = []

    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        prompt, truncated = build_prompt(pr, diff, policy, criteria=criteria, notes=notes)
        assert not truncated, "a piece the judge still has to cut is not a bounded piece"
        assert len(prompt.encode("utf-8")) <= policy.prompt_limits[provider], (
            f"prompt of {len(prompt.encode('utf-8'))} bytes over the {provider} limit"
        )
        seen.append(diff)
        return approve(provider, tier)

    outcome = review_pull(
        replace(PR, additions=1800),
        github=github,
        policy=policy,
        ledger=ledger,
        project="red-alpha",
        repo_path=repo,
        run_judge=run_judge,
        root=tmp_path,
    )

    assert len(seen) > 1, "the byte budget must force a split the character budget never saw"
    assert "".join(seen) == whole, "every byte of the change still reached a judge"
    assert outcome.verdict.diff_truncated is False


# -- the contract a pull request is judged against (ticket 155d3d67) ---------------------


def _criteria_seen(tmp_path: Path, *, bind: PullRequest | None) -> list:
    repo, ledger = _repo(tmp_path)
    if bind is not None:
        _bind(ledger, bind)
    seen: list = []

    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        seen.append(criteria)
        return approve(provider, tier)

    review_pull(
        PR,
        github=FakeGitHub(),
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        repo_path=repo,
        run_judge=run_judge,
        root=tmp_path,
    )
    return seen


def test_an_unbound_pull_request_is_not_judged_against_the_contract(tmp_path: Path) -> None:
    """Measured on #46 and #47: the ticket in rail.yaml was an accepted phase whose criteria
    described other work, and both pull requests were blocked for "not meeting" them. A pull
    request nobody bound to the contract is judged on its code."""
    assert _criteria_seen(tmp_path, bind=None) == [None]


def test_a_binding_of_another_pull_request_does_not_count(tmp_path: Path) -> None:
    assert _criteria_seen(tmp_path, bind=replace(PR, number=8)) == [None]


def test_a_bound_pull_request_is_judged_against_its_contract(tmp_path: Path) -> None:
    assert _criteria_seen(tmp_path, bind=PR) == [["tests pass"]]


# -- a slice judge reads one part of the change (ticket 155d3d67) ------------------------


def _patch(name: str) -> str:
    return f"diff --git a/{name} b/{name}\n" + "".join(f"+l{i}\n" for i in range(400))


def _sliced_review(tmp_path: Path, run_judge):
    repo, ledger = _repo(tmp_path)
    whole = _patch("a.go") + _patch("b.go") + _patch("c.go")
    policy = default_policy().model_copy(update={"max_diff_chars": len(_patch("a.go")) + 20})
    return review_pull(
        replace(PR, additions=1200),
        github=FakeGitHub(diff_text=whole, messages=["chore: plain"]),
        policy=policy,
        ledger=ledger,
        project="red-alpha",
        repo_path=repo,
        run_judge=run_judge,
        root=tmp_path,
    )


def _blocking_on(provider: str, tier: str, file: str) -> JudgeReply:
    finding = Finding(
        severity="blocking", file=file, line=None, title="check is missing", evidence="absent"
    )
    verdict = ReviewVerdict(
        verdict="request_changes",
        summary="missing",
        findings=[finding],
        mode=tier,
        providers=(provider,),
    )
    return JudgeReply(
        provider=provider, tier=tier, model="m", verdict=verdict, failure=None, raw=""
    )


def test_a_slice_judge_cannot_block_on_a_file_outside_its_slice(tmp_path: Path) -> None:
    """Measured on #46: each judge read one of three parts and declared "missing" (blocking)
    code that lives in another part. A judge that did not read a file cannot block on it: the
    finding stays, as important, and a reply left with no blocking finding approves."""

    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        if _patch("a.go") in diff:
            return _blocking_on(provider, tier, "c.go")
        return approve(provider, tier)

    outcome = _sliced_review(tmp_path, run_judge)
    assert outcome.verdict.verdict == "approve"
    [finding] = outcome.verdict.findings
    assert finding.file == "c.go" and finding.severity == "important"
    assert "not in the part this judge read" in finding.evidence


def test_a_slice_judge_still_blocks_on_a_file_it_read(tmp_path: Path) -> None:
    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        if _patch("a.go") in diff:
            return _blocking_on(provider, tier, "a.go")
        return approve(provider, tier)

    outcome = _sliced_review(tmp_path, run_judge)
    assert outcome.verdict.verdict == "request_changes"
    assert [f.severity for f in outcome.verdict.findings] == ["blocking"]


def test_each_slice_judge_is_told_which_files_the_other_parts_hold(tmp_path: Path) -> None:
    notes_of_first: list[str] = []

    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        if _patch("a.go") in diff:
            notes_of_first.append(notes)
        return approve(provider, tier)

    _sliced_review(tmp_path, run_judge)
    [notes] = notes_of_first
    assert "Part 1 of 3" in notes
    assert "b.go" in notes and "c.go" in notes
    assert "never conclude that something is absent" in notes


def test_a_path_spelled_with_a_prefix_is_still_the_file_the_judge_read(tmp_path: Path) -> None:
    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        if _patch("a.go") in diff:
            return _blocking_on(provider, tier, "./a.go")
        return approve(provider, tier)

    assert _sliced_review(tmp_path, run_judge).verdict.verdict == "request_changes"


def test_the_list_of_other_files_is_bounded() -> None:
    """The split budget keeps PROMPT_MARGIN free for the part marker, less the 200 bytes judge()
    keeps when it shrinks: a hundred file names would push the judge's own part past the
    provider's limit, so the list stops and says how many it left out."""
    from rail.reviewer.judges import PROMPT_MARGIN
    from rail.reviewer.service import _part_notes

    many = "".join(_patch(f"pkg/module_{i:03d}.go") for i in range(100))
    notes = _part_notes("", 1, [_patch("a.go"), many])
    assert len(notes.encode("utf-8")) <= PROMPT_MARGIN - 200
    assert "more" in notes and "module_000.go" in notes


def test_rulings_of_reads_this_pull_request_only(tmp_path: Path) -> None:
    from rail.reviewer.service import rulings_of

    repo, ledger = _repo(tmp_path)
    for pr, finding in ((7, "F-7-1"), (8, "F-8-1")):
        ledger.attest(
            "red-alpha",
            AttestationKind.REVIEW_RULING,
            {"repository": PR.repository, "pr": pr, "finding": finding, "ruling": "fix",
             "decision": "d"},
            issuer="operator",
            idempotency_key=f"review_ruling:{PR.repository}#{pr}:{finding}:1",
        )
    assert [r.data["finding"] for r in rulings_of(ledger, "red-alpha", PR)] == ["F-7-1"]


def _verdict_with(ledger, *, sha, check_run_id, round_, findings, decision="request_changes",
                  artifact="code", pr=PR):
    verdict = ReviewVerdict(
        verdict=decision, summary="earlier", findings=findings, mode="deep",
        providers=("codex",), round=round_, artifact=artifact,
    )
    ledger.attest(
        "red-alpha", AttestationKind.REVIEW_VERDICT,
        verdict.as_attestation_data(sha=sha, check_run_id=check_run_id,
                                    repository=pr.repository, pr=pr.number),
        issuer="red-rail-reviewer", idempotency_key=f"review_verdict:{sha}:{check_run_id}",
    )


def _open(n=1, status="new", klass="blocker"):
    return Finding.model_validate({"severity": "blocking", "file": "src/x.py", "line": 1,
                                   "title": f"bug{n}", "evidence": "e", "id": f"F-7-{n}",
                                   "class": klass, "status": status})


class NoCheckText(FakeGitHub):
    def check_run_text(self, repository, check_id):  # C5: the context comes from receipts
        raise AssertionError("the context must not be read from a check run")


def _judge_saying(reply_findings=(), previous=(), decision="request_changes", seen=None):
    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None, notes="",
                  instructions=""):
        if seen is not None:
            seen.append({"diff": diff, "notes": notes, "instructions": instructions})
        verdict = ReviewVerdict(verdict=decision, summary="s", findings=list(reply_findings),
                                mode=tier, providers=(provider,), previous=tuple(previous))
        return JudgeReply(provider=provider, tier=tier, model="m", verdict=verdict,
                          failure=None, raw="")
    return run_judge


def test_round_two_carries_every_earlier_finding_from_the_receipts(tmp_path) -> None:
    repo, ledger = _repo(tmp_path)
    _verdict_with(ledger, sha="0" * 40, check_run_id=11, round_=1, findings=[_open(1)])
    seen: list[dict] = []
    outcome = review_pull(
        PR, github=NoCheckText(), policy=default_policy(), ledger=ledger, project="red-alpha",
        run_judge=_judge_saying(previous=[PreviousAnswer(id="F-7-1", status="fixed")],
                                decision="approve", seen=seen),
    )
    assert "F-7-1" in seen[0]["notes"] and "verify" in seen[0]["notes"].lower()
    assert "exhaustive" in seen[0]["instructions"]
    assert outcome.verdict.round == 2 and outcome.verdict.verdict == "approve"
    assert [(f.id, f.status) for f in outcome.verdict.findings] == [("F-7-1", "fixed")]


def test_an_unanswered_blocker_blocks_even_on_an_approving_reply(tmp_path) -> None:  # C3
    repo, ledger = _repo(tmp_path)
    _verdict_with(ledger, sha="0" * 40, check_run_id=11, round_=1, findings=[_open(1)])
    outcome = review_pull(
        PR, github=FakeGitHub(), policy=default_policy(), ledger=ledger, project="red-alpha",
        run_judge=_judge_saying(decision="approve"),
    )
    assert outcome.verdict.verdict == "request_changes"
    assert outcome.verdict.findings[0].status == "still_open"


def test_after_round_three_the_loop_awaits_a_ruling_without_a_judge(tmp_path) -> None:
    repo, ledger = _repo(tmp_path)
    for n, sha in ((1, "0"), (2, "1"), (3, "2")):
        _verdict_with(ledger, sha=sha * 40, check_run_id=10 + n, round_=n,
                      findings=[_open(1, status="new" if n == 1 else "still_open")])
    calls: list[str] = []

    def run_judge(*args, **kwargs):
        calls.append("judged")
        raise AssertionError("no judge while awaiting a ruling")

    outcome = review_pull(PR, github=FakeGitHub(), policy=default_policy(), ledger=ledger,
                          project="red-alpha", run_judge=run_judge)
    assert calls == [] and outcome.attested
    assert outcome.verdict.round == "awaiting_ruling"
    assert outcome.verdict.verdict == "request_changes"
    assert (
        "rail reviewer rule --repository hawkixs/red-alpha --pr 7 --finding F-7-1"
        in outcome.verdict.summary
    )


def test_the_closure_check_verifies_the_fix_ruling_only(tmp_path) -> None:
    repo, ledger = _repo(tmp_path)
    for n, sha in ((1, "0"), (2, "1"), (3, "2")):
        _verdict_with(ledger, sha=sha * 40, check_run_id=10 + n, round_=n,
                      findings=[_open(1, status="new" if n == 1 else "still_open")])
    ledger.attest(
        "red-alpha", AttestationKind.REVIEW_RULING,
        {"repository": PR.repository, "pr": 7, "finding": "F-7-1", "ruling": "fix",
         "decision": "rename the flag"},
        issuer="operator", idempotency_key="review_ruling:hawkixs/red-alpha#7:F-7-1:1",
    )
    seen: list[dict] = []
    new_blocker = Finding(severity="blocking", file="src/y.py", title="new", evidence="e")
    outcome = review_pull(
        PR, github=FakeGitHub(), policy=default_policy(), ledger=ledger, project="red-alpha",
        run_judge=_judge_saying(reply_findings=[new_blocker],
                                previous=[PreviousAnswer(id="F-7-1", status="fixed")], seen=seen),
    )
    assert "rename the flag" in seen[0]["notes"] and "rulings" in seen[0]["instructions"]
    assert outcome.verdict.round == "closure" and outcome.verdict.verdict == "approve"
    by_title = {f.title: f for f in outcome.verdict.findings}
    assert by_title["new"].klass == "note"  # no new blocker in a closure check


def test_a_carry_forward_ruling_closes_without_a_judge(tmp_path) -> None:
    repo, ledger = _repo(tmp_path)
    for n, sha in ((1, "0"), (2, "1"), (3, "2")):
        _verdict_with(ledger, sha=sha * 40, check_run_id=10 + n, round_=n,
                      findings=[_open(1, status="new" if n == 1 else "still_open")])
    ledger.attest(
        "red-alpha", AttestationKind.REVIEW_RULING,
        {"repository": PR.repository, "pr": 7, "finding": "F-7-1", "ruling": "carry_forward",
         "decision": "later"},
        issuer="operator", idempotency_key="review_ruling:hawkixs/red-alpha#7:F-7-1:1",
    )

    def run_judge(*args, **kwargs):
        raise AssertionError("a carry_forward ruling needs no judge")

    outcome = review_pull(PR, github=FakeGitHub(), policy=default_policy(), ledger=ledger,
                          project="red-alpha", run_judge=run_judge)
    assert outcome.verdict.verdict == "approve" and outcome.verdict.round == "closure"
    assert [(f.id, f.klass, f.status) for f in outcome.verdict.findings] == [
        ("F-7-1", "carry_forward", "ruled")
    ]


def test_a_code_pull_request_must_account_for_open_carry_forwards(tmp_path) -> None:  # C7
    repo, ledger = _repo(tmp_path)
    spec_pr = replace(PR, number=5)
    _verdict_with(ledger, sha="9" * 40, check_run_id=5, round_=1, decision="approve",
                  artifact="spec_plan", pr=spec_pr,
                  findings=[Finding.model_validate({
                      "severity": "important", "file": "docs/specs/s.md", "title": "edge",
                      "evidence": "e", "id": "F-5-1", "class": "carry_forward",
                  })])
    outcome = review_pull(PR, github=FakeGitHub(), policy=default_policy(), ledger=ledger,
                          project="red-alpha", run_judge=_judge_saying(decision="approve"))
    assert outcome.verdict.verdict == "request_changes"
    assert any(f.title == "CF-5-1 not accounted for" for f in outcome.verdict.findings)

    body = "## Carry-forwards\n- CF-5-1: addressed\n"
    seen: list[dict] = []
    outcome = review_pull(
        replace(PR, body=body, head_sha="c" * 40), github=FakeGitHub(), policy=default_policy(),
        ledger=ledger, project="red-alpha",
        run_judge=_judge_saying(previous=[PreviousAnswer(id="CF-5-1", status="fixed")],
                                decision="approve", seen=seen),
    )
    assert "CF-5-1" in seen[0]["notes"]
    assert outcome.verdict.verdict == "approve"
    assert outcome.verdict.carry_forwards.addressed == ("CF-5-1",)


def test_a_reviewer_yaml_that_sets_max_passes_is_refused_by_name() -> None:
    from rail.reviewer.policy import ReviewPolicy

    with pytest.raises(ValueError, match="review-loop-closure"):
        ReviewPolicy.model_validate({"max_passes_per_pr": 4})


def test_disagreeing_judges_keep_a_finding_open(tmp_path) -> None:  # C1
    repo, ledger = _repo(tmp_path)
    _verdict_with(ledger, sha="0" * 40, check_run_id=11, round_=1, findings=[_open(1)])
    big_pr = replace(PR, additions=300, deletions=0)
    policy = default_policy().model_copy(update={"incremental": False})

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None, notes="",
                  instructions=""):
        status = "fixed" if provider == "agy" else "still_open"
        verdict = ReviewVerdict(verdict="approve", summary="s", findings=[], mode=tier,
                                providers=(provider,),
                                previous=(PreviousAnswer(id="F-7-1", status=status),))
        return JudgeReply(provider=provider, tier=tier, model="m", verdict=verdict,
                          failure=None, raw="")

    outcome = review_pull(big_pr, github=FakeGitHub(), policy=policy, ledger=ledger,
                          project="red-alpha", run_judge=run_judge)
    assert outcome.verdict.verdict == "request_changes"
    assert [(f.id, f.status) for f in outcome.verdict.findings] == [("F-7-1", "still_open")]


def test_new_ids_stay_unique_across_a_mechanical_blocker(tmp_path) -> None:  # C2, I3
    repo, ledger = _repo(tmp_path)
    spec_pr = replace(PR, number=5)
    _verdict_with(ledger, sha="9" * 40, check_run_id=5, round_=1, decision="approve",
                  artifact="spec_plan", pr=spec_pr,
                  findings=[Finding.model_validate({
                      "severity": "important", "file": "docs/specs/s.md", "title": "edge",
                      "evidence": "e", "id": "F-5-1", "class": "carry_forward",
                  })])
    outcome = review_pull(
        PR, github=FakeGitHub(), policy=default_policy(), ledger=ledger, project="red-alpha",
        run_judge=_judge_saying(
            reply_findings=[Finding(severity="blocking", file="src/z.py", title="one",
                                    evidence="e")],
            decision="request_changes",
        ),
    )
    assert sorted(f.id for f in outcome.verdict.findings) == ["F-7-1", "F-7-2"]

    seen: list[dict] = []
    outcome2 = review_pull(
        replace(PR, head_sha="c" * 40), github=FakeGitHub(), policy=default_policy(),
        ledger=ledger, project="red-alpha",
        run_judge=_judge_saying(
            reply_findings=[Finding(severity="blocking", file="src/w.py", title="two",
                                    evidence="e")],
            previous=[PreviousAnswer(id="F-7-1", status="still_open")],
            decision="request_changes", seen=seen,
        ),
    )
    ids = [f.id for f in outcome2.verdict.findings]
    assert len(ids) == len(set(ids))
    assert "F-7-3" in ids
    assert "not accounted for" not in seen[0]["notes"]  # I3: mechanical findings are not context


def test_a_carry_forward_ruling_closure_keeps_the_carry_forward_accounting(tmp_path) -> None:  # I4
    repo, ledger = _repo(tmp_path)
    spec_pr = replace(PR, number=5)
    _verdict_with(ledger, sha="9" * 40, check_run_id=5, round_=1, decision="approve",
                  artifact="spec_plan", pr=spec_pr,
                  findings=[Finding.model_validate({
                      "severity": "important", "file": "docs/specs/s.md", "title": "edge",
                      "evidence": "e", "id": "F-5-1", "class": "carry_forward",
                  })])
    body = "## Carry-forwards\n- CF-5-1: addressed\n"
    code_pr = replace(PR, body=body)

    def round_one(pr, diff, policy, *, provider, tier, criteria, root=None, notes="",
                  instructions=""):
        verdict = ReviewVerdict(
            verdict="request_changes", summary="s",
            findings=[Finding(severity="blocking", file="src/z.py", title="f1", evidence="e")],
            mode=tier, providers=(provider,),
            previous=(PreviousAnswer(id="CF-5-1", status="fixed"),),
        )
        return JudgeReply(provider=provider, tier=tier, model="m", verdict=verdict,
                          failure=None, raw="")

    def still_open_round(pr, diff, policy, *, provider, tier, criteria, root=None, notes="",
                         instructions=""):
        verdict = ReviewVerdict(
            verdict="request_changes", summary="s", findings=[], mode=tier,
            providers=(provider,),
            previous=(PreviousAnswer(id="F-7-1", status="still_open"),
                     PreviousAnswer(id="CF-5-1", status="fixed")),
        )
        return JudgeReply(provider=provider, tier=tier, model="m", verdict=verdict,
                          failure=None, raw="")

    review_pull(code_pr, github=FakeGitHub(), policy=default_policy(), ledger=ledger,
               project="red-alpha", run_judge=round_one)
    review_pull(replace(code_pr, head_sha="c" * 40), github=FakeGitHub(),
               policy=default_policy(), ledger=ledger, project="red-alpha",
               run_judge=still_open_round)
    review_pull(replace(code_pr, head_sha="d" * 40), github=FakeGitHub(),
               policy=default_policy(), ledger=ledger, project="red-alpha",
               run_judge=still_open_round)
    ledger.attest(
        "red-alpha", AttestationKind.REVIEW_RULING,
        {"repository": PR.repository, "pr": 7, "finding": "F-7-1", "ruling": "carry_forward",
         "decision": "later"},
        issuer="operator", idempotency_key="review_ruling:hawkixs/red-alpha#7:F-7-1:1",
    )

    def no_judge(*args, **kwargs):
        raise AssertionError("a carry_forward closure needs no judge")

    outcome = review_pull(replace(code_pr, head_sha="e" * 40), github=FakeGitHub(),
                          policy=default_policy(), ledger=ledger, project="red-alpha",
                          run_judge=no_judge)
    assert outcome.verdict.verdict == "approve" and outcome.verdict.round == "closure"
    assert outcome.verdict.carry_forwards.addressed == ("CF-5-1",)


def test_a_mechanical_only_awaiting_state_resolves_from_the_body(tmp_path) -> None:  # M8, NB2
    # a body-derived accounting gap alone (no ruling on it) never earns a no-judge closure
    # (Ruling 21): the next pass is another judged round 3, the fake judge included.
    repo, ledger = _repo(tmp_path)
    spec_pr = replace(PR, number=5)
    _verdict_with(ledger, sha="9" * 40, check_run_id=5, round_=1, decision="approve",
                  artifact="spec_plan", pr=spec_pr,
                  findings=[Finding.model_validate({
                      "severity": "important", "file": "docs/specs/s.md", "title": "edge",
                      "evidence": "e", "id": "F-5-1", "class": "carry_forward",
                  })])
    review_pull(PR, github=FakeGitHub(), policy=default_policy(), ledger=ledger,
               project="red-alpha", run_judge=_judge_saying(decision="approve"))
    review_pull(replace(PR, head_sha="c" * 40), github=FakeGitHub(), policy=default_policy(),
               ledger=ledger, project="red-alpha",
               run_judge=_judge_saying(decision="approve"))
    review_pull(replace(PR, head_sha="d" * 40), github=FakeGitHub(), policy=default_policy(),
               ledger=ledger, project="red-alpha",
               run_judge=_judge_saying(decision="approve"))

    calls: list[str] = []
    judge = _judge_saying(previous=[PreviousAnswer(id="CF-5-1", status="fixed")],
                          decision="approve")

    def judging(*args, **kwargs):
        calls.append("judged")
        return judge(*args, **kwargs)

    body = "## Carry-forwards\n- CF-5-1: addressed\n"
    outcome = review_pull(replace(PR, head_sha="e" * 40, body=body), github=FakeGitHub(),
                          policy=default_policy(), ledger=ledger, project="red-alpha",
                          run_judge=judging)
    assert calls == ["judged"]
    assert outcome.verdict.round == 3 and outcome.verdict.verdict == "approve"
    assert [f.status for f in outcome.verdict.findings] == ["fixed"]


def test_the_delta_note_is_added_only_when_a_delta_is_judged(tmp_path) -> None:  # M6
    repo, ledger = _repo(tmp_path)
    ledger.attest(
        "red-alpha", AttestationKind.REVIEW_VERDICT,
        {"sha": "0" * 40, "verdict": "request_changes", "check_run_id": 11,
         "repository": PR.repository, "pr": 7, "mode": "deep", "providers": ["codex"],
         "finding_count": 1, "blocking": True, "diff_truncated": False, "round": 1,
         "artifact": "code", "findings": 1},
        issuer="red-rail-reviewer", idempotency_key="review_verdict:0x40:11",
    )
    seen: list[dict] = []
    review_pull(
        replace(PR, head_sha="0" * 40), github=FakeGitHub(), policy=default_policy(),
        ledger=ledger, project="red-alpha", run_judge=_judge_saying(decision="approve", seen=seen),
    )
    assert _DELTA_NOTE not in seen[0]["notes"]
    assert "reviewed before" in seen[0]["notes"]


def test_no_check_run_fallback_when_the_findings_list_is_empty(tmp_path) -> None:  # M7
    repo, ledger = _repo(tmp_path)
    _verdict_with(ledger, sha="0" * 40, check_run_id=11, round_=1, findings=[], decision="approve")
    seen: list[dict] = []
    outcome = review_pull(
        replace(PR, head_sha="0" * 40), github=NoCheckText(), policy=default_policy(),
        ledger=ledger, project="red-alpha", run_judge=_judge_saying(decision="approve", seen=seen),
    )
    assert outcome.verdict.round == 2
    assert seen[0]["notes"] == ""


def test_the_closure_context_carries_only_the_fix_ruled_findings(tmp_path) -> None:  # M11
    repo, ledger = _repo(tmp_path)
    for n, sha in ((1, "0"), (2, "1"), (3, "2")):
        _verdict_with(ledger, sha=sha * 40, check_run_id=10 + n, round_=n,
                      findings=[_open(1, status="new" if n == 1 else "still_open"),
                               _open(2, status="new" if n == 1 else "still_open")])
    ledger.attest(
        "red-alpha", AttestationKind.REVIEW_RULING,
        {"repository": PR.repository, "pr": 7, "finding": "F-7-1", "ruling": "fix",
         "decision": "rename the flag"},
        issuer="operator", idempotency_key="review_ruling:hawkixs/red-alpha#7:F-7-1:1",
    )
    ledger.attest(
        "red-alpha", AttestationKind.REVIEW_RULING,
        {"repository": PR.repository, "pr": 7, "finding": "F-7-2", "ruling": "carry_forward",
         "decision": "later"},
        issuer="operator", idempotency_key="review_ruling:hawkixs/red-alpha#7:F-7-2:1",
    )
    seen: list[dict] = []
    outcome = review_pull(
        PR, github=FakeGitHub(), policy=default_policy(), ledger=ledger, project="red-alpha",
        run_judge=_judge_saying(previous=[PreviousAnswer(id="F-7-1", status="fixed")],
                                decision="approve", seen=seen),
    )
    assert "rename the flag" in seen[0]["notes"]
    assert "F-7-2" not in seen[0]["notes"] and "later" not in seen[0]["notes"]
    assert outcome.verdict.round == "closure"


def test_a_no_verdict_pass_at_round_two_keeps_blockers_open(tmp_path) -> None:  # M10
    repo, ledger = _repo(tmp_path)
    _verdict_with(ledger, sha="0" * 40, check_run_id=11, round_=1, findings=[_open(1)])

    def failing_judge(*args, **kwargs):
        return JudgeReply(provider="agy", tier="light", model="m", verdict=None,
                          failure="boom", raw="")

    outcome = review_pull(PR, github=FakeGitHub(), policy=default_policy(), ledger=ledger,
                          project="red-alpha", run_judge=failing_judge)
    assert outcome.verdict.round == 2
    assert outcome.verdict.verdict == "request_changes"
    assert outcome.verdict.findings[0].status == "still_open"


def test_a_failed_closure_check_keeps_the_ruling(tmp_path) -> None:  # I5
    from rail.reviewer import rounds
    from rail.reviewer.service import previous_verdicts, rulings_of

    repo, ledger = _repo(tmp_path)
    for n, sha in ((1, "0"), (2, "1"), (3, "2")):
        _verdict_with(ledger, sha=sha * 40, check_run_id=10 + n, round_=n,
                      findings=[_open(1, status="new" if n == 1 else "still_open")])
    ledger.attest(
        "red-alpha", AttestationKind.REVIEW_RULING,
        {"repository": PR.repository, "pr": 7, "finding": "F-7-1", "ruling": "fix",
         "decision": "rename the flag"},
        issuer="operator", idempotency_key="review_ruling:hawkixs/red-alpha#7:F-7-1:1",
    )

    def failing_judge(*args, **kwargs):
        return JudgeReply(provider="agy", tier="light", model="m", verdict=None,
                          failure="boom", raw="")

    outcome = review_pull(PR, github=FakeGitHub(), policy=default_policy(), ledger=ledger,
                          project="red-alpha", run_judge=failing_judge)
    assert outcome.verdict.round == "awaiting_ruling"
    assert outcome.verdict.mode == "awaiting_ruling"

    state = rounds.loop_state(
        previous_verdicts(ledger, "red-alpha", PR), rulings_of(ledger, "red-alpha", PR)
    )
    assert "F-7-1" in state.rulings
    assert rounds.next_step(state) == ("closure", None)


def test_a_body_claim_without_a_judges_confirmation_stays_open(tmp_path) -> None:  # NB1
    repo, ledger = _repo(tmp_path)
    spec_pr = replace(PR, number=5)
    _verdict_with(ledger, sha="9" * 40, check_run_id=5, round_=1, decision="approve",
                  artifact="spec_plan", pr=spec_pr,
                  findings=[Finding.model_validate({
                      "severity": "important", "file": "docs/specs/s.md", "title": "edge",
                      "evidence": "e", "id": "F-5-1", "class": "carry_forward",
                  })])
    body = "## Carry-forwards\n- CF-5-1: addressed\n"
    code_pr = replace(PR, body=body)
    review_pull(code_pr, github=FakeGitHub(), policy=default_policy(), ledger=ledger,
               project="red-alpha", run_judge=_judge_saying(decision="approve"))
    review_pull(replace(code_pr, head_sha="c" * 40), github=FakeGitHub(),
               policy=default_policy(), ledger=ledger, project="red-alpha",
               run_judge=_judge_saying(decision="approve"))
    outcome = review_pull(replace(code_pr, head_sha="d" * 40), github=FakeGitHub(),
                          policy=default_policy(), ledger=ledger, project="red-alpha",
                          run_judge=_judge_saying(decision="approve"))
    assert outcome.verdict.round == 3

    # round 4: an unruled "not addressed" blocker is real, so this is "awaiting_ruling" — the
    # body's claim alone never resolves it.
    outcome4 = review_pull(replace(code_pr, head_sha="e" * 40), github=FakeGitHub(),
                           policy=default_policy(), ledger=ledger, project="red-alpha",
                           run_judge=_judge_saying(decision="approve"))
    assert outcome4.verdict.verdict != "approve"
    assert any(f.title == "CF-5-1 not addressed" for f in outcome4.verdict.findings)


def test_the_closure_context_asks_the_judge_to_confirm_a_not_addressed_blocker(
    tmp_path,
) -> None:  # NB1c
    repo, ledger = _repo(tmp_path)
    spec_pr = replace(PR, number=5)
    _verdict_with(ledger, sha="9" * 40, check_run_id=5, round_=1, decision="approve",
                  artifact="spec_plan", pr=spec_pr,
                  findings=[Finding.model_validate({
                      "severity": "important", "file": "docs/specs/s.md", "title": "edge",
                      "evidence": "e", "id": "F-5-1", "class": "carry_forward",
                  })])
    body = "## Carry-forwards\n- CF-5-1: addressed\n"
    code_pr = replace(PR, body=body)
    for head in ("b", "c", "d"):
        review_pull(replace(code_pr, head_sha=head * 40), github=FakeGitHub(),
                   policy=default_policy(), ledger=ledger, project="red-alpha",
                   run_judge=_judge_saying(decision="approve"))
    outcome = review_pull(replace(code_pr, head_sha="e" * 40), github=FakeGitHub(),
                          policy=default_policy(), ledger=ledger, project="red-alpha",
                          run_judge=_judge_saying(decision="approve"))
    not_addressed = next(f for f in outcome.verdict.findings if f.title == "CF-5-1 not addressed")
    ledger.attest(
        "red-alpha", AttestationKind.REVIEW_RULING,
        {"repository": PR.repository, "pr": 7, "finding": not_addressed.id, "ruling": "fix",
         "decision": "go confirm it"},
        issuer="operator",
        idempotency_key=f"review_ruling:hawkixs/red-alpha#7:{not_addressed.id}:1",
    )
    seen: list[dict] = []
    outcome2 = review_pull(
        replace(code_pr, head_sha="f" * 40), github=FakeGitHub(), policy=default_policy(),
        ledger=ledger, project="red-alpha",
        run_judge=_judge_saying(previous=[PreviousAnswer(id="CF-5-1", status="fixed")],
                                decision="approve", seen=seen),
    )
    assert "CF-5-1" in seen[0]["notes"]
    assert outcome2.verdict.verdict == "approve"
    assert outcome2.verdict.carry_forwards.addressed == ("CF-5-1",)
