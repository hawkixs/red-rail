"""Carry verdicts only with equivalent patches and an untouched base (decision 57fa86dc).

This decision reads evidence only. Publishing and attesting belong to the reviewer service.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, replace
from typing import Protocol

from rail.reviewer.github import CheckRun, CommitStatus, Comparison, GitHubError, PullRequest

RAIL_CHECK = "red-rail/review"
INDEPENDENT_STATUS = "independent-review"
PATCH_TIMEOUT = 10
PASSING = frozenset(("success", "neutral", "skipped"))


class CarryGitHub(Protocol):
    def pull(self, repository: str, number: int) -> PullRequest: ...
    def commit_shas(self, repository: str, number: int) -> list[str]: ...
    def check_runs(
        self, repository: str, sha: str, *, name: str | None = None
    ) -> list[CheckRun]: ...
    def commit_statuses(self, repository: str, sha: str) -> list[CommitStatus]: ...
    def compare_files(self, repository: str, base_sha: str, head_sha: str) -> Comparison: ...
    def compare_diff(self, repository: str, base_sha: str, head_sha: str) -> str: ...


@dataclass(frozen=True, slots=True)
class CarryDecision:
    allowed: bool
    reason: str
    judged_head: str | None = None
    judged_base: str | None = None
    new_base: str | None = None
    patch_id: str | None = None
    rail_check: CheckRun | None = None
    independent_status: CommitStatus | None = None


def _patch_id(diff: str) -> str:
    """Hash stdin without a checkout, refusing empty evidence and bounding the subprocess."""
    result = subprocess.run(
        ["git", "patch-id", "--stable"],
        input=diff,
        text=True,
        capture_output=True,
        timeout=PATCH_TIMEOUT,
        check=True,
    )
    lines = result.stdout.splitlines()
    if len(lines) != 1 or not re.fullmatch(r"[0-9a-f]{40,64} [0-9a-f]{40,64}", lines[0]):
        raise ValueError("patch-id unavailable: empty or incomplete diff")
    return lines[0].split()[0]


def carry_decision(
    github: CarryGitHub,
    repository: str,
    pr_number: int,
    new_head: str,
    *,
    require_green: bool = True,
) -> CarryDecision:
    """Require both latest approvals, equal patches and blobs, untouched files, and green CI.

    The automatic reviewer may accept running CI, but never a completed failure. It only
    carries its own check; the producing session still waits for green CI to carry its status.
    """
    decision = CarryDecision(False, "no judged head with both verdicts")
    try:
        pr = github.pull(repository, pr_number)
        if pr.head_sha != new_head:
            return replace(decision, reason="pull request head changed")
        commits = github.commit_shas(repository, pr_number)
        if new_head not in commits:
            return decision
        earlier = commits[: commits.index(new_head)]
        for sha in reversed(earlier):
            checks = github.check_runs(repository, sha, name=RAIL_CHECK)
            statuses = [
                s
                for s in github.commit_statuses(repository, sha)
                if s.context == INDEPENDENT_STATUS
            ]
            check = max(checks, key=lambda c: c.id, default=None)
            status = max(statuses, key=lambda s: s.id, default=None)
            if check is None:
                continue
            if (
                check.status == "completed"
                and check.conclusion == "success"
                and status is not None
                and status.state == "success"
            ):
                decision = replace(
                    decision, judged_head=sha, rail_check=check, independent_status=status
                )
            # Never cross a newer judgment to revive an older approval.
            break
        if decision.judged_head is None:
            return decision
        judged = github.compare_files(repository, pr.base_sha, decision.judged_head)
        new = github.compare_files(repository, pr.base_sha, new_head)
        decision = replace(decision, judged_base=judged.merge_base, new_base=new.merge_base)
        old_diff = github.compare_diff(repository, judged.merge_base, decision.judged_head)
        new_diff = github.compare_diff(repository, new.merge_base, new_head)
        if any(
            line.startswith(("Binary files ", "GIT binary patch"))
            for diff in (old_diff, new_diff)
            for line in diff.splitlines()
        ):
            return replace(decision, reason="binary change: patch-id cannot prove equality")
        old_patch = _patch_id(old_diff)
        new_patch = _patch_id(new_diff)
        decision = replace(decision, patch_id=old_patch)
        if old_patch != new_patch:
            return replace(decision, reason="patch-id differs")
        judged_files = {f.filename: f for f in judged.file_details}
        new_files = {f.filename: f for f in new.file_details}
        for path in sorted(judged_files.keys() | new_files.keys()):
            if judged_files.get(path) != new_files.get(path):
                return replace(decision, reason=f"PR files differ: {path}")
        if judged.merge_base != new.merge_base:
            base = github.compare_files(repository, judged.merge_base, new.merge_base)
            if base.merge_base != judged.merge_base:
                return replace(decision, reason="base history diverged")
            overlap = sorted(set(judged.files) & set(base.files))
            if overlap:
                return replace(decision, reason=f"base changed {overlap[0]} touched by the PR")
        excluded = {RAIL_CHECK, INDEPENDENT_STATUS}
        checks = [c for c in github.check_runs(repository, new_head) if c.name not in excluded]
        statuses = [
            s for s in github.commit_statuses(repository, new_head) if s.context not in excluded
        ]
        if require_green:
            if not checks and not statuses:
                return replace(decision, reason="CI not green: no check on the new head")
            judged_checks = {
                (c.name, c.app_id)
                for c in github.check_runs(repository, decision.judged_head)
                if c.name not in excluded
            }
            judged_statuses = {
                (s.context, s.creator_id)
                for s in github.commit_statuses(repository, decision.judged_head)
                if s.context not in excluded
            }
            new_checks = {(c.name, c.app_id) for c in checks}
            new_statuses = {(s.context, s.creator_id) for s in statuses}
            judged_check_names = {name for name, _ in judged_checks}
            judged_status_names = {name for name, _ in judged_statuses}
            mismatched = sorted(
                {name for name, _ in new_checks - judged_checks if name in judged_check_names}
                | {
                    name
                    for name, _ in new_statuses - judged_statuses
                    if name in judged_status_names
                }
            )
            if mismatched:
                return replace(decision, reason=f"CI source differs: {mismatched[0]}")
            missing = sorted(
                {name for name, _ in judged_checks - new_checks}
                | {name for name, _ in judged_statuses - new_statuses}
            )
            if missing:
                return replace(decision, reason=f"CI not green: {missing[0]} missing")
        for check in checks:
            pending = check.status != "completed"
            if (pending and require_green) or (not pending and check.conclusion not in PASSING):
                state = "pending" if pending else check.conclusion or "unknown"
                return replace(decision, reason=f"CI not green: {check.name} {state}")
        for status in statuses:
            if status.state not in PASSING and (require_green or status.state != "pending"):
                return replace(decision, reason=f"CI not green: {status.context} {status.state}")
        return replace(decision, allowed=True, reason="unchanged patch and untouched PR files")
    except (
        GitHubError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        return replace(decision, reason=f"carry evidence unavailable: {exc}")
