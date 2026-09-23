"""Stages 1–3: a recorded contract, a spec with the mandatory sections, a plan that verifies."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from rail.gates import Stage
from rail.gates.design import spec
from rail.gates.intent import contract
from rail.gates.plan import plan
from rail.ledger import RECEIPTS_DIR, Contract, Deliverable
from rail.ledger.file import FileLedger
from tests.helpers import commit_all, conforming_tree, git

CLOCK = lambda: datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # noqa: E731
CONTRACT = Contract(
    objective="ship red-alpha",
    acceptance_criteria=["rail check passes"],
    deliverables=[
        Deliverable(
            key="main",
            repository="hawkixs/red-alpha",
            no_checks_reason="fixture: no check declared",
        )
    ],
)


def _with_contract(repo: Path) -> Path:
    FileLedger(repo / RECEIPTS_DIR, clock=CLOCK).contract_set(
        "red-alpha", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c1"
    )
    return repo


def test_intent_without_a_manifest_reads_the_default_file_ledger(tmp_path: Path) -> None:
    result = contract(tmp_path)
    assert result.stage is Stage.INTENT and not result.passed and result.needs is None
    assert "no contract recorded in docs/receipts (default file ledger)" in result.details
    _with_contract(tmp_path)
    assert contract(tmp_path).needs == "project"


def test_intent_fails_without_a_contract_and_names_the_command(tmp_path: Path) -> None:
    result = contract(conforming_tree(tmp_path, "red-alpha", "bootstrap"))
    assert not result.passed and "rail contract set" in result.details


def test_intent_passes_with_a_contract(tmp_path: Path) -> None:
    result = contract(_with_contract(conforming_tree(tmp_path, "red-alpha", "bootstrap")))
    assert result.passed and "ship red-alpha" in result.details


def test_intent_reports_an_unavailable_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    text = (
        (repo / "rail.yaml")
        .read_text()
        .replace("ledger: file", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f")
    )
    (repo / "rail.yaml").write_text(text)
    monkeypatch.setenv("RAIL_BRAIN_TOKEN_FILE", str(tmp_path / "none"))
    result = contract(repo)
    assert not result.passed and "brain token" in result.details


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
    assert "no `### Task …` heading (level 3)" in plan(repo).details


def test_plan_passes_on_a_conforming_plan(tmp_path: Path) -> None:
    result = plan(conforming_tree(tmp_path, "red-beta", "dev"))
    assert result.passed and "1 task(s)" in result.details


# The graph method's plan, in the two forms `gitnexus-plan` writes (its plan-template.md):
# the full form numbers its headings, the compact form carries the § in parentheses.
GITNEXUS_PLAN = "2026-09-16-gitnexus-plan-add-retry-support.md"
GITNEXUS_HEADINGS = {
    "full": {
        1: "## 1. Objective",
        7: "## 7. Implementation Sequence",
        8: "## 8. Test Strategy",
        11: "## 11. Reusable Implementation Context",
        13: "## 13. Definition of Done",
    },
    "compact": {
        1: "## Objective (§1)",
        7: "## Implementation Sequence (§7) — risks inline as step notes",
        8: "## Test Strategy (§8)",
        11: "## Implementation Context (§11) — the mini-pack (see context-pack.md)",
        13: "## Definition of Done (§13)",
    },
}
# two steps: a detail nested under a step is not a step of its own
STEPS = (
    "1. Wrap `ingest()` in a bounded retry.\n"
    "   - three attempts at most, then the error\n"
    "2. Log the attempt count.\n"
)
BULLETS = "- Wrap `ingest()` in a bounded retry.\n- Log the attempt count.\n"
COMMANDS = "\n    - uv run pytest -q tests/test_ingest.py\n"


def _pack(head: str | None, commands: str | None = COMMANDS) -> str:
    """The §11 context pack, `implementation_context` as its root (context-pack.md)."""
    pack = (
        "implementation_context:\n"
        "  task_summary: add retry support to the ingestion pipeline\n"
        "  evidence_provenance:\n"
        "    schema_version: 2\n"
    )
    if head is not None:
        pack += f"    head_commit: '{head}'\n"
    return pack if commands is None else pack + f"  verification_commands:{commands}"


def _gitnexus_plan(
    repo: Path, *, form: str = "full", steps: str = STEPS, pack: str | None = None
) -> None:
    """A plan written by `gitnexus-plan` at the fixture's HEAD, dated after the rail plan
    `conforming_tree` ships, so it is the latest dated plan."""
    heading = GITNEXUS_HEADINGS[form]
    head = git(repo, "rev-parse", "HEAD")
    fence = "```"
    (repo / "docs" / "plans" / GITNEXUS_PLAN).write_text(
        "# GitNexus Engineering Plan\n\n"
        "> Task: add retry support to the ingestion pipeline\n"
        f"> Evidence verified at commit {head}.\n\n"
        f"{heading[1]}\n\nRetry the transient failures of the ingestion call.\n\n"
        f"{heading[7]}\n\n{steps}\n"
        f"{heading[8]}\n\n- tests/test_ingest.py: a transient failure is retried, "
        "a permanent one is not.\n\n"
        f"{heading[11]}\n\n{fence}yaml\n{_pack(head) if pack is None else pack}{fence}\n\n"
        f"{heading[13]}\n\n- the new tests pass.\n"
    )


@pytest.mark.parametrize(
    ("form", "steps"),
    [("full", STEPS), ("compact", STEPS), ("full", BULLETS)],
    ids=["full", "compact", "bullets"],
)
def test_plan_reads_a_gitnexus_plan_in_its_own_form(tmp_path: Path, form: str, steps: str) -> None:
    """`gitnexus-plan` writes `<date>-gitnexus-plan-<slug>.md` and nowhere else, with no
    `### Task` and no spec by design. Read as a rail plan, it failed `plan` as soon as it was
    the latest dated one — a correctly planned change reported unplanned. It is read by what
    makes it a plan: evidence pinned to this history, steps, commands that verify."""
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    _gitnexus_plan(repo, form=form, steps=steps)

    result = plan(repo)

    assert result.passed, result.details
    assert GITNEXUS_PLAN in result.details
    assert "2 step(s)" in result.details and "1 verification command(s)" in result.details


def test_a_gitnexus_plan_without_a_step_fails(tmp_path: Path) -> None:
    """§7 kept its heading and lost its sequence: prose is not a step, and neither is a
    bullet of another section."""
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    _gitnexus_plan(repo, steps="Nothing to sequence yet.\n")

    result = plan(repo)

    assert not result.passed and "no step under §7" in result.details


@pytest.mark.parametrize("commands", [" []\n", " ['']\n", None], ids=["empty", "blank", "absent"])
def test_a_gitnexus_plan_without_a_verification_command_fails(
    tmp_path: Path, commands: str | None
) -> None:
    """The template's own default is `verification_commands: []`: an empty list must not
    pass for a plan that says how it will be verified."""
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    _gitnexus_plan(repo, pack=_pack(git(repo, "rev-parse", "HEAD"), commands))

    result = plan(repo)

    assert not result.passed and "verification_commands" in result.details


@pytest.mark.parametrize("pinned", ["unknown", "side-branch", "symbolic", "absent"])
def test_a_gitnexus_plan_pinned_outside_this_history_fails(tmp_path: Path, pinned: str) -> None:
    """The evidence commit is to a gitnexus plan what the cited spec is to a rail plan. A
    commit this branch never had — unknown, or rebased away onto another line — pins
    evidence nobody can re-read from here; a ref such as `HEAD` resolves to whatever is
    checked out, which pins nothing at all."""
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    head = {"unknown": "0123456789abcdef0123456789abcdef01234567", "symbolic": "HEAD"}.get(pinned)
    if pinned == "side-branch":
        git(repo, "checkout", "-q", "-b", "side")
        head = commit_all(repo, "chore: a commit the plan's branch never had")
        git(repo, "checkout", "-q", "main")
    _gitnexus_plan(repo, pack=_pack(head))

    result = plan(repo)

    assert not result.passed and (head or "head_commit")[:12] in result.details


def test_a_block_quoted_before_the_pack_never_makes_the_gate_raise(tmp_path: Path) -> None:
    """§2 and §5 quote source. A quoted date that does not exist (`2026-02-30`) makes PyYAML
    raise a ValueError, not a YAMLError: the quote is not the pack, and the gate reads on to
    the pack instead of raising."""
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    _gitnexus_plan(repo)
    doc = repo / "docs" / "plans" / GITNEXUS_PLAN
    objective = "Retry the transient failures of the ingestion call.\n"
    doc.write_text(
        doc.read_text().replace(objective, objective + "\n```yaml\nwhen: 2026-02-30\n```\n")
    )

    result = plan(repo)

    assert result.passed, result.details


@pytest.mark.parametrize(
    "pack", ["", "implementation_context: [unclosed\n", "task_summary: no root key\n"]
)
def test_a_gitnexus_plan_without_a_readable_pack_fails(tmp_path: Path, pack: str) -> None:
    """No pack, a pack that does not parse, a pack without its `implementation_context`
    root: the gate reports it, and never raises."""
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    _gitnexus_plan(repo, pack=pack)

    result = plan(repo)

    assert not result.passed and "implementation_context" in result.details
