# Gates Name the Missing Declaration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Without a `rail.yaml`, each of eleven gates reports what it observed in the repository, or
the precise manifest key its verdict depends on (`NEED`), instead of stopping at "rail.yaml is
missing".

**Architecture:** One partial reading of the manifest (`rail.model.declarations`) gives each field or
its documented default. It is used only when the file is absent, never when the file is invalid.
`GateResult` gains a fail-closed `needs` field, built through one `Need` value. The six evidence
gates share the rule through `_attestations`. `rail check` and `rail audit` render the new outcome
distinctly.

**Tech Stack:** Python 3.12, Pydantic 2, Click, pytest, ruff (`uv run …`).

**Spec:** `docs/specs/2026-09-23-gates-name-the-missing-declaration.md`

## Global Constraints

- `needs` set ⇒ `passed=False`. A gate never turns green because the manifest is missing.
- Defaults apply only when `rail.yaml` is **absent**. An invalid manifest keeps today's output
  (`rail.yaml is invalid: …` on every dependent gate).
- Without a manifest, no gate other than `hygiene.rail_config` mentions `rail.yaml` in its
  details.
- No inference of `project` or `stack` (no directory name, no `pyproject.toml` sniffing for the
  verdict).
- With a manifest present, gates, messages, scores and `tests/golden/audit-matrix.json` are
  unchanged.
- Everything written is English (code, comments, test names, commits). Commit through
  `/git-commit`.
- Work on branch `feat/hawixs/gates-name-the-missing-declaration`: rename the spec branch
  `docs/hawixs/gates-name-the-missing-declaration` before the first code commit.

## Review Focus

1. **Invalid manifest declaring `ledger: brain`, with receipts in `docs/receipts`.** The receipts
   must never be read. The gate reports `is invalid` and has `needs is None`. Test in Task 3.
2. **A tampered receipt and no manifest.** The ledger error surfaces as a FAIL naming the
   tampered receipt, not as a NEED and not as "no … attestation". Test in Task 3.
3. **`rail check --ci` without a manifest.** The ledger is the file default, so the ledger gates
   still run and give the same FAIL/NEED as locally. Nothing is silently skipped. Test in
   Task 5.
4. **A manifest is present.** `NEED` never appears and `needs_declaration` is empty on a
   conforming repository. Test in Task 5.
5. **`rail audit` on a manifest-less repository.** The undeclared tier is bootstrap (hygiene,
   intent, design), so build is out of scope there. The intent column of a repository holding a
   contract receipt but no manifest shows `?` and not `✗`, and a NEED still counts as not
   passed. Test in Task 4.

---

### Task 1: Declarations, `Need`, and the default file ledger

> **Superseded by the final review of the branch (2026-09-23).** The `open_ledger` default that
> this task plans was withdrawn. `open_ledger` stays fail-closed and still refuses a repository
> without a manifest, as on `main` (spec, decision 5). Without a manifest, the gates read
> `FileLedger(repo / RECEIPTS_DIR)` themselves; with one, they still go through `open_ledger`.
> `test_open_ledger_still_refuses_a_repository_without_a_manifest` (`tests/test_declarations.py`)
> and `test_attest_writes_nothing_without_a_manifest` (`tests/test_cli.py`) pin the refusal. The
> withdrawal also reaches Task 3 (the ledger reads) and Task 5 (mutant 5). The text below is left
> as planned, as the record of the plan.

**Files:**
- Modify: `src/rail/model.py` (add `Declarations`, `declarations`)
- Modify: `src/rail/gates/__init__.py` (`GateResult.needs`, `Need`)
- Modify: `src/rail/ledger/file.py:106-119` (`list` accepts `project=None`)
- Modify: `src/rail/ledger/__init__.py` (`open_ledger`, absent manifest → `FileLedger`)
- Test: `tests/test_declarations.py` (new)

**Interfaces:**
- Produces:
  - `rail.model.Declarations(project: str | None, stack: Stack | None, ledger: LedgerBackend, cfg: RailConfig | None)`, a frozen dataclass;
  - `rail.model.declarations(repo: Path) -> Declarations | str` (a `str` is the manifest problem);
  - `rail.gates.GateResult.needs: str | None = None`, as the last field;
  - `rail.gates.Need(key: str, observed: str)` with `.result(stage: Stage, code: str) -> GateResult`, whose details are ``f"needs `{key}:` — {observed}"``;
  - `FileLedger.list(project: str | None, *, kind=None, attestation=None)`, where `None` means every project in the directory;
  - `open_ledger(repo)`: returns `FileLedger(repo / RECEIPTS_DIR)` when `rail.yaml` is absent, and raises as before when it is invalid.

- [ ] **Step 1: Write the failing tests** — `tests/test_declarations.py`:

```python
"""The partial reading of the manifest (spec 2026-09-23): defaults only when it is absent."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from rail.gates import GateResult, Need, Stage
from rail.ledger import RECEIPTS_DIR, AttestationKind, open_ledger
from rail.ledger.file import FileLedger
from rail.model import Declarations, LedgerBackend, Stack, declarations
from tests.helpers import conforming_tree


def test_an_absent_manifest_gives_the_documented_defaults(tmp_path: Path) -> None:
    assert declarations(tmp_path) == Declarations(
        project=None, stack=None, ledger=LedgerBackend.FILE, cfg=None
    )


def test_a_valid_manifest_gives_its_fields(tmp_path: Path) -> None:
    decl = declarations(conforming_tree(tmp_path, "red-beta", "dev"))
    assert isinstance(decl, Declarations)
    assert (decl.project, decl.stack, decl.ledger) == ("red-beta", Stack.PYTHON, LedgerBackend.FILE)
    assert decl.cfg is not None and decl.cfg.project == "red-beta"


def test_an_invalid_manifest_never_falls_back_to_defaults(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text("rail: 1\nproject: nope\nledger: brain\n")
    problem = declarations(tmp_path)
    assert isinstance(problem, str) and problem.startswith("rail.yaml is invalid: ")
    with pytest.raises(ValidationError):
        open_ledger(tmp_path)


def test_open_ledger_without_a_manifest_is_the_default_file_ledger(tmp_path: Path) -> None:
    ledger = open_ledger(tmp_path)
    assert isinstance(ledger, FileLedger) and ledger.root == tmp_path / RECEIPTS_DIR


def test_file_ledger_lists_every_project_when_none_is_named(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / RECEIPTS_DIR)
    for project, key in (("red-a", "k1"), ("red-b", "k2")):
        ledger.attest(project, AttestationKind.INTEGRATED, {"sha": "0" * 40}, issuer="op",
                      idempotency_key=key)
    assert len(ledger.list(None, attestation=AttestationKind.INTEGRATED)) == 2
    assert len(ledger.list("red-a", attestation=AttestationKind.INTEGRATED)) == 1


def test_need_is_fail_closed_and_names_the_key() -> None:
    result = Need("stack", "no test file found").result(Stage.BUILD, "tests")
    assert result == GateResult(
        Stage.BUILD, "tests", False, "needs `stack:` — no test file found", needs="stack"
    )
    assert result.to_dict()["needs"] == "stack"
    assert GateResult(Stage.BUILD, "tests", True, "ok").to_dict()["needs"] is None
```

`open_ledger` raises Pydantic's `ValidationError` on an invalid manifest. The property under
test is that it never quietly returns a ledger.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_declarations.py`
Expected: FAIL at collection, `ImportError: cannot import name 'Need'` (then `Declarations`).

- [ ] **Step 3: Implement**

In `src/rail/gates/__init__.py`, add the field **after** `skipped` so positional construction
stays valid, and add `Need` below `GateResult`:

```python
    skipped: str | None = None  # why the gate was not evaluated (workstation-only under --ci)
    needs: str | None = None  # the manifest key the verdict depends on — always fail-closed


@dataclass(frozen=True, slots=True)
class Need:
    """The verdict depends on a manifest key that is not declared (spec 2026-09-23): what was
    observed is reported, and the gate stays closed until the key is declared."""

    key: str
    observed: str

    def result(self, stage: Stage, code: str) -> GateResult:
        return GateResult(stage, code, False, f"needs `{self.key}:` — {self.observed}", needs=self.key)
```

In `src/rail/model.py`, add `from dataclasses import dataclass` and, below `manifest_problem`:

```python
@dataclass(frozen=True, slots=True)
class Declarations:
    """What the manifest declares, field by field; with no manifest at all, the documented
    defaults (`ledger: file`) and None where there is no default. `cfg` is the full manifest."""

    project: str | None
    stack: Stack | None
    ledger: LedgerBackend
    cfg: RailConfig | None


def declarations(repo: Path) -> Declarations | str:
    """The manifest's declarations, or why it cannot be read. Defaults apply ONLY when the file
    is absent: an invalid manifest that declares `ledger: brain` must never make a gate judge
    `docs/receipts` as authoritative (spec decision 4)."""
    if not (repo / MANIFEST_NAME).exists():
        return Declarations(project=None, stack=None, ledger=LedgerBackend.FILE, cfg=None)
    cfg = try_load_rail_config(repo)
    if cfg is None:
        return manifest_problem(repo) or f"{MANIFEST_NAME} unreadable"
    return Declarations(project=cfg.project, stack=cfg.stack, ledger=cfg.ledger, cfg=cfg)
```

In `src/rail/ledger/file.py`, change `list`:

```python
    def list(
        self,
        project: str | None,
        *,
        kind: RecordKind | None = None,
        attestation: AttestationKind | None = None,
    ) -> list[Record]:
        """`project=None`: every receipt of the directory — how a gate observes the default
        file ledger of a repository that declares no project."""
        return [
            r
            for r in self._records()
            if (project is None or r.project == project)
            and (kind is None or r.kind is kind)
            and (attestation is None or r.attestation is attestation)
        ]
```

In `src/rail/ledger/__init__.py` `open_ledger`, before `cfg = load_rail_config(repo)`:

```python
    from rail.model import MANIFEST_NAME

    if not (repo / MANIFEST_NAME).exists():
        return FileLedger(repo / RECEIPTS_DIR)  # the documented default, manifest absent only
```

- [ ] **Step 4: Run to verify they pass, and the suite stays green**

Run: `uv run pytest -q tests/test_declarations.py && uv run pytest -q`
Expected: the six new tests PASS, and the full suite reports `passed` with zero failed.

- [ ] **Step 5: Commit** via `/git-commit`, e.g. `✨ feat(model): read the manifest's declarations, defaults only when it is absent`.

---

### Task 2: `build.tests` and `build.lint` need `stack:`

**Files:**
- Modify: `src/rail/gates/build.py:24-79`
- Test: `tests/test_gates_build.py` (the line-34 assertion changes, and one test is added)

**Interfaces:**
- Consumes: `declarations`, `Need` (Task 1).
- Produces: `has_tests` and `lint` return `needs="stack"` when no stack is declared.

- [ ] **Step 1: Write the failing test** — replace `assert "rail.yaml" in has_tests(tmp_path).details` (line 34) with the following, and add the test below:

```python
    assert has_tests(tmp_path).needs == "stack"
```

```python
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
    assert found.needs == "stack" and "1 test file(s) (tests/test_*.py), 0 (*_test.go)" in found.details
    linted = lint(tmp_path)
    assert not linted.passed and linted.needs == "stack"
    assert "ruff configured, no go.mod" in linted.details and "rail.yaml" not in linted.details
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_gates_build.py`
Expected: FAIL — `needs` is `None` and the details still say `rail.yaml is missing`.

- [ ] **Step 3: Implement** — in `build.py`, replace `_stack` with the two observers, and route both gates through `declarations`:

```python
from rail.gates import GateResult, GateSpec, Need, Stage
from rail.model import Stack, declarations


def _python_tests(repo: Path) -> list[Path]:
    root = repo / "tests"
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*.py") if p.name.startswith("test_") or p.name.endswith("_test.py")]


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
```

Keep the existing imports that other functions of `build.py` still use (`effective`, `re`, …).
Remove `MANIFEST_NAME`, `manifest_problem` and `try_load_rail_config` only if ruff reports them
unused.

- [ ] **Step 4: Run to verify**

Run: `uv run pytest -q tests/test_gates_build.py && uv run ruff check src/ tests/`
Expected: every test PASS, and `All checks passed!`.

- [ ] **Step 5: Commit** via `/git-commit`, e.g. `✨ feat(gates): build gates name the stack they need and what they saw`.

---

### Task 3: The ledger gates — evidence, `intent.contract`, `hygiene.mirrors`

**Files:**
- Modify: `src/rail/gates/evidence.py` (`_attestations`, `_absent`, and every caller of `_attestations`/`_newest`)
- Modify: `src/rail/gates/intent.py:14-24`
- Modify: `src/rail/gates/hygiene.py:236-250` (`mirrors`)
- Test: `tests/test_gates_evidence.py` (the line-57 assertion changes, and tests are added)
- Test: `tests/test_gates.py:144-164` (`test_a_missing_manifest_is_named_the_same_way_everywhere` is rewritten)
- Test: `tests/test_gates_docs.py:37-40` (`test_intent_requires_a_readable_manifest` is rewritten)

**Interfaces:**
- Consumes: `declarations`, `Declarations`, `Need`, `FileLedger.list(None, …)`, and `open_ledger` with its default (Task 1).
- Produces: `evidence._attestations(repo, kind) -> list[Record] | str | Need`. Without a declared project it lists `docs/receipts` for every project: `[]` when there are none, a `Need("project", …)` when there are some.

- [ ] **Step 1: Write the failing tests**

In `tests/test_gates_evidence.py`, replace line 57 (`assert "rail.yaml" in verdict(tmp_path).details`) with:

```python
    bare = verdict(tmp_path)
    assert "no review_verdict attestation in docs/receipts (default file ledger)" in bare.details
    assert bare.needs is None and "rail.yaml" not in bare.details
```

and add:

```python
def test_without_a_manifest_receipts_need_the_project(tmp_path: Path) -> None:
    FileLedger(tmp_path / RECEIPTS_DIR).attest(
        "red-beta", AttestationKind.REVIEW_VERDICT, {"sha": "0" * 40}, issuer="op",
        idempotency_key="v1",
    )
    result = verdict(tmp_path)
    assert not result.passed and result.needs == "project"
    assert "1 review_verdict receipt(s) in docs/receipts" in result.details
    assert "rail.yaml" not in result.details


def test_without_a_manifest_every_evidence_gate_names_an_observed_gap(tmp_path: Path) -> None:
    for gate in (verdict, integrated, released, deployed, visible, drill, fulfilled):
        result = gate(tmp_path)
        assert not result.passed and result.needs is None, result
        assert "docs/receipts (default file ledger)" in result.details, result
        assert "rail.yaml" not in result.details, result


def test_an_invalid_manifest_declaring_brain_never_reads_the_receipts(tmp_path: Path) -> None:
    """Review focus 1: the receipts of a brain ledger are mirrors; an invalid manifest must not
    turn them into the authority."""
    FileLedger(tmp_path / RECEIPTS_DIR).attest(
        "red-beta", AttestationKind.REVIEW_VERDICT, {"sha": "0" * 40}, issuer="op",
        idempotency_key="v1",
    )
    (tmp_path / "rail.yaml").write_text("rail: 1\nproject: nope\nledger: brain\n")
    for gate in (verdict, integrated, visible):
        result = gate(tmp_path)
        assert not result.passed and result.needs is None
        assert result.details.startswith("rail.yaml is invalid: "), result


def test_a_tampered_receipt_without_a_manifest_is_a_failure_not_a_need(tmp_path: Path) -> None:
    """Review focus 2."""
    ledger = FileLedger(tmp_path / RECEIPTS_DIR)
    ledger.attest("red-beta", AttestationKind.INTEGRATED, {"sha": "0" * 40}, issuer="op",
                  idempotency_key="i1")
    receipt = next((tmp_path / RECEIPTS_DIR).glob("*.json"))
    receipt.write_text(receipt.read_text().replace("0" * 40, "1" * 40))
    result = integrated(tmp_path)
    assert not result.passed and result.needs is None and "tampered" in result.details
```

In `tests/test_gates.py`, replace `test_a_missing_manifest_is_named_the_same_way_everywhere` with:

```python
def test_only_the_manifest_gate_names_a_missing_manifest(tmp_path: Path) -> None:
    """Spec 2026-09-23 supersedes "one fact, one wording" (red-arena, 2026-09-20) for an ABSENT
    manifest: the manifest gate names it, every other gate names what it observed or the key
    it needs. An INVALID manifest is still named the same way everywhere."""
    from rail.gates import build, evidence, hygiene, intent
    from rail.model import MISSING_HINT

    assert hygiene.rail_config(tmp_path).details == MISSING_HINT
    assert hygiene.mirrors(tmp_path).passed
    assert "file ledger (default)" in hygiene.mirrors(tmp_path).details
    assert "no contract recorded in docs/receipts" in intent.contract(tmp_path).details
    assert build.has_tests(tmp_path).needs == "stack"
    for gate in (hygiene.mirrors, intent.contract, build.has_tests, build.lint, evidence.visible):
        assert "rail.yaml" not in gate(tmp_path).details
    (tmp_path / "rail.yaml").write_text("rail: 1\nproject: nope\n")
    for gate in (hygiene.mirrors, intent.contract, build.has_tests, evidence.verdict):
        result = gate(tmp_path)
        assert not result.passed and "is invalid" in result.details, result
```

In `tests/test_gates_docs.py`, replace `test_intent_requires_a_readable_manifest` with:

```python
def test_intent_without_a_manifest_reads_the_default_file_ledger(tmp_path: Path) -> None:
    result = contract(tmp_path)
    assert result.stage is Stage.INTENT and not result.passed and result.needs is None
    assert "no contract recorded in docs/receipts (default file ledger)" in result.details
    _with_contract(tmp_path)
    assert contract(tmp_path).needs == "project"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_gates_evidence.py tests/test_gates.py tests/test_gates_docs.py`
Expected: FAIL — the details still say `rail.yaml is missing`, and `needs` is `None`.

- [ ] **Step 3: Implement**

`src/rail/gates/evidence.py`: import `Declarations`, `declarations`, `Need`, `RECEIPTS_DIR`,
`FileLedger`, and replace `_attestations`:

```python
def _attestations(repo: Path, kind: AttestationKind) -> list[Record] | str | Need:
    """Chronological attestations of `kind`; the reason they cannot be read; or, with no
    declared project, what the default file ledger holds (spec 2026-09-23): nothing is an
    observed gap, receipts are a `Need` — the rail does not guess whose they are."""
    decl = declarations(repo)
    if isinstance(decl, str):
        return decl
    try:
        if decl.project is None:
            found = FileLedger(repo / RECEIPTS_DIR).list(None, attestation=kind)
            if found:
                return Need(
                    "project",
                    f"{len(found)} {kind.value} receipt(s) in {RECEIPTS_DIR} — `project:` says "
                    "which are this repository's",
                )
            return []
        return open_ledger(repo).list(decl.project, attestation=kind)
    except LedgerError as exc:
        return str(exc)


def _absent(repo: Path, what: str) -> str:
    """`what`, plus where it was looked for when no manifest names the ledger."""
    decl = declarations(repo)
    if isinstance(decl, Declarations) and decl.cfg is None:
        return f"{what} in {RECEIPTS_DIR} (default file ledger)"
    return what
```

Then in every consumer, handle `Need` first, and wrap the "none found" messages in `_absent`:
- `_on_history`: after the `str` check, `if isinstance(records, Need): return records.result(stage, code), None`; and `f"no {kind.value} attestation"` → `_absent(repo, f"no {kind.value} attestation")`.
- `_newest` and `_newest_release_deploy`: return type `Record | None | str | Need`, and pass a `Need` through unchanged.
- `deployed`: `if isinstance(release, Need): return release.result(Stage.DEPLOY, "deployed")`, the same for `deploy`; wrap `"no released attestation to deploy"` and `"no deployed attestation"` in `_absent(repo, …)`.
- `visible`: replace the `try_load_rail_config` block with
  ```python
  decl = declarations(repo)
  if isinstance(decl, str):
      return GateResult(Stage.OBSERVE, "visible", False, decl)
  ```
  then `if isinstance(deploy, Need): return deploy.result(Stage.OBSERVE, "visible")`, and wrap
  `"no deployed attestation to observe"` in `_absent`. Where the code used `cfg.project`, use
  `project = decl.project`, guarded by
  ```python
  if project is None:  # unreachable: no project means _newest returned a Need or None
      return Need("project", "it names the stack red-monitor watches").result(Stage.OBSERVE, "visible")
  ```
- `drill`: `Need` from `_newest_release_deploy` → `.result(Stage.OBSERVE, "drill")`; wrap
  `"no deployed attestation to drill"` in `_absent`; when `rollbacks` or `restores` is a
  `Need`, return its result.
- `fulfilled`: `Need` from `_newest_release_deploy` or `_newest` → `.result(Stage.LEARN, "fulfilled")`; wrap `"no fulfilled attestation"` in `_absent`.

`src/rail/gates/intent.py` `contract`:

```python
def contract(repo: Path) -> GateResult:
    decl = declarations(repo)
    if isinstance(decl, str):
        return GateResult(Stage.INTENT, "contract", False, decl)
    try:
        if decl.project is None:
            found = FileLedger(repo / RECEIPTS_DIR).list(None, kind=RecordKind.CONTRACT)
            if found:
                return Need(
                    "project",
                    f"{len(found)} contract receipt(s) in {RECEIPTS_DIR} — `project:` says "
                    "which are this repository's",
                ).result(Stage.INTENT, "contract")
            return GateResult(
                Stage.INTENT,
                "contract",
                False,
                f"no contract recorded in {RECEIPTS_DIR} (default file ledger)",
            )
        project = decl.project
        ledger = open_ledger(repo)
        records = ledger.list(project, kind=RecordKind.CONTRACT)
        status = ledger.coordination_status()
    except LedgerError as exc:
        return GateResult(Stage.INTENT, "contract", False, str(exc))
    # ... unchanged below, with `cfg.project` replaced by `project`
```

`src/rail/gates/hygiene.py` `mirrors`, head of the function:

```python
    decl = declarations(repo)
    if isinstance(decl, str):
        return GateResult(Stage.HYGIENE, "mirrors", False, decl)
    if decl.ledger is LedgerBackend.FILE:
        where = "file ledger (default)" if decl.cfg is None else "file ledger"
        return GateResult(Stage.HYGIENE, "mirrors", True, f"{where}: the receipts are the ledger")
    cfg = decl.cfg
    assert cfg is not None  # a brain ledger is only ever declared by a manifest
```

- [ ] **Step 4: Run to verify**

Run: `uv run pytest -q && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: the full suite passes with zero failed, `All checks passed!`, and `files already formatted`.

- [ ] **Step 5: Commit** via `/git-commit`, e.g. `✨ feat(gates): ledger gates observe the default file ledger without a manifest`.

---

### Task 4: Rendering — `NEED` in `rail check`, `?` in `rail audit`

**Files:**
- Modify: `src/rail/commands/check.py` (`VERDICTS`, `_verdict`, `report`, summary line)
- Modify: `src/rail/audit.py` (`Status`, `SYMBOLS`, `audit_project`, legend)
- Test: `tests/test_cli.py`, `tests/test_audit.py`

**Interfaces:**
- Consumes: `GateResult.needs` (Task 1), and the gates of Tasks 2–3.
- Produces:
  - JSON: `needs_declaration: list[str]` at the top level of `rail check --json`;
  - text: tag `NEED`, and a summary suffix `— needs a declaration: <ids>`;
  - audit status `"needs"` with symbol `?`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
def test_check_tags_a_needed_declaration_and_lists_it(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "bare", remotes=False)
    out = CliRunner().invoke(main, ["check", "build", "--all", "--repo", str(repo)])
    assert out.exit_code == 1
    assert "NEED  build.tests" in out.output and "NEED  build.lint" in out.output
    assert "— needs a declaration: build.tests, build.lint" in out.output
    data = json.loads(
        CliRunner().invoke(main, ["check", "build", "--all", "--repo", str(repo), "--json"]).output
    )
    assert data["needs_declaration"] == ["build.tests", "build.lint"]
    assert data["passed"] is False
```

(`init_repo` comes from `tests.helpers`: add it to the import.)

`tests/test_audit.py`:

```python
def test_a_stage_that_only_needs_a_declaration_is_marked_apart(tmp_path: Path) -> None:
    """Review focus 5. Undeclared tier = bootstrap (hygiene, intent, design). The only intent
    gate is `intent.contract`: no manifest and one contract receipt make it a NEED."""
    repo = init_repo(tmp_path / "bare", remotes=False)
    FileLedger(repo / RECEIPTS_DIR).contract_set(
        "red-beta",
        Contract(objective="fixture", deliverables=[
            Deliverable(key="main", repository="hawkixs/red-beta", no_checks_reason="fixture")
        ]),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c1",
    )
    audit = audit_project(repo)
    statuses = {s.stage: s.status for s in audit.stages}
    assert statuses["intent"] == "needs"
    assert statuses["hygiene"] == "fail"  # rail_config is a FAIL: one FAIL makes the stage ✗
    intent = next(s for s in audit.stages if s.stage == "intent")
    assert (intent.passed, intent.total) == (0, 1)  # a NEED counts as not passed
    table = render_table([audit])
    assert " ? " in table and "? needs a declaration" in table
```

Imports to add in `tests/test_audit.py`: `from rail.ledger import RECEIPTS_DIR, Contract, Deliverable`
and `from rail.ledger.file import FileLedger`. These are the same names `tests/test_cli.py`
imports for `_bootstrap_repo`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_cli.py tests/test_audit.py`
Expected: FAIL — no `NEED` tag, `KeyError: 'needs_declaration'`, `SYMBOLS` has no `needs`.

- [ ] **Step 3: Implement**

`check.py`:

```python
VERDICTS = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "exception": "EXC ", "need": "NEED"}


def _verdict(result: GateResult) -> str:
    if result.skipped:
        return VERDICTS["skip"]
    if result.exception:
        return VERDICTS["exception"]
    if result.needs:
        return VERDICTS["need"]
    return VERDICTS["pass"] if result.passed else VERDICTS["fail"]
```

In `report(...)`, add `"needs_declaration": [r.gate_id for r in results if r.needs],`. In the
text branch, build the summary line:

```python
        counted = [r for r in results if not r.skipped]
        summary = f"passed {sum(r.passed for r in counted)}/{len(counted)}"
        if payload["needs_declaration"]:
            summary += f" — needs a declaration: {', '.join(payload['needs_declaration'])}"
        click.echo(summary)
```

If PR #33 has merged first, keep its `not_evaluated` suffix and join the two with `; `.

`audit.py`:

```python
Status = Literal["pass", "fail", "needs", "exception", "n/a"]
SYMBOLS: dict[str, str] = {"pass": "✓", "fail": "✗", "needs": "?", "exception": "!", "n/a": "·"}
```

In `audit_project`, replace `status = "fail"` with:

```python
        else:
            failing = [r for r in mine if not r.passed]
            status = "needs" if all(r.needs for r in failing) else "fail"
```

Legend:
`"✓ pass   ✗ fail   ? needs a declaration   ! declared exception   · not applicable to the tier   tier? undeclared"`.

- [ ] **Step 4: Run to verify**

Run: `uv run pytest -q`
Expected: the full suite passes, and `test_matrix_matches_the_golden_snapshot` passes unchanged.

- [ ] **Step 5: Commit** via `/git-commit`, e.g. `✨ feat(check): render a needed declaration apart from a failure`.

---

### Task 5: Red's acceptance criterion, non-regression and counter-proof

**Files:**
- Test: `tests/test_cli.py`
- No production code, unless a test exposes a gap. In that case, fix it in the gate concerned and say so in the commit.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the acceptance tests**

```python
def _verdict_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line[:4] in ("PASS", "FAIL", "NEED", "SKIP", "EXC ")]


@pytest.mark.parametrize("ci", [False, True])
def test_without_a_manifest_only_the_manifest_gate_names_it(tmp_path: Path, ci: bool) -> None:
    """Ticket 2cbbdd22, criterion 1 of 513e109b — red runs the same check itself."""
    repo = init_repo(tmp_path / "bare", remotes=False)
    args = ["check", "--all", "--repo", str(repo)] + (["--ci"] if ci else [])
    out = CliRunner().invoke(main, args)
    assert out.exit_code == 1
    lines = _verdict_lines(out.output)
    naming = [line for line in lines if "rail.yaml" in line]
    assert len(naming) == 1 and "hygiene.rail_config" in naming[0], naming
    for line in lines:
        if line.startswith("NEED"):
            assert "needs `" in line, line
    assert {line.split()[1] for line in lines if line.startswith("NEED")} == {"build.tests", "build.lint"}
    ledger_gates = ("intent.contract", "review.verdict", "integrate.receipt", "release.released",
                    "deploy.deployed", "observe.drill", "learn.fulfilled")
    for gate_id in ledger_gates:  # review focus 3: the file default runs under --ci too
        line = next(line for line in lines if line.split()[1] == gate_id)
        assert line.startswith("FAIL") and "docs/receipts" in line, line


def test_a_manifest_never_yields_a_need(tmp_path: Path) -> None:
    """Review focus 4: with a manifest present nothing changes."""
    repo = _bootstrap_repo(tmp_path)
    data = json.loads(CliRunner().invoke(main, ["check", "--repo", str(repo), "--json"]).output)
    assert data["needs_declaration"] == []
    assert all(g["needs"] is None for g in data["gates"])
```

`observe.visible` is a workstation gate. Under `--ci` it is SKIP, and locally it is FAIL on
`no deployed attestation to observe in docs/receipts`. That is why it is not in `ledger_gates`.

- [ ] **Step 2: Run** — `uv run pytest -q tests/test_cli.py`
Expected: PASS. If one fails, the failing line names the gate: fix that gate, not the test.

- [ ] **Step 3: Mutation counter-proof** — apply each mutant alone to the working tree, run `uv run pytest -q`, record which test goes red, and restore with `git checkout -- src/`:
  1. `declarations`: return the defaults for an invalid manifest too (drop the `try_load_rail_config` branch). Expected red: `test_an_invalid_manifest_never_falls_back_to_defaults` and `test_an_invalid_manifest_declaring_brain_never_reads_the_receipts`.
  2. `Need.result`: `passed=True`. Expected red: `test_need_is_fail_closed_and_names_the_key`.
  3. `has_tests`: turn the `Need` into a plain `GateResult(..., False, observed)`. Expected red: `test_without_a_manifest_tests_and_lint_need_the_stack_and_say_what_they_saw` and the acceptance test.
  4. `_absent`: return `f"{what} — rail.yaml is missing"`. Expected red: the acceptance test.
  5. `open_ledger`: drop the default (load the manifest unconditionally). Expected red: `test_open_ledger_without_a_manifest_is_the_default_file_ledger`.

  A mutant that survives means a test is missing. Add it before going on.

- [ ] **Step 4: Full proof**

Run: `make ci`
Expected: `All checks passed!`, `N passed` with zero failed, `rail check` `passed 18/18`, exit 0.

Run from this worktree: `./.venv/bin/rail check --repo ~/hawkixs_infra/git_repo/ReD_v1/projects/red-alerts`
Expected: `passed 18/18` (success criterion 2, the real checkout).

Run: `D=$(mktemp -d) && git -C $D init -q && ./.venv/bin/rail check --all --repo $D; echo exit=$?`
Expected: exactly one line containing `rail.yaml` (`hygiene.rail_config`), `NEED` on
`build.tests` and `build.lint`, `PASS  hygiene.mirrors … file ledger (default)`, and `exit=1`.
Paste this output into the PR description: it is what red will run.

- [ ] **Step 5: Commit** via `/git-commit`, e.g. `✅ test(check): red's criterion — only the manifest gate names a missing manifest`.
