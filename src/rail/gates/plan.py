"""Stage 3 — plan: the latest dated plan references an existing spec and every task
carries a verification (an expectation, an assertion, an exit code)."""

from __future__ import annotations

from pathlib import Path

from rail import markdown
from rail.gates import GateResult, GateSpec, Stage

PLANS_DIR = "docs/plans"


def plan(repo: Path) -> GateResult:
    latest = markdown.latest_doc(repo / PLANS_DIR)
    if latest is None:
        return GateResult(
            Stage.PLAN, "plan", False, f"no dated plan in {PLANS_DIR} (expected <date>-<topic>.md)"
        )
    text = latest.read_text()
    refs = markdown.spec_references(text)
    if not refs:
        return GateResult(
            Stage.PLAN,
            "plan",
            False,
            f"{latest.name}: references no spec (docs/specs/<date>-<topic>.md)",
        )
    missing = [ref for ref in refs if not (repo / ref).is_file()]
    if missing:
        return GateResult(
            Stage.PLAN,
            "plan",
            False,
            f"{latest.name}: references missing spec(s): {', '.join(missing)}",
        )
    sections = markdown.task_sections(text)
    if not sections:
        return GateResult(Stage.PLAN, "plan", False, f"{latest.name}: no `### Task` section")
    unverified = [s.splitlines()[0] for s in sections if not markdown.has_verification(s)]
    if unverified:
        return GateResult(
            Stage.PLAN,
            "plan",
            False,
            f"{latest.name}: {len(unverified)} task(s) without a verification, "
            f"first: {unverified[0]}",
        )
    return GateResult(
        Stage.PLAN, "plan", True, f"{latest.name}: {len(sections)} task(s) verified, spec {refs[0]}"
    )


GATES = [GateSpec(Stage.PLAN, "plan", plan)]
