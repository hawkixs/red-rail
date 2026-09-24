# Template alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** What `rail new` writes matches the ecosystem it lands in. The template CI installs the
rail at the ref it pins. The roster gate finds the ReD root from any depth and judges the row.
The row `rail new` prints fits the root's table. The generated guidance (`CLAUDE.md` and a new
`AGENTS.md`) points at the ReD root instead of copying a skill list that rots. red-rail's own
guidance does the same.

**Architecture:** One set of roster constants in `src/rail/gates/hygiene.py` feeds the gate, the
test fixture and the row `rail new` prints. The gate finds the root by walking up the lexical
ancestors to the first `CLAUDE.md` holding the full identity header. It has three outcomes: the
row is judged; a ReD position whose header drifted fails; or the repository is standalone, and
the message says why. The templates stop naming skills. They point at the root's two sections
and carry only the rail's own invariants. Each invariant row is prefixed by the `rail.yaml`
value it applies to, so nothing in `AGENTS.md` depends on answers that freeze at scaffold time.
The tests render every stack × tier × ledger combination, plus both target families at `prod`,
once per module. They check the rendered guidance and never the raw Jinja. The one exception is
the stack-chain marker test, which reads the template source by design.

**Tech Stack:** Python 3.12, Click, copier 9 (Jinja2), PyYAML, pytest, ruff.

**Spec:** `docs/specs/2026-09-24-template-alignment.md`. Read it whole first. This plan cites its
decisions (D1–D16) and success criteria (C1–C11) by number. Its "Order of work inside the pull
request" is the task order: spec step N is Task N, and step 9 closes Task 8 and the section after
it.

Work in the worktree `.claude/worktrees/template-alignment`, branch
`feat/hawixs/template-alignment`, from its root. Run `env -u VIRTUAL_ENV uv sync --all-extras`
once before Task 1: the worktree's environment was synced without the `brain` extra, and
`rail check` fails four ledger gates without it.

## Global Constraints

- Python 3.12+, ruff `line-length = 100`, lint `select = ["E", "F", "I", "UP", "B"]`. Every task ends with `env -u VIRTUAL_ENV uv run ruff format src/ tests/`, then `env -u VIRTUAL_ENV uv run ruff check src/ tests/` and `env -u VIRTUAL_ENV uv run ruff format --check src/ tests/`, all clean. When `ruff check` reports only an import order (I001), run `env -u VIRTUAL_ENV uv run ruff check --fix src/ tests/` and read the diff.
- Always run uv as `env -u VIRTUAL_ENV uv …`, including `env -u VIRTUAL_ENV make ci`: the host exports another project's virtual environment.
- A gate never raises. Every read error on the roster walk is caught and turned into a result.
- Tests never read the developer's host files: no `~/.claude/skills`, no ReD root, no `sites.yaml`. `tests/conftest.py` already points `RAIL_SITES_FILE` into `tmp_path`. The roster walk stats `CLAUDE.md` in the ancestors of `tmp_path` (`/tmp`, `/`). No such file exists on a sane host: a test that reports `listed in tmp/CLAUDE.md` has found a stray file, not a bug.
- No machine address in any tracked file, this plan included, outside RFC 5737 (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) and RFC 3849 (`2001:db8::/32`). `tests/test_no_machine_address.py` enforces it. A test that needs a foreign address builds it at run time from its parts.
- No machine name, deploy account or ssh alias anywhere in the repository: not in code, tests, docs, commits or the pull request. The host verification reads them from the ReD root at run time and reports only a count.
- Commits are conventional, in English, with a leading emoji. A `git commit  # <subject>` line in a step gives the subject. Write the message with `/git-commit`, ending with the two trailer lines of the session.
- `env -u VIRTUAL_ENV make ci` is green at the end (Task 8), and its summary line is read, never inferred.
- Never use `git stash`. To undo a temporary edit made to prove a test bites, use `git checkout -- <file>`, which only restores that file.
- Out of scope, per the spec's non-goals: `rail-ci.yml`, `src/rail/gates/build.py`, `Makefile.jinja`, `.gitignore.jinja`, `.claude/settings.json.jinja`, the ReD root, any `rust` stack, `rail bind`, and any upgrade of an already-generated repository.

## Review Focus

These five inputs are not exercised by any spec criterion, and are the most likely to hurt a
user. Each one gets its test in the task that owns the code.

1. **`rail check` with no `--repo`, from inside a nested worktree.** The gate is handed `.`.
   This is the default invocation, and cdb725e4 is about exactly this case. A person expects the
   walk to reach the root. Task 3 tests it in
   `test_roster_entry_walks_up_from_the_current_directory`.
2. **A root `CLAUDE.md` saved with CRLF line endings, or with its header written without spaces
   around the pipes** (`|Project|Domain|What it is|Brain key|`). A person expects this to still
   be the roster. Task 3 tests it in `test_roster_entry_reads_a_crlf_or_compact_header`.
3. **A `--description` containing a `|`.** Pasted as is, the printed row would have five cells
   and the root's table would shift. A person expects a row as wide as the header, with the pipe
   escaped. Task 4 tests it in
   `test_a_pipe_in_the_description_keeps_the_row_as_wide_as_the_header`.
4. **A brain key with an underscore**, shaped like `auto_discord`, which the root already holds.
   A person expects it to be accepted by the CLI and by copier, and its rendered session-start
   call to match D8's regex. Task 4 tests it through the row test's `--brain-key red_probe`.
   Tasks 5 and 8 test it through the `red_probe` combination in `COMBOS`.
5. **A `projects` directory nested inside another `projects` tree**
   (`~/projects/<ReD>/projects/<x>`). A person expects the ReD position to be the parent of the
   *nearest* `projects`, and the standalone message to name that directory. Task 3 tests it in
   `test_roster_entry_takes_the_nearest_projects_ancestor`.

## Pre-flight: what each task produces and who consumes it

Every task pair that shares a file or an interface is listed here. Each row was checked against
the code in this plan.

| Producer → consumer | File or interface | What must agree |
|---|---|---|
| Task 3 → Task 4 | `rail.gates.hygiene`: `ROSTER_HEADER`, `DOMAIN_PLACEHOLDER`, `table_row(cells: Iterable[str]) -> str`, `table_cells(line: str) -> tuple[str, ...] \| None` | Task 4 imports exactly these names, and adds `roster_row(project, description, brain_key) -> str` beside them. `roster_row` builds its cells through the same dict-over-`ROSTER_HEADER` pattern as the fixture. |
| Task 3 → existing tests | `tests.helpers.write_roster(root, names, *, domain="Infra")` | Existing callers pass `(root, names)` positionally: `test_cli.py`, `test_audit.py`, `test_metrics.py`, `test_gates.py`. The new keyword has a default. `tests/golden/audit-matrix.json` must stay byte-identical (C2). |
| Task 3 → Task 8 | the gate's PASS message `listed in <parent>/CLAUDE.md` (`_short`) | C9 reads `listed in ReD_v1/CLAUDE.md` from `rail check` in this worktree. |
| Task 4 → Tasks 5, 8 | `rail.commands.new.BRAIN_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")`, and the same string in `copier.yml`'s `regex_search` | Task 8 builds `SESSION_START` from `BRAIN_KEY.pattern`. Task 5's `red_probe` combination renders only because the copier validator accepts `_`. |
| Tasks 1, 2, 4, 5, 6, 7, 8 | `tests/test_scaffold.py` imports | Task 4 adds `BRAIN_KEY` and the hygiene names. Task 5 adds `re`, `NamedTuple` and `DeployTarget`, and removes the local `DeployTarget` import it would duplicate. Task 7 adds `tests.addresses._foreign`. The final import block is shown in Task 7, Step 2. |
| Task 5 → Tasks 6, 7, 8 | `Combo`, `COMBOS`, the module-scoped `renders` fixture, `ROOT_TITLES`, `STACK_LINE`, `STACK_CHAIN`, `STACK_CHAINS` | Task 6 replaces `test_rendered_guidance_points_at_the_root` as a whole, and adds `"AGENTS.md.jinja": {"gates"}` to `STACK_CHAINS`. Tasks 7 and 8 parametrise over `COMBOS` and read `renders`. |
| Task 5 → Task 8 | the rendered call `brain_session_start("<key>", client_key="<harness>-<key>-<YYYY-MM-DD>")` in `CLAUDE.md.jinja` | Byte-identical to what `SESSION_START` matches. Task 5 pins the literal. Task 8 pins the form. |
| Task 6 → Tasks 7, 8 | `GUIDANCE = ("CLAUDE.md", "AGENTS.md")`, and the text of `AGENTS.md.jinja` | No backticked kebab-case token, and the `<harness>` values not in backticks. So Task 7's `_cited_skills` returns the empty set on every render. |
| Task 6 → Task 8 | the phrase `names as "Parent project"` | Present in `AGENTS.md.jinja` (Task 6) and in red-rail's `AGENTS.md` (Task 8). The same needle is used in both tests. |
| Task 7 → Task 8 | `STALE` | Task 8's dogfood test asserts that no stale name remains in red-rail's own two files. |
| Task 7 (internal) | `tests/addresses.py`: `ALLOWED`, `_candidates`, `_foreign` | Moved verbatim. `test_no_machine_address.py` and `test_scaffold.py` import `_foreign` from it: one policy. |
| Task 2 ↔ Task 7 | the workflow template's new comment | It carries no address and no `@main`: `test_render_pins_the_reusable_workflow_to_a_resolved_sha` asserts `"@main" not in workflow`. |

## Coverage

| Spec item | Task |
|---|---|
| D12, C4 | 1 |
| D1, C1 | 2 |
| D2, D3, D4, C2 | 3 |
| D5, D8 (validation), C3 | 4 |
| D6, D7, D8 (`CLAUDE.md`), D11 (`CLAUDE.md` chains), C5, C6 | 5 |
| D9, D10, D8 (`AGENTS.md`), D11 (`AGENTS.md` chain), C5, C6 | 6 |
| D14, D15, C8 | 7 |
| D13, D8 (regex, red-rail's files), C7, C9 | 8 |
| D16, C10, C11 | After the last task |

---

### Task 1: `Stack` and copier's stack choices stay equal

Spec step 1 (D12). This is a guard, not a fix: it is green at once, and Step 3 proves it bites.

**Files:**
- Modify: `tests/test_scaffold.py` (one test, right after `test_every_deploy_target_the_cli_offers_is_a_copier_choice`)

**Interfaces:**
- Consumes: `rail.model.Stack`; `copier.yml` → `stack.choices`.
- Produces: the guard spec B must satisfy when it adds `rust`.

- [ ] **Step 1: Write the guard test**

Insert after `test_every_deploy_target_the_cli_offers_is_a_copier_choice`:

```python
def test_every_stack_the_cli_offers_is_a_copier_choice() -> None:
    """`rail new --stack` offers every `Stack`, and copier must accept exactly those: today a
    drift fails only when `rail new --stack X` runs, inside copier. Spec B adds `rust` to both
    or this fails (spec 2026-09-24-template-alignment, decision 12)."""
    import yaml

    questions = yaml.safe_load((ROOT / "copier.yml").read_text())
    assert set(questions["stack"]["choices"]) == {s.value for s in Stack}
```

- [ ] **Step 2: Run it**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py -k every_stack_the_cli_offers`
Expected: PASS (1 passed). This is spec order step 1: a guard, green at once.

- [ ] **Step 3: Prove the guard bites, then restore `copier.yml`**

```bash
sed -i 's/^  choices: \[python, go, docs\]$/  choices: [python, go, docs, rust]/' copier.yml
env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py -k every_stack_the_cli_offers
git checkout -- copier.yml
git diff --exit-code copier.yml
```

Expected: the pytest run FAILS with an `AssertionError`, and the set difference names `'rust'`.
The final `git diff --exit-code` exits 0: `copier.yml` is back to its committed content.

- [ ] **Step 4: Lint, format and run the suite**

Run the three ruff commands of the Global Constraints, then `env -u VIRTUAL_ENV uv run pytest -q`.
Expected: ruff clean, and every test passes. Read the summary line.

- [ ] **Step 5: Commit**

```bash
git add tests/test_scaffold.py
git commit  # ✅ test(scaffold): the Stack enum and copier's stack choices stay equal
```

---

### Task 2: The template CI forwards its pin to `rail-ref`

Spec step 2 (D1, C1, ticket 2a6781cb).

**Files:**
- Modify: `template/project/.github/workflows/continuous-integration.yml.jinja`
- Test: `tests/test_scaffold.py` (one parametrised test, after `test_render_without_a_pin_still_calls_main`)

**Interfaces:**
- Consumes: `NewProject.rail_ref` → the copier answer `rail_ref`; `rail-ci.yml`'s existing input `rail-ref` (unchanged, per the non-goals).
- Produces: every rendered workflow passes `with: rail-ref: "<rail_ref>"` as a quoted string.

- [ ] **Step 1: Write the failing test**

Insert after `test_render_without_a_pin_still_calls_main`:

```python
@pytest.mark.parametrize(
    "ref",
    ["0123456789abcdef0123456789abcdef01234567", "1234567890" * 4, "main"],
    ids=["hex-sha", "all-digit-sha", "main"],
)
def test_render_forwards_the_pin_to_rail_ref(template_dir: Path, tmp_path: Path, ref: str) -> None:
    """The workflow called at `ref` must install the rail at `ref` too, or the gates float on
    rail-ci's `main` default while the workflow reads as pinned (ticket 2a6781cb). Read as
    GitHub reads it, parsed: an unquoted all-digit SHA would be a number, not a ref."""
    import yaml

    dest = render(_project(template_dir, tmp_path / "red-beta", slug="red-beta", rail_ref=ref))
    workflow = yaml.safe_load(
        (dest / ".github" / "workflows" / "continuous-integration.yml").read_text()
    )
    job = workflow["jobs"]["rail"]
    assert job["uses"].endswith(f"@{ref}")
    assert job["with"]["rail-ref"] == ref and isinstance(job["with"]["rail-ref"], str)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py -k forwards_the_pin`
Expected: FAIL, three times, with `KeyError: 'rail-ref'`.

- [ ] **Step 3: Forward the pin in the template**

In `template/project/.github/workflows/continuous-integration.yml.jinja`, replace the `jobs:`
block, from `jobs:` down to `      stack: {{ stack }}`, with:

```yaml
jobs:
  rail:
    # The reusable rail workflow: the project's `make ci`, then `rail check --ci --json`.
    # One pin for both: the workflow is called at it and installs the rail's code at it, so
    # the gates guarding this repository move only with a commit here. Quoted: an all-digit
    # SHA would otherwise be read as a number.
    uses: hawkixs/red-rail/.github/workflows/rail-ci.yml@{{ rail_ref }}
    with:
      stack: {{ stack }}
      rail-ref: "{{ rail_ref }}"
```

Leave the four comment lines about `secrets: inherit` below it untouched.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py`
Expected: PASS. `test_render_pins_the_reusable_workflow_to_a_resolved_sha`,
`test_render_without_a_pin_still_calls_main` and
`test_upgrade_rebumps_the_pin_so_the_change_is_a_reviewable_line` pass unchanged. The upgrade
contract is still `seen["data"] == {"rail_ref": new}`: forwarding is a property of the render.

- [ ] **Step 5: Lint, format and run the suite**

Run the three ruff commands, then `env -u VIRTUAL_ENV uv run pytest -q`.
Expected: clean, and every test passes.

- [ ] **Step 6: Commit**

```bash
git add template/project/.github/workflows/continuous-integration.yml.jinja tests/test_scaffold.py
git commit  # 🐛 fix(template): the generated CI installs the rail at the ref it pins
```

---

### Task 3: The roster gate finds the root by its identity header, from any depth

Spec step 3 (D2, D3, D4, C2, ticket cdb725e4). The fixtures move to the constants first, then the
new tests, then the gate.

**Files:**
- Modify: `src/rail/gates/hygiene.py` (module docstring, constants, `find_roster`, `roster_entry`, new helpers)
- Modify: `tests/helpers.py` (`write_roster`)
- Modify: `tests/test_helpers.py` (`test_write_roster_lists_projects`)
- Test: `tests/test_gates_hygiene.py`

**Interfaces:**
- Produces, in `rail.gates.hygiene`:
  - `ROSTER_HEADER: tuple[str, ...] = ("Project", "Domain", "What it is", "Brain key")`;
  - `DOMAIN_PLACEHOLDER = "<domain>"`, `PROJECTS_DIR = "projects"`;
  - `table_row(cells: Iterable[str]) -> str`: escapes `|` inside a cell;
  - `table_cells(line: str) -> tuple[str, ...] | None`: splits on unescaped pipes;
  - `roster_header() -> str`: the header line, then its separator line;
  - `roster_rows(text: str) -> list[tuple[str, ...]] | None`;
  - `RosterSearch` (frozen dataclass), `find_roster(repo: Path) -> RosterSearch`. This replaces the old `-> Path | None`; the only caller is `roster_entry`.
- Produces, in `tests.helpers`: `write_roster(root: Path, names: list[str], *, domain: str = "Infra") -> None`.
- Removes: `ROSTER_MARKER`.

- [ ] **Step 1: Move the fixtures to the constants**

In `tests/helpers.py`, add this import block after `from pathlib import Path`, separated by
one blank line:

```python
from rail.gates.hygiene import ROSTER_HEADER, roster_header, table_row
```

Replace `write_roster` with:

```python
def write_roster(root: Path, names: list[str], *, domain: str = "Infra") -> None:
    """The ReD root `CLAUDE.md`: its identity table, built from the gate's own constants, so
    the fixture cannot drift from what the gate recognises (the French header it used to write
    hid cdb725e4)."""
    rows = []
    for name in names:
        cells = {
            "Project": name,
            "Domain": domain,
            "What it is": "fixture",
            "Brain key": f"`{name}`",
        }
        rows.append(table_row(cells[column] for column in ROSTER_HEADER))
    (root / "CLAUDE.md").write_text("# ReD\n\n" + roster_header() + "\n" + "\n".join(rows) + "\n")
```

In `tests/test_helpers.py`, add `from rail.gates.hygiene import ROSTER_HEADER, table_row` after
`from rail import gitrepo`. Then replace `test_write_roster_lists_projects` with:

```python
def test_write_roster_lists_projects(tmp_path: Path) -> None:
    write_roster(tmp_path, ["red-alpha", "red-beta"])
    text = (tmp_path / "CLAUDE.md").read_text()
    assert table_row(ROSTER_HEADER) in text and "| red-beta |" in text
```

- [ ] **Step 2: Write the failing gate tests**

In `tests/test_gates_hygiene.py`, add `DOMAIN_PLACEHOLDER` at the head of the
`from rail.gates.hygiene import (…)` list, before `GATES`. Replace
`test_roster_entry_is_standalone_without_a_roster` with the version below, keep
`test_roster_entry_reads_the_red_root` and `test_roster_entry_folds_dotdot_paths_to_the_root`
as they are, and add the other tests right after them:

```python
def test_roster_entry_is_standalone_without_a_roster(tmp_path: Path) -> None:
    """Standalone always says why (decision 4): no ReD root above `projects/`, or no
    `projects/` at all."""
    result = roster_entry(conforming_tree(tmp_path, "red-alpha", "bootstrap"))
    assert result.passed and result.details == f"standalone: no CLAUDE.md in {tmp_path.name}"
    lonely = init_repo(tmp_path / "elsewhere" / "red-lonely", remotes=False)
    result = roster_entry(lonely)
    assert result.passed and result.details == "standalone: no `projects` ancestor"


def _nested_worktree(repo: Path, nested: str) -> Path:
    """A checkout nested inside the project, carrying its own manifest as a worktree does."""
    worktree = repo / nested
    worktree.mkdir(parents=True)
    write_manifest(worktree, project=repo.name, tier="bootstrap")
    return worktree


@pytest.mark.parametrize("nested", [".claude/worktrees/w", ".worktrees/w"])
def test_roster_entry_finds_the_root_from_a_nested_worktree(tmp_path: Path, nested: str) -> None:
    """cdb725e4: two levels up from `projects/x/.claude/worktrees/w` is `projects/x/.claude`,
    so every worktree run of the gate reported standalone. The walk goes up to the root."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    worktree = _nested_worktree(repo, nested)
    write_roster(tmp_path, ["red-alpha"])
    result = roster_entry(worktree)
    assert result.passed and result.details == f"listed in {tmp_path.name}/CLAUDE.md"


def test_roster_entry_walks_up_from_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`rail check` with no `--repo` hands the gate `.`: the default invocation, from a nested
    worktree, reaches the root (review focus 1; success criterion 9 in miniature)."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    worktree = _nested_worktree(repo, ".claude/worktrees/w")
    write_roster(tmp_path, ["red-alpha"])
    monkeypatch.chdir(worktree)
    result = roster_entry(Path("."))
    assert result.passed and result.details == f"listed in {tmp_path.name}/CLAUDE.md"


def test_roster_entry_ignores_the_related_projects_table(tmp_path: Path) -> None:
    """A row counts only inside the identity table's block: a project named in a "Related
    projects" table further down is not in the roster (decision 2)."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-other"])
    with (tmp_path / "CLAUDE.md").open("a") as root:
        root.write(
            "\n## Related projects\n\n| Project | Path | Relation |\n|---|---|---|\n"
            "| red-alpha | `projects/red-alpha` | a sibling |\n"
        )
    result = roster_entry(repo)
    assert not result.passed
    assert result.details == f"red-alpha has no row in {tmp_path.name}/CLAUDE.md"


def test_roster_entry_fails_when_the_root_header_drifted(tmp_path: Path) -> None:
    """A `CLAUDE.md` above `projects/` without the identity header is a root whose format
    moved (the French header, here): a FAIL that names it, never a silent standalone."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (tmp_path / "CLAUDE.md").write_text(
        "# ReD\n\n| Projet | Domaine | Statut reel | Sante | Cle brain |\n|---|---|---|---|---|\n"
        "| red-alpha | Infra | fixture | OK | `red-alpha` |\n"
    )
    result = roster_entry(repo)
    assert not result.passed
    assert result.details == (
        f"roster header not found in {tmp_path.name}/CLAUDE.md: the root format drifted"
    )


def test_roster_entry_fails_on_a_row_still_holding_the_domain_placeholder(
    tmp_path: Path,
) -> None:
    """The row `rail new` prints was pasted unedited: the domain is the operator's call."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"], domain=DOMAIN_PLACEHOLDER)
    result = roster_entry(repo)
    assert not result.passed
    assert result.details == (
        f"red-alpha's row in {tmp_path.name}/CLAUDE.md still holds `<domain>`: fill in its domain"
    )


@pytest.mark.parametrize("broken", ["undecodable", "unreadable"])
def test_roster_entry_fails_closed_on_an_unreadable_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    """The ReD position's `CLAUDE.md` cannot be read: the roster cannot be checked, so the
    gate fails and names the file. It never raises (decision 4)."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    root_md = tmp_path / "CLAUDE.md"
    if broken == "undecodable":
        root_md.write_bytes(b"# ReD\n\xff\xfe\n")
    else:
        write_roster(tmp_path, ["red-alpha"])
        read_text = Path.read_text

        def refuse(self: Path, *args: object, **kwargs: object) -> str:
            if self == root_md:
                raise PermissionError(13, "Permission denied")
            return read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", refuse)
    result = roster_entry(repo)
    assert not result.passed
    assert result.details.startswith(f"cannot read {tmp_path.name}/CLAUDE.md (")
    assert result.details.endswith("): the roster cannot be checked")


def test_roster_entry_skips_an_unreadable_unrelated_ancestor(tmp_path: Path) -> None:
    """Only the ReD position may fail the gate on a read error. The sub-project's own
    `CLAUDE.md`, met on the way up from a nested worktree, is passed over."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    worktree = _nested_worktree(repo, ".claude/worktrees/w")
    (repo / "CLAUDE.md").write_bytes(b"\xff\xfe")
    write_roster(tmp_path, ["red-alpha"])
    result = roster_entry(worktree)
    assert result.passed and result.details == f"listed in {tmp_path.name}/CLAUDE.md"


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_roster_entry_reads_a_crlf_or_compact_header(tmp_path: Path, newline: str) -> None:
    """Review focus 2: the header is recognised by its cells, not by its spacing, and a
    CRLF file is still read line by line."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    text = (
        "# ReD\n\n|Project|Domain|What it is|Brain key|\n|:---|---|---|---:|\n"
        "|red-alpha|Infra|fixture|`red-alpha`|\n"
    )
    (tmp_path / "CLAUDE.md").write_bytes(text.replace("\n", newline).encode())
    result = roster_entry(repo)
    assert result.passed and result.details == f"listed in {tmp_path.name}/CLAUDE.md"


def test_roster_entry_takes_the_nearest_projects_ancestor(tmp_path: Path) -> None:
    """Review focus 5: under `<x>/projects/outer/projects/red-alpha`, the ReD position is
    `outer`, the parent of the nearest `projects`, and the standalone message names it."""
    outer = tmp_path / "projects" / "outer"
    repo = conforming_tree(outer, "red-alpha", "bootstrap")
    result = roster_entry(repo)
    assert result.passed and result.details == "standalone: no CLAUDE.md in outer"
    write_roster(outer, ["red-alpha"])
    assert roster_entry(repo).details == "listed in outer/CLAUDE.md"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_helpers.py tests/test_gates_hygiene.py`
Expected: FAIL at collection: `ImportError: cannot import name 'ROSTER_HEADER' from
'rail.gates.hygiene'`. The fixtures now depend on constants the gate does not have yet.

- [ ] **Step 4: Implement the constants, the table helpers and the walk**

In `src/rail/gates/hygiene.py`, replace the module docstring with:

```python
"""Hygiene gates: the floor every tier stands on, starting with `bootstrap`.

`remotes` and `roster_entry` are workstation-scoped: they read the operator's clone (the GitHub
remote — a mirror only where the manifest declares one — and the ReD root roster, found by
walking up to the first `CLAUDE.md` holding its identity header) and are reported as skipped
under `--ci`.
"""
```

Add `from collections.abc import Iterable` and `from dataclasses import dataclass` to the
standard-library imports, in isort order. Replace the two lines
`DOCS_DIRS = …` and `ROSTER_MARKER = "| Projet |"` with:

```python
DOCS_DIRS = ("docs/specs", "docs/plans", "docs/adr")
# The ReD root's identity table is recognised by its whole header: a bare `| Project |` prefix
# also opens every "Related projects" table (spec 2026-09-24-template-alignment, decision 2).
ROSTER_HEADER = ("Project", "Domain", "What it is", "Brain key")
DOMAIN_PLACEHOLDER = "<domain>"  # the cell `rail new` leaves to the operator's classification
PROJECTS_DIR = "projects"  # a ReD root keeps its sub-projects here
_DOMAIN = ROSTER_HEADER.index("Domain")
_SEPARATOR = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+$")
_CELL_BOUNDARY = re.compile(r"(?<!\\)\|")
```

Right after `_short`, add:

```python
def table_row(cells: Iterable[str]) -> str:
    """One Markdown table line; a `|` inside a cell is escaped, so the row keeps its width."""
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"


def table_cells(line: str) -> tuple[str, ...] | None:
    """The stripped cells of a Markdown table line, or None when the line is not one. An
    escaped `\\|` stays inside its cell."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    inner = stripped[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return tuple(cell.strip() for cell in _CELL_BOUNDARY.split(inner))


def roster_header() -> str:
    """The roster's header line and its separator, as the ReD root writes them."""
    return table_row(ROSTER_HEADER) + "\n|" + "---|" * len(ROSTER_HEADER)


def roster_rows(text: str) -> list[tuple[str, ...]] | None:
    """The rows of the identity table in `text`: the header, its separator, then consecutive
    table lines. None when no header line is followed by a separator."""
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        separator = _SEPARATOR.match(lines[index + 1].strip())
        if table_cells(line) != ROSTER_HEADER or not separator:
            continue
        rows: list[tuple[str, ...]] = []
        for row in lines[index + 2 :]:
            cells = table_cells(row)
            if cells is None:
                break
            rows.append(cells)
        return rows
    return None
```

Replace `find_roster` with:

```python
@dataclass(frozen=True, slots=True)
class RosterSearch:
    """What the walk up from a repository met (decisions 3 and 4)."""

    roster: Path | None = None  # the first CLAUDE.md holding the identity header
    rows: tuple[tuple[str, ...], ...] = ()  # the rows of its identity table
    red_root: Path | None = None  # the ReD position: parent of the nearest `projects` ancestor
    red_claude_md: bool = False  # a CLAUDE.md sits at the ReD position
    unreadable: str | None = None  # why the ReD position's CLAUDE.md could not be read


def _text(path: Path) -> str | None:
    """`path` as UTF-8 text, or None when there is no such file. Raises OSError (a stat or a
    read refused) or UnicodeDecodeError on a file that is there and cannot be read."""
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def find_roster(repo: Path) -> RosterSearch:
    """Walk up from `repo` to the first `CLAUDE.md` holding the identity header.

    The walk is lexical: `normpath` folds `..` (`rail audit ..` hands us `<repo>/../<x>`) and
    no symlink is resolved. It covers `projects/x`, a worktree nested in it
    (`projects/x/.claude/worktrees/w`, `projects/x/.worktrees/w`) and an audit from a
    sibling, without git. Only the ReD position — the parent of the nearest `projects`
    ancestor — may fail the gate on a read error; any other unreadable `CLAUDE.md` is passed
    over, so an unrelated ancestor never fails a repository."""
    start = Path(os.path.normpath(repo.absolute()))
    red_root: Path | None = None
    red_claude_md = False
    below: Path | None = None  # the repository itself is not an ancestor
    for ancestor in start.parents:
        red_position = red_root is None and below is not None and below.name == PROJECTS_DIR
        below = ancestor
        if red_position:
            red_root = ancestor
        candidate = ancestor / "CLAUDE.md"
        try:
            text = _text(candidate)
        except (OSError, UnicodeDecodeError) as exc:
            if red_position:
                return RosterSearch(
                    red_root=ancestor,
                    unreadable=f"cannot read {_short(candidate)} ({type(exc).__name__})",
                )
            continue
        if red_position:
            red_claude_md = text is not None
        rows = roster_rows(text) if text is not None else None
        if rows is not None:
            return RosterSearch(roster=candidate, rows=tuple(rows), red_root=red_root)
    return RosterSearch(red_root=red_root, red_claude_md=red_claude_md)
```

Replace `roster_entry` with:

```python
def roster_entry(repo: Path) -> GateResult:
    """Three outcomes (decision 4). The roster is found: the row is judged. No roster, but a
    `CLAUDE.md` sits at the ReD position: its header drifted or it cannot be read, a FAIL.
    Otherwise the repository is standalone, and the message says why."""

    def result(passed: bool, details: str) -> GateResult:
        return GateResult(Stage.HYGIENE, "roster_entry", passed, details)

    search = find_roster(repo)
    if search.unreadable is not None:
        return result(False, f"{search.unreadable}: the roster cannot be checked")
    if search.roster is None:
        if search.red_root is None:
            return result(True, f"standalone: no `{PROJECTS_DIR}` ancestor")
        if not search.red_claude_md:
            return result(True, f"standalone: no CLAUDE.md in {search.red_root.name}")
        drifted = _short(search.red_root / "CLAUDE.md")
        return result(False, f"roster header not found in {drifted}: the root format drifted")
    name = _project_name(repo)
    where = _short(search.roster)
    row = next((cells for cells in search.rows if cells[0] == name), None)
    if row is None:
        return result(False, f"{name} has no row in {where}")
    if len(row) > _DOMAIN and row[_DOMAIN] == DOMAIN_PLACEHOLDER:
        return result(
            False,
            f"{name}'s row in {where} still holds `{DOMAIN_PLACEHOLDER}`: fill in its domain",
        )
    return result(True, f"listed in {where}")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_helpers.py tests/test_gates_hygiene.py tests/test_cli.py tests/test_audit.py tests/test_metrics.py tests/test_gates.py`
Expected: PASS. Then run `git diff --exit-code tests/golden/audit-matrix.json`. Expected: exit
code 0, the golden is unchanged (C2).

- [ ] **Step 6: See cdb725e4 fixed live**

Run: `env -u VIRTUAL_ENV uv run rail check hygiene`
Expected: the line `PASS  hygiene.roster_entry   listed in ReD_v1/CLAUDE.md`, not
`standalone`. Only the roster line is judged here: `hygiene.mirrors` depends on the brain
ledger, and its state is Task 8's concern.

- [ ] **Step 7: Lint, format and run the suite**

Run the three ruff commands, then `env -u VIRTUAL_ENV uv run pytest -q`.
Expected: clean, and every test passes.

- [ ] **Step 8: Commit**

```bash
git add src/rail/gates/hygiene.py tests/helpers.py tests/test_helpers.py tests/test_gates_hygiene.py
git commit  # 🐛 fix(hygiene): the roster gate finds the ReD root by its identity header, from any depth
```

---

### Task 4: `rail new` prints a four-cell row, and refuses a brain key outside the pattern

Spec step 4 (D5, and D8's key validation; C3).

**Files:**
- Modify: `src/rail/gates/hygiene.py` (`roster_row`)
- Modify: `src/rail/commands/new.py`
- Modify: `copier.yml` (`brain_key.validator`)
- Test: `tests/test_scaffold.py` (three tests, after `test_cli_new_and_upgrade`)

**Interfaces:**
- Consumes: `ROSTER_HEADER`, `DOMAIN_PLACEHOLDER`, `table_row`, `table_cells` (Task 3).
- Produces:
  - `rail.gates.hygiene.roster_row(project: str, description: str, brain_key: str) -> str`;
  - `rail.commands.new.BRAIN_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")`, matched with `fullmatch`;
  - the copier validator with the same pattern string. Task 8 builds its regex from `BRAIN_KEY.pattern`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_scaffold.py`, add these imports in isort order within the `rail` block:

```python
from rail.commands.new import BRAIN_KEY
from rail.gates.hygiene import DOMAIN_PLACEHOLDER, ROSTER_HEADER, roster_row, table_cells
```

Then insert after `test_cli_new_and_upgrade`:

```python
def test_cli_new_prints_a_roster_row_shaped_like_the_header(
    template_dir: Path, tmp_path: Path
) -> None:
    """The row fits the root's four-column table: slug first, the domain left to the operator,
    the brain key last. The output says what fails until the row is in (decision 5)."""
    out = CliRunner().invoke(
        main,
        [
            "new",
            "red-probe",
            "--description",
            "A disposable HTTP probe.",
            "--brain-key",
            "red_probe",
            "--dest",
            str(tmp_path / "red-probe"),
            "--template",
            str(template_dir),
            "--rail-ref",
            FIXTURE_PIN,
            "--no-remotes",
        ],
    )
    assert out.exit_code == 0, out.output
    row = next(line for line in out.output.splitlines() if line.startswith("| red-probe |"))
    cells = table_cells(row)
    assert cells is not None and len(cells) == len(ROSTER_HEADER)
    assert cells[:2] == ("red-probe", DOMAIN_PLACEHOLDER)
    assert cells[-1] == "`red_probe`"
    assert "`rail check` fails hygiene.roster_entry" in out.output


def test_a_pipe_in_the_description_keeps_the_row_as_wide_as_the_header() -> None:
    """Review focus 3: a `|` in the description is escaped, so the pasted row does not
    shift the root's columns, and the gate reads it back as one cell."""
    cells = table_cells(roster_row("red-probe", "reads a | b", "red-probe"))
    assert cells is not None and len(cells) == len(ROSTER_HEADER)
    assert cells[2] == "reads a \\| b"


@pytest.mark.parametrize("key", ["Red-Probe", "red.probe"], ids=["uppercase", "dotted"])
def test_new_refuses_a_brain_key_outside_the_pattern(
    template_dir: Path, tmp_path: Path, key: str
) -> None:
    """The key is rendered into `brain_session_start("<key>", …)` and checked by one regex
    (decision 8): the CLI callback and the copier validator refuse the same keys, and use
    the same pattern."""
    out = CliRunner().invoke(
        main,
        [
            "new",
            "red-probe",
            "--description",
            "x.",
            "--brain-key",
            key,
            "--no-remotes",
            "--dest",
            str(tmp_path / "cli"),
        ],
    )
    assert out.exit_code == 2 and "[a-z0-9][a-z0-9_-]*" in out.output
    assert not (tmp_path / "cli").exists()
    with pytest.raises(ScaffoldError, match="brain_key"):
        render(_project(template_dir, tmp_path / "copier", brain_key=key))
    assert BRAIN_KEY.pattern in (ROOT / "copier.yml").read_text()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py`
Expected: FAIL at collection: `ImportError: cannot import name 'BRAIN_KEY' from
'rail.commands.new'`.

- [ ] **Step 3: Add `roster_row` to `src/rail/gates/hygiene.py`**

Right after `roster_header`, add:

```python
def roster_row(project: str, description: str, brain_key: str) -> str:
    """The row `rail new` prints for the root: one cell per header column, in the header's
    order, the domain left to the operator (decision 5)."""
    cells = {
        "Project": project,
        "Domain": DOMAIN_PLACEHOLDER,
        "What it is": description,
        "Brain key": f"`{brain_key}`",
    }
    return table_row(cells[column] for column in ROSTER_HEADER)
```

- [ ] **Step 4: Validate the key and print the row in `src/rail/commands/new.py`**

Add `from rail.gates.hygiene import DOMAIN_PLACEHOLDER, roster_row` to the imports, before
`from rail.ledger import LedgerError`. Below `SLUG = …`, add:

```python
# Every key of the root roster fits, `auto_discord` included. It is rendered into
# `brain_session_start("<key>", …)` and checked there by one regex (decision 8).
BRAIN_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
```

Below `_slug`, add:

```python
def _brain_key(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    if value is not None and not BRAIN_KEY.fullmatch(value):
        raise click.BadParameter(
            "must match [a-z0-9][a-z0-9_-]*: lowercase letters, digits, '-' and '_'"
        )
    return value
```

Replace the `--brain-key` option with:

```python
@click.option(
    "--brain-key",
    default=None,
    callback=_brain_key,
    help="brain-v42 project key (default: the slug).",
)
```

Replace the last five lines of `command`, from `click.echo("")` to the end, with:

```python
    click.echo("")
    click.echo(
        "Add this row to the ReD root roster (CLAUDE.md, the operator's gesture: the root is "
        f"not under git), with {DOMAIN_PLACEHOLDER} replaced by the project's domain:"
    )
    click.echo(roster_row(slug, description, project.brain_key))
    click.echo(
        "Until the row is in the root with its domain filled in, `rail check` fails "
        "hygiene.roster_entry. `rail new` cannot see the root: it checked this tree with the "
        "workstation gates skipped."
    )
```

- [ ] **Step 5: Validate the key in `copier.yml`**

Replace the `brain_key` question with:

```yaml
brain_key:
  type: str
  default: "{{ project }}"
  help: brain-v42 project key (group red)
  validator: >-
    {% if not (brain_key | regex_search('^[a-z0-9][a-z0-9_-]*$')) %}
    brain_key must match ^[a-z0-9][a-z0-9_-]*$ (lowercase letters, digits, '-' and '_')
    {% endif %}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py`
Expected: PASS. `test_cli_new_and_upgrade` still finds `| red-probe |` in the output.

- [ ] **Step 7: Lint, format and run the suite**

Run the three ruff commands, then `env -u VIRTUAL_ENV uv run pytest -q`.
Expected: clean, and every test passes.

- [ ] **Step 8: Commit**

```bash
git add src/rail/gates/hygiene.py src/rail/commands/new.py copier.yml tests/test_scaffold.py
git commit  # ✨ feat(new): a roster row shaped like the root's header, and a validated brain key
```

---

### Task 5: The template's `CLAUDE.md` points at the root and says where things live

Spec step 5 (D6, D7, D8 for `CLAUDE.md`, D11 for its two chains; C5 and C6 for `CLAUDE.md`).

**Files:**
- Modify: `template/project/CLAUDE.md.jinja` (whole file below)
- Test: `tests/test_scaffold.py` (the render machinery, two tests; the local `DeployTarget` import removed)

**Interfaces:**
- Consumes: `BRAIN_KEY` (Task 4): the `red_probe` combination renders only because it is accepted.
- Produces, in `tests/test_scaffold.py`:
  - `class Combo(NamedTuple)` with `stack`, `tier`, `ledger`, `target: str | None = None`, `brain_key: str = "red-probe"`, and `label -> str`;
  - `COMBOS: list[Combo]`, 25 entries: 3 stacks × 2 tiers × 2 ledgers, plus 3 × 2 × 2 targets at `prod`, plus the `red_probe` key;
  - the module-scoped fixture `renders(tmp_path_factory) -> dict[Combo, Path]`;
  - `ROOT_TITLES`, `STACK_LINE`, `STACK_CHAIN`, `STACK_CHAINS`.
- Produces, in the template: the call
  `brain_session_start("{{ brain_key }}", client_key="<harness>-{{ brain_key }}-<YYYY-MM-DD>")`,
  and the markers `{# stack-chain: <name> -#}` … `{#- /stack-chain #}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_scaffold.py`, add `import re` to the standard-library imports, add
`from typing import NamedTuple` after `from pathlib import Path`, and change the model import to
`from rail.model import DeployTarget, LedgerBackend, Stack, Tier`. In
`test_every_deploy_target_the_cli_offers_is_a_copier_choice`, delete the now-redundant local
line `from rail.model import DeployTarget`. Then append at the end of the file:

```python
# -- rendered guidance (spec 2026-09-24-template-alignment) ------------------------------

PRIVATE_HEALTHCHECK = "http://192.0.2.10:9204/healthz"  # RFC 5737: typed, never assumed
BRAIN_TICKET = "04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f"
TARGET_FAMILIES = (DeployTarget.VPS_TRAEFIK.value, DeployTarget.PRIVATE_COMPOSE.value)


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


COMBOS = [
    Combo(stack, tier, ledger, target)
    for stack in Stack
    for tier in Tier
    for ledger in LedgerBackend
    for target in (TARGET_FAMILIES if tier is Tier.PROD else (None,))
] + [Combo(Stack.PYTHON, Tier.BOOTSTRAP, LedgerBackend.FILE, brain_key="red_probe")]
ROOT_TITLES = (
    "Workflows — the operator picks the method",
    "Invariants — true whatever the method",
)
STACK_LINE = {
    Stack.PYTHON: "Python 3.12+, uv, pytest, ruff.",
    Stack.GO: "Go 1.22+, `go test`, `go vet`, `gofmt`.",
    Stack.DOCS: "Documentation only (Markdown).",
}
STACK_CHAIN = re.compile(
    r"\{#-?\s*stack-chain:\s*(?P<name>[a-z-]+)\s*-?#\}(?P<body>.*?)\{#-?\s*/stack-chain\s*-?#\}",
    re.DOTALL,
)
STACK_CHAINS = {"CLAUDE.md.jinja": {"stack", "structure"}}
_ELSE = re.compile(r"\{%-?\s*else\s*-?%\}")


def _render_combo(template: Path, dest: Path, combo: Combo) -> Path:
    private = combo.target == DeployTarget.PRIVATE_COMPOSE
    return render(
        NewProject(
            slug="red-probe",
            description="A disposable HTTP probe.",
            tier=combo.tier,
            stack=combo.stack,
            brain_key=combo.brain_key,
            dest=dest,
            template=str(template),
            deploy_target=combo.target or DeployTarget.VPS_TRAEFIK.value,
            healthcheck=PRIVATE_HEALTHCHECK if private else None,
            ledger=combo.ledger,
            ticket=BRAIN_TICKET if combo.ledger is LedgerBackend.BRAIN else None,
        )
    )


@pytest.fixture(scope="module")
def renders(tmp_path_factory: pytest.TempPathFactory) -> dict[Combo, Path]:
    """Every combination rendered once for the module, from a copy of the working tree's
    template (uncommitted edits included, as with `template_dir`)."""
    base = tmp_path_factory.mktemp("renders")
    template = base / "template-src"
    template.mkdir()
    shutil.copy(ROOT / "copier.yml", template / "copier.yml")
    shutil.copytree(ROOT / "template", template / "template")
    return {combo: _render_combo(template, base / combo.label, combo) for combo in COMBOS}


@pytest.mark.parametrize("combo", COMBOS, ids=[c.label for c in COMBOS])
def test_rendered_guidance_points_at_the_root(renders: dict[Combo, Path], combo: Combo) -> None:
    """The method, the review and the invariants that hold whatever the method live once, in
    the ReD root: `CLAUDE.md` points at their sections and copies neither, and says where
    each moving fact is read (decisions 6-8)."""
    claude = (renders[combo] / "CLAUDE.md").read_text()
    for title in ROOT_TITLES:
        assert f'§ "{title}"' in claude, title
    assert "## Working principles" not in claude
    assert not re.search(r"^\|\s*`(?:spec|graph|direct)`\s*\|", claude, re.MULTILINE)
    table = "## Where things live\n\n| Question | Where to look |\n|---|---|\n"
    assert table in claude
    assert claude.index("## Project") < claude.index(table) < claude.index("## Language")
    assert "| What tier, which ledger, which target? | `rail.yaml` |" in claude
    key = combo.brain_key
    call = f'brain_session_start("{key}", client_key="<harness>-{key}-<YYYY-MM-DD>")'
    assert f"`{call}`" in claude and claude.count("brain_session_start(") == 1
    lesson = f'brain_learn(topic, insight, project_key="{key}")'
    assert claude.index("## Brain MCP") < claude.index(lesson)
    assert f"## Stack\n\n{STACK_LINE[combo.stack]}\n\n## Commands" in claude
    assert "stack-chain" not in claude


def test_every_stack_chain_names_every_stack() -> None:
    """A render cannot see a missing stack branch (§ Structure and § Gates always render
    stack-independent lines). Each marked chain names every `Stack` member and has no
    catch-all `else`, so spec B's `rust` is refused chain by chain until it is added
    (decision 11). This test reads the template source on purpose."""
    for source, expected in STACK_CHAINS.items():
        text = (ROOT / "template" / "project" / source).read_text()
        chains = {m["name"]: m["body"] for m in STACK_CHAIN.finditer(text)}
        assert set(chains) == expected, f"{source}: marked chains {sorted(chains)}"
        for name, body in chains.items():
            missing = [s.value for s in Stack if f"stack == '{s.value}'" not in body]
            assert not missing, f"{source}, chain {name!r}: no branch for {missing}"
            assert not _ELSE.search(body), f"{source}, chain {name!r}: a catch-all else"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py -k "points_at_the_root or every_stack_chain"`
Expected: FAIL. The 25 render cases fail on the first root title. The chain test fails with
`CLAUDE.md.jinja: marked chains []`.

- [ ] **Step 3: Rewrite `template/project/CLAUDE.md.jinja`**

Replace the whole file with the text below. The `{# stack-chain … #}` comments render nothing.
Their `-` modifiers keep § Stack and § Structure byte-identical to what they rendered before.

````jinja
# {{ project }} — ReD sub-project

## Project

{{ description }}

- **Repo**: `~/hawkixs_infra/git_repo/ReD_v1/projects/{{ project }}/`
- **GitHub (`origin`)**: `git@github.com:hawkixs/{{ project }}.git` (private, canonical — the only remote)
- **Brain MCP project key**: `{{ brain_key }}` (group `red`)
- **Parent project**: ReD v1 (`~/hawkixs_infra/git_repo/ReD_v1/CLAUDE.md` — roster, cross-project rules)
- **Rail**: tier `{{ tier }}`, stack `{{ stack }}`, ledger `{{ ledger }}` — see `rail.yaml`; `rail check` is the verdict

ReD is GitHub only (decision `30acbbde`): one remote, one truth. A project that keeps a mirror
declares its host in `rail.yaml` (`gates: hygiene.mirror_host`) and the rail requires it.

## Where things live

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

## Language

Everything pushed to a remote is written in **English**: commits, branches, PRs, docs, code
comments, test names. The conversation with the operator stays in French.

## Architecture

Describe the modules as they appear. Durable decisions go to `docs/adr/`.

{% if tier == 'prod' and stack == 'python' -%}
## Service

`src/{{ project | replace('-', '_') }}/service.py` — standard-library HTTP server on `APP_PORT` (8080):
`/healthz` (liveness), `/version` (`project`, `version`, `git_sha` baked at build time,
`image_digest` from the deployment — the drift between git and what runs is measured here),
`/metrics` (Prometheus text). Non-root image (`Dockerfile`, base pinned by digest), stack in
`deploy/compose.yaml` behind Traefik at `{{ healthcheck | replace('/healthz', '') }}`, no port published.
Release and deployment go through the rail: `rail release --version X.Y.Z`, `rail deploy`,
`rail drill`; never `docker compose` by hand on the VPS.

{% endif -%}
## Stack

{# stack-chain: stack -#}
{% if stack == 'python' -%}
Python 3.12+, uv, pytest, ruff.
{%- elif stack == 'go' -%}
Go 1.22+, `go test`, `go vet`, `gofmt`.
{%- elif stack == 'docs' -%}
Documentation only (Markdown).
{%- endif %}
{#- /stack-chain #}

## Commands

```bash
make ci        # what CI runs: lint, test, check
make test      # the test suite
make lint      # lint and format check
make check     # the rail gates against this repository
```

## Structure

```
{{ project }}/
├── Makefile           # sync, lint, test, check, ci — `make ci` is exactly what CI runs
├── rail.yaml          # this project's manifest (tier {{ tier }})
├── docs/specs/        # design specs (dated)
├── docs/plans/        # implementation plans (dated)
├── docs/adr/          # architecture decision records (numbered)
├── docs/receipts/     # the file ledger: append-only evidence, written by `rail attest`, never by hand
{#- stack-chain: structure #}
{%- if stack == 'python' %}
{%- if tier == 'prod' %}
├── Dockerfile
├── deploy/compose.yaml
{%- endif %}
├── src/{{ project | replace('-', '_') }}/
└── tests/
{%- elif stack == 'go' %}
├── go.mod
└── *_test.go
{%- elif stack == 'docs' %}
└── README.md
{%- endif %}
{#- /stack-chain #}
```

## How we work

The method, the review that reads the diff before a commit, and the invariants that hold
whatever the method live once, in the ReD root `CLAUDE.md` (the "Parent project" above):
§ "Workflows — the operator picks the method" and § "Invariants — true whatever the method".
They are not copied here, the review gate included.

What the rail adds on this repository:

- `rail check` is the verdict, and `make ci` is exactly what CI runs.
- The only bypass is a `gates:` override in `rail.yaml`, with its reason: there is no
  `# rail: ignore`.
- Evidence is written by the rail only. With `ledger: file` it is the receipts in
  `docs/receipts/`; with `ledger: brain` it is the attestations against the ticket named in
  `rail.yaml`, the receipts being their mirrors.
- Secrets never enter the tree, an environment variable holding the value, or a command line.

## Brain MCP — proactive use

Project key: `{{ brain_key }}`. Use it without being asked.

- **Session start**: `brain_session_start("{{ brain_key }}", client_key="<harness>-{{ brain_key }}-<YYYY-MM-DD>")`,
  where `<harness>` is claude-code, codex or opencode. Reuse the key for every retry of that
  session, and give a parallel session its own suffix.
- **During work**: the specific tool, never `brain_learn` by default: `brain_log_decision` for
  a choice, `brain_save_snippet` for reusable code, `brain_create_runbook` for a procedure,
  `brain_learn` only for a pure insight.
- **After any correction from the operator**: `brain_learn(topic, insight, project_key="{{ brain_key }}")`.
- **Before solving a problem**: `brain_search(query, project_key="{{ brain_key }}")`.
- **Session end**: `brain_update_project_focus("{{ brain_key }}", current_focus="summary + next steps")`

## Related projects

| Project | Path | Relation |
|---|---|---|
| ReD (root) | `~/hawkixs_infra/git_repo/ReD_v1/` | parent, roster, cross-project rules |
| red-rail | `projects/red-rail` | the delivery rail: `rail check`, `rail audit`, `rail upgrade` |
````

The three harness names stay out of backticks on purpose. `claude-code` in a code span would be a
bare-skill-shaped token under D14 (Task 7).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py tests/test_template_service.py tests/test_template_go.py`
Expected: PASS. `test_render_prod_python_on_the_brain_ledger` still finds ``ledger `brain` ``
(§ Project line), and `test_template_service.py` still finds `/version` (§ Service).

- [ ] **Step 5: Lint, format and run the suite**

Run the three ruff commands, then `env -u VIRTUAL_ENV uv run pytest -q`.
Expected: clean, and every test passes.

- [ ] **Step 6: Commit**

```bash
git add template/project/CLAUDE.md.jinja tests/test_scaffold.py
git commit  # ✨ feat(template): CLAUDE.md points at the ReD root and says where things live
```

---

### Task 6: The template gains `AGENTS.md`

Spec step 6 (D9, D10, D8 for `AGENTS.md`, D11 for its chain; C5 and C6 for `AGENTS.md`).

**Files:**
- Create: `template/project/AGENTS.md.jinja`
- Test: `tests/test_scaffold.py` (`test_rendered_guidance_points_at_the_root` replaced as a whole, one test added, `STACK_CHAINS` extended)

**Interfaces:**
- Consumes: `Combo`, `COMBOS`, `renders`, `ROOT_TITLES`, `STACK_LINE`, `STACK_CHAINS` (Task 5).
- Produces: `GUIDANCE = ("CLAUDE.md", "AGENTS.md")`, `STACK_GATES`, `AGENTS_ORDER`, and
  `_in_order(text: str, needles: tuple[str, ...]) -> None`. The template carries the phrase
  `names as "Parent project"` and the keyed call, both consumed by Task 8.

- [ ] **Step 1: Write the failing tests**

In `tests/test_scaffold.py`, change `STACK_CHAINS` to:

```python
STACK_CHAINS = {"CLAUDE.md.jinja": {"stack", "structure"}, "AGENTS.md.jinja": {"gates"}}
```

After `_ELSE = …`, add:

```python
GUIDANCE = ("CLAUDE.md", "AGENTS.md")
STACK_GATES = {
    Stack.PYTHON: "`uv run pytest -q`",
    Stack.GO: "`go test -race -count=1 ./...`",
    Stack.DOCS: "no stack command of its own",
}
# D9's sections and D10's rows, in the order AGENTS.md must hold them
AGENTS_ORDER = (
    "Read `CLAUDE.md` first",
    'names as "Parent project"',
    "the `graph` method is **unavailable**",
    "A pre-review never satisfies the review gate",
    "## The invariants you must not break",
    "**Always: `rail check` is the verdict**",
    "**Always: the only bypass is a `gates:` override",
    "**With `ledger: file`:",
    "**With `ledger: brain`:",
    "**At `tier: prod`:",
    "**At `prod` with `deploy.target: vps-traefik`:",
    "**At `prod` with any other target:",
    "| project-specific: fill in |",
    "## Gates",
    "make ci        # exactly what CI runs",
    "rail check     # the rail's gates",
    "## Brain MCP",
    "never `brain_learn` by default",
    "## Subagents",
    "Every subagent prompt names its perimeter",
)


def _in_order(text: str, needles: tuple[str, ...]) -> None:
    position = 0
    for needle in needles:
        found = text.find(needle, position)
        assert found >= 0, f"missing, or out of order: {needle!r}"
        position = found + len(needle)
```

Replace `test_rendered_guidance_points_at_the_root` as a whole with:

```python
@pytest.mark.parametrize("combo", COMBOS, ids=[c.label for c in COMBOS])
def test_rendered_guidance_points_at_the_root(renders: dict[Combo, Path], combo: Combo) -> None:
    """The method, the review and the invariants that hold whatever the method live once, in
    the ReD root: `CLAUDE.md` points at their sections and copies neither (decisions 6-8).
    `AGENTS.md` carries what Codex cannot reach from a sub-project's git root, and points at
    the rest (decisions 9-10)."""
    key = combo.brain_key
    call = f'brain_session_start("{key}", client_key="<harness>-{key}-<YYYY-MM-DD>")'

    claude = (renders[combo] / "CLAUDE.md").read_text()
    for title in ROOT_TITLES:
        assert f'§ "{title}"' in claude, title
    assert "## Working principles" not in claude
    assert not re.search(r"^\|\s*`(?:spec|graph|direct)`\s*\|", claude, re.MULTILINE)
    table = "## Where things live\n\n| Question | Where to look |\n|---|---|\n"
    assert table in claude
    assert claude.index("## Project") < claude.index(table) < claude.index("## Language")
    assert "| What tier, which ledger, which target? | `rail.yaml` |" in claude
    assert f"`{call}`" in claude and claude.count("brain_session_start(") == 1
    lesson = f'brain_learn(topic, insight, project_key="{key}")'
    assert claude.index("## Brain MCP") < claude.index(lesson)
    assert f"## Stack\n\n{STACK_LINE[combo.stack]}\n\n## Commands" in claude
    assert "stack-chain" not in claude

    agents = (renders[combo] / "AGENTS.md").read_text()
    _in_order(agents, AGENTS_ORDER)
    assert f"`{call}`" in agents and agents.count("brain_session_start(") == 1
    assert agents.index("## Brain MCP") < agents.index(call)
    assert STACK_GATES[combo.stack] in agents
    assert "../../AGENTS.md" not in agents and "~/" not in agents
    assert "stack-chain" not in agents
    for target in DeployTarget:
        if target is not DeployTarget.VPS_TRAEFIK:
            assert target.value not in agents, f"AGENTS.md names the private {target.value}"


def test_agents_md_does_not_depend_on_tier_ledger_or_target(renders: dict[Combo, Path]) -> None:
    """Copier answers freeze at scaffold time and nothing re-answers `tier` on promotion, so
    the invariant rows are unconditional, each prefixed by the value it applies to (decision
    10): for one stack and one key, one `AGENTS.md`, whatever the tier, ledger and target."""
    texts: dict[tuple[Stack, str], set[str]] = {}
    for combo, dest in renders.items():
        texts.setdefault((combo.stack, combo.brain_key), set()).add(
            (dest / "AGENTS.md").read_text()
        )
    assert {stack for stack, _ in texts} == set(Stack)
    varying = sorted(f"{s.value}/{key}" for (s, key), seen in texts.items() if len(seen) != 1)
    assert not varying, f"AGENTS.md varies with tier, ledger or target for {varying}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py -k "points_at_the_root or agents_md or every_stack_chain"`
Expected: FAIL. The render cases fail with `FileNotFoundError` on `AGENTS.md`. The chain test
fails with `FileNotFoundError` on `AGENTS.md.jinja`.

- [ ] **Step 3: Create `template/project/AGENTS.md.jinja`**

````jinja
# {{ project }} — guidance for Codex and OpenCode

Read `CLAUDE.md` first: it holds this project's identity, stack, commands and layout, and
this file does not duplicate them. Then read the ReD root `AGENTS.md`, in the directory that
`CLAUDE.md` names as "Parent project". This file carries what a harness other than Claude
Code needs and cannot reach from here, and points at the rest.

## What this project is

{{ description }}

Stack: `{{ stack }}`. The tier, the ledger and the deploy target are read from `rail.yaml`,
never from this file: they move, and nothing re-renders this file when they do.

## Method on this harness

The operator picks the method (`spec`, `graph` or `direct`) once per session, at the first
non-trivial code task. On this harness the `graph` method is **unavailable**: the choice
narrows to `spec` or `direct`, and you say so rather than silently falling back. Whatever the
method, when `rail.yaml` is present its gates apply on top, and the independent verdict is the
verdict. A pre-review never satisfies the review gate.

## The invariants you must not break

Each row names the `rail.yaml` value it applies to: read the value there.

| Invariant | Why |
|---|---|
| **Always: `rail check` is the verdict** | `make ci` is exactly what CI runs, and a red gate is a policy failure, not a nuisance. |
| **Always: the only bypass is a `gates:` override in `rail.yaml`, with its reason** | There is no `# rail: ignore`: `rail check` and `rail audit` report every override, nothing is hidden. |
| **With `ledger: file`: never edit a receipt by hand** | `docs/receipts/` is append-only and digest-checked, and the rail fails closed on a tampered receipt. |
| **With `ledger: brain`: evidence is attested to brain, and the mirrors are written by the rail** | `integrated` and `fulfilled` are read from the ticket named in `rail.yaml`, never attested from here. |
| **At `tier: prod`: a release is an image pinned by digest, and deployment goes through `rail deploy` only** | Never by hand on the target: the rail locks it, verifies `/version` against the release and records the evidence. |
| **At `prod` with `deploy.target: vps-traefik`: nothing is published** | The Traefik route is the only way in. |
| **At `prod` with any other target: a compose file publishes on the target's bind address only, never on every interface** | Docker bypasses the firewall, so a port published on every interface is open to every network the machine sees; `rail deploy` refuses a compose file that would. |
| project-specific: fill in | |

## Gates

```bash
make ci        # exactly what CI runs
rail check     # the rail's gates against this repository: the exit code is the verdict
```

{# stack-chain: gates -#}
{% if stack == 'python' -%}
This stack's own commands: `uv run pytest -q`, `uv run ruff check src/ tests/`,
`uv run ruff format --check src/ tests/`.
{%- elif stack == 'go' -%}
This stack's own commands: `go test -race -count=1 ./...`, `go vet ./...`,
`go tool staticcheck ./...`, `go tool govulncheck ./...`; on a host with no Go installed,
`make GO=<wrapper> ci` runs them all through the wrapper.
{%- elif stack == 'docs' -%}
A documentation project has no stack command of its own: `make ci` runs the rail's gates.
{%- endif %}
{#- /stack-chain #}

## Brain MCP

Project key `{{ brain_key }}` (group `red`). Start material work with
`brain_session_start("{{ brain_key }}", client_key="<harness>-{{ brain_key }}-<YYYY-MM-DD>")`,
where `<harness>` is codex or opencode here (claude-code under Claude Code). Reuse the key for
every retry of that session, and give a parallel session its own suffix.

Persist knowledge with the specific tool, never `brain_learn` by default: `brain_log_decision`
for a choice, `brain_save_snippet` for a reusable pattern, `brain_create_runbook` for a
procedure, `brain_propose_adr` for durable architecture, `brain_learn` only as a last resort.

## Subagents

Every subagent prompt names its perimeter: a concrete path or glob, an explicit budget, or an
explicit output contract. Name the files you already know instead of asking an agent to find
them. The rest of the dispatch rule is in the root `CLAUDE.md`, § Subagents.
````

Four constraints hold this text. D10: the invariant rows are unconditional, and none names a
private target. D8: the call sits on one line. D9: no `../../AGENTS.md` and no new absolute path.
D14: no backticked kebab-case token other than `vps-traefik`, which is a `DeployTarget` value.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py`
Expected: PASS: 25 render cases, the invariance test and the chain test.

- [ ] **Step 5: Check that a fresh scaffold with `AGENTS.md` still passes its own gates**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py -k "passes_bootstrap_without_remotes or prod_scaffold_is_verified"`
Expected: PASS.

- [ ] **Step 6: Lint, format and run the suite**

Run the three ruff commands, then `env -u VIRTUAL_ENV uv run pytest -q`.
Expected: clean, and every test passes.

- [ ] **Step 7: Commit**

```bash
git add template/project/AGENTS.md.jinja tests/test_scaffold.py
git commit  # ✨ feat(template): AGENTS.md, the guidance Codex and OpenCode cannot reach from the root
```

---

### Task 7: The rendered guidance cites no skill and carries no address

Spec step 7 (D14, D15, C8). The address helper moves first: the existing test stays green. Then
the extraction unit test, red first. The two render tests are green at once because Tasks 5 and
6 landed. Step 7 proves they bite.

**Files:**
- Create: `tests/addresses.py`
- Modify: `tests/test_no_machine_address.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Produces, in `tests.addresses`: `ALLOWED`, `_candidates(text: str) -> list[str]`,
  `_foreign(text: str) -> list[str]`, moved verbatim.
- Produces, in `tests/test_scaffold.py`: `ROUTES`, `CITABLE`, `STALE`,
  `_cited_skills(text: str, *, own: set[str]) -> set[str]`. Task 8 uses `STALE`.
- Consumes: `COMBOS`, `renders` (Task 5), `GUIDANCE` (Task 6).

- [ ] **Step 1: Move the address policy to `tests/addresses.py`**

Create `tests/addresses.py`:

```python
"""The repository's one address policy (spec 2026-09-24-template-alignment, decision 15). An
address that is only an example comes from the documentation ranges (RFC 5737, RFC 3849);
loopback, unspecified and link-local are never a machine's. Shared by the scan of the tracked
files and by the scan of every rendered scaffold."""

import ipaddress
import re

from rail.contract_guard import _DOTTED

# Colon-separated hex groups, at least three groups; parsing decides whether it is an address,
# so a time of day (`12:34:56`) or a slice (`[::2]`) never counts.
_COLONS = re.compile(r"(?<![\w:.])([0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})(?![\w:.])")

ALLOWED = tuple(
    ipaddress.ip_network(net)
    for net in (
        "192.0.2.0/24",  # TEST-NET-1
        "198.51.100.0/24",  # TEST-NET-2
        "203.0.113.0/24",  # TEST-NET-3
        "127.0.0.0/8",  # loopback
        "0.0.0.0/32",  # the unspecified address, named in refusals
        "2001:db8::/32",  # IPv6 documentation
        # loopback, unspecified and the deprecated IPv4-compatible block: never a machine's
        # address, and a slice such as `[::2]` parses into it
        "::/96",
        "fe80::/10",  # link-local: an interface's, never a machine's reachable address
        # YAML 1.1 reads an all-digit IPv6 as a base-60 integer, and no documentation address
        # is written in digits only: the one example that test needs, and nothing around it
        "2001::1/128",
    )
)


def _candidates(text: str) -> list[str]:
    return _DOTTED.findall(text) + _COLONS.findall(text)


def _foreign(text: str) -> list[str]:
    """Literals that parse as an IP address and fall outside the documentation, loopback and
    link-local ranges. The contract guard's lookarounds: `1.2.3.4.5` and versions are not
    addresses."""
    found = []
    for candidate in _candidates(text):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not any(address.version == net.version and address in net for net in ALLOWED):
            found.append(candidate)
    return found
```

In `tests/test_no_machine_address.py`, replace the head of the module, from the docstring
through the end of `_foreign`, with the text below. Both `test_` functions below it stay
byte-identical.

```python
"""This repository is public: no machine address may be committed to it, not in code, not
in a test, not in a receipt. An address that is only an example comes from the documentation
ranges (RFC 5737, RFC 3849); a real one lives in the host's private files (`sites.yaml`). The
policy itself lives in `tests/addresses.py`, shared with the scan of rendered scaffolds."""

import subprocess
from pathlib import Path

from tests.addresses import _foreign

ROOT = Path(__file__).resolve().parents[1]
```

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_no_machine_address.py`
Expected: PASS (2 passed). The move changes no verdict.

- [ ] **Step 2: Write the failing extraction test**

In `tests/test_scaffold.py`, add `from tests.addresses import _foreign` before
`from tests.fake_brain import FakeBrain`. The complete import block now reads:

```python
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import pytest
from click.testing import CliRunner

from rail import gitrepo
from rail.brain.client import BrainClient
from rail.cli import main
from rail.commands.new import BRAIN_KEY
from rail.gates.hygiene import DOMAIN_PLACEHOLDER, ROSTER_HEADER, roster_row, table_cells
from rail.ledger import RECEIPTS_DIR, RecordKind
from rail.ledger.file import FileLedger
from rail.model import DeployTarget, LedgerBackend, Stack, Tier
from rail.remotes import RemoteError
from rail.scaffold import ANSWERS_FILE, NewProject, ScaffoldError, new_project, render
from tests.addresses import _foreign
from tests.fake_brain import FakeBrain
```

Append at the end of the file:

```python
def test_skill_extraction_reads_whole_code_spans_only() -> None:
    """Decision 14's extraction: anchored on whole code spans, so a git URL or a `gates:`
    key never reads as a skill, and the routes, the slug and the targets are not citations."""
    text = (
        "Run `gitnexus-lfg`, then `/red-review` and `superpowers:brainstorming`. Not "
        "`git@github.com:hawkixs/red-probe.git`, `gates: hygiene.mirror_host`, `rail check`, "
        "`/healthz`, `red-probe`, `vps-traefik`, `rail.yaml` or `brain_learn`."
    )
    assert _cited_skills(text, own={"red-probe"}) == {
        "gitnexus-lfg",
        "/red-review",
        "superpowers:brainstorming",
    }
```

- [ ] **Step 3: Run it to verify it fails**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py -k skill_extraction`
Expected: FAIL with `NameError: name '_cited_skills' is not defined`.

- [ ] **Step 4: Implement the extraction and the two render tests**

In `tests/test_scaffold.py`, insert right above `test_skill_extraction_reads_whole_code_spans_only`:

```python
ROUTES = frozenset({"/healthz", "/version", "/metrics"})  # the service's HTTP routes
# Every skill or slash-command a rendered guidance file may name. Empty since decision 6: the
# root is the pointer. Adding a name is a reviewed line (decision 14).
CITABLE: frozenset[str] = frozenset()
STALE = (
    "sdd-brainstorm",
    "writing-plans-parallel",
    "executing-plans-parallel",
    "/tdd-write-tests",
    "/reflexion-reflect",
    "/code-review-review-local-changes",
)
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_SLASH_COMMAND = re.compile(r"^/[a-z][a-z0-9-]*$")
_NAMESPACED_SKILL = re.compile(r"^[a-z][a-z0-9-]*:[a-z][a-z0-9-]*$")
_BARE_SKILL = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)+$")


def _cited_skills(text: str, *, own: set[str]) -> set[str]:
    """Every whole code span shaped like a skill or a slash-command (decision 14), minus the
    service's routes and what a render legitimately carries: its slug, its brain key and the
    deploy targets."""
    ignored = ROUTES | own | {target.value for target in DeployTarget}
    return {
        token
        for token in _CODE_SPAN.findall(text)
        if token not in ignored
        and (
            _SLASH_COMMAND.match(token)
            or _NAMESPACED_SKILL.match(token)
            or _BARE_SKILL.match(token)
        )
    }
```

Append at the end of the file:

```python
@pytest.mark.parametrize("combo", COMBOS, ids=[c.label for c in COMBOS])
def test_rendered_guidance_cites_only_allowed_skills(
    renders: dict[Combo, Path], combo: Combo
) -> None:
    """A skill list rots, and the template's did: the root is the pointer, so a rendered
    guidance file names no skill outside `CITABLE`, and none of the six stale names survives
    anywhere in the tree (decision 14)."""
    dest = renders[combo]
    for name in GUIDANCE:
        cited = _cited_skills((dest / name).read_text(), own={"red-probe", combo.brain_key})
        assert cited <= CITABLE, f"{name} cites {sorted(cited - CITABLE)}"
    for path in (p for p in dest.rglob("*") if p.is_file()):
        text = path.read_bytes().decode("utf-8", errors="replace")
        assert not [s for s in STALE if s in text], path.relative_to(dest)
    if combo.stack is Stack.PYTHON and combo.tier is Tier.PROD:
        claude = (dest / "CLAUDE.md").read_text()
        assert all(f"`{route}`" in claude for route in ROUTES)  # the exclusion is exercised


def test_rendered_files_carry_no_address_literal(renders: dict[Combo, Path]) -> None:
    """The template adds no address of its own: what reaches a render is the typed
    healthcheck (a documentation range here) and the service's loopback and wildcard, under
    the repository's one policy (decision 15)."""
    offenders = []
    for combo, dest in renders.items():
        for path in sorted(p for p in dest.rglob("*") if p.is_file()):
            if _foreign(path.read_bytes().decode("utf-8", errors="replace")):
                offenders.append(f"{combo.label}/{path.relative_to(dest)}")
    assert not offenders, "address literals in rendered files: " + ", ".join(offenders)
    private = [d for c, d in renders.items() if c.target == DeployTarget.PRIVATE_COMPOSE]
    assert private and all("192.0.2.10" in (d / "rail.yaml").read_text() for d in private)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py tests/test_no_machine_address.py`
Expected: PASS. The extraction test is now green. The two render tests are green at once (spec
order step 7: "green once steps 5-6 land").

- [ ] **Step 6: Lint and format**

Run the three ruff commands.
Expected: clean.

- [ ] **Step 7: Prove the two render tests bite, then restore the templates**

```bash
printf '\nSee `gitnexus-lfg`.\n' >> template/project/AGENTS.md.jinja
printf 'Reach %s.%s.%s.%s here.\n' 10 0 0 7 >> template/project/README.md.jinja
env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py -k "cites_only or no_address_literal"
git checkout -- template/project/AGENTS.md.jinja template/project/README.md.jinja
git status --short template/
```

Expected: both tests FAIL. The first names `gitnexus-lfg`; the second lists the `README.md` of
every render, by location only. Then `git status --short template/` prints nothing.

- [ ] **Step 8: Run the suite**

Run: `env -u VIRTUAL_ENV uv run pytest -q`
Expected: every test passes. Read the summary line.

- [ ] **Step 9: Commit**

```bash
git add tests/addresses.py tests/test_no_machine_address.py tests/test_scaffold.py
git commit  # ✅ test(template): rendered guidance cites no skill and carries no address literal
```

---

### Task 8: red-rail's own guidance follows its template, and the branch proves itself

Spec step 8 (D13, D8's regex, C7), then spec step 9's first half (C9).

**Files:**
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: `BRAIN_KEY` (Task 4), `COMBOS`, `renders`, `ROOT_TITLES` (Task 5), `GUIDANCE`
  (Task 6), `STALE` (Task 7).
- Produces: `SESSION_START` (compiled regex) and
  `_unkeyed_session_starts(text: str) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaffold.py`:

```python
_KEY_CLASS = BRAIN_KEY.pattern.removeprefix("^").removesuffix("$")  # the validator's own class
SESSION_START = re.compile(
    rf'brain_session_start\("(?P<k>{_KEY_CLASS})", client_key="<harness>-(?P=k)-<YYYY-MM-DD>"\)'
)


def _unkeyed_session_starts(text: str) -> list[str]:
    """Each `brain_session_start(` that does not open decision 8's call, as its line."""
    return [
        text[match.start() :].split("\n", 1)[0]
        for match in re.finditer(r"brain_session_start\(", text)
        if not SESSION_START.match(text, match.start())
    ]


def test_the_session_start_check_refuses_a_bare_or_mismatched_call() -> None:
    assert _unkeyed_session_starts('brain_session_start("red-rail")')
    assert _unkeyed_session_starts(
        'brain_session_start("red-rail", client_key="<harness>-red-alpha-<YYYY-MM-DD>")'
    )
    assert not _unkeyed_session_starts(
        'brain_session_start("auto_discord", client_key="<harness>-auto_discord-<YYYY-MM-DD>")'
    )


@pytest.mark.parametrize(
    "combo", [*COMBOS, None], ids=[*(c.label for c in COMBOS), "red-rail-itself"]
)
def test_every_brain_session_start_carries_a_client_key(
    renders: dict[Combo, Path], combo: Combo | None
) -> None:
    """One `client_key` form everywhere (decision 8), on every render and on red-rail's own
    two files, checked with the same class the key validator accepts."""
    tree = ROOT if combo is None else renders[combo]
    for name in GUIDANCE:
        text = (tree / name).read_text()
        assert "brain_session_start(" in text, f"{name}: no session-start example"
        assert not _unkeyed_session_starts(text), f"{name}: {_unkeyed_session_starts(text)}"


def test_red_rails_own_guidance_follows_its_template() -> None:
    """red-rail dogfoods its template (decision 13): the root pointer instead of a skill
    pipeline, the "Where things live" table, and `AGENTS.md` reaching the root through
    "Parent project" rather than a relative path that breaks from a nested worktree."""
    claude = (ROOT / "CLAUDE.md").read_text()
    agents = (ROOT / "AGENTS.md").read_text()
    for title in ROOT_TITLES:
        assert f'§ "{title}"' in claude, title
    assert "## Where things live\n\n| Question | Where to look |\n|---|---|\n" in claude
    assert "## Working principles" not in claude
    assert "`rail check` passes on this repository" in claude
    assert "`pre-review.js` must pass" in claude
    for stale in STALE:
        assert stale not in claude and stale not in agents, stale
    assert "../../AGENTS.md" not in agents
    assert 'names as "Parent project"' in agents
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py -k "session_start or own_guidance"`
Expected: FAIL. The `red-rail-itself` case reports `brain_session_start("red-rail")` in
`CLAUDE.md`. `test_red_rails_own_guidance_follows_its_template` fails on the first root title.
The 25 render cases and the refusal test PASS.

- [ ] **Step 3: Update red-rail's `CLAUDE.md`**

(a) After the paragraph that ends with ``and pushes release tags to it; `rail new` creates
GitHub alone.``, insert one blank line and:

```markdown
## Where things live

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
```

(b) Replace everything from `## Working principles` down to the line
`- **From scratch**: prefer building the small thing over adopting the big platform.` with:

```markdown
## How we work

The method, the review that reads the diff before a commit, and the invariants that hold
whatever the method live once, in the ReD root `CLAUDE.md` (the "Parent project" above):
§ "Workflows — the operator picks the method" and § "Invariants — true whatever the method".
They are not copied here, the review gate included.

What this repository adds:

- `rail check` passes on this repository. red-rail is delivered by its own rail, so a red gate
  here is a real policy failure, not a nuisance.
- Every Workflow `agent()` in `workflows/` carries an explicit tier: `pre-review.js` must pass
  the tiering gate.
- The invariants a change here must not break, the public-repository rules included, are in
  `AGENTS.md`.
```

(c) Replace the `## Brain MCP — proactive use` section, from its heading down to the blank line
before `## Related projects`, with:

```markdown
## Brain MCP — proactive use

Project key: `red-rail`. Use it without being asked.

- **Session start**: `brain_session_start("red-rail", client_key="<harness>-red-rail-<YYYY-MM-DD>")`,
  where `<harness>` is claude-code, codex or opencode. Reuse the key for every retry of that
  session, and give a parallel session its own suffix.
- **During work**: the specific tool, never `brain_learn` by default: `brain_log_decision` for
  a choice, `brain_save_snippet` for reusable code, `brain_create_runbook` for a procedure,
  `brain_learn` only for a pure insight.
- **After any correction from the operator**: `brain_learn(topic, insight, project_key="red-rail")`.
- **Before solving a problem**: `brain_search(query, project_key="red-rail")`.
- **Session end**: `brain_update_project_focus("red-rail", current_focus="summary + next steps")`
```

- [ ] **Step 4: Update red-rail's `AGENTS.md`**

Replace the line ``Also read the ecosystem-level [`../../AGENTS.md`](../../AGENTS.md).`` with
this single line (the test needle `names as "Parent project"` must not wrap):

```markdown
Then read the ReD root `AGENTS.md`, in the directory that `CLAUDE.md` names as "Parent project".
```

In § Brain MCP, replace the paragraph that starts ``Start material work with
`brain_session_start("red-rail")`.`` and ends with ``, `brain_learn` only as a last resort.``
with:

```markdown
Start material work with
`brain_session_start("red-rail", client_key="<harness>-red-rail-<YYYY-MM-DD>")`, where
`<harness>` is codex or opencode here (claude-code under Claude Code). Reuse the key for every
retry of that session, and give a parallel session its own suffix. Persist knowledge with the
specific tool, never `brain_learn` by default: `brain_log_decision` for a choice,
`brain_save_snippet` for a reusable pattern, `brain_create_runbook` for a procedure,
`brain_propose_adr` for durable architecture, `brain_learn` only as a last resort.
```

Keep § "This repository is public" and every other section as it is.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest -q tests/test_scaffold.py tests/test_dogfood.py`
Expected: PASS. `test_dogfood.py`'s `rail check --ci` still passes on the edited `CLAUDE.md`:
the `claude_md` gate finds `` `red-rail` `` and the unchanged Commands block.

- [ ] **Step 6: Lint and format**

Run the three ruff commands.
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md AGENTS.md tests/test_scaffold.py
git commit  # 📝 docs: red-rail's guidance follows its own template
```

- [ ] **Step 8: The whole proof (C9)**

Run: `env -u VIRTUAL_ENV make ci`
Expected: exit code 0. ruff is clean, and the pytest summary line reports every test passing.
`rail check` prints `passed 18/18`. Read both summary lines; do not infer them.

Run from this worktree's root: `env -u VIRTUAL_ENV uv run rail check`
Expected: exit code 0, and the roster line reads
`PASS  hygiene.roster_entry   listed in ReD_v1/CLAUDE.md`, not `standalone`. That is cdb725e4
proven live. If a ledger-scoped gate fails because the ticket in `rail.yaml` is already
accepted, stop and report it: that state is outside this spec (D16: no `rail bind`, no new
ticket), and `rail.yaml` is not edited here.

---

## After the last task (not part of the task gates)

1. **Host verification, C10.** Run it on the committed branch. Record the outcome in the pull
   request body: counts and verdicts only, never a machine name or address.

   ```bash
   D=$(mktemp -d)
   ROOT_MD="$(dirname "$(dirname "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")")")/CLAUDE.md"
   env -u VIRTUAL_ENV uv run rail new red-throwaway --description "throwaway" --no-remotes --template . --template-ref HEAD --dest "$D/python"
   env -u VIRTUAL_ENV uv run rail new red-throwaway --description "throwaway" --no-remotes --template . --template-ref HEAD --stack go --tier dev --dest "$D/go"
   env -u VIRTUAL_ENV uv run rail new red-throwaway --description "throwaway" --no-remotes --template . --template-ref HEAD --stack docs --dest "$D/docs"
   ls "$D"/python/AGENTS.md "$D"/go/AGENTS.md "$D"/docs/AGENTS.md
   ```

   Expected: three trees, each with `AGENTS.md`. That file came from this branch, not `v0.4.0`.

   Run decision 14's extraction on the trees:

   ```bash
   env -u VIRTUAL_ENV uv run python - "$D" <<'EOF'
   import sys
   from pathlib import Path

   from tests.test_scaffold import _cited_skills

   for tree in sorted(Path(sys.argv[1]).iterdir()):
       for name in ("CLAUDE.md", "AGENTS.md"):
           cited = _cited_skills((tree / name).read_text(), own={"red-throwaway"})
           print(tree.name, name, sorted(cited))
   EOF
   ```

   Expected: six lines, each ending with `[]`. A name that does appear must exist under
   `~/.claude/skills` or `~/.claude/plugins/cache`, or the branch is wrong.

   Read the machine names from the root table headed `| Machine | Role |`, never from the
   project roster. Keep them out of the output:

   ```bash
   env -u VIRTUAL_ENV uv run python - "$ROOT_MD" > "$D/names" <<'EOF'
   import re
   import sys
   from pathlib import Path

   lines = Path(sys.argv[1]).read_text().splitlines()
   start = next(i for i, line in enumerate(lines) if line.startswith("| Machine | Role |"))
   names = set()
   for line in lines[start + 2 :]:
       if not line.startswith("|"):
           break
       cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
       machine, wg, access = cells[0], cells[2], cells[3]
       names.update(re.findall(r"`([^`]+)`", machine))  # hostnames
       names.update(re.findall(r"^([\w-]+) \(", machine))  # a machine named by its first word
       names.update(re.findall(r"`([^`]+)`", wg))  # mesh addresses
       names.update(re.findall(r"ssh ([\w.-]+)", access))  # ssh aliases
       names.update(re.findall(r"\b\d+(?:\.\d+){3}\b", access))  # LAN addresses
   print("\n".join(sorted(names)))
   EOF
   wc -l < "$D/names"
   while read -r name; do grep -rlF -- "$name" "$D/python" "$D/go" "$D/docs" >/dev/null && echo "FOUND ONE"; done < "$D/names"
   ```

   Expected: a count of at least six, and no `FOUND ONE` line.

   Grep the root for the passages this branch is coupled to (D6, accepted coupling):

   ```bash
   for needle in 'Workflows — the operator picks the method' 'Invariants — true whatever the method' '### Harness limits' 'A pre-review never satisfies the review gate' 'the `graph` method is **unavailable**'; do grep -qF -- "$needle" "$ROOT_MD" && echo "found: $needle" || echo "MISSING: $needle"; done
   rm -rf "$D"
   ```

   Expected: five `found:` lines. The tmp trees are then deleted.

2. **Before the pull request, put the spec's § "For the operator" questions to the operator.**
   Decision 8's `client_key` form is cheap to change now and costly after five repositories
   upgrade.
3. **The whole-branch review of the `spec` method** (`red-review`), then the fixes, each
   test-first.
4. **One pull request** on `hawkixs/red-rail`, from this branch (started at `f5b7d8f`) into
   `main`. It is in English and carries C10's record. There is no `rail bind`: engagement
   `d096f911` allows none until red opens the next phase ticket. Then run
   `env -u VIRTUAL_ENV uv run rail reviewer once --repository hawkixs/red-rail --pr <n>`. Merge
   on the operator's approval, then reinstall the global `rail` (snippet `efdec356`).
5. **The tag, D16.** Cut an annotated `v0.5.0` on `origin`, from `main`, as `v0.4.0` was. First
   run `git show v0.4.0:pyproject.toml | grep '^version'`. If the `v0.4.0` commit declares
   `version = "0.4.0"`, then `[project].version` must read `0.5.0` before the tag. That is a
   one-line reviewed pull request of its own; `tests/test_version.py` follows it.
6. **C11.** In a temporary directory, run
   `env -u VIRTUAL_ENV uv run rail new red-throwaway --description "throwaway" --no-remotes --dest <tmp>/t`
   with no `--template-ref`. Expected: `<tmp>/t/AGENTS.md` exists. Then delete the tree.
7. **Hand-offs, none of them part of this pull request.** Close tickets `09b9e210`,
   `cdb725e4` and `2a6781cb` with the pull request as evidence. Open one propagation ticket per
   repository, using the facts in the spec's § "Propagation facts". Tell the operator that
   `red-e2e-target` and `leaked-claude-code` will now FAIL `hygiene.roster_entry` in the next
   audit.
