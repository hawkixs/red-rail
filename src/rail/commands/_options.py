"""Options shared by the commands."""

from __future__ import annotations

from pathlib import Path

import click

repo_option = click.option(
    "--repo",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Repository to work on.",
)
json_option = click.option(
    "--json", "as_json", is_flag=True, help="Emit a machine-readable report."
)
