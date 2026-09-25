"""Gates are pure functions of the repository state: same result locally, in CI, behind a skill."""

from pathlib import Path

import pytest

from rail.gates import GateResult, Stage, run_gates
from rail.gates import build as build_gates
from rail.gates.hygiene import docs_layout, rail_config


def _conforming_repo(tmp_path: Path) -> Path:
    (tmp_path / "rail.yaml").write_text(
        "rail: 1\nproject: red-probe\nbrain_key: red-probe\ntier: bootstrap\nstack: docs\n"
    )
    for sub in ("specs", "plans", "adr"):
        (tmp_path / "docs" / sub).mkdir(parents=True)
    return tmp_path


def test_rail_config_gate_passes_on_valid_manifest(tmp_path: Path) -> None:
    result = rail_config(_conforming_repo(tmp_path))
    assert result == GateResult(
        stage=Stage.HYGIENE, code="rail_config", passed=True, details="tier=bootstrap"
    )


def test_rail_config_gate_fails_without_manifest(tmp_path: Path) -> None:
    result = rail_config(tmp_path)
    assert not result.passed
    assert "rail.yaml" in result.details


def test_rail_config_gate_fails_on_invalid_manifest(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text("rail: 1\nproject: nope\n")
    result = rail_config(tmp_path)
    assert not result.passed
    assert result.code == "rail_config"


def test_docs_layout_gate_names_every_missing_directory(tmp_path: Path) -> None:
    (tmp_path / "docs" / "specs").mkdir(parents=True)
    result = docs_layout(tmp_path)
    assert not result.passed
    assert "docs/plans" in result.details
    assert "docs/adr" in result.details


def test_run_gates_returns_one_result_per_gate_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # gitleaks answers "no leaks" on an empty directory; pin the gate to a failure so the
    # assertion below does not depend on the host's gitleaks
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (1, "not a git repository"))
    results = run_gates(tmp_path)
    assert [r.gate_id for r in results] == [
        "hygiene.rail_config",
        "hygiene.docs_layout",
        "hygiene.claude_md",
        "hygiene.task_runner",
        "hygiene.settings",
        "hygiene.remotes",
        "hygiene.roster_entry",
        "hygiene.receipts",
        "hygiene.mirrors",
        "intent.contract",
        "design.spec",
        "plan.plan",
        "build.tests",
        "build.lint",
        "build.secrets",
        "build.commits",
        "review.verdict",
        "review.carry_forward",
        "integrate.receipt",
        "release.released",
        "deploy.deployed",
        "observe.visible",
        "observe.drill",
        "learn.fulfilled",
    ]
    by_id = {r.gate_id: r for r in results}
    # vacuous pass on an empty tree: no receipts to check, no roster in scope,
    # hygiene.mirrors defaults to the file ledger with no manifest to declare otherwise
    # (spec 2026-09-23: absent-manifest defaults apply, `docs/receipts` is the ledger), and
    # review.carry_forward has nothing recorded to account for.
    vacuous = (
        "hygiene.receipts",
        "hygiene.roster_entry",
        "hygiene.mirrors",
        "review.carry_forward",
    )
    assert all(by_id[gate_id].passed for gate_id in vacuous)
    assert all(not r.passed for r in results if r.gate_id not in vacuous)


def test_run_gates_all_pass_on_conforming_repo(tmp_path: Path) -> None:
    from rail.gates import Stage
    from rail.ledger import RECEIPTS_DIR, Contract, Deliverable
    from rail.ledger.file import FileLedger
    from tests.helpers import conforming_tree, write_roster

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"])
    FileLedger(repo / RECEIPTS_DIR).contract_set(
        "red-alpha",
        Contract(
            objective="x",
            deliverables=[
                Deliverable(
                    key="m",
                    repository="hawkixs/red-alpha",
                    no_checks_reason="fixture: no check declared",
                )
            ],
        ),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c1",
    )
    results = run_gates(repo, stages=[Stage.HYGIENE, Stage.INTENT, Stage.DESIGN])
    assert all(r.passed for r in results), [r for r in results if not r.passed]


def test_ledger_gates_are_skipped_in_ci_only_with_the_brain_ledger(tmp_path: Path) -> None:
    from rail.gates import GateResult, GateSpec, Stage, run_gate
    from tests.helpers import conforming_tree

    spec = GateSpec(
        Stage.REVIEW,
        "probe",
        lambda repo: GateResult(Stage.REVIEW, "probe", True, "ran"),
        scope="ledger",
    )
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    assert (
        run_gate(spec, repo, ci=True).details == "ran"
    )  # file ledger: receipts are in the checkout
    manifest = (repo / "rail.yaml").read_text()
    (repo / "rail.yaml").write_text(
        manifest.replace(
            "ledger: file\n", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n"
        )
    )
    skipped = run_gate(spec, repo, ci=True)
    assert skipped.passed and skipped.skipped == "ledger"
    assert "unreachable from CI" in skipped.details
    assert run_gate(spec, repo, ci=False).details == "ran"


def test_only_the_manifest_gate_names_a_missing_manifest(tmp_path: Path) -> None:
    """Spec 2026-09-23 supersedes "one fact, one wording" (red-arena, 2026-09-20) for an ABSENT
    manifest: the manifest gate names it, every other gate names what it observed or the key
    it needs. An INVALID manifest is still named the same way everywhere."""
    from rail.gates import build, evidence, hygiene, intent
    from rail.model import MISSING_HINT

    assert hygiene.rail_config(tmp_path).details == MISSING_HINT
    assert hygiene.mirrors(tmp_path).passed
    assert "file ledger (default)" in hygiene.mirrors(tmp_path).details
    assert "no contract recorded in docs/receipts" in intent.contract(tmp_path).details
    assert build.has_tests(tmp_path).needs == "stack"
    for gate in (hygiene.mirrors, intent.contract, build.has_tests, build.lint, evidence.visible):
        assert "rail.yaml" not in gate(tmp_path).details
    (tmp_path / "rail.yaml").write_text("rail: 1\nproject: nope\n")
    for gate in (hygiene.mirrors, intent.contract, build.has_tests, evidence.verdict):
        result = gate(tmp_path)
        assert not result.passed and "is invalid" in result.details, result
