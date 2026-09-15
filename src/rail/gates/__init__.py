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


Scope = Literal["repo", "workstation"]


@dataclass(frozen=True, slots=True)
class GateResult:
    stage: Stage
    code: str
    passed: bool
    details: str
    exception: str | None = None  # reason of a declared exception (`gates:` in rail.yaml)
    skipped: str | None = None  # why the gate was not evaluated (workstation-only under --ci)

    @property
    def gate_id(self) -> str:
        return f"{self.stage.value}.{self.code}"

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["stage"] = self.stage.value
        return data


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
    """Gates in evaluation order. Imported lazily so `rail.gates` stays dependency-free."""
    from rail.gates import hygiene

    return list(hygiene.GATES)


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
