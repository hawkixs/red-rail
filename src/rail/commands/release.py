"""`rail release --version X.Y.Z`: stage 7 from the host. `--plan` prints the steps and
touches nothing; the receipt lands in docs/receipts/ and travels in a dedicated receipts
PR (decision c8b0ea45)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.commands.attest import echo_record
from rail.ledger import LedgerError, Unattested, open_ledger
from rail.release import ReleaseError, attest, build_and_push, login, preflight, tag_and_push

RUN = subprocess.run  # module-level so a test can inject a fake host


@click.command("release")
@repo_option
@click.option("--version", "version", required=True, help="Semantic version X.Y.Z (tag vX.Y.Z).")
@click.option("--issuer", default="operator", show_default=True)
@click.option("--plan", "dry_run", is_flag=True, help="Print the steps, run nothing.")
@click.option("--yes", is_flag=True, help="Skip the confirmation.")
@json_option
def command(repo: Path, version: str, issuer: str, dry_run: bool, yes: bool, as_json: bool) -> None:
    """Tag, build and push the image, attest `released` (prod, from main, after integration)."""
    try:
        ledger = open_ledger(repo)
        plan = preflight(repo, version, ledger=ledger, run=RUN)
    except (FileNotFoundError, ValidationError, LedgerError, ReleaseError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    if dry_run:
        click.echo(f"{plan.project} {plan.version} from {plan.sha[:12]} → {plan.image_tag}")
        for step in plan.steps():
            click.echo(f"  {step}")
        return
    if not yes:
        click.confirm(
            f"release {plan.project} {plan.version} from {plan.sha[:12]} as {plan.image_tag}?",
            abort=True,
        )
    try:
        login(plan, run=RUN)
        digest = build_and_push(plan, repo, run=RUN)
    except ReleaseError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    try:
        tag_and_push(plan, repo, run=RUN)
    except ReleaseError as exc:
        click.echo(f"error: {exc}", err=True)
        click.echo(
            f"the image {plan.image_repository}@{digest} is published; re-run the release once "
            "the tag is settled (the build and the push are idempotent)",
            err=True,
        )
        raise SystemExit(1) from exc
    try:
        record = attest(ledger, plan, digest, issuer=issuer)
    except Unattested as exc:
        click.echo(f"error: {exc}", err=True)
        click.echo(
            f"the image {plan.image_repository}@{digest} and the tag {plan.tag} are published",
            err=True,
        )
        raise SystemExit(2) from exc
    except LedgerError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    echo_record(record, repo, as_json)
    if not as_json:
        click.echo("commit the receipt in a dedicated receipts PR (decision c8b0ea45)")
