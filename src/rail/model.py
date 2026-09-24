"""The per-project manifest (`rail.yaml`).

Deliberately tiny: anything not declared here is a tier default versioned in red-rail,
which is what keeps twenty manifests from diverging.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MANIFEST_NAME = "rail.yaml"

# A site is a label the repository may carry; its address lives on the host that deploys
# (spec 2026-09-23-sites-on-the-host). No dot and no colon: no address, no domain fits.
SITE_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
# the rail's own variable: in a compose file (`${BIND_ADDRESS}:9204:9204`) and, behind a site,
# as the healthcheck's host
ADDRESS_TOKEN = "${BIND_ADDRESS}"
# match-at-start only: a token followed by `:9204@other.example` used to pass (`:` opened the
# allowed set) even though that is userinfo, not a port — the URL's real host is
# `other.example`. An optional port is now the only thing allowed between the token and the
# next path/query/fragment boundary or the end of the string.
_TOKEN_HOST = re.compile(rf"^https?://{re.escape(ADDRESS_TOKEN)}(?::\d+)?(?=[/?#]|\Z)")
# A unit file inside the repository: relative, no `.`/`..` segment, and a plain service name
# (no template, no timer). The file name is the unit's name for systemd and for the sudoers
# rule alike.
UNIT_NAME_PATTERN = r"[a-z0-9]+(?:-[a-z0-9]+)*\.service"
_UNIT_PATH = re.compile(rf"^(?:[A-Za-z0-9._-]+/)*{UNIT_NAME_PATTERN}$")
# The binary's path inside the released image: absolute and of safe characters, because it
# reaches the remote script.
_BINARY_PATH = re.compile(r"^(?:/[A-Za-z0-9._-]+)+$")


def _has_dot_segment(path: str) -> bool:
    return any(part in {".", ".."} for part in path.split("/"))


def token_is_the_host(url: str) -> bool:
    """The token is the URL's host and nothing else: a site's address may fill only that."""
    return _TOKEN_HOST.match(url) is not None


class Tier(StrEnum):
    """Maturity tier. Each tier includes the previous one; the audit scores against it."""

    BOOTSTRAP = "bootstrap"
    DEV = "dev"
    PROD = "prod"


class Stack(StrEnum):
    PYTHON = "python"
    GO = "go"
    DOCS = "docs"


class LedgerBackend(StrEnum):
    """Where evidence is authoritative: the repository's receipts, or brain-v42 (shared)."""

    FILE = "file"
    BRAIN = "brain"


class DeployTarget(StrEnum):
    VPS_TRAEFIK = "vps-traefik"
    PRIVATE_COMPOSE = "private-compose"
    PRIVATE_SYSTEMD = "private-systemd"


PRIVATE_TARGETS = frozenset({DeployTarget.PRIVATE_COMPOSE, DeployTarget.PRIVATE_SYSTEMD})


class DeployConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: DeployTarget
    healthcheck: str = Field(pattern=r"^https?://")
    site: str | None = Field(default=None, pattern=SITE_PATTERN)
    unit: str | None = None  # private-systemd: the unit file, read at the released commit
    binary: str | None = None  # private-systemd: the binary's path inside the released image

    @model_validator(mode="after")
    def _a_site_and_its_token_go_together(self) -> DeployConfig:
        if self.site is not None and self.target not in PRIVATE_TARGETS:
            raise ValueError(
                "deploy.site applies to a private target only (private-compose, private-systemd)"
            )
        if self.site is None and ADDRESS_TOKEN in self.healthcheck:
            raise ValueError(
                f"deploy.healthcheck uses {ADDRESS_TOKEN} but no deploy.site says whose "
                "address it is"
            )
        if self.site is not None and not token_is_the_host(self.healthcheck):
            raise ValueError(
                f"behind deploy.site the healthcheck host is {ADDRESS_TOKEN}, filled from the "
                f"host's sites file (got {self.healthcheck})"
            )
        return self

    @model_validator(mode="after")
    def _a_systemd_target_names_its_unit_and_its_binary(self) -> DeployConfig:
        systemd = self.target is DeployTarget.PRIVATE_SYSTEMD
        for name, value in (("unit", self.unit), ("binary", self.binary)):
            if systemd and value is None:
                raise ValueError(f"deploy.{name} is required by target private-systemd")
            if not systemd and value is not None:
                raise ValueError(f"deploy.{name} applies to target private-systemd only")
        if self.unit is not None and (
            not _UNIT_PATH.fullmatch(self.unit) or _has_dot_segment(self.unit)
        ):
            raise ValueError(
                "deploy.unit must be a relative path inside the repository to a plain service "
                f"file ({UNIT_NAME_PATTERN}), got {self.unit!r}"
            )
        if self.binary is not None and (
            not _BINARY_PATH.fullmatch(self.binary) or _has_dot_segment(self.binary)
        ):
            raise ValueError(
                "deploy.binary must be an absolute path of safe characters inside the image, "
                f"got {self.binary!r}"
            )
        return self


class GateOverride(BaseModel):
    """A declared exception. `reason` is mandatory so the audit can show it instead of hiding it."""

    model_config = ConfigDict(extra="forbid")

    value: Any
    reason: str = Field(min_length=1)


class RailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rail: Literal[1]
    project: str = Field(pattern=r"^red-[a-z0-9]+(-[a-z0-9]+)*$")
    brain_key: str = Field(min_length=1, max_length=50)
    tier: Tier
    stack: Stack
    ledger: LedgerBackend = LedgerBackend.FILE
    ticket: UUID | None = None  # the delivery ticket (`red → <project>`), brain ledger only
    deploy: DeployConfig | None = None
    gates: dict[str, GateOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _prod_requires_deploy(self) -> RailConfig:
        if self.tier is Tier.PROD and self.deploy is None:
            raise ValueError("tier 'prod' requires a 'deploy' target")
        return self

    @model_validator(mode="after")
    def _ticket_follows_the_ledger(self) -> RailConfig:
        if self.ledger is LedgerBackend.BRAIN and self.ticket is None:
            raise ValueError("ledger 'brain' requires 'ticket' (the delivery ticket UUID)")
        if self.ledger is LedgerBackend.FILE and self.ticket is not None:
            raise ValueError("'ticket' is only meaningful with ledger 'brain'")
        return self

    @model_validator(mode="after")
    def _one_source_for_the_address(self) -> RailConfig:
        if self.deploy is not None and self.deploy.site is not None:
            if "deploy.bind_address" in self.gates:
                raise ValueError(
                    "deploy.site and a gates override of deploy.bind_address are two sources "
                    "for one address: the site's comes from the host, drop the override"
                )
        return self


MISSING_HINT = (
    f"{MANIFEST_NAME} is missing (run `rail new` for a new project, or write it for an "
    "existing repository)"
)


def try_load_rail_config(repo: Path) -> RailConfig | None:
    """The manifest when it is present and valid, else None — the one definition of
    "unreadable" shared by every gate and command (`manifest_problem` says why)."""
    try:
        return load_rail_config(repo)
    except (FileNotFoundError, ValidationError):
        return None


def manifest_problem(repo: Path) -> str | None:
    """Why the manifest cannot be read, in the words every gate and command repeat: missing
    (with the way out) or invalid (with the first error); None when it loads."""
    try:
        load_rail_config(repo)
    except FileNotFoundError:
        return MISSING_HINT
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        return f"{MANIFEST_NAME} is invalid: {location}: {first['msg']}"
    return None


@dataclass(frozen=True, slots=True)
class Declarations:
    """What the manifest declares, field by field; with no manifest at all, the documented
    defaults (`ledger: file`) and None where there is no default. `cfg` is the full manifest."""

    project: str | None
    stack: Stack | None
    ledger: LedgerBackend
    cfg: RailConfig | None


def declarations(repo: Path) -> Declarations | str:
    """The manifest's declarations, or why it cannot be read. Defaults apply ONLY when the file
    is absent: an invalid manifest that declares `ledger: brain` must never make a gate judge
    `docs/receipts` as authoritative (spec decision 4)."""
    if not (repo / MANIFEST_NAME).exists():
        return Declarations(project=None, stack=None, ledger=LedgerBackend.FILE, cfg=None)
    cfg = try_load_rail_config(repo)
    if cfg is None:
        return manifest_problem(repo) or f"{MANIFEST_NAME} unreadable"
    return Declarations(project=cfg.project, stack=cfg.stack, ledger=cfg.ledger, cfg=cfg)


def load_rail_config(repo: Path) -> RailConfig:
    """Read and validate `<repo>/rail.yaml`. A missing manifest is an error, not a default."""
    path = repo / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(
            f"{MISSING_HINT.replace(' is missing', f' is missing in {repo}', 1)}"
        )
    raw = yaml.safe_load(path.read_text()) or {}
    return RailConfig.model_validate(raw)
