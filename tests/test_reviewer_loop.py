"""One spec pull request through the whole loop, then a code pull request that accounts for its
carry-forward (spec 2026-09-25-review-loop-closure, C9). Fake GitHub, file ledger, scripted
judges; no label, no bypass."""

from dataclasses import replace
from pathlib import Path

from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from rail.reviewer.judges import JudgeReply
from rail.reviewer.policy import default_policy
from rail.reviewer.service import review_pull
from rail.reviewer.verdict import Finding, PreviousAnswer, ReviewVerdict
from tests.test_reviewer_service import PR, FakeGitHub, _repo

SPEC_DIFF = "diff --git a/docs/specs/2026-09-25-x.md b/docs/specs/2026-09-25-x.md\n+text\n"


def _reply(findings=(), previous=(), decision="request_changes"):
    def run_judge(
        pr, diff, policy, *, provider, tier, criteria, root=None, notes="", instructions=""
    ):
        return JudgeReply(
            provider=provider,
            tier=tier,
            model="m",
            failure=None,
            raw="",
            verdict=ReviewVerdict(
                verdict=decision,
                summary="s",
                findings=list(findings),
                mode=tier,
                providers=(provider,),
                previous=tuple(previous),
            ),
        )

    return run_judge


def _f(title, severity="blocking", klass="blocker", id_=None):
    data = {
        "severity": severity,
        "file": "docs/specs/2026-09-25-x.md",
        "line": 1,
        "title": title,
        "evidence": "e",
        "class": klass,
    }
    if id_:
        data["id"] = id_
    return Finding.model_validate(data)


def test_a_spec_loop_closes_and_its_carry_forward_reaches_the_code(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    spec_pr = replace(PR, number=5)
    github = FakeGitHub(diff_text=SPEC_DIFF, compare_text=SPEC_DIFF)
    policy = default_policy()

    def review(head: str, judge):
        return review_pull(
            replace(spec_pr, head_sha=head * 40),
            github=github,
            policy=policy,
            ledger=ledger,
            project="red-alpha",
            run_judge=judge,
        )

    one = review("1", _reply([_f("wrong decision")]))
    assert one.verdict.round == 1 and one.verdict.verdict == "request_changes"
    two = review(
        "2",
        _reply(
            [
                _f("wrong decision", id_="F-5-1"),
                _f("edge case", severity="important", klass="carry_forward"),
            ]
        ),
    )
    assert two.verdict.round == 2
    three = review("3", _reply([_f("wrong decision", id_="F-5-1")]))
    assert three.verdict.round == 3 and three.verdict.verdict == "request_changes"

    def no_judge(*args, **kwargs):
        raise AssertionError("awaiting a ruling: no judge")

    waiting = review("4", no_judge)
    assert waiting.verdict.round == "awaiting_ruling"

    ledger.attest(
        "red-alpha",
        AttestationKind.REVIEW_RULING,
        {
            "repository": PR.repository,
            "pr": 5,
            "finding": "F-5-1",
            "ruling": "fix",
            "decision": "use the ledger",
        },
        issuer="operator",
        idempotency_key="review_ruling:hawkixs/red-alpha#5:F-5-1:1",
    )
    closed = review("5", _reply(previous=[PreviousAnswer(id="F-5-1", status="fixed")]))
    assert closed.verdict.round == "closure" and closed.verdict.verdict == "approve"

    code = review_pull(
        replace(PR, number=8, head_sha="8" * 40, body="## Carry-forwards\n- CF-5-2: addressed\n"),
        github=FakeGitHub(),
        policy=policy,
        ledger=ledger,
        project="red-alpha",
        run_judge=_reply(
            previous=[PreviousAnswer(id="CF-5-2", status="fixed")], decision="approve"
        ),
    )
    assert code.verdict.verdict == "approve"
    assert code.verdict.carry_forwards.addressed == ("CF-5-2",)
    verdicts = FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.REVIEW_VERDICT
    )
    assert [v.data["round"] for v in verdicts] == [1, 2, 3, "awaiting_ruling", "closure", 1]
