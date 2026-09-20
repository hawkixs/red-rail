"""Stage 2 — design: the latest dated spec carries the mandatory sections."""

from __future__ import annotations

from pathlib import Path

from rail import markdown
from rail.gates import GateResult, GateSpec, Stage
from rail.policy import REQUIRED_SPEC_SECTIONS

SPECS_DIR = "docs/specs"


def spec(repo: Path) -> GateResult:
    latest = markdown.latest_doc(repo / SPECS_DIR)
    if latest is None:
        return GateResult(
            Stage.DESIGN,
            "spec",
            False,
            f"no dated spec in {SPECS_DIR} (expected <date>-<topic>.md)",
        )
    text = latest.read_text()
    missing = [
        name
        for name, aliases in REQUIRED_SPEC_SECTIONS.items()
        if not markdown.has_section(text, aliases)
    ]
    if missing:
        accepted = "; ".join(
            f"{name}: {' | '.join(REQUIRED_SPEC_SECTIONS[name])}" for name in missing
        )
        return GateResult(
            Stage.DESIGN,
            "spec",
            False,
            f"{latest.name}: missing section(s): {', '.join(missing)} "
            f"(a heading containing one of — {accepted})",
        )
    return GateResult(
        Stage.DESIGN,
        "spec",
        True,
        f"{latest.name}: {len(REQUIRED_SPEC_SECTIONS)} mandatory sections present",
    )


GATES = [GateSpec(Stage.DESIGN, "spec", spec)]
