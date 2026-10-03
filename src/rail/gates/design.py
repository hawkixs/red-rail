"""Stage 2 — design: every spec of the latest date carries the mandatory sections."""

from __future__ import annotations

from pathlib import Path

from rail import markdown
from rail.gates import GateResult, GateSpec, Stage, first_failure
from rail.policy import PRIVATE_DOCS_ROOT, REQUIRED_SPEC_SECTIONS, docs_root


def spec(repo: Path) -> GateResult:
    root, absent = docs_root(repo)
    if absent:
        return GateResult(Stage.DESIGN, "spec", True, absent, skipped=PRIVATE_DOCS_ROOT)
    specs_dir = f"{root}/specs"
    latest = markdown.latest_docs(repo / specs_dir)
    if not latest:
        return GateResult(
            Stage.DESIGN,
            "spec",
            False,
            f"no dated spec in {specs_dir} (expected <date>-<topic>.md)",
        )
    return first_failure(_spec(doc) for doc in latest)


def _spec(latest: Path) -> GateResult:
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
