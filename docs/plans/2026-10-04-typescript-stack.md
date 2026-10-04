# The typescript stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `rail new --stack typescript` scaffolds a Claude Code plugin repository whose `make ci` and `rail check` pass in CI, at tier `bootstrap` or `dev`.

**Architecture:** one new `Stack` member routed through the existing tables and chains, as the rust stack was: a build gate that reads the repository and never runs Node (`_typescript_tests`, `_typescript_profile`), template files derived from the reference plugin red-cockpit built, a Makefile branch that calls tools by path from `node_modules/.bin`, a `rail-ci.yml` branch that reads `.node-version`, and the refusal at tier `prod` generalised into one table (`NOT_AT_PROD`). The engine's types are not shipped: a project copies its own with `make types`, and `tsc` is a named SKIP until it does.

**Tech Stack:** Python 3.12, pytest, copier/Jinja, GNU make, GitHub Actions YAML; the rendered projects use Node, TypeScript, Biome and the `@anthropic-ai/claude-code` package.

**Spec:** `docs/specs/2026-10-04-typescript-stack.md` (two measured corrections folded in: commits `d3a677b` and `a14cfc1`). Executors read both.

## Global Constraints

- Tier: `typescript` is refused at `tier: prod`; `bootstrap` and `dev` only. The message is exactly `typescript at tier prod is not templated yet (a plugin has no image and no service): scaffold at dev`.
- Exact pins, one source each: Node in `.node-version` only; `typescript`, `@biomejs/biome` and `@anthropic-ai/claude-code` in `package.json` `devDependencies`, each `X.Y.Z` (no `^`, no `~`, no range). The template ships no `package-lock.json` and no `vendor/`.
- `make sync` installs with `--ignore-scripts`, then runs `npm rebuild @anthropic-ai/claude-code` and nothing else with scripts. CI runs `make sync INSTALL=ci` (`npm ci`).
- Tools are called by path, `$(BIN)/<tool>` with `BIN ?= node_modules/.bin`, never through `npx` (npx fetches an absent tool).
- The engine's types are never in the template. `make types` copies `.claude-plugin/types/claude-code/index.d.ts` to `vendor/claude-code/index.d.ts`; only that file.
- The build gate is a pure read: it never runs Node, npm or `claude`, and never raises.
- Every `uses:` in a workflow is pinned by 40-hex SHA. No `# rail: ignore`. No IP, address or infra name in this public repository (RFC 5737 or `.invalid` only).
- Everything pushed is English. No `rail bind` in this repository (engagement d096f911). Never `git stash`.
- A rendered guidance file cites no skill: a tool is written as a command with a space (`make types`), never as a bare backticked kebab token or a `/slash` token.

## Measured, recorded here (2026-10-04)

Read on the host, not assumed. The implementer re-reads the three versions the day they implement (`npm view <pkg> version`) and records any difference in the commit message of Task 3.

| Item | Value | How |
|---|---|---|
| Node | `24.21.0` (LTS "Krypton", 2026-09-07) | `nodejs.org/dist/index.json`; the whole flow below ran under it from a tarball with a verified checksum; the host's Node is 22.23.3 and also passes |
| `typescript` | `7.0.2` | `npm view`; `tsc 7.0.2 -p .` passed on the fixture and failed on a seeded type error |
| `@biomejs/biome` | `2.5.15` | `npm view`; `biome ci .` exit 0 with the config of Task 3 |
| `@anthropic-ai/claude-code` | `2.1.289` | `npm view`; the host's own `claude` is 2.1.288 |
| `actions/setup-node` | `v7.0.0` = `820762786026740c76f36085b0efc47a31fe5020` | `gh api repos/actions/setup-node/git/ref/tags/v7.0.0` (a commit) |
| Install scripts | after `npm install --ignore-scripts` the `claude` bin answers "native binary not installed"; after `npm rebuild @anthropic-ai/claude-code` it runs | measured on 2.1.289; the platform binary is an optional dependency, kept by the lock |
| Engine commands | `claude plugin test [dir]` runs every `*.test.ts` and `*.test.tsx` under `dir`; `claude plugin validate --strict .` passed; both ran with an empty `HOME` and no login | measured with the npm-installed binary under a clean environment |
| Engine types | line 1 is `// Written by Claude Code <version>.`; the core is `.claude-plugin/types/claude-code/index.d.ts`; the module declarations are ambient, so a `tsconfig.json` needs `include`, not `paths` | read from the declarations embedded in the 2.1.288 binary (header template and `declare module` lines) |
| `npx` | `npx --no-install` and `npx --no` both fetch an absent tool on npm 10.9 and 11.19 | measured with a nonexistent package |

## Divergences from the spec (all recorded, none new in substance)

- `$(NPX)` of the spec's first draft is `$(BIN)/` (spec commit `a14cfc1`); `Bash(npx:*)` is not allowed in `settings.json`.
- The command the template registers is `<project>-hello`, kept in one constant (`COMMAND`) so no slug length can push a line past Biome's width of 100.

## Correction after the proof (2026-10-04, Task 7)

Tasks 1 to 5 were built as written and reviewed. The proof of Task 6 on a real scaffold found two defects no static test could see, and the operator chose the fix:

- `make validate` (`claude plugin validate --strict`) failed on every scaffold: the rail's root `CLAUDE.md` is a warning when the repository root is the plugin root. The plugin now lives in `plugin/` (`plugin/.claude-plugin/`, `plugin/hooks/`, `plugin/src/`); `package.json`, `tsconfig.json`, `biome.json`, `.node-version`, the Makefile and `vendor/` stay at the root. `test` runs `claude plugin test plugin`, `validate` runs `claude plugin validate --strict plugin`; the engine writes its types to `plugin/.claude-plugin/types/`, which `make types` copies to `vendor/claude-code/index.d.ts`.
- `biome ci .` failed on the rail's own files (`.claude/settings.json`, `docs/receipts/*.json`): `biome.json` now excludes `.claude` and `docs`.

Every path in Tasks 2 and 3 below that names `.claude-plugin/`, `hooks/` or `src/` for the plugin is read as under `plugin/` from here on; Task 7 applies it to the code and the tests. The spec is amended accordingly (decisions 2, 4, 5, 6, 9 and criteria 1, 9).

## Review Focus

Failure modes the spec implies and no task's happy path exercises, most likely first. Each has its test in the task named.

1. A long slug (`red-` plus 40 characters): the rendered TypeScript, JSON and `package.json` must stay within Biome's width of 100, or `make lint` fails on a fresh scaffold. Task 3 (`test_typescript_files_fit_biome_width_at_any_slug_length`).
2. A test file under `node_modules`, `vendor` or `.claude-plugin/types` counted as the project's test: `build.tests` would pass on code nobody wrote. Task 2.
3. Vendored types whose first line is missing, has a byte-order mark, uses CRLF, or is not UTF-8: the gate must FAIL naming the file, never raise. Task 2.
4. A `package.json` that is not an object, whose `devDependencies` is a list, or whose versions are not strings: FAIL naming `package.json`, never an `AttributeError`. Task 2.
5. `make typecheck` with no vendored types must print the named SKIP and exit 0; with them but no installed `tsc` it must fail on the missing file, not fetch anything. `make types` with nothing to copy must fail with the instruction. Task 3 (these run real `make`, no Node needed).
6. A project at `tier: prod` reaching copier by `rail upgrade --stack typescript` or by an answers file holding both: refused before copier, tree untouched. Tasks 1 and 5.

## File Structure

| File | Responsibility |
|---|---|
| `src/rail/scaffold.py` | `NOT_AT_PROD` table and its three uses (Task 1); the typescript refusal message (Task 3) |
| `src/rail/gates/build.py` | `_typescript_tests`, `_typescript_profile` and their table entries (Tasks 2 and 3) |
| `src/rail/model.py`, `copier.yml` | `Stack.TYPESCRIPT`, the copier choice and validator (Task 3) |
| `template/project/…` | the plugin files, the Makefile branch, the six marked chains, `rail.yaml.jinja` (Task 3) |
| `.github/workflows/rail-ci.yml` | the two Node steps and the `stack` description (Task 4) |
| `tests/helpers.py` | `TYPESCRIPT_MAKEFILE`, `write_typescript_files` (Task 2) |
| `tests/combinations.py`, `tests/test_scaffold.py`, `tests/test_gates_build.py`, `tests/test_workflows.py` | the tests of each task |

Run tests from the worktree root with `unset VIRTUAL_ENV && uv run pytest <path> -q`. Commit with `/git-commit` format, English, ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

---

### Task 1: One table for the stacks that have no prod template

Behaviour-preserving: today `scaffold.py` knows `RUST_PROD_REFUSAL` in three places. After this task a table holds it, and Task 3 adds a second entry.

**Files:**
- Modify: `src/rail/scaffold.py:36-41` (the constant), `:79-80` (`NewProject.answers`), `:428-429` (`_switchable`), `:454-456` (`upgrade`)
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Produces: `NOT_AT_PROD: dict[Stack, str]` in `rail.scaffold`; `_refusal_at_prod(stack: object, tier: object) -> str | None`. `RUST_PROD_REFUSAL` stays exported.

- [ ] **Step 1: Write the failing test** (append to `tests/test_scaffold.py`, after `test_the_copier_validator_says_what_rail_new_says`)

```python
def test_not_at_prod_holds_each_refusal_once() -> None:
    """One table says which stacks have no prod template: `answers`, the stack switch and the
    answers-file check all read it (spec 2026-10-04-typescript-stack, decision 1)."""
    from rail.scaffold import NOT_AT_PROD, RUST_PROD_REFUSAL

    assert NOT_AT_PROD == {Stack.RUST: RUST_PROD_REFUSAL}
```

- [ ] **Step 2: Run it to see it fail**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_scaffold.py::test_not_at_prod_holds_each_refusal_once -q`
Expected: FAIL, `ImportError: cannot import name 'NOT_AT_PROD'`.

- [ ] **Step 3: Implement**

In `src/rail/scaffold.py`, after the `RUST_PROD_REFUSAL = (...)` block add:

```python
# The stacks whose prod template does not exist yet, each with the refusal it is given. The
# three places that refuse read this table, so a stack is added here and nowhere else.
NOT_AT_PROD: dict[Stack, str] = {Stack.RUST: RUST_PROD_REFUSAL}


def _refusal_at_prod(stack: object, tier: object) -> str | None:
    """The refusal for a stack and tier as an answers file or a manifest holds them: plain
    values, possibly invalid, so an unknown stack is no refusal here (copier says what is wrong)."""
    if tier != Tier.PROD.value:
        return None
    try:
        return NOT_AT_PROD.get(Stack(stack))
    except ValueError:
        return None
```

In `NewProject.answers` replace

```python
        if self.stack is Stack.RUST and self.tier is Tier.PROD:
            raise ScaffoldError(RUST_PROD_REFUSAL)
```

with

```python
        refusal = _refusal_at_prod(self.stack.value, self.tier.value)
        if refusal:
            raise ScaffoldError(refusal)
```

In `_switchable` replace

```python
    if stack is Stack.RUST and manifest.get("tier") == Tier.PROD.value:
        raise ScaffoldError(RUST_PROD_REFUSAL)
```

with

```python
    refusal = _refusal_at_prod(stack.value, manifest.get("tier"))
    if refusal:
        raise ScaffoldError(refusal)
```

In `upgrade` replace

```python
    if data.get("stack") == Stack.RUST.value and data.get("tier") == Tier.PROD.value:
        # copier would drop this answer as invalid and, under defaults, re-render as python
        raise ScaffoldError(f"{ANSWERS_FILE} holds stack rust at tier prod: {RUST_PROD_REFUSAL}")
```

with

```python
    refusal = _refusal_at_prod(data.get("stack"), data.get("tier"))
    if refusal:
        # copier would drop this answer as invalid and, under defaults, re-render as python
        raise ScaffoldError(
            f"{ANSWERS_FILE} holds stack {data['stack']} at tier prod: {refusal}"
        )
```

- [ ] **Step 4: Run the scaffold tests**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_scaffold.py -q`
Expected: all pass (the rust refusal tests are unchanged and green), summary line read.

- [ ] **Step 5: Lint and commit**

```bash
unset VIRTUAL_ENV && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
git add src/rail/scaffold.py tests/test_scaffold.py
git commit -m "♻️ refactor(scaffold): one table for the stacks with no prod template" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The typescript build gate, as pure functions

The profile functions are written and tested directly on a hand-built tree, before the stack exists: they need no `Stack` member. Task 3 registers them.

**Files:**
- Modify: `src/rail/gates/build.py` (imports; new functions after `_rust_profile`; `has_tests` observation)
- Modify: `tests/helpers.py` (after `RUST_TOOLCHAIN_TOML`; a branch in `conforming_tree`)
- Test: `tests/test_gates_build.py`

**Interfaces:**
- Produces: `build._typescript_tests(repo) -> list[Path]`, `build._typescript_test_profile(repo) -> GateResult`, `build._typescript_profile(repo) -> GateResult`; in `tests/helpers.py`: `TYPESCRIPT_PIN: str`, `TYPESCRIPT_NODE: str`, `TYPESCRIPT_MAKEFILE: str`, `write_typescript_files(repo: Path) -> None`.

- [ ] **Step 1: Write the helpers** (`tests/helpers.py`, after `RUST_TOOLCHAIN_TOML`)

```python
# A typescript project's files, as the template renders them: the calls `build.lint` reads
# (spec 2026-10-04-typescript-stack, decision 9) and the pins it compares.
TYPESCRIPT_NODE = "24.21.0"
TYPESCRIPT_PIN = "2.1.289"  # the @anthropic-ai/claude-code pin
TYPESCRIPT_MAKEFILE = (
    ".PHONY: sync lint typecheck test validate types check ci\n"
    "NPM ?= npm\nBIN ?= node_modules/.bin\nINSTALL ?= install\n"
    "sync:\n\t$(NPM) $(INSTALL) --ignore-scripts\n\t$(NPM) rebuild @anthropic-ai/claude-code\n"
    "lint:\n\t$(BIN)/biome ci .\n"
    "typecheck:\n\t$(BIN)/tsc --noEmit\n"
    "test:\n\t$(BIN)/claude plugin test .\n"
    "validate:\n\t$(BIN)/claude plugin validate --strict .\n"
    "check:\n\trail check\nci: lint typecheck test validate check\n"
)


def write_typescript_files(repo: Path) -> None:
    """Everything `build.tests` and `build.lint` read for a typescript plugin, and no more."""
    (repo / ".node-version").write_text(TYPESCRIPT_NODE + "\n")
    (repo / "package.json").write_text(
        json.dumps(
            {
                "name": repo.name,
                "private": True,
                "type": "module",
                "devDependencies": {
                    "@anthropic-ai/claude-code": TYPESCRIPT_PIN,
                    "@biomejs/biome": "2.5.15",
                    "typescript": "7.0.2",
                },
            },
            indent=2,
        )
        + "\n"
    )
    (repo / "package-lock.json").write_text('{"lockfileVersion": 3}\n')
    (repo / "biome.json").write_text("{}\n")
    (repo / "tsconfig.json").write_text("{}\n")
    (repo / ".claude-plugin").mkdir(exist_ok=True)
    (repo / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": repo.name, "version": "0.1.0", "author": {"name": "hawkixs"}}) + "\n"
    )
    (repo / "hooks").mkdir(exist_ok=True)
    (repo / "hooks" / "hooks.json").write_text('{"modules": ["./register.ts"]}\n')
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "core.test.ts").write_text("// a test\n")
    (repo / "Makefile").write_text(TYPESCRIPT_MAKEFILE)
```

In `conforming_tree`, after the `elif stack == "rust":` branch and before `commit_all(...)`, add:

```python
    elif stack == "typescript":
        write_typescript_files(repo)
```

- [ ] **Step 2: Write the failing tests** (`tests/test_gates_build.py`)

Add to the imports: `import json` (top), and extend the helpers import to
`from tests.helpers import (TYPESCRIPT_PIN, commit_all, conforming_tree, init_repo, write_manifest, write_typescript_files)`.
Append at the end of the file:

```python
# -- typescript (spec 2026-10-04-typescript-stack, decisions 8 and 9) ------------------------


def _ts_project(root: Path) -> Path:
    repo = init_repo(root)
    write_typescript_files(repo)
    return repo


def test_typescript_tests_are_found(tmp_path: Path) -> None:
    repo = _ts_project(tmp_path / "red-cockpit")
    (repo / "src" / "view.test.tsx").write_text("")
    (repo / "src" / "deep" / "nested").mkdir(parents=True)
    (repo / "src" / "deep" / "nested" / "x.test.ts").write_text("")
    found = build_gates._typescript_tests(repo)
    assert {p.name for p in found} == {"core.test.ts", "view.test.tsx", "x.test.ts"}
    assert build_gates._typescript_test_profile(repo).passed


@pytest.mark.parametrize(
    "where", ["node_modules/pkg", "vendor/claude-code", ".claude-plugin/types", ".git/hooks"]
)
def test_a_test_under_installed_or_vendored_code_is_not_counted(tmp_path: Path, where: str) -> None:
    repo = init_repo(tmp_path / "red-cockpit")
    (repo / where).mkdir(parents=True, exist_ok=True)
    (repo / where / "a.test.ts").write_text("")
    assert build_gates._typescript_tests(repo) == []
    result = build_gates._typescript_test_profile(repo)
    assert not result.passed and "*.test.ts" in result.details


def test_a_typescript_repo_with_only_go_tests_fails(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "red-cockpit")
    (repo / "main_test.go").write_text("package main\n")
    assert not build_gates._typescript_test_profile(repo).passed


def test_undeclared_stack_reports_typescript_tests(tmp_path: Path) -> None:
    assert (
        "no test file found (tests/test_*.py, *_test.go), none in tests/*.rs, none in *.test.ts"
        in has_tests(tmp_path).details
    )
    (tmp_path / "core.test.ts").write_text("")
    assert (
        "0 test file(s) (tests/test_*.py), 0 (*_test.go), 0 (tests/*.rs), 1 (*.test.ts)"
        in has_tests(tmp_path).details
    )


def test_typescript_lint_passes_and_names_the_skipped_typecheck(tmp_path: Path) -> None:
    result = build_gates._typescript_profile(_ts_project(tmp_path / "red-cockpit"))
    assert result.passed, result.details
    assert "tsc: SKIPPED (vendor/claude-code absent)" in result.details
    assert "24.21.0" in result.details


@pytest.mark.parametrize(
    ("mutate", "named"),
    [
        (lambda d: (d / ".node-version").unlink(), ".node-version"),
        (lambda d: (d / ".node-version").write_text("24\n"), "not an exact version"),
        (lambda d: _replace(d / "package.json", '"typescript": "7.0.2"', '"typescript": "^7.0.2"'), "typescript"),
        (lambda d: _replace(d / "package.json", '"@biomejs/biome": "2.5.15"', '"@biomejs/biome": "latest"'), "@biomejs/biome"),
        (lambda d: _replace(d / "package.json", f'"{TYPESCRIPT_PIN}"', f'"~{TYPESCRIPT_PIN}"'), "@anthropic-ai/claude-code"),
        (lambda d: (d / "package-lock.json").unlink(), "package-lock.json"),
        (lambda d: (d / "biome.json").unlink(), "biome.json"),
        (lambda d: (d / "tsconfig.json").unlink(), "tsconfig.json"),
        (lambda d: (d / ".claude-plugin" / "plugin.json").write_text('{"name": "x"}\n'), "author"),
        (lambda d: _replace(d / "Makefile", "\t$(BIN)/biome ci .", "\t$(BIN)/biome format ."), "biome ci"),
        (lambda d: _replace(d / "Makefile", " --noEmit", ""), "tsc --noEmit"),
        (lambda d: _replace(d / "Makefile", "claude plugin test", "claude plugin list"), "claude plugin test"),
        (lambda d: _replace(d / "Makefile", " --strict", ""), "--strict"),
        (lambda d: _replace(d / "Makefile", "\t$(BIN)/claude plugin validate --strict .", "\t# $(BIN)/claude plugin validate --strict ."), "claude plugin validate"),
        (lambda d: _replace(d / "Makefile", " --ignore-scripts", ""), "--ignore-scripts"),
        (lambda d: _replace(d / "Makefile", "\t$(NPM) rebuild @anthropic-ai/claude-code\n", ""), "rebuild"),
        (lambda d: (d / "Makefile").unlink(), "Makefile"),
    ],
    ids=[
        "no-node-version",
        "node-major-only",
        "typescript-caret",
        "biome-latest",
        "claude-tilde",
        "no-lock",
        "no-biome-json",
        "no-tsconfig",
        "no-author",
        "biome-ci-replaced",
        "tsc-without-noemit",
        "plugin-test-replaced",
        "validate-not-strict",
        "validate-commented-out",
        "install-with-scripts",
        "no-rebuild",
        "no-makefile",
    ],
)
def test_typescript_lint_fails_and_names_what_is_missing(tmp_path: Path, mutate, named: str) -> None:
    repo = _ts_project(tmp_path / "red-cockpit")
    mutate(repo)
    result = build_gates._typescript_profile(repo)
    assert not result.passed and named in result.details, result.details


@pytest.mark.parametrize(
    "package_text",
    ["[]\n", "{\n", '{"devDependencies": ["typescript"]}\n', '{"devDependencies": {"typescript": 7}}\n'],
    ids=["a-list", "unparsable", "dev-dependencies-a-list", "version-not-a-string"],
)
def test_typescript_lint_fails_on_a_malformed_package_json_and_does_not_raise(
    tmp_path: Path, package_text: str
) -> None:
    repo = _ts_project(tmp_path / "red-cockpit")
    (repo / "package.json").write_text(package_text)
    result = build_gates._typescript_profile(repo)
    assert not result.passed and "package.json" in result.details, result.details


def _vendor(repo: Path, content: bytes) -> None:
    (repo / "vendor" / "claude-code").mkdir(parents=True)
    (repo / "vendor" / "claude-code" / "index.d.ts").write_bytes(content)


def test_vendored_types_that_match_the_pin_pass(tmp_path: Path) -> None:
    repo = _ts_project(tmp_path / "red-cockpit")
    _vendor(repo, f"// Written by Claude Code {TYPESCRIPT_PIN}.\ndeclare module 'claude-code' {{}}\n".encode())
    result = build_gates._typescript_profile(repo)
    assert result.passed and f"types match claude {TYPESCRIPT_PIN}" in result.details
    assert "SKIPPED" not in result.details


def test_vendored_types_from_another_version_fail_and_name_both(tmp_path: Path) -> None:
    repo = _ts_project(tmp_path / "red-cockpit")
    _vendor(repo, b"// Written by Claude Code 2.1.288.\n")
    result = build_gates._typescript_profile(repo)
    assert not result.passed
    assert "2.1.288" in result.details and TYPESCRIPT_PIN in result.details
    assert "make types" in result.details


@pytest.mark.parametrize(
    "content",
    [
        b"declare module 'claude-code' {}\n",
        b"\xef\xbb\xbf// Written by Claude Code 2.1.289.\n",
        b"// Written by Claude Code 2.1.289\n",
        b"\xff\xfe\x00 not utf-8\n",
        b"",
    ],
    ids=["no-header", "byte-order-mark", "no-trailing-dot", "not-utf-8", "empty"],
)
def test_vendored_types_with_an_unreadable_first_line_fail_and_do_not_raise(
    tmp_path: Path, content: bytes
) -> None:
    repo = _ts_project(tmp_path / "red-cockpit")
    _vendor(repo, content)
    result = build_gates._typescript_profile(repo)
    assert not result.passed and "vendor/claude-code/index.d.ts" in result.details, result.details


def test_vendored_types_with_crlf_still_match(tmp_path: Path) -> None:
    repo = _ts_project(tmp_path / "red-cockpit")
    _vendor(repo, f"// Written by Claude Code {TYPESCRIPT_PIN}.\r\nx\r\n".encode())
    assert build_gates._typescript_profile(repo).passed
```

`_replace` is defined in this file by the rust tests, above these. If ruff-format rewraps the long parametrize lines, accept its output.

- [ ] **Step 3: Run them to see them fail**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_gates_build.py -q -k "typescript or vendored or installed_or_vendored"`
Expected: FAIL with `AttributeError: module 'rail.gates.build' has no attribute '_typescript_tests'` (and the `has_tests` text mismatch).

- [ ] **Step 4: Implement the gate** (`src/rail/gates/build.py`)

Add `import json` and `import os` to the imports (alphabetical: `import json` after `from __future__`'s block, `import os` before `import re`).

After `_rust_tests` add:

```python
_NOT_THE_TYPESCRIPT_PROJECT = {".git", "node_modules", "vendor", ".claude-plugin"}


def _typescript_tests(repo: Path) -> list[Path]:
    """What `claude plugin test` runs, `*.test.ts` and `*.test.tsx`, outside installed and vendored
    code and the engine's own `.claude-plugin/types` (spec 2026-10-04-typescript-stack, decision 8).
    The walk prunes those directories instead of reading them: node_modules can be large."""
    found: list[Path] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _NOT_THE_TYPESCRIPT_PROJECT]
        found += [Path(root) / f for f in files if f.endswith((".test.ts", ".test.tsx"))]
    return sorted(found)
```

After `_rust_test_profile` add:

```python
def _typescript_test_profile(repo: Path) -> GateResult:
    return _counted(_typescript_tests(repo), "*.test.ts")
```

In `has_tests`, replace the `decl.stack is None` block body with:

```python
        python, go, rust = len(_python_tests(repo)), len(_go_tests(repo)), len(_rust_tests(repo))
        ts = len(_typescript_tests(repo))
        observed = (
            "no test file found (tests/test_*.py, *_test.go), none in tests/*.rs, none in *.test.ts"
            if not python and not go and not rust and not ts
            else f"{python} test file(s) (tests/test_*.py), {go} (*_test.go), {rust} (tests/*.rs), "
            f"{ts} (*.test.ts)"
        )
        return Need("stack", observed).result(Stage.BUILD, "tests")
```

After `_rust_profile` (before the `TEST_PROFILES` comment) add:

```python
CLAUDE_PACKAGE = "@anthropic-ai/claude-code"
TYPESCRIPT_DEV_DEPENDENCIES = ("typescript", "@biomejs/biome", CLAUDE_PACKAGE)
# What the Makefile must run, however `npm` and `BIN` are spelled: the call, read from live
# recipe lines only. The tools are called by path, never through npx, which fetches an absent one.
TYPESCRIPT_CALLS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("biome ci", re.compile(r"\bbiome\s+ci\b"), "$(BIN)/biome ci ."),
    ("tsc --noEmit", re.compile(r"\btsc\b[^\n]*\s--noEmit\b"), "$(BIN)/tsc --noEmit"),
    (
        "claude plugin test",
        re.compile(r"\bclaude\s+plugin\s+test\b"),
        "$(BIN)/claude plugin test .",
    ),
    (
        "claude plugin validate --strict",
        re.compile(r"\bclaude\s+plugin\s+validate\b[^\n]*\s--strict\b"),
        "$(BIN)/claude plugin validate --strict .",
    ),
    (
        "an install with --ignore-scripts",
        re.compile(r"(?:\binstall\b|\$\(INSTALL\)|\bci\b)[^\n]*\s--ignore-scripts\b"),
        "$(NPM) $(INSTALL) --ignore-scripts",
    ),
    (
        "a rebuild of the claude package",
        re.compile(rf"\brebuild\s+{re.escape(CLAUDE_PACKAGE)}\b"),
        f"$(NPM) rebuild {CLAUDE_PACKAGE}",
    ),
)
_TYPES_HEADER = re.compile(r"// Written by Claude Code (\d+\.\d+\.\d+)\.\r?\n?")
TYPES_FILE = Path("vendor") / "claude-code" / "index.d.ts"


def _json(path: Path) -> dict | str:
    """The parsed JSON object, or why it could not be read: the gate never raises."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return f"{path.name} is missing"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"{path.name} does not parse: {exc}"
    return data if isinstance(data, dict) else f"{path.name} is not a JSON object"


def _types_version(path: Path) -> str | None:
    """The version on the first line of the vendored types, or None when it cannot be read."""
    try:
        with path.open(encoding="utf-8") as handle:
            first = handle.readline()
    except (OSError, UnicodeDecodeError):
        return None
    match = _TYPES_HEADER.fullmatch(first)
    return match.group(1) if match else None


def _typescript_profile(repo: Path) -> GateResult:
    """The typescript profile as a pure read, on the model of `_rust_profile`: exact Node and tool
    pins, the lock, the config files, a plugin manifest with an author, the Makefile's calls, and
    the vendored engine types compared with the claude pin (spec 2026-10-04-typescript-stack,
    decision 9). The gate never runs Node, npm or claude; CI does."""

    def fail(why: str) -> GateResult:
        return GateResult(Stage.BUILD, "lint", False, why)

    try:
        node = (repo / ".node-version").read_text().strip()
    except FileNotFoundError:
        return fail(".node-version is missing")
    except (OSError, UnicodeDecodeError) as exc:
        return fail(f".node-version could not be read: {exc}")
    if not _EXACT_CHANNEL.match(node):
        return fail(
            f".node-version {node!r} is not an exact version (X.Y.Z): a floating Node changes "
            "what the tools do under a green project"
        )
    package = _json(repo / "package.json")
    if isinstance(package, str):
        return fail(package)
    dev = package.get("devDependencies")
    if not isinstance(dev, dict):
        return fail("package.json has no devDependencies object")
    for name in TYPESCRIPT_DEV_DEPENDENCIES:
        version = dev.get(name)
        if not isinstance(version, str) or not _EXACT_CHANNEL.match(version):
            return fail(
                f"package.json devDependencies {name} is {version!r}, not an exact version "
                "(X.Y.Z): no caret, no tilde, no range"
            )
    if not (repo / "package-lock.json").is_file():
        return fail("package-lock.json is missing: run `make sync`, then commit it")
    for name in ("biome.json", "tsconfig.json"):
        if not (repo / name).is_file():
            return fail(f"{name} is missing")
    plugin = _json(repo / ".claude-plugin" / "plugin.json")
    if isinstance(plugin, str):
        return fail(plugin)
    if not plugin.get("name"):
        return fail(".claude-plugin/plugin.json has no name")
    if not plugin.get("author"):
        return fail(
            ".claude-plugin/plugin.json declares no author: `claude plugin validate --strict` "
            "fails without one"
        )
    makefile = repo / "Makefile"
    if not makefile.is_file():
        return fail("Makefile is missing")
    try:
        text = makefile.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        return fail(f"Makefile could not be read: {exc}")
    runner = _recipe_lines(text)
    for what, pattern, remedy in TYPESCRIPT_CALLS:
        if not pattern.search(runner):
            return fail(f"the Makefile never runs {what} (`{remedy}`)")
    pin = dev[CLAUDE_PACKAGE]
    types = repo / TYPES_FILE
    if not types.is_file():
        types_note = "tsc: SKIPPED (vendor/claude-code absent)"
    else:
        written = _types_version(types)
        if written is None:
            return fail(
                f"{TYPES_FILE.as_posix()} does not start with `// Written by Claude Code "
                "X.Y.Z.`: copy it again with `make types`"
            )
        if written != pin:
            return fail(
                f"{TYPES_FILE.as_posix()} was written by Claude Code {written} but the pin is "
                f"{pin}: load the plugin in a Claude Code {pin} session, run `make types`, or "
                "bump the pin"
            )
        types_note = f"types match claude {pin}"
    return GateResult(
        Stage.BUILD,
        "lint",
        True,
        f"Node {node} and exact tool pins; biome, tsc, claude plugin test and validate called by "
        f"the Makefile; {types_note}",
    )
```

Do not register the profiles in the tables yet (Task 3: the `Stack` member does not exist).

- [ ] **Step 5: Run the gate tests**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_gates_build.py -q`
Expected: all pass, summary line read (the existing rust, go and python tests unchanged and green).

- [ ] **Step 6: Lint and commit**

```bash
unset VIRTUAL_ENV && uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run ruff format --check src/ tests/
git add src/rail/gates/build.py tests/helpers.py tests/test_gates_build.py
git commit -m "✨ feat(gates): read a typescript plugin without running node" -m "Tests are *.test.ts outside installed and vendored code; lint reads the exact pins, the lock, the manifest author, the Makefile's calls and compares vendored engine types with the claude pin." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `typescript` becomes a stack: model, copier, template, chains

The largest task: the enum entry turns the parity, marker and matrix tests red until the template exists, so they are updated first and the task ends green.

**Files:**
- Modify: `src/rail/model.py:72`, `copier.yml` (stack choices and validator), `src/rail/scaffold.py` (message, table), `src/rail/gates/build.py` (the two tables), `tests/combinations.py`
- Create under `template/project/`: the 10 files listed in Step 4
- Modify under `template/project/`: `Makefile.jinja`, `.gitignore.jinja`, `.claude/settings.json.jinja`, `CLAUDE.md.jinja`, `AGENTS.md.jinja`, `rail.yaml.jinja`
- Test: `tests/test_scaffold.py`, `tests/test_gates_build.py`

**Interfaces:**
- Consumes: Task 1's `NOT_AT_PROD`; Task 2's `_typescript_test_profile` and `_typescript_profile`.
- Produces: `Stack.TYPESCRIPT`, `rail.scaffold.TYPESCRIPT_PROD_REFUSAL`.

- [ ] **Step 1: Update the expectation tables and write the new tests** (`tests/test_scaffold.py`)

In `STACK_LINE` add (after the `Stack.RUST` entry):

```python
    Stack.TYPESCRIPT: (
        "A Claude Code plugin in TypeScript: Node pinned by `.node-version`, Biome, "
        "`tsc --noEmit` against the vendored engine types, `claude plugin test` and "
        "`claude plugin validate --strict`."
    ),
```

In `STACK_GATES` add `Stack.TYPESCRIPT: "`node_modules/.bin/claude plugin test .`",`.
In `GITIGNORED` add `Stack.TYPESCRIPT: ["node_modules/", ".claude-plugin/types/"],`.

Replace `test_rust_at_prod_is_the_only_excluded_combination` with:

```python
def test_the_stacks_with_no_prod_template_are_the_only_excluded_combinations() -> None:
    """Every stack × tier pair is rendered by some combination, except exactly the pairs that
    have no prod template: rust and typescript at prod."""
    covered = {(c.stack, c.tier) for c in COMBINATIONS}
    refused = {(Stack.RUST, Tier.PROD), (Stack.TYPESCRIPT, Tier.PROD)}
    assert {(s, t) for s in Stack for t in Tier} - covered == refused
    assert EXCLUDED == refused
```

Add after `test_render_refuses_rust_at_prod_with_a_bare_message`:

```python
def test_typescript_at_prod_is_refused(template_dir: Path, tmp_path: Path) -> None:
    """Refused before copier by `rail new` (a bare message) and by copier's own validator for a
    direct run (spec 2026-10-04-typescript-stack, decision 1)."""
    import copier

    from rail.scaffold import NOT_AT_PROD, TYPESCRIPT_PROD_REFUSAL

    assert NOT_AT_PROD[Stack.TYPESCRIPT] == TYPESCRIPT_PROD_REFUSAL
    project = _project(
        template_dir,
        tmp_path / "red-cockpit",
        slug="red-cockpit",
        stack=Stack.TYPESCRIPT,
        tier=Tier.PROD,
    )
    with pytest.raises(ScaffoldError) as raised:
        _ = project.answers
    assert str(raised.value) == TYPESCRIPT_PROD_REFUSAL
    with pytest.raises(ScaffoldError) as rendered:
        render(project)
    assert str(rendered.value) == TYPESCRIPT_PROD_REFUSAL
    with pytest.raises(Exception, match="typescript at tier prod is not templated yet"):
        copier.run_copy(
            str(template_dir),
            tmp_path / "direct",
            data={
                "project": "red-cockpit",
                "description": "d",
                "brain_key": "red-cockpit",
                "tier": "prod",
                "stack": "typescript",
                "deploy_target": "vps-traefik",
                "healthcheck": "https://cockpit.example.invalid/healthz",
            },
            defaults=True,
            quiet=True,
            unsafe=False,
        )
    assert not (tmp_path / "direct" / "rail.yaml").exists()


def test_the_copier_validator_says_what_rail_new_says_for_typescript() -> None:
    from rail.scaffold import TYPESCRIPT_PROD_REFUSAL

    assert TYPESCRIPT_PROD_REFUSAL in (ROOT / "copier.yml").read_text()


@pytest.mark.parametrize("combo", COMBINATIONS, ids=[c.label for c in COMBINATIONS])
def test_every_rendered_settings_file_allows_npm_for_typescript_only(
    renders: dict[Combo, Path], combo: Combo
) -> None:
    allow = json.loads((renders[combo] / ".claude" / "settings.json").read_text())["permissions"]
    assert ("Bash(npm:*)" in allow["allow"]) is (combo.stack is Stack.TYPESCRIPT)
    assert "Bash(npx:*)" not in allow["allow"]


@pytest.mark.parametrize("combo", COMBINATIONS, ids=[c.label for c in COMBINATIONS])
def test_only_typescript_declares_the_vendored_types_as_generated(
    renders: dict[Combo, Path], combo: Combo
) -> None:
    """`review.ignored_globs` keeps 15,000 generated lines away from a judge, with a reason; no
    other stack declares an exception at birth. The manifest the render writes still loads."""
    import yaml

    from rail.model import try_load_rail_config

    gates = yaml.safe_load((renders[combo] / "rail.yaml").read_text())["gates"]
    if combo.stack is Stack.TYPESCRIPT:
        assert gates["review.ignored_globs"]["value"] == ["vendor/claude-code/**"]
        assert gates["review.ignored_globs"]["reason"].strip()
    else:
        assert gates == {}
    assert try_load_rail_config(renders[combo]) is not None


TYPESCRIPT_PINS = {
    "typescript": "7.0.2",
    "@biomejs/biome": "2.5.15",
    "@anthropic-ai/claude-code": "2.1.289",
}
TYPESCRIPT_NODE = "24.21.0"


def test_render_typescript_bootstrap(template_dir: Path, tmp_path: Path) -> None:
    dest = render(
        _project(
            template_dir, tmp_path / "red-throwaway", slug="red-throwaway", stack=Stack.TYPESCRIPT
        )
    )

    plugin = json.loads((dest / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["name"] == "red-throwaway" and plugin["version"] == "0.1.0"
    assert plugin["description"] == "A disposable HTTP probe."
    assert plugin["author"] == {"name": "hawkixs"}
    assert json.loads((dest / "hooks" / "hooks.json").read_text()) == {
        "modules": ["./register.ts"]
    }
    register = (dest / "hooks" / "register.ts").read_text()
    assert sorted(re.findall(r"^import .* from '([^']+)'$", register, re.MULTILINE)) == [
        "../src/core.ts",
        "claude-code",
    ]
    assert "red-throwaway-hello" in register
    assert "claude-code/testing" in (dest / "src" / "core.test.ts").read_text()

    package = json.loads((dest / "package.json").read_text())
    assert package["name"] == "red-throwaway"
    assert package["private"] is True and package["type"] == "module"
    assert "dependencies" not in package
    assert package["devDependencies"] == TYPESCRIPT_PINS
    assert (dest / ".node-version").read_text().strip() == TYPESCRIPT_NODE
    tsconfig = json.loads((dest / "tsconfig.json").read_text())
    assert tsconfig["include"] == ["vendor/claude-code", "hooks", "src"]
    assert "paths" not in tsconfig["compilerOptions"]
    assert tsconfig["compilerOptions"]["noEmit"] is True
    biome = json.loads((dest / "biome.json").read_text())
    assert "!vendor" in biome["files"]["includes"]

    assert not (dest / "package-lock.json").exists() and not (dest / "vendor").exists()
    assert not (dest / "pyproject.toml").exists() and not (dest / "go.mod").exists()
    assert not (dest / "Cargo.toml").exists()

    ignored = (dest / ".gitignore").read_text().splitlines()
    assert "node_modules/" in ignored and ".claude-plugin/types/" in ignored
    assert not any("package-lock" in line or "vendor" in line for line in ignored)

    claude = (dest / "CLAUDE.md").read_text()
    structure = claude.split("## Structure", 1)[1]
    for entry in (
        ".claude-plugin/plugin.json",
        "hooks/",
        "src/",
        "package.json",
        "package-lock.json",
        ".node-version",
        "tsconfig.json",
        "biome.json",
        "vendor/claude-code/",
    ):
        assert f"── {entry}" in structure, entry
    agents = (dest / "AGENTS.md").read_text()
    gates = agents.split("## Gates", 1)[1].split("## Brain MCP", 1)[0]
    assert gates.index("`make sync` first") < gates.index("Then `make ci`")
    assert "`make types`" in gates and "named SKIP" in gates

    _after(
        _dry_run(dest, "ci", "RAIL_FLAGS=--ci"),
        "node_modules/.bin/biome ci .",
        "node_modules/.bin/tsc --noEmit",
        "node_modules/.bin/claude plugin test .",
        "node_modules/.bin/claude plugin validate --strict .",
        "rail check --ci",
    )
    _after(
        _dry_run(dest, "sync"),
        "npm install --ignore-scripts",
        "npm rebuild @anthropic-ai/claude-code",
    )
    _after(
        _dry_run(dest, "sync", "INSTALL=ci"),
        "npm ci --ignore-scripts",
        "npm rebuild @anthropic-ai/claude-code",
    )
    assert not any("npx" in line for line in _dry_run(dest, "ci", "sync", "types"))


def _make(dest: Path, *args: str) -> subprocess.CompletedProcess[str]:
    make = shutil.which("make")
    assert make, "make is required: the Makefile is read by make itself"
    return subprocess.run([make, *args], cwd=dest, capture_output=True, text=True)


def test_typescript_typecheck_is_a_named_skip_until_the_types_are_vendored(
    template_dir: Path, tmp_path: Path
) -> None:
    """No Node is needed: with no vendored types the recipe never reaches `tsc`. With them and no
    installed `tsc`, it fails on the missing file: nothing is fetched (spec decision 4)."""
    dest = render(
        _project(template_dir, tmp_path / "red-throwaway", slug="red-throwaway", stack=Stack.TYPESCRIPT)
    )
    skipped = _make(dest, "typecheck")
    assert skipped.returncode == 0, skipped.stderr
    assert "typecheck: SKIPPED, vendor/claude-code is absent" in skipped.stdout

    (dest / "vendor" / "claude-code").mkdir(parents=True)
    (dest / "vendor" / "claude-code" / "index.d.ts").write_text("// Written by Claude Code 2.1.289.\n")
    attempted = _make(dest, "typecheck")
    assert attempted.returncode != 0
    assert "node_modules/.bin/tsc" in attempted.stdout + attempted.stderr


def test_typescript_make_types_copies_the_core_file_only(
    template_dir: Path, tmp_path: Path
) -> None:
    dest = render(
        _project(template_dir, tmp_path / "red-throwaway", slug="red-throwaway", stack=Stack.TYPESCRIPT)
    )
    missing = _make(dest, "types")
    assert missing.returncode != 0
    assert ".claude-plugin/types/claude-code/index.d.ts" in missing.stdout + missing.stderr

    types = dest / ".claude-plugin" / "types"
    (types / "claude-code").mkdir(parents=True)
    (types / "claude-code" / "index.d.ts").write_text("// Written by Claude Code 2.1.289.\nx\n")
    (types / "claude-code-mcp").mkdir()
    (types / "claude-code-mcp" / "index.d.ts").write_text("// this machine's MCP tools\n")
    done = _make(dest, "types")
    assert done.returncode == 0, done.stderr
    vendored = dest / "vendor" / "claude-code"
    assert (vendored / "index.d.ts").read_text() == "// Written by Claude Code 2.1.289.\nx\n"
    assert [p.name for p in vendored.iterdir()] == ["index.d.ts"]
    assert not (dest / "vendor" / "claude-code-mcp").exists()


@pytest.mark.parametrize("slug", ["red-x", f"red-{_LONG_KEBAB}"], ids=["short", "long"])
def test_typescript_files_fit_biome_width_at_any_slug_length(
    template_dir: Path, tmp_path: Path, slug: str
) -> None:
    """`biome ci` reformats a line past 100 columns, and a fresh scaffold must pass `make lint`
    whatever its name: the slug appears in one constant, never in a call (Review Focus 1). This
    test does not run Node; the host verification runs `biome ci` on a long-slug render."""
    dest = render(_project(template_dir, tmp_path / slug, slug=slug, stack=Stack.TYPESCRIPT))
    for relative in (
        "hooks/register.ts",
        "hooks/hooks.json",
        "src/core.ts",
        "src/core.test.ts",
        "package.json",
        ".claude-plugin/plugin.json",
        "tsconfig.json",
        "biome.json",
    ):
        for line in (dest / relative).read_text().splitlines():
            assert len(line) <= 100, f"{relative}: line too long for biome: {line!r}"
            assert line == line.rstrip(), f"{relative}: trailing whitespace: {line!r}"
            assert "\t" not in line, f"{relative}: tab: {line!r}"
```

Placement matters: `_dry_run`, `_after` and `_LONG_KEBAB` are defined in the rust render tests, and `_LONG_KEBAB` is read at import by a `parametrize`. Put `test_typescript_at_prod_is_refused`, the validator test, the settings test and the `rail.yaml` test right after `test_render_refuses_rust_at_prod_with_a_bare_message`; put `TYPESCRIPT_PINS`, `TYPESCRIPT_NODE`, `test_render_typescript_bootstrap`, `_make`, the two `make` tests and the width test after `test_rust_smoke_and_main_fit_rustfmt_width_at_any_slug_length`, before `_answered`.

Also in `tests/test_scaffold.py` change `test_every_stack_the_cli_offers_is_a_copier_choice`'s docstring nothing: it passes once both lists agree.

In `tests/test_gates_build.py` add the rendered-tree tests, next to `_rendered_rust`:

```python
def _rendered_typescript(tmp_path: Path) -> Path:
    src = tmp_path / "template-src"
    src.mkdir()
    shutil.copy(ROOT / "copier.yml", src / "copier.yml")
    shutil.copytree(ROOT / "template", src / "template")
    dest = render(
        NewProject(
            slug="red-cockpit",
            description="A disposable plugin.",
            tier=Tier.DEV,
            stack=Stack.TYPESCRIPT,
            brain_key="red-cockpit",
            dest=tmp_path / "red-cockpit",
            template=str(src),
        )
    )
    (dest / "package-lock.json").write_text('{"lockfileVersion": 3}\n')  # what `make sync` writes
    return dest


def test_typescript_lint_and_tests_pass_on_the_rendered_tree(tmp_path: Path) -> None:
    dest = _rendered_typescript(tmp_path)
    result = lint(dest)
    assert result.passed, result.details
    assert "24.21.0" in result.details and "SKIPPED" in result.details
    assert has_tests(dest).passed


def test_a_fresh_typescript_scaffold_fails_lint_until_make_sync_writes_the_lock(
    tmp_path: Path,
) -> None:
    dest = _rendered_typescript(tmp_path)
    (dest / "package-lock.json").unlink()
    result = lint(dest)
    assert not result.passed and "package-lock.json" in result.details
    assert "make sync" in result.details


def test_typescript_lint_fails_on_the_rendered_tree_when_a_call_is_dropped(tmp_path: Path) -> None:
    dest = _rendered_typescript(tmp_path)
    _replace(dest / "Makefile", "claude plugin validate --strict", "claude plugin validate")
    result = lint(dest)
    assert not result.passed and "--strict" in result.details, result.details
```

Extend `test_every_stack_has_a_build_profile`: it already asserts equality with `set(Stack)`, no edit needed.

- [ ] **Step 2: Run to see them fail (and the parity, marker and matrix tests turn red)**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_scaffold.py tests/test_gates_build.py -q -x`
Expected: FAIL (`Stack` has no `TYPESCRIPT`).

- [ ] **Step 3: The model, copier, scaffold, tables and combinations**

`src/rail/model.py:72`: after `RUST = "rust"` add `TYPESCRIPT = "typescript"`; the enum order is `PYTHON, GO, RUST, TYPESCRIPT, DOCS`.

`copier.yml`: `choices: [python, go, rust, typescript, docs]`, and the validator becomes

```yaml
  validator: >-
    {% if stack == 'rust' and tier == 'prod' %}
    rust at tier prod is not templated yet (no image, no service): scaffold at dev and promote when the rust prod template lands
    {% elif stack == 'typescript' and tier == 'prod' %}
    typescript at tier prod is not templated yet (a plugin has no image and no service): scaffold at dev
    {% endif %}
```

`src/rail/scaffold.py`, next to `RUST_PROD_REFUSAL` and updating the table:

```python
# A Claude Code plugin is distributed through a marketplace, not released as an image and deployed
# (spec 2026-10-04-typescript-stack, decision 1). copier.yml's validator says the same.
TYPESCRIPT_PROD_REFUSAL = (
    "typescript at tier prod is not templated yet (a plugin has no image and no service): "
    "scaffold at dev"
)
NOT_AT_PROD: dict[Stack, str] = {
    Stack.RUST: RUST_PROD_REFUSAL,
    Stack.TYPESCRIPT: TYPESCRIPT_PROD_REFUSAL,
}
```

Update Task 1's test `test_not_at_prod_holds_each_refusal_once` to
`assert NOT_AT_PROD == {Stack.RUST: RUST_PROD_REFUSAL, Stack.TYPESCRIPT: TYPESCRIPT_PROD_REFUSAL}` (importing both).

`src/rail/gates/build.py` tables: add `Stack.TYPESCRIPT: _typescript_test_profile,` to `TEST_PROFILES` and `Stack.TYPESCRIPT: _typescript_profile,` to `LINT_PROFILES`.

`tests/combinations.py`: `EXCLUDED = frozenset({(Stack.RUST, Tier.PROD), (Stack.TYPESCRIPT, Tier.PROD)})`; update the module docstring's last sentence to "One list, two exclusions: rust and typescript at tier prod are not templated yet."

- [ ] **Step 4: The template files**

Create, under `template/project/` (every file keeps the `.jinja` suffix, for the name's conditional to render; the stack is the only conditional):

1. `{% if stack == 'typescript' %}.claude-plugin{% endif %}/plugin.json.jinja`

```json
{
  "name": "{{ project }}",
  "version": "0.1.0",
  "description": "{{ description }}",
  "author": {
    "name": "hawkixs"
  }
}
```

2. `{% if stack == 'typescript' %}hooks{% endif %}/hooks.json.jinja`

```json
{ "modules": ["./register.ts"] }
```

3. `{% if stack == 'typescript' %}hooks{% endif %}/register.ts.jinja`

```ts
import type { Register } from 'claude-code'
import { greeting } from '../src/core.ts'

const COMMAND = '{{ project }}-hello'

let calls = 0

export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: COMMAND, description: 'Answer a greeting' })
    return next(e)
  })
  on('command.run', { command: COMMAND }, async () => {
    calls += 1
    return { text: greeting('{{ project }}', calls) }
  })
}
```

4. `{% if stack == 'typescript' %}src{% endif %}/core.ts.jinja`

```ts
// Pure logic: no `$` and no engine import, so a test needs nothing but this file.
export function greeting(name: string, count: number): string {
  return `hello ${name} (${count})`
}
```

5. `{% if stack == 'typescript' %}src{% endif %}/core.test.ts.jinja`

```ts
import { expect, test } from 'claude-code/testing'
import { greeting } from './core.ts'

test('greeting counts', () => {
  expect(greeting('rail', 2)).toBe('hello rail (2)')
})
```

6. `{% if stack == 'typescript' %}package.json{% endif %}.jinja`

```json
{
  "name": "{{ project }}",
  "private": true,
  "type": "module",
  "devDependencies": {
    "@anthropic-ai/claude-code": "2.1.289",
    "@biomejs/biome": "2.5.15",
    "typescript": "7.0.2"
  }
}
```

7. `{% if stack == 'typescript' %}tsconfig.json{% endif %}.jinja`

```json
{
  "compilerOptions": {
    "target": "es2023",
    "lib": ["es2023"],
    "types": [],
    "module": "esnext",
    "moduleResolution": "bundler",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noEmit": true,
    "skipLibCheck": true,
    "allowImportingTsExtensions": true
  },
  "include": ["vendor/claude-code", "hooks", "src"]
}
```

8. `{% if stack == 'typescript' %}biome.json{% endif %}.jinja`

```json
{
  "$schema": "./node_modules/@biomejs/biome/configuration_schema.json",
  "files": { "includes": ["**", "!node_modules", "!vendor", "!.claude-plugin/types"] },
  "formatter": { "indentStyle": "space", "indentWidth": 2, "lineWidth": 100 },
  "linter": { "enabled": true },
  "javascript": { "formatter": { "quoteStyle": "single", "semicolons": "asNeeded" } }
}
```

9. `{% if stack == 'typescript' %}.node-version{% endif %}.jinja`

```
24.21.0
```

(The `tsconfig.json` and `biome.json` above are the exact files measured on 2026-10-04: `tsc 7.0.2 -p .` exit 0, `biome ci .` exit 0, `claude plugin validate --strict .` passed with them.)

`Makefile.jinja`: in the `.PHONY` line change the tail to

```
.PHONY: sync lint test check ci serve image{% if stack == 'go' %} vuln{% elif stack == 'rust' %} deny{% elif stack == 'typescript' %} typecheck validate types{% endif %}
```

insert, before `{% elif stack == 'docs' -%}`, this branch (recipe lines are TAB-indented):

```
{% elif stack == 'typescript' -%}
## Where every target finds its tools: the project's own node_modules/.bin, never npx, which
## fetches a tool that is absent. `make NPM=<wrapper> sync` installs through a wrapper.
NPM ?= npm
BIN ?= node_modules/.bin
## CI runs `make sync INSTALL=ci`: `npm ci` fails on a missing or stale package-lock.json instead
## of writing one. Locally `npm install` writes the lock, or refreshes a stale one.
INSTALL ?= install

## Install the pinned tools with install scripts off, then run the one script that is needed:
## the claude package's postinstall puts its native binary in place.
sync:
	$(NPM) $(INSTALL) --ignore-scripts
	$(NPM) rebuild @anthropic-ai/claude-code

## Format and lint check, no writes
lint:
	$(BIN)/biome ci .

## Type check against the engine's types, once this project has copied them (`make types`).
## Until then the skip is named, never silent.
typecheck:
	@if [ -f vendor/claude-code/index.d.ts ]; then $(BIN)/tsc --noEmit; else echo "typecheck: SKIPPED, vendor/claude-code is absent (load the plugin in a Claude Code session, then run make types)"; fi

## Run the plugin's tests with the engine's own runner
test:
	$(BIN)/claude plugin test .

## What the engine accepts of the manifest and the hooks, warnings included
validate:
	$(BIN)/claude plugin validate --strict .

## Copy the engine's types out of the folder the engine ignores, into the repository. Only the
## core file: its sibling for MCP tools lists what is connected on one machine.
types:
	@test -f .claude-plugin/types/claude-code/index.d.ts || { echo "no .claude-plugin/types/claude-code/index.d.ts: load this plugin in a Claude Code session of the pinned version, which writes it"; exit 1; }
	mkdir -p vendor/claude-code
	cp .claude-plugin/types/claude-code/index.d.ts vendor/claude-code/index.d.ts
```

and the last line of the file becomes

```
ci: lint {% if stack == 'typescript' %}typecheck {% endif %}test {% if stack == 'go' %}vuln {% elif stack == 'rust' %}deny {% elif stack == 'typescript' %}validate {% endif %}check
```

`.gitignore.jinja`: insert before `{% elif stack == 'docs' -%}`:

```
{% elif stack == 'typescript' -%}
node_modules/
.claude-plugin/types/
```

`.claude/settings.json.jinja`: replace `"Bash(cargo:*)"{% elif stack == 'docs' %}{% endif %}` with

```
"Bash(cargo:*)"{% elif stack == 'typescript' %},
      "Bash(npm:*)"{% elif stack == 'docs' %}{% endif %}
```

`CLAUDE.md.jinja`, `stack` chain: insert before `{%- elif stack == 'docs' -%}`:

```
{%- elif stack == 'typescript' -%}
A Claude Code plugin in TypeScript: Node pinned by `.node-version`, Biome, `tsc --noEmit` against the vendored engine types, `claude plugin test` and `claude plugin validate --strict`.
```

`structure` chain: insert before `{%- elif stack == 'docs' %}`:

```
{%- elif stack == 'typescript' %}
├── .claude-plugin/plugin.json
├── hooks/             # hooks.json and the module it names
├── src/               # pure logic and its *.test.ts files
├── package.json       # exact devDependencies: typescript, Biome, claude
├── package-lock.json  # written by `make sync`, committed
├── .node-version      # the one Node pin
├── tsconfig.json
├── biome.json
└── vendor/claude-code/ # the engine's types, copied by `make types`
```

`AGENTS.md.jinja`, `gates` chain: insert before `{%- elif stack == 'docs' -%}`:

```
{%- elif stack == 'typescript' -%}
Run `make sync` first: it installs the pinned tools from `package-lock.json` (writing it the
first time) and runs the one install script the claude package needs. Then `make ci`. This
stack's own commands: `node_modules/.bin/biome ci .`, `node_modules/.bin/tsc --noEmit`,
`node_modules/.bin/claude plugin test .`, `node_modules/.bin/claude plugin validate --strict .`.
The engine's types are not in this repository until `make types` copies them (a Claude Code
session of the pinned version writes them into `.claude-plugin/types/` when it loads the plugin);
until then `make typecheck` prints a named SKIP.
```

`rail.yaml.jinja`: replace the final line `gates: {}` with

```
{%- if stack == 'typescript' %}
gates:
  review.ignored_globs:
    value: ["vendor/claude-code/**"]
    reason: "generated engine types, copied by make types, not authored"
{%- else %}
gates: {}
{%- endif %}
```

(the block starts on the line after the existing `{%- endif %}` of the prod block; the file still ends with one newline).

- [ ] **Step 5: Run the tests**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_scaffold.py tests/test_gates_build.py -q`
Expected: all pass, summary line read. If `test_typescript_files_fit_biome_width_at_any_slug_length` or a chain test fails, fix the template, never the test.

- [ ] **Step 6: Run the whole suite**

Run: `unset VIRTUAL_ENV && uv run pytest -q > /tmp/ts-suite.txt 2>&1; echo "exit=$?"; tail -3 /tmp/ts-suite.txt`
Expected: exit 0, summary line read (the audit golden, the address test and the guidance tests all run over the new combinations).

- [ ] **Step 7: Lint and commit**

```bash
unset VIRTUAL_ENV && uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run ruff format --check src/ tests/
git add -A src tests template copier.yml
git commit -m "✨ feat(template): the typescript stack for Claude Code plugins" -m "Stack.TYPESCRIPT refused at prod, the plugin files derived from the reference plugin, a Makefile that calls tools by path, and the six stack chains. Versions read on 2026-10-04: Node 24.21.0, typescript 7.0.2, biome 2.5.15, claude 2.1.289." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: CI sets up Node from the project's file

**Files:**
- Modify: `.github/workflows/rail-ci.yml` (input description; two steps after the rust steps)
- Test: `tests/test_workflows.py`

**Interfaces:**
- Consumes: the Makefile's `INSTALL` variable and `sync` target (Task 3).

- [ ] **Step 1: Write the failing test** (`tests/test_workflows.py`, after `test_rail_ci_installs_rust_from_the_project_pin`)

```python
def test_rail_ci_installs_node_from_the_project_pin() -> None:
    """setup-node reads the project's .node-version (the one pin, never repeated here) and keys
    its npm cache on the lock; `make sync INSTALL=ci` is `npm ci`, so a missing or stale lock
    fails there instead of being written (spec 2026-10-04-typescript-stack, decision 7)."""
    wf = _load("rail-ci.yml")
    assert "typescript" in wf["on"]["workflow_call"]["inputs"]["stack"]["description"]
    steps = _steps(wf)
    node = [s for s in steps if s.get("if") == "inputs.stack == 'typescript'"]
    assert [s["name"] for s in node] == [
        "Set up Node (the version in .node-version)",
        "Install from the lock (npm ci, claude rebuilt)",
    ]
    setup, install = node
    assert PINNED.match(setup["uses"])
    assert setup["uses"].startswith("actions/setup-node@")
    assert setup["with"] == {"node-version-file": ".node-version", "cache": "npm"}
    assert install["run"].strip() == "make sync INSTALL=ci"

    names = [s.get("name") for s in steps]
    assert names.index(install["name"]) < names.index("Project CI (make ci, rail gates in CI scope)")
    assert names.index(setup["name"]) < names.index(install["name"])
    assert not re.search(r"\b\d{2}\.\d+\.\d+\b", str(setup)), "the Node version lives in the project"
```

- [ ] **Step 2: Run it to see it fail**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_workflows.py::test_rail_ci_installs_node_from_the_project_pin -q`
Expected: FAIL (the description lacks `typescript`).

- [ ] **Step 3: Implement** (`.github/workflows/rail-ci.yml`)

Change the input description to `"python | go | rust | typescript | docs — must match rail.yaml"`. After the step named "Fetch against the committed lock, install cargo-deny" (and before "Install gitleaks") insert:

```yaml
      # Node: read from the project's own .node-version, the one pin, never repeated here. setup-node
      # keys its npm cache on package-lock.json. `make sync INSTALL=ci` is `npm ci`: a missing or
      # stale lock fails here instead of being written, install scripts stay off, and the one the
      # claude package needs is run by `npm rebuild` (spec 2026-10-04-typescript-stack, decision 3).
      - name: Set up Node (the version in .node-version)
        if: inputs.stack == 'typescript'
        uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0
        with:
          node-version-file: .node-version
          cache: npm

      - name: Install from the lock (npm ci, claude rebuilt)
        if: inputs.stack == 'typescript'
        run: make sync INSTALL=ci
```

- [ ] **Step 4: Run the workflow tests**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_workflows.py -q`
Expected: all pass (every `uses:` is 40-hex pinned, the rust test unchanged), summary line read.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/rail-ci.yml tests/test_workflows.py
git commit -m "👷 ci(rail-ci): set up Node from the project's .node-version" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `rail upgrade --stack typescript`

The mechanism exists; this task proves it for the new stack and parametrises the refusals over both stacks.

**Files:**
- Test: `tests/test_scaffold.py` (the upgrade tests near the end)

**Interfaces:**
- Consumes: Task 1's `NOT_AT_PROD`, Task 3's `Stack.TYPESCRIPT`.

- [ ] **Step 1: Write the tests**

In `test_upgrade_refuses_a_transition_not_from_docs` extend the parametrization:

```python
        ("docs", Stack.TYPESCRIPT, "prod", "typescript at tier prod is not templated yet"),
        ("python", Stack.TYPESCRIPT, "dev", "only out of docs"),
        ("typescript", Stack.DOCS, "dev", "cannot switch to docs"),
```

and the ids list gains `"docs-typescript-at-prod"`, `"python-typescript"`, `"typescript-docs"` in the same order.

Replace `test_upgrade_refuses_answers_holding_rust_at_prod` with a parametrized version:

```python
@pytest.mark.parametrize("stack", ["rust", "typescript"])
def test_upgrade_refuses_answers_holding_a_stack_with_no_prod_template(
    tmp_path: Path, stack: str
) -> None:
    """Copier would drop the invalid answer and, under defaults, re-render the project as python:
    refused with or without --stack."""
    repo = _answered(tmp_path / "red-life", stack=stack, tier="prod")
    with pytest.raises(ScaffoldError, match=f"{stack} at tier prod"):
        upgrade(repo, update=_never, resolve=lambda t, **k: "c" * 40)
```

Add, after `test_upgrade_switches_docs_to_rust`:

```python
def test_upgrade_switches_docs_to_typescript(template_dir: Path, tmp_path: Path) -> None:
    """Real copier on a tagged throwaway template: the answer, rail.yaml and the CI call all say
    typescript afterwards, the plugin files exist, and rail.yaml carries the declared exception
    for the vendored types, written by copier's merge alone."""
    _tagged_template(template_dir, "v0.1.0")
    project = _project(
        template_dir,
        tmp_path / "red-cockpit",
        slug="red-cockpit",
        stack=Stack.DOCS,
        tier=Tier.DEV,
        template_ref="v0.1.0",
    )
    new_project(project, publish=False, clock=CLOCK, resolve=_pin)
    _tagged_template(template_dir, "v0.2.0")

    upgrade(project.dest, stack=Stack.TYPESCRIPT, resolve=_pin)

    dest = project.dest
    assert "stack: typescript" in (dest / ANSWERS_FILE).read_text()
    assert "stack: typescript" in (dest / "rail.yaml").read_text()
    ci = dest / ".github" / "workflows" / "continuous-integration.yml"
    assert "stack: typescript" in ci.read_text()
    assert (dest / ".claude-plugin" / "plugin.json").is_file()
    assert (dest / "package.json").is_file() and (dest / ".node-version").is_file()
```

Add to `test_cli_upgrade_takes_a_stack` a second parametrize case by turning it into:

```python
@pytest.mark.parametrize("stack", [Stack.RUST, Stack.TYPESCRIPT])
def test_cli_upgrade_takes_a_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stack: Stack
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "rail.commands.upgrade.upgrade", lambda repo, **kw: calls.append(kw) or "v0.6.0"
    )
    result = CliRunner().invoke(main, ["upgrade", "--repo", str(tmp_path), "--stack", stack.value])
    assert result.exit_code == 0, result.output
    assert calls == [{"stack": stack}]
    assert "make sync" in result.output
```

(`new_project` flow with `Stack.DOCS` at `dev` is the one the rust test already uses; it needs no Node.)

- [ ] **Step 2: Run them**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_scaffold.py -q -k "upgrade"`
Expected: all pass, summary line read. If `test_upgrade_switches_docs_to_typescript` fails because copier's three-way merge conflicts on `rail.yaml`'s `gates:` block, the template's `rail.yaml.jinja` whitespace is wrong: fix the template (Task 3's last file edit), not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_scaffold.py
git commit -m "✅ test(upgrade): a docs project switches to typescript, never at prod" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Proof

No new behaviour. This task produces the evidence the spec's criteria 6 to 9 ask for, and records it for the pull request.

**Files:** none modified, except stack lists found in Step 2.

- [ ] **Step 1: `make ci` with its summary line read**

```bash
unset VIRTUAL_ENV && make ci > /tmp/ts-ci.txt 2>&1; echo "exit=$?"; grep -E "passed|failed|error" /tmp/ts-ci.txt | tail -5
```
Expected: `exit=0`, `N passed`, no failure; then `uv run rail check` passes (read its last line `passed M/M`). Criterion 7: `git diff --stat tests/golden/audit-matrix.json` is empty.

- [ ] **Step 2: Stack lists elsewhere in this repository**

Run: `grep -rnE "python \| go|python, go|go \| rust|\`rust\`" CLAUDE.md AGENTS.md README.md docs/adr skills src template .github 2>/dev/null`
For each hit that enumerates the stacks, add `typescript`; leave dated specs and plans as they are (history). Run `unset VIRTUAL_ENV && uv run pytest -q` again if anything changed, and commit with `📝 docs: name the typescript stack where the stacks are listed`.

- [ ] **Step 3: Criterion 8, this spec and plan pass their gates explicitly**

```bash
unset VIRTUAL_ENV
W=$PWD; T=$(mktemp -d)
git archive HEAD | tar -x -C "$T"
find "$T/docs/specs" -type f ! -name "2026-10-04-typescript-stack.md" -delete
find "$T/docs/plans" -type f ! -name "2026-10-04-typescript-stack.md" -delete
cd "$T" && git init -q && git add -A \
  && git -c user.name=rail -c user.email=rail@example.invalid commit -qm "chore: copy" \
  && "$W/.venv/bin/rail" check --ci design && "$W/.venv/bin/rail" check --ci plan
cd "$W"
```
Expected: `PASS design.spec` and `PASS plan.plan`, the two lines recorded in the pull request. Run it from the worktree root, after the spec and the plan are committed.

- [ ] **Step 4: Criterion 9, host verification on a throwaway render** (the host has Node and npm; the scratch directory is under the session's scratchpad, never in the repository)

```bash
unset VIRTUAL_ENV
D=$(mktemp -d) && uv run rail new red-throwaway --description "throwaway" --no-remotes \
  --template . --template-ref HEAD --dest "$D/red-throwaway" --stack typescript --tier dev
cd "$D/red-throwaway" && git init -q 2>/dev/null; make sync && ls package-lock.json \
  && node_modules/.bin/claude --version && make lint test validate && make typecheck
```
Expected: `make sync` writes `package-lock.json`; `claude --version` prints `2.1.289 (Claude Code)` (the rebuild worked); `make lint test validate` exit 0, with `1 pass` from `plugin test` and `Validation passed`; `make typecheck` prints `typecheck: SKIPPED, vendor/claude-code is absent`. If `rail new` is refused for lack of a remote or a roster row, render with `uv run python -c` through `rail.scaffold.render` exactly as `tests/test_gates_build.py::_rendered_typescript` does, into the throwaway directory.

Then the types: the engine writes `.claude-plugin/types/` when a Claude Code session loads this plugin from its folder. If the host's `claude` is 2.1.289 or the pin is set to the host's version, `make types && make typecheck` exits 0 and `uv run rail check --ci build --json` reports PASS for `build.lint` with `types match claude`; otherwise record that the types step could not be proven on this host and why. A second check: delete `package-lock.json`, run `make sync INSTALL=ci`, expect failure (`npm ci` refuses). A long-slug render (`red-` plus 40 characters) must pass `make lint` too: Review Focus 1, run on the host. Delete the throwaway directories afterwards.

- [ ] **Step 5: Record**

Append nothing to the repository. Keep the commands' output for the pull request description: the summary lines of Step 1, the two gate lines of Step 3, and the host evidence of Step 4. Criterion 10 (the throwaway private repository running `rail-ci`, cold and warm) and criterion 11 (red-cockpit's first PR) are the operator-gated steps after the pull request: do not create a GitHub repository without the operator's go.

---

## Self-review

**Spec coverage.** Decision 1 → Tasks 1 and 3 (`NOT_AT_PROD`, validator, `COMBINATIONS`). 2 → Task 3 Step 4. 3 → Task 3 (`package.json`, `.node-version`, Makefile `sync`/`INSTALL`), Task 4 (`npm ci`), Task 2 (the gate reads the pins). 4 → Task 3 (`typecheck`, `types`, `rail.yaml` exception, `tsconfig` include), Task 2 (SKIPPED, version comparison). 5 → Task 3 (Makefile branch, `.PHONY`, `ci:` order). 6 → Task 3 (six chains). 7 → Task 4. 8 → Tasks 2 and 3 (`_typescript_tests`, tables, observation). 9 → Task 2. 10 → Task 5. 11 → untouched by design. 12 → delivery (the pull request carries these tasks; Task 6 records the evidence). Criteria 1–8 are Tasks 3–6; 9 is Task 6 Step 4; 10 and 11 are operator-gated after the pull request.

**Placeholders.** None: every step names its file, its code and its expected output. The three pins are values read on 2026-10-04 and the plan says how to re-read them.

**Type consistency.** `NOT_AT_PROD`, `_refusal_at_prod`, `TYPESCRIPT_PROD_REFUSAL`, `_typescript_tests`, `_typescript_test_profile`, `_typescript_profile`, `TYPES_FILE`, `TYPESCRIPT_PIN`, `write_typescript_files`, `TYPESCRIPT_MAKEFILE` keep one spelling across tasks. The message strings in Task 3's tests equal the constants and the copier validator's text.

**Known soft spots, said rather than hidden.** Task 3's `rail.yaml.jinja` whitespace and the `.claude-plugin` conditional directory name are exercised by Task 3's render tests and by Task 5's real-copier test: those are the first places a Jinja detail would show. The `make types` copy is proven with a synthetic file, not the engine's real output, because the engine writes it only from a live session: Task 6 Step 4 says what is proven and what is not.
