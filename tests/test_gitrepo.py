"""Thin, never-raising git helpers used by the gates."""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

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
