"""`rail upgrade`: re-apply the template's latest tag to a scaffolded project (`copier update`)."""

from __future__ import annotations

from pathlib import Path

import click

from rail.commands._options import repo_option
from rail.scaffold import ScaffoldError, upgrade


@click.command("upgrade")
@repo_option
def command(repo: Path) -> None:
    """Resorb template drift: bring the repository to the template's latest version."""
    try:
        version = upgrade(repo)
    except ScaffoldError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    click.echo(f"{repo}: template {version}; review the diff, then commit it")
