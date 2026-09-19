"""Publishing a new repository on both remotes with the host's `gh` and `glab` — the tools the
operator already uses and authenticates; red-rail never handles a token (spec §7)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rail.policy import GATE_DEFAULTS

CANONICAL_OWNER = "hawkixs"
MIRROR_GROUP = "hawkixs_project/red"
# the hosts are the policy's (hygiene.canonical_host / hygiene.mirror_host): one place to change
CANONICAL_URL = f"git@{GATE_DEFAULTS['hygiene.canonical_host']}:{CANONICAL_OWNER}/{{slug}}.git"
MIRROR_URL = f"ssh://git@{GATE_DEFAULTS['hygiene.mirror_host']}:2222/{MIRROR_GROUP}/{{slug}}.git"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class RemoteError(Exception):
    """A remote step failed; the message says what was done and what not to do next."""


_ABSENT = re.compile(r"not found|could not resolve|404", re.IGNORECASE)


def _run(
    args: list[str], *, run: Runner, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "check": False}
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    try:
        return run(args, **kwargs)
    except (FileNotFoundError, OSError) as exc:
        raise RemoteError(f"{args[0]} is not available on this host: {exc}") from exc


def _ok(args: list[str], *, run: Runner, what: str, cwd: Path | None = None) -> str:
    done = _run(args, run=run, cwd=cwd)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        raise RemoteError(f"{what} failed (exit {done.returncode}): {detail}")
    return done.stdout


def ensure_absent(slug: str, *, run: Runner) -> None:
    """Both hosts must answer 'not found'. Any other failure (auth, network, rate limit) is
    not a free slug: it is a question the tool could not answer, and it stops here."""
    for args, where in (
        (["gh", "repo", "view", f"{CANONICAL_OWNER}/{slug}"], "GitHub"),
        (["glab", "repo", "view", f"{MIRROR_GROUP}/{slug}"], "GitLab"),
    ):
        done = _run(args, run=run)
        if done.returncode == 0:
            raise RemoteError(f"{where} already has {slug}; pick another slug")
        detail = (done.stderr or done.stdout).strip()
        if not _ABSENT.search(detail):
            raise RemoteError(
                f"cannot tell whether {where} has {slug} (exit {done.returncode}): {detail}"
            )


def create_github(slug: str, description: str, *, run: Runner) -> None:
    _ok(
        [
            "gh",
            "repo",
            "create",
            f"{CANONICAL_OWNER}/{slug}",
            "--private",
            "--description",
            description,
        ],
        run=run,
        what="gh repo create",
    )


def create_gitlab(slug: str, description: str, *, run: Runner) -> None:
    _ok(
        [
            "glab",
            "repo",
            "create",
            f"{MIRROR_GROUP}/{slug}",
            "--private",
            "--defaultBranch",
            "main",
            "--skipGitInit",
            "--description",
            description,
        ],
        run=run,
        what="glab repo create",
    )


def configure(repo: Path, slug: str, *, run: Runner) -> None:
    _ok(
        ["git", "remote", "add", "origin", CANONICAL_URL.format(slug=slug)],
        run=run,
        cwd=repo,
        what="git remote add origin",
    )
    _ok(
        ["git", "remote", "add", "gitlab", MIRROR_URL.format(slug=slug)],
        run=run,
        cwd=repo,
        what="git remote add gitlab",
    )
    _ok(["git", "config", "remote.pushDefault", "origin"], run=run, cwd=repo, what="git config")


def push_both(repo: Path, *, run: Runner) -> None:
    _ok(["git", "push", "-u", "origin", "main"], run=run, cwd=repo, what="git push origin main")
    done = _run(["git", "push", "gitlab", "main"], run=run, cwd=repo)
    if done.returncode != 0:
        raise RemoteError(
            "git push gitlab main failed after GitHub succeeded — do not rewrite GitHub; "
            f"fix the mirror and run `git push gitlab main`: {(done.stderr or done.stdout).strip()}"
        )


def parity(repo: Path, *, run: Runner) -> bool:
    heads = []
    for remote in ("origin", "gitlab"):
        out = _ok(
            ["git", "ls-remote", remote, "refs/heads/main"],
            run=run,
            cwd=repo,
            what=f"git ls-remote {remote}",
        )
        heads.append(out.split()[0] if out.split() else "")
    return heads[0] != "" and heads[0] == heads[1]


def github_repository_id(slug: str, *, run: Runner = subprocess.run) -> int:
    """The numeric id brain binds by (`gh api repos/<slug> --jq .id`)."""
    out = _ok(["gh", "api", f"repos/{slug}", "--jq", ".id"], run=run, what="gh api repos")
    try:
        return int(out.strip())
    except ValueError as exc:
        raise RemoteError(f"gh api repos/{slug}: not an id: {out!r}") from exc


def publish(repo: Path, slug: str, description: str, *, run: Runner = subprocess.run) -> None:
    """Kickstart runbook `a050e6ec`, steps 2 and 8–12, as one call."""
    ensure_absent(slug, run=run)
    create_github(slug, description, run=run)
    create_gitlab(slug, description, run=run)
    configure(repo, slug, run=run)
    push_both(repo, run=run)
    if not parity(repo, run=run):
        raise RemoteError(
            "main differs between GitHub and GitLab after the push; compare `git ls-remote`"
        )
