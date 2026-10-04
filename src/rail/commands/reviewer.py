"""`rail reviewer`: the independent reviewer, on the host, in pull mode (ADR-0003)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import click

from rail.commands._actor import resolve_or_exit
from rail.ledger import open_ledger
from rail.private import PrivateFileError


@click.group("reviewer")
def command() -> None:
    """The independent PR reviewer (GitHub App red-rail-reviewer)."""


def _config(path: Path | None):
    from rail.reviewer.config import DEFAULT_CONFIG, load_reviewer_config

    return load_reviewer_config(path or DEFAULT_CONFIG)


def _merged_ignored_globs(repo: Path, working: tuple[str, ...]) -> tuple[str, ...]:
    """The `review.ignored_globs` of the rail.yaml committed on origin/main (ticket f0aa9c29).

    The checkout is a working tree: an uncommitted rail.yaml, or another branch, was never
    judged, yet a glob hides code from every judge. Only what was merged counts; nothing
    applies when origin/main cannot be read. A working tree that declares something else is
    reported, never applied (found by the adversarial review)."""
    import yaml
    from pydantic import ValidationError

    from rail.gitrepo import file_at
    from rail.model import IGNORED_GLOBS, MANIFEST_NAME, RailConfig

    merged: tuple[str, ...] = ()
    # fully qualified: a bare `origin/main` resolves to a local branch of that name first, and
    # git only warns on stderr (found by the adversarial review). The ref is what the last
    # fetch brought from GitHub; a stale one lags, it never adds a glob no judge saw.
    text = file_at(repo, "refs/remotes/origin/main", MANIFEST_NAME)
    problem = None if text is not None else f"no {MANIFEST_NAME} readable on origin/main"
    if text is not None:
        try:
            declared = RailConfig.model_validate(yaml.safe_load(text) or {}).gates.get(
                IGNORED_GLOBS
            )
            merged = tuple(declared.value) if declared is not None else ()
        except (yaml.YAMLError, ValidationError) as exc:
            problem = f"the {MANIFEST_NAME} on origin/main is invalid ({type(exc).__name__})"
    if working != merged:
        click.echo(
            f"  ! {repo}: {IGNORED_GLOBS} {list(working)} in the working tree, "
            f"{list(merged)} applied from origin/main"
            + (f" ({problem})" if problem else "")
            + ": only a merged rail.yaml hides files from the judges (fetch, or merge first)",
            err=True,
        )
    return merged


def _once(config, *, only: str | None, pr: int | None) -> int:
    from rail.ledger import LedgerError
    from rail.model import IGNORED_GLOBS, MANIFEST_NAME, load_rail_config
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
            # The manifest chooses the project and the ledger the verdict is attested in, so it
            # is read from the checkout declared here, never from a pull request's head. The
            # onboarding pull request adds it: name the procedure (ticket 98d8f8a8).
            if not (repository.path / MANIFEST_NAME).is_file():
                raise FileNotFoundError(
                    f"{repository.slug}: no {MANIFEST_NAME} in {repository.path}, the checkout "
                    "reviewer.yaml declares. For the onboarding pull request that adds it, "
                    "point this repository's `path` in reviewer.yaml at the branch's worktree "
                    "for the length of the pull request, then back to a checkout of main once "
                    "it is merged."
                )
            cfg = load_rail_config(repository.path)
            ledger = open_ledger(repository.path)
            # the generated files this repository declares no judge reads, ADDED to the host's
            # list (ticket f0aa9c29), as merged on origin/main, never as the working tree says
            policy = config.policy
            declared = cfg.gates.get(IGNORED_GLOBS)
            merged = _merged_ignored_globs(
                repository.path, tuple(declared.value) if declared is not None else ()
            )
            if merged:
                policy = policy.model_copy(
                    update={"ignored_globs": (*policy.ignored_globs, *merged)}
                )
            pulls = (
                [github.pull(repository.slug, pr)]
                if pr
                else pending_reviews(github, repository.slug, policy)
            )
            for pull in pulls:
                outcome = review_pull(
                    pull,
                    github=github,
                    policy=policy,
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
    """Rule on one open blocker after round 3 (spec 2026-09-25, D9). Operator only, host only."""
    from rail.actor import stdin_is_tty
    from rail.contract_guard import Unwritable, refuse_unwritable
    from rail.ledger import AttestationKind, Unattested
    from rail.model import load_rail_config
    from rail.reviewer import rounds
    from rail.reviewer.service import previous_verdicts, rulings_of

    if os.environ.get("CI"):
        raise click.UsageError("a ruling is the operator's gesture: refused under CI")
    actor = resolve_or_exit()
    if actor != "operator":
        raise click.UsageError(f"a ruling is the operator's gesture, not {actor}'s")
    if not stdin_is_tty():
        raise click.UsageError("a ruling is the operator's gesture, typed at a terminal")
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
            project, AttestationKind.REVIEW_RULING, data, issuer=actor, idempotency_key=key
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
