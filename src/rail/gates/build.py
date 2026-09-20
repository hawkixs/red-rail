"""Stage 4 — build: static checks. Tests exist for the stack, a linter is configured, gitleaks
finds nothing in the history, commit subjects are conventional. Running the project's own
suite is CI's job (`make ci`); its exit code is the check, not this gate."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from rail import gitrepo
from rail.gates import GateResult, GateSpec, Stage
from rail.model import MANIFEST_NAME, Stack, manifest_problem, try_load_rail_config
from rail.policy import effective

CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]+\))?!?: \S")


def _stack(repo: Path) -> Stack | None:
    cfg = try_load_rail_config(repo)
    return cfg.stack if cfg else None


def has_tests(repo: Path) -> GateResult:
    # named `has_tests`, not `tests`: pytest would collect a `tests` function on import
    stack = _stack(repo)
    if stack is None:
        return GateResult(
            Stage.BUILD, "tests", False, manifest_problem(repo) or f"{MANIFEST_NAME} unreadable"
        )
    if stack is Stack.DOCS:
        return GateResult(Stage.BUILD, "tests", True, "stack docs: no test suite required")
    if stack is Stack.PYTHON:
        root = repo / "tests"
        found = (
            [
                p
                for p in root.rglob("*.py")
                if p.name.startswith("test_") or p.name.endswith("_test.py")
            ]
            if root.is_dir()
            else []
        )
        where = "tests/test_*.py"
    else:
        found = [p for p in repo.rglob("*_test.go") if "vendor" not in p.parts]
        where = "*_test.go"
    if not found:
        return GateResult(Stage.BUILD, "tests", False, f"no test files ({where})")
    return GateResult(Stage.BUILD, "tests", True, f"{len(found)} test file(s)")


def lint(repo: Path) -> GateResult:
    stack = _stack(repo)
    if stack is None:
        return GateResult(
            Stage.BUILD, "lint", False, manifest_problem(repo) or f"{MANIFEST_NAME} unreadable"
        )
    if stack is Stack.DOCS:
        return GateResult(Stage.BUILD, "lint", True, "stack docs: no linter required")
    if stack is Stack.PYTHON:
        pyproject = repo / "pyproject.toml"
        configured = (pyproject.is_file() and "[tool.ruff" in pyproject.read_text()) or any(
            (repo / name).is_file() for name in ("ruff.toml", ".ruff.toml")
        )
        if configured:
            return GateResult(Stage.BUILD, "lint", True, "ruff configured")
        return GateResult(
            Stage.BUILD,
            "lint",
            False,
            "ruff is not configured ([tool.ruff] in pyproject.toml or ruff.toml)",
        )
    if (repo / "go.mod").is_file():
        return GateResult(Stage.BUILD, "lint", True, "go.mod present (go vet is built in)")
    return GateResult(Stage.BUILD, "lint", False, "go.mod is missing")


def run_gitleaks(repo: Path) -> tuple[int, str] | None:
    """(exit code, last output line); None when gitleaks is not installed.
    Exit 0 = clean, 2 = leaks (`--exit-code 2`), anything else = gitleaks itself failed."""
    exe = shutil.which("gitleaks")
    if exe is None:
        return None
    done = subprocess.run(
        [exe, "git", "--no-banner", "--redact", "--exit-code", "2", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = (done.stderr or done.stdout).strip().splitlines()
    return done.returncode, lines[-1] if lines else ""


def secrets(repo: Path) -> GateResult:
    outcome = run_gitleaks(repo)
    if outcome is None:
        return GateResult(
            Stage.BUILD, "secrets", False, "gitleaks is not installed (required by the build gate)"
        )
    code, last = outcome
    if code == 0:
        return GateResult(Stage.BUILD, "secrets", True, "gitleaks: no leaks found")
    if code == 2:
        return GateResult(Stage.BUILD, "secrets", False, f"gitleaks found leaks: {last}")
    return GateResult(Stage.BUILD, "secrets", False, f"gitleaks failed (exit {code}): {last}")


def commits(repo: Path) -> GateResult:
    window, _ = effective(repo, "build.commit_window")
    types, _ = effective(repo, "build.conventional_types")
    if not gitrepo.is_git_repo(repo):
        return GateResult(Stage.BUILD, "commits", False, "not a git repository")
    subjects = gitrepo.recent_subjects(repo, int(window))
    if not subjects:
        return GateResult(Stage.BUILD, "commits", False, "no commits")
    bad = []
    for subject in subjects:
        match = CONVENTIONAL.match(subject)
        if match is None or match.group("type") not in types:
            bad.append(subject)
    if bad:
        return GateResult(
            Stage.BUILD,
            "commits",
            False,
            f"{len(bad)}/{len(subjects)} subject(s) not conventional, first: {bad[0]!r}",
        )
    return GateResult(
        Stage.BUILD,
        "commits",
        True,
        f"{len(subjects)} conventional subject(s) (English is not machine-checked)",
    )


GATES = [
    GateSpec(Stage.BUILD, "tests", has_tests),
    GateSpec(Stage.BUILD, "lint", lint),
    GateSpec(Stage.BUILD, "secrets", secrets),
    GateSpec(Stage.BUILD, "commits", commits),
]
