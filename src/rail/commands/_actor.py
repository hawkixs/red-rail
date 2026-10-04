"""The actor of a gesture command, resolved before any side effect (spec 2026-10-03 §3)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click

from rail.actor import ActorRefused, current_actor
from rail.policy import ACTOR_VARIABLE


def _issuer_is_gone(ctx: click.Context, param: click.Parameter, value: Any) -> None:
    if value is not None:
        raise click.UsageError(
            f"--issuer is gone: the actor is resolved from the environment; "
            f"set {ACTOR_VARIABLE}=agent:<name> or {ACTOR_VARIABLE}=service:<name> to name it"
        )


def no_issuer_option(func: Callable[..., Any]) -> Callable[..., Any]:
    """`--issuer` stays declared, hidden, so that using it fails loudly instead of being
    taken for an unknown option or silently ignored."""
    return click.option(
        "--issuer",
        hidden=True,
        is_eager=True,
        expose_value=False,
        is_flag=False,
        flag_value="",
        callback=_issuer_is_gone,
    )(func)


def resolve_or_exit() -> str:
    """The actor's label, or exit 2 with the fix named. Call it first in a gesture."""
    try:
        return current_actor().label
    except ActorRefused as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(2) from exc
