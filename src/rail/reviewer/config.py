"""The reviewer's host configuration: the App, its key, the repositories it watches and
where their checkouts (and therefore their ledgers) are. Private file, never in a tree."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from rail.private import read_private_file
from rail.reviewer.policy import ReviewPolicy, with_default_models

DEFAULT_CONFIG = Path("~/.config/red-rail/reviewer.yaml")


class RepositoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    path: Path  # local checkout: its rail.yaml chooses the ledger the verdict is attested in
    merge_queue: Path | None = None

    @field_validator("path")
    @classmethod
    def _expanded(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("merge_queue")
    @classmethod
    def _expanded_queue(cls, value: Path | None) -> Path | None:
        """Expand the private journal path without reading or resolving it at load time."""
        return value.expanduser() if value is not None else None


class ReviewerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: int = Field(gt=0)
    installation_id: int = Field(gt=0)
    private_key_file: Path
    repositories: list[RepositoryConfig] = Field(min_length=1)
    poll_seconds: int = Field(default=60, ge=5, le=3600)
    policy: ReviewPolicy = Field(default_factory=ReviewPolicy)
    # the operator delegations an agent's fix ruling may stand on (decision f38ce645): brain ids,
    # full or a unique prefix of 8 hex characters or more. A title in brain proves nothing — any
    # writer can log one — so the host file, the operator's, names the delegations it trusts,
    # and the coordination adds its succession's id at each handover
    delegations: tuple[str, ...] = ()

    @field_validator("delegations")
    @classmethod
    def _delegation_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not re.fullmatch(r"[0-9a-f]{8}[0-9a-f-]{0,28}", item):
                raise ValueError(f"delegations: {item!r} is not a brain decision id")
        return value

    @field_validator("private_key_file")
    @classmethod
    def _expanded(cls, value: Path) -> Path:
        return value.expanduser()

    def private_key_pem(self) -> str:
        return read_private_file(self.private_key_file).decode("ascii")


def load_reviewer_config(path: Path = DEFAULT_CONFIG) -> ReviewerConfig:
    raw = yaml.safe_load(read_private_file(path.expanduser())) or {}
    if isinstance(raw.get("policy"), dict):
        raw["policy"] = with_default_models(raw["policy"])
    return ReviewerConfig.model_validate(raw)
