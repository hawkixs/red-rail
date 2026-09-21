"""Stage 4 — build: static checks on the repository, never running the project's own suite."""

import random
import shutil
import string
from pathlib import Path

import pytest

from rail.gates import Stage
from rail.gates import build as build_gates
from rail.gates.build import GATES, commits, has_tests, lint, secrets
from tests.helpers import commit_all, conforming_tree, init_repo

# `tests` is a gate function, not a pytest test: its name matches pytest's default
# `python_functions = test*` glob, so without this it gets collected and fails at
# setup (it needs a `repo` argument pytest cannot supply as a fixture).


def test_registry() -> None:
    assert [g.code for g in GATES] == ["tests", "lint", "secrets", "commits"]
    assert all(g.stage is Stage.BUILD for g in GATES)


def test_tests_gate_per_stack(tmp_path: Path) -> None:
    py = conforming_tree(tmp_path / "py", "red-alpha", "dev")
    assert has_tests(py).passed and "1 test file" in has_tests(py).details
    (py / "tests" / "test_smoke.py").unlink()
    assert not has_tests(py).passed
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    assert has_tests(go).passed
    docs = conforming_tree(tmp_path / "docs", "red-gamma", "dev", stack="docs")
    assert has_tests(docs).passed and "docs" in has_tests(docs).details
    assert "rail.yaml" in has_tests(tmp_path).details


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


def test_lint_gate_reads_the_go_tool_profile_without_running_it(tmp_path: Path) -> None:
    """Since Go 1.24 the analysers are pinned by `tool` directives in `go.mod`
    (`go get -tool`), so the repository DECLARES the profile and CI EXECUTES it — the gate
    stays a pure read, and is far stronger than "go.mod exists". Measured on the red-alerts
    pilot: staticcheck v0.8.1, govulncheck v1.8.0 resolved by the module, never `@latest`."""
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    assert lint(go).passed, lint(go).details

    bare = go / "go.mod"
    bare.write_text("module example.invalid/red-beta\n\ngo 1.26.6\n")
    result = lint(go)
    assert not result.passed
    assert "staticcheck" in result.details and "govulncheck" in result.details
    assert "go get -tool" in result.details

    bare.write_text(
        "module example.invalid/red-beta\n\ngo 1.26.6\n\n"
        "tool (\n\thonnef.co/go/tools/cmd/staticcheck\n)\n"
    )
    assert "govulncheck" in lint(go).details and not lint(go).passed


def test_lint_gate_wants_the_go_profile_wired_into_the_task_runner(tmp_path: Path) -> None:
    """Declaring the tools and never calling them is a profile on paper. The Makefile is
    what CI runs, so the gate reads it too — still without executing anything."""
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    (go / "Makefile").write_text("ci: lint test\nlint:\n\tgo vet ./...\ntest:\n\tgo test ./...\n")
    result = lint(go)
    assert not result.passed and "Makefile" in result.details
    assert "staticcheck" in result.details and "govulncheck" in result.details


def test_lint_gate_is_not_satisfied_by_commented_out_text(tmp_path: Path) -> None:
    """Substring containment over whole files let dead text pass: a commented-out recipe
    runs nothing, and a `.PHONY` line naming a tool is not a call. The gate reads what go
    and make actually act on."""
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")

    (go / "Makefile").write_text(
        ".PHONY: staticcheck govulncheck ci\n"
        "ci: lint\n"
        "lint:\n"
        "\tgo vet ./...\n"
        "\t# go tool staticcheck ./...  # TODO re-enable\n"
        "\t# go tool govulncheck ./...\n"
    )
    result = lint(go)
    assert not result.passed, result.details
    assert "staticcheck" in result.details and "govulncheck" in result.details

    (go / "go.mod").write_text(
        "module example.invalid/red-beta\n\ngo 1.26.6\n\n"
        "// tool (\n"
        "// \tgolang.org/x/vuln/cmd/govulncheck\n"
        "// \thonnef.co/go/tools/cmd/staticcheck\n"
        "// )\n"
    )
    assert not lint(go).passed and "go get -tool" in lint(go).details


def test_lint_gate_reports_a_missing_makefile_for_a_go_repository(tmp_path: Path) -> None:
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    (go / "Makefile").unlink()
    result = lint(go)
    assert not result.passed and "Makefile is missing" in result.details


def test_lint_gate_wants_the_tools_invoked_not_merely_mentioned(tmp_path: Path) -> None:
    """The template's own `sync` target runs `go get -tool …/staticcheck@v0.8.1`, so the bare
    name is always present in the recipes: matching it would let anyone delete the real
    `go tool staticcheck` call and still pass. The invocation is what counts."""
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    (go / "Makefile").write_text(
        ".PHONY: sync lint ci\n"
        "sync:\n"
        "\tgo get -tool honnef.co/go/tools/cmd/staticcheck@v0.8.1\n"
        "\tgo get -tool golang.org/x/vuln/cmd/govulncheck@v1.8.0\n"
        "lint:\n"
        "\tgo vet ./...\n"
        "ci: lint\n"
    )
    result = lint(go)
    assert not result.passed, result.details
    assert "staticcheck" in result.details and "govulncheck" in result.details


def test_lint_gate_wants_a_tool_directive_not_a_bare_require(tmp_path: Path) -> None:
    """A package path that no longer sits under a `tool` directive is a dependency, not a
    declared analyser: `go tool <name>` would not resolve. Dropping the `tool (` and `)`
    lines and leaving the paths behind must not satisfy the gate."""
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    (go / "go.mod").write_text(
        "module example.invalid/red-beta\n\ngo 1.26.6\n\n"
        "require (\n"
        "\tgolang.org/x/vuln/cmd/govulncheck v1.8.0 // indirect\n"
        "\thonnef.co/go/tools/cmd/staticcheck v0.8.1 // indirect\n"
        ")\n"
    )
    result = lint(go)
    assert not result.passed and "go get -tool" in result.details


def test_lint_gate_accepts_a_single_line_tool_directive(tmp_path: Path) -> None:
    """`tool <package>` without parentheses is the other legal form."""
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    (go / "go.mod").write_text(
        "module example.invalid/red-beta\n\ngo 1.26.6\n\n"
        "tool golang.org/x/vuln/cmd/govulncheck\n"
        "tool honnef.co/go/tools/cmd/staticcheck\n"
    )
    assert lint(go).passed, lint(go).details


@pytest.mark.parametrize(
    "recipe",
    [
        "\tgo tool staticcheck ./...\n\tgo tool govulncheck ./...\n",
        "\t$(GO) tool staticcheck ./...\n\t$(GO) tool govulncheck ./...\n",
        "\t${GO} tool staticcheck ./...\n\t${GO} tool govulncheck ./...\n",
        "\t$(GOCMD) tool staticcheck ./...\n\t$(GOCMD) tool govulncheck ./...\n",
    ],
)
def test_lint_gate_accepts_a_parameterised_toolchain(tmp_path: Path, recipe: str) -> None:
    """`GO ?= go` is the ordinary Makefile idiom, and on a host with no Go toolchain it is
    not a preference: red-alerts runs `make GO=./scripts/go ci`, a wrapper that executes Go
    in the release image. Matching `go tool <name>` literally rejected the parameterised
    form and accepted only the hard-coded one."""
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    (go / "Makefile").write_text(f".PHONY: lint ci\nlint:\n\tgo vet ./...\n{recipe}ci: lint\n")
    assert lint(go).passed, lint(go).details


def test_lint_gate_still_refuses_an_installation_line(tmp_path: Path) -> None:
    """Dropping the `go` prefix must not reopen the hole the independent reviewer found.
    It cannot, structurally: after `-tool`, `go get` takes a MODULE PATH, never the bare
    name, so `tool <name>` on a word boundary can never match an installation line."""
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    (go / "Makefile").write_text(
        ".PHONY: sync lint ci\n"
        "sync:\n"
        "\tgo get -tool honnef.co/go/tools/cmd/staticcheck@v0.8.1\n"
        "\tgo get -tool golang.org/x/vuln/cmd/govulncheck@v1.8.0\n"
        "lint:\n\tgo vet ./...\n"
        "ci: sync lint\n"
    )
    result = lint(go)
    assert not result.passed, result.details
    assert "staticcheck" in result.details and "govulncheck" in result.details
