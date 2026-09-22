"""Stage 3 — plan: the latest dated plan references an existing spec and every task
carries a verification (an expectation, an assertion, an exit code).

A plan of the graph method — `gitnexus-plan` writes `<date>-gitnexus-plan-<slug>.md` and
nowhere else — cites no spec and has no `### Task` by design. It is read in its own form:
its evidence is pinned to a commit of this branch's history, §7 sequences at least one step,
and its §11 pack lists at least one verification command."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from rail import gitrepo, markdown
from rail.gates import GateResult, GateSpec, Stage

PLANS_DIR = "docs/plans"

_GITNEXUS_PLAN = re.compile(r"^\d{4}-\d{2}-\d{2}-gitnexus-plan-.+\.md$")
# a full object name, SHA-1 or SHA-256: a ref such as `HEAD` resolves to whatever is checked
# out, and an abbreviation can become ambiguous — neither pins the evidence
_FULL_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def plan(repo: Path) -> GateResult:
    latest = markdown.latest_doc(repo / PLANS_DIR)
    if latest is None:
        return GateResult(
            Stage.PLAN, "plan", False, f"no dated plan in {PLANS_DIR} (expected <date>-<topic>.md)"
        )
    text = latest.read_text()
    if _GITNEXUS_PLAN.match(latest.name):
        return _gitnexus_plan(repo, latest.name, text)
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
        return GateResult(
            Stage.PLAN, "plan", False, f"{latest.name}: no `### Task …` heading (level 3)"
        )
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


def _context_pack(text: str) -> dict | None:
    """The `implementation_context` mapping of the plan's §11 pack, from the first fenced
    block that parses to one; a block that does not parse is not the pack. PyYAML raises a
    ValueError, not a YAMLError, on a date that does not exist (`2026-02-30`)."""
    for block in markdown.fenced_blocks(text):
        try:
            data = yaml.safe_load(block)
        except (yaml.YAMLError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("implementation_context"), dict):
            return data["implementation_context"]
    return None


def _gitnexus_plan(repo: Path, name: str, text: str) -> GateResult:
    def failed(details: str) -> GateResult:
        return GateResult(Stage.PLAN, "plan", False, f"{name}: {details}")

    pack = _context_pack(text)
    if pack is None:
        return failed("no readable `implementation_context` pack (§11)")
    evidence = pack.get("evidence_provenance")
    head = evidence.get("head_commit") if isinstance(evidence, dict) else None
    if not isinstance(head, str) or not head:
        return failed("the §11 pack pins no evidence_provenance.head_commit")
    if not _FULL_SHA.fullmatch(head):
        return failed(f"evidence commit {head!r} is not a full commit SHA")
    if not gitrepo.is_ancestor(repo, head):
        return failed(
            f"evidence commit {head[:12]} is not in this branch's history "
            "(re-pin it: gitnexus-plan, Deepen mode)"
        )
    sequence = markdown.section(text, "Implementation Sequence")
    steps = markdown.list_items(sequence) if sequence is not None else 0
    if not steps:
        return failed("no step under §7 Implementation Sequence")
    listed = pack.get("verification_commands")
    commands = (
        [c for c in listed if isinstance(c, str) and c.strip()] if isinstance(listed, list) else []
    )
    if not commands:
        return failed("the §11 pack lists no verification_commands")
    return GateResult(
        Stage.PLAN,
        "plan",
        True,
        f"{name}: {steps} step(s), {len(commands)} verification command(s), "
        f"evidence at {head[:12]}",
    )


GATES = [GateSpec(Stage.PLAN, "plan", plan)]
