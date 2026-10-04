"""Publishing a new repository on GitHub with the host's `gh` — and on a declared mirror with
`glab` — the tools the operator already uses and authenticates; red-rail never handles a token
(spec §7). ReD is GitHub only (decision 30acbbde): the mirror exists where `hygiene.mirror_host`
is declared, nowhere else."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

from rail.policy import GATE_DEFAULTS
from rail.private import PrivateFileError, read_private_file

CANONICAL_OWNER = "hawkixs"
MIRROR_GROUP = "hawkixs_project/red"
MIRROR_REMOTE = "gitlab"
# the canonical host is the policy's (hygiene.canonical_host); the mirror host is the project's
# declaration (`{host}`), since the default is none
CANONICAL_URL = f"git@{GATE_DEFAULTS['hygiene.canonical_host']}:{CANONICAL_OWNER}/{{slug}}.git"
MIRROR_URL = f"ssh://git@{{host}}:2222/{MIRROR_GROUP}/{{slug}}.git"

# The independent reviewer's GitHub App is private: its public page does not exist, so its id is
# read from the reviewer config of the host that runs the reviewer. The slug is the reviewer's
# identity (`review.reviewer_identity`), which is also the slug the review check is pinned to.
REVIEWER_APP_SLUG = str(GATE_DEFAULTS["review.reviewer_identity"])
REVIEWER_CONFIG = Path("~/.config/red-rail/reviewer.yaml")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class RemoteError(Exception):
    """A remote step failed; the message says what was done and what not to do next."""


_ABSENT = re.compile(r"not found|could not resolve|404", re.IGNORECASE)


def _run(
    args: list[str],
    *,
    run: Runner,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "check": False}
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if env is not None:
        kwargs["env"] = env
    if stdin is not None:
        kwargs["input"] = stdin
    try:
        return run(args, **kwargs)
    except (FileNotFoundError, OSError) as exc:
        raise RemoteError(f"{args[0]} is not available on this host: {exc}") from exc


def _ok(
    args: list[str],
    *,
    run: Runner,
    what: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> str:
    done = _run(args, run=run, cwd=cwd, env=env, stdin=stdin)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        raise RemoteError(f"{what} failed (exit {done.returncode}): {detail}")
    return done.stdout


def _glab_env(mirror: str) -> dict[str, str]:
    """`glab` targets the declared mirror host, not whatever host it was last logged into."""
    return {**os.environ, "GITLAB_HOST": mirror}


def ensure_absent(slug: str, *, mirror: str | None = None, run: Runner) -> None:
    """Every host must answer 'not found'. Any other failure (auth, network, rate limit) is
    not a free slug: it is a question the tool could not answer, and it stops here."""
    questions: list[tuple[list[str], str, dict[str, str] | None]] = [
        (["gh", "repo", "view", f"{CANONICAL_OWNER}/{slug}"], "GitHub", None)
    ]
    if mirror:
        questions.append(
            (["glab", "repo", "view", f"{MIRROR_GROUP}/{slug}"], "GitLab", _glab_env(mirror))
        )
    for args, where, env in questions:
        done = _run(args, run=run, env=env)
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


def create_gitlab(slug: str, description: str, *, mirror: str, run: Runner) -> None:
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
        env=_glab_env(mirror),
    )


def configure(repo: Path, slug: str, *, mirror: str | None = None, run: Runner) -> None:
    _ok(
        ["git", "remote", "add", "origin", CANONICAL_URL.format(slug=slug)],
        run=run,
        cwd=repo,
        what="git remote add origin",
    )
    if mirror:
        _ok(
            ["git", "remote", "add", MIRROR_REMOTE, MIRROR_URL.format(host=mirror, slug=slug)],
            run=run,
            cwd=repo,
            what=f"git remote add {MIRROR_REMOTE}",
        )
    _ok(["git", "config", "remote.pushDefault", "origin"], run=run, cwd=repo, what="git config")


def push(repo: Path, *, mirror: str | None = None, run: Runner) -> None:
    """`main` to GitHub, then to the declared mirror when there is one; a mirror failure after
    GitHub succeeded is reported as what it is, never repaired by rewriting GitHub."""
    _ok(["git", "push", "-u", "origin", "main"], run=run, cwd=repo, what="git push origin main")
    if not mirror:
        return
    done = _run(["git", "push", MIRROR_REMOTE, "main"], run=run, cwd=repo)
    if done.returncode != 0:
        raise RemoteError(
            f"git push {MIRROR_REMOTE} main failed after GitHub succeeded — do not rewrite GitHub; "
            f"fix the mirror and run `git push {MIRROR_REMOTE} main`: "
            f"{(done.stderr or done.stdout).strip()}"
        )


def parity(repo: Path, *, run: Runner) -> bool:
    heads = []
    for remote in ("origin", MIRROR_REMOTE):
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


def _public_app_id(slug: str, *, run: Runner) -> int:
    out = _ok(["gh", "api", f"apps/{slug}", "--jq", ".id"], run=run, what=f"gh api apps/{slug}")
    try:
        return int(out.strip())
    except ValueError as exc:
        raise RemoteError(f"gh api apps/{slug}: not an id: {out!r}") from exc


def _configured_app_id(config: Path) -> int | None:
    """`app_id` of the host's reviewer config; None when the file is absent. A file that is
    there but not private, or without a usable `app_id`, is refused: that is a defect to name,
    never an absence. The file is read for that one field — the rest of it (key path, installation)
    is not this module's business and nothing from it is reported."""
    if not os.path.lexists(config):
        return None
    try:
        raw = yaml.safe_load(read_private_file(config))
    except (PrivateFileError, yaml.YAMLError) as exc:
        raise RemoteError(f"{config}: {exc}") from exc
    found = raw.get("app_id") if isinstance(raw, dict) else None
    if not isinstance(found, int) or isinstance(found, bool) or found <= 0:
        raise RemoteError(f"{config}: `app_id` is not a positive integer")
    return found


def app_id(slug: str, *, run: Runner = subprocess.run, reviewer_config: Path | None = None) -> int:
    """The numeric id of the GitHub App `slug`. No App identity is wired in code.

    The independent reviewer's App is private, so `gh api apps/<slug>` answers 404 for it: its
    id is read from this host's reviewer config (`app_id`), and the public page is asked only
    where that file is absent. Any other App is public and resolved by its page."""
    if slug != REVIEWER_APP_SLUG:
        return _public_app_id(slug, run=run)
    config = (reviewer_config or REVIEWER_CONFIG).expanduser()
    configured = _configured_app_id(config)
    if configured is not None:
        return configured
    try:
        return _public_app_id(slug, run=run)
    except RemoteError as exc:
        raise RemoteError(
            f"cannot resolve the id of the reviewer App {slug}: {config} is absent and "
            f"`gh api apps/{slug}` failed ({exc}) — put the App's `app_id` in the reviewer "
            "config, or make the App public"
        ) from exc


def protect_main(
    slug: str,
    checks: Sequence[tuple[str, str]],
    *,
    run: Runner = subprocess.run,
    reviewer_config: Path | None = None,
) -> None:
    """Require `checks` — `(check name, slug of the App that publishes it)` — on `main`, each
    pinned to its App so a same-named check from another App never satisfies it (decision
    a3846910). Admins are not bound: merging on judgment stays the operator's gesture. Not
    strict: a main that moves forces no extra review pass. Every step here runs after the
    repository was created and pushed, so any refusal says main is left unprotected."""
    repository = f"{CANONICAL_OWNER}/{slug}"
    try:
        pinned = [
            {"context": name, "app_id": app_id(app, run=run, reviewer_config=reviewer_config)}
            for name, app in checks
        ]
        body = {
            "required_status_checks": {"strict": False, "checks": pinned},
            "enforce_admins": False,
            "required_pull_request_reviews": None,
            "restrictions": None,
        }
        _ok(
            ["gh", "api", "-X", "PUT", f"repos/{repository}/branches/main/protection"]
            + ["--input", "-"],
            run=run,
            what="gh api branch protection",
            stdin=json.dumps(body),
        )
    except RemoteError as exc:
        raise RemoteError(
            f"{exc} — {repository} is created and pushed, but main is NOT protected: put the "
            "protection on before the first pull request"
        ) from exc


def publish(
    repo: Path,
    slug: str,
    description: str,
    *,
    mirror: str | None = None,
    run: Runner = subprocess.run,
) -> None:
    """Kickstart runbook `a050e6ec`, steps 2 and 8–12, as one call; `mirror` is the declared
    mirror host (`hygiene.mirror_host`), None for GitHub only."""
    ensure_absent(slug, mirror=mirror, run=run)
    create_github(slug, description, run=run)
    if mirror:
        create_gitlab(slug, description, mirror=mirror, run=run)
    configure(repo, slug, mirror=mirror, run=run)
    push(repo, mirror=mirror, run=run)
    if mirror and not parity(repo, run=run):
        raise RemoteError(
            "main differs between GitHub and GitLab after the push; compare `git ls-remote`"
        )
