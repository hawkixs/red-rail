"""An unchanged patch can inherit evidence, never a failed or unverified CI result."""

import subprocess
from dataclasses import replace
from functools import partial
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from rail.ledger import AttestationKind
from rail.reviewer import carry_verdict, rounds
from rail.reviewer.carry_verdict import carry_decision
from rail.reviewer.github import CheckRun, CommitStatus, ComparedFile, Comparison, GitHubError
from rail.reviewer.policy import default_policy
from rail.reviewer.service import REVIEWER_IDENTITY, carry_review, review_pull
from tests.test_reviewer_service import PR as BASE_PR
from tests.test_reviewer_service import FakeGitHub, _repo, approve

PR = replace(BASE_PR, repository="example/red-alpha", author="example")
OLD = "c" * 40
OLDER = "d" * 40
OLD_BASE = "e" * 40
NEW_BASE = "f" * 40
PATCH = (
    "diff --git a/src/x.py b/src/x.py\n"
    "index 7898192..6178079 100644\n"
    "--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-old\n+new\n"
)


class CarryGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.heads = [OLDER, OLD, PR.head_sha]
        self.checks = {
            OLD: [CheckRun(12, "completed", "success", "red-rail/review")],
            PR.head_sha: [CheckRun(13, "completed", "success", "test-unit")],
        }
        self.statuses = {OLD: [CommitStatus(14, "independent-review", "success", "approved")]}
        self.patches = {(OLD_BASE, OLD): PATCH, (NEW_BASE, PR.head_sha): PATCH}
        self.base_files = ("docs/readme.md",)
        detail = ComparedFile("src/x.py", None, "modified", "1" * 40)
        self.details = {OLD: (detail,), PR.head_sha: (detail,)}

    def pull(self, repository, number):
        return PR

    def commit_shas(self, repository, number):
        return self.heads

    def check_runs(self, repository, sha, *, name=None):
        return [c for c in self.checks.get(sha, []) if name is None or c.name == name]

    def commit_statuses(self, repository, sha):
        return self.statuses.get(sha, [])

    def compare_files(self, repository, base, head):
        if (base, head) == (OLD_BASE, NEW_BASE):
            return Comparison(OLD_BASE, self.base_files, ())
        return Comparison(OLD_BASE if head == OLD else NEW_BASE, ("src/x.py",), self.details[head])

    def compare_diff(self, repository, base, head):
        return self.patches[(base, head)]

    def complete_check(self, repository, check_id, **kwargs):
        check = super().complete_check(repository, check_id, **kwargs)
        self.checks[PR.head_sha].append(replace(check, name="red-rail/review"))
        return check


def test_identical_patch_and_unrelated_base_change_carry_both_verdicts():
    decision = carry_decision(CarryGitHub(), PR.repository, PR.number, PR.head_sha)
    assert decision.allowed
    assert (decision.judged_head, decision.judged_base, decision.new_base) == (
        OLD,
        OLD_BASE,
        NEW_BASE,
    )
    assert len(decision.patch_id) == 40
    assert decision.rail_check.id == 12
    assert decision.independent_status.id == 14


@pytest.mark.parametrize("missing", ["check", "status"])
def test_both_successful_verdicts_are_required(missing):
    github = CarryGitHub()
    if missing == "check":
        github.checks[OLD] = [CheckRun(12, "completed", "failure", "red-rail/review")]
    else:
        github.statuses[OLD] = []
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    assert not decision.allowed
    assert decision.reason == "no judged head with both verdicts"
    assert not github.calls


def test_most_recent_eligible_head_is_used():
    github = CarryGitHub()
    github.checks[OLDER] = github.checks[OLD]
    github.statuses[OLDER] = github.statuses[OLD]
    assert carry_decision(github, PR.repository, PR.number, PR.head_sha).judged_head == OLD


def test_changed_patch_is_refused():
    github = CarryGitHub()
    github.patches[(NEW_BASE, PR.head_sha)] = PATCH.replace("+new", "+different")
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    assert not decision.allowed
    assert decision.reason == "patch-id differs"


@pytest.mark.parametrize("head", [OLD, PR.head_sha])
@pytest.mark.parametrize(
    "marker", ["Binary files a/image.png and b/image.png differ", "GIT binary patch"]
)
def test_binary_change_cannot_be_proved_by_equal_text_patches(head, marker):
    github = CarryGitHub()
    base = OLD_BASE if head == OLD else NEW_BASE
    github.patches[(base, head)] += f"diff --git a/image.png b/image.png\n{marker}\n"
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    assert not decision.allowed
    assert decision.reason == "binary change: patch-id cannot prove equality"
    assert not github.calls


@pytest.mark.parametrize(
    "change",
    [
        {"sha": "2" * 40},
        {"filename": "src/y.py"},
        {"previous_filename": "src/old.py"},
        {"status": "added"},
    ],
)
def test_equal_patch_ids_require_equal_pr_file_details(change):
    github = CarryGitHub()
    github.details[PR.head_sha] = (replace(github.details[OLD][0], **change),)
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    assert not decision.allowed
    assert decision.reason == "PR files differ: src/x.py"
    assert not github.calls


def test_base_touching_a_pr_file_is_refused_even_with_identical_patch():
    github = CarryGitHub()
    github.base_files = ("src/x.py",)
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    assert not decision.allowed
    assert decision.reason == "base changed src/x.py touched by the PR"


def test_newest_failed_verdict_does_not_revive_an_older_approval():
    github = CarryGitHub()
    github.checks[OLD].append(CheckRun(15, "completed", "failure", "red-rail/review"))
    assert not carry_decision(github, PR.repository, PR.number, PR.head_sha).allowed


@pytest.mark.parametrize("state", ["failure", "in_progress", "missing-status"])
def test_carry_never_crosses_a_more_recent_judged_head(state):
    github = CarryGitHub()
    github.heads.insert(-1, NEW_BASE)
    github.checks[NEW_BASE] = [
        CheckRun(
            25,
            "in_progress" if state == "in_progress" else "completed",
            None
            if state == "in_progress"
            else "success"
            if state == "missing-status"
            else "failure",
            "red-rail/review",
        )
    ]
    github.statuses[NEW_BASE] = [] if state == "missing-status" else github.statuses[OLD]
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    assert not decision.allowed
    assert decision.reason == "no judged head with both verdicts"
    assert not github.calls


def test_patch_id_ignores_hunk_offsets_and_whitespace():
    github = CarryGitHub()
    github.patches[(NEW_BASE, PR.head_sha)] = PATCH.replace("@@ -1 +1 @@", "@@ -8 +8 @@").replace(
        "+new", "+ new "
    )
    assert carry_decision(github, PR.repository, PR.number, PR.head_sha).allowed


def test_stale_target_head_is_refused():
    decision = carry_decision(CarryGitHub(), PR.repository, PR.number, OLD)
    assert not decision.allowed
    assert decision.reason == "pull request head changed"


@pytest.mark.parametrize("state", ["pending", "failure", "error"])
def test_ci_status_must_be_green(state):
    github = CarryGitHub()
    github.statuses[PR.head_sha] = [CommitStatus(20, "test-unit", state, "testing")]
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    assert not decision.allowed
    assert decision.reason == f"CI not green: test-unit {state}"


@pytest.mark.parametrize(
    ("status", "conclusion", "allowed"),
    [
        ("in_progress", None, False),
        ("completed", "failure", False),
        ("completed", "success", True),
        ("completed", "neutral", True),
        ("completed", "skipped", True),
        ("completed", "cancelled", False),
    ],
)
def test_all_ci_checks_are_examined(status, conclusion, allowed):
    github = CarryGitHub()
    github.checks[PR.head_sha] = [CheckRun(20, status, conclusion, "test-unit")]
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    assert decision.allowed is allowed


def test_automatic_decision_accepts_pending_ci_but_refuses_failures():
    github = CarryGitHub()
    github.checks[PR.head_sha] = [CheckRun(20, "in_progress", None, "test-unit")]
    assert carry_decision(
        github, PR.repository, PR.number, PR.head_sha, require_green=False
    ).allowed
    github.statuses[PR.head_sha] = [CommitStatus(21, "lint", "error", "error")]
    assert not carry_decision(
        github, PR.repository, PR.number, PR.head_sha, require_green=False
    ).allowed


def test_the_two_carried_contexts_are_excluded_from_ci():
    github = CarryGitHub()
    github.checks[PR.head_sha].append(CheckRun(22, "in_progress", None, "red-rail/review"))
    github.statuses[PR.head_sha] = [CommitStatus(23, "independent-review", "pending", "")]
    assert carry_decision(github, PR.repository, PR.number, PR.head_sha).allowed


@pytest.mark.parametrize("require_green", [True, False])
@pytest.mark.parametrize("evidence", ["check", "status"])
def test_judged_ci_evidence_must_be_present_on_the_new_head(require_green, evidence):
    github = CarryGitHub()
    if evidence == "check":
        github.checks[OLD].append(CheckRun(30, "completed", "success", "lint"))
    else:
        github.statuses[OLD].append(CommitStatus(30, "lint", "success", "passed"))
    decision = carry_decision(
        github, PR.repository, PR.number, PR.head_sha, require_green=require_green
    )
    assert decision.allowed is (not require_green)
    if require_green:
        assert decision.reason == "CI not green: lint missing"
    assert not github.calls


@pytest.mark.parametrize("require_green", [True, False])
def test_carried_verdicts_alone_are_not_green_ci_evidence(require_green):
    github = CarryGitHub()
    github.checks[PR.head_sha] = [CheckRun(30, "completed", "success", "red-rail/review")]
    github.statuses[PR.head_sha] = [CommitStatus(31, "independent-review", "success", "")]
    decision = carry_decision(
        github, PR.repository, PR.number, PR.head_sha, require_green=require_green
    )
    assert decision.allowed is (not require_green)
    if require_green:
        assert decision.reason == "CI not green: no check on the new head"
    assert not github.calls


def test_green_commit_status_alone_is_ci_evidence():
    github = CarryGitHub()
    github.checks[PR.head_sha] = []
    github.statuses[PR.head_sha] = [CommitStatus(31, "lint", "success", "")]
    assert carry_decision(github, PR.repository, PR.number, PR.head_sha).allowed


@pytest.mark.parametrize("failure", ["empty", "timeout", "api"])
def test_missing_patch_evidence_fails_closed(monkeypatch, failure):
    github = CarryGitHub()
    if failure == "empty":
        github.patches[(OLD_BASE, OLD)] = ""
    elif failure == "timeout":
        import subprocess

        def timeout(*args, **kwargs):
            assert kwargs["timeout"] <= 10
            raise subprocess.TimeoutExpired("patch-id", 10)

        monkeypatch.setattr(carry_verdict.subprocess, "run", timeout)
    else:

        def refuse(*args):
            raise GitHubError("incomplete comparison")

        monkeypatch.setattr(github, "compare_files", refuse)
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    assert not decision.allowed
    assert not github.calls


def source_record(ledger):
    verdict = approve("codex").verdict.model_copy(update={"round": 2})
    data = verdict.as_attestation_data(
        sha=OLD, check_run_id=12, repository=PR.repository, pr=PR.number
    )
    data["findings"] = [
        {
            "id": "F-7-1",
            "class": "carry_forward",
            "status": "ruled",
            "severity": "minor",
            "file": "src/x.py",
            "line": 1,
            "title": "later",
            "evidence": "e",
        }
    ]
    data["carry_forwards"] = {"addressed": ["CF-2-1"], "deferred": ["CF-3-1"]}
    return ledger.attest(
        "red-alpha",
        AttestationKind.REVIEW_VERDICT,
        data,
        issuer=REVIEWER_IDENTITY,
        idempotency_key="source",
    )


def test_automatic_carry_preserves_findings_without_a_new_round_or_status(tmp_path):
    _, ledger = _repo(tmp_path)
    source = source_record(ledger)
    github = CarryGitHub()
    github.checks[PR.head_sha] = [CheckRun(20, "in_progress", None, "test-unit")]

    def no_judge(*args, **kwargs):
        pytest.fail("unchanged patch must not be judged again")

    outcome = review_pull(
        PR,
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        run_judge=no_judge,
    )
    assert outcome.attested
    assert not any(c[0] in ("status", "review") for c in github.calls)
    carried = ledger.list("red-alpha", attestation=AttestationKind.REVIEW_VERDICT)[-1]
    assert carried.data["sha"] == PR.head_sha
    assert carried.data["carried_from"] == OLD
    assert carried.data["findings"] == source.data["findings"]
    assert carried.data["carry_forwards"] == source.data["carry_forwards"]
    before = rounds.loop_state([source], [])
    after = rounds.loop_state([source, carried], [])
    assert after.last_judged == carried
    assert after.judged == before.judged
    assert after.findings == before.findings
    assert after.confirmed == before.confirmed
    assert rounds.open_carry_forwards([source, carried], [], repository=PR.repository) == (
        rounds.open_carry_forwards([source], [], repository=PR.repository)
    )
    assert "57fa86dc" in github.check_summaries[99]


def test_a_carried_head_does_not_expire_rulings_after_the_source(tmp_path):
    _, ledger = _repo(tmp_path)
    source = source_record(ledger)
    ruling = ledger.attest(
        "red-alpha",
        AttestationKind.REVIEW_RULING,
        {
            "repository": PR.repository,
            "pr": PR.number,
            "finding": "F-7-1",
            "ruling": "carry_forward",
            "decision": "defer",
        },
        issuer="operator",
        idempotency_key="ruling",
    )
    github = CarryGitHub()
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    carry_review(
        PR, decision, github=github, policy=default_policy(), ledger=ledger, project="red-alpha"
    )
    carried = ledger.list("red-alpha", attestation=AttestationKind.REVIEW_VERDICT)[-1]
    before = rounds.loop_state([source], [ruling])
    after = rounds.loop_state([source, carried], [ruling])
    assert after.rulings == before.rulings
    assert after.findings == before.findings


def test_rerun_label_forces_a_judge(tmp_path):
    _, ledger = _repo(tmp_path)
    source_record(ledger)
    github = CarryGitHub()
    calls = []

    def judging(*args, **kwargs):
        calls.append(kwargs)
        return approve(kwargs["provider"])

    review_pull(
        replace(PR, labels=(default_policy().rerun_label,)),
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        run_judge=judging,
    )
    assert calls


@pytest.mark.parametrize("changed", ["patch", "base", "failed-ci"])
def test_automatic_path_reviews_when_carry_is_refused(tmp_path, changed):
    _, ledger = _repo(tmp_path)
    source_record(ledger)
    github = CarryGitHub()
    if changed == "patch":
        github.patches[(NEW_BASE, PR.head_sha)] = PATCH.replace("+new", "+different")
    elif changed == "base":
        github.base_files = ("src/x.py",)
    else:
        github.checks[PR.head_sha] = [CheckRun(21, "completed", "failure", "test-unit")]
    # The ordinary incremental review compares the earlier head to the current one.
    github.patches[(OLD, PR.head_sha)] = PATCH
    calls = []

    def judging(*args, **kwargs):
        calls.append(kwargs)
        return approve(kwargs["provider"])

    outcome = review_pull(
        PR,
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        run_judge=judging,
    )
    assert calls and outcome.attested
    assert (
        "carried_from"
        not in ledger.list("red-alpha", attestation=AttestationKind.REVIEW_VERDICT)[-1].data
    )


def test_ledger_refusal_never_publishes_a_carried_approval(tmp_path, monkeypatch):
    from rail.ledger import Unattested

    _, ledger = _repo(tmp_path)
    source_record(ledger)
    github = CarryGitHub()

    def refuse(*args, **kwargs):
        raise Unattested(tmp_path / "pending.json", "delivery_disabled")

    monkeypatch.setattr(ledger, "attest", refuse)
    outcome = review_pull(
        PR,
        github=github,
        policy=default_policy(),
        ledger=ledger,
        project="red-alpha",
        run_judge=lambda *a, **k: pytest.fail("no judge"),
    )
    assert not outcome.attested
    assert any(c[:3] == ("complete", 99, "failure") for c in github.calls)
    assert not any(c[0] in ("review", "status") for c in github.calls)


def command_setup(tmp_path, monkeypatch, github):
    from rail.commands import verdict

    repo, ledger = _repo(tmp_path)
    source_record(ledger)
    github.close = lambda: None
    config = SimpleNamespace(
        repositories=[SimpleNamespace(slug=PR.repository, path=repo)],
        policy=default_policy(),
        app_id=1,
        installation_id=2,
        private_key_pem=lambda: "fake",
    )
    monkeypatch.setattr(verdict, "_config", lambda path: config)
    monkeypatch.setattr("rail.reviewer.github.GitHubApp", lambda **kwargs: github)
    monkeypatch.setattr(verdict, "open_ledger", lambda *args, **kwargs: ledger)
    return verdict, ledger


def invoke_carry(verdict):
    return CliRunner().invoke(
        verdict.command, ["carry", "--repository", PR.repository, "--pr", str(PR.number)]
    )


@pytest.mark.parametrize(
    "scenario", ["allowed", "refused", "in_progress", "failure", "neutral", "already", "both"]
)
def test_carry_command(tmp_path, monkeypatch, scenario):
    github = CarryGitHub()
    verdict, ledger = command_setup(tmp_path, monkeypatch, github)
    if scenario == "refused":
        github.base_files = ("src/x.py",)
    if scenario != "allowed":
        github.checks[PR.head_sha].extend(
            [
                CheckRun(89, "completed", "success", "red-rail/review"),
                CheckRun(
                    90,
                    "in_progress" if scenario == "in_progress" else "completed",
                    None
                    if scenario == "in_progress"
                    else (scenario if scenario in ("failure", "neutral") else "success"),
                    "red-rail/review",
                ),
            ]
        )
    if scenario == "both":
        github.statuses[PR.head_sha] = [
            CommitStatus(40, "independent-review", "failure", "old"),
            CommitStatus(41, "independent-review", "success", "latest"),
        ]
    posted = []

    def run(args, **kwargs):
        posted.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(
        verdict, "post_independent_status", partial(verdict.post_independent_status, run=run)
    )
    count = len(ledger.list("red-alpha"))
    result = invoke_carry(verdict)
    refused = scenario in ("refused", "in_progress", "failure", "neutral")
    assert result.exit_code == (1 if refused else 0), result.output
    if scenario in ("allowed", "already"):
        assert posted == [
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"repos/{PR.repository}/statuses/{PR.head_sha}",
                "-f",
                "state=success",
                "-f",
                "context=independent-review",
                "-f",
                f"description=Carried from {OLD}: approved",
            ]
        ]
        assert len(ledger.list("red-alpha")) == count + (scenario == "allowed")
    else:
        assert not posted and not github.calls
        assert len(ledger.list("red-alpha")) == count
    if scenario in ("already", "both"):
        assert f"red-rail/review already on {PR.head_sha}" in result.output
    if scenario == "both":
        assert f"independent-review already on {PR.head_sha}" in result.output
    if scenario == "refused":
        assert "base changed" in result.output
    if scenario in ("in_progress", "failure", "neutral"):
        state = "in_progress/none" if scenario == "in_progress" else f"completed/{scenario}"
        assert f"new head already judged: {state}" in result.output


def test_carry_retry_after_gh_failure_posts_only_status(tmp_path, monkeypatch):
    github = CarryGitHub()
    verdict, ledger = command_setup(tmp_path, monkeypatch, github)
    attempts = []

    def run(args, **kwargs):
        attempts.append(args)
        if len(attempts) == 1:
            return subprocess.CompletedProcess(args, 1, "", "gh: service unavailable")
        github.statuses[PR.head_sha] = [
            CommitStatus(50, "independent-review", "success", "carried")
        ]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(
        verdict, "post_independent_status", partial(verdict.post_independent_status, run=run)
    )
    count = len(ledger.list("red-alpha"))
    first = invoke_carry(verdict)
    assert first.exit_code == 1
    assert "gh: service unavailable" in first.output
    assert len(ledger.list("red-alpha")) == count + 1
    writes = list(github.calls)
    monkeypatch.setattr(
        verdict, "open_ledger", lambda *a, **k: pytest.fail("retry must not open the ledger")
    )
    second = invoke_carry(verdict)
    assert second.exit_code == 0, second.output
    assert f"red-rail/review already on {PR.head_sha}" in second.output
    assert attempts == [attempts[0], attempts[0]]
    assert github.calls == writes
    assert len(ledger.list("red-alpha")) == count + 1
    third = invoke_carry(verdict)
    assert third.exit_code == 0, third.output
    assert f"independent-review already on {PR.head_sha}" in third.output
    assert len(attempts) == 2
    assert github.calls == writes
    assert len(ledger.list("red-alpha")) == count + 1


def test_user_status_uses_exact_gh_argv_and_bounded_runner():
    from rail.commands.verdict import post_independent_status

    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    post_independent_status(PR.repository, PR.head_sha, description="x" * 180, run=run)
    assert calls == [
        (
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"repos/{PR.repository}/statuses/{PR.head_sha}",
                "-f",
                "state=success",
                "-f",
                "context=independent-review",
                "-f",
                f"description={'x' * 140}",
            ],
            {"capture_output": True, "text": True, "check": False, "timeout": 10},
        )
    ]


def test_user_status_names_gh_error_without_exposing_token():
    import click

    from rail.commands.verdict import post_independent_status

    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "gh: forbidden ghs_privatevalue")

    with pytest.raises(click.ClickException, match="gh: forbidden") as error:
        post_independent_status(PR.repository, PR.head_sha, description="carried", run=run)
    assert "ghs_privatevalue" not in str(error.value)


@pytest.mark.parametrize("failure", ["missing", "timeout"])
def test_user_status_reports_unavailable_gh(failure):
    import click

    from rail.commands.verdict import post_independent_status

    def run(args, **kwargs):
        if failure == "missing":
            raise FileNotFoundError("gh not installed")
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    with pytest.raises(click.ClickException, match="gh"):
        post_independent_status(PR.repository, PR.head_sha, description="carried", run=run)


def test_carry_refuses_missing_ledger_source_without_publishing(tmp_path):
    _, ledger = _repo(tmp_path)
    github = CarryGitHub()
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    with pytest.raises(ValueError, match="source verdict"):
        carry_review(
            PR, decision, github=github, policy=default_policy(), ledger=ledger, project="red-alpha"
        )
    assert not github.calls


def test_carry_refuses_a_malformed_source_before_creating_a_check(tmp_path):
    _, ledger = _repo(tmp_path)
    source = source_record(ledger)
    ledger.attest(
        "red-alpha",
        AttestationKind.REVIEW_VERDICT,
        {**source.data, "findings": [None]},
        issuer=REVIEWER_IDENTITY,
        idempotency_key="malformed-source",
    )
    github = CarryGitHub()
    decision = carry_decision(github, PR.repository, PR.number, PR.head_sha)
    with pytest.raises(ValueError, match="source verdict malformed"):
        carry_review(
            PR, decision, github=github, policy=default_policy(), ledger=ledger, project="red-alpha"
        )
    assert not github.calls


def test_carry_command_refuses_ci_before_loading_credentials(monkeypatch):
    from rail.commands import verdict

    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(verdict, "_config", lambda path: pytest.fail("must not load credentials"))
    result = CliRunner().invoke(
        verdict.command, ["carry", "--repository", PR.repository, "--pr", str(PR.number)]
    )
    assert result.exit_code == 1
    assert "host only" in result.output
