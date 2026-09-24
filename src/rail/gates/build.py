"""Stage 4 — build: static checks. Tests exist for the stack, a linter is configured, gitleaks
finds nothing in the history, commit subjects are conventional. Running the project's own
suite is CI's job (`make ci`); its exit code is the check, not this gate."""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
import unicodedata
from collections.abc import Callable
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


_NOT_THE_PROJECT = {"target", ".cargo-tools"}  # built or installed crates, not the project's


def _rust_tests(repo: Path) -> list[Path]:
    """The targets Cargo discovers as integration tests, `tests/*.rs` and `tests/<dir>/main.rs`,
    beside every Cargo.toml (the root package and each member). A helper module such as
    `tests/common/mod.rs` is not a target (spec 2026-09-24-rust-stack, decision 11)."""
    found: list[Path] = []
    for manifest in sorted(repo.rglob("Cargo.toml")):
        if _NOT_THE_PROJECT & set(manifest.relative_to(repo).parts):
            continue
        tests = manifest.parent / "tests"
        if tests.is_dir():
            found += sorted(tests.glob("*.rs")) + sorted(tests.glob("*/main.rs"))
    return found


def _ruff_configured(repo: Path) -> bool:
    pyproject = repo / "pyproject.toml"
    return (pyproject.is_file() and "[tool.ruff" in pyproject.read_text()) or any(
        (repo / name).is_file() for name in ("ruff.toml", ".ruff.toml")
    )


NO_PROFILE = "stack `{stack}` has no build profile in this rail version"


def _counted(found: list[Path], where: str) -> GateResult:
    if not found:
        return GateResult(Stage.BUILD, "tests", False, f"no test files ({where})")
    return GateResult(Stage.BUILD, "tests", True, f"{len(found)} test file(s)")


def _python_test_profile(repo: Path) -> GateResult:
    return _counted(_python_tests(repo), "tests/test_*.py")


def _go_test_profile(repo: Path) -> GateResult:
    return _counted(_go_tests(repo), "*_test.go")


def _rust_test_profile(repo: Path) -> GateResult:
    return _counted(_rust_tests(repo), "tests/*.rs")


def _docs_test_profile(repo: Path) -> GateResult:
    return GateResult(Stage.BUILD, "tests", True, "stack docs: no test suite required")


def _python_lint(repo: Path) -> GateResult:
    if _ruff_configured(repo):
        return GateResult(Stage.BUILD, "lint", True, "ruff configured")
    return GateResult(
        Stage.BUILD,
        "lint",
        False,
        "ruff is not configured ([tool.ruff] in pyproject.toml or ruff.toml)",
    )


def _docs_lint(repo: Path) -> GateResult:
    return GateResult(Stage.BUILD, "lint", True, "stack docs: no linter required")


def has_tests(repo: Path) -> GateResult:
    # named `has_tests`, not `tests`: pytest would collect a `tests` function on import
    decl = declarations(repo)
    if isinstance(decl, str):
        return GateResult(Stage.BUILD, "tests", False, decl)
    if decl.stack is None:
        python, go, rust = len(_python_tests(repo)), len(_go_tests(repo)), len(_rust_tests(repo))
        observed = (
            "no test file found (tests/test_*.py, *_test.go), none in tests/*.rs"
            if not python and not go and not rust
            else f"{python} test file(s) (tests/test_*.py), {go} (*_test.go), {rust} (tests/*.rs)"
        )
        return Need("stack", observed).result(Stage.BUILD, "tests")
    profile = TEST_PROFILES.get(decl.stack)
    if profile is None:
        return GateResult(Stage.BUILD, "tests", False, NO_PROFILE.format(stack=decl.stack.value))
    return profile(repo)


def lint(repo: Path) -> GateResult:
    decl = declarations(repo)
    if isinstance(decl, str):
        return GateResult(Stage.BUILD, "lint", False, decl)
    if decl.stack is None:
        ruff = "ruff configured" if _ruff_configured(repo) else "ruff not configured"
        go = "go.mod present" if (repo / "go.mod").is_file() else "no go.mod"
        return Need("stack", f"{ruff}, {go}").result(Stage.BUILD, "lint")
    profile = LINT_PROFILES.get(decl.stack)
    if profile is None:
        return GateResult(Stage.BUILD, "lint", False, NO_PROFILE.format(stack=decl.stack.value))
    return profile(repo)


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


_EXACT_CHANNEL = re.compile(r"^\d+\.\d+\.\d+$")
# What the Makefile must run, however the toolchain is spelled (`cargo`, `$(CARGO)`): the call,
# read from live recipe lines only, never the bare tool name.
RUST_CALLS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("fmt --check", re.compile(r"\bfmt\b[^\n]*\s--check\b"), "$(CARGO) fmt --all --check"),
    (
        "clippy -D warnings",
        re.compile(r"\bclippy\b[^\n]*\s-D\s+warnings\b"),
        "$(CARGO) clippy --workspace --all-targets --all-features -- -D warnings",
    ),
    ("deny check", re.compile(r"\bdeny\s+check\b"), "$(CARGO) deny check"),
    (
        "a pinned cargo-deny install",
        re.compile(
            r"\binstall\s+cargo-deny\b"
            r"(?=[^\n]*\s--locked\b)"
            r"(?=[^\n]*\s--version\s+\d+\.\d+\.\d+\b)"
        ),
        "$(CARGO) install cargo-deny --locked --version X.Y.Z --root .cargo-tools",
    ),
)


def _toml(path: Path) -> dict | str:
    """The parsed file, or why it could not be read: the gate never raises."""
    try:
        return tomllib.loads(path.read_text())
    except FileNotFoundError:
        return f"{path.name} is missing"
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return f"{path.name} does not parse: {exc}"


def _rust_profile(repo: Path) -> GateResult:
    """The rust profile as a pure read, on the model of `_go_profile`: rust-toolchain.toml pins
    an exact toolchain with rustfmt and clippy, the lock and deny.toml exist, and the Makefile
    calls fmt, clippy, cargo-deny and a pinned install of it (spec 2026-09-24-rust-stack,
    decision 12). The gate never runs cargo; CI does."""

    def fail(why: str) -> GateResult:
        return GateResult(Stage.BUILD, "lint", False, why)

    toolchain = _toml(repo / "rust-toolchain.toml")
    if isinstance(toolchain, str):
        return fail(toolchain)
    pinned = toolchain.get("toolchain")
    if not isinstance(pinned, dict):
        return fail("rust-toolchain.toml has no [toolchain] table")
    channel = str(pinned.get("channel", ""))
    if not _EXACT_CHANNEL.match(channel):
        return fail(
            f"rust-toolchain.toml channel {channel!r} is not an exact version (X.Y.Z): a floating "
            "channel changes clippy's lints under a green project"
        )
    components = pinned.get("components", [])
    if not isinstance(components, list) or not all(isinstance(c, str) for c in components):
        components = []  # the wrong shape is treated as absent, never as a crash
    missing = sorted({"rustfmt", "clippy"} - set(components))
    if missing:
        return fail(f"rust-toolchain.toml components lack {', '.join(missing)}")
    if not (repo / "Cargo.toml").is_file():
        return fail("Cargo.toml is missing")
    if not (repo / "Cargo.lock").is_file():
        return fail("Cargo.lock is missing: run `make sync`, then commit it")
    deny = _toml(repo / "deny.toml")
    if isinstance(deny, str):
        return fail(deny)
    makefile = repo / "Makefile"
    if not makefile.is_file():
        return fail("Makefile is missing")
    try:
        text = makefile.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        return fail(f"Makefile could not be read: {exc}")
    runner = _recipe_lines(text)
    for what, pattern, remedy in RUST_CALLS:
        if not pattern.search(runner):
            return fail(f"the Makefile never runs {what} (`{remedy}`)")
    version = re.search(r"--version\s+(\d+\.\d+\.\d+)", runner)
    return GateResult(
        Stage.BUILD,
        "lint",
        True,
        f"toolchain {channel} pinned by rust-toolchain.toml; fmt, clippy and cargo-deny "
        f"{version.group(1) if version else '?'} called by the Makefile",
    )


# Every stack is routed explicitly: a stack with no entry FAILs with NO_PROFILE and is never
# judged as another stack (spec 2026-09-24-rust-stack, decision 10).
TEST_PROFILES: dict[Stack, Callable[[Path], GateResult]] = {
    Stack.PYTHON: _python_test_profile,
    Stack.GO: _go_test_profile,
    Stack.DOCS: _docs_test_profile,
    Stack.RUST: _rust_test_profile,
}
LINT_PROFILES: dict[Stack, Callable[[Path], GateResult]] = {
    Stack.PYTHON: _python_lint,
    Stack.GO: _go_profile,
    Stack.DOCS: _docs_lint,
    Stack.RUST: _rust_profile,
}


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
