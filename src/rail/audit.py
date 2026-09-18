"""`rail audit`: the repository × stage matrix. Every project is scored against its declared
tier (`bootstrap` when undeclared), declared exceptions are shown, the template version
comes from `.copier-answers.yml` — drift becomes reproducible, versioned, diffable."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from rail.gates import Stage, run_gates
from rail.model import Tier
from rail.policy import applicable_stages, declared_tier

SCHEMA_VERSION = 1
ANSWERS_FILE = ".copier-answers.yml"
Status = Literal["pass", "fail", "exception", "n/a"]
SYMBOLS: dict[str, str] = {"pass": "✓", "fail": "✗", "exception": "!", "n/a": "·"}
COLUMNS = {
    Stage.HYGIENE: "hyg",
    Stage.INTENT: "int",
    Stage.DESIGN: "dsg",
    Stage.PLAN: "pln",
    Stage.BUILD: "bld",
    Stage.REVIEW: "rev",
    Stage.INTEGRATE: "itg",
    Stage.RELEASE: "rel",
    Stage.DEPLOY: "dpl",
    Stage.OBSERVE: "obs",
    Stage.LEARN: "lrn",
}


@dataclass(frozen=True, slots=True)
class StageScore:
    stage: str
    status: Status
    passed: int
    total: int


@dataclass(frozen=True, slots=True)
class ProjectAudit:
    name: str
    path: str
    declared_tier: str | None
    tier_used: str
    template_version: str | None
    stages: list[StageScore]
    passed: int
    applicable: int
    exceptions: list[str]
    gates: list[dict[str, object]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def template_version(repo: Path) -> str | None:
    path = repo / ANSWERS_FILE
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return None
    commit = data.get("_commit") if isinstance(data, dict) else None
    return str(commit) if commit else None


def is_project(path: Path) -> bool:
    return path.is_dir() and ((path / ".git").exists() or (path / "rail.yaml").is_file())


def discover(root: Path) -> list[Path]:
    """Direct children that are projects (a `.git` or a `rail.yaml`), symlinks followed."""
    return sorted(
        (child for child in root.iterdir() if not child.name.startswith(".") and is_project(child)),
        key=lambda p: p.name,
    )


def audit_project(repo: Path, *, ci: bool = False) -> ProjectAudit:
    tier = declared_tier(repo)
    applicable = set(applicable_stages(repo))
    results = run_gates(repo, stages=applicable, ci=ci)
    stages: list[StageScore] = []
    passed = total = 0
    exceptions: list[str] = []
    for stage in Stage:
        mine = [r for r in results if r.stage is stage and not r.skipped]
        if stage not in applicable or not mine:
            # outside the tier, or every gate of the stage was skipped: nothing was judged
            stages.append(StageScore(stage.value, "n/a", 0, 0))
            continue
        ok = sum(1 for r in mine if r.passed)
        passed += ok
        total += len(mine)
        if ok == len(mine):
            status: Status = "exception" if any(r.exception for r in mine) else "pass"
        else:
            status = "fail"
        exceptions.extend(f"{r.gate_id}: {r.exception}" for r in mine if r.exception)
        stages.append(StageScore(stage.value, status, ok, len(mine)))
    return ProjectAudit(
        name=repo.name,
        path=str(repo),
        declared_tier=tier.value if tier else None,
        tier_used=(tier or Tier.BOOTSTRAP).value,
        template_version=template_version(repo),
        stages=stages,
        passed=passed,
        applicable=total,
        exceptions=exceptions,
        gates=[r.to_dict() for r in results],
    )


def audit_paths(paths: Iterable[Path], *, ci: bool = False) -> list[ProjectAudit]:
    """Each path is a project, or a directory whose project children are audited."""
    audits: list[ProjectAudit] = []
    for path in paths:
        if is_project(path):
            audits.append(audit_project(path, ci=ci))
        elif path.is_dir():
            audits.extend(audit_project(child, ci=ci) for child in discover(path))
    return audits


def matrix(audits: list[ProjectAudit]) -> dict[str, Any]:
    """The golden shape: statuses and scores, no free-text details, no absolute paths."""
    return {
        "schema_version": SCHEMA_VERSION,
        "projects": [
            {
                "name": a.name,
                "declared_tier": a.declared_tier,
                "tier_used": a.tier_used,
                "template_version": a.template_version,
                "score": {"passed": a.passed, "applicable": a.applicable},
                "stages": {s.stage: s.status for s in a.stages},
                "exceptions": a.exceptions,
            }
            for a in audits
        ],
    }


def to_json(audits: list[ProjectAudit]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "projects": [a.to_dict() for a in audits]}


def render_table(audits: list[ProjectAudit]) -> str:
    width = max((len(a.name) for a in audits), default=7)
    header = (
        f"{'project':<{width}}  {'tier':<10} {'template':<10} "
        + " ".join(COLUMNS.values())
        + "  score"
    )
    lines = [header, "-" * len(header)]
    for a in audits:
        tier = a.tier_used + ("" if a.declared_tier else "?")
        cells = " ".join(f"{SYMBOLS[s.status]:^3}" for s in a.stages)
        lines.append(
            f"{a.name:<{width}}  {tier:<10} {(a.template_version or '-'):<10} "
            f"{cells}  {a.passed}/{a.applicable}"
        )
    lines.append("")
    lines.append(
        "✓ pass   ✗ fail   ! declared exception   · not applicable to the tier   tier? undeclared"
    )
    for a in audits:
        for exc in a.exceptions:
            lines.append(f"  {a.name}: {exc}")
    return "\n".join(lines)
