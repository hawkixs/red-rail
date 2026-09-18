"""`rail audit [PATHS...]`: the matrix over projects (default: the parent directory)."""

from __future__ import annotations

import json
from pathlib import Path

import click

from rail.audit import audit_paths, matrix, render_table, to_json
from rail.commands._options import json_option


@click.command("audit")
@click.argument("paths", nargs=-1, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--ci", is_flag=True, help="Skip workstation-only gates (remotes, roster).")
@click.option(
    "--matrix", "as_matrix", is_flag=True, help="Only statuses and scores (the golden shape)."
)
@json_option
def command(paths: tuple[Path, ...], ci: bool, as_matrix: bool, as_json: bool) -> None:
    """Score every project against its declared tier. A report: the exit code is always 0."""
    targets = list(paths) or [Path.cwd().parent]
    audits = audit_paths(targets, ci=ci)
    if not audits:
        raise click.UsageError("no project found (a project has a .git or a rail.yaml)")
    if as_matrix:
        click.echo(json.dumps(matrix(audits), indent=2, sort_keys=True))
    elif as_json:
        click.echo(json.dumps(to_json(audits), indent=2))
    else:
        click.echo(render_table(audits))
