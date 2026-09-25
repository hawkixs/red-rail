"""`rail reviewer`: the independent reviewer, on the host, in pull mode (ADR-0003)."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import click

from rail.ledger import open_ledger
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
    """One pass over the watched repositories; exit 0, or 2 for a repository it does not watch."""
    if pr is not None and not only:
        raise click.UsageError("--pr needs --repository")
    try:
        config = _config(config_path)
    except (PrivateFileError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    watched = [repository.slug for repository in config.repositories]
    if only and only not in watched:
        from rail.reviewer.config import DEFAULT_CONFIG

        raise click.UsageError(
            f"{only} is not watched by {config_path or DEFAULT_CONFIG}; "
            f"watched: {', '.join(watched) or 'none'}"
        )
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


def _interactive() -> bool:
    return sys.stdin.isatty()


@dataclass(frozen=True)
class _PR:
    """A minimal stand-in for `PullRequest`: `loop_state`'s filters read only these two."""

    repository: str
    number: int


@command.command("rule")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Default: ~/.config/red-rail/reviewer.yaml",
)
@click.option("--repository", required=True, help="owner/name, as reviewer.yaml watches it.")
@click.option("--pr", type=int, required=True)
@click.option("--finding", required=True, help="The open blocker's id, e.g. F-50-2.")
@click.option("--as", "ruling", type=click.Choice(["fix", "carry-forward"]), required=True)
@click.option("--decision", required=True, help="Your decision, the text the judge verifies.")
def rule(
    config_path: Path | None, repository: str, pr: int, finding: str, ruling: str, decision: str
) -> None:
    """Rule on one open blocker after round 3 (spec 2026-09-25, D9). Interactive, host only."""
    from rail.contract_guard import Unwritable, refuse_unwritable
    from rail.ledger import AttestationKind, Unattested
    from rail.model import load_rail_config
    from rail.reviewer import rounds
    from rail.reviewer.service import previous_verdicts, rulings_of

    if os.environ.get("CI"):
        raise click.UsageError("a ruling is the operator's gesture: refused under CI")
    if not _interactive():
        raise click.UsageError("a ruling needs a terminal: it asks you to type the finding id")
    text = decision.strip()
    if not text or len(text) > 2000:
        raise click.UsageError("--decision must hold 1 to 2000 characters")
    try:
        refuse_unwritable([text])
    except Unwritable as exc:
        raise click.UsageError(str(exc)) from exc
    try:
        config = _config(config_path)
    except (PrivateFileError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    watched = {r.slug: r for r in config.repositories}
    if repository not in watched:
        raise click.UsageError(f"{repository} is not watched by the reviewer's configuration")
    repo_path = watched[repository].path
    project = load_rail_config(repo_path).project
    ledger = open_ledger(repo_path)
    target = _PR(repository=repository, number=pr)
    state = rounds.loop_state(
        previous_verdicts(ledger, project, target), rulings_of(ledger, project, target)
    )
    waiting = {f.id: f for f in rounds.unruled(state)} if state.awaiting else {}
    if finding not in waiting:
        raise click.UsageError(
            f"{finding} is not an open blocker awaiting a ruling on {repository}#{pr}"
        )
    f = waiting[finding]
    click.echo(f"{finding} [{f.file}{':' + str(f.line) if f.line else ''}] {f.title}\n{f.evidence}")
    if click.prompt("Type the finding id to confirm", default="", show_default=False) != finding:
        raise click.UsageError("confirmation did not match: nothing written")
    earlier = [r for r in rulings_of(ledger, project, target) if r.data.get("finding") == finding]
    data = {
        "repository": repository,
        "pr": pr,
        "finding": finding,
        "ruling": ruling.replace("-", "_"),
        "decision": text,
    }
    key = f"review_ruling:{repository}#{pr}:{finding}:{len(earlier) + 1}"
    try:
        ledger.attest(
            project, AttestationKind.REVIEW_RULING, data, issuer="operator", idempotency_key=key
        )
    except Unattested as exc:
        click.echo(
            f"unattested ({exc.cause}): replay with rail attest review_ruling --from {exc.receipt}",
            err=True,
        )
        raise SystemExit(2) from exc
    click.echo(
        f"ruled {finding}: {ruling}; the next review pass is the closure check once every "
        "open blocker has a ruling"
    )
