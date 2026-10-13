"""Thin, never-raising git helpers used by the gates."""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from rail import gitrepo

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "rail",
    "GIT_AUTHOR_EMAIL": "rail@example.invalid",
    "GIT_COMMITTER_NAME": "rail",
    "GIT_COMMITTER_EMAIL": "rail@example.invalid",
    "GIT_AUTHOR_DATE": "2026-09-15T08:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-09-15T08:00:00+00:00",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=ENV
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "remote", "add", "origin", "git@github.com:hawkixs/red-probe.git")
    _git(
        tmp_path,
        "remote",
        "add",
        "gitlab",
        "ssh://git@gitlab.hawkixs.local:2222/hawkixs_project/red/red-probe.git",
    )
    (tmp_path / "a.txt").write_text("a\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "feat: first")
    return tmp_path


def test_is_git_repo(tmp_path: Path) -> None:
    assert gitrepo.is_git_repo(tmp_path) is False
    assert gitrepo.is_git_repo(_repo(tmp_path)) is True


def test_head_sha_and_ancestry(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = gitrepo.head_sha(repo)
    assert first and len(first) == 40
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix: second")
    assert gitrepo.is_ancestor(repo, first) is True
    assert gitrepo.distance(repo, first) == 1
    assert gitrepo.is_ancestor(repo, "0" * 40) is False
    assert gitrepo.distance(repo, "0" * 40) is None


def test_remotes_are_push_urls_by_name(tmp_path: Path) -> None:
    assert gitrepo.remotes(_repo(tmp_path)) == {
        "origin": "git@github.com:hawkixs/red-probe.git",
        "gitlab": "ssh://git@gitlab.hawkixs.local:2222/hawkixs_project/red/red-probe.git",
    }
    assert gitrepo.remotes(tmp_path / "nowhere") == {}


def test_recent_subjects_newest_first(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "fix: second")
    assert gitrepo.recent_subjects(repo, 5) == ["fix: second", "feat: first"]
    assert gitrepo.recent_subjects(repo, 1) == ["fix: second"]
    assert gitrepo.recent_subjects(tmp_path / "nowhere", 5) == []


def test_commit_timestamp_is_aware(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    when = gitrepo.commit_timestamp(repo, gitrepo.head_sha(repo) or "")
    assert when == datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
    assert gitrepo.commit_timestamp(repo, "0" * 40) is None


def test_latest_tag(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert gitrepo.latest_tag(repo) is None
    _git(repo, "tag", "v0.1.0")
    assert gitrepo.latest_tag(repo) == "v0.1.0"


def test_helpers_never_raise_outside_a_repository(tmp_path: Path) -> None:
    assert gitrepo.head_sha(tmp_path) is None
    assert gitrepo.latest_tag(tmp_path) is None
    assert gitrepo.is_ancestor(tmp_path, "abc") is False


@pytest.mark.parametrize("error", [OSError("unavailable"), subprocess.TimeoutExpired("git", 30)])
def test_fetch_main_is_bounded_noninteractive_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def run(args: list[str], **kwargs: Any) -> None:
        assert args == ["git", "-C", str(tmp_path), "fetch", "--quiet", "origin", "main"]
        assert kwargs["timeout"] == 30
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["check"] is False
        raise error

    monkeypatch.setattr(gitrepo.subprocess, "run", run)
    assert gitrepo.fetch_main(tmp_path) is False


@pytest.mark.parametrize("code", [0, 1])
def test_fetch_main_reports_exit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    monkeypatch.setattr(
        gitrepo.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], code),
    )
    assert gitrepo.fetch_main(tmp_path) is (code == 0)


def test_files_at_reads_only_immediate_json_blobs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    receipts = repo / "docs/receipts"
    receipts.mkdir(parents=True)
    (receipts / "a.json").write_text('{"value": 1}\n')
    (receipts / "with space.json").write_text('{"value": 2}\n')
    (receipts / "notes.txt").write_text("not a receipt")
    nested = receipts / "nested"
    nested.mkdir()
    (nested / "ignored.json").write_text("ignored")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Record receipts")
    (receipts / "a.json").write_text("uncommitted")
    assert gitrepo.files_at(repo, "HEAD", "docs/receipts") == {
        "docs/receipts/a.json": '{"value": 1}',
        "docs/receipts/with space.json": '{"value": 2}',
    }
    assert gitrepo.files_at(repo, "HEAD", "absent") == {}
    assert gitrepo.files_at(repo, "HEAD", "a.txt") is None
    assert gitrepo.files_at(repo, "missing-ref", "docs/receipts") is None
    assert gitrepo.files_at(tmp_path / "absent", "HEAD", "docs/receipts") is None


def test_files_at_rejects_non_utf8_blobs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    receipts = repo / "docs/receipts"
    receipts.mkdir(parents=True)
    (receipts / "invalid.json").write_bytes(b"\xff")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Unreadable receipt")
    assert gitrepo.files_at(repo, "HEAD", "docs/receipts") is None


def test_read_helpers_return_none_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired("git", 10)

    monkeypatch.setattr(gitrepo.subprocess, "run", timeout)
    assert gitrepo.file_at(tmp_path, "HEAD", "rail.yaml") is None
    assert gitrepo.files_at(tmp_path, "HEAD", "docs/receipts") is None


def test_url_host_reads_the_authority_of_every_remote_syntax() -> None:
    """The mirror host is compared exactly, never as a substring of the whole URL."""
    from rail.gitrepo import url_host

    assert url_host("git@github.com:hawkixs/red-probe.git") == "github.com"
    assert url_host("ssh://git@gitlab.hawkixs.local:2222/hawkixs_project/red/red-probe.git") == (
        "gitlab.hawkixs.local"
    )
    assert url_host("https://GitHub.com/hawkixs/red-probe.git") == "github.com"
    assert url_host("git@github.com:hawkixs/gitlab.hawkixs.local-tools.git") == "github.com"
    assert url_host("/srv/git/red-probe.git") == ""
