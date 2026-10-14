"""Read-only git helpers for the gates. They never raise: outside a repository they answer
None / False / empty, and the gate turns that into an explicit failure."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

_SCP_LIKE = re.compile(r"^(?:[^@/:]+@)?(?P<host>[^:/]+):(?!//).+$")  # git@host:path


def _git(repo: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def is_git_repo(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-inside-work-tree") == "true"


def head_sha(repo: Path) -> str | None:
    return _git(repo, "rev-parse", "HEAD")


def file_at(repo: Path, ref: str, path: str) -> str | None:
    """`path` as committed at `ref`; None when the ref or the file is missing."""
    return _git(repo, "show", f"{ref}:{path}")


def fetch_main(repo: Path) -> bool:
    """Refresh main's remote-tracking ref without touching HEAD or prompting for credentials."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "fetch", "--quiet", "origin", "main"],
            capture_output=True,
            check=False,
            timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def files_at(repo: Path, ref: str, directory: str) -> dict[str, str] | None:
    """Read immediate JSON blobs, never local files; an absent directory is an empty ledger.

    An unreadable ref, directory or blob returns None so publication cannot report zero
    evidence after a failed read. Blob IDs keep the listing stable during concurrent fetches.
    """
    entry = _git(repo, "ls-tree", "-z", ref, "--", directory)
    if entry is None:
        return None
    if not entry:
        return {}
    metadata, _, _ = entry.partition("\t")
    parts = metadata.split()
    if len(parts) != 3 or parts[1] != "tree":
        return None
    listing = _git(repo, "ls-tree", "-z", parts[2])
    if listing is None:
        return None
    files: dict[str, str] = {}
    for entry in listing.split("\0"):
        if not entry:
            continue
        metadata, _, name = entry.partition("\t")
        if not name.endswith(".json"):
            continue
        parts = metadata.split()
        if len(parts) != 3 or parts[1] != "blob":
            return None
        content = _git(repo, "cat-file", "blob", parts[2])
        if content is None:
            return None
        files[f"{directory}/{name}"] = content
    return files


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
