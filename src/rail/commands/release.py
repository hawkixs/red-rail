"""`rail release --version X.Y.Z`: stage 7 from the host. `--plan` prints the steps and
touches nothing; under `ledger: file` the receipt lands in docs/receipts/ and travels in a
dedicated receipts PR (decision c8b0ea45)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import click
import yaml
from pydantic import ValidationError

from rail import gitrepo
from rail.commands._actor import no_issuer_option, resolve_or_exit
from rail.commands._options import json_option, repo_option
from rail.commands.attest import echo_record
from rail.ledger import RECEIPTS_DIR, LedgerError, Unattested, open_ledger, sweep
from rail.ledger.file import receipt_filename
from rail.model import LedgerBackend, load_rail_config
from rail.private import PrivateFileError
from rail.release import ReleaseError, attest, build_and_push, login, preflight, tag_and_push

RUN = subprocess.run  # module-level so a test can inject a fake host


def _reviewer_checkout(repo: Path) -> tuple[Path | None, str | None]:
    """Resolve origin's reviewer seat without exposing private configuration or host paths."""
    origin = gitrepo.remotes(repo).get("origin", "")
    if gitrepo.url_host(origin) != "github.com":
        return None, "origin has no GitHub slug"
    try:
        path = urlsplit(origin).path if "://" in origin else origin.split(":", 1)[1]
    except (ValueError, IndexError):
        return None, "origin has no GitHub slug"
    slug = path.strip("/").removesuffix(".git")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", slug):
        return None, "origin has no GitHub slug"
    try:
        from rail.reviewer.config import load_reviewer_config

        config = load_reviewer_config()
    except ImportError:
        return None, "reviewer configuration loader is unavailable"
    except (PrivateFileError, OSError, ValueError, yaml.YAMLError, AttributeError, TypeError):
        return None, "reviewer configuration is absent or unreadable"
    entry = next((entry for entry in config.repositories if entry.slug == slug), None)
    if entry is None:
        return None, "no reviewer checkout configured for origin"
    try:
        if entry.path.resolve() == repo.resolve():
            return None, "reviewer checkout is this release checkout"
    except (OSError, RuntimeError):
        return None, "reviewer checkout is unavailable"
    return entry.path, None


def _sweep(repo: Path, *, dry_run: bool, as_json: bool) -> dict[str, Any]:
    """A sweep cannot fail an already published release; every diagnostic stays path-free."""
    source, reason = _reviewer_checkout(repo)
    report = sweep.SweepReport()
    if source is not None:
        try:
            report = sweep.sweep_receipts(source, repo, dry_run=dry_run)
        except OSError:
            reason = "reviewer receipts are unavailable"
    if reason:
        click.echo(f"reviewer receipts: nothing to sweep ({reason})", err=as_json)
    elif not as_json:
        action = "would be swept" if dry_run else "swept"
        click.echo(
            f"{len(report.copied)} reviewer receipt(s) {action} from {source.name} "
            f"into docs/receipts ({len(report.already_present)} already present, "
            f"{len(report.refused)} refused, {len(report.conflicts)} conflicts); "
            "commit them with the released receipt in the receipts PR (decision c8b0ea45)"
        )
    for name in report.refused:
        click.echo(f"refused: {name}", err=True)
    for name in report.conflicts:
        click.echo(f"conflict: {name}", err=True)
    return {**report.as_dict(), "source": source.name if source else None, "reason": reason}


@click.command("release")
@repo_option
@click.option("--version", "version", required=True, help="Semantic version X.Y.Z (tag vX.Y.Z).")
@no_issuer_option
@click.option("--plan", "dry_run", is_flag=True, help="Print the steps, run nothing.")
@click.option("--yes", is_flag=True, help="Skip the confirmation.")
@json_option
def command(repo: Path, version: str, dry_run: bool, yes: bool, as_json: bool) -> None:
    """Tag, build and push the image, attest `released` (prod, from main, after integration)."""
    # before preflight: it fetches, reads the remote and reads the ledger; --plan attests nothing
    issuer = None if dry_run else resolve_or_exit()
    try:
        config = load_rail_config(repo)
        ledger = open_ledger(repo)
        plan = preflight(repo, version, ledger=ledger, run=RUN)
    except (FileNotFoundError, ValidationError, LedgerError, ReleaseError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    if dry_run:
        if as_json and config.ledger is LedgerBackend.FILE:
            click.echo(
                json.dumps(
                    {
                        "project": plan.project,
                        "version": plan.version,
                        "sha": plan.sha,
                        "image_tag": plan.image_tag,
                        "steps": plan.steps(),
                        "sweep": _sweep(repo, dry_run=True, as_json=True),
                    },
                    indent=2,
                )
            )
            return
        click.echo(f"{plan.project} {plan.version} from {plan.sha[:12]} → {plan.image_tag}")
        for step in plan.steps():
            click.echo(f"  {step}")
        if config.ledger is LedgerBackend.FILE:
            _sweep(repo, dry_run=True, as_json=False)
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
    if config.ledger is LedgerBackend.FILE:
        if not as_json:
            echo_record(record, repo, False)
        report = _sweep(repo, dry_run=False, as_json=as_json)
        if as_json:
            click.echo(json.dumps({**record.model_dump(mode="json"), "sweep": report}, indent=2))
        elif report["reason"] and (repo / RECEIPTS_DIR / receipt_filename(record)).is_file():
            click.echo("commit the receipt in a dedicated receipts PR (decision c8b0ea45)")
    else:
        echo_record(record, repo, as_json)
