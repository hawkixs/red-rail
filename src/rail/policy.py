"""Tier defaults, versioned here so twenty manifests never diverge.

Everything a `rail.yaml` does not declare comes from this module. A `gates:` entry in the
manifest overrides one key and must carry a reason; `effective()` returns both so the
audit can show the exception instead of hiding it. A key is either a gate id
(`stage.code`, boolean, default True — `false` disables the gate as a declared exception)
or a typed parameter (`build.commit_window`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rail.gates import Stage
from rail.model import Tier, try_load_rail_config

TIER_STAGES: dict[Tier, tuple[Stage, ...]] = {
    Tier.BOOTSTRAP: (Stage.HYGIENE, Stage.INTENT, Stage.DESIGN),
    Tier.DEV: (
        Stage.HYGIENE,
        Stage.INTENT,
        Stage.DESIGN,
        Stage.PLAN,
        Stage.BUILD,
        Stage.REVIEW,
        Stage.INTEGRATE,
    ),
    Tier.PROD: tuple(Stage),
}

# Sections the design gate requires in a spec, with the heading aliases in use across ReD.
REQUIRED_SPEC_SECTIONS: dict[str, tuple[str, ...]] = {
    "problem": ("problem", "problème", "probleme", "context", "contexte"),
    "decisions": ("decision", "décision"),
    "non-goals": (
        "non-goal",
        "non goal",
        "out of scope",
        "hors périmètre",
        "hors perimetre",
        "non-objectif",
    ),
    "success criteria": (
        "success criteri",
        "critères de succès",
        "criteres de succes",
        "critère de succès",
    ),
}

GATE_DEFAULTS: dict[str, Any] = {
    "hygiene.canonical_host": "github.com",
    "hygiene.mirror_host": "gitlab.hawkixs.local",
    "build.commit_window": 20,
    "build.conventional_types": (
        "feat",
        "fix",
        "docs",
        "chore",
        "refactor",
        "test",
        "ci",
        "build",
        "perf",
        "style",
        "revert",
    ),
}


def stages_for(tier: Tier) -> tuple[Stage, ...]:
    return TIER_STAGES[tier]


def declared_tier(repo: Path) -> Tier | None:
    """The tier written in `rail.yaml`, or None when the manifest is missing or invalid."""
    cfg = try_load_rail_config(repo)
    return cfg.tier if cfg else None


def applicable_stages(repo: Path) -> tuple[Stage, ...]:
    """Stages a repository is scored against: its declared tier, else the `bootstrap` floor."""
    return stages_for(declared_tier(repo) or Tier.BOOTSTRAP)


def effective(repo: Path, key: str) -> tuple[Any, str | None]:
    """(value, reason): the manifest override when declared, else the versioned default."""
    default = GATE_DEFAULTS.get(key, True)
    cfg = try_load_rail_config(repo)
    if cfg is None:
        return default, None
    override = cfg.gates.get(key)
    if override is None:
        return default, None
    return override.value, override.reason
