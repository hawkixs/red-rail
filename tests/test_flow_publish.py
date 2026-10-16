"""Independent flow reads keep failed sources unknown and cached evidence private."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from rail.flow import FlowDiscovery, FlowPull, RunEvidence, StatusEvidence, SuiteEvidence
from rail.flow_cache import FlowCache
from rail.flow_publish import FlowCollection, collect_flow, flow_block
from rail.ledger import Record, RecordKind
from rail.reviewer.config import RepositoryConfig, ReviewerConfig
from rail.reviewer.github import GitHubError

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
START = NOW - timedelta(days=2)


def receipt(*, recorded_at: datetime = START + timedelta(minutes=8), **data: Any) -> Record:
    """Use real digests and pre-merge timestamps for the final review run."""
    return Record.build(
        kind=RecordKind.ATTESTATION,
        project="red-example",
        issuer="example-reviewer",
        idempotency_key=f"review:{recorded_at.isoformat()}",
        payload={
            "kind": "review_verdict",
            "data": {
                "repository": "example/repo",
                "pr": 1,
                "sha": "a" * 40,
                "round": 1,
                "check_run_id": 2,
                **data,
            },
        },
        recorded_at=recorded_at,
    )


class FlowGitHub:
    """Expose only flow's list and evidence readers; any other request fails the test."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.fail: set[str] = set()
        self.pull = FlowPull(
            "example/repo", 1, "a" * 40, "b" * 40, START, START + timedelta(minutes=10)
        )

    def flow_merged_pulls(
        self, repository: str, *, base: str, since: datetime, until: datetime
    ) -> FlowDiscovery:
        self.calls.append(("pulls", repository, base, since, until))
        if repository in self.fail or "pulls" in self.fail:
            raise GitHubError("source content must stay private")
        if "detail" in self.fail:
            return FlowDiscovery((), 1)
        pull = replace(self.pull, repository=repository)
        return FlowDiscovery((pull,) if since <= pull.merged_at <= until else (), 0)

    def check_suites(self, repository: str, sha: str) -> list[SuiteEvidence]:
        self.calls.append(("suites", repository, sha))
        if "suites" in self.fail:
            raise GitHubError("source content must stay private")
        if "empty_suites" in self.fail:
            return []
        return [SuiteEvidence(1, 99, "github-actions", self.pull.created_at)]

    def flow_check_runs(self, repository: str, sha: str) -> list[RunEvidence]:
        self.calls.append(("runs", repository, sha))
        if "runs" in self.fail:
            raise GitHubError("source content must stay private")
        if "empty_runs" in self.fail:
            return []
        return [
            RunEvidence(
                1,
                "ci",
                99,
                "github-actions",
                "completed",
                START + timedelta(minutes=1),
                START + timedelta(minutes=5),
            ),
            RunEvidence(
                2,
                "red-rail/review",
                1,
                "example-reviewer",
                "completed",
                START + timedelta(minutes=4),
                START + timedelta(minutes=8),
            ),
        ]

    def status_history(self, repository: str, sha: str) -> list[StatusEvidence]:
        self.calls.append(("statuses", repository, sha))
        if "statuses" in self.fail:
            raise GitHubError("source content must stay private")
        if "empty_statuses" in self.fail:
            return []
        description = (
            "Carried from a previous verdict (ha 20261014T120600-abc)"
            if "carried_judge" in self.fail
            else "Passed probe (ha 20261014T120600-abc)"
        )
        return [
            StatusEvidence(
                1,
                "independent-review",
                "success",
                description,
                START + timedelta(minutes=9),
            )
        ]


def config(tmp_path: Path, *, queue: Path | None = None) -> ReviewerConfig:
    """Do not open any host configuration or credential."""
    return ReviewerConfig(
        app_id=1,
        installation_id=2,
        private_key_file=tmp_path / "unused.pem",
        repositories=[
            RepositoryConfig(
                slug="example/repo", path=tmp_path / "missing-checkout", merge_queue=queue
            )
        ],
    )


def gather(tmp_path: Path, github: FlowGitHub, **kwargs: Any) -> FlowCollection:
    return collect_flow(
        kwargs.pop("config", config(tmp_path)),
        now=NOW,
        window_days=30,
        github=github,
        cache=FlowCache(tmp_path / "cache"),
        records_for=kwargs.pop("records_for", lambda repo: []),
        **kwargs,
    )


def test_flow_covers_every_watched_repository_independently(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = config(tmp_path)
    cfg = cfg.model_copy(
        update={
            "repositories": cfg.repositories
            + [RepositoryConfig(slug="example/broken", path=tmp_path / "absent")]
        }
    )
    github = FlowGitHub()
    github.fail.add("example/broken")
    answer = gather(tmp_path, github, config=cfg, records_for=lambda repo: None)
    assert [r.repository for r in answer.repositories] == ["example/repo"]
    assert answer.coverage.missing == ("example/broken",)
    assert answer.repositories[0].segments["flow"].median == 10
    assert answer.coverage.unread_segments["example/repo"]["ledger"] == 1
    assert capsys.readouterr().err == "example/broken pulls GitHubError\n"
    from rail import publish

    monkeypatch.setattr("rail.gitrepo.fetch_main", lambda repo: True)
    monkeypatch.setattr("rail.gitrepo.file_at", lambda *args: None)

    def forbidden_ledger(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("an unread manifest must not select unverified receipts")

    published = publish.collect(
        cfg,
        now=NOW,
        clock=lambda: NOW,
        github=github,
        open_ledger=forbidden_ledger,
        flow_cache=FlowCache(tmp_path / "cache"),
    )
    assert published.flow is not None
    assert published.autonomy is published.delivery is published.delivery_v2 is None
    assert published.flow.repositories[0].segments["flow"].median == 10
    assert published.flow.coverage.missing == ("example/broken",)


@pytest.mark.parametrize(
    "failure,unread,nulls,step",
    [
        ("detail", 1, (), None),
        ("suites", 1, (), "checks"),
        ("empty_suites", 1, (), None),
        ("runs", 0, ("ci_start", "ci", "ci_longest", "review_start", "review", "idle"), "checks"),
        ("statuses", 0, ("judge_wait", "judge", "idle"), "statuses"),
        ("ledger", 0, ("review_start", "review"), "ledger"),
        ("queue", 0, ("holder",), "queue"),
        ("empty_runs", 0, ("ci_start", "ci", "ci_longest", "review_start", "review"), None),
        ("empty_statuses", 0, ("judge_wait", "judge"), None),
        ("empty_ledger", 0, ("review_start", "review"), None),
        ("empty_queue", 0, ("holder",), None),
        ("carried_judge", 0, (), None),
    ],
)
def test_flow_failure_matrix_keeps_or_excludes_exactly_once(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure: str,
    unread: int,
    nulls: tuple[str, ...],
    step: str | None,
) -> None:
    github = FlowGitHub()
    github.fail.add(failure)
    queue = tmp_path / "queue.json"
    if failure != "queue":
        queue.write_text(
            json.dumps(
                {
                    "merged": [
                        {
                            "pr": 1,
                            "sha": "a" * 40,
                            "merged_at": github.pull.merged_at.isoformat(),
                            "holder_since": (START - timedelta(minutes=2)).isoformat(),
                        }
                    ]
                }
            )
        )
    if failure == "empty_queue":
        queue.write_text('{"merged":[]}')

    def records_for(repo: RepositoryConfig) -> list[Record] | None:
        if failure == "ledger":
            print(repo.slug, "ledger", "LedgerError", file=sys.stderr)
            return None
        if failure == "empty_ledger":
            return []
        return [receipt()]

    answer = gather(tmp_path, github, config=config(tmp_path, queue=queue), records_for=records_for)
    assert answer.coverage.unread_prs == ({"example/repo": 1} if unread else {})
    assert len(answer.recent) == (0 if unread else 1)
    counts = {name: 1 for name in nulls}
    if failure == "ledger":
        counts["ledger"] = 1
    assert answer.coverage.unread_segments == ({"example/repo": counts} if counts else {})
    if not unread:
        timeline = answer.recent[0]
        assert all(getattr(timeline.segments, name) is None for name in nulls)
        expected = {
            "flow": 10,
            "ci_start": 1,
            "ci": 4,
            "ci_longest": 4,
            "review_start": 4,
            "review": 4,
            "judge_wait": 9,
            "judge": 3,
            "idle": 2,
            "holder": 12,
        }
        expected.update(dict.fromkeys(nulls))
        if failure == "empty_runs":
            expected["idle"] = 7
        elif failure in ("empty_statuses", "carried_judge"):
            expected["idle"] = 3
        if failure == "carried_judge":
            expected["judge"] = None
        assert asdict(timeline.segments) == expected
        assert answer.repositories[0].carry_rate == (
            None if failure in ("ledger", "empty_ledger") else 0
        )
        if failure == "ledger":
            assert timeline.rounds is timeline.carried is None
            assert timeline.segments.idle == 2
        if failure == "empty_ledger":
            assert timeline.rounds == 0 and timeline.carried is None
        if failure == "carried_judge":
            assert timeline.judge_carried is True and timeline.segments.judge is None
            assert timeline.segments.judge_wait == 9
            assert answer.repositories[0].segments["judge"].n == 0
    stderr = capsys.readouterr().err
    assert "source content" not in stderr and str(queue) not in stderr
    if step:
        assert stderr.count(f" {step} ") == 1
    else:
        assert stderr == ""


def test_flow_second_publication_has_no_per_pr_requests(tmp_path: Path) -> None:
    github = FlowGitHub()
    gather(tmp_path, github)
    for _ in range(2):
        github.calls.clear()
        gather(tmp_path, github)
        assert [call[0] for call in github.calls] == ["pulls"]
    incomplete = tmp_path / "incomplete"
    github.fail.add("statuses")
    gather(incomplete, github)
    github.fail.clear()
    github.calls.clear()
    gather(incomplete, github)
    assert [call[0] for call in github.calls] == ["pulls", "suites", "runs", "statuses"]


def test_flow_reloads_local_sources_even_on_complete_github_cache(tmp_path: Path) -> None:
    github = FlowGitHub()
    first = gather(tmp_path, github)
    assert first.recent[0].carried is None
    queue = tmp_path / "queue.json"
    queue.write_text(
        json.dumps(
            {
                "merged": [
                    {
                        "pr": 1,
                        "sha": "a" * 40,
                        "merged_at": github.pull.merged_at.isoformat(),
                        "holder_since": (START - timedelta(minutes=2)).isoformat(),
                    }
                ]
            }
        )
    )
    github.calls.clear()
    second = gather(
        tmp_path,
        github,
        config=config(tmp_path, queue=queue),
        records_for=lambda repo: [
            receipt(carried_from="c" * 40),
            receipt(recorded_at=START + timedelta(minutes=11)),
        ],
    )
    timeline = second.recent[0]
    assert timeline.carried is True and timeline.segments.review == 0
    assert timeline.segments.holder == 12 and timeline.holder_approx is False
    assert second.repositories[0].carry_rate == 1
    assert second.repositories[0].segments["review"].n == 0
    assert [call[0] for call in github.calls] == ["pulls"]
    block = flow_block(second, generated_at=NOW, now=NOW, window_days=30)
    assert block.recent[0].head_sha == "a" * 12
    assert block.recent[0].rounds == 0 and block.recent[0].carried is True
    assert block.recent[0].segments.holder == 12
    assert "description" not in block.model_dump_json()
    assert "(ha " not in block.model_dump_json() and "a" * 40 not in block.model_dump_json()


def test_flow_window_and_main_are_independent_of_autonomy_since_and_default_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = FlowGitHub()
    for merged_at in (NOW - timedelta(days=40), NOW):
        github.pull = replace(github.pull, merged_at=merged_at, created_at=merged_at)
        answer = collect_flow(
            config(tmp_path),
            now=NOW,
            window_days=40,
            github=github,
            cache=FlowCache(tmp_path / "cache"),
            records_for=lambda repo: [],
        )
        assert github.calls[-4][2:] == ("main", NOW - timedelta(days=40), NOW)
        assert answer.coverage.missing == ()
        assert len(answer.recent) == 1 and answer.recent[0].segments.flow == 0
    block = flow_block(answer, generated_at=NOW, now=NOW, window_days=40)
    assert block.window_start == NOW - timedelta(days=40)
    assert block.generated_at == NOW
    from rail import publish
    from rail.autonomy import SINCE, MergedPull
    from tests.test_cli_metrics_publish import manifest

    path = tmp_path / "checkout"
    manifest(path, "red-example", tier="dev")
    cfg = config(tmp_path).model_copy(
        update={"repositories": [RepositoryConfig(slug="example/repo", path=path)]}
    )
    monkeypatch.setattr("rail.gitrepo.fetch_main", lambda repo: True)
    monkeypatch.setattr("rail.gitrepo.file_at", lambda *args: (path / "rail.yaml").read_text())
    monkeypatch.setattr("rail.gitrepo.files_at", lambda *args: {})

    class IndependentGitHub(FlowGitHub):
        def default_branch(self, repository: str) -> str:
            self.calls.append(("branch", repository))
            return "trunk"

        def merged_pulls(self, repository: str, *, base: str, since: datetime) -> list[MergedPull]:
            assert base == "trunk" and since == SINCE
            self.calls.append(("autonomy_pulls", repository, base, since))
            return [MergedPull(2, START, START - timedelta(hours=1))]

        def commits_since(
            self, repository: str, *, branch: str, since: datetime
        ) -> list[tuple[datetime, str]]:
            assert branch == "trunk" and since == SINCE
            self.calls.append(("commits", repository, branch, since))
            return []

    independent = IndependentGitHub()
    independent.pull = replace(
        independent.pull, created_at=NOW - timedelta(days=40), merged_at=NOW - timedelta(days=40)
    )
    published = publish.collect(
        cfg,
        now=NOW,
        clock=lambda: NOW,
        github=independent,
        open_ledger=lambda *a, **k: None,
        window_days=40,
        flow_cache=FlowCache(tmp_path / "independent-cache"),
    )
    assert published.flow is not None and published.autonomy is not None
    assert published.flow.repositories[0].merged_prs == 1
    assert published.flow.recent[0].merged_at < SINCE
    assert published.autonomy.total is not None
    assert published.autonomy.total.d30.merged_prs == 1
    assert published.autonomy.total.d30.ready_to_merged_hours_median == 1
    assert [call[0] for call in independent.calls] == [
        "branch",
        "autonomy_pulls",
        "commits",
        "pulls",
        "suites",
        "runs",
        "statuses",
    ]


def test_flow_assembly_failure_does_not_abort_other_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from rail import publish
    from tests.test_cli_metrics_publish import Clock, ReadGitHub, ReadLedger, manifest

    path = tmp_path / "repo"
    manifest(path, "red-example", tier="dev")
    cfg = config(tmp_path)
    cfg = cfg.model_copy(
        update={"repositories": [RepositoryConfig(slug="example/repo", path=path)]}
    )
    monkeypatch.setattr("rail.gitrepo.fetch_main", lambda repo: True)
    monkeypatch.setattr("rail.gitrepo.file_at", lambda *args: (path / "rail.yaml").read_text())
    monkeypatch.setattr("rail.gitrepo.files_at", lambda *args: {})

    github = ReadGitHub(Clock())
    github.flow_fail = True
    answer = publish.collect(
        cfg,
        now=NOW,
        clock=lambda: NOW,
        open_ledger=lambda *a, **k: ReadLedger([]),
        github=github,
        flow_cache=FlowCache(tmp_path / "cache"),
    )
    assert answer.autonomy is not None and answer.flow is not None
    assert answer.flow.coverage.missing == ("example/repo",)
    assert capsys.readouterr().err == "example/repo pulls GitHubError\n"

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("source content must stay private")

    monkeypatch.setattr(publish, "collect_flow", broken)
    answer = publish.collect(
        cfg,
        now=NOW,
        clock=Clock(),
        open_ledger=lambda *a, **k: ReadLedger([]),
        github=ReadGitHub(Clock()),
        flow_cache=FlowCache(tmp_path / "cache"),
    )
    assert answer.flow is None and answer.autonomy is not None
    assert "source content" not in capsys.readouterr().err
    monkeypatch.setattr(publish, "window_values", broken)
    with pytest.raises(ValueError, match="source content"):
        publish.collect(
            cfg,
            now=NOW,
            clock=Clock(),
            open_ledger=lambda *a, **k: ReadLedger([]),
            github=ReadGitHub(Clock()),
            flow_cache=FlowCache(tmp_path / "cache"),
        )
