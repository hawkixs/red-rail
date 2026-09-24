# Rust stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `rail new --stack rust` scaffolds a Rust project that builds, lints, tests and audits
its dependencies on the rail. The build gate judges it by a Rust profile instead of mistaking it
for Go. `rail-ci.yml` installs the toolchain the project pins. `rail upgrade --stack rust` moves
a `docs` project onto the stack.

**Architecture:** `Stack.RUST` joins the model and copier's choices. Two refusals keep it off
`tier: prod` for now, one in `NewProject.answers` and one in a copier validator. The build gate
routes through two explicit tables, `TEST_PROFILES` and `LINT_PROFILES`. A stack missing from a
table FAILs instead of falling through to Go. The rust profile stays a pure read: it checks the
toolchain file, the lock, `deny.toml` and the Makefile's recipe lines, and it never runs cargo.
Every stack chain in the template is marked and names every stack. `rust-toolchain.toml` is the
only version source, read by rustup locally and in CI. `rail upgrade --stack` hands copier one
more answer and never writes `rail.yaml` itself.

**Tech Stack:** Python 3.12, Click, copier 9 (Jinja2), PyYAML, `tomllib`, pytest, ruff; the
rendered project: Rust edition 2024, cargo, rustup, cargo-deny, GitHub Actions.

**Spec:** `docs/specs/2026-09-24-rust-stack.md`. Read it whole first. This plan cites its
decisions (D1–D15) and success criteria (C1–C12) by number. The spec's "Order of work" is followed
with one regrouping, argued in the pre-flight table below: each commit stays green.

Work in the worktree `.claude/worktrees/rust-stack`, branch `feat/hawixs/rust-stack`, from its
root. The branch already contains spec A's head (merged, not rebased: no force-push). Run
`env -u VIRTUAL_ENV uv sync --all-extras` once before Task 1.

## Values read on 2026-09-24 (D3, D5, D8, C10)

Read by a research pass on 2026-09-24. Use them verbatim; do not re-resolve them during
implementation.

| Pin | Value | Source |
|---|---|---|
| Rust stable (D3) | `1.98.1` | `static.rust-lang.org/dist/channel-rust-stable.toml` (2026-09-01) |
| rustup (D8) | `1.29.1` | `static.rust-lang.org/rustup/release-stable.toml` |
| rustup-init sha256, x86_64-unknown-linux-gnu | `dda7234360b7f578ca8b0ddcb80145646fa61a67c1720a5abc7051b35c9fcb71` | `…/rustup/archive/1.29.1/x86_64-unknown-linux-gnu/rustup-init.sha256` |
| cargo-deny (D5) | `0.20.2` | crates.io `max_stable_version` |
| actions/cache (D8) | `55cc8345863c7cc4c66a329aec7e433d2d1c52a9` (`v6.1.0`) | GitHub API, tag ref |
| `rust:1.98.1-slim` (C10 fallback) | `sha256:f47a8de237dcbb0b0ce1099901e60a89728e3d51f24e664b40e947171538ade7` | Docker Hub tag API |

cargo-deny 0.20 schema (its book, `checks/*/cfg.html`). `[advisories] version` is a no-op and is
not written. Vulnerabilities always fail. `yanked = "deny"`. `unmaintained = "workspace"` limits
unmaintained advisories to direct dependencies. `[licenses] unused-allowed-license = "allow"`.

## Global Constraints

- Python 3.12+, ruff `line-length = 100`, lint `select = ["E", "F", "I", "UP", "B"]`. Every task ends with `env -u VIRTUAL_ENV uv run ruff format src/ tests/`, then `env -u VIRTUAL_ENV uv run ruff check src/ tests/` and `env -u VIRTUAL_ENV uv run ruff format --check src/ tests/`, all clean.
- Always run uv and make as `env -u VIRTUAL_ENV …`: the host exports another project's virtual environment.
- A gate never raises. Every read error (missing file, `tomllib.TOMLDecodeError`, `OSError`, `UnicodeDecodeError`) becomes a FAIL result that names the file.
- red-rail's tests are static (D9): they render and read, and never run cargo, rustup or cargo-deny. `make -n` (dry run) is allowed: it prints recipes and executes nothing.
- Tests never read the developer's host files: no `~/.claude/skills`, no ReD root, no `sites.yaml`, no `~/.cargo`.
- No machine address in any tracked file outside RFC 5737 (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) and RFC 3849 (`2001:db8::/32`). No machine name, deploy account or ssh alias anywhere in the repository: not in code, tests, docs, commits or the pull request.
- In rendered guidance, cite a tool by its command (`cargo deny check`) or in plain text, never as a bare backticked kebab token such as `` `cargo-deny` ``: A's bare-skill extractor would read it as a skill (D7).
- The Python, Go and docs renders stay byte-identical: every template edit touches only the rust branch, a marker, or the `else` → `elif stack == 'docs'` rewrite (D7).
- Commits are conventional, in English, with a leading emoji, ending with the session's two trailer lines. An implementer without the Skill tool writes the message itself (`git commit -F <file>`).
- `env -u VIRTUAL_ENV make ci` is green at the end (Task 7), and its summary lines are read, never inferred.
- Never use `git stash`. To undo a temporary edit made to prove a test bites, use `git checkout -- <file>`.
- Out of scope, per the spec's non-goals: rust at `tier: prod` (image, release, deploy), project-specific toolchain needs (wasm, licence widenings), already-generated repositories, transitions other than out of `docs`, a prebuilt cargo-deny, caching `target/`, the Go and Python CI paths, `rail bind`.

## Review Focus

These five inputs are exercised by no spec criterion, and are the most likely to hurt a user.
Each one gets its test in the task that owns the code.

1. **A slug with hyphens, `red-throwaway`.** A person expects `src/main.rs` to print it and
   `tests/smoke.rs` to reach the binary through `env!("CARGO_BIN_EXE_red-throwaway")`: Cargo keeps
   the hyphen in a bin target's name. Task 3 asserts the exact `env!` string for a hyphenated slug.
2. **A `rust-toolchain.toml` that is not valid TOML, or a `deny.toml` that is not valid TOML.** A
   person expects `build.lint` to FAIL and name the file, never to raise. Task 4 tests both in
   `test_rust_lint_fails_and_names_the_broken_file`.
3. **A `target/` or `.cargo-tools/` directory holding a `Cargo.toml` with `tests/*.rs`** (a
   vendored or installed crate). A person expects it not to count as a project test. Task 4 tests
   it in `test_rust_tests_are_found_under_tests_dirs`.
4. **`rail upgrade --stack rust` on a tree with uncommitted changes.** A person expects the
   refusal to come from copier (a dirty tree is refused), with nothing half-written. Task 6 tests
   it in `test_upgrade_to_rust_refuses_a_dirty_tree`.
5. **`rail.yaml` missing or unparsable before `rail upgrade --stack`.** A person expects a
   ScaffoldError naming `rail.yaml`, not a traceback, with `update` never called. Task 6 tests it
   in `test_upgrade_refuses_when_answers_and_manifest_disagree`, one parametrised case each.

## Pre-flight: what each task produces and who consumes it

| # | Producer → consumer | Interface | Finding and ruling |
|---|---|---|---|
| 1 | Task 1 → Task 4 | `TEST_PROFILES`, `LINT_PROFILES`: `dict[Stack, Callable[[Path], GateResult]]`, `NO_PROFILE` message format | Task 4 adds the `RUST` entries and the completeness test. Task 1 has no completeness assertion, so that Task 2's `Stack.RUST` leaves Task 1's tests green. Between Tasks 2 and 4, a rust manifest meets the documented miss path: FAIL "no build profile". |
| 2 | Task 2 → Tasks 3, 4, 6 | `Stack.RUST`, `RUST_PROD_REFUSAL` (in `rail.scaffold`) | Task 6 reuses the constant for its `docs→rust at prod` refusal. |
| 3 | Task 2 → Task 3 | `tests/combinations.py`: `Combo`, `COMBINATIONS`, `TARGET_FAMILIES`, `EXCLUDED` | Task 3 renders rust combinations through the existing module fixture `renders`. |
| 4 | Task 2 → Task 3 | five marked chains (`STACK_CHAINS`), each with a rust branch | Spec order puts `Stack.RUST` (step 2) before the chains (step 3). A's marker test iterates `Stack`, so the chains must carry `rust` in the same commit as `Stack.RUST`. Ruling: Task 2 = D1 plus D6/D7's chains; Task 3 = D2–D5's project files. |
| 5 | Task 3 → Task 4 | the rendered rust tree (`Cargo.toml`, `rust-toolchain.toml`, `deny.toml`, `tests/smoke.rs`, Makefile) | Task 4's `test_rust_lint_passes_on_the_rendered_tree` reads it through a template copy. |
| 6 | Task 4 → Task 7 | `tests/helpers.py`: `conforming_tree(..., stack="rust")`, `RUST_MAKEFILE` | fixture trees for the gate tests |
| 7 | Task 6 → Task 7 | `upgrade(repo, *, stack: Stack | None = None, update=…, resolve=…) -> str`, CLI `rail upgrade --stack` | used by C10's host step |

Each task's own text: the tests it specifies call only symbols it creates or an earlier task
created, and every file is created before a later task edits it.

## Coverage

| Spec item | Task |
|---|---|
| D1 (stack, both refusals, COMBINATIONS) | 2 |
| D2 (files), D3 (toolchain), D4 (lock), D5 (cargo-deny, deny.toml) | 3 |
| D6 (Makefile branch), D7 (marked chains) | 2 (chains, Makefile rust branch), 3 (render assertions) |
| D8 (CI) | 5 |
| D9 (runners) | no code: the template passes no `runs-on` (asserted in Task 5) |
| D10 (tables) | 1, 4 |
| D11 (`_rust_tests`), D12 (`_rust_profile`) | 4 |
| D13 (`upgrade --stack`) | 6 |
| D14 (gates on B's files) | 7, C8 |
| D15 (delivery) | after the last task |
| C1 | 3 · C2: 2 · C3: 1, 4 · C4: 5 · C5: 6 · C6, C7, C8: 7 · C9–C12: after the last task |

---

### Task 1: The build gate routes by an explicit table

Spec step 1 (D10, C3's first two tests, re-stated for three stacks).

**Files:**
- Modify: `src/rail/gates/build.py`
- Test: `tests/test_gates_build.py`

**Interfaces:**
- Produces: `TEST_PROFILES: dict[Stack, Callable[[Path], GateResult]]`,
  `LINT_PROFILES: dict[Stack, Callable[[Path], GateResult]]`, and
  `NO_PROFILE = "stack `{stack}` has no build profile in this rail version"`. Python's and Go's
  bodies move in unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gates_build.py`:

```python
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
```

Add `from rail.model import Stack` to the imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_gates_build.py -q -k without_a_profile`
Expected: FAIL with `AttributeError: module 'rail.gates.build' has no attribute 'TEST_PROFILES'`.

- [ ] **Step 3: Move the stack bodies into tables**

In `src/rail/gates/build.py`, add `from collections.abc import Callable` to the imports. Replace
`has_tests` and `lint` with this, keeping `_go_profile` where it is. `_python_lint` below is the
current Python branch of `lint`, moved unchanged.

```python
NO_PROFILE = "stack `{stack}` has no build profile in this rail version"


def _counted(found: list[Path], where: str) -> GateResult:
    if not found:
        return GateResult(Stage.BUILD, "tests", False, f"no test files ({where})")
    return GateResult(Stage.BUILD, "tests", True, f"{len(found)} test file(s)")


def _python_test_profile(repo: Path) -> GateResult:
    return _counted(_python_tests(repo), "tests/test_*.py")


def _go_test_profile(repo: Path) -> GateResult:
    return _counted(_go_tests(repo), "*_test.go")


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
        python, go = len(_python_tests(repo)), len(_go_tests(repo))
        observed = (
            "no test file found (tests/test_*.py, *_test.go)"
            if not python and not go
            else f"{python} test file(s) (tests/test_*.py), {go} (*_test.go)"
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
```

After `_go_profile`'s definition, add the tables. They sit below every function they name, and
`has_tests`/`lint` read them at call time.

```python
# Every stack is routed explicitly: a stack with no entry FAILs with NO_PROFILE and is never
# judged as another stack (spec 2026-09-24-rust-stack, decision 10).
TEST_PROFILES: dict[Stack, Callable[[Path], GateResult]] = {
    Stack.PYTHON: _python_test_profile,
    Stack.GO: _go_test_profile,
    Stack.DOCS: _docs_test_profile,
}
LINT_PROFILES: dict[Stack, Callable[[Path], GateResult]] = {
    Stack.PYTHON: _python_lint,
    Stack.GO: _go_profile,
    Stack.DOCS: _docs_lint,
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_gates_build.py -q`
Expected: PASS. Every existing Go and Python build test stays green unchanged.

- [ ] **Step 5: Lint, format, full suite**

Run: `env -u VIRTUAL_ENV uv run ruff format src/ tests/ && env -u VIRTUAL_ENV uv run ruff check src/ tests/ && env -u VIRTUAL_ENV uv run ruff format --check src/ tests/ && env -u VIRTUAL_ENV uv run pytest -q`
Expected: all clean, the pytest summary line reports every test passing.

- [ ] **Step 6: Commit**

```bash
git add src/rail/gates/build.py tests/test_gates_build.py
git commit  # ♻️ refactor(build): route each stack through an explicit profile table
```

---

### Task 2: `rust` is a stack, refused at prod, and every stack chain names it

Spec step 2, plus the chain half of step 3 (D1, D6, D7; C2). Ruling in the pre-flight table, row
4: A's marker test iterates `Stack`, so the chains take their rust branch in the same commit as
`Stack.RUST`.

**Files:**
- Modify: `src/rail/model.py`, `src/rail/scaffold.py`, `copier.yml`
- Modify: `template/project/Makefile.jinja`, `template/project/.gitignore.jinja`,
  `template/project/.claude/settings.json.jinja`, `template/project/CLAUDE.md.jinja`,
  `template/project/AGENTS.md.jinja`
- Create: `tests/combinations.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: `Combo`, `COMBOS`, `TARGET_FAMILIES`, `STACK_LINE`, `STACK_GATES`, `STACK_CHAINS`,
  `renders` (A, in `tests/test_scaffold.py`).
- Produces: `Stack.RUST = "rust"`; `rail.scaffold.RUST_PROD_REFUSAL: str`; `tests/combinations.py`
  with `Combo`, `TARGET_FAMILIES`, `EXCLUDED = frozenset({(Stack.RUST, Tier.PROD)})`,
  `COMBINATIONS: list[Combo]`. `COMBOS` disappears: every use in `tests/test_scaffold.py` reads
  `COMBINATIONS`.

- [ ] **Step 0: Record the three baselines**

Before any edit, render python (bootstrap and prod), go and docs from the current working tree
into a directory outside the worktree:

```bash
B=$(mktemp -d); OUT="$B"
env -u VIRTUAL_ENV uv run python - "$OUT" <<'EOF'
"""Render the working tree's template, from a plain copy as the tests do (copier would render
the last tag of a git source)."""
import shutil
import sys
import tempfile
from pathlib import Path

from rail.model import Stack, Tier
from rail.scaffold import NewProject, render

out, src = Path(sys.argv[1]), Path(tempfile.mkdtemp())
shutil.copy("copier.yml", src / "copier.yml")
shutil.copytree("template", src / "template")
for stack, tier in ((Stack.PYTHON, Tier.BOOTSTRAP), (Stack.PYTHON, Tier.PROD),
                    (Stack.GO, Tier.BOOTSTRAP), (Stack.DOCS, Tier.BOOTSTRAP)):
    render(NewProject(slug="red-probe", description="probe", tier=tier, stack=stack,
                      brain_key="red-probe", dest=out / f"{stack.value}-{tier.value}",
                      template=str(src)))
shutil.rmtree(src)
EOF
echo "$B"
```

Step 6 compares against these trees.

- [ ] **Step 1: Move the combinations to their own module**

Create `tests/combinations.py`:

```python
"""The template's answer sets, shared by every test that renders "every combination" (spec
2026-09-24-template-alignment, decisions 10, 14 and 15; spec 2026-09-24-rust-stack, decision 1).
One list, one exclusion: rust at tier prod is not templated yet."""

from typing import NamedTuple

from rail.model import DeployTarget, LedgerBackend, Stack, Tier

TARGET_FAMILIES = (DeployTarget.VPS_TRAEFIK.value, DeployTarget.PRIVATE_COMPOSE.value)
EXCLUDED = frozenset({(Stack.RUST, Tier.PROD)})


class Combo(NamedTuple):
    """One answer set of the template. Every guidance test reads the same renders."""

    stack: Stack
    tier: Tier
    ledger: LedgerBackend
    target: str | None = None  # prod only: one target per family, public and private
    brain_key: str = "red-probe"

    @property
    def label(self) -> str:
        parts = [self.stack.value, self.tier.value, self.ledger.value]
        parts += [self.target] if self.target else []
        parts += [self.brain_key] if self.brain_key != "red-probe" else []
        return "-".join(parts)


COMBINATIONS = [
    Combo(stack, tier, ledger, target)
    for stack in Stack
    for tier in Tier
    if (stack, tier) not in EXCLUDED
    for ledger in LedgerBackend
    for target in (TARGET_FAMILIES if tier is Tier.PROD else (None,))
] + [Combo(Stack.PYTHON, Tier.BOOTSTRAP, LedgerBackend.FILE, brain_key="red_probe")]
```

In `tests/test_scaffold.py`, delete `TARGET_FAMILIES`, `class Combo` and `COMBOS`, add
`from tests.combinations import COMBINATIONS, EXCLUDED, Combo`, and replace every `COMBOS` with
`COMBINATIONS` (`sed -i 's/\bCOMBOS\b/COMBINATIONS/g' tests/test_scaffold.py`). Run
`env -u VIRTUAL_ENV uv run pytest tests/test_scaffold.py -q` and expect PASS: this step is a
move, not a change.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_scaffold.py`:

```python
def test_rust_at_prod_is_the_only_excluded_combination() -> None:
    """Every stack × tier pair is rendered by some combination, except exactly (rust, prod)."""
    covered = {(c.stack, c.tier) for c in COMBINATIONS}
    assert {(s, t) for s in Stack for t in Tier} - covered == {(Stack.RUST, Tier.PROD)}
    assert EXCLUDED == {(Stack.RUST, Tier.PROD)}


def test_rust_at_prod_is_refused(template_dir: Path, tmp_path: Path) -> None:
    """Refused before copier by `rail new` (a bare message, not wrapped in "copier could not
    render"), and by copier's own validator for a direct run (decision 1)."""
    import copier

    from rail.scaffold import RUST_PROD_REFUSAL

    project = _project(
        template_dir, tmp_path / "red-life", slug="red-life", stack=Stack.RUST, tier=Tier.PROD
    )
    with pytest.raises(ScaffoldError) as raised:
        project.answers
    assert str(raised.value) == RUST_PROD_REFUSAL
    with pytest.raises(Exception, match="rust at tier prod is not templated yet"):
        copier.run_copy(
            str(template_dir),
            tmp_path / "direct",
            data={
                "project": "red-life",
                "description": "d",
                "brain_key": "red-life",
                "tier": "prod",
                "stack": "rust",
                "deploy_target": "vps-traefik",
                "healthcheck": "https://life.example.invalid/healthz",
            },
            defaults=True,
            quiet=True,
            unsafe=False,
        )
    assert not (tmp_path / "direct" / "rail.yaml").exists()


def test_the_copier_validator_says_what_rail_new_says() -> None:
    from rail.scaffold import RUST_PROD_REFUSAL

    assert RUST_PROD_REFUSAL in (ROOT / "copier.yml").read_text()


@pytest.mark.parametrize("combo", COMBINATIONS, ids=[c.label for c in COMBINATIONS])
def test_every_rendered_settings_file_parses_and_allows_cargo_for_rust_only(
    renders: dict[Combo, Path], combo: Combo
) -> None:
    allow = json.loads((renders[combo] / ".claude" / "settings.json").read_text())
    assert ("Bash(cargo:*)" in allow["permissions"]["allow"]) is (combo.stack is Stack.RUST)
```

Extend the stack tables, beside `STACK_LINE`:

```python
STACK_LINE = {
    Stack.PYTHON: "Python 3.12+, uv, pytest, ruff.",
    Stack.GO: "Go 1.22+, `go test`, `go vet`, `gofmt`.",
    Stack.RUST: (
        "Rust, edition 2024, toolchain pinned by `rust-toolchain.toml`: `cargo fmt`, clippy with "
        "`-D warnings`, `cargo test --locked`, `cargo deny check`."
    ),
    Stack.DOCS: "Documentation only (Markdown).",
}
STACK_CHAINS = {
    "CLAUDE.md.jinja": {"stack", "structure"},
    "AGENTS.md.jinja": {"gates"},
    "Makefile.jinja": {"makefile"},
    ".gitignore.jinja": {"gitignore"},
    ".claude/settings.json.jinja": {"settings"},
}
STACK_GATES = {
    Stack.PYTHON: "`uv run pytest -q`",
    Stack.GO: "`go test -race -count=1 ./...`",
    Stack.RUST: "`cargo test --workspace --locked`",
    Stack.DOCS: "no stack command of its own",
}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_scaffold.py -q -x`
Expected: FAIL. `Stack.RUST` does not exist (`AttributeError` at `STACK_LINE`).

- [ ] **Step 4: Add the stack and both refusals**

`src/rail/model.py`:

```python
class Stack(StrEnum):
    PYTHON = "python"
    GO = "go"
    RUST = "rust"
    DOCS = "docs"
```

`src/rail/scaffold.py`, after `ANSWERS_FILE`:

```python
# Rust at tier prod needs a release image and a service the template does not carry yet
# (spec 2026-09-24-rust-stack, decision 1). copier.yml's validator on `stack` says the same.
RUST_PROD_REFUSAL = (
    "rust at tier prod is not templated yet (no image, no service): scaffold at dev and "
    "promote when the rust prod template lands"
)
```

At the top of `NewProject.answers`, before `data` is built:

```python
        if self.stack is Stack.RUST and self.tier is Tier.PROD:
            raise ScaffoldError(RUST_PROD_REFUSAL)
```

`copier.yml`, the `stack` question (`tier` is asked first, so the validator sees it). Keep the
message on one line so the parity test finds it:

```yaml
stack:
  type: str
  choices: [python, go, rust, docs]
  default: python
  validator: >-
    {% if stack == 'rust' and tier == 'prod' %}
    rust at tier prod is not templated yet (no image, no service): scaffold at dev and promote when the rust prod template lands
    {% endif %}
```

- [ ] **Step 5: Mark every chain and give it its rust branch**

Every chain below carries A's markers, names every stack, and has no catch-all `else`. Edit only
the marker lines, the rust branch, and the Makefile's `else`. Nothing a python, go or docs render
prints may change.

`template/project/Makefile.jinja`:

- Line 3 becomes
  `.PHONY: sync lint test check ci serve image{% if stack == 'go' %} vuln{% elif stack == 'rust' %} deny{% endif %}`.
- `{# stack-chain: makefile -#}` goes on its own line directly above `{% if stack == 'python' -%}`.
- `{% else -%}` (the docs branch) becomes `{% elif stack == 'docs' -%}`.
- Insert the rust branch before it:

```
{% elif stack == 'rust' -%}
## The one way every target reaches the toolchain. On a host with no Rust installed,
## `make CARGO=<wrapper> ci` runs all of them through the wrapper — a container, for instance.
CARGO ?= cargo
## CI runs `make sync LOCKED=--locked`: a missing or stale Cargo.lock fails there instead of
## being written. Empty here, so `cargo fetch` writes the lock, or refreshes a stale one.
LOCKED ?=
## cargo-deny lives in this project's .cargo-tools, never in the shared $CARGO_HOME/bin: two
## projects pinning different versions would replace each other's binary on every sync.
export PATH := $(CURDIR)/.cargo-tools/bin:$(PATH)

## Fetch the dependencies (writes Cargo.lock), then install the pinned cargo-deny.
## Reinstalling the installed version is a no-op.
sync:
	$(CARGO) fetch $(LOCKED)
	$(CARGO) install cargo-deny --locked --version 0.20.2 --root .cargo-tools

## Format check, then clippy with warnings as errors, on every target of every member
lint:
	$(CARGO) fmt --all --check
	$(CARGO) clippy --workspace --all-targets --all-features -- -D warnings

## Run the test suite against the committed lock
test:
	$(CARGO) test --workspace --locked

## Licences, advisories, bans and sources, as deny.toml declares them
deny:
	$(CARGO) deny check
```

- `{% endif %}` closing the chain is followed by `{#- /stack-chain #}` on the next line.
- The last line becomes
  `ci: lint test {% if stack == 'go' %}vuln {% elif stack == 'rust' %}deny {% endif %}check`.

Recipe lines start with a TAB.

`template/project/.gitignore.jinja` becomes:

```
{# stack-chain: gitignore -#}
{% if stack == 'python' -%}
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.venv/
build/
dist/
*.egg-info/
{% elif stack == 'go' -%}
bin/
{% elif stack == 'rust' -%}
target/
.cargo-tools/
{% elif stack == 'docs' -%}
{% endif -%}
{#- /stack-chain -#}
.env
```

`template/project/.claude/settings.json.jinja`, the allow list:

```
      "Bash(rail:*)"{# stack-chain: settings #}{% if stack == 'python' %},
      "Bash(uv sync:*)",
      "Bash(uv run:*)"{% elif stack == 'go' %},
      "Bash(go:*)"{% elif stack == 'rust' %},
      "Bash(cargo:*)"{% elif stack == 'docs' %}{% endif %}{# /stack-chain #}
```

`template/project/CLAUDE.md.jinja`, chain `stack`, before `{%- elif stack == 'docs' -%}`:

```
{%- elif stack == 'rust' -%}
Rust, edition 2024, toolchain pinned by `rust-toolchain.toml`: `cargo fmt`, clippy with `-D warnings`, `cargo test --locked`, `cargo deny check`.
```

Chain `structure`, before `{%- elif stack == 'docs' %}`:

```
{%- elif stack == 'rust' %}
├── Cargo.toml         # a package and an empty [workspace]: a member is one line away
├── Cargo.lock         # written by `make sync`, committed
├── rust-toolchain.toml # the one toolchain pin
├── deny.toml          # licences, advisories, bans, sources
├── src/main.rs
└── tests/
```

`template/project/AGENTS.md.jinja`, chain `gates`, before `{%- elif stack == 'docs' -%}`:

```
{%- elif stack == 'rust' -%}
Run `make sync` first: it writes `Cargo.lock` and installs the pinned cargo-deny into
`.cargo-tools/`. Then `make ci`. This stack's own commands: `cargo fmt --all --check`,
`cargo clippy --workspace --all-targets --all-features -- -D warnings`,
`cargo test --workspace --locked`, `cargo deny check`; on a host with no Rust installed,
`make CARGO=<wrapper> ci` runs them all through the wrapper. A new licence or source is a
reviewed line in `deny.toml`.
```

- [ ] **Step 6: Run the tests, then prove the other renders did not move**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_scaffold.py -q`
Expected: PASS: A's parity, marker and guidance tests over `COMBINATIONS`, rust included, and the
four new tests.

Then render the same four trees from the edited working tree, with the same script, and compare:

```bash
A=$(mktemp -d); OUT="$A"
env -u VIRTUAL_ENV uv run python - "$OUT" <<'EOF'
"""Render the working tree's template, from a plain copy as the tests do (copier would render
the last tag of a git source)."""
import shutil
import sys
import tempfile
from pathlib import Path

from rail.model import Stack, Tier
from rail.scaffold import NewProject, render

out, src = Path(sys.argv[1]), Path(tempfile.mkdtemp())
shutil.copy("copier.yml", src / "copier.yml")
shutil.copytree("template", src / "template")
for stack, tier in ((Stack.PYTHON, Tier.BOOTSTRAP), (Stack.PYTHON, Tier.PROD),
                    (Stack.GO, Tier.BOOTSTRAP), (Stack.DOCS, Tier.BOOTSTRAP)):
    render(NewProject(slug="red-probe", description="probe", tier=tier, stack=stack,
                      brain_key="red-probe", dest=out / f"{stack.value}-{tier.value}",
                      template=str(src)))
shutil.rmtree(src)
EOF
for t in python-bootstrap python-prod go-bootstrap docs-bootstrap; do diff -r -x .copier-answers.yml "$B/$t" "$A/$t" && echo "$t identical"; done
```

The answers file names the temporary template path and is excluded.
Expected: four `… identical` lines. Record them in the report. Keep `$A` and `$B` for Step 7.

- [ ] **Step 7: Prove the chain test bites**

The file holds this task's uncommitted edits, so restoring it from git would lose them: save a
copy instead. Run `cp template/project/CLAUDE.md.jinja "$B/claude.bak"`, delete the
`{%- elif stack == 'rust' %}` branch of chain `structure` (the branch line and its six entries),
and run `env -u VIRTUAL_ENV uv run pytest tests/test_scaffold.py -q -k every_stack_chain`.
Expected: FAIL `chain 'structure': no branch for ['rust']`. Restore with
`cp "$B/claude.bak" template/project/CLAUDE.md.jinja` and re-run the test: PASS. Then
`rm -rf "$A" "$B"`.

- [ ] **Step 8: Lint, format, full suite**

Run: `env -u VIRTUAL_ENV uv run ruff format src/ tests/ && env -u VIRTUAL_ENV uv run ruff check src/ tests/ && env -u VIRTUAL_ENV uv run ruff format --check src/ tests/ && env -u VIRTUAL_ENV uv run pytest -q`
Expected: all clean, every test passing.

- [ ] **Step 9: Commit**

```bash
git add src/rail/model.py src/rail/scaffold.py copier.yml template/project tests/combinations.py tests/test_scaffold.py
git commit  # ✨ feat(template): rust is a stack, refused at prod, and every stack chain names it
```

---

### Task 3: A rust scaffold carries a package, a pinned toolchain and a strict deny.toml

Spec step 3, the files half (D2, D3, D4, D5, D6's recipes; C1; Review Focus 1).

**Files:**
- Create: `template/project/{% if stack == 'rust' %}Cargo.toml{% endif %}.jinja`
- Create: `template/project/{% if stack == 'rust' %}src{% endif %}/main.rs.jinja`
- Create: `template/project/{% if stack == 'rust' %}tests{% endif %}/smoke.rs.jinja`
- Create: `template/project/{% if stack == 'rust' %}rust-toolchain.toml{% endif %}.jinja`
- Create: `template/project/{% if stack == 'rust' %}deny.toml{% endif %}.jinja`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: `Stack.RUST`, `_project`, `template_dir` (Task 2 and before).
- Produces: the rendered rust tree Task 4 reads.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scaffold.py`:

```python
RUST_TOOLCHAIN = "1.98.1"  # latest stable on 2026-09-24 (spec 2026-09-24-rust-stack, decision 3)
DENY_LICENCES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unicode-3.0"}


def _dry_run(dest: Path, *args: str) -> list[str]:
    """`make -n`: the recipes make would run, in order, executing none of them."""
    make = shutil.which("make")
    assert make, "make is required: the Makefile is read by make itself"
    done = subprocess.run(
        [make, "-n", *args], cwd=dest, capture_output=True, text=True, check=True
    )
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def _after(lines: list[str], *needles: str) -> None:
    """Each needle appears in a later line than the previous one."""
    position = -1
    for needle in needles:
        index = next((i for i, line in enumerate(lines) if i > position and needle in line), None)
        assert index is not None, f"{needle!r} missing or out of order in {lines}"
        position = index


def test_render_rust_bootstrap(template_dir: Path, tmp_path: Path) -> None:
    import tomllib

    dest = render(
        _project(
            template_dir, tmp_path / "red-throwaway", slug="red-throwaway", stack=Stack.RUST
        )
    )

    cargo = tomllib.loads((dest / "Cargo.toml").read_text())
    assert cargo["package"]["name"] == "red-throwaway"
    assert cargo["package"]["edition"] == "2024"
    assert cargo["package"]["publish"] is False
    assert cargo["workspace"] == {}
    assert "rust-version" not in cargo["package"]

    toolchain = tomllib.loads((dest / "rust-toolchain.toml").read_text())["toolchain"]
    assert toolchain["channel"] == RUST_TOOLCHAIN
    assert re.fullmatch(r"\d+\.\d+\.\d+", toolchain["channel"])
    assert {"rustfmt", "clippy"} <= set(toolchain["components"])
    assert toolchain["profile"] == "minimal"

    deny = tomllib.loads((dest / "deny.toml").read_text())
    assert set(deny["licenses"]["allow"]) == DENY_LICENCES
    assert deny["licenses"]["private"]["ignore"] is True
    assert deny["licenses"]["unused-allowed-license"] == "allow"
    assert deny["sources"]["unknown-registry"] == "deny"
    assert deny["sources"]["unknown-git"] == "deny"
    assert deny["advisories"]["yanked"] == "deny"
    assert deny["advisories"]["unmaintained"] == "workspace"
    assert deny["bans"]["wildcards"] == "deny"
    assert deny["bans"]["allow-wildcard-paths"] is True
    assert deny["bans"]["multiple-versions"] == "warn"

    # Review Focus 1: Cargo keeps the hyphen in a bin target's name
    smoke = (dest / "tests" / "smoke.rs").read_text()
    assert 'env!("CARGO_BIN_EXE_red-throwaway")' in smoke
    assert '"red-throwaway"' in (dest / "src" / "main.rs").read_text()

    ignored = (dest / ".gitignore").read_text().splitlines()
    assert "target/" in ignored and ".cargo-tools/" in ignored
    assert not any("Cargo.lock" in line for line in ignored)

    claude = (dest / "CLAUDE.md").read_text()
    stack = claude.split("## Stack", 1)[1].split("## Commands", 1)[0]
    for needle in ("edition 2024", "rust-toolchain.toml", "-D warnings"):
        assert needle in stack, needle
    assert "cargo test --locked" in stack and "cargo deny check" in stack
    structure = claude.split("## Structure", 1)[1]
    for entry in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "deny.toml", "src/main.rs"):
        assert f"── {entry}" in structure, entry
    assert "└── tests/" in structure
    agents = (dest / "AGENTS.md").read_text()
    gates = agents.split("## Gates", 1)[1].split("## Brain MCP", 1)[0]
    assert gates.index("`make sync` first") < gates.index("Then `make ci`")

    assert not (dest / "pyproject.toml").exists() and not (dest / "go.mod").exists()
    assert not (dest / "src" / "red_throwaway" / "__init__.py").exists()

    _after(
        _dry_run(dest, "ci", "RAIL_FLAGS=--ci"),
        "fmt --all --check",
        "clippy --workspace --all-targets --all-features -- -D warnings",
        "test --workspace --locked",
        "deny check",
        "rail check --ci",
    )
    _after(
        _dry_run(dest, "sync"),
        "cargo fetch",
        "cargo install cargo-deny --locked --version 0.20.2 --root .cargo-tools",
    )
    locked = _dry_run(dest, "sync", "LOCKED=--locked")
    assert any(line.startswith("cargo fetch --locked") for line in locked)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_scaffold.py -q -k render_rust_bootstrap`
Expected: FAIL with `FileNotFoundError` on `Cargo.toml`.

- [ ] **Step 3: Create the five files**

`template/project/{% if stack == 'rust' %}Cargo.toml{% endif %}.jinja`:

```toml
[package]
name = "{{ project }}"
version = "0.1.0"
edition = "2024"
publish = false

# A package that is also a workspace: a member is one line in `members`, and the Makefile's
# `--workspace` already covers it. No `rust-version`: rust-toolchain.toml is the one pin.
[workspace]

[dependencies]
```

`template/project/{% if stack == 'rust' %}src{% endif %}/main.rs.jinja`:

```rust
fn main() {
    println!("{{ project }}");
}
```

`template/project/{% if stack == 'rust' %}tests{% endif %}/smoke.rs.jinja`:

```rust
//! The binary runs and names its project: an integration test, since a binary crate has no
//! library to import.

use std::process::Command;

#[test]
fn the_binary_runs_and_names_the_project() {
    let output = Command::new(env!("CARGO_BIN_EXE_{{ project }}"))
        .output()
        .expect("the binary runs");
    assert!(output.status.success());
    assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "{{ project }}");
}
```

`template/project/{% if stack == 'rust' %}rust-toolchain.toml{% endif %}.jinja`:

```toml
# The one toolchain pin: rustup reads it on the workstation and in CI, and Cargo.toml names no
# version. Latest stable on 2026-09-24; a bump is this line plus `rail upgrade`.
[toolchain]
channel = "1.98.1"
components = ["rustfmt", "clippy"]
profile = "minimal"
```

`template/project/{% if stack == 'rust' %}deny.toml{% endif %}.jinja` (cargo-deny 0.20 schema):

```toml
# Strict by default. Every widening, a licence, a source or an advisory ignored, is a reviewed
# line here.

[advisories]
# vulnerabilities always fail; a yanked crate fails too
yanked = "deny"
# unmaintained crates fail for direct dependencies only: a deep transitive one is not ours to fix
unmaintained = "workspace"

[licenses]
allow = ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unicode-3.0"]
# this project's own crates are `publish = false` and carry no licence
private = { ignore = true }
unused-allowed-license = "allow"

[bans]
wildcards = "deny"
# members are path dependencies
allow-wildcard-paths = true
multiple-versions = "warn"

[sources]
unknown-registry = "deny"
unknown-git = "deny"
allow-registry = ["https://github.com/rust-lang/crates.io-index"]
```

The python stack has `{% if stack == 'python' %}src{% endif %}` and
`{% if stack == 'python' %}tests{% endif %}` directories. The rust ones are separate directories
beside them, and only one renders.

- [ ] **Step 4: Run the test to verify it passes**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_scaffold.py -q`
Expected: PASS, including every guidance test over the rust combinations.

- [ ] **Step 5: Lint, format, full suite**

Run: `env -u VIRTUAL_ENV uv run ruff format src/ tests/ && env -u VIRTUAL_ENV uv run ruff check src/ tests/ && env -u VIRTUAL_ENV uv run ruff format --check src/ tests/ && env -u VIRTUAL_ENV uv run pytest -q`
Expected: all clean, every test passing. A's address and skill tests run over the rust renders:
the versions have three parts and are not addresses, and no bare kebab token is cited.

- [ ] **Step 6: Commit**

```bash
git add template/project tests/test_scaffold.py
git commit  # ✨ feat(template): a rust scaffold with a pinned toolchain, a lock and a strict deny.toml
```

---

### Task 4: The build gate judges a rust repository by its own profile

Spec step 4 (D10's completeness and message, D11, D12; C3; Review Focus 2 and 3).

**Files:**
- Modify: `src/rail/gates/build.py`
- Modify: `tests/helpers.py`
- Test: `tests/test_gates_build.py`

**Interfaces:**
- Consumes: `TEST_PROFILES`, `LINT_PROFILES`, `NO_PROFILE`, `_counted`, `_recipe_lines` (Task 1
  and before); the rendered rust tree (Task 3).
- Produces: `_rust_tests(repo: Path) -> list[Path]`, `_rust_profile(repo: Path) -> GateResult`,
  `RUST_CALLS`; `tests/helpers.py`: `RUST_MAKEFILE`, `conforming_tree(..., stack="rust")`.

- [ ] **Step 1: Give the fixture tree a rust shape**

In `tests/helpers.py`, after `GO_MAKEFILE`:

```python
# A rust project's task runner: the calls `build.lint` reads (spec 2026-09-24-rust-stack, D12).
RUST_MAKEFILE = (
    ".PHONY: sync lint test deny check ci\n"
    "CARGO ?= cargo\n"
    "sync:\n\t$(CARGO) fetch $(LOCKED)\n"
    "\t$(CARGO) install cargo-deny --locked --version 0.20.2 --root .cargo-tools\n"
    "lint:\n\t$(CARGO) fmt --all --check\n"
    "\t$(CARGO) clippy --workspace --all-targets --all-features -- -D warnings\n"
    "test:\n\t$(CARGO) test --workspace --locked\n"
    "deny:\n\t$(CARGO) deny check\n"
    "check:\n\trail check\nci: lint test deny check\n"
)
RUST_TOOLCHAIN_TOML = (
    '[toolchain]\nchannel = "1.98.1"\ncomponents = ["rustfmt", "clippy"]\nprofile = "minimal"\n'
)
```

In `conforming_tree`, after the `go` branch:

```python
    elif stack == "rust":
        (repo / "Cargo.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2024"\n\n[workspace]\n'
        )
        (repo / "Cargo.lock").write_text("version = 4\n")
        (repo / "rust-toolchain.toml").write_text(RUST_TOOLCHAIN_TOML)
        (repo / "deny.toml").write_text('[licenses]\nallow = ["MIT"]\n')
        (repo / "tests").mkdir()
        (repo / "tests" / "smoke.rs").write_text("#[test]\nfn smoke() {}\n")
        (repo / "Makefile").write_text(RUST_MAKEFILE)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_gates_build.py`. Add `from rail.scaffold import NewProject, render`,
`from rail.model import Tier` and `from tests.helpers import commit_all, conforming_tree, init_repo, write_manifest`
as needed, and add `ROOT = Path(__file__).resolve().parents[1]`.

```python
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
        (lambda d: _replace(d / "rust-toolchain.toml", '"rustfmt", "clippy"', '"rustfmt"'), "clippy"),
        (lambda d: (d / "Cargo.lock").unlink(), "Cargo.lock"),
        (lambda d: (d / "Cargo.toml").unlink(), "Cargo.toml"),
        (lambda d: _replace(d / "Makefile", "\t$(CARGO) deny check", "\t# $(CARGO) deny check"), "deny check"),
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_gates_build.py -q`
Expected: FAIL. `test_every_stack_has_a_build_profile` finds no `Stack.RUST` key, and the rust
tests fail on NO_PROFILE.

- [ ] **Step 4: Add the rust profiles**

In `src/rail/gates/build.py`, add `import tomllib` to the imports. After `_go_tests`:

```python
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


def _rust_test_profile(repo: Path) -> GateResult:
    return _counted(_rust_tests(repo), "tests/*.rs")
```

In `has_tests`, the `decl.stack is None` branch counts rust too:

```python
    if decl.stack is None:
        python, go, rust = len(_python_tests(repo)), len(_go_tests(repo)), len(_rust_tests(repo))
        observed = (
            "no test file found (tests/test_*.py, *_test.go), none in tests/*.rs"
            if not python and not go and not rust
            else f"{python} test file(s) (tests/test_*.py), {go} (*_test.go), {rust} (tests/*.rs)"
        )
        return Need("stack", observed).result(Stage.BUILD, "tests")
```

After `_go_profile`, add the rust profile:

```python
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
    pinned = toolchain.get("toolchain", {})
    channel = str(pinned.get("channel", ""))
    if not _EXACT_CHANNEL.match(channel):
        return fail(
            f"rust-toolchain.toml channel {channel!r} is not an exact version (X.Y.Z): a floating "
            "channel changes clippy's lints under a green project"
        )
    missing = sorted({"rustfmt", "clippy"} - set(pinned.get("components", [])))
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
    runner = _recipe_lines(makefile.read_text())
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
```

`makefile.read_text()` can raise `OSError`/`UnicodeDecodeError` like `_go_profile`'s read. Wrap it
the same way `_toml` does, returning `fail("Makefile could not be read: …")`.

Add the rust entries to both tables:

```python
    Stack.RUST: _rust_test_profile,
```
```python
    Stack.RUST: _rust_profile,
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_gates_build.py -q`
Expected: PASS, and every existing build test unchanged and green.

- [ ] **Step 6: Lint, format, full suite, golden**

Run: `env -u VIRTUAL_ENV uv run ruff format src/ tests/ && env -u VIRTUAL_ENV uv run ruff check src/ tests/ && env -u VIRTUAL_ENV uv run ruff format --check src/ tests/ && env -u VIRTUAL_ENV uv run pytest -q && git status --short tests/golden`
Expected: all clean, every test passing, and `tests/golden/audit-matrix.json` unchanged (C7: no
audited repository is rust).

- [ ] **Step 7: Commit**

```bash
git add src/rail/gates/build.py tests/helpers.py tests/test_gates_build.py
git commit  # ✨ feat(build): judge a rust repository by its toolchain pin, lock, deny.toml and Makefile
```

---

### Task 5: CI installs rustup by checksum and takes the toolchain from the project

Spec step 5 (D8, D9; C4).

**Files:**
- Modify: `.github/workflows/rail-ci.yml`
- Test: `tests/test_workflows.py`

**Interfaces:**
- Consumes: `_load`, `_steps`, `PINNED` (existing, `tests/test_workflows.py`).
- Produces: three steps under `if: inputs.stack == 'rust'`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workflows.py`:

```python
def test_rail_ci_installs_rust_from_the_project_pin() -> None:
    """rustup by version and checksum, the toolchain from the project's rust-toolchain.toml
    (the one pin), a cache keyed on what decides its content, then the lock checked by
    `--locked` (spec 2026-09-24-rust-stack, decision 8)."""
    wf = _load("rail-ci.yml")
    assert "rust" in wf["on"]["workflow_call"]["inputs"]["stack"]["description"]
    steps = _steps(wf)
    rust = [s for s in steps if s.get("if") == "inputs.stack == 'rust'"]
    assert [s["name"] for s in rust] == [
        "Set up Rust (rustup pinned, checksum verified)",
        "Cache the cargo registry and the pinned cargo-deny",
        "Fetch against the committed lock, install cargo-deny",
    ]
    setup, cache, sync = rust
    assert re.fullmatch(r"[0-9a-f]{64}", setup["env"]["RUSTUP_INIT_SHA256"])
    assert setup["env"]["RUSTUP_VERSION"] == "1.29.1"
    run = setup["run"]
    assert "sha256sum -c" in run
    assert "--default-toolchain none" in run
    assert re.search(r'"\$HOME/\.cargo/bin/rustup" toolchain install\s*$', run, re.MULTILINE)
    assert '"$HOME/.cargo/bin/rustup" component add rustfmt clippy' in run
    assert run.index("cargo fmt --version") < run.index("cargo clippy --version")
    assert not re.search(r"\b1\.\d+\.\d+\b", run), "the toolchain version lives in the project"
    assert run.rstrip().endswith('echo "$HOME/.cargo/bin" >> "$GITHUB_PATH"')

    assert PINNED.match(cache["uses"])
    key = cache["with"]["key"]
    for name in ("rust-toolchain.toml", "Cargo.lock", "Makefile"):
        assert f"'{name}'" in key, name
    paths = cache["with"]["path"].split()
    assert ".cargo-tools" in paths and not any("target" in p for p in paths)
    assert sync["run"].strip() == "make sync LOCKED=--locked"

    names = [s.get("name") for s in steps]
    assert names.index(sync["name"]) < names.index("Project CI (make ci, rail gates in CI scope)")


def test_the_template_ci_passes_no_runner() -> None:
    """GitHub-hosted by default: a project moves to red-ci by its own choice (decision 9)."""
    template = (
        ROOT / "template" / "project" / ".github" / "workflows" / "continuous-integration.yml.jinja"
    )
    assert "runs-on" not in template.read_text()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_workflows.py -q`
Expected: FAIL on the input description (`rust` absent).

- [ ] **Step 3: Add the three steps**

In `.github/workflows/rail-ci.yml`, the `stack` input description becomes
`"python | go | rust | docs — must match rail.yaml"`. After the `Set up Go` step, insert:

```yaml
      # Rust: rustup pinned by version and sha256, as gitleaks is below, and the toolchain read
      # from the project's own rust-toolchain.toml, the one pin, never repeated here. rustup is
      # called by absolute path: a $GITHUB_PATH write reaches later steps only. `component add`
      # acts on that file's toolchain; the bare `toolchain install` does not reliably add its
      # components (rustup #4216). The two --version calls fail setup, not lint, on a missing one.
      - name: Set up Rust (rustup pinned, checksum verified)
        if: inputs.stack == 'rust'
        env:
          RUSTUP_VERSION: "1.29.1"
          RUSTUP_INIT_SHA256: "dda7234360b7f578ca8b0ddcb80145646fa61a67c1720a5abc7051b35c9fcb71"
        run: |
          set -euo pipefail
          curl -sSfL -o /tmp/rustup-init "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init"
          echo "${RUSTUP_INIT_SHA256}  /tmp/rustup-init" | sha256sum -c -
          chmod 0755 /tmp/rustup-init
          /tmp/rustup-init -y --no-modify-path --profile minimal --default-toolchain none
          "$HOME/.cargo/bin/rustup" toolchain install
          "$HOME/.cargo/bin/rustup" component add rustfmt clippy
          "$HOME/.cargo/bin/cargo" fmt --version
          "$HOME/.cargo/bin/cargo" clippy --version
          echo "$HOME/.cargo/bin" >> "$GITHUB_PATH"

      # Keyed on what decides the content: the toolchain, the lock, and the Makefile that pins
      # cargo-deny. target/ is not cached. A prefix restore is safe: `cargo install --version`
      # replaces an older cargo-deny, and `--locked` still judges the lock.
      - name: Cache the cargo registry and the pinned cargo-deny
        if: inputs.stack == 'rust'
        uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
        with:
          path: |
            ~/.cargo/registry/index
            ~/.cargo/registry/cache
            ~/.cargo/git/db
            .cargo-tools
          key: rust-${{ runner.os }}-${{ hashFiles('rust-toolchain.toml', 'Cargo.lock', 'Makefile') }}
          restore-keys: |
            rust-${{ runner.os }}-

      # A missing (uncommitted) or stale Cargo.lock fails here instead of being written.
      - name: Fetch against the committed lock, install cargo-deny
        if: inputs.stack == 'rust'
        run: make sync LOCKED=--locked
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_workflows.py -q`
Expected: PASS, including `test_every_action_is_pinned_to_a_commit_sha`.

- [ ] **Step 5: Lint, format, full suite**

Run: `env -u VIRTUAL_ENV uv run ruff format src/ tests/ && env -u VIRTUAL_ENV uv run ruff check src/ tests/ && env -u VIRTUAL_ENV uv run ruff format --check src/ tests/ && env -u VIRTUAL_ENV uv run pytest -q`
Expected: all clean, every test passing.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/rail-ci.yml tests/test_workflows.py
git commit  # 👷 ci(rail-ci): install rustup by checksum and the toolchain the project pins
```

---

### Task 6: `rail upgrade --stack` moves a docs project onto a stack

Spec step 6 (D13; C5; Review Focus 4 and 5).

**Files:**
- Modify: `src/rail/scaffold.py`, `src/rail/commands/upgrade.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: `RUST_PROD_REFUSAL`, `Stack.RUST` (Task 2); the rust files (Task 3).
- Produces: `upgrade(repo, *, stack: Stack | None = None, update=…, resolve=…) -> str`;
  `rail upgrade --stack {python,go,rust,docs}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaffold.py`:

```python
def _answered(repo: Path, *, stack: str, tier: str = "dev", manifest: str | None = None) -> Path:
    """A scaffolded-looking tree: answers file and rail.yaml, no template behind it."""
    repo.mkdir(parents=True)
    (repo / ANSWERS_FILE).write_text(
        "_commit: v0.5.0\n_src_path: git@github.com:hawkixs/red-rail.git\n"
        f"stack: {stack}\ntier: {tier}\nrail_ref: {'a' * 40}\n"
    )
    body = manifest if manifest is not None else (
        f"rail: 1\nproject: red-life\nbrain_key: red-life\ntier: {tier}\nstack: {stack}\n"
    )
    (repo / "rail.yaml").write_text(body)
    return repo


def _never(*args: object, **kwargs: object) -> None:
    raise AssertionError("copier update must not run")


@pytest.mark.parametrize(
    ("current", "target", "tier", "message"),
    [
        ("python", Stack.GO, "dev", "only out of docs"),
        ("go", Stack.RUST, "dev", "only out of docs"),
        ("rust", Stack.DOCS, "dev", "cannot switch to docs"),
        ("docs", Stack.DOCS, "dev", "cannot switch to docs"),
        ("docs", Stack.RUST, "prod", "rust at tier prod is not templated yet"),
    ],
    ids=["python-go", "go-rust", "rust-docs", "docs-docs", "docs-rust-at-prod"],
)
def test_upgrade_refuses_a_transition_not_from_docs(
    tmp_path: Path, current: str, target: Stack, tier: str, message: str
) -> None:
    repo = _answered(tmp_path / "red-life", stack=current, tier=tier)
    before = {p.name: p.read_text() for p in repo.iterdir()}
    with pytest.raises(ScaffoldError, match=message):
        upgrade(repo, stack=target, update=_never, resolve=lambda t, **k: "c" * 40)
    assert {p.name: p.read_text() for p in repo.iterdir()} == before


def test_upgrade_refuses_answers_holding_rust_at_prod(tmp_path: Path) -> None:
    """Copier would drop the invalid answer and, under defaults, re-render the project as python:
    refused with or without --stack."""
    repo = _answered(tmp_path / "red-life", stack="rust", tier="prod")
    with pytest.raises(ScaffoldError, match="rust at tier prod"):
        upgrade(repo, update=_never, resolve=lambda t, **k: "c" * 40)


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ("rail: 1\nproject: red-life\nbrain_key: red-life\ntier: dev\nstack: python\n", "disagree"),
        (None, "rail.yaml"),
        ("rail: 1\n<<<<<<< ours\nstack: docs\n", "rail.yaml"),
    ],
    ids=["disagree", "missing", "unparsable"],
)
def test_upgrade_refuses_when_answers_and_manifest_disagree(
    tmp_path: Path, manifest: str | None, message: str
) -> None:
    repo = _answered(tmp_path / "red-life", stack="docs", manifest=manifest or "")
    if manifest is None:
        (repo / "rail.yaml").unlink()
    with pytest.raises(ScaffoldError, match=message):
        upgrade(repo, stack=Stack.RUST, update=_never, resolve=lambda t, **k: "c" * 40)


def test_upgrade_fails_when_rail_yaml_does_not_follow(tmp_path: Path) -> None:
    repo = _answered(tmp_path / "red-life", stack="docs")
    seen: dict[str, object] = {}

    def update(dest: Path, **kwargs: object) -> None:  # copier ran, rail.yaml kept `docs`
        seen.update(kwargs)

    with pytest.raises(ScaffoldError, match="copier did not carry `stack` into rail.yaml"):
        upgrade(repo, stack=Stack.RUST, update=update, resolve=lambda t, **k: "c" * 40)
    assert seen["data"] == {"rail_ref": "c" * 40, "stack": "rust"}


def _tagged_template(template_dir: Path, tag: str) -> None:
    if not (template_dir / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main", str(template_dir)], check=True)
    subprocess.run([*GIT, "-C", str(template_dir), "add", "-A"], check=True)
    subprocess.run(
        [*GIT, "-C", str(template_dir), "commit", "-q", "--allow-empty", "-m", f"chore: {tag}"],
        check=True,
    )
    subprocess.run(["git", "-C", str(template_dir), "tag", tag], check=True)


def test_upgrade_switches_docs_to_rust(template_dir: Path, tmp_path: Path) -> None:
    """Real copier on a tagged throwaway template: the answer, rail.yaml and the CI call all say
    rust afterwards, and the rust files exist. rail.yaml is written by copier's merge alone."""
    _tagged_template(template_dir, "v0.1.0")
    project = _project(
        template_dir, tmp_path / "red-life", slug="red-life", stack=Stack.DOCS,
        tier=Tier.DEV, template_ref="v0.1.0",
    )
    new_project(project, publish=False, clock=CLOCK, resolve=_pin)
    _tagged_template(template_dir, "v0.2.0")

    upgrade(project.dest, stack=Stack.RUST, resolve=_pin)

    dest = project.dest
    assert "stack: rust" in (dest / ANSWERS_FILE).read_text()
    assert "stack: rust" in (dest / "rail.yaml").read_text()
    ci = dest / ".github" / "workflows" / "continuous-integration.yml"
    assert "stack: rust" in ci.read_text()
    assert (dest / "Cargo.toml").is_file() and (dest / "rust-toolchain.toml").is_file()


def test_upgrade_to_rust_refuses_a_dirty_tree(template_dir: Path, tmp_path: Path) -> None:
    """Uncommitted changes: copier refuses, and nothing is half-written (Review Focus 4)."""
    _tagged_template(template_dir, "v0.1.0")
    project = _project(
        template_dir, tmp_path / "red-life", slug="red-life", stack=Stack.DOCS,
        tier=Tier.DEV, template_ref="v0.1.0",
    )
    new_project(project, publish=False, clock=CLOCK, resolve=_pin)
    _tagged_template(template_dir, "v0.2.0")
    (project.dest / "README.md").write_text("an uncommitted edit\n")

    with pytest.raises(Exception):  # copier's own "dirty" refusal
        upgrade(project.dest, stack=Stack.RUST, resolve=_pin)
    assert not (project.dest / "Cargo.toml").exists()
    assert "stack: docs" in (project.dest / "rail.yaml").read_text()


def test_cli_upgrade_takes_a_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "rail.commands.upgrade.upgrade", lambda repo, **kw: calls.append(kw) or "v0.6.0"
    )
    result = CliRunner().invoke(main, ["upgrade", "--repo", str(tmp_path), "--stack", "rust"])
    assert result.exit_code == 0, result.output
    assert calls == [{"stack": Stack.RUST}]
    assert "make sync" in result.output
```

In `test_upgrade_to_rust_refuses_a_dirty_tree`, check first which exception copier raises for a
dirty tree. If `upgrade` lets it escape as a non-`ScaffoldError`, wrap `update(...)` in
`upgrade()` like `render` does (`except Exception as exc: raise ScaffoldError(f"copier could not
update {repo}: {exc}") from exc`), and assert `ScaffoldError` in the test. Record which one you
kept in the report.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_scaffold.py -q -k "upgrade"`
Expected: FAIL with `TypeError: upgrade() got an unexpected keyword argument 'stack'`.

- [ ] **Step 3: Implement the refusals and the postcondition**

Replace `upgrade` in `src/rail/scaffold.py`:

```python
def _manifest(repo: Path) -> dict[str, Any] | str:
    """rail.yaml as a mapping, or why it cannot be read: the switch never guesses a stack."""
    path = repo / "rail.yaml"
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        return "rail.yaml is missing"
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return f"rail.yaml does not parse: {exc}"
    return data if isinstance(data, dict) else "rail.yaml is not a mapping"


def _switchable(answers: dict[str, Any], repo: Path, stack: Stack) -> None:
    """Refuse, before copier, any switch but one out of docs (spec 2026-09-24-rust-stack,
    decision 13): python→go or go→rust leaves build files only a diff review would catch."""
    manifest = _manifest(repo)
    if isinstance(manifest, str):
        raise ScaffoldError(f"{manifest}: the stack switch reads it before copier runs")
    current, declared = answers.get("stack"), manifest.get("stack")
    if current != declared:
        raise ScaffoldError(
            f"{ANSWERS_FILE} says stack {current} but rail.yaml says {declared}: the two "
            "disagree, reconcile them before switching"
        )
    if stack is Stack.DOCS:
        raise ScaffoldError("a project cannot switch to docs: that transition is not templated")
    if current != Stack.DOCS.value:
        raise ScaffoldError(
            f"--stack switches only out of docs (this project is {current}): python↔go and "
            "go→rust leave build files only a diff review would catch"
        )
    if stack is Stack.RUST and manifest.get("tier") == Tier.PROD.value:
        raise ScaffoldError(RUST_PROD_REFUSAL)


def upgrade(
    repo: Path,
    *,
    stack: Stack | None = None,
    update: Callable[..., Any] = copier.run_update,
    resolve: Callable[..., str] = resolve_rail_ref,
) -> str:
    """`copier update` towards the template's latest tag; returns the new `_commit`.

    Re-resolves the CI workflow pin at the same time, so a gate change reaches a repository
    as a reviewable line in this command's diff rather than as an effect of `@main` moving
    under it. With `stack`, the project leaves `docs` for that stack: copier takes the new
    answer, and its three-way merge is the only writer of rail.yaml."""
    answers = repo / ANSWERS_FILE
    if not answers.is_file():
        raise ScaffoldError(f"{ANSWERS_FILE} missing: not scaffolded by copier")
    data = yaml.safe_load(answers.read_text()) or {}
    if not data.get("_commit") or not data.get("_src_path"):
        raise ScaffoldError(
            f"{ANSWERS_FILE} has no _src_path/_commit: the template was not versioned; "
            "re-scaffold from a tagged red-rail before upgrading"
        )
    if data.get("stack") == Stack.RUST.value and data.get("tier") == Tier.PROD.value:
        # copier would drop this answer as invalid and, under defaults, re-render as python
        raise ScaffoldError(f"{ANSWERS_FILE} holds stack rust at tier prod: {RUST_PROD_REFUSAL}")
    if stack is not None:
        _switchable(data, repo, stack)
    answers_to_give: dict[str, Any] = {"rail_ref": resolve(str(data["_src_path"]))}
    if stack is not None:
        answers_to_give["stack"] = stack.value
    update(
        repo,
        data=answers_to_give,
        defaults=True,
        overwrite=True,
        skip_answered=True,
        quiet=True,
        unsafe=False,
    )
    if stack is not None:
        manifest = _manifest(repo)
        if isinstance(manifest, str) or manifest.get("stack") != stack.value:
            raise ScaffoldError(
                f"copier did not carry `stack` into rail.yaml: resolve it to `stack: {stack.value}`"
            )
    return str((yaml.safe_load(answers.read_text()) or {}).get("_commit", ""))
```

`src/rail/commands/upgrade.py`:

```python
@click.command("upgrade")
@repo_option
@click.option(
    "--stack",
    type=click.Choice([s.value for s in Stack]),
    default=None,
    help="Leave stack docs for this stack (the only switch the template supports).",
)
def command(repo: Path, stack: str | None) -> None:
    """Resorb template drift: bring the repository to the template's latest version."""
    try:
        version = upgrade(repo, stack=Stack(stack)) if stack else upgrade(repo)
    except ScaffoldError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    if stack:
        click.echo(
            f"{repo}: template {version}, stack {stack}; run `make sync`, review the diff, "
            "then commit it"
        )
    else:
        click.echo(f"{repo}: template {version}; review the diff, then commit it")
```

Add `from rail.model import Stack` to its imports. The CLI test's `calls == [{"stack": Stack.RUST}]`
depends on this call shape.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_scaffold.py -q -k "upgrade"`
Expected: PASS, including the existing
`test_upgrade_rebumps_the_pin_so_the_change_is_a_reviewable_line`
(`seen["data"] == {"rail_ref": new}` without `--stack`).

- [ ] **Step 5: Lint, format, full suite**

Run: `env -u VIRTUAL_ENV uv run ruff format src/ tests/ && env -u VIRTUAL_ENV uv run ruff check src/ tests/ && env -u VIRTUAL_ENV uv run ruff format --check src/ tests/ && env -u VIRTUAL_ENV uv run pytest -q`
Expected: all clean, every test passing.

- [ ] **Step 6: Commit**

```bash
git add src/rail/scaffold.py src/rail/commands/upgrade.py tests/test_scaffold.py
git commit  # ✨ feat(upgrade): --stack moves a docs project onto a stack, refused anywhere else
```

---

### Task 7: The branch proves itself

Spec step 7, the in-repository half (C6, C7, C8).

**Files:**
- Modify: any tracked doc of red-rail that lists the stacks as `python | go | docs` or
  `python, go, docs` outside `docs/specs/` and `docs/plans/`. Find them with
  `git grep -nE "python \| go \| docs|python, go(,| and) docs"`, and add `rust` in each.
  `rail-ci.yml` was done in Task 5.

- [ ] **Step 1: Update the stack lists**

Run the `git grep` above, add `rust` wherever the list is a statement of what the rail supports,
and commit only if something changed:

```bash
git commit -am "📝 docs: list rust among the stacks"
```

- [ ] **Step 2: The whole proof (C6, C7)**

Run: `env -u VIRTUAL_ENV make ci`
Expected: exit 0, ruff clean, the pytest summary line reports every test passing, `rail check`
prints `passed 18/18`. Read both summary lines.

Run: `git status --short tests/golden`
Expected: empty (C7).

- [ ] **Step 3: B's spec and plan pass their gates, explicitly (C8, D14)**

`rail check` reads A's files: `template-alignment` sorts after `rust-stack`. Prove B's own files
on a copy that holds only them:

```bash
C=$(mktemp -d)
git archive HEAD | tar -x -C "$C"
find "$C/docs/specs" "$C/docs/plans" -name '*.md' ! -name '2026-09-24-rust-stack.md' -delete
env -u VIRTUAL_ENV uv run rail check design plan --repo "$C"
```

If `rail check` takes one stage at a time, run it once with `design` and once with `plan`.

Expected: `PASS  design.spec  2026-09-24-rust-stack.md: …` and
`PASS  plan.plan  2026-09-24-rust-stack.md: 7 task(s) verified, spec docs/specs/2026-09-24-rust-stack.md`.
Copy both lines into the report for the PR, then `rm -rf "$C"`.

---

## After the last task (not part of the task gates)

1. **Final whole-branch review**: `workflows/pre-review.js` (skill `rail-review`), then one fix
   wave, each fix test-first.
2. **C10, host verification** on the committed branch, recorded in the PR (counts and verdicts,
   no machine name). Rust is installed in user space on this host (`~/.cargo/bin`, rustc 1.98.1),
   so the wrapper is a script that runs `~/.cargo/bin/cargo` with
   `PATH=<tree>/.cargo-tools/bin:~/.cargo/bin:$PATH`. The container fallback is
   `rust:1.98.1-slim@sha256:f47a8de237dcbb0b0ce1099901e60a89728e3d51f24e664b40e947171538ade7`,
   run as in the spec. Render `--stack rust` at `bootstrap` and `dev` with `--template . --template-ref HEAD`. Then:
   - bootstrap: `make CARGO=<wrapper> RAIL_FLAGS=--ci sync ci` exits 0, including
     `cargo deny check` on the rendered `deny.toml`;
   - dev: `make CARGO=<wrapper> sync lint test deny` exits 0, and
     `rail check --ci build --json` reports PASS for `build.tests` and `build.lint`;
   - after deleting `Cargo.lock`, `make CARGO=<wrapper> sync LOCKED=--locked` fails;
   - `rail upgrade --stack rust` on a committed `docs` render leaves both files saying `rust`;
   - record `cargo fmt --version`, `cargo clippy --version`, and the `make sync` durations;
   - delete the trees.
3. **C11**, which needs the operator. The private throwaway repository (`delete_repo` scope) and
   the hosted-runner minutes are pending with maestro. When both are granted, push the
   bootstrap tree rendered at `--template-ref <B head SHA>` with its `Cargo.lock` committed. Run
   `rail-ci` twice, cold then warm. Record both summary lines, both run URLs and both durations
   against `timeout-minutes: 20` (C9). Then delete the repository.
4. **One pull request** from `feat/hawixs/rust-stack` into `main`, after A (#47) merges and the
   branch is brought up to date with main by a merge, not a rebase. No `rail bind`. Then
   `env -u VIRTUAL_ENV uv run rail reviewer once --repository hawkixs/red-rail --pr <n>`. It may
   be merged under decision 39f7ea9f: an approve from a non-Claude judge, green CI and green
   `rail check`.
5. **The tag `v0.6.0`** on the operator's go, after `v0.5.0`. Then tell the session red-42 that
   `rail new` and `rail upgrade --stack rust` can create red-life's rust project (C12 is
   red-life's own PR).
