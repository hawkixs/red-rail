"""One review: check run started → judges → verdict → check completed + PR review →
`review_verdict` attested through the project's own ledger. Fail-closed on every gap."""

from dataclasses import dataclass, field, replace
from pathlib import Path

from rail.ledger import RECEIPTS_DIR, AttestationKind, Contract, Deliverable
from rail.ledger.file import FileLedger
from rail.reviewer.github import CheckRun, PullRequest
from rail.reviewer.judges import JudgeReply
from rail.reviewer.policy import default_policy
from rail.reviewer.service import docs_only, needs_review, review_pull
from rail.reviewer.verdict import Finding, ReviewVerdict
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


def test_light_review_approves_publishes_and_attests(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    github = FakeGitHub()
    seen = []

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None):
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

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None):
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

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None):
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
        run_judge=lambda pr, diff, policy, *, provider, tier, criteria, root=None: fail(
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
        run_judge=lambda pr, diff, policy, *, provider, tier, criteria, root=None: approve(
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
        run_judge=lambda pr, diff, policy, *, provider, tier, criteria, root=None: approve(
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

    def exploding_judge(pr, diff, policy, *, provider, tier, criteria, root=None):
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
    data = ReviewVerdict(
        verdict=verdict, summary="earlier", findings=[], mode="deep", providers=("codex",)
    ).as_attestation_data(
        sha=sha, check_run_id=check_run_id, repository=PR.repository, pr=PR.number
    )
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

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None, notes=""):
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

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None, notes=""):
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


def test_the_pass_budget_fails_the_check_without_a_judge_until_relabelled(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    policy = default_policy().model_copy(update={"max_passes_per_pr": 2})
    _earlier_verdict(ledger, sha="0" * 40, check_run_id=11)
    _earlier_verdict(ledger, sha="1" * 40, check_run_id=12)
    github = FakeGitHub()
    calls: list[str] = []

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None, notes=""):
        calls.append(provider)
        return approve(provider, tier)

    outcome = review_pull(
        PR, github=github, policy=policy, ledger=ledger, project="red-alpha", run_judge=run_judge
    )
    assert calls == [] and outcome.attested
    assert outcome.verdict.mode == "budget" and outcome.verdict.verdict == "request_changes"
    assert "budget" in outcome.verdict.summary and "rail-review:rerun" in outcome.verdict.summary
    assert ("complete", 99, "failure", "review budget exhausted") in github.calls
    assert ("review", 7, "REQUEST_CHANGES") in github.calls
    verdicts = ledger.list("red-alpha", attestation=AttestationKind.REVIEW_VERDICT)
    assert [v.data["mode"] for v in verdicts][-1] == "budget"
    relabelled = replace(PR, labels=("rail-review:rerun",), head_sha="c" * 40)
    outcome = review_pull(
        relabelled,
        github=FakeGitHub(),
        policy=policy,
        ledger=ledger,
        project="red-alpha",
        run_judge=run_judge,
    )
    assert calls and outcome.verdict.mode == "light"


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

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None, notes=""):
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

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None, notes=""):
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

    def run_judge(pr, diff, policy, *, provider, tier, criteria, root=None, notes=""):
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
