"""`rail drill`: the rollback drill (spec §6 step 8) — incident simulated on the live
artefact, rollback to the previous one, recovery measured, roll-forward; every record
marked `drill` so the change failure rate stays clean."""

from __future__ import annotations

import json
from pathlib import Path

import click
from pydantic import ValidationError

from rail.commands._actor import no_issuer_option, resolve_or_exit
from rail.commands._options import json_option, repo_option
from rail.commands.deploy import report
from rail.deploy import DeployError, Locked, flow
from rail.deploy import restore as restore_flow
from rail.ledger import LedgerError, open_ledger
from rail.model import load_rail_config


@click.command("drill")
@repo_option
@click.option("--restore", is_flag=True, help="Restore a backup in a disposable database.")
@click.option("--backup", help="Absolute backup path on the target (never read on this host).")
@click.option("--backup-sha256", help="Expected SHA-256 of the backup, as 64 hex characters.")
@click.option(
    "--backup-schema-head", help="Backup schema head (otherwise read its configured file)."
)
@click.option("--yes", is_flag=True, help="Skip the confirmation.")
@no_issuer_option
@json_option
def command(
    repo: Path,
    restore: bool,
    backup: str | None,
    backup_sha256: str | None,
    backup_schema_head: str | None,
    yes: bool,
    as_json: bool,
) -> None:
    """Drill a rollback or an isolated backup restore against the previous artefact."""
    if restore and (backup is None or backup_sha256 is None):
        raise click.UsageError("--restore requires --backup and --backup-sha256")
    backup_options = (backup, backup_sha256, backup_schema_head)
    if not restore and any(value is not None for value in backup_options):
        raise click.UsageError("backup options require --restore")
    issuer = resolve_or_exit()
    try:
        cfg = load_rail_config(repo)
        if restore:
            restore_flow.check_config(cfg)
        ledger = open_ledger(repo)
        target = flow.make_target(repo, cfg)
        live = flow.live_artefact(ledger, cfg.project)
        previous = flow.previous_artefact(ledger, cfg.project, live.digest) if live else None
        if live is None or previous is None:
            raise DeployError("a drill needs two deployed artefacts: deploy a second release first")
        if not yes:
            action = (
                f"restore drill {cfg.project}: isolated backup read with {previous.version} "
                f"(live {live.version})"
                if restore
                else f"drill {cfg.project}: {live.version} → {previous.version} → {live.version}"
            )
            click.confirm(
                f"{action} on {target.domain}?",
                abort=True,
            )
        if restore:
            assert backup is not None and backup_sha256 is not None
            outcome = restore_flow.drill(
                repo,
                cfg,
                ledger,
                issuer=issuer,
                target=target,
                backup=backup,
                backup_sha256=backup_sha256,
                backup_head=backup_schema_head,
            )
        else:
            outcome = flow.drill(repo, cfg, ledger, issuer=issuer, target=target)
    except flow.DrillRefused as exc:
        click.echo(json.dumps(exc.data) if as_json else str(exc), err=not as_json)
        raise SystemExit(1) from exc
    except Locked as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(3) from exc
    except (FileNotFoundError, ValidationError, LedgerError, DeployError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    report(outcome, as_json)
