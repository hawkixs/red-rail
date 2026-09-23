"""Stage 4 — build: static checks. Tests exist for the stack, a linter is configured, gitleaks
finds nothing in the history, commit subjects are conventional. Running the project's own
suite is CI's job (`make ci`); its exit code is the check, not this gate."""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from rail import gitrepo
from rail.gates import GateResult, GateSpec, Need, Stage
from rail.model import Stack, declarations
from rail.policy import effective

CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]+\))?!?: \S")
# What an emoji is made of: a symbol, then what qualifies or joins it — variation selector,
# skin tone, keycap, zero-width joiner. Unicode categories, so no new dependency.
_EMOJI_PARTS = frozenset({"So", "Sk", "Mn", "Me", "Cf"})


def _python_tests(repo: Path) -> list[Path]:
    root = repo / "tests"
    if not root.is_dir():
        return []
    return [
        p for p in root.rglob("*.py") if p.name.startswith("test_") or p.name.endswith("_test.py")
    ]


def _go_tests(repo: Path) -> list[Path]:
    return [p for p in repo.rglob("*_test.go") if "vendor" not in p.parts]


def _ruff_configured(repo: Path) -> bool:
    pyproject = repo / "pyproject.toml"
    return (pyproject.is_file() and "[tool.ruff" in pyproject.read_text()) or any(
        (repo / name).is_file() for name in ("ruff.toml", ".ruff.toml")
    )


def has_tests(repo: Path) -> GateResult:
    # named `has_tests`, not `tests`: pytest would collect a `tests` function on import
    decl = declarations(repo)
    if isinstance(decl, str):
        return GateResult(Stage.BUILD, "tests", False, decl)
    if decl.stack is None:
        python, go = len(_python_tests(repo)), len(_go_tests(repo))
        observed = (
            "no test file found (tests/test_*.py, *_test.go)"
            if not python and not go
            else f"{python} test file(s) (tests/test_*.py), {go} (*_test.go)"
        )
        return Need("stack", observed).result(Stage.BUILD, "tests")
    if decl.stack is Stack.DOCS:
        return GateResult(Stage.BUILD, "tests", True, "stack docs: no test suite required")
    if decl.stack is Stack.PYTHON:
        found, where = _python_tests(repo), "tests/test_*.py"
    else:
        found, where = _go_tests(repo), "*_test.go"
    if not found:
        return GateResult(Stage.BUILD, "tests", False, f"no test files ({where})")
    return GateResult(Stage.BUILD, "tests", True, f"{len(found)} test file(s)")


def lint(repo: Path) -> GateResult:
    decl = declarations(repo)
    if isinstance(decl, str):
        return GateResult(Stage.BUILD, "lint", False, decl)
    if decl.stack is None:
        ruff = "ruff configured" if _ruff_configured(repo) else "ruff not configured"
        go = "go.mod present" if (repo / "go.mod").is_file() else "no go.mod"
        return Need("stack", f"{ruff}, {go}").result(Stage.BUILD, "lint")
    if decl.stack is Stack.DOCS:
        return GateResult(Stage.BUILD, "lint", True, "stack docs: no linter required")
    if decl.stack is Stack.PYTHON:
        if _ruff_configured(repo):
            return GateResult(Stage.BUILD, "lint", True, "ruff configured")
        return GateResult(
            Stage.BUILD,
            "lint",
            False,
            "ruff is not configured ([tool.ruff] in pyproject.toml or ruff.toml)",
        )
    return _go_profile(repo)


# The Go analysers, pinned by `tool` directives in `go.mod` since Go 1.24 (`go get -tool`) so
# the module resolves one version for the workstation, CI and the release alike — never
# `@latest`. Measured on the red-alerts pilot: staticcheck v0.8.1, govulncheck v1.8.0.
GO_TOOLS: tuple[tuple[str, str], ...] = (
    ("staticcheck", "honnef.co/go/tools/cmd/staticcheck"),
    ("govulncheck", "golang.org/x/vuln/cmd/govulncheck"),
)


def _INVOKES(name: str) -> re.Pattern[str]:  # noqa: N802  (a pattern factory, read as a constant)
    """`go tool <name>` however the toolchain is spelled — `go`, `$(GO)`, `${GOCMD}`. The
    property is the invocation, not the list of ways to name the binary that performs it."""
    return re.compile(rf"\btool\s+{re.escape(name)}\b")


def _live_lines(text: str, *, comment: str) -> str:
    """`text` with commented-out content removed, so dead text never satisfies the gate: a
    `# go tool staticcheck ./...  # TODO re-enable` runs nothing and must not count as a
    call, and neither must a commented-out `tool (…)` block."""
    kept = []
    for line in text.splitlines():
        head = line.split(comment, 1)[0]
        if head.strip():
            kept.append(head)
    return "\n".join(kept)


def _recipe_lines(makefile: str) -> str:
    """Only what make actually runs: recipe lines are TAB-indented, and `#` starts a comment.
    A target named `staticcheck` that runs nothing, or a `.PHONY` listing it, is not a call."""
    return _live_lines(
        "\n".join(line for line in makefile.splitlines() if line.startswith("\t")), comment="#"
    )


def tool_directives(go_mod: str) -> set[str]:
    """The packages under a `tool` directive, in either legal form — `tool <package>` and a
    parenthesised `tool ( … )` block. Read as a directive rather than searched as text: a
    package left behind in `require` after the directive is gone is a dependency, not a
    declared analyser, and `go tool <name>` would not resolve."""
    packages: set[str] = set()
    in_block = False
    for line in _live_lines(go_mod, comment="//").splitlines():
        entry = line.strip()
        if in_block:
            if entry.startswith(")"):
                in_block = False
            elif entry:
                packages.add(entry.split()[0])
            continue
        if entry == "tool (" or entry.startswith("tool ("):
            in_block = True
            rest = entry[len("tool (") :].strip()
            if rest and not rest.startswith(")"):
                packages.add(rest.split()[0])
        elif entry.startswith("tool "):
            packages.add(entry[len("tool ") :].strip().split()[0])
    return packages


def _go_profile(repo: Path) -> GateResult:
    """The Go profile as a pure read: `go.mod` DECLARES the analysers, the task runner
    CALLS them, CI executes it. `go vet` and `gofmt` need no directive — they ship with the
    toolchain — so only the two pinned tools are checked here."""
    go_mod = repo / "go.mod"
    if not go_mod.is_file():
        return GateResult(Stage.BUILD, "lint", False, "go.mod is missing")
    declared = tool_directives(go_mod.read_text())
    undeclared = [name for name, package in GO_TOOLS if package not in declared]
    if undeclared:
        return GateResult(
            Stage.BUILD,
            "lint",
            False,
            f"go.mod declares no tool directive for {', '.join(undeclared)} "
            f"(`go get -tool {' '.join(p for n, p in GO_TOOLS if n in undeclared)}`)",
        )
    makefile = repo / "Makefile"
    if not makefile.is_file():
        return GateResult(Stage.BUILD, "lint", False, "Makefile is missing")
    runner = _recipe_lines(makefile.read_text())
    # `tool <name>`, not the bare name and not `go tool <name>`. The bare name is always
    # present — the template's own `sync` runs `go get -tool …/staticcheck@v0.8.1` — so
    # matching it would let anyone delete the real call and still pass. Requiring the literal
    # `go` rejected `$(GO) tool staticcheck`, which is the ordinary Makefile idiom and the
    # only option on a host with no Go toolchain, where `make GO=./scripts/go` runs it in a
    # container. Dropping the prefix cannot reopen the hole: after `-tool`, `go get` takes a
    # module path, never the bare name, so an installation line has no `tool <name>` in it.
    uncalled = [name for name, _ in GO_TOOLS if not _INVOKES(name).search(runner)]
    if uncalled:
        return GateResult(
            Stage.BUILD,
            "lint",
            False,
            f"go.mod pins {', '.join(name for name, _ in GO_TOOLS)} but the Makefile never "
            f"calls {', '.join(uncalled)} — a profile CI does not run is a profile on paper",
        )
    return GateResult(
        Stage.BUILD,
        "lint",
        True,
        f"{', '.join(name for name, _ in GO_TOOLS)} pinned by go.mod and called by the "
        "Makefile (gofmt and go vet ship with the toolchain and are not checked here)",
    )


# What a leak actually costs, said by the gate rather than discovered. `gitleaks git` reads
# the history through every ref, so the commit — not the file — is the thing to remove, and a
# rewrite that stops at the working tree leaves it reachable from `origin/*`. Measured on
# red-alerts (2026-09-21): ~20 minutes on a FALSE positive, most of it spent finding out that
# `rail check` stayed red on a commit `git branch --contains` no longer found.
LEAK_REMEDY = (
    "scanned across history and every ref, so editing the file at HEAD is not enough: "
    "rewrite the commits, then force-push, or the leak stays reachable from origin/*. "
    "Rotate the secret either way — pushed once is compromised"
)


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
        return GateResult(
            Stage.BUILD, "secrets", False, f"gitleaks found leaks: {last} — {LEAK_REMEDY}"
        )
    return GateResult(Stage.BUILD, "secrets", False, f"gitleaks failed (exit {code}): {last}")


def _without_leading_emoji(subject: str) -> str:
    """`subject` less one leading emoji and its space — the form `/git-commit` writes
    (decision d6a4cb7c). Anything else is returned whole, for the conventional form to judge."""
    head, _, rest = subject.partition(" ")
    if (
        head
        and unicodedata.category(head[0]) == "So"
        and all(unicodedata.category(char) in _EMOJI_PARTS for char in head)
    ):
        return rest
    return subject


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
        match = CONVENTIONAL.match(_without_leading_emoji(subject))
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
