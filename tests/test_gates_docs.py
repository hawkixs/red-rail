"""Stages 1–3: a recorded contract, a spec with the mandatory sections, a plan that verifies."""

from datetime import UTC, datetime
from pathlib import Path

from rail.gates import Stage
from rail.gates.design import spec
from rail.gates.intent import contract
from rail.gates.plan import plan
from rail.ledger import RECEIPTS_DIR, Contract, Deliverable
from rail.ledger.file import FileLedger
from tests.helpers import conforming_tree

CLOCK = lambda: datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # noqa: E731
CONTRACT = Contract(
    objective="ship red-alpha",
    acceptance_criteria=["rail check passes"],
    deliverables=[Deliverable(key="main", repository="hawkixs/red-alpha")],
)


def _with_contract(repo: Path) -> Path:
    FileLedger(repo / RECEIPTS_DIR, clock=CLOCK).contract_set(
        "red-alpha", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c1"
    )
    return repo


def test_intent_requires_a_readable_manifest(tmp_path: Path) -> None:
    result = contract(tmp_path)
    assert result.stage is Stage.INTENT and not result.passed
    assert "rail.yaml" in result.details


def test_intent_fails_without_a_contract_and_names_the_command(tmp_path: Path) -> None:
    result = contract(conforming_tree(tmp_path, "red-alpha", "bootstrap"))
    assert not result.passed and "rail contract set" in result.details


def test_intent_passes_with_a_contract(tmp_path: Path) -> None:
    result = contract(_with_contract(conforming_tree(tmp_path, "red-alpha", "bootstrap")))
    assert result.passed and "ship red-alpha" in result.details


def test_intent_reports_an_unavailable_backend(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    text = (
        (repo / "rail.yaml")
        .read_text()
        .replace("ledger: file", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f")
    )
    (repo / "rail.yaml").write_text(text)
    result = contract(repo)
    assert not result.passed and "phase 2" in result.details


def test_design_wants_a_dated_spec(tmp_path: Path) -> None:
    result = spec(tmp_path)
    assert result.stage is Stage.DESIGN and not result.passed
    assert "docs/specs" in result.details


def test_design_names_every_missing_section(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (repo / "docs" / "specs" / "2026-09-15-red-alpha-design.md").write_text("# x\n\n## Problem\n")
    result = spec(repo)
    assert not result.passed
    for name in ("decisions", "non-goals", "success criteria"):
        assert name in result.details


def test_design_accepts_french_aliases(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (repo / "docs" / "specs" / "2026-09-15-red-alpha-design.md").write_text(
        "# x\n\n## 1. Contexte\n\n## 2. Décisions\n\n## 3. Hors périmètre\n\n"
        "## 4. Critères de succès\n"
    )
    assert spec(repo).passed


def test_design_passes_on_a_conforming_spec(tmp_path: Path) -> None:
    result = spec(conforming_tree(tmp_path, "red-alpha", "bootstrap"))
    assert result.passed and "2026-09-15-red-alpha-design.md" in result.details


def test_plan_wants_a_dated_plan(tmp_path: Path) -> None:
    result = plan(tmp_path)
    assert result.stage is Stage.PLAN and not result.passed


def test_plan_must_reference_an_existing_spec(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    doc = repo / "docs" / "plans" / "2026-09-15-red-beta-plan.md"
    doc.write_text("# plan\n\n### Task 1.1: x\n- expect PASS\n")
    assert "references no spec" in plan(repo).details
    doc.write_text("# plan\n\ndocs/specs/2026-01-01-missing.md\n\n### Task 1.1: x\n- expect PASS\n")
    assert "missing spec" in plan(repo).details


def test_plan_requires_a_verification_per_task(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    doc = repo / "docs" / "plans" / "2026-09-15-red-beta-plan.md"
    doc.write_text(
        "# plan\n\ndocs/specs/2026-09-15-red-beta-design.md\n\n"
        "### Task 1.1: ok\n- expect PASS\n\n### Task 1.2: nope\n- write code\n"
    )
    result = plan(repo)
    assert not result.passed and "Task 1.2" in result.details
    doc.write_text("# plan\n\ndocs/specs/2026-09-15-red-beta-design.md\n")
    assert "no `### Task`" in plan(repo).details


def test_plan_passes_on_a_conforming_plan(tmp_path: Path) -> None:
    result = plan(conforming_tree(tmp_path, "red-beta", "dev"))
    assert result.passed and "1 task(s)" in result.details
