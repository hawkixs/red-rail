"""The per-project manifest (`rail.yaml`).

Deliberately tiny: anything not declared here is a tier default versioned in red-rail,
which is what keeps twenty manifests from diverging.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MANIFEST_NAME = "rail.yaml"


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
    PC_SERVER_SYSTEMD = "pc-server-systemd"


class DeployConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: DeployTarget
    healthcheck: str = Field(pattern=r"^https?://")


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
