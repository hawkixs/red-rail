"""`rail bind --pr N`: bind this repository's pull request to the delivery contract (spec §6
step 3), at the opening of the PR. The ledger's view is read first: a pull request already
bound is reported, never re-bound — brain does not replay a binding key."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.commands.attest import echo_record
from rail.commands.contract import canonical_slug
from rail.ledger import LedgerError, PullRequestRef, bindings_of, open_ledger
from rail.model import load_rail_config

RUN: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run  # injectable in tests
_SHA = re.compile(r"^[0-9a-f]{40}$")


def head_sha_of(slug: str, number: int) -> str:
    """The pull request's head as GitHub reports it, through the operator's `gh`."""
    args = ["gh", "api", f"repos/{slug}/pulls/{number}", "--jq", ".head.sha"]
    try:
        done = RUN(args, capture_output=True, text=True, check=False)
    except (FileNotFoundError, OSError) as exc:
        raise LedgerError(f"gh is not available on this host: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        raise LedgerError(f"gh api repos/{slug}/pulls/{number} failed: {detail}")
    sha = done.stdout.strip()
    if not _SHA.match(sha):
        raise LedgerError(f"gh api repos/{slug}/pulls/{number}: not a commit sha: {sha!r}")
    return sha


@click.command("bind")
@repo_option
@click.option("--pr", "number", type=click.IntRange(min=1), required=True, help="PR number.")
@click.option("--head", "head_sha", default=None, help="The PR head (default: asked to GitHub).")
@click.option("--issuer", default="operator", show_default=True)
@json_option
def command(repo: Path, number: int, head_sha: str | None, issuer: str, as_json: bool) -> None:
    """Bind pull request N of this repository to the delivery contract (at its opening)."""
    try:
        project = load_rail_config(repo).project
        slug = canonical_slug(repo)
        if slug is None:
            raise LedgerError("no GitHub remote: the pull request's repository is unknown")
        ledger = open_ledger(repo)
        bound = bindings_of(ledger, project, slug, number)
        if bound:
            click.echo(
                f"note: already bound: {slug}#{number} ({bound[-1].idempotency_key})", err=True
            )
            echo_record(bound[-1], repo, as_json)
            return
        sha = head_sha or head_sha_of(slug, number)
        pr = PullRequestRef(repository=slug, number=number, head_sha=sha)
        record = ledger.bind(
            project, pr, issuer=issuer, idempotency_key=f"bind:{slug}:{number}:{sha[:12]}"
        )
    except (FileNotFoundError, ValidationError, LedgerError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    echo_record(record, repo, as_json)
