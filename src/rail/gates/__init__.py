"""Gates are pure functions of the repository state.

Each gate returns a `GateResult`; it never raises. The same function runs locally
(pre-push), in CI, and behind a Claude Code skill, so the policy exists in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path


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


@dataclass(frozen=True, slots=True)
class GateResult:
    stage: Stage
    code: str
    passed: bool
    details: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["stage"] = self.stage.value
        return data


Gate = Callable[[Path], GateResult]


def registry() -> list[Gate]:
    """Gates in evaluation order. Imported lazily so `rail.gates` stays dependency-free."""
    from rail.gates import hygiene

    return [hygiene.rail_config, hygiene.docs_layout]


def run_gates(repo: Path) -> list[GateResult]:
    return [gate(repo) for gate in registry()]
