"""Synthetic GitHub reads pin completeness, retry costs and private cache storage."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from rail.flow import FlowPull, GitHubEvidence, RunEvidence, StatusEvidence, SuiteEvidence
from rail.flow_cache import CachedGitHub, FlowCache, cache_key, default_cache_root, is_complete
from rail.reviewer.github import GitHubError

MERGED = datetime(2026, 10, 10, 12, tzinfo=UTC)
MATURE = datetime(2026, 10, 12, 12, tzinfo=UTC)


def pull() -> FlowPull:
    """Distinct head and merge identities catch requests against the wrong commit."""
    return FlowPull("example/repo", 7, "a" * 40, "b" * 40, MERGED - timedelta(hours=1), MERGED)


def evidence(pr: FlowPull | None = None) -> GitHubEvidence:
    """A completed run ends ten minutes before merge; the judge succeeds five minutes later."""
    return GitHubEvidence(
        pr or pull(),
        (SuiteEvidence(1, 1, "github-actions", MERGED - timedelta(minutes=30)),),
        (
            RunEvidence(
                2,
                "ci",
                1,
                "github-actions",
                "completed",
                MERGED - timedelta(minutes=25),
                MERGED - timedelta(minutes=10),
            ),
        ),
        (
            StatusEvidence(
                3, "independent-review", "success", "approved", MERGED - timedelta(minutes=5)
            ),
        ),
    )


class CountingGitHub:
    """Count all final-head requests and fail each reader independently without a transport."""

    def __init__(self, facts: GitHubEvidence | None = None) -> None:
        self.facts = facts or evidence()
        self.calls: list[tuple[str, str, str]] = []
        self.errors: dict[str, Exception] = {}

    def _read(self, kind: str, repository: str, sha: str) -> Any:
        self.calls.append((kind, repository, sha))
        if kind in self.errors:
            raise self.errors[kind]
        return list(getattr(self.facts, kind))

    def check_suites(self, repository: str, sha: str) -> list[SuiteEvidence]:
        return self._read("suites", repository, sha)

    def flow_check_runs(self, repository: str, sha: str) -> list[RunEvidence]:
        return self._read("runs", repository, sha)

    def status_history(self, repository: str, sha: str) -> list[StatusEvidence]:
        return self._read("statuses", repository, sha)


def cache_file(root: Path, pr: FlowPull | None = None) -> Path:
    """Digest filenames never expose raw repository or pull identifiers."""
    return root / f"{cache_key(pr or pull())}.json"


def test_complete_cache_reuses_without_any_per_pr_call(tmp_path: Path) -> None:
    """A fresh instance reuses mature complete evidence, including successful empty reads."""
    for index, facts in enumerate((evidence(), GitHubEvidence(pull(), (), (), ()))):
        root = tmp_path / str(index)
        github = CountingGitHub(facts)
        assert FlowCache(root).get_or_read(pull(), github=github, now=MATURE) == facts
        assert github.calls == [
            ("suites", "example/repo", "a" * 40),
            ("runs", "example/repo", "a" * 40),
            ("statuses", "example/repo", "a" * 40),
        ]
        github.calls.clear()
        assert FlowCache(root).get_or_read(pull(), github=github, now=MATURE) == facts
        assert github.calls == []
        assert json.loads(cache_file(root).read_text())["complete"] is True


def test_cache_key_separates_repository_pr_merge_sha_and_version(tmp_path: Path) -> None:
    """All four key inputs matter; head and every pull field are checked on reuse."""
    pr = pull()
    canonical = json.dumps(
        ("example/repo", 7, "b" * 40, "rail-flow/v1"), separators=(",", ":")
    ).encode()
    assert cache_key(pr) == hashlib.sha256(canonical).hexdigest()
    keys = {
        cache_key(pr),
        cache_key(replace(pr, repository="example/other")),
        cache_key(replace(pr, number=8)),
        cache_key(replace(pr, merge_sha="c" * 40)),
        cache_key(pr, version="rail-flow/v2"),
    }
    assert len(keys) == 5
    assert all(len(key) == 64 and set(key) <= set("0123456789abcdef") for key in keys)
    cache = FlowCache(tmp_path)
    github = CountingGitHub()
    cache.get_or_read(pr, github=github, now=MATURE)
    original = cache_file(tmp_path).read_text()
    for field, value in (
        ("repository", "example/other"),
        ("number", 8),
        ("head_sha", "c" * 40),
        ("merge_sha", "d" * 40),
        ("created_at", "2026-10-10T10:00:00+00:00"),
        ("merged_at", "2026-10-10T11:00:00+00:00"),
    ):
        raw = json.loads(original)
        raw["evidence"]["pull"][field] = value
        cache_file(tmp_path).write_text(json.dumps(raw))
        github.calls.clear()
        assert cache.get_or_read(pr, github=github, now=MATURE) == evidence()
        assert len(github.calls) == 3
    for changed in (
        replace(pr, head_sha="c" * 40),
        replace(pr, created_at=MERGED - timedelta(hours=2)),
        replace(pr, merged_at=MERGED - timedelta(minutes=1)),
        replace(pr, number=8),
    ):
        github.facts = evidence(changed)
        github.calls.clear()
        assert cache.get_or_read(changed, github=github, now=MATURE) == github.facts
        assert len(github.calls) == 3


def test_incomplete_cache_is_reread_until_completed_and_older_than_24h(tmp_path: Path) -> None:
    """Aging never promotes a fetch; real completion and successful reads are required."""
    facts = evidence()
    assert not is_complete(facts, now=MERGED + timedelta(hours=23))
    assert not is_complete(facts, now=MERGED + timedelta(hours=24))
    assert is_complete(facts, now=MERGED + timedelta(hours=24, seconds=1))
    for field in ("suites", "runs", "statuses"):
        assert not is_complete(replace(facts, **{field: None}), now=MATURE)
    completed = facts.runs[0]
    for run in (
        replace(completed, status="in_progress", completed_at=None),
        replace(completed, status="in_progress"),
        replace(completed, completed_at=None),
        replace(completed, completed_at=MATURE + timedelta(seconds=1)),
        replace(completed, started_at=None),
    ):
        assert not is_complete(replace(facts, runs=(run,)), now=MATURE)
    assert is_complete(replace(facts, runs=(replace(completed, completed_at=MATURE),)), now=MATURE)
    late = replace(completed, started_at=MERGED + timedelta(seconds=1), completed_at=None)
    assert is_complete(replace(facts, runs=(late,)), now=MATURE)
    at_merge = replace(late, started_at=MERGED)
    assert is_complete(replace(facts, runs=(at_merge,)), now=MATURE)
    unknown = replace(completed, app_id=None, app_slug=None, started_at=None)
    assert not is_complete(replace(facts, runs=(unknown,)), now=MATURE)

    cache = FlowCache(tmp_path)
    github = CountingGitHub()
    early = MERGED + timedelta(hours=23)
    assert cache.get_or_read(pull(), github=github, now=early) == facts
    raw = json.loads(cache_file(tmp_path).read_text())
    assert raw["complete"] is False
    assert raw["fetched_at"] == early.isoformat()
    for now in (MERGED + timedelta(hours=24), MATURE):
        github.calls.clear()
        assert cache.get_or_read(pull(), github=github, now=now) == facts
        assert len(github.calls) == 3
    github.calls.clear()
    cache.get_or_read(pull(), github=github, now=MATURE)
    assert github.calls == []

    active_root = tmp_path / "active"
    active = replace(completed, status="in_progress", completed_at=None)
    github = CountingGitHub(replace(facts, runs=(active,)))
    cache = FlowCache(active_root)
    cache.get_or_read(pull(), github=github, now=MATURE)
    assert json.loads(cache_file(active_root).read_text())["complete"] is False
    github.facts = replace(facts, runs=(replace(completed, completed_at=MATURE),))
    github.calls.clear()
    assert cache.get_or_read(pull(), github=github, now=MATURE) == github.facts
    assert len(github.calls) == 3
    assert json.loads(cache_file(active_root).read_text())["complete"] is True


def test_status_permission_recovery_refetches_incomplete_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refused status read retains CI and is retried without persisting error text."""
    github = CountingGitHub()
    github.errors["statuses"] = GitHubError("synthetic refusal", status=403)
    cache = FlowCache(tmp_path)
    first = cache.get_or_read(pull(), github=github, now=MATURE)
    assert first == replace(evidence(), statuses=None)
    assert capsys.readouterr().err == "example/repo statuses GitHubError\n"
    raw = json.loads(cache_file(tmp_path).read_text())
    assert raw["complete"] is False
    assert raw["evidence"]["statuses"] is None
    assert "synthetic refusal" not in cache_file(tmp_path).read_text()
    github.errors.clear()
    github.calls.clear()
    assert cache.get_or_read(pull(), github=github, now=MATURE) == evidence()
    assert len(github.calls) == 3
    assert json.loads(cache_file(tmp_path).read_text())["complete"] is True

    for kind, field, step in (("suites", "suites", "checks"), ("runs", "runs", "checks")):
        root = tmp_path / kind
        github = CountingGitHub()
        github.errors[kind] = GitHubError("synthetic refusal", status=500)
        assert FlowCache(root).get_or_read(pull(), github=github, now=MATURE) == replace(
            evidence(), **{field: None}
        )
        assert len(github.calls) == 3
        assert capsys.readouterr().err == f"example/repo {step} GitHubError\n"
        assert json.loads(cache_file(root).read_text())["complete"] is False

    github = CountingGitHub()
    github.errors["statuses"] = GitHubError("synthetic refusal", status=403)
    changed = replace(pull(), head_sha="c" * 40)
    github.facts = evidence(changed)
    assert cache.get_or_read(changed, github=github, now=MATURE).statuses is None
    assert json.loads(cache_file(tmp_path).read_text())["complete"] is False


def test_corrupt_wrong_version_and_unwritable_cache_are_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed or inaccessible storage cannot remove a discovered pull."""
    cache = FlowCache(tmp_path)
    github = CountingGitHub()
    cache.get_or_read(pull(), github=github, now=MATURE)
    original = cache_file(tmp_path).read_text()
    corrupt: list[str | bytes] = ["{", "null", "[]", b"\xff"]
    for field, value in (
        ("version", "rail-flow/v2"),
        ("key", "0" * 64),
        ("complete", "true"),
        ("fetched_at", "2026-10-12T12:00:00"),
        ("fetched_at", "2026-10-13T12:00:00+00:00"),
        ("evidence", []),
    ):
        raw = json.loads(original)
        raw[field] = value
        corrupt.append(json.dumps(raw))
    for path, value in (
        (("pull", "number"), True),
        (("pull", "head_sha"), 3),
        (("suites",), {}),
        (("runs", 0, "id"), "2"),
        (("runs", 0, "started_at"), "bad date"),
        (("statuses", 0, "description"), []),
        (("statuses", 0, "created_at"), "2026-10-10T12:00:00+01:00"),
    ):
        raw = json.loads(original)
        target = raw["evidence"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        corrupt.append(json.dumps(raw))
    raw = json.loads(original)
    raw["fetched_at"] = (MERGED + timedelta(hours=23)).isoformat()
    corrupt.append(json.dumps(raw))
    raw = json.loads(original)
    raw["evidence"]["statuses"] = None
    corrupt.append(json.dumps(raw))
    for content in corrupt:
        cache_file(tmp_path).write_bytes(content.encode() if isinstance(content, str) else content)
        github.calls.clear()
        assert cache.get_or_read(pull(), github=github, now=MATURE) == evidence()
        assert len(github.calls) == 3

    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied")
    github.calls.clear()
    assert FlowCache(blocked).get_or_read(pull(), github=github, now=MATURE) == evidence()
    assert len(github.calls) == 3
    assert blocked.read_text() == "occupied"

    def denied(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("synthetic I/O refusal")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", denied)
        patch.setattr(os, "replace", denied)
        github.calls.clear()
        assert cache.get_or_read(pull(), github=github, now=MATURE) == evidence()
        assert len(github.calls) == 3
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        f"{cache_key(pull())}.json",
        "not-a-directory",
    ]


def test_cache_contains_only_github_inputs_and_has_private_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only raw GitHub evidence is stored, using a private atomic replacement."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert default_cache_root() == tmp_path / "state" / "red-rail" / "flow"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert default_cache_root() == tmp_path / "home" / ".local" / "state" / "red-rail" / "flow"
    monkeypatch.setenv("XDG_STATE_HOME", "")
    assert default_cache_root() == tmp_path / "home" / ".local" / "state" / "red-rail" / "flow"

    cached = CachedGitHub(evidence(), MATURE, True)
    assert [field.name for field in fields(cached)] == ["evidence", "fetched_at", "complete"]
    assert not hasattr(cached, "__dict__")
    with pytest.raises(FrozenInstanceError):
        cached.complete = False

    root = tmp_path / "cache"
    root.mkdir(mode=0o755)
    cache = FlowCache(root)
    github = CountingGitHub()
    cache.get_or_read(pull(), github=github, now=MERGED + timedelta(hours=23))
    target = cache_file(root)
    previous = target.read_bytes()
    target.chmod(0o644)
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def observed_replace(source: str | Path, destination: str | Path) -> None:
        source, destination = Path(source), Path(destination)
        assert source.parent == root
        assert source != target
        assert destination == target
        assert target.read_bytes() == previous
        assert source.stat().st_mode & 0o777 == 0o600
        assert json.loads(source.read_text())["complete"] is True
        replacements.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", observed_replace)
    assert cache.get_or_read(pull(), github=github, now=MATURE) == evidence()
    assert len(replacements) == 1
    assert root.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600
    assert list(root.iterdir()) == [target]
    raw = json.loads(target.read_text())
    assert set(raw) == {"key", "version", "evidence", "fetched_at", "complete"}
    assert set(raw["evidence"]) == {"pull", "suites", "runs", "statuses"}
    for name, cls in (
        ("pull", FlowPull),
        ("suites", SuiteEvidence),
        ("runs", RunEvidence),
        ("statuses", StatusEvidence),
    ):
        value = raw["evidence"][name]
        item = value if name == "pull" else value[0]
        assert set(item) == {field.name for field in fields(cls)}
    assert raw["fetched_at"] == MATURE.isoformat()
    assert raw["evidence"]["statuses"][0]["description"] == "approved"
