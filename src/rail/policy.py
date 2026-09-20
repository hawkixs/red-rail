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
    # ReD is GitHub only (decision 30acbbde, 2026-09-20): no mirror by default. A project that
    # keeps one declares its host here (`gates:` in rail.yaml); `hygiene.remotes` then requires
    # it, `rail new` creates it and `rail release` pushes the tag to it.
    "hygiene.mirror_host": None,
    # the App identity the verdict gate trusts (issuer of the review_verdict attestation)
    "review.reviewer_identity": "red-rail-reviewer",
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
    # --- deploy target `vps-traefik` (spec §6 steps 7–8), measured on the border VPS on
    # 2026-09-19: Traefik v2.11 docker provider, exposedByDefault=false, entrypoint
    # `websecure`, certresolver `letsencrypt`, external network `pls_project_default`;
    # stacks under /opt/<name>/releases/<version> with a `current` symlink (red-gift) ---
    "deploy.ssh_host": "red-vps",
    "deploy.stack_root": "/opt",
    "deploy.traefik_network": "pls_project_default",
    "deploy.cert_resolver": "letsencrypt",
    "deploy.image_repository": "ghcr.io/hawkixs/{project}",  # decision 8faab5a3
    "deploy.platform": "linux/amd64",
    "deploy.healthcheck_timeout_seconds": 120,  # the first deployment waits for its certificate
    "deploy.compose_path": "deploy/compose.yaml",  # in the project, read at the released commit
    "deploy.remote_timeout_seconds": 900,  # the ssh session (pull + up --wait) is killed after
    # --- observe (spec §6 step 8): the red-monitor server and the agent watching the target ---
    "observe.monitor_url": "http://10.100.0.2:8081",
    "observe.monitor_agent": "vps",
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


def parameter(repo: Path, key: str, *, project: str | None = None) -> Any:
    """`effective()` without the reason; `{project}` is expanded in string values so a
    default can name the project (`ghcr.io/hawkixs/{project}`)."""
    value, _ = effective(repo, key)
    if isinstance(value, str) and project is not None:
        return value.replace("{project}", project)  # literal: a stray brace never raises
    return value
