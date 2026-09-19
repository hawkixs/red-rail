"""Where the rail finds brain-v42: loopback URL and the operator's private bearer token."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from rail.private import private_token

DEFAULT_URL = "http://127.0.0.1:8765/mcp"
DEFAULT_TOKEN_FILE = "~/.config/red-rail/brain-token"  # the rail's own private file
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback(url: str) -> bool:
    return urlsplit(url).hostname in LOOPBACK


@dataclass(frozen=True, slots=True)
class BrainSettings:
    """The bearer is read from a private file and from nowhere else (reference client's
    rule): `RAIL_BRAIN_TOKEN_FILE` names the path, the environment never holds the value."""

    url: str
    token: str
    token_file: Path

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> BrainSettings:
        env = os.environ if environ is None else environ
        url = env.get("RAIL_BRAIN_URL", DEFAULT_URL)
        path = Path(env.get("RAIL_BRAIN_TOKEN_FILE", DEFAULT_TOKEN_FILE)).expanduser()
        return cls(url=url, token=private_token(path), token_file=path)
