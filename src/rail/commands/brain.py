"""`rail brain ping`: is brain-v42 reachable with the operator's token, as this project?"""

from __future__ import annotations

from pathlib import Path

import click

from rail.commands._options import repo_option
from rail.model import load_rail_config


@click.group("brain")
def command() -> None:
    """The shared ledger (brain-v42)."""


@command.command("ping")
@repo_option
@click.option("--agent", default="red-rail", show_default=True, help="X-Brain-Agent label.")
def ping(repo: Path, agent: str) -> None:
    """One read call; exit 0 when brain answers, 1 with the reason otherwise."""
    from rail.brain.client import BrainClient, BrainToolError, BrainUnreachable
    from rail.brain.settings import BrainSettings
    from rail.private import PrivateFileError

    try:
        cfg = load_rail_config(repo)
        settings = BrainSettings.from_environment()
        client = BrainClient.http(settings.url, token=settings.token, agent=agent)
        page = client.call("brain_delivery_list", {"actor_project": cfg.project, "limit": 1})
    except (PrivateFileError, BrainUnreachable, BrainToolError, OSError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    total = len(page.get("items", [])) + int(page.get("omitted_count", 0))
    click.echo(
        f"brain-v42 reachable at {settings.url} as {agent}: "
        f"{total} delivery view(s) for {cfg.project}"
    )
