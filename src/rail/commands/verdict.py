"""Carry an unchanged pull request's verdicts on the host (decision 57fa86dc)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import click

from rail.brain.settings import DEFAULT_REVIEWER_TOKEN_FILE
from rail.commands.reviewer import _config
from rail.ledger import LedgerError, open_ledger
from rail.model import load_rail_config
from rail.private import PrivateFileError
from rail.remotes import Runner


def post_independent_status(
    repository: str,
    sha: str,
    *,
    description: str,
    run: Runner = subprocess.run,
) -> None:
    """Post with the user's gh credentials (decision 57fa86dc), without handling a token.

    The App has no statuses permission and can never post independent-review, so the
    automatic reviewer path structurally cannot either.
    """
    args = [
        "gh",
        "api",
        "-X",
        "POST",
        f"repos/{repository}/statuses/{sha}",
        "-f",
        "state=success",
        "-f",
        "context=independent-review",
        "-f",
        f"description={description[:140]}",
    ]
    try:
        done = run(args, capture_output=True, text=True, check=False, timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise click.ClickException("gh api status timed out after 10 seconds") from exc
    except OSError as exc:
        raise click.ClickException("gh api status could not run on this host") from exc
    if done.returncode:
        detail = re.sub(
            r"(?i)\b(?:gh[pousr]_[a-z0-9_]+|github_pat_[a-z0-9_]+|bearer\s+\S+)",
            "[redacted]",
            (done.stderr or done.stdout).strip(),
        )
        raise click.ClickException(f"gh api status failed (exit {done.returncode}): {detail}")


@click.group("verdict")
def command() -> None:
    """Manage independent review evidence on the host."""


@command.command("carry")
@click.option("--repository", required=True, help="owner/name, as reviewer.yaml watches it.")
@click.option("--pr", type=click.IntRange(min=1), required=True)
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
def carry(repository: str, pr: int, config_path: Path | None) -> None:
    """Carry both verdicts to the current head only after every other CI result is green."""
    if os.environ.get("CI"):
        raise click.ClickException("verdict carry is host only, never CI")

    # Reviewer dependencies remain optional for every other command.
    from rail.reviewer.carry_verdict import INDEPENDENT_STATUS, RAIL_CHECK, carry_decision
    from rail.reviewer.github import GitHubApp, GitHubError
    from rail.reviewer.service import REVIEWER_IDENTITY, carry_review

    github = None
    try:
        config = _config(config_path)
        watched = next((r for r in config.repositories if r.slug == repository), None)
        if watched is None:
            raise click.UsageError(f"{repository} is not watched by the reviewer configuration")
        github = GitHubApp(
            app_id=config.app_id,
            installation_id=config.installation_id,
            private_key_pem=config.private_key_pem(),
        )
        pull = github.pull(repository, pr)
        decision = carry_decision(github, repository, pr, pull.head_sha, require_green=True)
        if not decision.allowed:
            click.echo(decision.reason)
            raise SystemExit(1)
        latest = max(
            github.check_runs(repository, pull.head_sha, name=RAIL_CHECK),
            key=lambda c: c.id,
            default=None,
        )
        if latest is None:
            cfg = load_rail_config(watched.path)
            ledger = open_ledger(
                watched.path, agent=REVIEWER_IDENTITY, token_file=DEFAULT_REVIEWER_TOKEN_FILE
            )
            outcome = carry_review(
                pull,
                decision,
                github=github,
                policy=config.policy,
                ledger=ledger,
                project=cfg.project,
                repo_path=watched.path,
            )
            if not outcome.attested:
                raise click.ClickException("; ".join(outcome.failures))
            click.echo(
                f"attested review_verdict for {pull.head_sha}, carried from {decision.judged_head}"
            )
            click.echo(f"completed {RAIL_CHECK} check run {outcome.check_run_id}: success")
        elif latest.status == "completed" and latest.conclusion == "success":
            click.echo(f"{RAIL_CHECK} already on {pull.head_sha}")
        else:
            click.echo(f"new head already judged: {latest.status}/{latest.conclusion or 'none'}")
            raise SystemExit(1)
        status = max(
            (
                s
                for s in github.commit_statuses(repository, pull.head_sha)
                if s.context == INDEPENDENT_STATUS
            ),
            key=lambda s: s.id,
            default=None,
        )
        if status is not None and status.state == "success":
            click.echo(f"{INDEPENDENT_STATUS} already on {pull.head_sha}")
            return
        original = decision.independent_status
        description = (
            f"Carried from {decision.judged_head}: {original.description if original else ''}"
        )
        post_independent_status(
            repository,
            pull.head_sha,
            description=description,
        )
        click.echo(f"posted {INDEPENDENT_STATUS} on {pull.head_sha}: success")
    except (PrivateFileError, OSError, ValueError, GitHubError, LedgerError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if github is not None:
            github.close()
