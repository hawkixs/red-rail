# The typescript stack: Claude Code plugin repositories on the rail

Status: proposed — 2026-10-04

Motivation: unblocks the first product delivery that cannot start on the rail today, red-cockpit
(ticket 47577a6a, red → red-rail). It lowers no operator load by itself; without it red-cockpit is
born with `--stack docs` and ships its first TypeScript with no build gate, no CI and no lock.

Operator decisions behind this spec, taken on 2026-10-03 with the operator driving the session
directly: the `spec` method; the stack takes priority over the autonomy batch; `claude` is an
exact devDependency run in CI; the tier is capped at `dev` (`bootstrap` and `dev` only); and the
design below (A–D: model and template, build gate, CI, ledger and upgrade), approved on
2026-10-04, including that the engine's types are not shipped in the public template. Merge rule
`39f7ea9f` does not change.

It builds on the rust stack (`docs/specs/2026-09-24-rust-stack.md`) and reuses its mechanisms:
the `Stack` enum and copier choice, the refusal at `tier: prod`, the `TEST_PROFILES` and
`LINT_PROFILES` tables, the conditional-path template files, the marked stack chains, the
`rail-ci.yml` stack branch, the `COMBINATIONS` matrix, and `rail upgrade --stack`.

## Problem

- **No `typescript` stack exists.** `Stack` and `copier.yml` `stack.choices` offer `python`, `go`,
  `rust` and `docs`. red-cockpit, a Claude Code plugin written in TypeScript, cannot be scaffolded
  on the rail.
- **What a Claude Code plugin needs is unlike the other stacks.** Measured on Claude Code 2.1.288
  (2026-10-03 and 2026-10-04):
  - A plugin is `.claude-plugin/plugin.json`, `hooks/hooks.json` (`{"modules":["./register.ts"]}`)
    and modules exporting `register(on, options)`. A module imports only its own relative files
    (with the explicit `.ts` suffix) and `claude-code`, which is types only.
  - Tests import `claude-code/testing`: no other runner can execute them, only
    `claude plugin test`. `claude plugin validate [--strict] <dir>` checks the manifest and hooks.
    Both passed with an empty `HOME` and no network.
  - The engine's types are not on npm. The engine writes them into `.claude-plugin/types/` (the
    core in `claude-code/index.d.ts`, about 15,000 lines, line 1 `// Written by Claude Code
    <version>.`; beside it `claude-code-tools/`, `claude-code-mcp/` and a `tsconfig.json`) each time
    a session loads the plugin from its folder, and `/plugin-types` forces it; it also puts a
    `.gitignore` containing `*` there. Neither the CLI, `validate` nor `test` writes them, so no
    CI job can produce them. The package `@anthropic-ai/claude-code` carries the binary, not these
    types (measured on 2.1.289 and 2.1.288).
- **The build gate would judge it as nothing.** `TEST_PROFILES` and `LINT_PROFILES` have no entry
  for a fifth stack: a lookup miss FAILs "no build profile", and `build.tests` cannot count
  `*.test.ts`.
- **CI can install Go and Rust only.** `rail-ci.yml` has no Node path and its `stack` input says
  `python | go | rust | docs`.
- **A reviewer would be handed 21,000 generated lines** the first time a project vendors the
  types, unless the project declares them.

## Decisions

Each decision says what, why, what it costs if wrong, and which files it touches.

1. **`typescript` becomes a stack, scoped to a Claude Code plugin, refused at `tier: prod`, in
   both places the rust precedent uses.**
   - `Stack.TYPESCRIPT = "typescript"`, and `typescript` in `copier.yml` `stack.choices`. The
     parity test forces both.
   - The refusal, message "typescript at tier prod is not templated yet (a plugin has no image and
     no service): scaffold at dev", lives where rust's does: in `NewProject.answers`
     (`ScaffoldError` before copier, printed bare) and in the `copier.yml` validator on `stack`.
   - The two stack-specific refusals of `scaffold.py` (`RUST_PROD_REFUSAL`, the answers-file check
     and the `_switchable` check) become one table, `NOT_AT_PROD: dict[Stack, str]`, with an entry
     for rust and one for typescript. The rust messages do not change.
   - `COMBINATIONS` (`tests/combinations.py`) excludes `(typescript, prod)` as well: `EXCLUDED`
     holds exactly the two pairs, and a test pins that.
   - `RailConfig` is not tightened: a hand-promoted manifest stays valid and the release and
     deploy gates judge it.
   - Why a plugin and not "TypeScript in general": every file the template writes is a plugin file
     (decision 2), and the build gate reads them. A web or Node service in TypeScript is a different
     template; if one comes, it is a new answer, not a rename.
   - Why capped at `dev`: a plugin is distributed through a marketplace, not released as an image
     and deployed by ssh; stages 7–9 have no meaning for it yet.
   - Cost if wrong: a plugin that must reach `prod` waits for a later spec.
   - Files: `src/rail/model.py`, `src/rail/scaffold.py`, `copier.yml`, `tests/combinations.py`.

2. **The files follow the conditional-path pattern, derived from the reference plugin red-cockpit
   built.** Under `template/project/`, each file named `{% if stack == 'typescript' %}…{% endif %}`:
   - `.claude-plugin/plugin.json`: `name` = the project, `version` `0.1.0`, `description`, and an
     `author` (`{"name": "hawkixs"}`), because `claude plugin validate` warns without one and the
     template validates with `--strict`.
   - `hooks/hooks.json`: `{"modules":["./register.ts"]}`; `hooks/register.ts` registers one
     command, `<project>-hello`, importing `greeting` from `../src/core.ts` and the `Register` type
     from `claude-code`.
   - `src/core.ts`: pure logic, no engine import. `src/core.test.ts`: imports `test` and `expect`
     from `claude-code/testing`.
   - `package.json`: `private: true`, `type: module`, no runtime dependency, three devDependencies
     at exact versions (decision 3). `tsconfig.json`, `biome.json`, `.node-version`.
   - No `package-lock.json` (decision 3) and no `vendor/` (decision 4).
   - Why: the fixture is the one plugin measured to pass `validate` and `plugin test`. The template
     starts from what is known to work.
   - Cost if wrong: red-cockpit rewrites a file the template got wrong, in its own PR. The plan
     runs `validate --strict` and `plugin test` on a rendered tree before any commit.

3. **Three exact pins, one source each; the lock is written by `make sync` and committed.**
   - Node: `.node-version` holds one exact version (`X.Y.Z`), the only place that names it. CI reads
     it (decision 7).
   - `package.json` `devDependencies`: `typescript`, `@biomejs/biome` and `@anthropic-ai/claude-code`,
     each an exact `X.Y.Z` (no `^`, no `~`, no range). The first and the second are the latest
     stable on the day of implementation; the third is the Claude Code the plugin targets. The plan
     records the values read.
   - `claude` is a devDependency because only the engine can run `claude plugin test` and `validate`,
     and a CI job has no other way to get it. The lock gives its integrity.
   - `package-lock.json` is written by `make sync` and committed. Locally `make sync` runs
     `npm install`, which writes or refreshes it. CI runs `make sync INSTALL=ci`: `npm ci` fails on
     a missing or stale lock instead of writing one there. This mirrors Rust's `LOCKED=--locked`.
   - Install scripts are off by default: `sync` passes `--ignore-scripts`, then runs
     `npm rebuild @anthropic-ai/claude-code`. Measured on 2.1.289: after an install with scripts off,
     `claude` answers "native binary not installed" (its `postinstall` copies the platform binary);
     after that one rebuild, `--version`, `validate --strict` and `plugin test` run. The
     platform binary itself arrives as an optional dependency, which the lock records.
   - Every tool is called by its path, `$(BIN)/<tool>` with `BIN ?= node_modules/.bin`, never through
     `npx`: measured on npm 10.9 and 11.19, `npx --no-install` still fetches an absent tool from the
     registry, while a missing file in `node_modules/.bin` is an error. Nothing is fetched when a
     target runs.
   - Why exact: a floating Biome or TypeScript changes the lints and the type errors under a green
     project; a floating `claude` changes what `validate --strict` accepts.
   - Cost: a version bump is a template edit plus `rail upgrade` per repository, as Go's is.
   - Files: `template/project/` (the five files above), `Makefile.jinja`, `.gitignore.jinja`.

4. **The engine's types are not shipped in the template: each project vendors its own, and until it
   does, `tsc` is a named SKIP.**
   - Why not ship them: the types are Anthropic's; whether a public Apache-2.0 template may
     redistribute them is unverified, and the engine itself marks them non-committable.
   - The project's own copy: a session in the plugin folder writes `.claude-plugin/types/` (or runs
     `/plugin-types`); `make types` then copies `.claude-plugin/types/claude-code/index.d.ts` to
     `vendor/claude-code/index.d.ts` (committed, a path the engine does not ignore) and fails with
     that instruction when the source file is absent. Only the core file is copied:
     `claude-code-mcp/` lists the MCP tools connected on one machine and stays out of the repository.
   - `tsconfig.json` is the one the engine documents for a hooks module, with `include` of
     `vendor/claude-code`, `hooks` and `src`: the file declares the modules `claude-code` and
     `claude-code/testing` ambiently, so no `paths` mapping is needed (measured with `tsc` 7.0.2).
   - `make typecheck` runs `tsc --noEmit` when `vendor/claude-code/index.d.ts` exists. When it does
     not, it prints one line, `typecheck: SKIPPED, vendor/claude-code is absent (run /plugin-types, then
     make types)`, and exits 0. The line is in the CI log and in the lint gate's observation
     (decision 9). It is a named SKIP, never a silent pass: `docs.root` is the precedent.
   - The vendored types are only ever read through `import type`: a stale or tampered file cannot
     change what runs. Decision 9 binds the copy to the pin.
   - The rendered `rail.yaml` declares `gates: review.ignored_globs: ["vendor/claude-code/**"]` with
     a reason ("generated engine types, copied by `make types`, not authored"). Without it the
     reviewer would send 21,000 lines to a judge. `package-lock.json` is already ignored by the
     reviewer's own list. Biome and the test counter skip `vendor/`.
   - Cost if wrong: red-cockpit has no type check until its first `make types`. The alternative
     (ship the types) waits on a licence answer nobody here can give.
   - Files: `Makefile.jinja`, `tsconfig.json`, `biome.json`, `.gitignore.jinja`,
     `rail.yaml.jinja`.

5. **The typescript Makefile branch lines up with the other stacks.**
   - Variables: `NPM ?= npm`, `BIN ?= node_modules/.bin`, `INSTALL ?= install`, so
     `make NPM=<wrapper> ci` runs every target through a container (as `GO` and `CARGO` do).
   - `sync`: `$(NPM) $(INSTALL) --ignore-scripts`, then `$(NPM) rebuild @anthropic-ai/claude-code`
     (decision 3). `lint`: `$(BIN)/biome ci .` (formatting and
     lints, no writes). `typecheck`: decision 4. `test`: `$(BIN)/claude plugin test .`.
     `validate`: `$(BIN)/claude plugin validate --strict .`. `types`: decision 4.
   - `ci: lint typecheck test validate check`, so `rail check` runs last. `.PHONY` gains
     `typecheck validate types` for typescript.
   - Cost if wrong: the exact `plugin test` arguments differ from what is written here; the plan
     measures them on the rendered tree.

6. **Every stack chain names typescript.** The six marked chains (`Makefile.jinja`,
   `.gitignore.jinja`, `.claude/settings.json.jinja`, the `stack` and `structure` chains of
   `CLAUDE.md.jinja` and the `gates` chain of `AGENTS.md.jinja`) get an explicit typescript branch.
   - `.gitignore`: `node_modules/`, `.claude-plugin/types/`. Never `package-lock.json`, never
     `vendor/`.
   - `settings.json`: `Bash(npm:*)`, as Go has `Bash(go:*)`; nothing calls `npx`. Not `Bash(claude:*)`:
     the engine is reached through `make`.
   - `CLAUDE.md` § Stack: a Claude Code plugin, TypeScript, Node pinned by `.node-version`, Biome,
     `tsc --noEmit` against the vendored types, `claude plugin test` and `validate --strict`.
     § Structure: `.claude-plugin/plugin.json`, `hooks/`, `src/`, `package.json`,
     `package-lock.json` ("written by `make sync`, committed"), `.node-version`, `vendor/claude-code/`
     ("copied by `make types`"). `AGENTS.md` § Gates: `make sync` first, then `make ci`; the types
     step is `/plugin-types` then `make types`.
   - A tool is cited by its command or in plain text, never as a bare backticked kebab token (the
     bare-skill regex of the template-alignment spec): `@biomejs/biome` is written as "Biome".
   - Why: after this spec every stack chain still names every stack; the marker test reads them.

7. **CI sets up Node from the project's file, installs from the lock, and caches.**
   - `rail-ci.yml` gets two steps under `if: inputs.stack == 'typescript'`, placed where Go's and
     Rust's are:
     - "Set up Node" with `actions/setup-node` pinned by 40-hex SHA, `node-version-file: .node-version`
       (the one pin, never repeated in the workflow) and `cache: npm` (keyed by the lock);
     - "Install from the lock": `make sync INSTALL=ci`.
   - The `stack` input description becomes `python | go | rust | typescript | docs`.
   - Runner: GitHub-hosted by default, as rust's. Whether the `red-ci` guest can reach the npm
     registry and download Node is not known; the first CI run of a project on it says so, and
     red-cockpit decides where its CI runs.
   - red-rail's own tests stay static: they render and read, never run Node. The container its CI
     uses gets no Node.
   - Why: `setup-node` is the maintained way to read a version file, and `.node-version` is the
     format it reads. `npm ci` is the lock check CI needs.
   - Cost: the `setup-node` pin is one more third-party action in the shared workflow, kept like the
     others. File: `.github/workflows/rail-ci.yml`.

8. **The build gate routes typescript by the existing tables.**
   - `TEST_PROFILES` and `LINT_PROFILES` gain `Stack.TYPESCRIPT`. The completeness assertion
     (`set(LINT_PROFILES) == set(Stack)`) keeps the miss path honest.
   - `_typescript_tests(repo)` counts `*.test.ts` and `*.test.tsx` anywhere, skipping
     `node_modules`, `vendor` and `.claude-plugin`. The failure message names `*.test.ts`.
   - The `stack is None` observation keeps its text and appends the count, as rust's did: "…, none
     in tests/*.rs, none in *.test.ts" when empty, "…, {ts} (*.test.ts)" otherwise. The substring
     the existing test asserts is unchanged.
   - Cost if wrong: a plugin tested only through a `.spec.ts` FAILs `build.tests` and declares an
     override. The engine runner's own discovery pattern is measured at the plan.

9. **The typescript lint profile is a pure read; it compares the vendored types to the pin.**
   - `_typescript_profile` PASSes when all hold:
     - `.node-version` is an exact `X.Y.Z`;
     - `package.json` parses and its three devDependencies are exact versions;
     - `package-lock.json` is present (presence only: that it is committed is CI's `npm ci`);
     - `biome.json` and `tsconfig.json` are present, `.claude-plugin/plugin.json` parses with a
       `name` and an `author`;
     - the Makefile's live recipe lines (`_recipe_lines`) call `biome ci`, `tsc --noEmit`,
       `claude plugin test`, `claude plugin validate --strict` and `install` with
       `--ignore-scripts` and `rebuild @anthropic-ai/claude-code`;
     - **types:** if `vendor/claude-code/index.d.ts` is absent, the gate PASSes and its observation
       says `tsc: SKIPPED (vendor/claude-code absent)`; if present, its first line is
       `// Written by Claude Code <version>.` and `<version>` equals the pinned
       `@anthropic-ai/claude-code`, or the gate FAILs with "types written by X, pinned Y: load the
       plugin in a Claude Code Y session (or /plugin-types), then make types, or bump the pin".
   - The regexes accept `$(BIN)/`, as the others accept `$(CARGO)`. Each FAIL names the first
     missing piece and its remedy. The gate never runs Node.
   - A fresh `rail new --stack typescript --tier dev` FAILs `build.lint` until `make sync` writes
     the lock; `rail new` verifies only the bootstrap floor, so the scaffold stays green (rust's
     behaviour).
   - Why a PASS that says SKIPPED and not the gate's `skipped` field: `skipped` means the whole gate
     was not evaluated; the other seven checks are.
   - Cost if wrong: a Makefile that spells a call some unforeseen way declares an override.

10. **`rail upgrade --stack typescript` works through the existing mechanism.** The transition
    rules are rust's (only out of `docs`; refused when the manifest says `prod`; the refusal comes
    before copier; copier's merge is the one writer of `rail.yaml`). Adding the stack to
    `NOT_AT_PROD` (decision 1) is all the code changes. The tests are parametrised over both
    stacks.

11. **Ledger, tiers and the review loop are untouched.** The brain and file ledgers, the combination
    matrix and the reviewer work as for any stack; red-cockpit is born on the brain ledger with
    its ticket, through `rail new --ledger brain --ticket …`.

12. **Delivery: two pull requests, no `rail bind`.** The first carries this spec and its plan; the
    second, the code and the tests. Each is below 3,000 changed lines, excluding lockfiles and
    generated files. After the code merge, the global rail is reinstalled (snippet efdec356), and a
    tag is the operator's gesture. red-cockpit's `rail new --stack typescript` waits for that tag
    (`rail upgrade` and `rail new` render the latest tag).

## Order of work inside the code pull request

Each step is test-first, and each one is a commit.

1. Decision 8: the completeness test turns red with the enum entry, then `_typescript_tests` and
   the two table entries.
2. Decision 1: `COMBINATIONS`, then `Stack.TYPESCRIPT`, the copier choice, `NOT_AT_PROD` and both
   refusals. The parity and marker tests turn red.
3. Decisions 2–6: the render tests, then the files, the Makefile branch and the marked chains.
4. Decision 9: the gate tests on a rendered tree plus mutations, then `_typescript_profile`.
5. Decision 7: the `rail-ci.yml` test, then the two steps.
6. Decision 10: the upgrade tests over both stacks.
7. `make ci` and `rail check` from the worktree, then the host verification (criteria 9 and 10).

## Non-goals

- **A plugin at tier prod**, marketplace publication, and `rail release` or `rail deploy` for a
  plugin.
- **TypeScript that is not a Claude Code plugin** (a web app, a Node service, a library).
- **Shipping the engine's types in the template**, and verifying their content: the gate binds the
  copy to the pin by version, not by hash.
- **A second fixture** for `$.state` and any engine behaviour beyond what the reference plugin uses.
- **Another test runner**, a bundler, `tsc` emitting JavaScript, Node version managers.
- **Already-generated repositories**: red-cockpit is born on this stack; nothing is ported.
- **Changing the Go, Rust or Python CI paths.**

## Success criteria

Tests in red-rail. All are static; none needs Node, except criteria 9 and 10.

1. `test_render_typescript_bootstrap` renders `typescript`/`bootstrap`/`file` and asserts:
   - `plugin.json` parses: `name == project`, a `version`, an `author`; `hooks.json` lists
     `./register.ts`; `register.ts` imports only `../src/core.ts` and `claude-code`;
   - `package.json`: `private`, `type == "module"`, no `dependencies`, the three devDependencies
     exact; `.node-version` is `X.Y.Z`; `tsconfig.json` includes `vendor/claude-code`, `hooks` and
     `src`;
   - no `package-lock.json`, no `vendor/`, no `pyproject.toml`, `go.mod` or `Cargo.toml`;
   - `.gitignore` has `node_modules/` and `.claude-plugin/types/` and no lockfile entry;
     `settings.json` parses and allows `Bash(npm:*)`, absent from the other stacks;
   - `rail.yaml` declares `review.ignored_globs` with `vendor/claude-code/**` and a reason;
   - `CLAUDE.md` § Stack and § Structure and `AGENTS.md` § Gates carry the decision 6 content;
   - `make -n ci RAIL_FLAGS=--ci` prints, in order: `biome ci`, the typecheck, `plugin test`,
     `plugin validate --strict`, `rail check --ci`; `make -n sync` prints the install with
     `--ignore-scripts`, then the rebuild.
2. `test_typescript_at_prod_is_refused`: `NewProject(...).answers` raises `ScaffoldError` with the
   message, and a direct `render` is refused by the copier validator. A test pins `EXCLUDED` as
   exactly `(rust, prod)` and `(typescript, prod)`. The template-alignment tests stay green over
   `COMBINATIONS`: parity, the marker test, `test_rendered_guidance_points_at_the_root`, the
   `AGENTS.md` invariance, the allowed-skills test and the address test.
3. In `tests/test_gates_build.py`:
   - `test_every_stack_has_a_build_profile` and the no-profile FAIL still hold with five stacks;
   - `test_typescript_tests_are_found` (`src/core.test.ts`, a `.test.tsx`, a nested one);
     `test_a_test_under_node_modules_or_vendor_is_not_counted`; `test_typescript_repo_with_only_go_tests_fails`;
   - `test_undeclared_stack_reports_typescript_tests` (the decision 8 text);
   - `test_typescript_lint_passes_on_the_rendered_tree` (a placeholder lock written);
   - `test_typescript_lint_fails_…`, parametrised: `.node-version` as `22`, a caret in a devDependency,
     no lock, no `biome.json`, no `author`, the `validate` call commented out, an install without
     `--ignore-scripts`;
   - `test_typescript_lint_names_the_skipped_typecheck` (no `vendor/`: PASS, observation says
     SKIPPED); `test_vendored_types_must_match_the_pin` (first line equal: PASS; different: FAIL
     naming both; a file with no `Written by` line: FAIL).
4. `test_rail_ci_installs_node_from_the_project_pin` reads the parsed `rail-ci.yml`: the node steps
   are conditioned on `inputs.stack == 'typescript'`; the `setup-node` step reads
   `node-version-file: .node-version`, names no Node version, and `uses:` is 40-hex pinned;
   `make sync INSTALL=ci` follows; the input description lists `typescript`.
5. The upgrade tests, parametrised over rust and typescript: docs → stack works with real copier on
   a tagged throwaway template (answers, `rail.yaml`, the CI file and the stack's files);
   python → typescript, typescript → docs and docs → typescript at prod each exit 1 with `update`
   not called and `git status` clean; an answers file holding `typescript` and `prod` is refused
   without `--stack`.
6. `make ci` is green with its summary line read, and `uv run rail check` passes from the worktree.
7. `rail audit`'s golden `tests/golden/audit-matrix.json` is unchanged: no audited repository is
   typescript.
8. **This spec and its plan pass their gates, explicitly:** `design.spec` and `plan.plan` run on a
   copy of the tree whose `docs/specs` and `docs/plans` hold only these two files; both PASS, the
   output recorded in the PR. (The newest date is read, and the actor-identity pair is 2026-10-03.)

On the host, before merge:

9. **Measured, not assumed.** On a throwaway render at `dev`: `make sync` writes a lock and leaves
   `claude` runnable from `node_modules/.bin` (the rebuild of decision 3); `make lint test validate` exits 0 and `rail check --ci
   build --json` reports PASS for `build.tests` and `build.lint` with the SKIPPED observation;
   after the engine writes `.claude-plugin/types/` and `make types` copies it, `make typecheck`
   exits 0 and the gate compares the versions; a second `make sync INSTALL=ci` after deleting the lock fails. The trees are deleted.
10. **The ticket's "in CI", before the tag.** A throwaway private repository rendered with
    `--template-ref <head SHA>` and its lock committed: its `rail-ci` run is green twice (cold, then
    warm on the npm cache), summary lines read, run URLs in the PR; durations against
    `timeout-minutes: 20`. A wrong `setup-node` pin or a missing `claude` binary surfaces here.
    The repository is deleted afterwards.
11. **The ticket's end-to-end test.** After the tag, red-cockpit is born with
    `rail new --stack typescript --ledger brain`; its first PR runs `make ci` green in GitHub CI,
    summary line read, and `rail check` is green at its tier. 47577a6a is accepted on criteria 10
    and 11.

## Failure and threat model

What the design defends against:

- **A floating tool version.** Node, Biome, TypeScript and `claude` are exact, and the gate reads
  them; the lock fixes the rest, and `npm ci` rejects a stale one.
- **A package install script.** None runs by default (`--ignore-scripts`).
- **A silent type-check gap.** The absence of vendored types is named in the CI log and in the
  gate's observation; a present copy that no longer matches the pinned `claude` FAILs.
- **A tool fetched at run time.** Targets call `node_modules/.bin` only (`--no-install`).

What it does not defend against, declared:

- **The content of the vendored types.** They are bound to the pin by the version on their first
  line, not by a hash, and a judge never reads them. They are `import type` only, so a wrong file
  can change a type error, never what runs.
- **A project that deletes `vendor/claude-code/`.** `tsc` returns to a named SKIP; the gate passes.
- **The engine's own behaviour.** `validate --strict` and `plugin test` are the engine's checks;
  the rail runs them and reads their exit code, nothing more.

## For the operator

- **The throwaway repository of criterion 10**: creating a private GitHub repository and deleting
  it afterwards (deletion needs the `delete_repo` scope, possibly an operator gesture).
  Recommendation: allow it, as for rust; it is the only way to run the Node path before a tag.
- **Where the vendored types may live.** The copy is Anthropic's file. In a private repository
  (red-cockpit) it is the project's call; in a public one it needs the licence answer decision 4
  avoids. Recommendation: vendor only in private repositories until a public one asks.
- **`red-ci` and npm.** Unknown until a run: if the guest cannot reach the npm registry or download
  Node, red-cockpit's CI stays GitHub-hosted, or a ticket red → red-runners opens egress.
  Recommendation: start GitHub-hosted.
- **`--ignore-scripts`.** Measured: the engine package needs its `postinstall`, so decision 3 runs
  that one script through `npm rebuild`. Recommendation: keep scripts off for everything else.
- **A SKIP at `dev`.** Recommendation: allow it at day 0, with the named line; red-cockpit vendors
  its types in its first PR, and a project that wants it stricter can add a gate later.
