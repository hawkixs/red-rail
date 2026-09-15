"""The audit matrix: every project scored against its declared tier, drift made diffable."""

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.audit import audit_paths, audit_project, discover, matrix, render_table, template_version
from rail.cli import main
from rail.gates import build as build_gates
from tests.helpers import conforming_tree, init_repo, with_evidence, write_manifest, write_roster

GOLDEN = Path(__file__).parent / "golden" / "audit-matrix.json"


@pytest.fixture(autouse=True)
def _no_real_gitleaks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (0, "no leaks found"))


def fixture_root(root: Path) -> Path:
    """Four projects: conforming bootstrap, conforming dev (templated), prod with a declared
    exception and missing post-integration evidence, and a bare repository without manifest."""
    alpha = conforming_tree(root, "red-alpha", "bootstrap")
    with_evidence(alpha, through="design")
    beta = conforming_tree(root, "red-beta", "dev")
    with_evidence(beta, through="integrate")
    (beta / ".copier-answers.yml").write_text(
        "_commit: v0.1.0\n_src_path: red-rail\nproject: red-beta\n"
    )
    gamma = conforming_tree(root, "red-gamma", "prod")
    with_evidence(gamma, through="integrate")
    write_manifest(
        gamma,
        project="red-gamma",
        tier="prod",
        deploy=True,
        gates={"review.verdict": (False, "reviewer arrives in phase 2")},
    )
    init_repo(root / "projects" / "red-delta")
    (root / "projects" / "not-a-project").mkdir()
    (root / "projects" / "notes.txt").write_text("x")
    write_roster(root, ["red-alpha", "red-beta", "red-gamma", "red-delta"])
    return root / "projects"


def test_discover_keeps_git_repositories_and_manifests_only(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    assert [p.name for p in discover(projects)] == [
        "red-alpha",
        "red-beta",
        "red-delta",
        "red-gamma",
    ]


def test_template_version_reads_copier_answers(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    assert template_version(projects / "red-beta") == "v0.1.0"
    assert template_version(projects / "red-alpha") is None


def test_audit_project_scores_against_the_declared_tier(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    alpha = audit_project(projects / "red-alpha")
    assert (alpha.declared_tier, alpha.tier_used, alpha.passed, alpha.applicable) == (
        "bootstrap",
        "bootstrap",
        10,
        10,
    )
    assert [s.status for s in alpha.stages] == ["pass", "pass", "pass"] + ["n/a"] * 8
    gamma = audit_project(projects / "red-gamma")
    assert (gamma.passed, gamma.applicable) == (17, 21)
    assert gamma.exceptions == ["review.verdict: reviewer arrives in phase 2"]
    assert {s.stage: s.status for s in gamma.stages}["review"] == "exception"
    delta = audit_project(projects / "red-delta")
    assert (delta.declared_tier, delta.tier_used, delta.passed, delta.applicable) == (
        None,
        "bootstrap",
        3,
        10,
    )


def test_matrix_matches_the_golden_snapshot(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    got = matrix(audit_paths([projects]))
    if os.environ.get("RAIL_UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
    assert got == json.loads(GOLDEN.read_text())


def test_audit_paths_accepts_projects_and_roots(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    assert [a.name for a in audit_paths([projects / "red-beta", projects / "red-alpha"])] == [
        "red-beta",
        "red-alpha",
    ]
    assert len(audit_paths([projects])) == 4
    assert audit_paths([projects / "not-a-project"]) == []


def test_render_table_is_one_row_per_project(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    table = render_table(audit_paths([projects]))
    lines = [line for line in table.splitlines() if line.startswith("red-")]
    assert len(lines) == 4
    assert "red-gamma" in table and "!" in table and "·" in table
    assert "review.verdict: reviewer arrives in phase 2" in table


def test_cli_audit_json_and_matrix(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    out = CliRunner().invoke(main, ["audit", str(projects), "--json"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.output)
    assert payload["schema_version"] == 1 and len(payload["projects"]) == 4
    assert {
        "name",
        "path",
        "declared_tier",
        "tier_used",
        "template_version",
        "stages",
        "passed",
        "applicable",
        "exceptions",
        "gates",
    } <= set(payload["projects"][0])
    out = CliRunner().invoke(main, ["audit", str(projects), "--matrix"])
    assert json.loads(out.output) == json.loads(GOLDEN.read_text())
    out = CliRunner().invoke(main, ["audit", str(projects / "not-a-project")])
    assert out.exit_code == 2 and "no project found" in out.output
