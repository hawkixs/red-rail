"""`rail drill`: the rollback drill (spec §6 step 8) — incident simulated on the live
artefact, rollback to the previous one, recovery measured, roll-forward; every record
marked `drill` so the change failure rate stays clean."""

from __future__ import annotations

from pathlib import Path

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.commands.deploy import report
from rail.deploy import DeployError, Locked, flow
from rail.ledger import LedgerError, open_ledger
from rail.model import load_rail_config


@click.command("drill")
@repo_option
@click.option("--yes", is_flag=True, help="Skip the confirmation.")
@click.option("--issuer", default="operator", show_default=True)
@json_option
def command(repo: Path, yes: bool, issuer: str, as_json: bool) -> None:
    """Roll back to the previous artefact, measure the recovery, roll forward — a drill."""
    try:
        cfg = load_rail_config(repo)
        ledger = open_ledger(repo)
        target = flow.make_target(repo, cfg)
        live = flow.live_artefact(ledger, cfg.project)
        previous = flow.previous_artefact(ledger, cfg.project, live.digest) if live else None
        if live is None or previous is None:
            raise DeployError("a drill needs two deployed artefacts: deploy a second release first")
        if not yes:
            click.confirm(
                f"drill {cfg.project}: {live.version} → {previous.version} → {live.version} "
                f"on {target.domain}?",
                abort=True,
            )
        outcome = flow.drill(repo, cfg, ledger, issuer=issuer, target=target)
    except Locked as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(3) from exc
    except (FileNotFoundError, ValidationError, LedgerError, DeployError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    report(outcome, as_json)
