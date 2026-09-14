"""`rail` command line. The exit code is the verdict; `--json` is the contract for machines."""

from __future__ import annotations

import json
from pathlib import Path

import click

from rail import __version__
from rail.gates import run_gates


@click.group()
@click.version_option(__version__, prog_name="rail")
def main() -> None:
    """The ReD delivery rail."""


@main.command()
@click.option(
    "--repo",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Repository to check.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a machine-readable report.")
def check(repo: Path, as_json: bool) -> None:
    """Run every gate against a repository and exit non-zero if one fails."""
    results = run_gates(repo)
    passed = all(r.passed for r in results)
    if as_json:
        report = {"repo": str(repo), "passed": passed, "gates": [r.to_dict() for r in results]}
        click.echo(json.dumps(report, indent=2))
    else:
        for r in results:
            verdict = "PASS" if r.passed else "FAIL"
            click.echo(f"{verdict}  {r.stage.value}.{r.code:<14} {r.details}")
    raise SystemExit(0 if passed else 1)
