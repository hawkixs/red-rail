"""Stage 4 — build: static checks on the repository, never running the project's own suite."""

import random
import shutil
import string
from pathlib import Path

import pytest

from rail.gates import Stage
from rail.gates import build as build_gates
from rail.gates.build import GATES, commits, has_tests, lint, secrets
from rail.model import Stack, Tier
from rail.scaffold import NewProject, render
from tests.helpers import commit_all, conforming_tree, init_repo, write_manifest

ROOT = Path(__file__).resolve().parents[1]

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
    assert has_tests(tmp_path).needs == "stack"


def test_without_a_manifest_tests_and_lint_need_the_stack_and_say_what_they_saw(
    tmp_path: Path,
) -> None:
    empty = has_tests(tmp_path)
    assert not empty.passed and empty.needs == "stack"
    assert "rail.yaml" not in empty.details
    assert "no test file found (tests/test_*.py, *_test.go)" in empty.details
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    found = has_tests(tmp_path)
    assert found.needs == "stack"
    assert "1 test file(s) (tests/test_*.py), 0 (*_test.go)" in found.details
    linted = lint(tmp_path)
    assert not linted.passed and linted.needs == "stack"
    assert "ruff configured, no go.mod" in linted.details and "rail.yaml" not in linted.details


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


@pytest.mark.parametrize(
    "subject",
    [
        "📝 docs(watcher): measure the exposure from the edge",
        "✨ feat: add a gate",
        "♻️ refactor(core): simplify the loader",  # a variation selector follows the symbol
        "🧑‍💻 chore: improve the developer loop",  # two symbols joined by a ZWJ
    ],
    ids=["memo", "sparkles", "variation-selector", "zwj-sequence"],
)
def test_commits_gate_accepts_one_leading_emoji(tmp_path: Path, subject: str) -> None:
    """`/git-commit` writes `<emoji> type(scope): subject`. The gate checks the conventional
    form, not the typography: one emoji before the type passes (decision d6a4cb7c), so the
    history already written that way passes without a rewrite."""
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    commit_all(repo, subject)

    result = commits(repo)

    assert result.passed, result.details


@pytest.mark.parametrize(
    "subject",
    [
        "📝 📝 docs: two emoji",
        "📝docs: no space after the emoji",
        "📝notes fix: a word glued to the emoji",
        "📝 update the docs",
        "- fix: a dash is not an emoji",
        "^ fix: a caret is a modifier symbol, not an emoji",
        "📝 wip: an unknown type",
    ],
    ids=["two-emoji", "no-space", "glued-word", "no-type", "dash", "caret", "unknown-type"],
)
def test_commits_gate_still_wants_the_conventional_form_after_the_emoji(
    tmp_path: Path, subject: str
) -> None:
    """One emoji, one space, then the whole conventional form: the emoji is the only thing
    the gate lets through."""
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    commit_all(repo, subject)

    result = commits(repo)

    assert not result.passed and subject in result.details


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
    bare.write_text("module example.invalid/red-beta\n\ngo 1.26.8\n")
    result = lint(go)
    assert not result.passed
    assert "staticcheck" in result.details and "govulncheck" in result.details
    assert "go get -tool" in result.details

    bare.write_text(
        "module example.invalid/red-beta\n\ngo 1.26.8\n\n"
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
        "module example.invalid/red-beta\n\ngo 1.26.8\n\n"
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
        "module example.invalid/red-beta\n\ngo 1.26.8\n\n"
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
        "module example.invalid/red-beta\n\ngo 1.26.8\n\n"
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


def test_a_leak_says_that_fixing_head_is_not_enough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gitleaks git` reads the history through every ref, so the fix is never "edit the
    file": the commit has to stop being reachable, remote-tracking refs included, and the
    secret is burnt either way. Measured on red-alerts: ~20 minutes on a FALSE positive,
    most of it spent discovering that `rail check` stayed red on a commit `git branch
    --contains` no longer found. The gate knew all of that and said none of it."""
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (2, "leaks found: 1"))
    details = secrets(tmp_path).details.lower()
    assert "history" in details, "say where it looked, or HEAD is assumed"
    assert "force-push" in details, "a local rewrite leaves the leak on origin/*"
    assert "rotate" in details, "a pushed secret is compromised whatever the history says"
    assert secrets(tmp_path).passed is False


@pytest.mark.parametrize("table", ["TEST_PROFILES", "LINT_PROFILES"])
def test_a_stack_without_a_profile_fails_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, table: str
) -> None:
    """A stack the rail does not know how to judge FAILs and says so, instead of being judged
    as Go (spec 2026-09-24-rust-stack, decision 10)."""
    repo = conforming_tree(tmp_path / "py", "red-alpha", "dev")
    profiles = dict(getattr(build_gates, table))
    del profiles[Stack.PYTHON]
    monkeypatch.setattr(build_gates, table, profiles)
    gate = has_tests if table == "TEST_PROFILES" else lint
    result = gate(repo)
    assert not result.passed
    assert result.details == "stack `python` has no build profile in this rail version"


def test_every_stack_has_a_build_profile() -> None:
    assert set(build_gates.TEST_PROFILES) == set(Stack)
    assert set(build_gates.LINT_PROFILES) == set(Stack)


def _rust_repo(root: Path) -> Path:
    """A manifest declaring rust, and nothing else: each test writes the crates it needs."""
    repo = init_repo(root)
    write_manifest(repo, project="red-life", tier="dev", stack="rust")
    return repo


def _crate(directory: Path, *tests: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Cargo.toml").write_text('[package]\nname = "x"\n')
    for test in tests:
        path = directory / "tests" / test
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#[test]\nfn t() {}\n")


def test_rust_tests_are_found_under_tests_dirs(tmp_path: Path) -> None:
    """Cargo's integration targets: `tests/*.rs` and `tests/<dir>/main.rs` beside every
    Cargo.toml, root package and members alike. `target/` and `.cargo-tools/` hold built or
    installed crates, not the project's tests (Review Focus 3)."""
    repo = _rust_repo(tmp_path / "red-life")
    _crate(repo, "smoke.rs", "it/main.rs")
    _crate(repo / "crates" / "engine", "rules.rs")
    _crate(repo / "target" / "package" / "x-0.1.0", "vendored.rs")
    _crate(repo / ".cargo-tools" / "src" / "y", "installed.rs")
    result = has_tests(repo)
    assert result.passed and result.details == "3 test file(s)", result.details


def test_a_rust_helper_module_alone_is_not_a_test(tmp_path: Path) -> None:
    repo = _rust_repo(tmp_path / "red-life")
    _crate(repo, "common/mod.rs")
    (repo / "src").mkdir()
    (repo / "src" / "main.rs").write_text("#[cfg(test)]\nmod tests {}\n")
    result = has_tests(repo)
    assert not result.passed and result.details == "no test files (tests/*.rs)"


def test_rust_repo_with_only_go_tests_fails(tmp_path: Path) -> None:
    repo = _rust_repo(tmp_path / "red-life")
    _crate(repo)
    (repo / "main_test.go").write_text("package main\n")
    assert not has_tests(repo).passed


def test_undeclared_stack_reports_rust_tests(tmp_path: Path) -> None:
    assert "no test file found (tests/test_*.py, *_test.go), none in tests/*.rs" in (
        has_tests(tmp_path).details
    )
    _crate(tmp_path, "smoke.rs")
    assert "0 test file(s) (tests/test_*.py), 0 (*_test.go), 1 (tests/*.rs)" in (
        has_tests(tmp_path).details
    )


def _rendered_rust(tmp_path: Path) -> Path:
    src = tmp_path / "template-src"
    src.mkdir()
    shutil.copy(ROOT / "copier.yml", src / "copier.yml")
    shutil.copytree(ROOT / "template", src / "template")
    dest = render(
        NewProject(
            slug="red-life",
            description="A disposable rust project.",
            tier=Tier.DEV,
            stack=Stack.RUST,
            brain_key="red-life",
            dest=tmp_path / "red-life",
            template=str(src),
        )
    )
    (dest / "Cargo.lock").write_text("version = 4\n")  # what `make sync` writes
    return dest


def test_rust_lint_passes_on_the_rendered_tree(tmp_path: Path) -> None:
    dest = _rendered_rust(tmp_path)
    result = lint(dest)
    assert result.passed, result.details
    assert "1.98.1" in result.details and "0.20.2" in result.details
    assert has_tests(dest).passed


def test_a_fresh_rust_scaffold_fails_lint_until_make_sync_writes_the_lock(tmp_path: Path) -> None:
    dest = _rendered_rust(tmp_path)
    (dest / "Cargo.lock").unlink()
    result = lint(dest)
    assert not result.passed and "Cargo.lock" in result.details and "make sync" in result.details


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not in {path.name}"
    path.write_text(text.replace(old, new))


@pytest.mark.parametrize(
    ("mutate", "named"),
    [
        (lambda d: (d / "deny.toml").unlink(), "deny.toml"),
        (lambda d: _replace(d / "rust-toolchain.toml", '"1.98.1"', '"stable"'), "channel"),
        (
            lambda d: _replace(d / "rust-toolchain.toml", '"rustfmt", "clippy"', '"rustfmt"'),
            "clippy",
        ),
        (lambda d: (d / "Cargo.lock").unlink(), "Cargo.lock"),
        (lambda d: (d / "Cargo.toml").unlink(), "Cargo.toml"),
        (
            lambda d: _replace(d / "Makefile", "\t$(CARGO) deny check", "\t# $(CARGO) deny check"),
            "deny check",
        ),
        (lambda d: _replace(d / "Makefile", " --version 0.20.2", ""), "cargo-deny"),
        (lambda d: _replace(d / "Makefile", " -- -D warnings", ""), "-D warnings"),
        (lambda d: _replace(d / "Makefile", "fmt --all --check", "fmt --all"), "fmt"),
    ],
    ids=[
        "no-deny-toml",
        "floating-channel",
        "no-clippy-component",
        "no-lock",
        "no-cargo-toml",
        "deny-check-commented-out",
        "cargo-deny-unpinned",
        "clippy-without-deny-warnings",
        "fmt-without-check",
    ],
)
def test_rust_lint_fails_and_names_what_is_missing(tmp_path: Path, mutate, named: str) -> None:
    dest = _rendered_rust(tmp_path)
    mutate(dest)
    result = lint(dest)
    assert not result.passed and named in result.details, result.details


@pytest.mark.parametrize("broken", ["rust-toolchain.toml", "deny.toml"])
def test_rust_lint_fails_and_names_the_broken_file(tmp_path: Path, broken: str) -> None:
    """A file that is not TOML is a FAIL naming it, never an exception (Review Focus 2)."""
    dest = _rendered_rust(tmp_path)
    (dest / broken).write_text("[toolchain\nchannel = \n")
    result = lint(dest)
    assert not result.passed and broken in result.details, result.details


@pytest.mark.parametrize(
    "toolchain_text",
    [
        'toolchain = "stable"\n',
        '[toolchain]\nchannel = "1.98.1"\ncomponents = "rustfmt"\n',
    ],
    ids=["toolchain-not-a-table", "components-not-a-list"],
)
def test_rust_lint_fails_and_names_the_file_when_toolchain_is_malformed(
    tmp_path: Path, toolchain_text: str
) -> None:
    """Valid TOML whose shape is wrong (`toolchain` not a table, or `components` not a list of
    strings) is a FAIL naming rust-toolchain.toml, never an AttributeError (the gate never
    raises)."""
    dest = _rendered_rust(tmp_path)
    (dest / "rust-toolchain.toml").write_text(toolchain_text)
    result = lint(dest)
    assert not result.passed and "rust-toolchain.toml" in result.details, result.details
