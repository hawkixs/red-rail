"""Gates are pure functions of the repository state.

Each gate returns a `GateResult`; it never raises. The same function runs locally
(pre-push), in CI, and behind a Claude Code skill, so the policy exists in one place.

A gate is registered as a `GateSpec` (stage, code, function, scope). `run_gate` applies the
manifest's declared exceptions (`gates:` in `rail.yaml`, reason mandatory) and the `--ci`
scope rule, and turns an unexpected crash into a failed result — visibly, never silently.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal


class Stage(StrEnum):
    HYGIENE = "hygiene"
    INTENT = "intent"
    DESIGN = "design"
    PLAN = "plan"
    BUILD = "build"
    REVIEW = "review"
    INTEGRATE = "integrate"
    RELEASE = "release"
    DEPLOY = "deploy"
    OBSERVE = "observe"
    LEARN = "learn"


Scope = Literal["repo", "workstation", "ledger"]
# "ledger": reads the ledger — skipped under --ci when the ledger is brain (CI holds no credential)


@dataclass(frozen=True, slots=True)
class GateResult:
    stage: Stage
    code: str
    passed: bool
    details: str
    exception: str | None = None  # reason of a declared exception (`gates:` in rail.yaml)
    skipped: str | None = None  # why the gate was not evaluated (workstation-only under --ci)
    needs: str | None = None  # the manifest key the verdict depends on — always fail-closed

    @property
    def gate_id(self) -> str:
        return f"{self.stage.value}.{self.code}"

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["stage"] = self.stage.value
        return data


@dataclass(frozen=True, slots=True)
class Need:
    """The verdict depends on a manifest key that is not declared (spec 2026-09-23): what was
    observed is reported, and the gate stays closed until the key is declared."""

    key: str
    observed: str

    def result(self, stage: Stage, code: str) -> GateResult:
        return GateResult(
            stage, code, False, f"needs `{self.key}:` — {self.observed}", needs=self.key
        )


@dataclass(frozen=True, slots=True)
class GateSpec:
    stage: Stage
    code: str
    fn: Callable[[Path], GateResult]
    scope: Scope = "repo"  # "workstation": needs the operator's clone (remotes, roster)

    @property
    def gate_id(self) -> str:
        return f"{self.stage.value}.{self.code}"


def registry() -> list[GateSpec]:
    """Gates in stage order. Imported lazily so `rail.gates` stays dependency-free."""
    from rail.gates import build, design, evidence, hygiene, intent, plan

    return [
        *hygiene.GATES,
        *intent.GATES,
        *design.GATES,
        *plan.GATES,
        *build.GATES,
        *evidence.GATES,
    ]


def _ledger_is_brain(repo: Path) -> bool:
    from rail.model import LedgerBackend, try_load_rail_config

    cfg = try_load_rail_config(repo)
    return cfg is not None and cfg.ledger is LedgerBackend.BRAIN


def run_gate(spec: GateSpec, repo: Path, *, ci: bool = False) -> GateResult:
    from rail.policy import effective

    if ci and spec.scope == "workstation":
        return GateResult(
            spec.stage,
            spec.code,
            True,
            "not evaluated: workstation-only gate under --ci",
            skipped="workstation",
        )
    if ci and spec.scope == "ledger" and _ledger_is_brain(repo):
        return GateResult(
            spec.stage,
            spec.code,
            True,
            "not evaluated: ledger brain is unreachable from CI (spec §5 rule 3)",
            skipped="ledger",
        )
    value, reason = effective(repo, spec.gate_id)
    if value is False:
        return GateResult(
            spec.stage, spec.code, True, f"declared exception: {reason}", exception=reason
        )
    try:
        return spec.fn(repo)
    except Exception as exc:  # a gate never raises: a crash is a failed gate, visibly
        return GateResult(
            spec.stage, spec.code, False, f"gate crashed: {type(exc).__name__}: {exc}"
        )


def run_gates(
    repo: Path, *, stages: Iterable[Stage] | None = None, ci: bool = False
) -> list[GateResult]:
    wanted = None if stages is None else set(stages)
    return [
        run_gate(spec, repo, ci=ci) for spec in registry() if wanted is None or spec.stage in wanted
    ]
