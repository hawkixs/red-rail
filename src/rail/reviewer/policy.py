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

Provider = Literal["agy", "codex", "claude", "glm"]
Tier = Literal["light", "deep"]
Mode = Literal["light", "deep"]

# Operator cost rule (2026-10-13): GLM Flash only, never the full model at either tier.
GLM_FLASH = "openrouter/z-ai/glm-5.3-flash"
DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "light": {
        "agy": "gemini-3.8-flash-high",
        "codex": "gpt-5.6-luna",
        "claude": "claude-sonnet-5",
        "glm": GLM_FLASH,
    },
    "deep": {
        # agy 1.3.4 lists no pro model any more: gemini-3.1-pro-high ended every deep pass in a
        # provider_fallback (ticket 7dc34128), so deep uses its strongest listed model
        "agy": "gemini-3.8-flash-high",
        "codex": "gpt-6-astra",
        "claude": "claude-opus-5",
        "glm": GLM_FLASH,
    },
}
TRAILER = re.compile(r"^co-authored-by:\s*(?P<who>.+?)\s*<", re.IGNORECASE | re.MULTILINE)
# ha-delegate's trailer on a commit integrated from a ha worker: `<provider>/<model> (ha <run>)`
IMPLEMENTED = re.compile(r"^implemented-by:\s*(?P<who>[^/\s]+)/", re.IGNORECASE | re.MULTILINE)
PRODUCERS: tuple[tuple[str, Provider], ...] = (
    ("claude", "claude"),
    ("anthropic", "claude"),
    ("codex", "codex"),
    ("openai", "codex"),
    ("antigravity", "agy"),
    ("gemini", "agy"),
    ("agy", "agy"),  # the provider id itself, as an `Implemented-by` trailer writes it
    ("glm", "glm"),
    ("z-ai", "glm"),
    ("zhipu", "glm"),
    ("opencode", "glm"),
)


class ReviewPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    providers: tuple[Provider, ...] = ("glm", "codex", "claude", "agy")
    models: dict[Tier, dict[Provider, str]] = Field(
        default_factory=lambda: {tier: dict(models) for tier, models in DEFAULT_MODELS.items()}
    )
    light_max_changed_lines: int = Field(default=200, ge=0)
    max_diff_chars: int = Field(default=200_000, ge=1000)
    # the order in which a diff is judged when it exceeds the budget: code, then tests, then
    # the rest; docs come last and generated lockfiles are never judged
    diff_priority: tuple[str, ...] = ("src/", "tests/", "workflows/", "skills/", "template/")
    ignored_globs: tuple[str, ...] = ("uv.lock", "*.lock", "package-lock.json")
    # bytes of prompt a provider accepts: agy and glm take their prompt in argv (headless-agents
    # refuses more than 120000 bytes); codex and claude read stdin
    prompt_limits: dict[str, int] = Field(default_factory=lambda: {"agy": 115_000, "glm": 115_000})
    # when no trailer names the producer, the ecosystem's default producer is still excluded:
    # ReD's sessions are Claude Code (found by the independent reviewer on PR #3)
    unknown_producer_excludes: tuple[Provider, ...] = ("claude",)
    timeout_seconds: float = Field(default=600.0, gt=0)
    check_name: str = "red-rail/review"
    rerun_label: str = "rail-review:rerun"
    docs_globs: tuple[str, ...] = ("docs/**", "*.md", "**/*.md")
    # dated records written by `rail` commands: a finding on one is at most minor (ticket
    # 98cce763 — a binding receipt carries the head at `rail bind` time, never the PR's head)
    records_globs: tuple[str, ...] = ("docs/receipts/*",)
    # convergence (2026-09-20): a pass after the first judges the delta since the last verdict
    # with the earlier findings in hand; review loops close by rounds and operator rulings
    # (spec 2026-09-25-review-loop-closure), never by a pass count
    incremental: bool = True

    @model_validator(mode="before")
    @classmethod
    def _no_pass_budget(cls, data):
        if isinstance(data, dict) and "max_passes_per_pr" in data:
            raise ValueError(
                "max_passes_per_pr is gone: review loops close by rounds and operator rulings "
                "(docs/specs/2026-09-25-review-loop-closure.md); remove the line from "
                "reviewer.yaml"
            )
        return data

    @model_validator(mode="after")
    def _every_provider_has_a_model(self) -> ReviewPolicy:
        for tier in ("light", "deep"):
            missing = [p for p in self.providers if p not in self.models.get(tier, {})]
            if missing:
                raise ValueError(f"no {tier} model for {missing}")
        return self

    @model_validator(mode="after")
    def _glm_flash_only(self) -> ReviewPolicy:
        for models in self.models.values():
            if "glm" in models and models["glm"] != GLM_FLASH:
                raise ValueError(
                    "the glm judge runs GLM 5.3 Flash only (GLM_FLASH), operator cost rule "
                    f"2026-10-13: got {models['glm']}"
                )
        return self

    def chain_for(self, *, producers: frozenset[Provider]) -> tuple[Provider, ...]:
        """Never a provider that wrote the change; an unknown producer excludes the usual
        suspects. An empty chain is a pass with no judge, which fails closed."""
        excluded = producers or frozenset(self.unknown_producer_excludes)
        return tuple(p for p in self.providers if p not in excluded)

    def mode_for(self, pr: PullRequest, *, docs_only: bool) -> Mode:
        if docs_only or pr.changed_lines <= self.light_max_changed_lines:
            return "light"
        return "deep"

    def model(self, provider: Provider, tier: Tier) -> str:
        return self.models[tier][provider]


def default_policy() -> ReviewPolicy:
    return ReviewPolicy()


def with_default_models(raw: dict) -> dict:
    """A `models` override is partial, per tier: never a silent drop of a provider. Every path
    that reads a policy from a file goes through here (ticket 7dc34128: the host configuration
    used to REPLACE the defaults)."""
    if "models" in raw:
        merged = {tier: dict(models) for tier, models in DEFAULT_MODELS.items()}
        for tier, models in (raw["models"] or {}).items():
            merged.setdefault(tier, {}).update(models)
        raw = {**raw, "models": merged}
    return raw


def load_policy(path: Path) -> ReviewPolicy:
    raw = yaml.safe_load(path.read_text()) or {}
    return ReviewPolicy.model_validate(with_default_models(raw))


def producer_providers(commit_messages: list[str]) -> frozenset[Provider]:
    """Every agent that wrote the PR: the `Co-Authored-By` trailers and ha-delegate's
    `Implemented-by` trailers. A name no judge provider matches excludes nothing."""
    found: set[Provider] = set()
    for message in commit_messages:
        names = [m.group("who") for m in TRAILER.finditer(message)]
        names += [m.group("who") for m in IMPLEMENTED.finditer(message)]
        for name in names:
            who = name.lower()
            found.update(provider for needle, provider in PRODUCERS if needle in who)
    return frozenset(found)
