"""Stage 4 — build: static checks on the repository, never running the project's own suite."""

import random
import shutil
import string
from pathlib import Path

import pytest

from rail.gates import Stage
from rail.gates import build as build_gates
from rail.gates.build import GATES, commits, lint, secrets, tests
from tests.helpers import commit_all, conforming_tree, init_repo

# `tests` is a gate function, not a pytest test: its name matches pytest's default
# `python_functions = test*` glob, so without this it gets collected and fails at
# setup (it needs a `repo` argument pytest cannot supply as a fixture).
tests.__test__ = False


def test_registry() -> None:
    assert [g.code for g in GATES] == ["tests", "lint", "secrets", "commits"]
    assert all(g.stage is Stage.BUILD for g in GATES)


def test_tests_gate_per_stack(tmp_path: Path) -> None:
    py = conforming_tree(tmp_path / "py", "red-alpha", "dev")
    assert tests(py).passed and "1 test file" in tests(py).details
    (py / "tests" / "test_smoke.py").unlink()
    assert not tests(py).passed
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    assert tests(go).passed
    docs = conforming_tree(tmp_path / "docs", "red-gamma", "dev", stack="docs")
    assert tests(docs).passed and "docs" in tests(docs).details
    assert "rail.yaml" in tests(tmp_path).details


def test_lint_gate_per_stack(tmp_path: Path) -> None:
    py = conforming_tree(tmp_path / "py", "red-alpha", "dev")
    assert lint(py).passed
    (py / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert not lint(py).passed and "ruff" in lint(py).details
    (py / "ruff.toml").write_text("line-length = 100\n")
    assert lint(py).passed
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    assert lint(go).passed
    (go / "go.mod").unlink()
    assert not lint(go).passed


def test_secrets_gate_reads_the_gitleaks_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (0, "no leaks found"))
    assert secrets(tmp_path).passed
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (2, "leaks found: 1"))
    result = secrets(tmp_path)
    assert not result.passed and "leaks" in result.details
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (1, "boom"))
    assert "exit 1" in secrets(tmp_path).details
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: None)
    assert "not installed" in secrets(tmp_path).details


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_secrets_gate_runs_gitleaks_for_real(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    assert secrets(repo).passed, secrets(repo).details
    # Built at run time, never a literal in this file: red-rail's own history must stay clean.
    # Seed 7 yields a high-entropy fake GitHub PAT that gitleaks flags (verified 2026-09-15).
    alphabet = string.ascii_letters + string.digits
    fake_pat = "ghp_" + "".join(random.Random(7).choices(alphabet, k=36))
    (repo / "config.py").write_text(f'GITHUB_TOKEN = "{fake_pat}"\n')
    commit_all(repo, "feat: leak")
    assert not secrets(repo).passed


def test_commits_gate_checks_conventional_subjects(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    assert commits(repo).passed
    commit_all(repo, "Fixed stuff")
    result = commits(repo)
    assert not result.passed and "Fixed stuff" in result.details
    commit_all(repo, "wip(scope)!: allowed type with scope and bang")
    assert "wip" in commits(repo).details  # unknown type is reported
    empty = init_repo(tmp_path / "empty")
    assert "no commits" in commits(empty).details
    assert "not a git repository" in commits(tmp_path / "nowhere").details


def test_commits_gate_honours_the_window_override(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    commit_all(repo, "Fixed stuff")
    commit_all(repo, "feat: fine")
    assert not commits(repo).passed
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text()
        + "gates:\n  build.commit_window:\n    value: 1\n    reason: fixture\n"
    )
    assert commits(repo).passed
