# Template alignment: what `rail new` writes matches the ecosystem it lands in

Status: proposed — 2026-09-24

Tickets: 09b9e210 (template `CLAUDE.md` drift, with the four RED-6 gaps), cdb725e4 (the roster
gate checks nothing), 2a6781cb (the template CI passes no `rail-ref`). Decision 8d875924 is the
contract for guidance files. This is spec A of two. Spec B (the rust stack, ticket 293524e3, and
`rail upgrade --stack`) comes later and builds on the hooks this spec leaves.

## Problem

A project scaffolded today inherits four kinds of drift, and one gate that should catch part of
it passes without looking.

- **Stale working principles.** `template/project/CLAUDE.md.jinja:81-112` cites skills and
  slash-commands that no longer exist (`sdd-brainstorm`, `writing-plans-parallel`,
  `executing-plans-parallel`, `/tdd-write-tests`, `/reflexion-reflect`,
  `/code-review-review-local-changes`). It also restates part of the method chain that the ReD
  root `CLAUDE.md` now owns once, in § "Workflows — the operator picks the method" and
  § "Invariants — true whatever the method". red-rail's own `CLAUDE.md` has the same pipeline.
- **RED-6's four gaps.**
  1. There is no `AGENTS.md.jinja`, although 8d875924 wants one per project. Codex stops at
     the sub-project's git root and never sees the root `AGENTS.md`.
  2. No "Question → where to look" table exists, in the template or in red-rail.
  3. `rail new` prints a roster row with five cells (`new.py:144`: tier, `n/a`), while the root
     roster has four (`| Project | Domain | What it is | Brain key |`).
  4. Every `brain_session_start(...)` example omits `client_key`: `CLAUDE.md.jinja:118`,
     red-rail `CLAUDE.md` § Brain MCP, red-rail `AGENTS.md` § Brain MCP.
- **`hygiene.roster_entry` never finds the roster.**
  `ROSTER_MARKER = "| Projet |"` (`hygiene.py:32`) is French while the root is English;
  `find_roster` looks exactly two levels up (`hygiene.py:82`), `<repo>/.claude` from a nested
  worktree; nothing found means PASS "standalone" (`hygiene.py:193-196`). The fixtures
  (`tests/helpers.py:124-127`, `tests/test_helpers.py:26-29`) use the French header, so the
  suite cannot see the regression.
- **The CI pin is half a pin.** `continuous-integration.yml.jinja` pins
  `uses: …/rail-ci.yml@{{ rail_ref }}` but passes only `stack`, so `rail-ci.yml` installs
  `rail` at its `rail-ref` default, `main`: the workflow is frozen, the gate code floats.
- **Stack list with no guard.** `copier.yml` `stack.choices` duplicates `Stack` (`model.py`)
  by hand. `deploy_target` has a guard test (`test_scaffold.py:605`); `stack` has none.

## Decisions

Each decision states what it decides, why, and the files it touches; the cost if it is wrong
is given where it is not negligible.

1. **The template CI forwards the pin.** `continuous-integration.yml.jinja` gains
   `rail-ref: "{{ rail_ref }}"` under `with:`, always emitted and quoted (an all-digit SHA
   would otherwise parse as a number; `uses:` is safe because the SHA sits inside a string).
   - Why: one resolved value (`resolve_rail_ref`) drives the workflow and the `rail` code it
     installs. Rejected: emitting it only for a SHA (two render paths); changing the
     `rail-ci.yml` default (it cannot equal each caller's pin). Files: the workflow template,
     `tests/test_scaffold.py`.

2. **The roster is recognised by its full identity header, held in one constant.**
   - `hygiene.py` replaces `ROSTER_MARKER` with
     `ROSTER_HEADER = ("Project", "Domain", "What it is", "Brain key")`, plus a helper that
     renders it as a Markdown header line, and `DOMAIN_PLACEHOLDER = "<domain>"`. A row counts
     only inside that table's block: the header, the separator, then consecutive `|` lines.
   - Why: a bare `| Project |` prefix also matches "Related projects" tables. A
     language-tolerant regex would keep a dead language alive in an English-only root.
   - Files: `src/rail/gates/hygiene.py`; `tests/helpers.py` (`write_roster` builds its header
     from the constant and its rows with `len(ROSTER_HEADER)` cells); `tests/test_helpers.py`
     (its `"| Projet |"` assertion follows the constant).

3. **The root is found by walking up the ancestors, not by a fixed depth.**
   - The walk starts at the parent of `normpath(repo.absolute())`, lexically, with no symlink
     resolution, as today. The first `CLAUDE.md` holding the identity header wins.
   - Only one candidate may fail the gate on a read error: the `CLAUDE.md` in the parent of a
     `projects` ancestor (the ReD position). Any other unreadable `CLAUDE.md` met on the walk
     is skipped, so an unrelated ancestor never fails a repository.
   - Why: it covers `projects/x`, `projects/x/.claude/worktrees/w`, `projects/x/.worktrees/w`
     and `rail audit ..` without git or a subprocess. It is safe only because of decision 2: no
     sub-project `CLAUDE.md` carries the full header.
   - Rejected: asking git for the common directory (a subprocess, and a fixed depth still).
   - Known limit, unchanged: `Path.cwd()` is physical, so `rail check` run from inside a
     symlinked project (brain-v42, auto-discord, red-runners), the default invocation, walks up
     from the target and reports standalone (checked: `Path('.').absolute()` in
     `projects/brain-v42` is `…/git_repo/brain_v42`). Only a lexical path handed from outside
     (`rail audit ..`, `rail check projects/brain-v42` from the root) reaches the roster.
     Cost if wrong: an unrelated ancestor with the exact header is taken as the roster.

4. **The gate has three outcomes, and "standalone" always says why.**
   - (i) Header found: PASS "listed in …" if the row is in the block; FAIL "… has no row"
     otherwise; FAIL "… row still holds `<domain>`" when the row's Domain cell equals
     `DOMAIN_PLACEHOLDER` (the row was pasted from decision 5 unedited).
   - (ii) No header found, an ancestor is named `projects` and its parent holds a `CLAUDE.md`:
     FAIL "roster header not found in <parent>/CLAUDE.md: the root format drifted". An
     `OSError` or `UnicodeDecodeError` on that file is a FAIL naming it. The gate never
     raises; scope stays `workstation` (skipped under `--ci`).
   - (iii) Otherwise: PASS, with the reason in the message: "standalone: no `projects`
     ancestor", or "standalone: no CLAUDE.md in <parent of projects>".
   - Deliberate narrowing of cdb725e4's "never standalone under `projects/`": a `projects/`
     with no `CLAUDE.md` above it has no ReD root to drift from; failing it would fail any
     unrelated `~/projects/` checkout and the suite's fixtures (`conforming_tree` builds
     `tmp/projects/red-alpha` with no root). A moved ReD root stays visible in the message.
   - Cost if wrong: a project under another `projects/` with a `CLAUDE.md` above it fails
     locally and declares an override. Files: `hygiene.py`, `tests/test_gates_hygiene.py`.

5. **`rail new` prints a four-cell row built from the same constants, and says what follows.**
   - The row is ``| {slug} | <domain> | {description} | `{brain_key}` |``, its second cell
     `DOMAIN_PLACEHOLDER`: the domain is the operator's classification.
   - The message states that `rail check` fails `hygiene.roster_entry` until the row is in the
     root with its domain filled in; `rail new` cannot see it, its gates run with `ci=True`
     (`scaffold.py:272`). One set of constants feeds the gate, the fixture and the printout.
     Files: `src/rail/commands/new.py`, `tests/test_scaffold.py`.

6. **The template's working principles become a pointer plus the rail's own invariants.**
   - `CLAUDE.md.jinja` § Working principles is replaced by one section, `## How we work`:
     - The method, the review that reads the diff, and the invariants that hold whatever the
       method live once in the ReD root `CLAUDE.md`, § "Workflows — the operator picks the
       method" and § "Invariants — true whatever the method". They are not copied here, the
       review gate included (root invariant 1: the independent verdict is the verdict, a
       pre-review never satisfies it). The root's path is already in § Project, "Parent
       project".
     - What the rail adds: `rail check` is the verdict and `make ci` is exactly what CI runs;
       the only bypass is a `gates:` override in `rail.yaml` with its reason (no
       `# rail: ignore`); evidence is written by the rail only (`ledger: file`:
       `docs/receipts/`; `ledger: brain`: attestations against the ticket in `rail.yaml`,
       receipts as mirrors); secrets never enter the tree, an environment variable holding
       the value, or a command line.
   - The self-improvement lines move into § Brain MCP. No skill or slash-command is named.
   - Why: a skill list rots, and this one did. Rejected: a refreshed list (rots again);
     dropping the section (loses the rail invariants).
   - Accepted coupling, checked on the host by criterion 10: the root's two section titles
     (a rename leaves generated pointers stale until the next upgrade); and the two root
     passages `AGENTS.md` restates because Codex may never read the root (decision 9): root
     invariant 1's review sentence and § "Harness limits", fixed-string checked.

7. **Every generated `CLAUDE.md` carries a "Where things live" table**, right after § Project,
   with project-level rows only. The root's methods table is never copied.

   | Question | Where to look |
   |---|---|
   | What is this project, what stack, what key? | this file |
   | What tier, which ledger, which target? | `rail.yaml` |
   | What state is it in, what is the focus, what is blocked? | the session-start call in § Brain MCP |
   | What can break here, which gates? | `AGENTS.md` |
   | What was promised, what evidence exists? | `ledger: file`: `docs/receipts/`; `ledger: brain`: the ticket named in `rail.yaml` |
   | Why did we choose Y? | brain decisions, `docs/adr/` |
   | Specs and plans | `docs/specs/`, `docs/plans/` |
   | Which method, which review? | root `CLAUDE.md` § "Workflows — the operator picks the method" |
   | Machines, addresses, access | brain machine records (`brain_recall`), never this file |

   - Every row renders unconditionally: tier, ledger and target move and nothing re-answers
     them (decision 10), so the table names `rail.yaml`, not a value. The state row names no
     call (the only `brain_session_start(` is decision 8's). Flagged below: 8d875924's "a
     table in each file" is read as each `CLAUDE.md`.

8. **One `client_key` form, everywhere, over a validated key.** Every example of the call reads
   `brain_session_start("<brain_key>", client_key="<harness>-<brain_key>-<YYYY-MM-DD>")`,
   where `<harness>` is `claude-code`, `codex` or `opencode`, followed by one sentence: reuse
   the key for every retry of that session, and give a parallel session its own suffix.
   - `<brain_key>` is rendered (in the template) or written out (`red-rail` in red-rail's own
     files); `<harness>` and `<YYYY-MM-DD>` stay literal placeholders.
   - `brain_key` is validated at scaffold time by `BRAIN_KEY = ^[a-z0-9][a-z0-9_-]*$` (every
     root roster key fits, `auto_discord` included): a `copier.yml` validator and a
     `--brain-key` callback in `new.py`, as `project`/`SLUG` are. `RailConfig.brain_key` is
     not tightened (existing manifests stay valid).
   - The checkable form: every `brain_session_start(` starts a match of
     `brain_session_start\("(?P<k>[a-z0-9][a-z0-9_-]*)", client_key="<harness>-(?P=k)-<YYYY-MM-DD>"\)`,
     on the rendered `CLAUDE.md` and `AGENTS.md` of every combination and on red-rail's own
     two files, never on raw Jinja. Same class as the validator, so every accepted key is covered.
   - Why: `client_key` is free-form; one placeholder form reads the same everywhere and is
     checkable with one regex. Files: both templates, `CLAUDE.md`, `AGENTS.md`, `copier.yml`,
     `src/rail/commands/new.py`.

9. **`AGENTS.md.jinja` follows the generic shape of red-rail's `AGENTS.md`.** It carries what
   Codex cannot reach and points at the rest. Sections, in this order:
   - Header: read `CLAUDE.md` first (identity, stack, commands, layout; not duplicated here),
     then the ReD root `AGENTS.md`, in the directory `CLAUDE.md` names as "Parent project".
     No relative `../../AGENTS.md` (it breaks from a nested worktree, as cdb725e4 did); no new
     absolute path (the template already names the root once).
   - § What this project is: `{{ description }}` and the stack; tier, ledger and deploy target
     are read from `rail.yaml`, never rendered here.
   - § Method on this harness: the operator picks the method; `graph` is unavailable outside
     Claude Code, so the choice narrows to `spec` or `direct`, and the harness says so. When
     `rail.yaml` is present its gates apply on top, and the independent verdict is the verdict
     (both restated from the root: decision 6, accepted coupling).
   - § The invariants you must not break: an Invariant | Why table (decision 10), ending with
     the row "project-specific: fill in".
   - § Gates: `make ci`, `rail check`, and the stack's own commands (decision 11).
   - § Brain MCP: the project key, the decision 8 call, and "the specific tool, never
     `brain_learn` by default".
   - § Subagents: the perimeter rule in three lines (a path or glob, a budget, or an output
     contract; name the files you know), then a pointer to the root's § Subagents.
   - Why: RED-6 requires the file to carry or point at what matters. A thin pointer loses the
     invariants; a copy of red-rail's file is public-repo- and project-specific.
     Files: `template/project/AGENTS.md.jinja` (new).

10. **The invariant rows are rendered unconditionally, each prefixed by the `rail.yaml` value
    it applies to.** They never name a private target, and they state only what the rail
    enforces.
    - Always: `rail check` is the verdict; the only bypass is a `gates:` override with a reason.
    - "With `ledger: file`": never edit a receipt by hand (append-only, digest-checked, fails
      closed).
    - "With `ledger: brain`": evidence is attested to brain; mirrors are written by the rail;
      `integrated` and `fulfilled` are read from the ticket.
    - "At `tier: prod`": a release is an image pinned by digest; deployment goes through
      `rail deploy` only, never by hand on the target.
    - "At `prod` with `deploy.target: vps-traefik`": nothing is published; the Traefik route
      is the only way in.
    - "At `prod` with any other target": Docker bypasses the firewall, so a compose file
      publishes on the target's bind address only, never on every interface; `rail deploy`
      refuses a compose file that would. The row makes no claim about addresses in the
      repository, because the template does not yet render `deploy.site` (see § Non-goals).
    - Why unconditional: copier answers freeze at scaffold time. `rail upgrade` passes only
      `rail_ref` with `skip_answered=True` (`scaffold.py:344-351`) and nothing re-answers
      `tier` on promotion, so a branched row would leave a promoted project without its prod
      rows; a fact that moves is read where it moves (8d875924), in `rail.yaml`.
    - Why no private target name: the private-systemd branch renames and adds private targets
      (`copier.yml`, `model.py`); naming only `vps-traefik` keeps the coupling a textual
      conflict in `copier.yml`, resolved by whichever branch merges second.
    - Tested: for a given stack, `AGENTS.md` is byte-identical across tier × ledger × target.
      Cost if wrong: a bootstrap project reads rows that do not apply yet.

11. **Stack-conditional blocks name every stack explicitly, inside a marker.**
    - `CLAUDE.md.jinja` § Stack and § Structure, and `AGENTS.md.jinja` § Gates, become
      `if python / elif go / elif docs` chains with no catch-all `else`. Each chain sits
      between `{# stack-chain: <name> #}` and `{# /stack-chain #}` Jinja comments, which render
      nothing. Stack stays a render-time branch: B's `rail upgrade --stack` re-answers it.
    - One test reads the two template sources, extracts every marked block, and asserts that
      each holds `stack == '<value>'` for every `Stack` member.
    - Why: spec B's hook. A render test cannot see a missing branch (§ Structure and § Gates
      always render stack-independent lines); once B adds `rust`, this test names each marked
      chain still missing it. Files: both templates, `tests/test_scaffold.py`.

12. **`Stack` and `copier.yml` stay equal, by test**, modelled on the `deploy_target` guard:
    `{s.value for s in Stack} == set(copier.yml["stack"]["choices"])`. Today a drift fails
    only when `rail new --stack X` runs; this protects B directly.

13. **red-rail dogfoods its template in the same pull request.**
    - `CLAUDE.md` § Working principles gets decision 6: the stale pipeline and the copied
      method chain leave, the root pointer comes in. What stays: "`rail check` passes on this
      repository"; "every Workflow `agent()` in `workflows/` carries an explicit tier"
      (`pre-review.js` must pass the tiering gate); a pointer to `AGENTS.md` for invariants.
      It gains the decision 7 table; both files get the decision 8 call. `AGENTS.md` replaces
      `../../AGENTS.md` with the decision 9 header wording and keeps its public-repo rules.
    - Why: a rail whose guidance contradicts its template cannot ask twenty repositories to
      align. Files: `CLAUDE.md`, `AGENTS.md`.

14. **Citing a skill is guarded in CI by an allowlist, and on the host by one verification.**
    - Tests never read host files (the suite's rule, `tests/conftest.py`). The CI test renders
      stack × tier × ledger plus both target families at `prod`, and reads the rendered
      `CLAUDE.md` and `AGENTS.md`.
    - Extraction runs on each whole backticked token, anchored:
      - slash-command: `^/[a-z][a-z0-9-]*$`, minus `ROUTES = {"/healthz", "/version",
        "/metrics"}` (the service's HTTP routes, rendered in § Service at `prod`/python);
      - namespaced skill: `^[a-z][a-z0-9-]*:[a-z][a-z0-9-]*$` (so `git@github.com:…` and
        `gates: …` never match);
      - bare skill: kebab-case `^[a-z][a-z0-9]*(-[a-z0-9]+)+$`, minus the render's own slug and
        brain key and the `DeployTarget` values.
    - It asserts that the extracted set is a subset of `CITABLE`, a constant kept in the test,
      empty after decision 6, and that none of the six stale names appears anywhere.
    - Why: the pointer removes the need to cite; the allowlist makes any new citation, bare
      name included (`gitnexus-lfg`, `red-review`), a reviewed line. Files: `test_scaffold.py`.

15. **No machine address in rendered output, under the repository's one address policy.
    Machine names are checked on the host only.**
    - `ALLOWED`, `_candidates` and `_foreign` move from `tests/test_no_machine_address.py` to
      `tests/addresses.py`, imported by both tests: one policy (documentation ranges,
      loopback, unspecified, link-local). A narrower loopback-only list is rejected: a private
      target needs a typed healthcheck (`copier.yml:50`, no default), the suite types
      `http://192.0.2.10:9204/healthz`, and such a list would fail it by construction.
    - A test renders every combination and applies `_foreign` to every file. Addresses reach
      the render through the typed healthcheck (`rail.yaml`, `rail.yaml.jinja:13`; `CLAUDE.md`
      § Service, `CLAUDE.md.jinja:32`) and the `prod`/python service's fixed loopback and
      wildcard (`Dockerfile`, `compose.yaml`, `Makefile`, `test_service.py`, `service.py`,
      `__main__.py`). The test proves the template adds no address of its own.
    - Machine names cannot be listed in a public test without publishing them: criterion 10
      greps for them on the host. Why: red-rail is public. Files: `tests/addresses.py` (new),
      `tests/test_no_machine_address.py`, `tests/test_scaffold.py`.

16. **Delivery: one pull request from `origin/main`, with no `rail bind`, then a tag.**
    - The branch starts at `f5b7d8f` and does not wait for the private-systemd branch
      (decision 10). No `rail bind`: engagement d096f911 allows none until red opens the next
      phase ticket. One pull request: the three tickets share `CLAUDE.md.jinja`,
      `test_scaffold.py` and the roster constant; commits follow the order below.
    - After the merge, an annotated tag `v0.5.0` is cut on `origin` from `main`, as `v0.4.0`
      was. `rail upgrade` and a `rail new` without `--template-ref` render the latest tag
      (`scaffold.py:330`, no `vcs_ref`), so nothing of A reaches any repository before it.

## Order of work inside the pull request

Each step is test-first, and each one is a commit.

1. Decision 12: the `Stack` ↔ copier guard (green at once; it is a guard, not a fix).
2. Decision 1: the failing parsed-YAML render test, then the template line.
3. Decisions 2-4: the fixtures move to the constants (the roster tests fail), then the
   nested-worktree, drift, related-projects, placeholder and unreadable tests, then the gate.
4. Decisions 5 and 8 (key validation): row-shape and key-refusal tests, then `new.py`, `copier.yml`.
5. Decisions 6-8 and 11: the rendered-guidance and marker tests, then `CLAUDE.md.jinja`.
6. Decisions 9-10: the `AGENTS.md` render and invariance tests, then `AGENTS.md.jinja`.
7. Decisions 14-15: the address helper moves (the existing test stays green), then the
   allowlist and rendered-address tests (green once steps 5-6 land).
8. Decision 13: red-rail's `CLAUDE.md` and `AGENTS.md`, then the decision 8 regex test.
9. `make ci`, `rail check` from this worktree, and the host verification (criterion 10).

## What spec B adds, and where A leaves room

- `Stack.RUST` and `rust` in `copier.yml` (the decision 12 test forces both), and one
  `elif stack == 'rust'` per marked chain of decision 11 (the marker test lists those missing).
- The unmarked stack chains no A test guards: `Makefile.jinja`, `.gitignore.jinja`,
  `.claude/settings.json.jinja`. B extends them (and may mark them).
- The rust files as conditional template paths (`{% if stack == 'python' %}src{% endif %}`).
- The build gate: `build.py` sends every non-Python stack to Go (a bare `else` in
  `has_tests`, a final `return _go_profile` in `lint`); a per-stack branch and a FAIL for an
  unknown stack belong to B. A leaves `build.py` alone.
- `rail upgrade --stack`, the transition rules, and the rust step in `rail-ci.yml`.

## Propagation facts for the per-repository tickets

Out of scope for A (non-goal). After A merges and `v0.5.0` is cut, one ticket per repository,
each carried out in a reviewed pull request on that repository's own rail. These facts, read on
the host on 2026-09-24, are what those tickets need.

- **Precondition: the tag.** Before `v0.5.0`, `rail upgrade` brings nothing of A (it still
  re-pins `rail_ref`); a per-repository PR shows `_commit: v0.5.0`.
- **Per repository, `.copier-answers.yml` decides the path.** `rail upgrade` refuses a missing
  file or one without `_commit`/`_src_path` (`scaffold.py:335-343`).

  | Repository | Answers file | CI file | Path |
  |---|---|---|---|
  | red-probe | `_commit: v0.4.0`, git `_src_path` | `continuous-integration.yml` | `rail upgrade`, as designed |
  | red-store | `_src_path: .`, `_commit: fa79407` | `continuous-integration.yml` | fix the answers file (git URL, a tag), then `rail upgrade` |
  | red-writer | none; hand-written `AGENTS.md` | `continuous-integration.yml` | hand port, or re-scaffold into a versioned answers file |
  | red-alerts | none | `ci.yml` (SHA `uses:`, no `rail-ref`) | hand port: add `rail-ref`, port the guidance |
  | red-monitor | none | `ci.yml` | hand port, as red-alerts |

- **On the copier path (red-probe, then red-store):** `AGENTS.md` arrives through
  `copier update` (`overwrite=True`, no `_skip_if_exists`, which would freeze it); the PR
  fills the placeholder row. `CLAUDE.md` conflicts where the old Working principles were
  edited, and `rail.yaml` is re-rendered (hand-added `gates:` overrides may return with
  conflict markers). Diff review (`upgrade.py:22`) is the only safety net.
- **On every path:**
  - Forwarding `rail-ref` moves a repository's CI `rail` from floating `main` to its pin;
    gate differences surfacing in CI is the point.
  - The roster gate starts evaluating locally, for every child `rail audit` discovers (a
    `.git` or a `rail.yaml`, `audit.py:74-89`). A child with no identity row turns FAIL in
    `rail check` and `rail audit`; CI is unaffected (workstation scope). Known today:
    `red-e2e-target` and `leaked-claude-code` have a `.git` and no row ("not a sub-project",
    "foreign body" in the root's Layout), so the next audit snapshot FAILs both. That is the
    root's roster rule made visible, not a regression; the resolution is the operator's.
  - Symlinked projects still report standalone from their own directory (decision 3).

## Non-goals

- **Spec B**: the rust stack (293524e3), `rail upgrade --stack`, the build gate's per-stack
  routing, the unmarked stack chains, and the rust CI step.
- **Beyond B**: rust at tier `prod` (the static musl image). Installing cargo-deny with
  `cargo install --locked` is a B matter, named here only.
- **Already-generated repositories** (see § Propagation facts): no upgrade or port here.
- **No new gate on hand-written CI pins** (red-alerts' `rail-ref`: its own ticket).
- **Tier promotion.** Nothing re-answers `tier` or `deploy_target`, so the prod-only files
  (§ Service, `deploy/`, the service skeleton) never appear on promotion. A adds no
  tier-, ledger- or target-conditional guidance (decisions 7, 10); a promotion path is its own
  ticket.
- **The template's private-target render.** At `prod`/python a private target still renders
  Traefik-shaped (§ Service's "behind Traefik" sentence, `deploy/compose.yaml`) and writes the
  typed healthcheck address into `rail.yaml`, with no `deploy.site`. That belongs to the
  private-target work; decision 10's private row claims nothing this render contradicts.
- **No edit to the ReD root** (not under git, the operator's), including its
  `brain_session_start("red")` examples without `client_key` and its roster rows.
- **No change to `rail-ci.yml`** (`rail-ref` default stays `main`); no fix for symlinked
  projects run from their own directory (decision 3); no waiting for the private-systemd
  branch and no `rail bind` (decision 16).

## Success criteria

1. `test_render_forwards_the_pin_to_rail_ref`, parametrised over a hex SHA, an all-digit
   40-character SHA and `main`: in the parsed rendered YAML, `jobs.rail.uses` ends with
   `@<ref>` and `jobs.rail.with["rail-ref"]` is the string `<ref>`. The upgrade test keeps its
   contract (`seen["data"] == {"rail_ref": new}`): forwarding is a property of the render.
2. Roster tests in `tests/test_gates_hygiene.py`:
   - `test_roster_entry_finds_the_root_from_a_nested_worktree` (`.claude/worktrees/w`,
     `.worktrees/w`); `…_ignores_the_related_projects_table`;
     `…_fails_when_the_root_header_drifted`; `…_fails_on_a_row_still_holding_the_domain_placeholder`;
   - `…_fails_closed_on_an_unreadable_roster` (the ReD-position candidate),
     `…_skips_an_unreadable_unrelated_ancestor`;
   - kept and green (standalone now states its reason): `…_reads_the_red_root`,
     `…_folds_dotdot_paths_to_the_root`, `…_is_standalone_without_a_roster`. The golden
     `tests/golden/audit-matrix.json` is unchanged.
3. `test_cli_new_prints_a_roster_row_shaped_like_the_header`: `len(ROSTER_HEADER)` cells,
   slug first, `DOMAIN_PLACEHOLDER` second, and the output says `rail check` fails
   `hygiene.roster_entry` until the row is added. `test_new_refuses_a_brain_key_outside_the_pattern`
   covers the CLI callback and the `copier.yml` validator (an uppercase key, a dotted key).
4. `test_every_stack_the_cli_offers_is_a_copier_choice` (decision 12).
5. `test_rendered_guidance_points_at_the_root`, parametrised over stack × tier × ledger, plus
   both target families at `prod`. It asserts:
   - `CLAUDE.md`: both root section titles verbatim, the "Where things live" header with its
     `rail.yaml` row, no `spec`/`graph`/`direct` table;
   - `AGENTS.md`, in order: the `CLAUDE.md` pointer; the root pointer via "Parent project";
     the harness-method paragraph; every decision 10 row with its condition; § Gates; the
     decision 8 call; the perimeter rule; and no private target's name.
   And `test_agents_md_does_not_depend_on_tier_ledger_or_target`: per stack, one `AGENTS.md`.
6. `test_every_stack_chain_names_every_stack` (decision 11): each marked chain holds a branch
   per `Stack` member.
7. `test_every_brain_session_start_carries_a_client_key` (decision 8's regex, on the rendered
   files and red-rail's own two files).
8. `test_rendered_guidance_cites_only_allowed_skills`, including a `prod`/python render where
   `/healthz`, `/version` and `/metrics` appear, and `test_rendered_files_carry_no_address_literal`,
   including a private-target render with the documentation-range healthcheck, through the
   shared `_foreign` (decisions 14 and 15). `test_no_tracked_file_carries_a_machine_address`
   stays green after the move.
9. `make ci` is green, with its summary line read. `uv run rail check` passes when run from
   this worktree (`.claude/worktrees/template-alignment`), and its `hygiene.roster_entry` line
   reads "listed in …/CLAUDE.md", not "standalone". That is cdb725e4 proven live.
10. Host verification by the session on the committed branch, recorded in the pull request:
    - `uv run rail new red-throwaway --description "throwaway" --no-remotes --template .
      --template-ref HEAD --dest <tmp>` for python/bootstrap, go/dev and docs/bootstrap; each
      tree contains `AGENTS.md` (it came from A, not `v0.4.0`). Decision 14's extraction on
      them yields only names under `~/.claude/skills` or the plugins' cache (expected: none).
    - Machine names, sourced from the root table headed `| Machine | Role |` (never the
      project roster, which holds no machine and whose `red-rail` is in every render): its
      hostnames, ssh aliases and WG/LAN addresses. A grep for each finds nothing rendered.
    - Fixed-string greps in the root `CLAUDE.md` find: both section titles of decision 6,
      `### Harness limits`, `A pre-review never satisfies the review gate`, and
      ``the `graph` method is **unavailable**`` (decision 6, accepted coupling).
    - The tmp trees are deleted afterwards.
11. After the merge, `v0.5.0` exists on `origin`, and a throwaway `rail new` with no
    `--template-ref` renders `AGENTS.md` (decision 16).

## For the operator

- **Decision 7:** 8d875924's "table in each file" is read as each `CLAUDE.md`. `AGENTS.md`
  reaches the table through its first pointer. Say so if `AGENTS.md` should carry its own.
- **Decision 8:** `<harness>-<brain_key>-<YYYY-MM-DD>` is one of several equivalent forms:
  cheap to change before the PR, costly after five repositories upgrade.
- **Decision 9:** `AGENTS.md` reaches the root through "Parent project", keeping a
  personal-machine path out of the public `AGENTS.md` at the cost of one read for Codex.
- **Roster:** `red-e2e-target` and `leaked-claude-code` will FAIL `hygiene.roster_entry` in
  the audit. Give them identity rows, or move them out of `projects/` (the root rule asks one).
- **Decision 3:** starting the walk from `$PWD` when it names the same directory would end
  standalone for symlinked projects, at the cost of a gate reading the environment. Out of A.
