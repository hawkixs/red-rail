"""The per-project manifest (`rail.yaml`).

Deliberately tiny: anything not declared here is a tier default versioned in red-rail,
which is what keeps twenty manifests from diverging.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    deploy: DeployConfig | None = None
    gates: dict[str, GateOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _prod_requires_deploy(self) -> RailConfig:
        if self.tier is Tier.PROD and self.deploy is None:
            raise ValueError("tier 'prod' requires a 'deploy' target")
        return self


def load_rail_config(repo: Path) -> RailConfig:
    """Read and validate `<repo>/rail.yaml`. A missing manifest is an error, not a default."""
    path = repo / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(f"{MANIFEST_NAME} not found in {repo}")
    raw = yaml.safe_load(path.read_text()) or {}
    return RailConfig.model_validate(raw)
