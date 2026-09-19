"""The reviewer's host configuration: the App, its key, the repositories it watches and
where their checkouts (and therefore their ledgers) are. Private file, never in a tree."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from rail.private import read_private_file
from rail.reviewer.policy import ReviewPolicy

DEFAULT_CONFIG = Path("~/.config/red-rail/reviewer.yaml")


class RepositoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    path: Path  # local checkout: its rail.yaml chooses the ledger the verdict is attested in

    @field_validator("path")
    @classmethod
    def _expanded(cls, value: Path) -> Path:
        return value.expanduser()


class ReviewerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: int = Field(gt=0)
    installation_id: int = Field(gt=0)
    private_key_file: Path
    repositories: list[RepositoryConfig] = Field(min_length=1)
    poll_seconds: int = Field(default=60, ge=5, le=3600)
    policy: ReviewPolicy = Field(default_factory=ReviewPolicy)

    @field_validator("private_key_file")
    @classmethod
    def _expanded(cls, value: Path) -> Path:
        return value.expanduser()

    def private_key_pem(self) -> str:
        return read_private_file(self.private_key_file).decode("ascii")


def load_reviewer_config(path: Path = DEFAULT_CONFIG) -> ReviewerConfig:
    raw = yaml.safe_load(read_private_file(path.expanduser())) or {}
    return ReviewerConfig.model_validate(raw)
