"""`rail deploy`: stage 8 from the host (spec §6 step 7). Refuses without a `released`
attestation, asks for confirmation, applies the target, verifies through the public route,
attests. `--rollback` puts the previous artefact back; `--plan` prints and touches nothing.
Exit codes: 0 live and attested; 1 refused or failed (rolled back); 2 live but a record is
unattested (replay commands printed); 3 another deployment holds the lock."""

from __future__ import annotations

import json
from pathlib import Path

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.deploy import DeployError, Locked, flow
from rail.ledger import LedgerError, open_ledger
from rail.model import load_rail_config


def report(outcome: flow.Outcome, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(outcome.to_dict(), indent=2))
    else:
        for record in outcome.records:
            label = record.attestation.value if record.attestation else record.kind.value
            click.echo(
                f"{label:<18} {record.data.get('digest') or record.data.get('to_digest', '')}"
            )
        if outcome.live is not None:
            click.echo(
                f"live: {outcome.live.version} {outcome.live.git_sha[:12]} "
                f"{outcome.live.image_digest}"
            )
        if outcome.recovery_seconds is not None:
            click.echo(f"recovery: {outcome.recovery_seconds}s")
        if outcome.failed:
            click.echo(f"error: {outcome.failed}", err=True)
    for unattested in outcome.unattested:
        click.echo(f"error: {unattested}", err=True)
    if outcome.unattested:
        raise SystemExit(2)
    if outcome.failed:
        raise SystemExit(1)


@click.command("deploy")
@repo_option
@click.option(
    "--version", "version", default=None, help="A released version (default: the newest)."
)
@click.option("--rollback", is_flag=True, help="Put the previous deployed artefact back.")
@click.option("--plan", "dry_run", is_flag=True, help="Print the steps, run nothing.")
@click.option("--yes", is_flag=True, help="Skip the confirmation.")
@click.option("--issuer", default="operator", show_default=True)
@json_option
def command(
    repo: Path,
    version: str | None,
    rollback: bool,
    dry_run: bool,
    yes: bool,
    issuer: str,
    as_json: bool,
) -> None:
    """Deploy the newest release to the manifest's target, or roll back to the previous one."""
    try:
        cfg = load_rail_config(repo)
        ledger = open_ledger(repo)
        target = flow.make_target(repo, cfg)
        if rollback:
            live = flow.live_artefact(ledger, cfg.project)
            artefact = flow.previous_artefact(ledger, cfg.project, live.digest) if live else None
            if artefact is None:
                raise DeployError(
                    "nothing to roll back to: the ledger names no earlier deployed artefact"
                )
        else:
            artefact = flow.newest_release(ledger, cfg.project, version)
        if dry_run:
            steps = target.steps(artefact)  # computed first: a refusal prints no half plan
            click.echo(
                f"{cfg.project} {'rollback to' if rollback else 'deploy'} "
                f"{artefact.version} ({artefact.digest}) on {target.domain}"
            )
            for step in steps:
                click.echo(f"  {step.title}")
                click.echo(f"    $ {' '.join(step.argv)}")
            return
        if not yes:
            click.confirm(
                f"{'roll back' if rollback else 'deploy'} {cfg.project} to "
                f"{artefact.version} ({artefact.digest}) on {target.domain}?",
                abort=True,
            )
        outcome = (
            flow.rollback(repo, cfg, ledger, issuer=issuer, target=target)
            if rollback
            else flow.forward(repo, cfg, ledger, version=version, issuer=issuer, target=target)
        )
    except Locked as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(3) from exc
    except (FileNotFoundError, ValidationError, LedgerError, DeployError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    report(outcome, as_json)
