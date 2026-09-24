# The rust stack, and `rail upgrade --stack`

Status: proposed — 2026-09-24

Ticket: 293524e3 (red → red-rail): a `rust` stack in `copier.yml` and `template/project/`, for
red-life (the first Rust project of ReD and the pilot of the `rail new` procedure), and later
red-arena's engine. The rail-5 thread adds `rail upgrade --stack`. This is spec B. It builds on
spec A (`docs/specs/2026-09-24-template-alignment.md`: decisions 11 and 12, § "What spec B adds")
and does not redo it.

## Problem

- **No `rust` stack exists.** `Stack` (`model.py`) and `copier.yml` `stack.choices` offer
  `python`, `go` and `docs`. red-life must start `--stack docs` and cannot write its first line
  of Rust on the rail.
- **The build gate judges every non-Python stack as Go.** In `build.py`, `has_tests` ends with a
  bare `else` that searches for `*_test.go`, and `lint` ends with `return _go_profile(repo)`. A
  Rust repository would fail on "go.mod is missing", and nothing would say why.
- **Nothing can change a stack after the scaffold.** `upgrade()` passes only `rail_ref`, with
  `skip_answered=True` (`scaffold.py:344-352`). Today one runs `copier update --data stack=rust`
  by hand and edits `rail.yaml`, and no check guards the transition.
- **CI can only install Go.** `rail-ci.yml` sets up Go when `inputs.stack == 'go'`. It has no
  Rust path, and its `stack` input describes `python | go | docs`.
- **Three stack chains are unguarded.** `Makefile.jinja`, `.gitignore.jinja` and
  `.claude/settings.json.jinja` branch on the stack, but A's marker test does not read them. The
  Makefile's `docs` branch is a catch-all `else`.

## Decisions

Each decision says what, why, what it costs if wrong, and which files it touches.

1. **`rust` becomes a stack, refused at `tier: prod` for now, in both places the precedent uses.**
   - `Stack.RUST = "rust"`, and `rust` in `copier.yml` `stack.choices`. A's parity test forces
     both.
   - The refusal, message "rust at tier prod is not templated yet (no image, no service):
     scaffold at dev and promote when the rust prod template lands", lives where the
     private-systemd precedent puts its own:
     - in `NewProject.answers` (`scaffold.py`), raising `ScaffoldError` before copier, so
       `rail new` prints it bare rather than wrapped in "copier could not render …";
     - in a `copier.yml` validator on `stack` (`tier` is asked first), for a direct copier run.
   - A's matrix tests render "every combination" (A decisions 10, 14, 15). They move onto one
     shared `COMBINATIONS` (`tests/combinations.py`): stack × tier × ledger plus both target
     families at `prod`, minus exactly `(rust, prod)`. A test pins that pair as the only
     exclusion.
   - `RailConfig` is not tightened: a hand-promoted manifest stays valid, and the release and
     deploy gates judge it. Promotion edits `rail.yaml` only; the answers file keeps the tier it
     was rendered with (A decision 10: nothing re-answers `tier`). Decision 13 refuses an answers
     file that holds both `rust` and `prod`.
   - Why: the multi-stage musl image is new design with no reference in the repository. red-life
     starts at `docs`, then `bootstrap`/`dev`.
   - Cost if wrong: red-arena's engine waits for a later spec.
   - Files: `src/rail/model.py`, `src/rail/scaffold.py`, `copier.yml`, `tests/combinations.py`.

2. **The files follow the conditional-path pattern the python stack already uses.**
   - `{% if stack == 'rust' %}Cargo.toml{% endif %}.jinja`: a root package plus an empty
     `[workspace]` table, `edition = "2024"`, `publish = false`, no dependencies, and no
     `rust-version`: the toolchain file is the only version source (decision 3).
   - `{% if stack == 'rust' %}src{% endif %}/main.rs.jinja` prints `{{ project }}`.
   - `{% if stack == 'rust' %}tests{% endif %}/smoke.rs.jinja` runs the binary through
     `env!("CARGO_BIN_EXE_{{ project }}")` and asserts it exits 0 and prints the project name.
     It is an integration test, as the ticket asks; a binary crate has no library to import.
     The directory sits beside python's `{% if stack == 'python' %}tests{% endif %}`; only one
     renders.
   - `rust-toolchain.toml` and `deny.toml`, same pattern (decisions 3 and 5).
   - Why a root package with a `[workspace]` table: "workspace-ready" with a flat layout, and a
     member is one line away. A virtual workspace would move the one crate under `crates/` on
     day 0 for a member that does not exist yet.
   - Cost if wrong: red-life moves to a virtual workspace by hand, which is one PR.

3. **One toolchain pin, an exact stable version, in `rust-toolchain.toml`.**
   - `[toolchain] channel = "X.Y.Z"`, `components = ["rustfmt", "clippy"]`, `profile = "minimal"`.
   - X.Y.Z is the latest stable on the day of implementation, read from
     `static.rust-lang.org/dist/channel-rust-stable.toml`; the plan records the value read.
   - Nothing else names the version: rustup reads the file locally and in CI (decision 8), and
     `Cargo.toml` carries no `rust-version`.
   - Why exact: the Go step pins `1.26.8` for reproducibility (`rail-ci.yml:66-74`). A floating
     `stable` would change clippy's lints under a green project.
   - Cost: a version bump is a template edit plus `rail upgrade` per repository, as Go's is.

4. **`Cargo.lock` is written by `make sync`, committed, and never regenerated in CI.**
   - `sync` runs `$(CARGO) fetch $(LOCKED)`. Locally `LOCKED` is empty: `cargo fetch` creates
     the lock or updates a stale one. CI runs `make sync LOCKED=--locked`, so a missing
     (uncommitted) or stale lock fails the job instead of being generated there.
   - `ci` runs `cargo test --locked`. `.gitignore` gets `target/`, never `Cargo.lock`.
   - Rejected: a lock rendered by Jinja (its format version depends on the cargo reading it);
     `cargo generate-lockfile` in `sync` (re-resolves everything on each run);
     `[ -f Cargo.lock ] || generate` (in CI it creates the lock `--locked` is there to demand).
   - This mirrors `go.sum` ("the template ships no go.sum, `make sync` is what writes one",
     `rail-ci.yml:72-73`).
   - Cost if wrong: a project that forgot to commit its lock fails CI with cargo's own message.
     That is the intent. Files: `Makefile.jinja`, `.gitignore.jinja`.

5. **cargo-deny is pinned in `make sync`, per project, and `deny.toml` is strict by default.**
   - `sync` runs `$(CARGO) install cargo-deny --locked --version A.B.C --root .cargo-tools`. The
     version is literal, as Go's `@v0.8.1` is: the latest release on the day of implementation.
     The same target runs on the workstation and in CI (ruling). Reinstalling the installed
     version exits 0 without rebuilding.
   - `--root .cargo-tools` (gitignored), with `export PATH := $(CURDIR)/.cargo-tools/bin:$(PATH)`
     in the rust branch of the Makefile. Why: `cargo install` into the global `$CARGO_HOME/bin`
     makes two projects with different pins (red-life, later red-arena) replace each other's
     binary on every `make sync`, and `cargo deny` would run whichever was installed last.
   - `deny.toml` is written in the schema of the pinned version:
     - `[licenses]`: `allow` = MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, Unicode-3.0;
       `private.ignore = true` (the project's own `publish = false` crates carry no licence); an
       unused allowed licence is not an error.
     - `[sources]`: `unknown-registry = "deny"`, `unknown-git = "deny"`, crates.io only.
     - `[advisories]`: every vulnerability and every yanked crate fails. Unmaintained-crate
       advisories fail for direct dependencies only: graphics trees (wgpu) carry unmaintained
       transitive crates the project cannot act on.
     - `[bans]`: `wildcards = "deny"` with `allow-wildcard-paths = true` (members are path
       dependencies), `multiple-versions = "warn"`.
   - Why: licences, advisories, bans and sources are the ticket's four checks. Every widening,
     such as red-life adding Zlib for wgpu, is then a reviewed line in its own `deny.toml`.
   - Cost: one source compile of cargo-deny per fresh checkout; CI caches it (decision 8).
   - Files: `Makefile.jinja`, `.gitignore.jinja`, the template's `deny.toml`.

6. **The rust Makefile branch lines up with the other stacks.**
   - `CARGO ?= cargo` is the one way to reach the toolchain, as `GO ?= go` is, so
     `make CARGO=<wrapper>` runs every target through a container (criterion 10).
   - `lint`: `$(CARGO) fmt --all --check`, then
     `$(CARGO) clippy --workspace --all-targets --all-features -- -D warnings`.
     `test`: `$(CARGO) test --workspace --locked`. `deny`: `$(CARGO) deny check` (Go's `vuln`).
     `ci: lint test deny check`, so `rail check` runs last. `.PHONY` gains `deny` for rust.
   - `--all`/`--workspace` go beyond the ticket's literal commands: at the root of a package that
     is also a workspace, cargo acts on the root package alone. The flags make "workspace-ready"
     true: a member added later is linted and tested without a Makefile edit.

7. **Every stack chain is marked, names every stack, and gets its rust branch.**
   - `Makefile.jinja`, `.gitignore.jinja` and `.claude/settings.json.jinja` get A's
     `{# stack-chain: <name> #}` markers. The Makefile's catch-all `else` becomes
     `elif stack == 'docs'`, so an unknown stack renders no `lint` and make fails loudly;
     `.gitignore` and `settings.json` get an explicit, empty `docs` branch. A's marker test reads
     five templates instead of two.
   - The rust branches:
     - `.gitignore`: `target/`, `.cargo-tools/`. `settings.json`: `Bash(cargo:*)`.
     - `CLAUDE.md` § Stack: Rust edition 2024, toolchain pinned by `rust-toolchain.toml`,
       `cargo fmt`, clippy with `-D warnings`, `cargo test --locked`, `cargo deny check`.
     - `CLAUDE.md` § Structure: `Cargo.toml`, `Cargo.lock` ("written by `make sync`,
       committed"), `rust-toolchain.toml`, `deny.toml`, `src/main.rs`, `tests/`.
     - `AGENTS.md` § Gates: `make sync` first (lock and cargo-deny), then `make ci`. A new
       licence or source is a reviewed `deny.toml` line.
   - A tool is cited by its command (`cargo deny check`) or in plain text, never as a bare
     backticked kebab token: A's bare-skill regex (A decision 14) would read `cargo-deny` as a
     skill, and `CITABLE` stays empty. A's criterion 8 runs over the rust renders.
   - The single-stack extras (`.PHONY`, the `ci:` line) stay unmarked; criterion 1 pins them.
   - Why: after this spec no stack chain is left unguarded, which completes A's hook.

8. **CI installs rustup by checksum, takes the toolchain from the project's file, and caches.**
   - `rail-ci.yml` gets three steps under `if: inputs.stack == 'rust'`, placed where Go's is.
   - Step 1, "Set up Rust (rustup pinned, checksum verified)", `env: RUSTUP_VERSION`,
     `RUSTUP_INIT_SHA256`:
     - downloads `static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init`
       and runs `sha256sum -c`;
     - `rustup-init -y --no-modify-path --profile minimal --default-toolchain none`;
     - then, by absolute path (a `$GITHUB_PATH` write applies to later steps only, and the
       red-ci guest has no Rust, `red-runners/provision/bootstrap.sh:15`, as the gitleaks step
       already does): `"$HOME/.cargo/bin/rustup" toolchain install` with no argument (installs
       the file's toolchain), then `"$HOME/.cargo/bin/rustup" component add rustfmt clippy`
       (acts on that active toolchain: the no-argument install does not reliably add the file's
       components, rustup #4216), then `cargo fmt --version` and `cargo clippy --version`, so a
       missing component fails setup, not lint;
     - finally `$HOME/.cargo/bin` onto `$GITHUB_PATH`.
   - Step 2, `actions/cache` pinned by 40-hex SHA: paths `~/.cargo/registry/index`,
     `~/.cargo/registry/cache`, `~/.cargo/git/db` and `.cargo-tools`; key
     `rust-${{ runner.os }}-${{ hashFiles('rust-toolchain.toml', 'Cargo.lock', 'Makefile') }}`
     (the Makefile holds the cargo-deny pin), restore-keys `rust-${{ runner.os }}-`. `target/`
     is not cached. A prefix restore is safe: `cargo install --version` replaces an older
     cargo-deny, and `--locked` still judges the lock.
   - Step 3: `make sync LOCKED=--locked`.
   - The `stack` input description becomes `python | go | rust | docs`.
   - Why: the gitleaks precedent (pinned version and sha256, no new trust); it reads the one pin
     (decision 3); it works on a runner with nothing preinstalled. The cache is the ticket's
     ask, and unlike Go (`cache: false`, no `go.sum`), a committed lock gives it a key.
   - Rejected: `dtolnay/rust-toolchain` takes its version from its ref or input, a second pin;
     `actions-rust-lang/setup-rust-toolchain` reads the toml and its cache can be turned off,
     but it is one more third-party action for what rustup already does.
   - Cost: a rustup bump is two lines here; a cache action and its key are maintained in the
     shared workflow. File: `.github/workflows/rail-ci.yml`.

9. **Runners: GitHub-hosted by default, and red-rail needs no runner.**
   - `rail-ci.yml` runs on `fromJSON(inputs.runs-on)`, default `"ubuntu-latest"`. The template's
     `continuous-integration.yml` passes no `runs-on`, so red-life's CI runs GitHub-hosted with
     no provisioning. Rust links through `cc`, which ubuntu-latest ships; `build-essential` is
     also in the red-ci guest, where step 1 installs rustup into the instance's persistent HOME.
   - A move of red-life to `red-ci` is red-life's choice, through a ticket red → red-runners for
     a `github-runner@red-life` service. It blocks neither B nor red-rail's CI.
   - red-rail's own tests stay static: they render and read, never run cargo. The container its
     CI uses (`python:3.12-slim`) gets no Rust.

10. **The build gate routes by an explicit table, and a stack without a profile FAILs.**
    - `build.py` gains `TEST_PROFILES` and `LINT_PROFILES`, `dict[Stack, Callable[[Path], GateResult]]`
      with entries for `PYTHON`, `GO`, `RUST` and `DOCS`. The Python and Go bodies move in
      unchanged.
    - A lookup miss returns FAIL "stack `<s>` has no build profile in this rail version". The
      gate never raises and never falls through to Go.
    - The `stack is None` observation keeps its current text and appends the rust count:
      "no test file found (tests/test_*.py, *_test.go), none in tests/*.rs" when empty,
      "…, {go} (*_test.go), {rust} (tests/*.rs)" otherwise. The substring asserted at
      `tests/test_gates_build.py:43` is unchanged.
    - Why a table: completeness is one assertion (`set(LINT_PROFILES) == set(Stack)`), and the
      miss path is tested by removing an entry.
    - Cost if wrong: none for Go and Python; their tests stay green unchanged, which protects
      red-alerts and red-monitor. Files: `src/rail/gates/build.py`, `tests/test_gates_build.py`.

11. **Rust tests are the targets Cargo discovers as integration tests.**
    - `_rust_tests(repo)` counts `tests/*.rs` and `tests/*/main.rs` in every directory holding a
      `Cargo.toml` (root package and members), skipping `target/`. A helper such as
      `tests/common/mod.rs` is not a test target and does not count.
    - Unit tests inside `src/` do not count: the ticket requires an integration test, and a
      `#[cfg(test)]` search would be text matching. The failure message names `tests/*.rs`.
    - Cost if wrong: a crate tested only inline FAILs `build.tests` and declares an override.

12. **The rust lint profile is a pure read, on the model of `_go_profile`.**
    - `_rust_profile` PASSes when all hold:
      - `rust-toolchain.toml` parses with `tomllib`, `channel` matches `^\d+\.\d+\.\d+$`, and
        `components` include `rustfmt` and `clippy`;
      - `Cargo.toml` and `Cargo.lock` are present, and `deny.toml` parses;
      - the Makefile's live recipe lines (`_recipe_lines`) call `fmt … --check`,
        `clippy … -D warnings`, `deny check`, and `install cargo-deny … --locked … --version \d+\.\d+\.\d+`.
    - The regexes accept `$(CARGO)`, as `_INVOKES` accepts `$(GO)`. Each FAIL names the first
      missing piece and its remedy.
    - About the lock: the gate checks presence only. A fresh `rail new --stack rust --tier dev`
      FAILs `build.lint` until `make sync` writes the lock; that the lock is committed is
      enforced by CI's `--locked` (decision 4). That is Go's behaviour today, and `rail new`
      verifies only the bootstrap floor (`scaffold.py:272`), so the scaffold stays green.
    - Why: the same contract as Go's. The gate never runs cargo.
    - Cost if wrong: a Makefile that spells a call some unforeseen way declares an override.

13. **`rail upgrade --stack <s>`: the only allowed transition is out of `docs`, and the refusal
    comes before copier.**
    - `upgrade(repo, *, stack: Stack | None = None, …)`. With a stack, `data` becomes
      `{"rail_ref": …, "stack": s}`; copier gives `data` priority over a stored answer under
      `skip_answered=True`.
    - It refuses, exit 1, tree untouched, `update` never called:
      - with or without `--stack`, when `.copier-answers.yml` holds `stack: rust` and
        `tier: prod`: copier would drop that answer as invalid and, under `defaults=True`,
        re-render the project as `python`;
      - the stacks in `.copier-answers.yml` and `rail.yaml` disagree;
      - the current stack is not `docs`, or the target is `docs`;
      - the target is `rust` and `rail.yaml` says `tier: prod` (decision 1).
    - After copier it re-reads both files. If `rail.yaml` does not parse (conflict markers) or
      its `stack:` is not the target, it raises "copier did not carry `stack` into rail.yaml:
      resolve it to `stack: <s>`". It never edits `rail.yaml`: copier's three-way merge is the
      one writer, and on an unedited `stack:` line it carries the change.
    - Why only from `docs`: python→go or go→rust leaves orphan build files that only diff review
      would catch (`upgrade.py:22`). `docs` has no stack files.
    - The switch also moves the repository to the template's latest tag and re-pins `rail_ref`,
      which is necessary: `rust` exists only from B's tag on.
    - Cost if wrong: a legitimate python→rust rewrite is done by hand, as today.
    - Files: `src/rail/scaffold.py`, `src/rail/commands/upgrade.py`, `tests/test_scaffold.py`.

14. **The design gate and this file's name.**
    - The name is ruled: `docs/specs/2026-09-24-rust-stack.md`; the plan is
      `docs/plans/2026-09-24-rust-stack.md`. `latest_doc` sorts by name (`markdown.py:83-88`),
      and `rust-stack` sorts before `template-alignment` whatever day B is cut or merged. So
      red-rail's `design.spec` and `plan.plan` keep reading A's files until a later-dated spec
      and plan land: they stay green and prove nothing about B.
    - Decision: the proof that B's spec and plan pass is criterion 8, mandatory, not a fallback:
      both gates run on B's files explicitly. Branch base and merge order (decision 15) do not
      change this.
    - Cost: none on code. The residue is an operator question below.

15. **Delivery: one pull request, no `rail bind`, then tag `v0.6.0`.**
    - The branch starts from A's head, or from `main` once A has merged; it rebases onto main
      when A merges, and onto private-systemd if that merges first (`copier.yml`, `model.py`,
      `scaffold.py`).
    - No `rail bind` (engagement d096f911).
    - Before merge, the rust CI path runs for real on the PR's head (criterion 11). After the
      merge, an annotated `v0.6.0` is cut on `origin`. `rail upgrade` renders the latest tag, so
      red-life's switch waits on the tag.
    - Then, in red-life's own PR: `rail upgrade --stack rust`, `make sync`, commit `Cargo.lock`.

## Order of work inside the pull request

Each step is test-first, and each one is a commit.

1. Decision 10: the table tests (completeness, miss is FAIL), then Python and Go move into the
   tables; the existing build tests stay green.
2. Decision 1: `COMBINATIONS` and A's matrix tests moved onto it; then `Stack.RUST`, the copier
   choice and both refusals. A's parity test and marker test turn red.
3. Decisions 2-7: the render tests, then the rust files, the Makefile branch and the marked
   chains. A's marker test turns green.
4. Decisions 11-12: the rust gate tests on a rendered tree plus mutations, then `_rust_tests`
   and `_rust_profile`.
5. Decision 8: the `rail-ci.yml` test, then the three steps.
6. Decision 13: the refusal and postcondition tests, then `upgrade()` and the CLI option.
7. `make ci` and `rail check` from the worktree, criterion 8, then the host verification and
   the pre-merge CI run (criteria 10 and 11).

## Non-goals

- **Rust at tier prod**: the multi-stage static-musl image (distroless or scratch by digest,
  non-root, `/healthz` `/version` `/metrics`), `rail release` and `rail deploy` for rust. Beyond
  B; decision 1 refuses the combination meanwhile.
- **Project-specific toolchain needs**: the `wasm32-unknown-unknown` target, WebGPU crates,
  licence widenings. They are red-life's, in its own `rust-toolchain.toml` and `deny.toml`.
- **Already-generated repositories**: no upgrade or port here.
- **Other transitions**: python↔go, anything to `docs`, and promoting a tier.
- **A prebuilt cargo-deny binary**, and caching `target/`.
- **Changing the Go or Python CI paths**, and adding `make sync` to their CI.
- **The rest of A**: markers, parity test, guidance files and roster gate are used as they are.
- **The workstation gates in the throwaway proofs** (criterion 10).
- **Installing Rust on the server host**: an operator question (criterion 10 uses a container).

## Success criteria

Tests in red-rail. All are static; none needs cargo.

1. `test_render_rust_bootstrap` renders `rust`/`bootstrap`/`file` and asserts:
   - `Cargo.toml` parses with `tomllib`: `package.name == project`, `edition == "2024"`,
     `publish is False`, a `workspace` table, no `rust-version`;
   - the toolchain `channel` is an exact version and `components` ⊇ {rustfmt, clippy};
   - `deny.toml`: `licenses.allow` is exactly the six licences, `licenses.private.ignore` is
     true, both `sources.unknown-*` are `deny`;
   - `tests/smoke.rs` names `CARGO_BIN_EXE_<project>`, and `src/main.rs` exists;
   - `.gitignore` has `target/` and `.cargo-tools/`, and no `Cargo.lock`;
   - `.claude/settings.json` parses as JSON and allows `Bash(cargo:*)` (absent from the python,
     go and docs renders);
   - `CLAUDE.md` § Stack names edition 2024, `rust-toolchain.toml`, `-D warnings`,
     `cargo test --locked` and `cargo deny check`; § Structure lists `Cargo.toml`, `Cargo.lock`,
     `rust-toolchain.toml`, `deny.toml`, `src/main.rs` and `tests/`; `AGENTS.md` § Gates puts
     `make sync` before `make ci`;
   - no `pyproject.toml`, `go.mod` or `src/<pkg>/__init__.py`;
   - `make -n ci RAIL_FLAGS=--ci` prints, in order: fmt `--check`, clippy `-D warnings`, test
     `--locked`, `deny check`, `rail check --ci`. `make -n sync` prints `fetch` and
     `install cargo-deny --locked --version … --root .cargo-tools`.
2. `test_rust_at_prod_is_refused`: `NewProject(...).answers` raises `ScaffoldError` with the
   message (as the private-systemd test does), and a direct `render` is refused by the copier
   validator. `test_rust_at_prod_is_the_only_excluded_combination` pins `COMBINATIONS`. A's
   tests stay green over `COMBINATIONS`, rust included: parity, the marker test over five
   templates, `test_rendered_guidance_points_at_the_root`, the `AGENTS.md` invariance,
   `test_rendered_guidance_cites_only_allowed_skills` and the address test.
3. In `tests/test_gates_build.py`:
   - `test_every_stack_has_a_build_profile`;
   - `test_a_stack_without_a_profile_fails_and_does_not_raise` (an entry removed with
     `monkeypatch`);
   - `test_rust_tests_are_found_under_tests_dirs` (root, a member, `tests/<dir>/main.rs`);
   - `test_a_rust_helper_module_alone_is_not_a_test`; `test_rust_repo_with_only_go_tests_fails`;
   - `test_undeclared_stack_reports_rust_tests` (the decision 10 text);
   - `test_rust_lint_passes_on_the_rendered_tree` (a placeholder `Cargo.lock` written);
   - `test_rust_lint_fails_…`, parametrised: no `deny.toml`, `channel = "stable"`, no `clippy`
     component, no `Cargo.lock`, the `deny check` call commented out, a cargo-deny install
     without `--version`;
   - every existing Go and Python build test unchanged and green.
4. `test_rail_ci_installs_rust_from_the_project_pin` reads the parsed `rail-ci.yml`:
   - the rust steps are conditioned on `inputs.stack == 'rust'`;
   - the rustup step verifies a sha256, names no Rust toolchain version, calls
     `"$HOME/.cargo/bin/rustup"` by absolute path, runs `component add rustfmt clippy`, and
     ends with `cargo fmt --version` and `cargo clippy --version`;
   - the cache step's key hashes `rust-toolchain.toml`, `Cargo.lock` and `Makefile`, and its
     paths include `.cargo-tools` and not `target`;
   - `make sync LOCKED=--locked` follows them; every `uses:` is 40-hex pinned; the input
     description lists `rust`.
5. The upgrade tests:
   - `test_upgrade_switches_docs_to_rust`, real copier on a tagged throwaway template: the
     answers, `rail.yaml` and the CI file say `rust`, and `Cargo.toml` exists;
   - `test_upgrade_refuses_a_transition_not_from_docs`, parametrised over python→go, go→rust,
     rust→docs, docs→docs and docs→rust at prod: exit 1, `update` not called, `git status` clean;
   - `test_upgrade_refuses_answers_holding_rust_at_prod`, without `--stack`;
   - `test_upgrade_refuses_when_answers_and_manifest_disagree`;
   - `test_upgrade_fails_when_rail_yaml_does_not_follow` (a fake update leaving `stack: docs`);
   - the existing `seen["data"] == {"rail_ref": new}` test holds without `--stack`.
6. `make ci` is green with its summary line read, and `uv run rail check` passes from the
   worktree.
7. `rail audit`'s golden `tests/golden/audit-matrix.json` is unchanged: no audited repository is
   rust.
8. **B's spec and plan pass their gates, explicitly** (decision 14): `design.spec` and
   `plan.plan` run on a copy of the PR tree whose `docs/specs` and `docs/plans` hold only B's
   two files; both PASS, output recorded in the PR. Mandatory, since `rail check` reads A's.

On the host, before merge:

9. **Measured, not assumed.** Criterion 11 records the cold and the warm run durations, and
   the duration of `make sync` in each, against `timeout-minutes: 20`. red-life's first rust
   run is recorded again in its PR.
10. **Host verification** by the session on the committed branch, recorded in the PR. rustup is
    absent from the server host, so cargo runs in a container.
    - Render: `uv run rail new red-throwaway --description "throwaway" --no-remotes
      --template . --template-ref HEAD --dest <tmp> --stack rust`, at `bootstrap` and at `dev`.
      That `rail new` succeeds proves the bootstrap gates in `--ci` scope. The workstation
      gates are out: `hygiene.remotes` needs a GitHub remote and `hygiene.roster_entry` a root
      roster row, neither depends on the stack, and B changes neither.
    - The wrapper: the official `rust:<pin>-slim` image by digest, `--user "$(id -u):$(id -g)"`,
      `HOME=/tmp`, `CARGO_HOME` a directory under the throwaway root, the tree mounted at its
      own path with `<tree>/.cargo-tools/bin` prepended to the container's `PATH`. Its setup
      runs the same `rustup component add rustfmt clippy` as CI; the evidence records
      `cargo fmt --version` and `cargo clippy --version`.
    - Bootstrap tree: `make CARGO=<wrapper> RAIL_FLAGS=--ci sync ci` exits 0, which includes
      `cargo deny check` accepting the rendered `deny.toml` (decision 5's schema proven here).
    - Dev tree: `make CARGO=<wrapper> sync lint test deny` exits 0, and
      `rail check --ci build --json` reports PASS for `build.tests` and `build.lint`. Not
      `make ci`: at `dev`, `plan.plan`, `review.verdict` and `integrate.receipt` FAIL on any
      fresh render with the file ledger (`policy.py:20-28`, `gates/__init__.py:116`).
    - A second `make sync LOCKED=--locked` after deleting `Cargo.lock` fails (decision 4).
    - `rail upgrade --stack rust` on a committed throwaway `docs` render leaves both files
      saying `rust`.
    - The trees and the `CARGO_HOME` directory are deleted afterwards.
11. **The ticket's "in CI", before the tag.** The bootstrap tree, rendered with
    `--template-ref <B head SHA>` (so its CI calls `rail-ci.yml@<SHA>` with `rail-ref: <SHA>`)
    and its `Cargo.lock` committed, is pushed to a private throwaway repository. Its
    `rail-ci` run is green twice (cold, then warm on the cache), summary lines read, run URLs
    in the PR. A wrong rustup sha256 or a missing component surfaces here, not in `v0.6.x`. The
    repository is deleted afterwards.
12. **The ticket's end-to-end test.** After `v0.6.0`, red-life switches with
    `rail upgrade --stack rust`. Its first rust PR runs `make ci` green in GitHub CI, summary
    line read, and `rail check` is green at its tier. 293524e3 is accepted on criteria 11
    and 12.

## For the operator

- **The throwaway repository of criterion 11**: creating a private GitHub repository and
  deleting it afterwards (deletion needs the `delete_repo` scope, possibly an operator gesture).
  Recommendation: allow it; it is the only way to run the rust CI path before a tag.
- **The ruled file name** (decision 14) leaves red-rail's `design.spec` and `plan.plan` on A's
  files until a later-dated spec lands. Recommendation: keep the ruled name, with criterion 8
  as the proof; a later-dated name would reopen the ruling for a gate-reading convenience.
- **Cold CI cost**: the first run on a cache miss still compiles cargo-deny. If criterion 11's
  cold run exceeds about 10 minutes, the lever is moving red-life to `red-ci` (persistent HOME,
  a red-runners ticket). Recommendation: stay GitHub-hosted with the cache.
- **Rust on the server host.** red-life "starts on the server host", and its local `make ci`
  needs rustup. Recommendation: a per-user rustup install, as the operator's gesture, before
  red-life's first rust PR. The container wrapper covers verification until then.
- **Unmaintained advisories scoped to direct dependencies** (decision 5). Recommendation: keep
  it; tighten per project once the dependency tree is known.
