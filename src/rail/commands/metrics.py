"""`rail metrics`: DORA + conformance for one repository, from its ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.ledger import LedgerError, open_ledger
from rail.metrics import compute_metrics
from rail.model import load_rail_config


def _fmt(value: float | None, unit: str) -> str:
    return "n/a" if value is None else f"{value:g} {unit}".rstrip()


@click.command("metrics")
@repo_option
@click.option(
    "--window", type=click.IntRange(min=1), default=30, show_default=True, help="Window in days."
)
@click.option("--ci", is_flag=True, help="Skip workstation-only gates in the conformance score.")
@json_option
def command(repo: Path, window: int, ci: bool, as_json: bool) -> None:
    """Lead time, deployment frequency, change failure rate, recovery time, conformance."""
    try:
        cfg = load_rail_config(repo)
        ledger = open_ledger(repo)
        metrics = compute_metrics(
            ledger, cfg.project, repo, now=datetime.now(UTC), window_days=window, ci=ci
        )
    except (FileNotFoundError, ValidationError, LedgerError) as exc:
        if as_json:  # a machine consumer gets JSON even on an error
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    if as_json:
        click.echo(json.dumps(metrics.to_dict(), indent=2))
        return
    c = metrics.conformance
    click.echo(f"{metrics.project}  window {window}d since {metrics.since}")
    click.echo(
        f"deployments                 {metrics.deployments} "
        f"({metrics.deployment_frequency_per_week:g}/week)"
    )
    click.echo(f"lead time commit → deploy   {_fmt(metrics.lead_time_commit_to_deploy_hours, 'h')}")
    click.echo(
        f"lead time contract → deploy {_fmt(metrics.lead_time_contract_to_deploy_hours, 'h')}"
    )
    click.echo(f"change failure rate         {_fmt(metrics.change_failure_rate, '')}")
    click.echo(f"recovery time               {_fmt(metrics.recovery_time_hours, 'h')}")
    click.echo(f"drill recovery time         {_fmt(metrics.drill_recovery_time_minutes, 'min')}")
    click.echo(
        f"conformance                 {c.passed}/{c.applicable} "
        f"({c.exceptions} declared exception(s))"
    )
