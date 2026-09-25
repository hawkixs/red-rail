"""Where an attestation waits for brain under `ledger: brain` (spec
2026-09-25-spool-replaces-committed-mirrors, decision 1): one directory per host and per
project, never inside a repository, holding only what brain has not recorded yet."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

SPOOL_VARIABLE = "RAIL_SPOOL_DIR"
DEFAULT_SPOOL = "~/.local/state/red-rail/spool"


def spool_directory(project: str, env: Mapping[str, str] | None = None) -> Path:
    """`$RAIL_SPOOL_DIR/<project>`, the root defaulting to `~/.local/state/red-rail/spool`."""
    source = os.environ if env is None else env
    return Path(source.get(SPOOL_VARIABLE) or DEFAULT_SPOOL).expanduser() / project
