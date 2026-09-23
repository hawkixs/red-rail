# Gates name the missing declaration

Status: proposed — 2026-09-23 · ticket `2cbbdd22` (criterion 1 of delivery `513e109b`)

## Problem

Run `rail check --all` in an empty repository with no `rail.yaml`: it reports `passed 3/23`, and
twelve gates say exactly the same thing, `rail.yaml is missing (run rail new …)`. One of them,
`hygiene.rail_config`, is right to say it, because the manifest is its subject. The other eleven
stop at the missing manifest and hide what is actually missing from the repository:

`hygiene.mirrors`, `intent.contract`, `build.tests`, `build.lint`, `review.verdict`,
`integrate.receipt`, `release.released`, `deploy.deployed`, `observe.visible`, `observe.drill`,
`learn.fulfilled`.

Delivery `513e109b` makes the rail adoptable by existing repositories, and such a repository
starts with no manifest. Its first `rail check` is the moment the rail should show what the
repository lacks. Today it shows one thing, eleven times. The acceptance criterion reads: *a
target that can be declared but is not implemented must be visible.*

Other gates already behave the way we want: `hygiene.docs_layout` says `missing: docs/specs,
docs/plans, docs/adr`, and `design.spec`, `plan.plan` and `build.commits` name what they found or
failed to find, whether or not there is a manifest.

These eleven gates depend on the manifest for three fields only:

- `stack`: `build.tests`, `build.lint`;
- `project`: filters the ledger for `intent.contract` and the six evidence gates, and names the
  stack red-monitor watches for `observe.visible`;
- `ledger`: which backend to open (every ledger gate, and `hygiene.mirrors`).

## Decisions

1. **A third outcome: the verdict needs a declaration.** `GateResult` gains
   `needs: str | None`, the manifest key the verdict depends on (`"stack"`, `"project"`). When
   `needs` is set, `passed` is `False`. It is fail-closed: a gate never turns green because a
   manifest is missing, the exit code stays 1 and the score counts the gate as not passed. The
   outcome is shown distinctly from an observed gap, as `NEED` beside `PASS`/`FAIL`/`SKIP`/`EXC`.

2. **The rule that separates FAIL from NEED, without a manifest.**
   - A field with a documented default (`ledger: file`) is evaluated with that default, exactly
     as it would be for a manifest that omits the field. The details say so
     (`default file ledger`).
   - For a field with no default (`project`, `stack`): when what the gate observed is a gap
     whatever the field's value, the result is **FAIL**, naming the observed gap. When some value
     of the field could change the verdict, the result is **NEED** with that field, and the
     details carry what was observed.

3. **One partial reading of the manifest.** `rail.model.declarations(repo)` returns
   `Declarations(project, stack, ledger, cfg)`:
   - manifest absent: `project=None`, `stack=None`, `ledger=FILE`, `cfg=None`;
   - manifest valid: its real fields, and the full `cfg` for the gates that need more (tier,
     deploy, gates overrides);
   - manifest invalid: the `manifest_problem` string, unchanged.

   The eleven gates read this view instead of calling `load_rail_config` themselves. When a
   manifest is present the gates take the same path as today.

4. **An invalid manifest never falls back to defaults.** A `rail.yaml` that declares
   `ledger: brain` and has a mistake elsewhere must not make the gates treat `docs/receipts` as
   authoritative: those receipts are mirrors, and judging them could produce a false green. The
   defaults of decision 2 apply only when the file is **absent**. An invalid manifest keeps
   today's behaviour: every dependent gate reports the manifest problem.

5. **`open_ledger` is unchanged.** It keeps refusing a repository without a manifest, same as
   `origin/main`: absent raises `FileNotFoundError`, invalid raises `ValidationError`. It is a
   write path, not an observation, and stays fail-closed. The gates observe the default file
   ledger directly (`FileLedger(repo / RECEIPTS_DIR)`), so no write command can reach receipts
   without a manifest.

6. **Only `hygiene.rail_config` names the manifest file.** No other gate's details mention
   `rail.yaml` when there is no manifest. They say `default file ledger` or ``needs `stack:` ``.
   This is a tested property, not a wording habit.

7. **Rendering.**
   - `rail check` text: tag `NEED`, which takes precedence over `FAIL`. The summary line appends
     `— needs a declaration: <gate ids>`.
   - `rail check --json`: each gate carries `needs`. The top level gains
     `needs_declaration: [gate ids]`, alongside `not_evaluated` (PR #33). `passed` stays false.
   - `rail audit`: a new cell status `needs`, symbol `?`. A stage is `?` when every non-passing
     gate in it is NEED, and `✗` as soon as one of them is FAIL. The legend gains `? needs a
     declaration`. The score is unchanged.

## The eleven gates, without a manifest

| Gate | Observation | Outcome |
|---|---|---|
| `build.tests` | `tests/test_*.py` / `*_test.go` counted | always **NEED `stack`** (`stack: docs` needs no tests), the counts in the details |
| `build.lint` | ruff configuration and `go.mod`, present or not | always **NEED `stack`**, what was found in the details |
| `hygiene.mirrors` | none needed | **PASS**: `file ledger (default): the receipts are the ledger` |
| `intent.contract` | `contract` receipts in `docs/receipts` | none: **FAIL** `no contract recorded in docs/receipts (default file ledger)`; some: **NEED `project`** |
| `review.verdict`, `integrate.receipt`, `release.released`, `deploy.deployed`, `observe.drill`, `learn.fulfilled` | receipts of the gate's kind, through `_attestations` | none: **FAIL** with today's message, suffixed `in docs/receipts (default file ledger)`; some: **NEED `project`**, `N <kind> receipt(s) in docs/receipts — project: says which are this repository's` |
| `observe.visible` | a `deployed` receipt | none: **FAIL** `no deployed attestation to observe`; one: **NEED `project`**, since the project names the stack red-monitor watches |

On an empty repository this gives one line naming the manifest, two NEED `stack` lines,
`hygiene.mirrors` passing, and eight FAIL lines each naming an observed gap.

The six evidence gates all read the ledger through `_attestations(repo, kind)`. That single
function carries the rule for them: without a declared project, it lists every receipt of the
kind in `docs/receipts`. None found is an observed gap. Some found is a NEED, because the rail
does not guess which project they belong to.

## Non-goals

- **No inference of `project` or `stack`.** The rail does not guess the project from the
  directory name or the stack from `pyproject.toml`. A guessed declaration would let a gate judge
  on something nobody declared.
- **No change to an invalid manifest's output** (decision 4).
- **No change when a manifest is present.** Gates, messages and scores stay identical. The
  golden audit matrix is unchanged for every repository with a manifest; the manifest-less
  fixture gains one pass (`hygiene.mirrors`, default file ledger).
- **No change to `skipped` or to the `not_evaluated` list of PR #33.** "Not evaluated here"
  (scope) and "needs a declaration" (manifest) remain distinct.
- **The other gates** (`hygiene.*` other than `mirrors`, `design.spec`, `plan.plan`,
  `build.secrets`, `build.commits`) already name what they observe and are not touched.

## Success criteria

1. In a `git init` repository with no `rail.yaml`, `rail check --all` shows exactly one line
   containing `rail.yaml`, `hygiene.rail_config`. Every other non-passing line is either a FAIL
   naming an observed gap or a NEED naming a manifest key. Tested at the CLI level: this is
   red's criterion, which red will run itself.
2. `rail check` on red-alerts stays 18/18, measured on the real checkout.
3. The existing suite passes; the golden audit matrix passes unchanged for every repository
   with a manifest, and the manifest-less `red-delta` row gains one pass.
4. A manifest that declares `ledger: brain` but is invalid never reads `docs/receipts` (test).
5. Mutation counter-proof: each of these mutants turns a test red:
   - removing `declarations`'s default fallback for an absent manifest;
   - turning a NEED into a FAIL;
   - letting `declarations`'s fallback apply to an invalid manifest;
   - letting a non-`rail_config` gate mention `rail.yaml`.
6. `rail check design` passes on this repository with this spec.
