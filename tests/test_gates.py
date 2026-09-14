"""Gates are pure functions of the repository state: same result locally, in CI, behind a skill."""

from pathlib import Path

from rail.gates import GateResult, Stage, run_gates
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


def test_run_gates_returns_one_result_per_gate_and_never_raises(tmp_path: Path) -> None:
    results = run_gates(tmp_path)
    assert [r.code for r in results] == ["rail_config", "docs_layout"]
    assert all(not r.passed for r in results)


def test_run_gates_all_pass_on_conforming_repo(tmp_path: Path) -> None:
    results = run_gates(_conforming_repo(tmp_path))
    assert all(r.passed for r in results)
