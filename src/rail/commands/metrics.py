"""`rail metrics`: DORA + conformance for one repository, from its ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import click
from pydantic import ValidationError
from yaml import YAMLError

from rail.commands._options import json_option, repo_option
from rail.ledger import LedgerError, open_ledger
from rail.metrics import compute_metrics
from rail.metrics_contract import MAX_BYTES
from rail.model import load_rail_config


def _fmt(value: float | None, unit: str) -> str:
    return "n/a" if value is None else f"{value:g} {unit}".rstrip()


@click.group("metrics", invoke_without_command=True)
@repo_option
@click.option(
    "--window", type=click.IntRange(min=1), default=30, show_default=True, help="Window in days."
)
@click.option("--ci", is_flag=True, help="Skip workstation-only gates in the conformance score.")
@json_option
@click.pass_context
def command(ctx: click.Context, repo: Path, window: int, ci: bool, as_json: bool) -> None:
    """Lead time, deployment frequency, change failure rate, recovery time, conformance."""
    if ctx.invoked_subcommand is not None:
        return
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


@command.command("publish")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Default: ~/.config/red-rail/reviewer.yaml",
)
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Default: $XDG_STATE_HOME/red-rail/metrics.json (or ~/.local/state/red-rail/metrics.json)."
    ),
)
@click.option(
    "--max-bytes",
    type=click.IntRange(1, MAX_BYTES),
    default=16 * 1024,
    show_default=True,
    help="Consumer budget; above 16 KiB requires confirmed relay upgrades before release.",
)
@click.pass_context
def publish(ctx: click.Context, config_path: Path | None, out: Path | None, max_bytes: int) -> None:
    """Publish delivery and autonomy metrics from the reviewer's read-only sources."""
    import jwt

    from rail import publish as publication
    from rail.private import PrivateFileError
    from rail.reviewer.config import DEFAULT_CONFIG, load_reviewer_config
    from rail.reviewer.github import GitHubApp, GitHubError

    try:
        config = load_reviewer_config(config_path or DEFAULT_CONFIG)
        github = GitHubApp(
            app_id=config.app_id,
            installation_id=config.installation_id,
            private_key_pem=config.private_key_pem(),
        )
        try:
            answer = publication.collect(
                config,
                now=datetime.now(UTC),
                clock=lambda: datetime.now(UTC),
                open_ledger=open_ledger,
                github=github,
                window_days=ctx.parent.params["window"],
            )
        finally:
            github.close()
        destination = out or publication.default_output()
        size = publication.write(answer, destination, max_bytes=max_bytes)
    except (
        ValueError,
        OSError,
        LedgerError,
        GitHubError,
        PrivateFileError,
        jwt.PyJWTError,
        YAMLError,
    ) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    if answer.autonomy is not None:
        coverage = answer.autonomy.coverage
        read, expected = coverage.read, coverage.expected
    else:
        assert answer.flow is not None
        read = len(answer.flow.repositories)
        expected = read + len(answer.flow.coverage.missing)
    click.echo(f"{destination} {size} bytes {read}/{expected} read")


@command.command("serve")
@click.option(
    "--file",
    "file_path",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Default: $XDG_STATE_HOME/red-rail/metrics.json (or ~/.local/state/red-rail/metrics.json)."
    ),
)
@click.option("--port", type=click.IntRange(0, 65535), default=9230, show_default=True)
@click.option(
    "--max-bytes",
    type=click.IntRange(1, MAX_BYTES),
    default=16 * 1024,
    show_default=True,
    help="Consumer budget; above 16 KiB requires confirmed relay upgrades before release.",
)
def serve(file_path: Path | None, port: int, max_bytes: int) -> None:
    """Serve strictly validated metrics on 127.0.0.1 only."""
    from rail import publish as publication
    from rail.serve import create_server

    if file_path is None:
        file_path = publication.default_output()
    try:
        with create_server(file_path, port=port, max_bytes=max_bytes) as server:
            server.serve_forever()
    except OSError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
