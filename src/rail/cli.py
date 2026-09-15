"""`rail` command line. The exit code is the verdict; `--json` is the contract for machines.

Every `rail.commands.<name>` module (not starting with `_`) exports `command`, a Click
command registered here — adding a command never edits this file.
"""

from __future__ import annotations

import importlib
import pkgutil

import click

from rail import __version__, commands


@click.group()
@click.version_option(__version__, prog_name="rail")
def main() -> None:
    """The ReD delivery rail."""


for _module in pkgutil.iter_modules(commands.__path__):
    if not _module.name.startswith("_"):
        main.add_command(importlib.import_module(f"rail.commands.{_module.name}").command)
