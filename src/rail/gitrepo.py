"""Read-only git helpers for the gates. They never raise: outside a repository they answer
None / False / empty, and the gate turns that into an explicit failure."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

_SCP_LIKE = re.compile(r"^(?:[^@/:]+@)?(?P<host>[^:/]+):(?!//).+$")  # git@host:path


def _git(repo: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
        )
    except (FileNotFoundError, OSError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def is_git_repo(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-inside-work-tree") == "true"


def head_sha(repo: Path) -> str | None:
    return _git(repo, "rev-parse", "HEAD")


def is_ancestor(repo: Path, sha: str, of: str = "HEAD") -> bool:
    return _git(repo, "merge-base", "--is-ancestor", sha, of) is not None


def distance(repo: Path, sha: str) -> int | None:
    """Commits on HEAD since `sha` (0 when `sha` is HEAD); None when `sha` is not an ancestor."""
    if not is_ancestor(repo, sha):
        return None
    count = _git(repo, "rev-list", "--count", f"{sha}..HEAD")
    return int(count) if count is not None else None


def url_host(url: str) -> str:
    """The host of a remote URL, lower-cased: `ssh://git@host:2222/path`, `git@host:path`,
    `https://host/path`; a local path has none. Compared exactly, never as a substring."""
    match = _SCP_LIKE.match(url) if "://" not in url else None
    if match:
        return match.group("host").lower()
    if "://" in url:
        authority = url.split("://", 1)[1].split("/", 1)[0]
        host = authority.rsplit("@", 1)[-1].split(":", 1)[0]
        return host.lower()
    return ""


def remotes(repo: Path) -> dict[str, str]:
    """Remote name -> push URL."""
    out = _git(repo, "remote", "-v")
    found: dict[str, str] = {}
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == "(push)":
            found[parts[0]] = parts[1]
    return found


def recent_subjects(repo: Path, count: int) -> list[str]:
    out = _git(repo, "log", f"-{count}", "--no-merges", "--format=%s")
    return [s for s in (out or "").splitlines() if s]


def commit_timestamp(repo: Path, sha: str) -> datetime | None:
    out = _git(repo, "show", "-s", "--format=%cI", sha)
    if not out:
        return None
    try:
        return datetime.fromisoformat(out.splitlines()[0])
    except ValueError:
        return None


def latest_tag(repo: Path) -> str | None:
    return _git(repo, "describe", "--tags", "--abbrev=0") or None
