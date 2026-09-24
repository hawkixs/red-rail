"""`rail new SLUG`: the intent stage in one command (spec §6, step 1)."""

from __future__ import annotations

import re
from pathlib import Path

import click

from rail.gates.hygiene import DOMAIN_PLACEHOLDER, roster_row
from rail.ledger import LedgerError
from rail.model import DeployTarget, LedgerBackend, Stack, Tier
from rail.policy import stages_for
from rail.remotes import RemoteError
from rail.scaffold import TEMPLATE_SOURCE, NewProject, ScaffoldError, new_project

SLUG = re.compile(r"^red-[a-z0-9]+(-[a-z0-9]+)*$")
# Every key of the root roster fits, `auto_discord` included. It is rendered into
# `brain_session_start("<key>", …)` and checked there by one regex (decision 8).
BRAIN_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _slug(ctx: click.Context, param: click.Parameter, value: str) -> str:
    if not SLUG.match(value):
        raise click.BadParameter("must match red-<kebab-case>")
    return value


def _brain_key(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    if value is not None and not BRAIN_KEY.fullmatch(value):
        raise click.BadParameter(
            "must match [a-z0-9][a-z0-9_-]*: lowercase letters, digits, '-' and '_'"
        )
    return value


@click.command("new")
@click.argument("slug", callback=_slug)
@click.option("--description", required=True, help="One sentence — what the project does.")
@click.option(
    "--tier", type=click.Choice([t.value for t in Tier]), default="bootstrap", show_default=True
)
@click.option(
    "--stack", type=click.Choice([s.value for s in Stack]), default="python", show_default=True
)
@click.option(
    "--brain-key",
    default=None,
    callback=_brain_key,
    help="brain-v42 project key (default: the slug).",
)
@click.option(
    "--deploy-target",
    type=click.Choice([d.value for d in DeployTarget]),
    default="vps-traefik",
    show_default=True,
)
@click.option(
    "--healthcheck", default=None, help="prod only (default: https://<name>.hawkixs.com/healthz)."
)
@click.option(
    "--dest", type=click.Path(path_type=Path), default=None, help="Destination (default: ./SLUG)."
)
@click.option(
    "--template",
    default=TEMPLATE_SOURCE,
    show_default=True,
    help="copier source: git URL or directory.",
)
@click.option("--template-ref", default=None, help="red-rail tag to pin (git sources only).")
@click.option(
    "--rail-ref",
    default=None,
    help=(
        "Ref the generated CI calls the reusable workflow at (default: resolve the current "
        "commit of the template's main). Pass a tag or SHA to pin deliberately."
    ),
)
@click.option(
    "--remotes/--no-remotes",
    "publish",
    default=True,
    show_default=True,
    help="Create the GitHub repository with gh and push main (a declared mirror: glab too).",
)
@click.option(
    "--ledger",
    type=click.Choice(["file", "brain"]),
    default="file",
    show_default=True,
    help="Evidence authority: the repository's receipts, or brain-v42 (shared, observed).",
)
@click.option(
    "--ticket",
    default=None,
    help="brain-v42 delivery ticket UUID — required with --ledger brain.",
)
def command(
    slug: str,
    description: str,
    tier: str,
    stack: str,
    brain_key: str | None,
    deploy_target: str,
    healthcheck: str | None,
    dest: Path | None,
    template: str,
    template_ref: str | None,
    rail_ref: str | None,
    publish: bool,
    ledger: str,
    ticket: str | None,
) -> None:
    """Scaffold a ReD project: tree, contract, bootstrap spec, first commit, gates, remotes."""
    if ledger == "brain" and not ticket:
        raise click.UsageError("--ledger brain needs --ticket <uuid>")
    if ledger == "file" and ticket:
        raise click.UsageError("--ticket is only meaningful with --ledger brain")
    project = NewProject(
        slug=slug,
        description=description,
        tier=Tier(tier),
        stack=Stack(stack),
        brain_key=brain_key or slug,
        dest=(dest or Path.cwd() / slug),
        template=template,
        template_ref=template_ref,
        rail_ref=rail_ref,
        deploy_target=deploy_target,
        healthcheck=healthcheck,
        ledger=LedgerBackend(ledger),
        ticket=ticket,
    )
    try:
        results = new_project(project, publish=publish)
    except ScaffoldError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    except RemoteError as exc:
        click.echo(f"error: {exc}\nthe local tree is intact under {project.dest}", err=True)
        raise SystemExit(1) from exc
    except LedgerError as exc:
        # the file ledger records the contract before anything is published; a brain refusal
        # arrives as a ScaffoldError that carries the resume steps (b185c51d)
        click.echo(
            f"error: {exc}\nthe tree under {project.dest} is in place and nothing is published",
            err=True,
        )
        raise SystemExit(1) from exc
    click.echo(f"created {project.dest}")
    for r in results:
        click.echo(f"PASS  {r.gate_id:<22} {r.details}")
    if Tier(tier) is not Tier.BOOTSTRAP:
        remaining = [s.value for s in stages_for(Tier(tier)) if s not in stages_for(Tier.BOOTSTRAP)]
        click.echo(f"remaining stages of tier {tier}: {', '.join(remaining)}")
    click.echo("")
    click.echo(
        "Add this row to the ReD root roster (CLAUDE.md, the operator's gesture: the root is "
        f"not under git), with {DOMAIN_PLACEHOLDER} replaced by the project's domain:"
    )
    click.echo(roster_row(slug, description, project.brain_key))
    click.echo(
        "Until the row is in the root with its domain filled in, `rail check` fails "
        "hygiene.roster_entry. `rail new` cannot see the root: it checked this tree with the "
        "workstation gates skipped."
    )
