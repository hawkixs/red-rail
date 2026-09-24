"""`rail upgrade`: re-apply the template's latest tag to a scaffolded project (`copier update`)."""

from __future__ import annotations

from pathlib import Path

import click

from rail.commands._options import repo_option
from rail.model import Stack
from rail.scaffold import ScaffoldError, upgrade


@click.command("upgrade")
@repo_option
@click.option(
    "--stack",
    type=click.Choice([s.value for s in Stack if s is not Stack.DOCS]),
    default=None,
    help="Leave stack docs for this stack (the only switch the template supports).",
)
def command(repo: Path, stack: str | None) -> None:
    """Resorb template drift: bring the repository to the template's latest version."""
    try:
        version = upgrade(repo, stack=Stack(stack) if stack else None)
    except ScaffoldError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    if stack:
        click.echo(
            f"{repo}: template {version}, stack {stack}; run `make sync`, review the diff, "
            "then commit it"
        )
    else:
        click.echo(f"{repo}: template {version}; review the diff, then commit it")
