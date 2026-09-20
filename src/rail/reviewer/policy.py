"""The review policy as data (ADR-0003): which providers, which models per tier, when a PR
is light or deep, who the producer is. Defaults are the values measured on the neighbours
(brain-v42 Dream defaults canaried on 2026-09-12); `~/.config/red-rail/reviewer.yaml`
overrides what it names."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rail.reviewer.github import PullRequest

Provider = Literal["agy", "codex", "claude"]
Tier = Literal["light", "deep"]
Mode = Literal["light", "deep"]

DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "light": {"agy": "gemini-3.8-flash-high", "codex": "gpt-5.6-luna", "claude": "claude-sonnet-5"},
    "deep": {"agy": "gemini-3.1-pro-high", "codex": "gpt-6-astra", "claude": "claude-opus-5"},
}
TRAILER = re.compile(r"^co-authored-by:\s*(?P<who>.+?)\s*<", re.IGNORECASE | re.MULTILINE)
PRODUCERS: tuple[tuple[str, Provider], ...] = (
    ("claude", "claude"),
    ("anthropic", "claude"),
    ("codex", "codex"),
    ("openai", "codex"),
    ("antigravity", "agy"),
    ("gemini", "agy"),
)


class ReviewPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    providers: tuple[Provider, ...] = ("agy", "codex", "claude")
    models: dict[Tier, dict[Provider, str]] = Field(
        default_factory=lambda: {tier: dict(models) for tier, models in DEFAULT_MODELS.items()}
    )
    light_max_changed_lines: int = Field(default=200, ge=0)
    max_diff_chars: int = Field(default=200_000, ge=1000)
    # the order in which a diff is judged when it exceeds the budget: code, then tests, then
    # the rest; docs come last and generated lockfiles are never judged
    diff_priority: tuple[str, ...] = ("src/", "tests/", "workflows/", "skills/", "template/")
    ignored_globs: tuple[str, ...] = ("uv.lock", "*.lock", "package-lock.json")
    # bytes of prompt a provider accepts: agy takes its prompt in argv (headless-agents refuses
    # more than 120000 bytes); codex and claude read stdin
    prompt_limits: dict[str, int] = Field(default_factory=lambda: {"agy": 115_000})
    # when no trailer names the producer, the ecosystem's default producer is still excluded:
    # ReD's sessions are Claude Code (found by the independent reviewer on PR #3)
    unknown_producer_excludes: tuple[Provider, ...] = ("claude",)
    timeout_seconds: float = Field(default=600.0, gt=0)
    check_name: str = "red-rail/review"
    rerun_label: str = "rail-review:rerun"
    docs_globs: tuple[str, ...] = ("docs/**", "*.md", "**/*.md")
    # convergence (2026-09-20): a pass after the first judges the delta since the last verdict
    # with the earlier findings in hand; beyond the budget the check fails until the label
    max_passes_per_pr: int = Field(default=4, ge=1)
    incremental: bool = True

    @model_validator(mode="after")
    def _every_provider_has_a_model(self) -> ReviewPolicy:
        for tier in ("light", "deep"):
            missing = [p for p in self.providers if p not in self.models.get(tier, {})]
            if missing:
                raise ValueError(f"no {tier} model for {missing}")
        return self

    def chain_for(self, *, producer: Provider | None) -> tuple[Provider, ...]:
        """Never the producer's provider; an unknown producer excludes the usual suspects."""
        excluded = {producer} if producer else set(self.unknown_producer_excludes)
        return tuple(p for p in self.providers if p not in excluded)

    def mode_for(self, pr: PullRequest, *, docs_only: bool) -> Mode:
        if docs_only or pr.changed_lines <= self.light_max_changed_lines:
            return "light"
        return "deep"

    def model(self, provider: Provider, tier: Tier) -> str:
        return self.models[tier][provider]


def default_policy() -> ReviewPolicy:
    return ReviewPolicy()


def load_policy(path: Path) -> ReviewPolicy:
    raw = yaml.safe_load(path.read_text()) or {}
    if "models" in raw:  # partial override per tier, never a silent drop of a provider
        merged = {tier: dict(models) for tier, models in DEFAULT_MODELS.items()}
        for tier, models in raw["models"].items():
            merged.setdefault(tier, {}).update(models)
        raw["models"] = merged
    return ReviewPolicy.model_validate(raw)


def producer_provider(commit_messages: list[str]) -> Provider | None:
    """The agent that wrote the PR, read from the `Co-Authored-By` trailers."""
    for message in commit_messages:
        for match in TRAILER.finditer(message):
            who = match.group("who").lower()
            for needle, provider in PRODUCERS:
                if needle in who:
                    return provider
    return None
