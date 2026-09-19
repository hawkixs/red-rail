"""`rail reviewer`: the independent reviewer, on the host, in pull mode (ADR-0003)."""

from __future__ import annotations

import time
from pathlib import Path

import click

from rail.private import PrivateFileError


@click.group("reviewer")
def command() -> None:
    """The independent PR reviewer (GitHub App red-rail-reviewer)."""


def _config(path: Path | None):
    from rail.reviewer.config import DEFAULT_CONFIG, load_reviewer_config

    return load_reviewer_config(path or DEFAULT_CONFIG)


def _once(config, *, only: str | None, pr: int | None) -> int:
    from rail.ledger import LedgerError, open_ledger
    from rail.model import load_rail_config
    from rail.reviewer.github import GitHubApp, GitHubError
    from rail.reviewer.service import pending_reviews, review_pull

    github = GitHubApp(
        app_id=config.app_id,
        installation_id=config.installation_id,
        private_key_pem=config.private_key_pem(),
    )
    reviewed = 0
    try:
        for repository in config.repositories:
            if only and repository.slug != only:
                continue
            cfg = load_rail_config(repository.path)
            ledger = open_ledger(repository.path)
            pulls = (
                [github.pull(repository.slug, pr)]
                if pr
                else pending_reviews(github, repository.slug, config.policy)
            )
            for pull in pulls:
                outcome = review_pull(
                    pull,
                    github=github,
                    policy=config.policy,
                    ledger=ledger,
                    project=cfg.project,
                    repo_path=repository.path,
                )
                reviewed += 1
                status = "attested" if outcome.attested else "UNATTESTED"
                click.echo(
                    f"{repository.slug}#{pull.number} {pull.head_sha[:12]} "
                    f"{outcome.verdict.verdict} check={outcome.check_run_id} {status}"
                )
                for failure in outcome.failures:
                    click.echo(f"  ! {failure}", err=True)
    except (GitHubError, LedgerError, FileNotFoundError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    finally:
        github.close()
    return reviewed


@command.command("once")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Default: ~/.config/red-rail/reviewer.yaml",
)
@click.option("--repository", "only", default=None, help="owner/name — only this repository.")
@click.option(
    "--pr",
    type=int,
    default=None,
    help="Review this PR even if already reviewed (needs --repository).",
)
def once(config_path: Path | None, only: str | None, pr: int | None) -> None:
    """One pass over the watched repositories; exit 0."""
    if pr is not None and not only:
        raise click.UsageError("--pr needs --repository")
    try:
        config = _config(config_path)
    except (PrivateFileError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    reviewed = _once(config, only=only, pr=pr)
    click.echo(f"reviewed {reviewed} pull request(s)")


@command.command("run")
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
def run(config_path: Path | None) -> None:
    """Poll forever (Ctrl-C to stop) — the host service, never CI."""
    try:
        config = _config(config_path)
    except (PrivateFileError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    while True:
        _once(config, only=None, pr=None)
        time.sleep(config.poll_seconds)
