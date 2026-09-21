# red-rail phase 1 — the rail without network — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans-parallel` to dispatch tasks in batches via TeamCreate.

**Goal:** Deliver phase 1 of the design spec [docs/specs/2026-09-14-red-rail-design.md](../specs/2026-09-14-red-rail-design.md) §8: every gate as a pure function, `rail check` scored against the declared tier, the `Ledger` protocol with `FileLedger`, `rail attest` / `rail contract set` / `rail metrics` (four DORA + conformance) without brain, `rail audit` producing the repository × stage matrix over the 24 ReD projects, a copier template with `rail new` / `rail upgrade`, a reusable `rail-ci.yml`, and facade skills — then red-rail dogfooded at tier `dev` on a file ledger.
**Test command:** `cd <ReD_ROOT>/ReD_v1/projects/red-rail && make lint test`
**Tech Stack:** Python 3.12, uv, Click 8, Pydantic 2, PyYAML, copier 9.18, pytest, ruff; git ≥ 2.28 and gitleaks 8.30 on the host.

**Branch:** all tasks commit on `feat/phase-1-rail-without-network`, created from `main@d7a11a9`; this plan is its first commit. CI on that branch is expected red between Batch 3 and Batch 5 (new gates arrive before red-rail's own receipts do); the PR is opened after Batch 5 with `make ci` green.

**Lint rule for every task:** `ruff format` wraps calls but not string literals; when `ruff check` reports E501 on a literal, split it with implicit concatenation rather than adding `# noqa`.

**Language:** everything committed is in English (commits, code, comments, docs, test names).

## Scope decisions frozen for this plan (measured on 2026-09-15)

- **Checkpoint command is `make lint test`, not `make ci`**: `make ci` runs `rail check` on red-rail itself, which cannot pass at tier `dev` until Batch 5 records red-rail's own contract and evidence. Batch 5 ends with `make ci` green.
- **Gate signature stays `fn(repo: Path) -> GateResult`** (spec §5). Policy (tier defaults, `gates:` overrides) is read from the repository itself via `rail.policy.effective(repo, key)`, so a gate remains a pure function of the repository state.
- **Declared exceptions**: a `gates:` entry whose `value` is `false` disables that gate; the result is reported as `passed=True` with `exception=<reason>` — visible in `rail check` and `rail audit`, never hidden. Other keys are typed parameters (`build.commit_window`).
- **Two gate scopes**: `repo` (any checkout) and `workstation` (needs the operator's clone: `hygiene.remotes`, `hygiene.roster_entry`). `rail check --ci` reports workstation gates as skipped (`skipped="workstation"`), explicitly, because a CI checkout has one remote and no ReD root.
- **Ledger gates on a moving HEAD**: `review.verdict`, `integrate.receipt`, `release.released` pass when the newest matching attestation's `sha` is an ancestor of HEAD; the distance in commits is reported in `details` so drift is measured, not hidden.
- **`build` gate is static**: tests present, lint configured, gitleaks clean (`gitleaks git --no-banner --redact --exit-code 2 <repo>` — verified on this host, rc 0 = clean, 2 = leaks, 1 = error), conventional-commit subjects. "Tests cover the diff" stays the CI job's job (`make ci` exit code); "English" is not machine-checked and the gate says so.
- **Reusable workflow lives at `.github/workflows/rail-ci.yml`** (GitHub only calls reusable workflows from that directory); `workflows/` keeps `pre-review.js` for phase 2. The spec is amended in Batch 5.
- **Remote creation uses `gh` and `glab`** (both installed and authenticated on the host: `gh 2.96`, `glab 1.117` logged in to the mirror), never tokens in Python — the neighbours' way (`runnerctl`, kickstart runbook `a050e6ec`).
- **copier source** = the red-rail git repository (`copier.yml` at its root, `_subdirectory: template/project`), pinned by tag through `.copier-answers.yml` (`_commit`); tests render from a plain directory copy so uncommitted template changes are visible.
- **red-rail dogfoods `ledger: file`** from Batch 5 (ADR-0002: standalone first); `ledger: brain` returns to the manifest with `BrainLedger` in phase 2. `review.verdict` is a declared exception on red-rail until the independent reviewer exists (ADR-0003).
- **Golden matrix** is computed over deterministic fixture projects (fixed commit dates → fixed SHAs, injected clock → fixed receipts); the real 24-project day-0 snapshot is committed under `docs/audits/` as evidence, and a test checks its shape (24 projects, schema), not its content, because the neighbours change daily.

## Shared API (every task below inlines what it needs; this table is the alignment reference)

| Module | Public surface after this plan |
|---|---|
| `rail.gates` | `Stage`, `Scope`, `GateResult(stage, code, passed, details, exception=None, skipped=None)`, `GateSpec(stage, code, fn, scope="repo")`, `registry()`, `run_gate(spec, repo, *, ci=False)`, `run_gates(repo, *, stages=None, ci=False)` |
| `rail.policy` | `TIER_STAGES`, `REQUIRED_SPEC_SECTIONS`, `GATE_DEFAULTS`, `stages_for(tier)`, `declared_tier(repo)`, `applicable_stages(repo)`, `effective(repo, key) -> (value, reason)` |
| `rail.model` | unchanged except `Ledger` enum renamed `LedgerBackend` |
| `rail.ledger` | `RecordKind`, `AttestationKind`, `RequiredCheck`, `ReviewPolicy`, `Deliverable`, `Contract`, `PullRequestRef`, `Record`, `Ledger` (Protocol), `LedgerError`, `LedgerUnavailable`, `IdempotencyConflict`, `canonical_json`, `compute_digest`, `RECEIPTS_DIR`, `open_ledger(repo)` |
| `rail.ledger.file` | `FileLedger(root, *, clock=None)`, `receipt_filename(record)`, `load_receipt(path)` |
| `rail.gitrepo` | `is_git_repo`, `head_sha`, `is_ancestor`, `distance`, `remotes`, `recent_subjects`, `commit_timestamp`, `latest_tag` |
| `rail.markdown` | `headings`, `has_section`, `fenced_blocks`, `command_lines`, `latest_doc`, `spec_references`, `task_sections`, `has_verification` |
| `rail.gates.<stage>` | `hygiene`, `intent`, `design`, `plan`, `build`, `evidence` — each exports `GATES: list[GateSpec]` |
| `rail.commands.<name>` | one module per CLI command exporting `command` (auto-discovered by `rail.cli`): `check`, `attest`, `contract`, `ledger`, `audit`, `metrics`, `new`, `upgrade` |
| `rail.audit`, `rail.metrics`, `rail.scaffold`, `rail.remotes` | see Batch 4 |
| `tests/helpers.py` | `git`, `init_repo`, `commit_all`, `write_manifest`, `write_roster`, `conforming_tree`, `MINIMAL_MANIFEST` |

Attestation payload conventions (read by the gates and by `rail metrics`): `sha` (commit the evidence is about), `independent` (bool, `review_verdict`), `verdict` (`approve` | `request_changes`), `version` + `digest` (`released`, `deployed`), `drill` (bool, `rolled_back` / `restored`).

---

## Batch 1: Foundations — policy, ledger, git, markdown, test helpers (parallel)

### Task 1.1: Gate runner with declared exceptions and scopes + tier policy (`rail.policy`)

**Files:**
- Modify: `src/rail/gates/__init__.py` (full rewrite below)
- Modify: `src/rail/gates/hygiene.py` (append `GATES`)
- Create: `src/rail/policy.py`
- Create: `tests/test_policy.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_policy.py`:
```python
"""Tier defaults live in red-rail; a manifest only declares overrides, each with a reason."""

from pathlib import Path

from rail.gates import GateResult, GateSpec, Stage, run_gate, run_gates
from rail.model import Tier
from rail.policy import GATE_DEFAULTS, applicable_stages, declared_tier, effective, stages_for

MINIMAL = "rail: 1\nproject: red-probe\nbrain_key: red-probe\ntier: dev\nstack: python\n"


def _manifest(tmp_path: Path, body: str) -> Path:
    (tmp_path / "rail.yaml").write_text(body)
    return tmp_path


def test_each_tier_includes_the_previous_one() -> None:
    assert set(stages_for(Tier.BOOTSTRAP)) < set(stages_for(Tier.DEV)) < set(stages_for(Tier.PROD))
    assert stages_for(Tier.PROD) == tuple(Stage)


def test_bootstrap_is_hygiene_intent_design() -> None:
    assert stages_for(Tier.BOOTSTRAP) == (Stage.HYGIENE, Stage.INTENT, Stage.DESIGN)


def test_declared_tier_is_none_without_a_readable_manifest(tmp_path: Path) -> None:
    assert declared_tier(tmp_path) is None
    _manifest(tmp_path, "rail: 1\nproject: nope\n")
    assert declared_tier(tmp_path) is None


def test_applicable_stages_fall_back_to_bootstrap(tmp_path: Path) -> None:
    assert applicable_stages(tmp_path) == stages_for(Tier.BOOTSTRAP)
    _manifest(tmp_path, MINIMAL)
    assert applicable_stages(tmp_path) == stages_for(Tier.DEV)


def test_effective_returns_the_default_without_override(tmp_path: Path) -> None:
    _manifest(tmp_path, MINIMAL)
    assert effective(tmp_path, "build.commit_window") == (GATE_DEFAULTS["build.commit_window"], None)
    assert effective(tmp_path, "review.verdict") == (True, None)


def test_effective_returns_the_override_and_its_reason(tmp_path: Path) -> None:
    _manifest(tmp_path, MINIMAL + "gates:\n  review.verdict:\n    value: false\n    reason: solo spike\n")
    assert effective(tmp_path, "review.verdict") == (False, "solo spike")


def test_run_gate_turns_a_false_override_into_a_declared_exception(tmp_path: Path) -> None:
    _manifest(tmp_path, MINIMAL + "gates:\n  review.verdict:\n    value: false\n    reason: solo spike\n")
    spec = GateSpec(
        Stage.REVIEW, "verdict", lambda repo: GateResult(Stage.REVIEW, "verdict", False, "none")
    )
    result = run_gate(spec, tmp_path)
    assert result.passed is True
    assert result.exception == "solo spike"
    assert result.to_dict()["exception"] == "solo spike"


def test_run_gate_skips_workstation_gates_under_ci(tmp_path: Path) -> None:
    spec = GateSpec(
        Stage.HYGIENE,
        "remotes",
        lambda repo: GateResult(Stage.HYGIENE, "remotes", False, "one remote"),
        scope="workstation",
    )
    assert run_gate(spec, tmp_path, ci=True).skipped == "workstation"
    assert run_gate(spec, tmp_path).passed is False


def test_run_gate_reports_a_crash_as_a_failed_gate(tmp_path: Path) -> None:
    def boom(repo: Path) -> GateResult:
        raise RuntimeError("bug in the gate")

    result = run_gate(GateSpec(Stage.BUILD, "tests", boom), tmp_path)
    assert result.passed is False
    assert "RuntimeError" in result.details


def test_run_gates_filters_by_stage(tmp_path: Path) -> None:
    assert run_gates(tmp_path, stages=[Stage.DESIGN]) == []
    assert [r.code for r in run_gates(tmp_path, stages=[Stage.HYGIENE])] == [
        "rail_config",
        "docs_layout",
    ]
```

- [ ] **Step 2: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_policy.py -q
```
Expected: `ImportError` (no `rail.policy`, no `GateSpec`).

- [ ] **Step 3: Rewrite `src/rail/gates/__init__.py`**

```python
"""Gates are pure functions of the repository state.

Each gate returns a `GateResult`; it never raises. The same function runs locally
(pre-push), in CI, and behind a Claude Code skill, so the policy exists in one place.

A gate is registered as a `GateSpec` (stage, code, function, scope). `run_gate` applies the
manifest's declared exceptions (`gates:` in `rail.yaml`, reason mandatory) and the `--ci`
scope rule, and turns an unexpected crash into a failed result — visibly, never silently.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal


class Stage(StrEnum):
    HYGIENE = "hygiene"
    INTENT = "intent"
    DESIGN = "design"
    PLAN = "plan"
    BUILD = "build"
    REVIEW = "review"
    INTEGRATE = "integrate"
    RELEASE = "release"
    DEPLOY = "deploy"
    OBSERVE = "observe"
    LEARN = "learn"


Scope = Literal["repo", "workstation"]


@dataclass(frozen=True, slots=True)
class GateResult:
    stage: Stage
    code: str
    passed: bool
    details: str
    exception: str | None = None  # reason of a declared exception (`gates:` in rail.yaml)
    skipped: str | None = None  # why the gate was not evaluated (workstation-only under --ci)

    @property
    def gate_id(self) -> str:
        return f"{self.stage.value}.{self.code}"

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["stage"] = self.stage.value
        return data


@dataclass(frozen=True, slots=True)
class GateSpec:
    stage: Stage
    code: str
    fn: Callable[[Path], GateResult]
    scope: Scope = "repo"  # "workstation": needs the operator's clone (remotes, roster)

    @property
    def gate_id(self) -> str:
        return f"{self.stage.value}.{self.code}"


def registry() -> list[GateSpec]:
    """Gates in evaluation order. Imported lazily so `rail.gates` stays dependency-free."""
    from rail.gates import hygiene

    return list(hygiene.GATES)


def run_gate(spec: GateSpec, repo: Path, *, ci: bool = False) -> GateResult:
    from rail.policy import effective

    if ci and spec.scope == "workstation":
        return GateResult(
            spec.stage,
            spec.code,
            True,
            "not evaluated: workstation-only gate under --ci",
            skipped="workstation",
        )
    value, reason = effective(repo, spec.gate_id)
    if value is False:
        return GateResult(
            spec.stage, spec.code, True, f"declared exception: {reason}", exception=reason
        )
    try:
        return spec.fn(repo)
    except Exception as exc:  # a gate never raises: a crash is a failed gate, visibly
        return GateResult(
            spec.stage, spec.code, False, f"gate crashed: {type(exc).__name__}: {exc}"
        )


def run_gates(
    repo: Path, *, stages: Iterable[Stage] | None = None, ci: bool = False
) -> list[GateResult]:
    wanted = None if stages is None else set(stages)
    return [
        run_gate(spec, repo, ci=ci)
        for spec in registry()
        if wanted is None or spec.stage in wanted
    ]
```

- [ ] **Step 4: Append the registry list to `src/rail/gates/hygiene.py`**

Add `GateSpec` to the existing import (`from rail.gates import GateResult, GateSpec, Stage`) and append at the end of the file:
```python
GATES = [
    GateSpec(Stage.HYGIENE, "rail_config", rail_config),
    GateSpec(Stage.HYGIENE, "docs_layout", docs_layout),
]
```

- [ ] **Step 5: Create `src/rail/policy.py`**

```python
"""Tier defaults, versioned here so twenty manifests never diverge.

Everything a `rail.yaml` does not declare comes from this module. A `gates:` entry in the
manifest overrides one key and must carry a reason; `effective()` returns both so the
audit can show the exception instead of hiding it. A key is either a gate id
(`stage.code`, boolean, default True — `false` disables the gate as a declared exception)
or a typed parameter (`build.commit_window`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rail.gates import Stage
from rail.model import Tier, load_rail_config

TIER_STAGES: dict[Tier, tuple[Stage, ...]] = {
    Tier.BOOTSTRAP: (Stage.HYGIENE, Stage.INTENT, Stage.DESIGN),
    Tier.DEV: (
        Stage.HYGIENE,
        Stage.INTENT,
        Stage.DESIGN,
        Stage.PLAN,
        Stage.BUILD,
        Stage.REVIEW,
        Stage.INTEGRATE,
    ),
    Tier.PROD: tuple(Stage),
}

# Sections the design gate requires in a spec, with the heading aliases in use across ReD.
REQUIRED_SPEC_SECTIONS: dict[str, tuple[str, ...]] = {
    "problem": ("problem", "problème", "probleme", "context", "contexte"),
    "decisions": ("decision", "décision"),
    "non-goals": (
        "non-goal",
        "non goal",
        "out of scope",
        "hors périmètre",
        "hors perimetre",
        "non-objectif",
    ),
    "success criteria": (
        "success criteri",
        "critères de succès",
        "criteres de succes",
        "critère de succès",
    ),
}

GATE_DEFAULTS: dict[str, Any] = {
    "hygiene.canonical_host": "github.com",
    "hygiene.mirror_host": "gitlab.hawkixs.local",
    "build.commit_window": 20,
    "build.conventional_types": (
        "feat",
        "fix",
        "docs",
        "chore",
        "refactor",
        "test",
        "ci",
        "build",
        "perf",
        "style",
        "revert",
    ),
}


def stages_for(tier: Tier) -> tuple[Stage, ...]:
    return TIER_STAGES[tier]


def declared_tier(repo: Path) -> Tier | None:
    """The tier written in `rail.yaml`, or None when the manifest is missing or invalid."""
    try:
        return load_rail_config(repo).tier
    except (FileNotFoundError, ValidationError):
        return None


def applicable_stages(repo: Path) -> tuple[Stage, ...]:
    """Stages a repository is scored against: its declared tier, else the `bootstrap` floor."""
    return stages_for(declared_tier(repo) or Tier.BOOTSTRAP)


def effective(repo: Path, key: str) -> tuple[Any, str | None]:
    """(value, reason): the manifest override when declared, else the versioned default."""
    default = GATE_DEFAULTS.get(key, True)
    try:
        cfg = load_rail_config(repo)
    except (FileNotFoundError, ValidationError):
        return default, None
    override = cfg.gates.get(key)
    if override is None:
        return default, None
    return override.value, override.reason
```

- [ ] **Step 6: Run the tests, expect PASS (existing suites still green)**

```bash
uv run pytest -q
```
Expected: `33 passed` (22 existing + 11 new), no failure.

- [ ] **Step 7: Lint**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```
Expected: `All checks passed!` twice. If `ruff format --check` lists files, run `uv run ruff format src/ tests/` and re-check.

- [ ] **Step 8: Commit**

```bash
git add src/rail/gates/__init__.py src/rail/gates/hygiene.py src/rail/policy.py tests/test_policy.py
git commit -m "feat(policy): tier defaults, declared exceptions and gate scopes in the runner"
```

### Task 1.2: `Ledger` protocol, record model and `FileLedger` (receipts are the ledger)

**Files:**
- Create: `src/rail/ledger/__init__.py`
- Create: `src/rail/ledger/file.py`
- Create: `tests/ledger_contract.py` (shared contract suite, reused for `BrainLedger` in phase 2)
- Create: `tests/test_ledger_file.py`
- Modify: `src/rail/model.py` (rename enum `Ledger` → `LedgerBackend`, the name `Ledger` is the protocol)
- Modify: `tests/test_model.py` (same rename)

- [ ] **Step 1: Rename the manifest enum**

In `src/rail/model.py` rename `class Ledger(StrEnum)` to `class LedgerBackend(StrEnum)` and the field to `ledger: LedgerBackend = LedgerBackend.FILE`. In `tests/test_model.py` replace `Ledger` by `LedgerBackend` in the import and in the three `test_ledger_*` tests (`cfg.ledger is LedgerBackend.FILE`, `LedgerBackend.BRAIN`).

```bash
uv run pytest tests/test_model.py -q
```
Expected: `12 passed`.

- [ ] **Step 2: Write the shared contract suite**

`tests/ledger_contract.py`:
```python
"""The contract every ledger backend must satisfy. Phase 2 runs it against `BrainLedger`."""

from pathlib import Path

import pytest

from rail.ledger import (
    AttestationKind,
    Contract,
    Deliverable,
    IdempotencyConflict,
    Ledger,
    PullRequestRef,
    RecordKind,
)

CONTRACT = Contract(
    objective="ship the probe",
    acceptance_criteria=["/healthz answers 200"],
    deliverables=[Deliverable(key="probe", repository="hawkixs/red-probe")],
)
PR = PullRequestRef(repository="hawkixs/red-probe", number=7, head_sha="a" * 40)


class LedgerContract:
    """Subclass, implement `make_ledger`, inherit every test."""

    def make_ledger(self, tmp_path: Path) -> Ledger:
        raise NotImplementedError

    def test_attest_returns_a_verified_record(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        record = ledger.attest(
            "red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1"
        )
        assert record.kind is RecordKind.ATTESTATION
        assert record.attestation is AttestationKind.DEPLOYED
        assert record.data == {"sha": "b" * 40}
        assert record.digest.startswith("sha256:") and record.verify()

    def test_replay_with_same_key_and_content_is_idempotent(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        first = ledger.attest("red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1")
        again = ledger.attest("red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1")
        assert again.digest == first.digest
        assert len(ledger.list("red-probe")) == 1

    def test_same_key_with_different_content_is_a_conflict(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        ledger.attest("red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1")
        with pytest.raises(IdempotencyConflict):
            ledger.attest("red-probe", AttestationKind.DEPLOYED, {"sha": "c" * 40}, issuer="op", idempotency_key="d1")

    def test_contract_set_and_bind_are_records_of_their_kind(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        contract = ledger.contract_set("red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c1")
        binding = ledger.bind("red-probe", PR, issuer="op", idempotency_key="b1")
        assert contract.kind is RecordKind.CONTRACT
        assert contract.payload["contract"]["objective"] == "ship the probe"
        assert contract.payload["reason"] == "bootstrap"
        assert binding.kind is RecordKind.BINDING
        assert binding.payload["number"] == 7

    def test_list_is_chronological_and_filters(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        ledger.contract_set("red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c1")
        ledger.attest("red-probe", AttestationKind.RELEASED, {"version": "1.0.0"}, issuer="op", idempotency_key="r1")
        ledger.attest("red-probe", AttestationKind.DEPLOYED, {"digest": "sha256:x"}, issuer="op", idempotency_key="d1")
        kinds = [r.kind for r in ledger.list("red-probe")]
        assert kinds == [RecordKind.CONTRACT, RecordKind.ATTESTATION, RecordKind.ATTESTATION]
        assert [r.attestation for r in ledger.list("red-probe", kind=RecordKind.ATTESTATION)] == [
            AttestationKind.RELEASED,
            AttestationKind.DEPLOYED,
        ]
        assert len(ledger.list("red-probe", attestation=AttestationKind.DEPLOYED)) == 1

    def test_get_by_digest(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        record = ledger.attest("red-probe", AttestationKind.FULFILLED, {}, issuer="op", idempotency_key="f1")
        assert ledger.get("red-probe", record.digest) == record
        assert ledger.get("red-probe", "sha256:" + "0" * 64) is None

    def test_projects_are_isolated(self, tmp_path: Path) -> None:
        ledger = self.make_ledger(tmp_path)
        ledger.attest("red-probe", AttestationKind.DEPLOYED, {}, issuer="op", idempotency_key="d1")
        assert ledger.list("red-other") == []
```

- [ ] **Step 3: Write the file-backend tests**

`tests/test_ledger_file.py`:
```python
"""`FileLedger`: `docs/receipts/*.json` are the ledger — append-only, digested, idempotent."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pydantic import ValidationError

from rail.ledger import (
    RECEIPTS_DIR,
    AttestationKind,
    Ledger,
    LedgerError,
    LedgerUnavailable,
    RequiredCheck,
    open_ledger,
)
from rail.ledger.file import FileLedger, load_receipt, receipt_filename
from tests.ledger_contract import LedgerContract

T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
MANIFEST = "rail: 1\nproject: red-probe\nbrain_key: red-probe\ntier: dev\nstack: python\n"


def _clock(start: datetime = T0):
    ticks = [start + timedelta(seconds=i) for i in range(100)]
    return lambda: ticks.pop(0)


class TestFileLedgerContract(LedgerContract):
    def make_ledger(self, tmp_path: Path) -> Ledger:
        return FileLedger(tmp_path / RECEIPTS_DIR, clock=_clock())


def test_receipt_filename_is_timestamp_kind_digest(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path, clock=_clock())
    record = ledger.attest("red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1")
    name = receipt_filename(record)
    assert name == f"20260915T080000Z-deployed-{record.digest[7:19]}.json"
    assert (tmp_path / name).is_file()


def test_receipts_are_append_only(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path, clock=_clock())
    first = ledger.attest("red-probe", AttestationKind.RELEASED, {"version": "1.0.0"}, issuer="op", idempotency_key="r1")
    before = (tmp_path / receipt_filename(first)).read_bytes()
    ledger.attest("red-probe", AttestationKind.DEPLOYED, {"digest": "sha256:x"}, issuer="op", idempotency_key="d1")
    assert (tmp_path / receipt_filename(first)).read_bytes() == before
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_receipt_round_trips_through_json(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path, clock=_clock())
    record = ledger.attest("red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1")
    loaded = load_receipt(tmp_path / receipt_filename(record))
    assert loaded == record and loaded.verify()
    raw = json.loads((tmp_path / receipt_filename(record)).read_text())
    assert set(raw) == {"schema_version", "kind", "project", "recorded_at", "issuer", "idempotency_key", "payload", "digest"}


def test_a_tampered_receipt_fails_verification(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path, clock=_clock())
    record = ledger.attest("red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1")
    path = tmp_path / receipt_filename(record)
    raw = json.loads(path.read_text())
    raw["payload"]["data"]["sha"] = "c" * 40
    path.write_text(json.dumps(raw))
    assert load_receipt(path).verify() is False


def test_a_malformed_receipt_is_a_broken_ledger(tmp_path: Path) -> None:
    (tmp_path / "junk.json").write_text("{not json")
    with pytest.raises(LedgerError, match="junk.json"):
        FileLedger(tmp_path).list("red-probe")


def test_open_ledger_reads_the_manifest(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text(MANIFEST)
    ledger = open_ledger(tmp_path)
    assert isinstance(ledger, FileLedger)
    assert ledger.root == tmp_path / RECEIPTS_DIR


def test_required_check_names_its_publisher() -> None:
    check = RequiredCheck(name="red-rail/review", app_slug="red-rail-reviewer")
    assert check.kind == "check_run" and check.provider_id is None
    assert RequiredCheck(kind="commit_status", name="ci", provider_id=42).app_slug is None
    with pytest.raises(ValidationError, match="publisher"):
        RequiredCheck(name="anonymous")


def test_open_ledger_refuses_brain_until_phase_2(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text(MANIFEST + "ledger: brain\n")
    with pytest.raises(LedgerUnavailable, match="phase 2"):
        open_ledger(tmp_path)
```

- [ ] **Step 4: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_ledger_file.py -q
```
Expected: `ModuleNotFoundError: No module named 'rail.ledger'`.

- [ ] **Step 5: Create `src/rail/ledger/__init__.py`**

```python
"""The ledger protocol: where evidence is authoritative (ADR-0001, ADR-0002).

Two backends implement the same protocol. `FileLedger` (default): the repository's
`docs/receipts/*.json` are the ledger — append-only, committed, no network. `BrainLedger`
(phase 2): brain-v42 is the shared authority and the receipts become mirrors.

Every record carries a digest (sha256 over its canonical JSON) and an idempotency key:
replaying a write with the same key and the same content returns the existing record; the
same key with different content is a conflict, never a silent overwrite.

Attestation payload conventions read by the gates and by `rail metrics`:
  sha          git commit the evidence is about (review_verdict, integrated, released, deployed)
  independent  bool — review_verdict only; a pre-review from the producing session is False
  verdict      "approve" | "request_changes" — review_verdict only
  version      released; digest — released and deployed (artefact digest)
  drill        bool — rolled_back / restored produced by the rollback drill
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

RECEIPTS_DIR = "docs/receipts"
SCHEMA_VERSION = 1


class RecordKind(StrEnum):
    CONTRACT = "contract"
    BINDING = "binding"
    ATTESTATION = "attestation"


class AttestationKind(StrEnum):
    GATE_PASSED = "gate_passed"
    REVIEW_VERDICT = "review_verdict"
    INTEGRATED = "integrated"
    RELEASED = "released"
    DEPLOYED = "deployed"
    ROLLED_BACK = "rolled_back"
    INCIDENT_DETECTED = "incident_detected"
    RESTORED = "restored"
    FULFILLED = "fulfilled"


class LedgerError(Exception):
    """The ledger cannot answer: unreadable receipt, unreachable backend."""


class LedgerUnavailable(LedgerError):
    """The configured backend is not available in this rail version."""


class IdempotencyConflict(LedgerError):
    """An idempotency key was reused with different content."""


class RequiredCheck(BaseModel):
    """A trusted check selector as brain-v42's evaluator compares it: kind + name + the App
    that publishes it (`app_slug` or numeric `provider_id`) — no App identity is ever wired
    in code, so a third-party App's check is first-class (ticket 04bc1f4a, ADR-0001 am. 4)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["check_run", "commit_status"] = "check_run"
    name: str = Field(min_length=1, max_length=200)
    app_slug: str | None = Field(default=None, min_length=1, max_length=200)
    provider_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _publisher_named(self) -> RequiredCheck:
        if self.app_slug is None and self.provider_id is None:
            raise ValueError("a required check names its publisher: app_slug or provider_id")
        return self


class ReviewPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_approvals: int = Field(default=1, ge=0, le=100)
    allowed_reviewers: list[str] = Field(default_factory=list, max_length=200)


class Deliverable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    repository: str = Field(min_length=3, max_length=201)  # owner/name, as brain-v42 expects
    target_branch: str = "main"
    required_checks: list[RequiredCheck] = Field(default_factory=list, max_length=100)
    review: ReviewPolicy = Field(default_factory=ReviewPolicy)


class Contract(BaseModel):
    """Mirrors the `contract` argument of `brain_delivery_contract_set`, so phase 2 maps 1:1."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    objective: str = Field(min_length=1, max_length=8000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=200)
    constraints: list[str] = Field(default_factory=list, max_length=200)
    deliverables: list[Deliverable] = Field(min_length=1, max_length=20)


class PullRequestRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=3)
    number: int = Field(gt=0)
    head_sha: str = Field(min_length=7, max_length=64)


def canonical_json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def compute_digest(fields: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(fields)).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = SCHEMA_VERSION
    kind: RecordKind
    project: str = Field(min_length=1)
    recorded_at: datetime
    issuer: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    payload: dict[str, Any]
    digest: str

    @classmethod
    def build(
        cls,
        *,
        kind: RecordKind,
        project: str,
        issuer: str,
        idempotency_key: str,
        payload: dict[str, Any],
        recorded_at: datetime,
    ) -> Record:
        draft = cls(
            kind=kind,
            project=project,
            recorded_at=recorded_at.astimezone(UTC),
            issuer=issuer,
            idempotency_key=idempotency_key,
            payload=payload,
            digest="sha256:pending",
        )
        return draft.model_copy(update={"digest": compute_digest(draft.digest_fields())})

    def digest_fields(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"digest"})

    def verify(self) -> bool:
        return self.digest == compute_digest(self.digest_fields())

    @property
    def attestation(self) -> AttestationKind | None:
        if self.kind is not RecordKind.ATTESTATION:
            return None
        return AttestationKind(self.payload["kind"])

    @property
    def data(self) -> dict[str, Any]:
        """The attestation's own payload (`payload["data"]`), or the whole payload otherwise."""
        if self.kind is RecordKind.ATTESTATION:
            return dict(self.payload.get("data", {}))
        return dict(self.payload)


class Ledger(Protocol):
    def contract_set(
        self, project: str, contract: Contract, *, reason: str, issuer: str, idempotency_key: str
    ) -> Record: ...

    def bind(
        self, project: str, pr: PullRequestRef, *, issuer: str, idempotency_key: str
    ) -> Record: ...

    def attest(
        self,
        project: str,
        kind: AttestationKind,
        data: dict[str, Any],
        *,
        issuer: str,
        idempotency_key: str,
    ) -> Record: ...

    def list(
        self,
        project: str,
        *,
        kind: RecordKind | None = None,
        attestation: AttestationKind | None = None,
    ) -> list[Record]: ...

    def get(self, project: str, digest: str) -> Record | None: ...


def open_ledger(repo: Path) -> Ledger:
    """The backend declared in `rail.yaml`. Raises like `load_rail_config` on a bad manifest."""
    from rail.ledger.file import FileLedger
    from rail.model import LedgerBackend, load_rail_config

    cfg = load_rail_config(repo)
    if cfg.ledger is LedgerBackend.FILE:
        return FileLedger(repo / RECEIPTS_DIR)
    raise LedgerUnavailable(
        "ledger 'brain' arrives in phase 2 (ADR-0002); declare `ledger: file` for now"
    )
```

- [ ] **Step 6: Create `src/rail/ledger/file.py`**

```python
"""`FileLedger`: the repository's receipts are the ledger. One operator, one repository, no network."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rail.ledger import (
    AttestationKind,
    Contract,
    IdempotencyConflict,
    LedgerError,
    PullRequestRef,
    Record,
    RecordKind,
)


def receipt_filename(record: Record) -> str:
    label = record.attestation.value if record.attestation else record.kind.value
    stamp = record.recorded_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{label}-{record.digest[7:19]}.json"


def load_receipt(path: Path) -> Record:
    try:
        return Record.model_validate(json.loads(path.read_text()))
    except (OSError, ValueError, ValidationError) as exc:
        raise LedgerError(f"unreadable receipt {path.name}: {exc}") from exc


class FileLedger:
    def __init__(self, root: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self.root = root
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- protocol -------------------------------------------------------------------------

    def contract_set(
        self, project: str, contract: Contract, *, reason: str, issuer: str, idempotency_key: str
    ) -> Record:
        payload = {"contract": contract.model_dump(mode="json"), "reason": reason}
        return self._append(RecordKind.CONTRACT, project, payload, issuer, idempotency_key)

    def bind(
        self, project: str, pr: PullRequestRef, *, issuer: str, idempotency_key: str
    ) -> Record:
        return self._append(
            RecordKind.BINDING, project, pr.model_dump(mode="json"), issuer, idempotency_key
        )

    def attest(
        self,
        project: str,
        kind: AttestationKind,
        data: dict[str, Any],
        *,
        issuer: str,
        idempotency_key: str,
    ) -> Record:
        payload = {"kind": kind.value, "data": data}
        return self._append(RecordKind.ATTESTATION, project, payload, issuer, idempotency_key)

    def list(
        self,
        project: str,
        *,
        kind: RecordKind | None = None,
        attestation: AttestationKind | None = None,
    ) -> list[Record]:
        return [
            r
            for r in self._records()
            if r.project == project
            and (kind is None or r.kind is kind)
            and (attestation is None or r.attestation is attestation)
        ]

    def get(self, project: str, digest: str) -> Record | None:
        return next((r for r in self._records() if r.project == project and r.digest == digest), None)

    # -- internals ------------------------------------------------------------------------

    def _records(self) -> list[Record]:
        if not self.root.is_dir():
            return []
        records = [load_receipt(p) for p in sorted(self.root.glob("*.json"))]
        return sorted(records, key=lambda r: (r.recorded_at, r.digest))

    def _append(
        self,
        kind: RecordKind,
        project: str,
        payload: dict[str, Any],
        issuer: str,
        idempotency_key: str,
    ) -> Record:
        for existing in self._records():
            if existing.project == project and existing.idempotency_key == idempotency_key:
                if existing.kind is kind and existing.payload == payload:
                    return existing
                raise IdempotencyConflict(
                    f"idempotency key {idempotency_key!r} already used by {existing.digest}"
                )
        record = Record.build(
            kind=kind,
            project=project,
            issuer=issuer,
            idempotency_key=idempotency_key,
            payload=payload,
            recorded_at=self._clock(),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / receipt_filename(record)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
        return record
```

- [ ] **Step 7: Run the tests, expect PASS**

```bash
uv run pytest tests/test_ledger_file.py tests/test_model.py -q
```
Expected: `27 passed` (7 contract + 8 file-specific + 12 model).

- [ ] **Step 8: Lint, then commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run pytest -q
git add src/rail/ledger tests/ledger_contract.py tests/test_ledger_file.py src/rail/model.py tests/test_model.py
git commit -m "feat(ledger): Ledger protocol, digested idempotent records and FileLedger (ADR-0002)"
```

### Task 1.3: Git helpers (`rail.gitrepo`)

**Files:**
- Create: `src/rail/gitrepo.py`
- Create: `tests/test_gitrepo.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_gitrepo.py`:
```python
"""Thin, never-raising git helpers used by the gates."""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from rail import gitrepo

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "rail",
    "GIT_AUTHOR_EMAIL": "rail@example.invalid",
    "GIT_COMMITTER_NAME": "rail",
    "GIT_COMMITTER_EMAIL": "rail@example.invalid",
    "GIT_AUTHOR_DATE": "2026-09-15T08:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-09-15T08:00:00+00:00",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=ENV
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "remote", "add", "origin", "git@github.com:hawkixs/red-probe.git")
    _git(tmp_path, "remote", "add", "gitlab", "ssh://git@gitlab.hawkixs.local:2222/hawkixs_project/red/red-probe.git")
    (tmp_path / "a.txt").write_text("a\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "feat: first")
    return tmp_path


def test_is_git_repo(tmp_path: Path) -> None:
    assert gitrepo.is_git_repo(tmp_path) is False
    assert gitrepo.is_git_repo(_repo(tmp_path)) is True


def test_head_sha_and_ancestry(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = gitrepo.head_sha(repo)
    assert first and len(first) == 40
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix: second")
    assert gitrepo.is_ancestor(repo, first) is True
    assert gitrepo.distance(repo, first) == 1
    assert gitrepo.is_ancestor(repo, "0" * 40) is False
    assert gitrepo.distance(repo, "0" * 40) is None


def test_remotes_are_push_urls_by_name(tmp_path: Path) -> None:
    assert gitrepo.remotes(_repo(tmp_path)) == {
        "origin": "git@github.com:hawkixs/red-probe.git",
        "gitlab": "ssh://git@gitlab.hawkixs.local:2222/hawkixs_project/red/red-probe.git",
    }
    assert gitrepo.remotes(tmp_path / "nowhere") == {}


def test_recent_subjects_newest_first(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "fix: second")
    assert gitrepo.recent_subjects(repo, 5) == ["fix: second", "feat: first"]
    assert gitrepo.recent_subjects(repo, 1) == ["fix: second"]
    assert gitrepo.recent_subjects(tmp_path / "nowhere", 5) == []


def test_commit_timestamp_is_aware(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    when = gitrepo.commit_timestamp(repo, gitrepo.head_sha(repo) or "")
    assert when == datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
    assert gitrepo.commit_timestamp(repo, "0" * 40) is None


def test_latest_tag(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert gitrepo.latest_tag(repo) is None
    _git(repo, "tag", "v0.1.0")
    assert gitrepo.latest_tag(repo) == "v0.1.0"


def test_helpers_never_raise_outside_a_repository(tmp_path: Path) -> None:
    assert gitrepo.head_sha(tmp_path) is None
    assert gitrepo.latest_tag(tmp_path) is None
    assert gitrepo.is_ancestor(tmp_path, "abc") is False
```

- [ ] **Step 2: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_gitrepo.py -q
```
Expected: `ImportError: cannot import name 'gitrepo'`.

- [ ] **Step 3: Create `src/rail/gitrepo.py`**

```python
"""Read-only git helpers for the gates. They never raise: outside a repository they answer
None / False / empty, and the gate turns that into an explicit failure."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path


def _git(repo: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
        )
    except (FileNotFoundError, OSError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def is_git_repo(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-inside-work-tree") == "true"


def head_sha(repo: Path) -> str | None:
    return _git(repo, "rev-parse", "HEAD")


def is_ancestor(repo: Path, sha: str, of: str = "HEAD") -> bool:
    return _git(repo, "merge-base", "--is-ancestor", sha, of) is not None


def distance(repo: Path, sha: str) -> int | None:
    """Commits on HEAD since `sha` (0 when `sha` is HEAD); None when `sha` is not an ancestor."""
    if not is_ancestor(repo, sha):
        return None
    count = _git(repo, "rev-list", "--count", f"{sha}..HEAD")
    return int(count) if count is not None else None


def remotes(repo: Path) -> dict[str, str]:
    """Remote name → push URL."""
    out = _git(repo, "remote", "-v")
    found: dict[str, str] = {}
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == "(push)":
            found[parts[0]] = parts[1]
    return found


def recent_subjects(repo: Path, count: int) -> list[str]:
    out = _git(repo, "log", f"-{count}", "--no-merges", "--format=%s")
    return [s for s in (out or "").splitlines() if s]


def commit_timestamp(repo: Path, sha: str) -> datetime | None:
    out = _git(repo, "show", "-s", "--format=%cI", sha)
    if not out:
        return None
    try:
        return datetime.fromisoformat(out.splitlines()[0])
    except ValueError:
        return None


def latest_tag(repo: Path) -> str | None:
    return _git(repo, "describe", "--tags", "--abbrev=0") or None
```

- [ ] **Step 4: Run the tests, expect PASS**

```bash
uv run pytest tests/test_gitrepo.py -q
```
Expected: `7 passed`.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/
git add src/rail/gitrepo.py tests/test_gitrepo.py
git commit -m "feat(gitrepo): never-raising git helpers for the gates"
```

### Task 1.4: Markdown helpers (`rail.markdown`)

**Files:**
- Create: `src/rail/markdown.py`
- Create: `tests/test_markdown.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_markdown.py`:
```python
"""Markdown helpers shared by the design, plan and CLAUDE.md gates."""

from pathlib import Path

from rail import markdown

SPEC = """# red-probe — Design

## 1. Problem
text

## 2. Decisions taken during the brainstorm
| a | b |

### Non-goals of the POC
none

## 8. Proof
### Success criteria
- one
"""

PLAN = """# Plan

Spec: [design](../specs/2026-09-14-red-probe-design.md) — docs/specs/2026-09-14-red-probe-design.md

### Task 1.1: first
- [ ] Run `uv run pytest -q`, expect PASS

### Task 1.2: second
- [ ] Write the code
"""

FENCE = "`" * 3  # built at run time so this file never contains a Markdown fence
CLAUDE_MD = (
    "## Commands\n\n"
    f"{FENCE}bash\nmake ci        # what CI runs\nuv run pytest -q\n$ make nope\n{FENCE}\n\n"
    f"{FENCE}python\nprint(\"not a command\")\n{FENCE}\n"
)


def test_headings_strip_levels_and_numbering() -> None:
    assert markdown.headings(SPEC) == [
        "red-probe — Design",
        "Problem",
        "Decisions taken during the brainstorm",
        "Non-goals of the POC",
        "Proof",
        "Success criteria",
    ]


def test_has_section_matches_aliases_case_insensitively() -> None:
    assert markdown.has_section(SPEC, ("non-goal", "hors périmètre"))
    assert markdown.has_section(SPEC, ("SUCCESS CRITERI",))
    assert not markdown.has_section(SPEC, ("rollback",))


def test_fenced_blocks_filter_by_language() -> None:
    assert markdown.fenced_blocks(CLAUDE_MD, "bash") == ["make ci        # what CI runs\nuv run pytest -q\n$ make nope\n"]
    assert len(markdown.fenced_blocks(CLAUDE_MD)) == 2


def test_command_lines_drop_comments_blank_lines_and_prompts() -> None:
    block = markdown.fenced_blocks(CLAUDE_MD, "bash")[0]
    assert markdown.command_lines(block) == ["make ci", "uv run pytest -q", "make nope"]


def test_latest_doc_picks_the_newest_dated_file(tmp_path: Path) -> None:
    assert markdown.latest_doc(tmp_path) is None
    (tmp_path / "README.md").write_text("x")
    (tmp_path / "2026-09-01-old.md").write_text("x")
    (tmp_path / "2026-09-14-new.md").write_text("x")
    (tmp_path / "notes.md").write_text("x")
    assert markdown.latest_doc(tmp_path) == tmp_path / "2026-09-14-new.md"


def test_spec_references_are_repository_relative_paths() -> None:
    assert markdown.spec_references(PLAN) == ["docs/specs/2026-09-14-red-probe-design.md"]


def test_task_sections_and_verification() -> None:
    sections = markdown.task_sections(PLAN)
    assert [s.splitlines()[0] for s in sections] == ["### Task 1.1: first", "### Task 1.2: second"]
    assert markdown.has_verification(sections[0]) is True
    assert markdown.has_verification(sections[1]) is False


def test_structure_inside_fences_is_ignored() -> None:
    quoted = f"# Plan\n\n{FENCE}markdown\n## Not a heading\n### Task 9.9: quoted\n{FENCE}\n\n### Task 1.1: real\nexpect PASS\n"
    assert markdown.headings(quoted) == ["Plan", "Task 1.1: real"]
    assert [s.splitlines()[0] for s in markdown.task_sections(quoted)] == ["### Task 1.1: real"]
    four = f"{FENCE}`python\nx = 1\n{FENCE}\ny = 2\n{FENCE}`\n"
    assert markdown.fenced_blocks(four, "python") == [f"x = 1\n{FENCE}\ny = 2\n"]
    cited = f"see docs/specs/2026-09-14-a.md\n{FENCE}\ndocs/specs/2026-01-01-quoted.md\n{FENCE}\n"
    assert markdown.spec_references(cited) == ["docs/specs/2026-09-14-a.md"]
```

- [ ] **Step 2: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_markdown.py -q
```
Expected: `ImportError: cannot import name 'markdown'`.

- [ ] **Step 3: Create `src/rail/markdown.py`**

```python
"""Small Markdown readers. Regex-based on purpose: the gates need headings, fences and
references, not a full parser (no new dependency)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?|[IVXLC]+\.)\s+")
_FENCE = re.compile(r"^(`{3,})([\w+-]*)[^\n]*\n(.*?)^\1[ \t]*$", re.MULTILINE | re.DOTALL)
_DATED = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")
_SPEC_REF = re.compile(r"docs/specs/[\w.\-/]+\.md")
_TASK = re.compile(r"^###\s+Task\b.*$", re.MULTILINE)
_VERIFICATION = re.compile(
    r"\b(expect(?:ed)?|verify|assert|should (?:pass|fail)|exit code)\b", re.IGNORECASE
)


def _mask_fences(text: str) -> str:
    """Fenced code blanked out (same length, newlines kept) so `#` lines and `### Task`
    headings quoted inside a fence are never read as structure."""
    return _FENCE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def headings(text: str) -> list[str]:
    """Heading titles at every level, numbering prefixes stripped (`## 1. Problem` → `Problem`)."""
    return [
        _NUMBERING.sub("", m.group(2)).strip() for m in _HEADING.finditer(_mask_fences(text))
    ]


def has_section(text: str, aliases: Iterable[str]) -> bool:
    wanted = [a.lower() for a in aliases]
    return any(alias in h.lower() for h in headings(text) for alias in wanted)


def fenced_blocks(text: str, lang: str | None = None) -> list[str]:
    return [m.group(3) for m in _FENCE.finditer(text) if lang is None or m.group(2) == lang]


def command_lines(block: str) -> list[str]:
    """Executable lines of a shell fence: comments, blank lines and `$ ` prompts removed."""
    lines = []
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith("$ "):
            line = line[2:].strip()
        line = line.split(" #", 1)[0].rstrip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def latest_doc(directory: Path) -> Path | None:
    """The newest `<date>-<topic>.md` in a directory (name order = date order)."""
    if not directory.is_dir():
        return None
    dated = sorted(p for p in directory.glob("*.md") if _DATED.match(p.name))
    return dated[-1] if dated else None


def spec_references(text: str) -> list[str]:
    """`docs/specs/…md` paths cited outside fenced code, first occurrence first."""
    seen: list[str] = []
    for ref in _SPEC_REF.findall(_mask_fences(text)):
        if ref not in seen:
            seen.append(ref)
    return seen


def task_sections(text: str) -> list[str]:
    """Bodies of `### Task …` sections, each starting with its heading line. Headings quoted
    inside fenced code (a plan that shows a sample plan) do not start a section."""
    starts = [m.start() for m in _TASK.finditer(_mask_fences(text))]
    return [text[s:e].rstrip() for s, e in zip(starts, starts[1:] + [len(text)], strict=True)]


def has_verification(section: str) -> bool:
    return _VERIFICATION.search(section) is not None
```

- [ ] **Step 4: Run the tests, expect PASS**

```bash
uv run pytest tests/test_markdown.py -q
```
Expected: `7 passed`.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/
git add src/rail/markdown.py tests/test_markdown.py
git commit -m "feat(markdown): heading, fence and reference readers for the docs gates"
```

### Task 1.5: Deterministic test repository factory (`tests/helpers.py`)

**Files:**
- Create: `tests/__init__.py` (empty — makes `tests.helpers` importable)
- Create: `tests/helpers.py`
- Create: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing test**

`tests/test_helpers.py`:
```python
"""The fixture factory must be deterministic: same tree → same commit SHA, run after run."""

from pathlib import Path

from rail import gitrepo
from tests.helpers import commit_all, conforming_tree, init_repo, write_roster


def test_conforming_tree_is_deterministic(tmp_path: Path) -> None:
    a = conforming_tree(tmp_path / "one", "red-alpha", "bootstrap")
    b = conforming_tree(tmp_path / "two", "red-alpha", "bootstrap")
    assert gitrepo.head_sha(a) == gitrepo.head_sha(b)
    assert (a / "rail.yaml").read_text() == (b / "rail.yaml").read_text()


def test_conforming_tree_has_the_structural_floor(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    for rel in ("rail.yaml", "CLAUDE.md", "Makefile", ".claude/settings.json", "README.md"):
        assert (repo / rel).is_file(), rel
    for rel in ("docs/specs", "docs/plans", "docs/adr", "tests"):
        assert (repo / rel).is_dir(), rel
    assert set(gitrepo.remotes(repo)) == {"origin", "gitlab"}
    assert repo == tmp_path / "projects" / "red-beta"


def test_write_roster_lists_projects(tmp_path: Path) -> None:
    write_roster(tmp_path, ["red-alpha", "red-beta"])
    text = (tmp_path / "CLAUDE.md").read_text()
    assert "| Projet |" in text and "| red-beta |" in text


def test_commit_all_returns_the_new_head(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "r")
    (repo / "x").write_text("x")
    sha = commit_all(repo, "feat: x")
    assert sha == gitrepo.head_sha(repo)
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
uv run pytest tests/test_helpers.py -q
```
Expected: `ModuleNotFoundError: No module named 'tests.helpers'`.

- [ ] **Step 3: Create `tests/__init__.py` (empty) and `tests/helpers.py`**

```python
"""Deterministic fixture repositories for the gate, audit and metrics tests.

Fixed author and dates make commit SHAs stable across runs and machines, which keeps the
golden audit matrix diffable.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

GITHUB_URL = "git@github.com:hawkixs/{name}.git"
MIRROR_URL = "ssh://git@gitlab.hawkixs.local:2222/hawkixs_project/red/{name}.git"
FIXED_DATE = "2026-09-15T08:00:00+00:00"
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "rail",
    "GIT_AUTHOR_EMAIL": "rail@example.invalid",
    "GIT_COMMITTER_NAME": "rail",
    "GIT_COMMITTER_EMAIL": "rail@example.invalid",
    "GIT_AUTHOR_DATE": FIXED_DATE,
    "GIT_COMMITTER_DATE": FIXED_DATE,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}

MINIMAL_MANIFEST = (
    "rail: 1\nproject: {project}\nbrain_key: {project}\ntier: {tier}\nstack: {stack}\n"
    "ledger: file\n"
)

SPEC = """# {project} — Design

## 1. Problem
Why this project exists.

## 2. Decisions
| # | Decision |
|---|---|
| 1 | Keep it small |

## 3. Non-goals
Nothing beyond the fixture.

## 4. Success criteria
`rail check` passes at tier {tier}.
"""

PLAN = """# {project} — Implementation plan

Spec: docs/specs/2026-09-15-{project}-design.md

### Task 1.1: Smoke test
- [ ] Run `make test`, expect PASS
"""

FENCE = "`" * 3  # built at run time so this file never contains a Markdown fence
CLAUDE_MD = (
    "# {project}\n\n- **Brain MCP project key**: `{project}`\n\n## Commands\n\n"
    f"{FENCE}bash\nmake ci        # lint, test, check\nmake test\n{FENCE}\n"
)

MAKEFILE = ".PHONY: lint test check ci\nlint:\n\t@true\ntest:\n\t@true\ncheck:\n\trail check\nci: lint test check\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=GIT_ENV
    ).stdout.strip()


def init_repo(path: Path, *, remotes: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=GIT_ENV)
    git(path, "config", "commit.gpgsign", "false")
    if remotes:
        git(path, "remote", "add", "origin", GITHUB_URL.format(name=path.name))
        git(path, "remote", "add", "gitlab", MIRROR_URL.format(name=path.name))
    return path


def commit_all(repo: Path, subject: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


def write_manifest(
    repo: Path,
    *,
    project: str,
    tier: str,
    stack: str = "python",
    gates: dict[str, tuple[object, str]] | None = None,
    deploy: bool = False,
) -> None:
    body = MINIMAL_MANIFEST.format(project=project, tier=tier, stack=stack)
    if deploy:
        body += "deploy:\n  target: vps-traefik\n"
        body += f"  healthcheck: https://{project}.example.invalid/healthz\n"
    if gates:
        body += "gates:\n"
        for key, (value, reason) in gates.items():
            body += f"  {key}:\n    value: {json.dumps(value)}\n    reason: {reason}\n"
    (repo / "rail.yaml").write_text(body)


def write_roster(root: Path, names: list[str]) -> None:
    rows = "\n".join(f"| {n} | Infra | fixture | OK | `{n}` |" for n in names)
    header = "| Projet | Domaine | Statut reel | Sante | Cle brain |\n|---|---|---|---|---|\n"
    (root / "CLAUDE.md").write_text("# ReD\n\n" + header + rows + "\n")


def conforming_tree(root: Path, name: str, tier: str, *, stack: str = "python") -> Path:
    """`<root>/projects/<name>`: everything the structural gates want at `tier` (no receipts —
    evidence is written by the tests that need it, through `FileLedger`)."""
    repo = init_repo(root / "projects" / name)
    write_manifest(repo, project=name, tier=tier, stack=stack, deploy=(tier == "prod"))
    for sub in ("specs", "plans", "adr", "receipts"):
        (repo / "docs" / sub).mkdir(parents=True)
        (repo / "docs" / sub / ".gitkeep").write_text("")
    (repo / "docs" / "specs" / f"2026-09-15-{name}-design.md").write_text(SPEC.format(project=name, tier=tier))
    (repo / "docs" / "plans" / f"2026-09-15-{name}-plan.md").write_text(PLAN.format(project=name))
    (repo / "CLAUDE.md").write_text(CLAUDE_MD.format(project=name))
    (repo / "README.md").write_text(f"# {name}\n")
    (repo / "Makefile").write_text(MAKEFILE)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(make:*)"]}}, indent=2) + "\n")
    if stack == "python":
        (repo / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.1.0"\n\n[tool.ruff]\nline-length = 100\n'
        )
        (repo / "tests").mkdir()
        (repo / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n")
    elif stack == "go":
        (repo / "go.mod").write_text(f"module example.invalid/{name}\n\ngo 1.22\n")
        (repo / "main_test.go").write_text("package main\n")
    commit_all(repo, "chore: bootstrap the fixture")
    return repo
```

- [ ] **Step 4: Run the tests, expect PASS**

```bash
uv run pytest tests/test_helpers.py -q
```
Expected: `4 passed`.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/
git add tests/__init__.py tests/helpers.py tests/test_helpers.py
git commit -m "test: deterministic fixture repository factory"
```

--- checkpoint ---

## Batch 2: Gates — one module per stage (parallel)

### Task 2.1: Hygiene gates — CLAUDE.md, task runner, settings, remotes, roster, receipts

**Files:**
- Modify: `src/rail/gates/hygiene.py` (full rewrite below)
- Create: `tests/test_gates_hygiene.py`
- Modify: `tests/test_gates.py` (expected code list grows)
- Modify: `tests/test_policy.py` (`test_run_gates_filters_by_stage` expected code list grows)

- [ ] **Step 1: Write the failing tests**

`tests/test_gates_hygiene.py`:
```python
"""Hygiene: the floor every tier stands on."""

import json
from datetime import UTC, datetime
from pathlib import Path

from rail.gates import Stage
from rail.gates.hygiene import (
    GATES,
    claude_md,
    receipts,
    remotes,
    roster_entry,
    settings,
    task_runner,
)
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from tests.helpers import conforming_tree, git, init_repo, write_roster

CLOCK = lambda: datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # noqa: E731


def test_registry_order_and_scopes() -> None:
    assert [g.code for g in GATES] == [
        "rail_config",
        "docs_layout",
        "claude_md",
        "task_runner",
        "settings",
        "remotes",
        "roster_entry",
        "receipts",
    ]
    assert {g.code for g in GATES if g.scope == "workstation"} == {"remotes", "roster_entry"}
    assert all(g.stage is Stage.HYGIENE for g in GATES)


def test_claude_md_passes_on_a_conforming_tree(tmp_path: Path) -> None:
    result = claude_md(conforming_tree(tmp_path, "red-alpha", "bootstrap"))
    assert result.passed, result.details


def test_claude_md_requires_the_brain_key(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (repo / "CLAUDE.md").write_text("# red-alpha\n")
    result = claude_md(repo)
    assert not result.passed and "brain key" in result.details


def test_claude_md_rejects_commands_that_cannot_run(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (repo / "CLAUDE.md").write_text("`red-alpha`\n\n```bash\nmake nope\n```\n")
    result = claude_md(repo)
    assert not result.passed and "make nope" in result.details


def test_task_runner_wants_a_makefile_with_a_ci_target(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    assert task_runner(repo).passed
    (repo / "Makefile").write_text("test:\n\t@true\n")
    assert "ci" in task_runner(repo).details and not task_runner(repo).passed
    (repo / "Makefile").unlink()
    assert "Makefile" in task_runner(repo).details


def test_settings_wants_a_permission_allowlist(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    assert settings(repo).passed
    (repo / ".claude" / "settings.json").write_text("{not json")
    assert "invalid JSON" in settings(repo).details
    (repo / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": []}}))
    assert not settings(repo).passed
    (repo / ".claude" / "settings.json").unlink()
    assert not settings(repo).passed


def test_remotes_need_github_and_the_mirror(tmp_path: Path) -> None:
    assert remotes(conforming_tree(tmp_path, "red-alpha", "bootstrap")).passed
    lonely = init_repo(tmp_path / "lonely", remotes=False)
    git(lonely, "remote", "add", "origin", "git@github.com:hawkixs/lonely.git")
    result = remotes(lonely)
    assert not result.passed and "gitlab.hawkixs.local" in result.details
    assert "not a git repository" in remotes(tmp_path / "nowhere").details


def test_roster_entry_reads_the_red_root(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"])
    assert roster_entry(repo).passed
    write_roster(tmp_path, ["red-other"])
    result = roster_entry(repo)
    assert not result.passed and "red-alpha" in result.details


def test_roster_entry_is_standalone_without_a_roster(tmp_path: Path) -> None:
    result = roster_entry(conforming_tree(tmp_path, "red-alpha", "bootstrap"))
    assert result.passed and "standalone" in result.details


def test_receipts_pass_when_absent_or_well_formed(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    assert receipts(repo).details == "no receipts"
    FileLedger(repo / RECEIPTS_DIR, clock=CLOCK).attest(
        "red-alpha", AttestationKind.INTEGRATED, {"sha": "a" * 40}, issuer="op", idempotency_key="i1"
    )
    result = receipts(repo)
    assert result.passed and "1 well-formed" in result.details


def test_receipts_report_tampering_misnaming_and_duplicates(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    ledger = FileLedger(repo / RECEIPTS_DIR, clock=CLOCK)
    ledger.attest("red-alpha", AttestationKind.INTEGRATED, {"sha": "a" * 40}, issuer="op", idempotency_key="i1")
    path = next((repo / RECEIPTS_DIR).glob("*.json"))
    raw = json.loads(path.read_text())
    raw["payload"]["data"]["sha"] = "b" * 40
    path.write_text(json.dumps(raw))
    assert "digest" in receipts(repo).details
    path.rename(path.with_name("renamed.json"))
    assert "expected name" in receipts(repo).details
    (repo / RECEIPTS_DIR / "junk.json").write_text("{")
    assert "junk.json" in receipts(repo).details


def test_receipts_reject_a_reused_idempotency_key(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    ledger = FileLedger(repo / RECEIPTS_DIR, clock=CLOCK)
    first = ledger.attest("red-alpha", AttestationKind.INTEGRATED, {"sha": "a" * 40}, issuer="op", idempotency_key="i1")
    later = FileLedger(tmp_path / "elsewhere", clock=lambda: datetime(2026, 9, 16, tzinfo=UTC))
    second = later.attest("red-alpha", AttestationKind.INTEGRATED, {"sha": "b" * 40}, issuer="op", idempotency_key="i1")
    src = next((tmp_path / "elsewhere").glob("*.json"))
    src.rename(repo / RECEIPTS_DIR / src.name)
    result = receipts(repo)
    assert not result.passed and "idempotency key" in result.details
    assert first.digest != second.digest
```

- [ ] **Step 2: Update the two existing expectations**

In `tests/test_gates.py::test_run_gates_returns_one_result_per_gate_and_never_raises` and in `tests/test_policy.py::test_run_gates_filters_by_stage`, replace the expected list `["rail_config", "docs_layout"]` by:
```python
[
    "rail_config",
    "docs_layout",
    "claude_md",
    "task_runner",
    "settings",
    "remotes",
    "roster_entry",
    "receipts",
]
```
In `tests/test_gates.py::test_run_gates_all_pass_on_conforming_repo`, replace the body by:
```python
    from tests.helpers import conforming_tree, write_roster

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"])
    results = run_gates(repo)
    assert all(r.passed for r in results), [r for r in results if not r.passed]
```
and delete `_conforming_repo` from `tests/test_gates.py` if nothing else uses it (`test_rail_config_gate_passes_on_valid_manifest` still does — keep it in that case).

- [ ] **Step 3: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_gates_hygiene.py tests/test_gates.py tests/test_policy.py -q
```
Expected: `ImportError: cannot import name 'claude_md'`.

- [ ] **Step 4: Rewrite `src/rail/gates/hygiene.py`**

```python
"""Hygiene gates: the floor every tier stands on, starting with `bootstrap`.

`remotes` and `roster_entry` are workstation-scoped: they read the operator's clone (two
remotes, the ReD root roster two directories up) and are reported as skipped under `--ci`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from rail import gitrepo, markdown
from rail.gates import GateResult, GateSpec, Stage
from rail.ledger import RECEIPTS_DIR, LedgerError
from rail.ledger.file import load_receipt, receipt_filename
from rail.model import MANIFEST_NAME, RailConfig, load_rail_config
from rail.policy import effective

DOCS_DIRS = ("docs/specs", "docs/plans", "docs/adr")
ROSTER_MARKER = "| Projet |"
_MAKE_TARGET = re.compile(r"^([A-Za-z0-9_./-]+)\s*:(?!=)", re.MULTILINE)


def _config(repo: Path) -> RailConfig | None:
    try:
        return load_rail_config(repo)
    except (FileNotFoundError, ValidationError):
        return None


def _project_name(repo: Path) -> str:
    cfg = _config(repo)
    return cfg.project if cfg else repo.absolute().name


def make_targets(repo: Path) -> set[str]:
    makefile = repo / "Makefile"
    if not makefile.is_file():
        return set()
    found = {m.group(1) for m in _MAKE_TARGET.finditer(makefile.read_text())}
    return {t for t in found if not t.startswith(".")}


def find_roster(repo: Path) -> Path | None:
    """The ReD root `CLAUDE.md` (two levels up: `<root>/projects/<repo>`), if it holds the roster."""
    candidate = repo.absolute().parent.parent / "CLAUDE.md"
    if candidate.is_file() and ROSTER_MARKER in candidate.read_text():
        return candidate
    return None


def rail_config(repo: Path) -> GateResult:
    try:
        cfg = load_rail_config(repo)
    except FileNotFoundError:
        return GateResult(Stage.HYGIENE, "rail_config", False, f"{MANIFEST_NAME} is missing")
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        return GateResult(Stage.HYGIENE, "rail_config", False, f"{location}: {first['msg']}")
    return GateResult(Stage.HYGIENE, "rail_config", True, f"tier={cfg.tier.value}")


def docs_layout(repo: Path) -> GateResult:
    missing = [d for d in DOCS_DIRS if not (repo / d).is_dir()]
    if missing:
        return GateResult(Stage.HYGIENE, "docs_layout", False, "missing: " + ", ".join(missing))
    return GateResult(Stage.HYGIENE, "docs_layout", True, "docs/{specs,plans,adr} present")


def claude_md(repo: Path) -> GateResult:
    path = repo / "CLAUDE.md"
    if not path.is_file():
        return GateResult(Stage.HYGIENE, "claude_md", False, "CLAUDE.md is missing")
    text = path.read_text()
    problems: list[str] = []
    cfg = _config(repo)
    if cfg is None:
        problems.append(f"{MANIFEST_NAME} unreadable, brain key not verifiable")
    elif f"`{cfg.brain_key}`" not in text:
        problems.append(f"brain key `{cfg.brain_key}` not mentioned")
    targets = make_targets(repo)
    commands = 0
    for block in markdown.fenced_blocks(text, "bash"):
        for line in markdown.command_lines(block):
            commands += 1
            words = line.split()
            if words[0] == "make" and len(words) > 1 and words[1] not in targets:
                problems.append(f"`{line}` names a Makefile target that does not exist")
    if problems:
        return GateResult(Stage.HYGIENE, "claude_md", False, "; ".join(problems))
    return GateResult(
        Stage.HYGIENE, "claude_md", True, f"brain key present, {commands} command(s) resolvable"
    )


def task_runner(repo: Path) -> GateResult:
    if not (repo / "Makefile").is_file():
        return GateResult(Stage.HYGIENE, "task_runner", False, "Makefile is missing")
    targets = make_targets(repo)
    if "ci" not in targets:
        return GateResult(Stage.HYGIENE, "task_runner", False, "Makefile has no `ci` target")
    return GateResult(
        Stage.HYGIENE, "task_runner", True, f"Makefile with `ci` ({len(targets)} targets)"
    )


def settings(repo: Path) -> GateResult:
    path = repo / ".claude" / "settings.json"
    if not path.is_file():
        return GateResult(Stage.HYGIENE, "settings", False, ".claude/settings.json is missing")
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        return GateResult(Stage.HYGIENE, "settings", False, f"invalid JSON: {exc}")
    allow = data.get("permissions", {}).get("allow") if isinstance(data, dict) else None
    if not isinstance(allow, list) or not allow:
        return GateResult(
            Stage.HYGIENE, "settings", False, "permissions.allow is missing or empty"
        )
    return GateResult(Stage.HYGIENE, "settings", True, f"{len(allow)} allowed pattern(s)")


def remotes(repo: Path) -> GateResult:
    canonical, _ = effective(repo, "hygiene.canonical_host")
    mirror, _ = effective(repo, "hygiene.mirror_host")
    if not gitrepo.is_git_repo(repo):
        return GateResult(Stage.HYGIENE, "remotes", False, "not a git repository")
    urls = gitrepo.remotes(repo)
    missing = [
        host for host in (canonical, mirror) if not any(host in url for url in urls.values())
    ]
    if missing:
        names = ", ".join(sorted(urls)) or "none"
        return GateResult(
            Stage.HYGIENE, "remotes", False, f"no remote on {', '.join(missing)} (remotes: {names})"
        )
    return GateResult(Stage.HYGIENE, "remotes", True, f"{canonical} + {mirror}")


def roster_entry(repo: Path) -> GateResult:
    roster = find_roster(repo)
    if roster is None:
        return GateResult(
            Stage.HYGIENE, "roster_entry", True, "no roster in scope (standalone repository)"
        )
    name = _project_name(repo)
    if re.search(rf"^\|\s*{re.escape(name)}\s*\|", roster.read_text(), re.MULTILINE):
        return GateResult(Stage.HYGIENE, "roster_entry", True, f"listed in {roster}")
    return GateResult(Stage.HYGIENE, "roster_entry", False, f"{name} has no row in {roster}")


def receipts(repo: Path) -> GateResult:
    directory = repo / RECEIPTS_DIR
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        return GateResult(Stage.HYGIENE, "receipts", True, "no receipts")
    problems: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for path in files:
        try:
            record = load_receipt(path)
        except LedgerError as exc:
            problems.append(str(exc))
            continue
        if not record.verify():
            problems.append(f"{path.name}: digest does not match its content")
        expected = receipt_filename(record)
        if path.name != expected:
            problems.append(f"{path.name}: expected name {expected}")
        key = (record.project, record.idempotency_key)
        if key in seen:
            problems.append(
                f"{path.name}: idempotency key {record.idempotency_key!r} already used by {seen[key]}"
            )
        seen.setdefault(key, path.name)
    if problems:
        shown = "; ".join(problems[:5])
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        return GateResult(Stage.HYGIENE, "receipts", False, shown + more)
    return GateResult(Stage.HYGIENE, "receipts", True, f"{len(files)} well-formed receipt(s)")


GATES = [
    GateSpec(Stage.HYGIENE, "rail_config", rail_config),
    GateSpec(Stage.HYGIENE, "docs_layout", docs_layout),
    GateSpec(Stage.HYGIENE, "claude_md", claude_md),
    GateSpec(Stage.HYGIENE, "task_runner", task_runner),
    GateSpec(Stage.HYGIENE, "settings", settings),
    GateSpec(Stage.HYGIENE, "remotes", remotes, scope="workstation"),
    GateSpec(Stage.HYGIENE, "roster_entry", roster_entry, scope="workstation"),
    GateSpec(Stage.HYGIENE, "receipts", receipts),
]
```

- [ ] **Step 5: Run the tests, expect PASS**

```bash
uv run pytest tests/test_gates_hygiene.py tests/test_gates.py tests/test_policy.py -q
```
Expected: all passed (13 new + updated existing), 0 failed.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run pytest -q
git add src/rail/gates/hygiene.py tests/test_gates_hygiene.py tests/test_gates.py tests/test_policy.py
git commit -m "feat(gates): hygiene floor — CLAUDE.md, task runner, settings, remotes, roster, receipts"
```

### Task 2.2: Docs gates — `intent.contract`, `design.spec`, `plan.plan`

**Files:**
- Create: `src/rail/gates/intent.py`
- Create: `src/rail/gates/design.py`
- Create: `src/rail/gates/plan.py`
- Create: `tests/test_gates_docs.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_gates_docs.py`:
```python
"""Stages 1–3: a recorded contract, a spec with the mandatory sections, a plan that verifies."""

from datetime import UTC, datetime
from pathlib import Path

from rail.gates import Stage
from rail.gates.design import spec
from rail.gates.intent import contract
from rail.gates.plan import plan
from rail.ledger import RECEIPTS_DIR, Contract, Deliverable
from rail.ledger.file import FileLedger
from tests.helpers import conforming_tree

CLOCK = lambda: datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # noqa: E731
CONTRACT = Contract(
    objective="ship red-alpha",
    acceptance_criteria=["rail check passes"],
    deliverables=[Deliverable(key="main", repository="hawkixs/red-alpha")],
)


def _with_contract(repo: Path) -> Path:
    FileLedger(repo / RECEIPTS_DIR, clock=CLOCK).contract_set(
        "red-alpha", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c1"
    )
    return repo


def test_intent_requires_a_readable_manifest(tmp_path: Path) -> None:
    result = contract(tmp_path)
    assert result.stage is Stage.INTENT and not result.passed
    assert "rail.yaml" in result.details


def test_intent_fails_without_a_contract_and_names_the_command(tmp_path: Path) -> None:
    result = contract(conforming_tree(tmp_path, "red-alpha", "bootstrap"))
    assert not result.passed and "rail contract set" in result.details


def test_intent_passes_with_a_contract(tmp_path: Path) -> None:
    result = contract(_with_contract(conforming_tree(tmp_path, "red-alpha", "bootstrap")))
    assert result.passed and "ship red-alpha" in result.details


def test_intent_reports_an_unavailable_backend(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    text = (repo / "rail.yaml").read_text().replace("ledger: file", "ledger: brain")
    (repo / "rail.yaml").write_text(text)
    result = contract(repo)
    assert not result.passed and "phase 2" in result.details


def test_design_wants_a_dated_spec(tmp_path: Path) -> None:
    result = spec(tmp_path)
    assert result.stage is Stage.DESIGN and not result.passed
    assert "docs/specs" in result.details


def test_design_names_every_missing_section(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (repo / "docs" / "specs" / "2026-09-15-red-alpha-design.md").write_text("# x\n\n## Problem\n")
    result = spec(repo)
    assert not result.passed
    for name in ("decisions", "non-goals", "success criteria"):
        assert name in result.details


def test_design_accepts_french_aliases(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (repo / "docs" / "specs" / "2026-09-15-red-alpha-design.md").write_text(
        "# x\n\n## 1. Contexte\n\n## 2. Décisions\n\n## 3. Hors périmètre\n\n## 4. Critères de succès\n"
    )
    assert spec(repo).passed


def test_design_passes_on_a_conforming_spec(tmp_path: Path) -> None:
    result = spec(conforming_tree(tmp_path, "red-alpha", "bootstrap"))
    assert result.passed and "2026-09-15-red-alpha-design.md" in result.details


def test_plan_wants_a_dated_plan(tmp_path: Path) -> None:
    result = plan(tmp_path)
    assert result.stage is Stage.PLAN and not result.passed


def test_plan_must_reference_an_existing_spec(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    doc = repo / "docs" / "plans" / "2026-09-15-red-beta-plan.md"
    doc.write_text("# plan\n\n### Task 1.1: x\n- expect PASS\n")
    assert "references no spec" in plan(repo).details
    doc.write_text("# plan\n\ndocs/specs/2026-01-01-missing.md\n\n### Task 1.1: x\n- expect PASS\n")
    assert "missing spec" in plan(repo).details


def test_plan_requires_a_verification_per_task(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    doc = repo / "docs" / "plans" / "2026-09-15-red-beta-plan.md"
    doc.write_text(
        "# plan\n\ndocs/specs/2026-09-15-red-beta-design.md\n\n"
        "### Task 1.1: ok\n- expect PASS\n\n### Task 1.2: nope\n- write code\n"
    )
    result = plan(repo)
    assert not result.passed and "Task 1.2" in result.details
    doc.write_text("# plan\n\ndocs/specs/2026-09-15-red-beta-design.md\n")
    assert "no `### Task`" in plan(repo).details


def test_plan_passes_on_a_conforming_plan(tmp_path: Path) -> None:
    result = plan(conforming_tree(tmp_path, "red-beta", "dev"))
    assert result.passed and "1 task(s)" in result.details
```

- [ ] **Step 2: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_gates_docs.py -q
```
Expected: `ModuleNotFoundError: No module named 'rail.gates.design'`.

- [ ] **Step 3: Create `src/rail/gates/intent.py`**

```python
"""Stage 1 — intent: a delivery contract for this project exists in the ledger."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from rail.gates import GateResult, GateSpec, Stage
from rail.ledger import LedgerError, RecordKind, open_ledger
from rail.model import MANIFEST_NAME, load_rail_config


def contract(repo: Path) -> GateResult:
    try:
        cfg = load_rail_config(repo)
        records = open_ledger(repo).list(cfg.project, kind=RecordKind.CONTRACT)
    except (FileNotFoundError, ValidationError):
        return GateResult(Stage.INTENT, "contract", False, f"{MANIFEST_NAME} unreadable")
    except LedgerError as exc:
        return GateResult(Stage.INTENT, "contract", False, str(exc))
    if not records:
        return GateResult(
            Stage.INTENT,
            "contract",
            False,
            f"no contract recorded for {cfg.project} (run `rail contract set`)",
        )
    latest = records[-1]
    objective = str(latest.payload.get("contract", {}).get("objective", ""))
    return GateResult(
        Stage.INTENT, "contract", True, f"contract {latest.digest[:19]} — {objective[:60]}"
    )


GATES = [GateSpec(Stage.INTENT, "contract", contract)]
```

- [ ] **Step 4: Create `src/rail/gates/design.py`**

```python
"""Stage 2 — design: the latest dated spec carries the mandatory sections."""

from __future__ import annotations

from pathlib import Path

from rail import markdown
from rail.gates import GateResult, GateSpec, Stage
from rail.policy import REQUIRED_SPEC_SECTIONS

SPECS_DIR = "docs/specs"


def spec(repo: Path) -> GateResult:
    latest = markdown.latest_doc(repo / SPECS_DIR)
    if latest is None:
        return GateResult(
            Stage.DESIGN, "spec", False, f"no dated spec in {SPECS_DIR} (expected <date>-<topic>.md)"
        )
    text = latest.read_text()
    missing = [
        name
        for name, aliases in REQUIRED_SPEC_SECTIONS.items()
        if not markdown.has_section(text, aliases)
    ]
    if missing:
        return GateResult(
            Stage.DESIGN, "spec", False, f"{latest.name}: missing section(s): {', '.join(missing)}"
        )
    return GateResult(
        Stage.DESIGN,
        "spec",
        True,
        f"{latest.name}: {len(REQUIRED_SPEC_SECTIONS)} mandatory sections present",
    )


GATES = [GateSpec(Stage.DESIGN, "spec", spec)]
```

- [ ] **Step 5: Create `src/rail/gates/plan.py`**

```python
"""Stage 3 — plan: the latest dated plan references an existing spec and every task
carries a verification (an expectation, an assertion, an exit code)."""

from __future__ import annotations

from pathlib import Path

from rail import markdown
from rail.gates import GateResult, GateSpec, Stage

PLANS_DIR = "docs/plans"


def plan(repo: Path) -> GateResult:
    latest = markdown.latest_doc(repo / PLANS_DIR)
    if latest is None:
        return GateResult(
            Stage.PLAN, "plan", False, f"no dated plan in {PLANS_DIR} (expected <date>-<topic>.md)"
        )
    text = latest.read_text()
    refs = markdown.spec_references(text)
    if not refs:
        return GateResult(
            Stage.PLAN, "plan", False, f"{latest.name}: references no spec (docs/specs/<date>-<topic>.md)"
        )
    missing = [ref for ref in refs if not (repo / ref).is_file()]
    if missing:
        return GateResult(
            Stage.PLAN, "plan", False, f"{latest.name}: references missing spec(s): {', '.join(missing)}"
        )
    sections = markdown.task_sections(text)
    if not sections:
        return GateResult(Stage.PLAN, "plan", False, f"{latest.name}: no `### Task` section")
    unverified = [s.splitlines()[0] for s in sections if not markdown.has_verification(s)]
    if unverified:
        return GateResult(
            Stage.PLAN,
            "plan",
            False,
            f"{latest.name}: {len(unverified)} task(s) without a verification, first: {unverified[0]}",
        )
    return GateResult(
        Stage.PLAN, "plan", True, f"{latest.name}: {len(sections)} task(s) verified, spec {refs[0]}"
    )


GATES = [GateSpec(Stage.PLAN, "plan", plan)]
```

- [ ] **Step 6: Run the tests, expect PASS**

```bash
uv run pytest tests/test_gates_docs.py -q
```
Expected: `12 passed`.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run pytest -q
git add src/rail/gates/intent.py src/rail/gates/design.py src/rail/gates/plan.py tests/test_gates_docs.py
git commit -m "feat(gates): intent, design and plan gates over the ledger and the dated docs"
```

### Task 2.3: Build gates — tests, lint, secrets (gitleaks), conventional commits

**Files:**
- Create: `src/rail/gates/build.py`
- Create: `tests/test_gates_build.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_gates_build.py`:
```python
"""Stage 4 — build: static checks on the repository, never running the project's own suite."""

import random
import shutil
import string
from pathlib import Path

import pytest

from rail.gates import Stage
from rail.gates import build as build_gates
from rail.gates.build import GATES, commits, lint, secrets, tests
from tests.helpers import commit_all, conforming_tree, init_repo


def test_registry() -> None:
    assert [g.code for g in GATES] == ["tests", "lint", "secrets", "commits"]
    assert all(g.stage is Stage.BUILD for g in GATES)


def test_tests_gate_per_stack(tmp_path: Path) -> None:
    py = conforming_tree(tmp_path / "py", "red-alpha", "dev")
    assert tests(py).passed and "1 test file" in tests(py).details
    (py / "tests" / "test_smoke.py").unlink()
    assert not tests(py).passed
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    assert tests(go).passed
    docs = conforming_tree(tmp_path / "docs", "red-gamma", "dev", stack="docs")
    assert tests(docs).passed and "docs" in tests(docs).details
    assert "rail.yaml" in tests(tmp_path).details


def test_lint_gate_per_stack(tmp_path: Path) -> None:
    py = conforming_tree(tmp_path / "py", "red-alpha", "dev")
    assert lint(py).passed
    (py / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert not lint(py).passed and "ruff" in lint(py).details
    (py / "ruff.toml").write_text("line-length = 100\n")
    assert lint(py).passed
    go = conforming_tree(tmp_path / "go", "red-beta", "dev", stack="go")
    assert lint(go).passed
    (go / "go.mod").unlink()
    assert not lint(go).passed


def test_secrets_gate_reads_the_gitleaks_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (0, "no leaks found"))
    assert secrets(tmp_path).passed
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (2, "leaks found: 1"))
    result = secrets(tmp_path)
    assert not result.passed and "leaks" in result.details
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (1, "boom"))
    assert "exit 1" in secrets(tmp_path).details
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: None)
    assert "not installed" in secrets(tmp_path).details


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_secrets_gate_runs_gitleaks_for_real(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    assert secrets(repo).passed, secrets(repo).details
    # Built at run time, never a literal in this file: red-rail's own history must stay clean.
    # Seed 7 yields a high-entropy fake GitHub PAT that gitleaks flags (verified 2026-09-15).
    alphabet = string.ascii_letters + string.digits
    fake_pat = "ghp_" + "".join(random.Random(7).choices(alphabet, k=36))
    (repo / "config.py").write_text(f'GITHUB_TOKEN = "{fake_pat}"\n')
    commit_all(repo, "feat: leak")
    assert not secrets(repo).passed


def test_commits_gate_checks_conventional_subjects(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    assert commits(repo).passed
    commit_all(repo, "Fixed stuff")
    result = commits(repo)
    assert not result.passed and "Fixed stuff" in result.details
    commit_all(repo, "wip(scope)!: allowed type with scope and bang")
    assert "wip" in commits(repo).details  # unknown type is reported
    empty = init_repo(tmp_path / "empty")
    assert "no commits" in commits(empty).details
    assert "not a git repository" in commits(tmp_path / "nowhere").details


def test_commits_gate_honours_the_window_override(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    commit_all(repo, "Fixed stuff")
    commit_all(repo, "feat: fine")
    assert not commits(repo).passed
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text()
        + "gates:\n  build.commit_window:\n    value: 1\n    reason: fixture\n"
    )
    assert commits(repo).passed
```

- [ ] **Step 2: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_gates_build.py -q
```
Expected: `ModuleNotFoundError: No module named 'rail.gates.build'`.

- [ ] **Step 3: Create `src/rail/gates/build.py`**

```python
"""Stage 4 — build: static checks. Tests exist for the stack, a linter is configured, gitleaks
finds nothing in the history, commit subjects are conventional. Running the project's own
suite is CI's job (`make ci`); its exit code is the check, not this gate."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from pydantic import ValidationError

from rail import gitrepo
from rail.gates import GateResult, GateSpec, Stage
from rail.model import MANIFEST_NAME, Stack, load_rail_config
from rail.policy import effective

CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]+\))?!?: \S")


def _stack(repo: Path) -> Stack | None:
    try:
        return load_rail_config(repo).stack
    except (FileNotFoundError, ValidationError):
        return None


def tests(repo: Path) -> GateResult:
    stack = _stack(repo)
    if stack is None:
        return GateResult(Stage.BUILD, "tests", False, f"{MANIFEST_NAME} unreadable")
    if stack is Stack.DOCS:
        return GateResult(Stage.BUILD, "tests", True, "stack docs: no test suite required")
    if stack is Stack.PYTHON:
        root = repo / "tests"
        found = (
            [p for p in root.rglob("*.py") if p.name.startswith("test_") or p.name.endswith("_test.py")]
            if root.is_dir()
            else []
        )
        where = "tests/test_*.py"
    else:
        found = [p for p in repo.rglob("*_test.go") if "vendor" not in p.parts]
        where = "*_test.go"
    if not found:
        return GateResult(Stage.BUILD, "tests", False, f"no test files ({where})")
    return GateResult(Stage.BUILD, "tests", True, f"{len(found)} test file(s)")


def lint(repo: Path) -> GateResult:
    stack = _stack(repo)
    if stack is None:
        return GateResult(Stage.BUILD, "lint", False, f"{MANIFEST_NAME} unreadable")
    if stack is Stack.DOCS:
        return GateResult(Stage.BUILD, "lint", True, "stack docs: no linter required")
    if stack is Stack.PYTHON:
        pyproject = repo / "pyproject.toml"
        configured = (pyproject.is_file() and "[tool.ruff" in pyproject.read_text()) or any(
            (repo / name).is_file() for name in ("ruff.toml", ".ruff.toml")
        )
        if configured:
            return GateResult(Stage.BUILD, "lint", True, "ruff configured")
        return GateResult(
            Stage.BUILD, "lint", False, "ruff is not configured ([tool.ruff] in pyproject.toml or ruff.toml)"
        )
    if (repo / "go.mod").is_file():
        return GateResult(Stage.BUILD, "lint", True, "go.mod present (go vet is built in)")
    return GateResult(Stage.BUILD, "lint", False, "go.mod is missing")


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
        return GateResult(Stage.BUILD, "secrets", False, f"gitleaks found leaks: {last}")
    return GateResult(Stage.BUILD, "secrets", False, f"gitleaks failed (exit {code}): {last}")


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
        match = CONVENTIONAL.match(subject)
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
    GateSpec(Stage.BUILD, "tests", tests),
    GateSpec(Stage.BUILD, "lint", lint),
    GateSpec(Stage.BUILD, "secrets", secrets),
    GateSpec(Stage.BUILD, "commits", commits),
]
```

- [ ] **Step 4: Run the tests, expect PASS**

```bash
uv run pytest tests/test_gates_build.py -q
```
Expected: `7 passed` (the real-gitleaks test runs on the host, where `gitleaks 8.30.1` is installed; it is skipped elsewhere).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run pytest -q
git add src/rail/gates/build.py tests/test_gates_build.py
git commit -m "feat(gates): build gate — tests, lint config, gitleaks, conventional commits"
```

### Task 2.4: Evidence gates — review, integrate, release, deploy, observe, learn

**Files:**
- Create: `src/rail/gates/evidence.py`
- Create: `tests/test_gates_evidence.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_gates_evidence.py`:
```python
"""Stages 5–10 read the ledger: the newest matching attestation must sit on HEAD's history."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from rail import gitrepo
from rail.gates import Stage
from rail.gates.evidence import GATES, deployed, drill, fulfilled, integrated, released, verdict
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from tests.helpers import commit_all, conforming_tree

T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def _ledger(repo: Path) -> FileLedger:
    ticks = [T0 + timedelta(minutes=i) for i in range(100)]
    return FileLedger(repo / RECEIPTS_DIR, clock=lambda: ticks.pop(0))


def _attest(ledger: FileLedger, kind: AttestationKind, key: str, **data: object) -> None:
    ledger.attest("red-beta", kind, dict(data), issuer="op", idempotency_key=key)


def test_registry_covers_stages_5_to_10() -> None:
    assert [(g.stage, g.code) for g in GATES] == [
        (Stage.REVIEW, "verdict"),
        (Stage.INTEGRATE, "receipt"),
        (Stage.RELEASE, "released"),
        (Stage.DEPLOY, "deployed"),
        (Stage.OBSERVE, "drill"),
        (Stage.LEARN, "fulfilled"),
    ]


def test_every_gate_fails_explicitly_without_evidence(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    for gate in (verdict, integrated, released, deployed, drill, fulfilled):
        result = gate(repo)
        assert not result.passed and "no " in result.details, result
    assert "rail.yaml" in verdict(tmp_path).details


def test_verdict_must_be_independent_and_approving_on_history(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    head = gitrepo.head_sha(repo)
    ledger = _ledger(repo)
    _attest(ledger, AttestationKind.REVIEW_VERDICT, "v1", sha=head, independent=False, verdict="approve")
    assert "pre-review" in verdict(repo).details and not verdict(repo).passed
    _attest(ledger, AttestationKind.REVIEW_VERDICT, "v2", sha=head, independent=True, verdict="request_changes")
    assert not verdict(repo).passed
    _attest(ledger, AttestationKind.REVIEW_VERDICT, "v3", sha=head, independent=True, verdict="approve")
    result = verdict(repo)
    assert result.passed and "distance 0" in result.details
    commit_all(repo, "feat: more")
    assert "distance 1" in verdict(repo).details
    _attest(ledger, AttestationKind.REVIEW_VERDICT, "v4", sha="0" * 40, independent=True, verdict="approve")
    assert "not on HEAD's history" in verdict(repo).details


def test_integrated_is_the_newest_receipt_on_history(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    ledger = _ledger(repo)
    _attest(ledger, AttestationKind.INTEGRATED, "i1", sha=gitrepo.head_sha(repo))
    assert integrated(repo).passed
    _attest(ledger, AttestationKind.INTEGRATED, "i2", sha="0" * 40)
    assert not integrated(repo).passed


def test_release_deploy_observe_learn_chain(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    head = gitrepo.head_sha(repo)
    ledger = _ledger(repo)
    _attest(ledger, AttestationKind.RELEASED, "r1", sha=head, version="1.0.0", digest="sha256:aaa")
    assert released(repo).passed and "1.0.0" in released(repo).details
    _attest(ledger, AttestationKind.DEPLOYED, "d0", sha=head, digest="sha256:old")
    assert "sha256:aaa" in deployed(repo).details and not deployed(repo).passed
    _attest(ledger, AttestationKind.DEPLOYED, "d1", sha=head, digest="sha256:aaa")
    assert deployed(repo).passed
    assert not drill(repo).passed
    _attest(ledger, AttestationKind.ROLLED_BACK, "rb1", drill=True, digest="sha256:old")
    assert not drill(repo).passed
    _attest(ledger, AttestationKind.RESTORED, "rs1", drill=True, digest="sha256:aaa")
    assert drill(repo).passed
    assert not fulfilled(repo).passed
    _attest(ledger, AttestationKind.FULFILLED, "f1")
    assert fulfilled(repo).passed


def test_released_without_version_or_digest_is_incomplete(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    _attest(_ledger(repo), AttestationKind.RELEASED, "r1", sha=gitrepo.head_sha(repo))
    result = released(repo)
    assert not result.passed and "version" in result.details
```

- [ ] **Step 2: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_gates_evidence.py -q
```
Expected: `ModuleNotFoundError: No module named 'rail.gates.evidence'`.

- [ ] **Step 3: Create `src/rail/gates/evidence.py`**

```python
"""Stages 5–10 read evidence from the ledger. A gate passes when the newest matching
attestation is on HEAD's history; the distance in commits is reported so drift is
measured. Phase 3 adds the live checks (`/version`, red-monitor); phase 1 checks the
evidence chain itself."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rail import gitrepo
from rail.gates import GateResult, GateSpec, Stage
from rail.ledger import AttestationKind, LedgerError, Record, open_ledger
from rail.model import MANIFEST_NAME, load_rail_config


def _attestations(repo: Path, kind: AttestationKind) -> list[Record] | str:
    """Chronological attestations of `kind`, or the reason they cannot be read."""
    try:
        cfg = load_rail_config(repo)
        return open_ledger(repo).list(cfg.project, attestation=kind)
    except (FileNotFoundError, ValidationError):
        return f"{MANIFEST_NAME} unreadable"
    except LedgerError as exc:
        return str(exc)


def _on_history(
    stage: Stage, code: str, repo: Path, kind: AttestationKind, accept: Callable[[dict[str, Any]], str | None]
) -> GateResult:
    """Newest `kind` attestation: `accept(data)` returns a rejection reason or None; then its
    `sha` must be an ancestor of HEAD."""
    records = _attestations(repo, kind)
    if isinstance(records, str):
        return GateResult(stage, code, False, records)
    if not records:
        return GateResult(stage, code, False, f"no {kind.value} attestation")
    newest = records[-1]
    data = newest.data
    rejection = accept(data)
    if rejection:
        return GateResult(stage, code, False, f"{kind.value} {newest.digest[:19]}: {rejection}")
    sha = str(data.get("sha", ""))
    distance = gitrepo.distance(repo, sha) if sha else None
    if distance is None:
        return GateResult(
            stage, code, False, f"{kind.value} {newest.digest[:19]} for {sha[:12] or '?'} not on HEAD's history"
        )
    return GateResult(
        stage, code, True, f"{kind.value} {newest.digest[:19]} for {sha[:12]} at distance {distance}"
    )


def verdict(repo: Path) -> GateResult:
    def accept(data: dict[str, Any]) -> str | None:
        if not data.get("independent"):
            return "pre-review from the producing session, not an independent verdict"
        if data.get("verdict") != "approve":
            return f"verdict is {data.get('verdict')!r}"
        return None

    return _on_history(Stage.REVIEW, "verdict", repo, AttestationKind.REVIEW_VERDICT, accept)


def integrated(repo: Path) -> GateResult:
    return _on_history(Stage.INTEGRATE, "receipt", repo, AttestationKind.INTEGRATED, lambda d: None)


def released(repo: Path) -> GateResult:
    def accept(data: dict[str, Any]) -> str | None:
        missing = [k for k in ("version", "digest") if not data.get(k)]
        return f"missing {', '.join(missing)}" if missing else None

    result = _on_history(Stage.RELEASE, "released", repo, AttestationKind.RELEASED, accept)
    if result.passed:
        records = _attestations(repo, AttestationKind.RELEASED)
        assert not isinstance(records, str)
        return GateResult(
            result.stage, result.code, True, f"{result.details}, version {records[-1].data['version']}"
        )
    return result


def _newest(repo: Path, kind: AttestationKind) -> Record | None | str:
    records = _attestations(repo, kind)
    if isinstance(records, str):
        return records
    return records[-1] if records else None


def deployed(repo: Path) -> GateResult:
    release = _newest(repo, AttestationKind.RELEASED)
    if isinstance(release, str):
        return GateResult(Stage.DEPLOY, "deployed", False, release)
    if release is None:
        return GateResult(Stage.DEPLOY, "deployed", False, "no released attestation to deploy")
    deploy = _newest(repo, AttestationKind.DEPLOYED)
    if isinstance(deploy, str):
        return GateResult(Stage.DEPLOY, "deployed", False, deploy)
    if deploy is None:
        return GateResult(Stage.DEPLOY, "deployed", False, "no deployed attestation")
    expected = release.data.get("digest")
    actual = deploy.data.get("digest")
    if actual != expected:
        return GateResult(
            Stage.DEPLOY, "deployed", False, f"deployed digest {actual} differs from released {expected}"
        )
    return GateResult(Stage.DEPLOY, "deployed", True, f"deployed {expected} ({deploy.digest[:19]})")


def drill(repo: Path) -> GateResult:
    deploy = _newest(repo, AttestationKind.DEPLOYED)
    if isinstance(deploy, str):
        return GateResult(Stage.OBSERVE, "drill", False, deploy)
    if deploy is None:
        return GateResult(Stage.OBSERVE, "drill", False, "no deployed attestation to drill")
    rollbacks = _attestations(repo, AttestationKind.ROLLED_BACK)
    restores = _attestations(repo, AttestationKind.RESTORED)
    if isinstance(rollbacks, str) or isinstance(restores, str):
        return GateResult(Stage.OBSERVE, "drill", False, "ledger unreadable")
    after = [r for r in rollbacks if r.data.get("drill") and r.recorded_at > deploy.recorded_at]
    if not after:
        return GateResult(Stage.OBSERVE, "drill", False, "no rollback drill after the last deployment")
    restored = [r for r in restores if r.data.get("drill") and r.recorded_at > after[-1].recorded_at]
    if not restored:
        return GateResult(Stage.OBSERVE, "drill", False, "no restored attestation after the drill's rollback")
    return GateResult(
        Stage.OBSERVE, "drill", True, f"drill {after[-1].digest[:19]} → {restored[-1].digest[:19]}"
    )


def fulfilled(repo: Path) -> GateResult:
    deploy = _newest(repo, AttestationKind.DEPLOYED)
    if isinstance(deploy, str):
        return GateResult(Stage.LEARN, "fulfilled", False, deploy)
    done = _newest(repo, AttestationKind.FULFILLED)
    if isinstance(done, str):
        return GateResult(Stage.LEARN, "fulfilled", False, done)
    if done is None:
        return GateResult(Stage.LEARN, "fulfilled", False, "no fulfilled attestation")
    if deploy is not None and done.recorded_at < deploy.recorded_at:
        return GateResult(Stage.LEARN, "fulfilled", False, "fulfilled predates the last deployment")
    return GateResult(Stage.LEARN, "fulfilled", True, f"fulfilled {done.digest[:19]}")


GATES = [
    GateSpec(Stage.REVIEW, "verdict", verdict),
    GateSpec(Stage.INTEGRATE, "receipt", integrated),
    GateSpec(Stage.RELEASE, "released", released),
    GateSpec(Stage.DEPLOY, "deployed", deployed),
    GateSpec(Stage.OBSERVE, "drill", drill),
    GateSpec(Stage.LEARN, "fulfilled", fulfilled),
]
```

- [ ] **Step 4: Run the tests, expect PASS**

```bash
uv run pytest tests/test_gates_evidence.py -q
```
Expected: `6 passed`.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run pytest -q
git add src/rail/gates/evidence.py tests/test_gates_evidence.py
git commit -m "feat(gates): evidence gates for review, integrate, release, deploy, observe, learn"
```

--- checkpoint ---

## Batch 3: Registry by stage and the CLI surface (sequential)

### Task 3.1: Full registry, tier-scored `rail check [STAGE] [--ci] [--all]`, auto-discovered command modules

**Files:**
- Modify: `src/rail/gates/__init__.py:registry` (all stage modules, in stage order)
- Modify: `src/rail/cli.py` (full rewrite: group + auto-discovery of `rail.commands.*`)
- Create: `src/rail/commands/__init__.py`
- Create: `src/rail/commands/_options.py`
- Create: `src/rail/commands/check.py`
- Modify: `tests/test_gates.py` (registry expectations)
- Modify: `tests/test_cli.py` (full rewrite below)

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_cli.py` entirely:
```python
"""`rail check`: exit code is the verdict, JSON is the contract, the tier decides the scope."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.gates import build as build_gates
from rail.ledger import RECEIPTS_DIR, AttestationKind, Contract, Deliverable
from rail.ledger.file import FileLedger
from tests.helpers import conforming_tree, git, write_roster

CLOCK = lambda: datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # noqa: E731


@pytest.fixture(autouse=True)
def _no_real_gitleaks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (0, "no leaks found"))


def _bootstrap_repo(tmp_path: Path) -> Path:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"])
    FileLedger(repo / RECEIPTS_DIR, clock=CLOCK).contract_set(
        "red-alpha",
        Contract(
            objective="fixture",
            deliverables=[Deliverable(key="main", repository="hawkixs/red-alpha")],
        ),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c1",
    )
    return repo


def _dev_repo(tmp_path: Path) -> Path:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    write_roster(tmp_path, ["red-beta"])
    ledger = FileLedger(repo / RECEIPTS_DIR, clock=CLOCK)
    ledger.contract_set(
        "red-beta",
        Contract(
            objective="fixture",
            deliverables=[Deliverable(key="main", repository="hawkixs/red-beta")],
        ),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c1",
    )
    head = git(repo, "rev-parse", "HEAD")
    ledger.attest(
        "red-beta",
        AttestationKind.REVIEW_VERDICT,
        {"sha": head, "independent": True, "verdict": "approve"},
        issuer="reviewer",
        idempotency_key="v1",
    )
    ledger.attest(
        "red-beta", AttestationKind.INTEGRATED, {"sha": head}, issuer="op", idempotency_key="i1"
    )
    return repo


def test_version_and_command_discovery() -> None:
    out = CliRunner().invoke(main, ["--version"])
    assert out.exit_code == 0 and "rail" in out.output
    assert "check" in CliRunner().invoke(main, ["--help"]).output


def test_check_scores_a_bootstrap_repo_against_its_tier(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["check", "--repo", str(_bootstrap_repo(tmp_path))])
    assert out.exit_code == 0, out.output
    assert "tier=bootstrap" in out.output
    assert "design.spec" in out.output and "plan.plan" not in out.output


def test_check_scores_a_dev_repo_through_integrate(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["check", "--repo", str(_dev_repo(tmp_path)), "--json"])
    assert out.exit_code == 0, out.output
    report = json.loads(out.output)
    assert report["project"] == "red-beta" and report["tier"] == "dev" and report["declared"]
    assert report["stages"] == ["hygiene", "intent", "design", "plan", "build", "review", "integrate"]
    assert report["passed"] is True
    assert {g["stage"] for g in report["gates"]} == set(report["stages"])
    assert all(set(g) == {"stage", "code", "passed", "details", "exception", "skipped"} for g in report["gates"])


def test_check_exits_nonzero_and_names_the_failing_gate(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["check", "--repo", str(tmp_path)])
    assert out.exit_code == 1
    assert "FAIL  hygiene.rail_config" in out.output
    assert "tier=bootstrap (undeclared)" in out.output


def test_check_one_stage(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["check", "design", "--repo", str(_bootstrap_repo(tmp_path)), "--json"])
    assert out.exit_code == 0, out.output
    report = json.loads(out.output)
    assert report["stages"] == ["design"]
    assert [g["code"] for g in report["gates"]] == ["spec"]


def test_check_rejects_a_stage_outside_the_tier_unless_all(tmp_path: Path) -> None:
    repo = _bootstrap_repo(tmp_path)
    out = CliRunner().invoke(main, ["check", "deploy", "--repo", str(repo)])
    assert out.exit_code == 2 and "not applicable at tier bootstrap" in out.output
    out = CliRunner().invoke(main, ["check", "--all", "--repo", str(repo), "--json"])
    assert json.loads(out.output)["stages"][-1] == "learn"
    assert out.exit_code == 1  # the evidence gates fail on a bootstrap fixture, visibly


def test_check_ci_skips_workstation_gates(tmp_path: Path) -> None:
    repo = _bootstrap_repo(tmp_path)
    git(repo, "remote", "remove", "gitlab")
    assert CliRunner().invoke(main, ["check", "--repo", str(repo)]).exit_code == 1
    out = CliRunner().invoke(main, ["check", "--repo", str(repo), "--ci", "--json"])
    assert out.exit_code == 0, out.output
    skipped = [g["code"] for g in json.loads(out.output)["gates"] if g["skipped"]]
    assert skipped == ["remotes", "roster_entry"]


def test_check_shows_declared_exceptions(tmp_path: Path) -> None:
    repo = _dev_repo(tmp_path)
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text()
        + "gates:\n  review.verdict:\n    value: false\n    reason: reviewer arrives in phase 2\n"
    )
    out = CliRunner().invoke(main, ["check", "--repo", str(repo)])
    assert out.exit_code == 0, out.output
    assert "EXC   review.verdict" in out.output and "reviewer arrives in phase 2" in out.output
```

In `tests/test_gates.py::test_run_gates_returns_one_result_per_gate_and_never_raises`, replace the expected code list by the full registry order:
```python
    assert [r.gate_id for r in results] == [
        "hygiene.rail_config",
        "hygiene.docs_layout",
        "hygiene.claude_md",
        "hygiene.task_runner",
        "hygiene.settings",
        "hygiene.remotes",
        "hygiene.roster_entry",
        "hygiene.receipts",
        "intent.contract",
        "design.spec",
        "plan.plan",
        "build.tests",
        "build.lint",
        "build.secrets",
        "build.commits",
        "review.verdict",
        "integrate.receipt",
        "release.released",
        "deploy.deployed",
        "observe.drill",
        "learn.fulfilled",
    ]
```
(keep `assert all(not r.passed for r in results)`) and in `test_run_gates_all_pass_on_conforming_repo` restrict to the bootstrap stages with a contract:
```python
    from rail.gates import Stage
    from rail.ledger import RECEIPTS_DIR, Contract, Deliverable
    from rail.ledger.file import FileLedger
    from tests.helpers import conforming_tree, write_roster

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"])
    FileLedger(repo / RECEIPTS_DIR).contract_set(
        "red-alpha",
        Contract(objective="x", deliverables=[Deliverable(key="m", repository="hawkixs/red-alpha")]),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c1",
    )
    results = run_gates(repo, stages=[Stage.HYGIENE, Stage.INTENT, Stage.DESIGN])
    assert all(r.passed for r in results), [r for r in results if not r.passed]
```

- [ ] **Step 2: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_cli.py tests/test_gates.py -q
```
Expected: failures (`check` has no STAGE argument, registry lists hygiene only).

- [ ] **Step 3: Complete the registry in `src/rail/gates/__init__.py`**

Replace the `registry` function body:
```python
def registry() -> list[GateSpec]:
    """Gates in stage order. Imported lazily so `rail.gates` stays dependency-free."""
    from rail.gates import build, design, evidence, hygiene, intent, plan

    return [
        *hygiene.GATES,
        *intent.GATES,
        *design.GATES,
        *plan.GATES,
        *build.GATES,
        *evidence.GATES,
    ]
```

- [ ] **Step 4: Rewrite `src/rail/cli.py` with command auto-discovery**

```python
"""`rail` command line. The exit code is the verdict; `--json` is the contract for machines.

Every `rail.commands.<name>` module (not starting with `_`) exports `command`, a Click
command registered here — adding a command never edits this file.
"""

from __future__ import annotations

import importlib
import pkgutil

import click

from rail import __version__, commands


@click.group()
@click.version_option(__version__, prog_name="rail")
def main() -> None:
    """The ReD delivery rail."""


for _module in pkgutil.iter_modules(commands.__path__):
    if not _module.name.startswith("_"):
        main.add_command(importlib.import_module(f"rail.commands.{_module.name}").command)
```

Create `src/rail/commands/__init__.py`:
```python
"""One module per `rail` command; `rail.cli` discovers them."""
```

Create `src/rail/commands/_options.py`:
```python
"""Options shared by the commands."""

from __future__ import annotations

from pathlib import Path

import click

repo_option = click.option(
    "--repo",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Repository to work on.",
)
json_option = click.option("--json", "as_json", is_flag=True, help="Emit a machine-readable report.")
```

- [ ] **Step 5: Create `src/rail/commands/check.py`**

```python
"""`rail check [STAGE]`: the gates of the declared tier (or one stage), exit code = verdict."""

from __future__ import annotations

import json
from pathlib import Path

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.gates import GateResult, Stage, run_gates
from rail.model import load_rail_config
from rail.policy import applicable_stages, declared_tier

VERDICTS = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "exception": "EXC "}


def _verdict(result: GateResult) -> str:
    if result.skipped:
        return VERDICTS["skip"]
    if result.exception:
        return VERDICTS["exception"]
    return VERDICTS["pass"] if result.passed else VERDICTS["fail"]


def project_name(repo: Path) -> str:
    """The manifest's project, else the directory name (the manifest gate reports the rest)."""
    try:
        return load_rail_config(repo).project
    except (FileNotFoundError, ValidationError):
        return repo.absolute().name


def report(
    repo: Path, results: list[GateResult], stages: list[Stage], *, ci: bool
) -> dict[str, object]:
    tier = declared_tier(repo)
    return {
        "repo": str(repo),
        "project": project_name(repo),
        "tier": tier.value if tier else "bootstrap",
        "declared": tier is not None,
        "ci": ci,
        "stages": [s.value for s in stages],
        "passed": all(r.passed for r in results),
        "gates": [r.to_dict() for r in results],
    }


@click.command("check")
@click.argument("stage", required=False, type=click.Choice([s.value for s in Stage]))
@repo_option
@json_option
@click.option("--ci", is_flag=True, help="CI checkout: workstation-only gates are skipped, visibly.")
@click.option("--all", "everything", is_flag=True, help="Run every stage regardless of the tier.")
def command(stage: str | None, repo: Path, as_json: bool, ci: bool, everything: bool) -> None:
    """Run the gates against a repository and exit non-zero if one fails."""
    scope = list(Stage) if everything else list(applicable_stages(repo))
    if stage is not None:
        wanted = Stage(stage)
        if wanted not in scope:
            tier = declared_tier(repo)
            raise click.UsageError(
                f"stage {stage} is not applicable at tier {(tier or 'bootstrap')}; use --all to force it"
            )
        scope = [wanted]
    results = run_gates(repo, stages=scope, ci=ci)
    payload = report(repo, results, scope, ci=ci)
    if as_json:
        click.echo(json.dumps(payload, indent=2))
    else:
        declared = "" if payload["declared"] else " (undeclared)"
        click.echo(f"{payload['project']}  tier={payload['tier']}{declared}  stages={','.join(payload['stages'])}")
        for r in results:
            click.echo(f"{_verdict(r)}  {r.gate_id:<22} {r.details}")
        counted = [r for r in results if not r.skipped]
        click.echo(f"passed {sum(r.passed for r in counted)}/{len(counted)}")
    raise SystemExit(0 if payload["passed"] else 1)
```
Note: `tier` in the `UsageError` message is a `Tier` (StrEnum) or the string `"bootstrap"`; both format as the tier name.

- [ ] **Step 6: Run the tests, expect PASS**

```bash
uv run pytest tests/test_cli.py tests/test_gates.py -q
```
Expected: `13 passed` (9 CLI + 4 remaining gate tests... count whatever pytest prints, 0 failed). Then the whole suite:
```bash
uv run pytest -q
```
Expected: 0 failed.

- [ ] **Step 7: Prove it on this repository (expected to FAIL until Batch 5)**

```bash
uv run rail check; echo "exit=$?"
```
Expected: `red-rail  tier=dev  stages=hygiene,...,integrate`, `FAIL  intent.contract` (no receipts yet) and `exit=1`. This is the known red state until Batch 5 — do not "fix" it here.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run pytest -q
git add src/rail/gates/__init__.py src/rail/cli.py src/rail/commands tests/test_cli.py tests/test_gates.py
git commit -m "feat(cli): tier-scored rail check with stages, --ci and --all; command auto-discovery"
```

### Task 3.2: `rail attest`, `rail contract set`, `rail ledger list`

**Files:**
- Create: `src/rail/commands/attest.py`
- Create: `src/rail/commands/contract.py`
- Create: `src/rail/commands/ledger.py`
- Create: `tests/test_cli_ledger.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_ledger.py`:
```python
"""Writing and reading evidence from the command line, file ledger only in phase 1."""

import json
from pathlib import Path

from click.testing import CliRunner

from rail.cli import main
from rail.ledger import RECEIPTS_DIR, AttestationKind, RecordKind
from rail.ledger.file import FileLedger, load_receipt
from tests.helpers import conforming_tree, git


def _repo(tmp_path: Path) -> Path:
    return conforming_tree(tmp_path, "red-alpha", "bootstrap")


def test_contract_set_records_a_contract_from_the_canonical_remote(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    out = CliRunner().invoke(
        main,
        [
            "contract", "set", "--repo", str(repo),
            "--objective", "ship red-alpha",
            "--criterion", "rail check passes",
            "--criterion", "deployed once",
            "--reason", "bootstrap",
            "--json",
        ],
    )
    assert out.exit_code == 0, out.output
    record = json.loads(out.output)
    assert record["kind"] == "contract"
    assert record["payload"]["contract"]["deliverables"] == [
        {
            "key": "main",
            "repository": "hawkixs/red-alpha",
            "target_branch": "main",
            "required_checks": [],
            "review": {"required_approvals": 1, "allowed_reviewers": []},
        }
    ]
    assert record["payload"]["contract"]["acceptance_criteria"] == ["rail check passes", "deployed once"]
    records = FileLedger(repo / RECEIPTS_DIR).list("red-alpha", kind=RecordKind.CONTRACT)
    assert len(records) == 1 and records[0].verify()


def test_contract_set_is_idempotent_by_key(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    args = ["contract", "set", "--repo", str(repo), "--objective", "x", "--reason", "r", "--key", "c1"]
    assert CliRunner().invoke(main, args).exit_code == 0
    assert CliRunner().invoke(main, args).exit_code == 0
    assert len(list((repo / RECEIPTS_DIR).glob("*.json"))) == 1
    conflict = CliRunner().invoke(main, [*args[:-2], "--objective", "y", "--key", "c1"])
    assert conflict.exit_code == 1 and "idempotency" in conflict.output


def test_contract_set_needs_a_deliverable_when_no_github_remote(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    git(repo, "remote", "remove", "origin")
    out = CliRunner().invoke(main, ["contract", "set", "--repo", str(repo), "--objective", "x", "--reason", "r"])
    assert out.exit_code == 2 and "--deliverable" in out.output
    out = CliRunner().invoke(
        main,
        ["contract", "set", "--repo", str(repo), "--objective", "x", "--reason", "r", "--deliverable", "hawkixs/other:release"],
    )
    assert out.exit_code == 0, out.output


def test_attest_writes_a_receipt_with_typed_data(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    out = CliRunner().invoke(
        main,
        [
            "attest", "released", "--repo", str(repo),
            "--data", f"sha={head}", "--data", "version=1.0.0", "--data", "digest=sha256:abc",
            "--data", "drill=false", "--data", "attempts=2",
            "--issuer", "op", "--json",
        ],
    )
    assert out.exit_code == 0, out.output
    record = json.loads(out.output)
    assert record["payload"] == {
        "kind": "released",
        "data": {"sha": head, "version": "1.0.0", "digest": "sha256:abc", "drill": False, "attempts": 2},
    }
    assert record["idempotency_key"] == f"released:{head}"
    path = next((repo / RECEIPTS_DIR).glob("*-released-*.json"))
    assert load_receipt(path).verify()


def test_attest_data_json_and_explicit_key(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    out = CliRunner().invoke(
        main,
        ["attest", "incident_detected", "--repo", str(repo), "--data-json", '{"severity": "high"}', "--key", "inc-1", "--json"],
    )
    assert out.exit_code == 0, out.output
    record = json.loads(out.output)
    assert record["payload"]["data"] == {"severity": "high"} and record["idempotency_key"] == "inc-1"


def test_attest_without_sha_or_key_gets_a_random_key(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    out = CliRunner().invoke(main, ["attest", "fulfilled", "--repo", str(repo)])
    assert out.exit_code == 0, out.output
    assert "fulfilled:" in out.output and "docs/receipts/" in out.output


def test_attest_from_replays_a_receipt(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    elsewhere = FileLedger(tmp_path / "elsewhere")
    record = elsewhere.attest(
        "red-alpha", AttestationKind.DEPLOYED, {"digest": "sha256:abc"}, issuer="op", idempotency_key="d1"
    )
    receipt = next((tmp_path / "elsewhere").glob("*.json"))
    out = CliRunner().invoke(main, ["attest", "deployed", "--repo", str(repo), "--from", str(receipt), "--json"])
    assert out.exit_code == 0, out.output
    replayed = json.loads(out.output)
    assert replayed["idempotency_key"] == "d1" and replayed["payload"] == record.payload
    assert CliRunner().invoke(main, ["attest", "deployed", "--repo", str(repo), "--from", str(receipt)]).exit_code == 0
    assert len(list((repo / RECEIPTS_DIR).glob("*.json"))) == 1


def test_attest_refuses_a_brain_ledger_in_phase_1(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "rail.yaml").write_text((repo / "rail.yaml").read_text().replace("ledger: file", "ledger: brain"))
    out = CliRunner().invoke(main, ["attest", "fulfilled", "--repo", str(repo)])
    assert out.exit_code == 1 and "phase 2" in out.output


def test_ledger_list_reads_back(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    CliRunner().invoke(main, ["attest", "fulfilled", "--repo", str(repo), "--key", "f1"])
    CliRunner().invoke(main, ["attest", "deployed", "--repo", str(repo), "--key", "d1", "--data", "digest=sha256:x"])
    out = CliRunner().invoke(main, ["ledger", "list", "--repo", str(repo)])
    assert out.exit_code == 0 and "fulfilled" in out.output and "deployed" in out.output
    out = CliRunner().invoke(main, ["ledger", "list", "--repo", str(repo), "--attestation", "deployed", "--json"])
    rows = json.loads(out.output)
    assert [r["payload"]["kind"] for r in rows] == ["deployed"]
```

- [ ] **Step 2: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_cli_ledger.py -q
```
Expected: `Error: No such command 'contract'` in every test.

- [ ] **Step 3: Create `src/rail/commands/attest.py`**

```python
"""`rail attest KIND`: append one attestation to the ledger. Idempotent by key; a receipt can
be replayed with `--from` after a failed attestation (spec §7)."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.ledger import AttestationKind, LedgerError, Record, RecordKind, open_ledger
from rail.ledger.file import load_receipt, receipt_filename


_INT = re.compile(r"-?\d{1,12}")
_FLOAT = re.compile(r"-?\d+\.\d+")


def coerce(value: str) -> Any:
    """`key=value` data: true/false, short integers and decimals become typed; anything else
    (a version, a sha, a digest) stays text."""
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if _INT.fullmatch(value):
        return int(value)
    if _FLOAT.fullmatch(value):
        return float(value)
    return value


def parse_data(pairs: tuple[str, ...], data_json: str | None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if data_json:
        loaded = json.loads(data_json)
        if not isinstance(loaded, dict):
            raise click.UsageError("--data-json must be a JSON object")
        data.update(loaded)
    for pair in pairs:
        if "=" not in pair:
            raise click.UsageError(f"--data expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        data[key] = coerce(value)
    return data


def echo_record(record: Record, repo: Path, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(record.model_dump(mode="json"), indent=2))
        return
    label = record.attestation.value if record.attestation else record.kind.value
    click.echo(
        f"{label}  {record.idempotency_key}  {record.digest}  docs/receipts/{receipt_filename(record)}"
    )


@click.command("attest")
@click.argument("kind", type=click.Choice([k.value for k in AttestationKind]))
@repo_option
@click.option("--data", "pairs", multiple=True, help="key=value (repeatable).")
@click.option("--data-json", help="JSON object merged before --data pairs.")
@click.option("--issuer", default="operator", show_default=True)
@click.option("--key", "idempotency_key", help="Idempotency key (default: KIND:<sha> or KIND:<uuid>).")
@click.option(
    "--from",
    "replay",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Replay a receipt file: same key, same payload, same issuer.",
)
@json_option
def command(
    kind: str,
    repo: Path,
    pairs: tuple[str, ...],
    data_json: str | None,
    issuer: str,
    idempotency_key: str | None,
    replay: Path | None,
    as_json: bool,
) -> None:
    """Record an attestation (released, deployed, rolled_back, …) in the project's ledger."""
    attestation = AttestationKind(kind)
    try:
        ledger = open_ledger(repo)
        from rail.model import load_rail_config

        project = load_rail_config(repo).project
        if replay is not None:
            source = load_receipt(replay)
            if source.kind is not RecordKind.ATTESTATION or source.attestation is not attestation:
                raise click.UsageError(f"{replay.name} is not a {kind} attestation")
            record = ledger.attest(
                project, attestation, source.data, issuer=source.issuer, idempotency_key=source.idempotency_key
            )
        else:
            data = parse_data(pairs, data_json)
            key = idempotency_key or (
                f"{kind}:{data['sha']}" if data.get("sha") else f"{kind}:{uuid.uuid4()}"
            )
            record = ledger.attest(project, attestation, data, issuer=issuer, idempotency_key=key)
    except (FileNotFoundError, ValidationError, LedgerError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    echo_record(record, repo, as_json)
```

- [ ] **Step 4: Create `src/rail/commands/contract.py`**

```python
"""`rail contract set`: the intent stage — a delivery contract for this repository, shaped
like brain-v42's `brain_delivery_contract_set` so phase 2 maps 1:1."""

from __future__ import annotations

import re
from pathlib import Path

import click
from pydantic import ValidationError

from rail import gitrepo
from rail.commands._options import json_option, repo_option
from rail.commands.attest import echo_record
from rail.ledger import Contract, Deliverable, LedgerError, open_ledger
from rail.model import load_rail_config

_GITHUB_SLUG = re.compile(r"github\.com[:/](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?$")


def canonical_slug(repo: Path) -> str | None:
    """`owner/name` of the GitHub remote, whatever its name."""
    for url in gitrepo.remotes(repo).values():
        match = _GITHUB_SLUG.search(url)
        if match:
            return match.group("slug")
    return None


def parse_deliverable(spec: str) -> Deliverable:
    """`owner/name[:key]` → Deliverable on `main`."""
    repository, _, key = spec.partition(":")
    return Deliverable(key=key or "main", repository=repository)


@click.group("contract")
def command() -> None:
    """Delivery contracts (stage 1, intent)."""


@command.command("set")
@repo_option
@click.option("--objective", required=True)
@click.option("--criterion", "criteria", multiple=True, help="Acceptance criterion (repeatable).")
@click.option("--constraint", "constraints", multiple=True, help="Constraint (repeatable).")
@click.option(
    "--deliverable",
    "deliverables",
    multiple=True,
    help="owner/name[:key] (repeatable; default: the GitHub remote, key `main`).",
)
@click.option("--reason", required=True, help="Why this contract (or this amendment) exists.")
@click.option("--issuer", default="operator", show_default=True)
@click.option("--key", "idempotency_key", help="Idempotency key (default: contract:<project>:<n>).")
@json_option
def set_(
    repo: Path,
    objective: str,
    criteria: tuple[str, ...],
    constraints: tuple[str, ...],
    deliverables: tuple[str, ...],
    reason: str,
    issuer: str,
    idempotency_key: str | None,
    as_json: bool,
) -> None:
    """Create or amend the project's delivery contract in the ledger."""
    if not deliverables:
        slug = canonical_slug(repo)
        if slug is None:
            raise click.UsageError("no GitHub remote found; pass --deliverable owner/name[:key]")
        deliverables = (slug,)
    try:
        cfg = load_rail_config(repo)
        ledger = open_ledger(repo)
        contract = Contract(
            objective=objective,
            acceptance_criteria=list(criteria),
            constraints=list(constraints),
            deliverables=[parse_deliverable(d) for d in deliverables],
        )
        from rail.ledger import RecordKind

        key = idempotency_key or f"contract:{cfg.project}:{len(ledger.list(cfg.project, kind=RecordKind.CONTRACT)) + 1}"
        record = ledger.contract_set(cfg.project, contract, reason=reason, issuer=issuer, idempotency_key=key)
    except (FileNotFoundError, ValidationError, LedgerError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    echo_record(record, repo, as_json)
```

- [ ] **Step 5: Create `src/rail/commands/ledger.py`**

```python
"""`rail ledger list`: read the project's evidence back, chronologically."""

from __future__ import annotations

import json
from pathlib import Path

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.ledger import AttestationKind, LedgerError, RecordKind, open_ledger
from rail.model import load_rail_config


@click.group("ledger")
def command() -> None:
    """Read the ledger."""


@command.command("list")
@repo_option
@click.option("--kind", type=click.Choice([k.value for k in RecordKind]))
@click.option("--attestation", type=click.Choice([k.value for k in AttestationKind]))
@json_option
def list_(repo: Path, kind: str | None, attestation: str | None, as_json: bool) -> None:
    """List the records of this project, oldest first."""
    try:
        cfg = load_rail_config(repo)
        records = open_ledger(repo).list(
            cfg.project,
            kind=RecordKind(kind) if kind else None,
            attestation=AttestationKind(attestation) if attestation else None,
        )
    except (FileNotFoundError, ValidationError, LedgerError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    if as_json:
        click.echo(json.dumps([r.model_dump(mode="json") for r in records], indent=2))
        return
    for r in records:
        label = r.attestation.value if r.attestation else r.kind.value
        summary = json.dumps(r.data, sort_keys=True)[:60]
        click.echo(f"{r.recorded_at.isoformat()}  {label:<18} {r.idempotency_key:<28} {summary}")
```

- [ ] **Step 6: Run the tests, expect PASS**

```bash
uv run pytest tests/test_cli_ledger.py -q
```
Expected: `9 passed`.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run pytest -q
git add src/rail/commands/attest.py src/rail/commands/contract.py src/rail/commands/ledger.py tests/test_cli_ledger.py
git commit -m "feat(cli): rail attest, rail contract set and rail ledger list on the file ledger"
```

--- checkpoint ---

## Batch 4: Products — audit, metrics, template + new/upgrade, CI, skills (parallel)

### Task 4.1: `rail audit` — the repository × stage matrix, golden-tested

**Files:**
- Create: `src/rail/audit.py`
- Create: `src/rail/commands/audit.py`
- Create: `tests/test_audit.py`
- Create: `tests/golden/audit-matrix.json`
- Modify: `tests/helpers.py` (append `with_evidence`)

- [ ] **Step 1: Append the evidence helper to `tests/helpers.py`**

```python
def with_evidence(repo: Path, *, through: str, clock: Callable[[], datetime] | None = None) -> None:
    """Write the file-ledger evidence a conforming project has at a given stage: `design`
    (contract), `integrate` (+ independent approving verdict and integration receipt on HEAD)
    or `learn` (+ release, deployment, rollback drill and fulfilment)."""
    from rail.ledger import RECEIPTS_DIR, AttestationKind, Contract, Deliverable
    from rail.ledger.file import FileLedger

    name = repo.name
    head = git(repo, "rev-parse", "HEAD")
    ticks = [datetime(2026, 9, 15, 9, 0, tzinfo=UTC) + timedelta(minutes=i) for i in range(50)]
    ledger = FileLedger(repo / RECEIPTS_DIR, clock=clock or (lambda: ticks.pop(0)))
    contract = Contract(
        objective=f"ship {name}",
        acceptance_criteria=["rail check passes"],
        deliverables=[Deliverable(key="main", repository=f"hawkixs/{name}")],
    )
    ledger.contract_set(name, contract, reason="bootstrap", issuer="op", idempotency_key="c1")
    if through == "design":
        return
    ledger.attest(
        name,
        AttestationKind.REVIEW_VERDICT,
        {"sha": head, "independent": True, "verdict": "approve"},
        issuer="reviewer",
        idempotency_key="v1",
    )
    ledger.attest(name, AttestationKind.INTEGRATED, {"sha": head}, issuer="op", idempotency_key="i1")
    if through == "integrate":
        return
    ledger.attest(
        name,
        AttestationKind.RELEASED,
        {"sha": head, "version": "1.0.0", "digest": "sha256:aaa"},
        issuer="op",
        idempotency_key="r1",
    )
    ledger.attest(
        name, AttestationKind.DEPLOYED, {"sha": head, "digest": "sha256:aaa"}, issuer="op", idempotency_key="d1"
    )
    ledger.attest(name, AttestationKind.ROLLED_BACK, {"drill": True}, issuer="op", idempotency_key="rb1")
    ledger.attest(name, AttestationKind.RESTORED, {"drill": True}, issuer="op", idempotency_key="rs1")
    ledger.attest(name, AttestationKind.FULFILLED, {}, issuer="op", idempotency_key="f1")
```
Add to the imports at the top of `tests/helpers.py`: `from collections.abc import Callable` and `from datetime import UTC, datetime, timedelta`.

- [ ] **Step 2: Write the failing tests**

`tests/test_audit.py`:
```python
"""The audit matrix: every project scored against its declared tier, drift made diffable."""

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.audit import audit_paths, audit_project, discover, matrix, render_table, template_version
from rail.cli import main
from rail.gates import build as build_gates
from tests.helpers import conforming_tree, init_repo, with_evidence, write_manifest, write_roster

GOLDEN = Path(__file__).parent / "golden" / "audit-matrix.json"


@pytest.fixture(autouse=True)
def _no_real_gitleaks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (0, "no leaks found"))


def fixture_root(root: Path) -> Path:
    """Four projects: conforming bootstrap, conforming dev (templated), prod with a declared
    exception and missing post-integration evidence, and a bare repository without manifest."""
    alpha = conforming_tree(root, "red-alpha", "bootstrap")
    with_evidence(alpha, through="design")
    beta = conforming_tree(root, "red-beta", "dev")
    with_evidence(beta, through="integrate")
    (beta / ".copier-answers.yml").write_text("_commit: v0.1.0\n_src_path: red-rail\nproject: red-beta\n")
    gamma = conforming_tree(root, "red-gamma", "prod")
    with_evidence(gamma, through="integrate")
    write_manifest(
        gamma,
        project="red-gamma",
        tier="prod",
        deploy=True,
        gates={"review.verdict": (False, "reviewer arrives in phase 2")},
    )
    init_repo(root / "projects" / "red-delta")
    (root / "projects" / "not-a-project").mkdir()
    (root / "projects" / "notes.txt").write_text("x")
    write_roster(root, ["red-alpha", "red-beta", "red-gamma", "red-delta"])
    return root / "projects"


def test_discover_keeps_git_repositories_and_manifests_only(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    assert [p.name for p in discover(projects)] == ["red-alpha", "red-beta", "red-delta", "red-gamma"]


def test_template_version_reads_copier_answers(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    assert template_version(projects / "red-beta") == "v0.1.0"
    assert template_version(projects / "red-alpha") is None


def test_audit_project_scores_against_the_declared_tier(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    alpha = audit_project(projects / "red-alpha")
    assert (alpha.declared_tier, alpha.tier_used, alpha.passed, alpha.applicable) == ("bootstrap", "bootstrap", 10, 10)
    assert [s.status for s in alpha.stages] == ["pass", "pass", "pass"] + ["n/a"] * 8
    gamma = audit_project(projects / "red-gamma")
    assert (gamma.passed, gamma.applicable) == (17, 21)
    assert gamma.exceptions == ["review.verdict: reviewer arrives in phase 2"]
    assert {s.stage: s.status for s in gamma.stages}["review"] == "exception"
    delta = audit_project(projects / "red-delta")
    assert (delta.declared_tier, delta.tier_used, delta.passed, delta.applicable) == (None, "bootstrap", 3, 10)


def test_matrix_matches_the_golden_snapshot(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    got = matrix(audit_paths([projects]))
    if os.environ.get("RAIL_UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
    assert got == json.loads(GOLDEN.read_text())


def test_audit_paths_accepts_projects_and_roots(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    assert [a.name for a in audit_paths([projects / "red-beta", projects / "red-alpha"])] == ["red-beta", "red-alpha"]
    assert len(audit_paths([projects])) == 4
    assert audit_paths([projects / "not-a-project"]) == []


def test_render_table_is_one_row_per_project(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    table = render_table(audit_paths([projects]))
    lines = [line for line in table.splitlines() if line.startswith("red-")]
    assert len(lines) == 4
    assert "red-gamma" in table and "!" in table and "·" in table
    assert "review.verdict: reviewer arrives in phase 2" in table


def test_cli_audit_json_and_matrix(tmp_path: Path) -> None:
    projects = fixture_root(tmp_path)
    out = CliRunner().invoke(main, ["audit", str(projects), "--json"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.output)
    assert payload["schema_version"] == 1 and len(payload["projects"]) == 4
    assert {"name", "path", "declared_tier", "tier_used", "template_version", "stages", "passed", "applicable", "exceptions", "gates"} <= set(payload["projects"][0])
    out = CliRunner().invoke(main, ["audit", str(projects), "--matrix"])
    assert json.loads(out.output) == json.loads(GOLDEN.read_text())
    out = CliRunner().invoke(main, ["audit", str(projects / "not-a-project")])
    assert out.exit_code == 2 and "no project found" in out.output
```

- [ ] **Step 3: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_audit.py -q
```
Expected: `ModuleNotFoundError: No module named 'rail.audit'`.

- [ ] **Step 4: Create `src/rail/audit.py`**

```python
"""`rail audit`: the repository × stage matrix. Every project is scored against its declared
tier (`bootstrap` when undeclared), declared exceptions are shown, the template version
comes from `.copier-answers.yml` — drift becomes reproducible, versioned, diffable."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from rail.gates import Stage, run_gates
from rail.model import Tier
from rail.policy import applicable_stages, declared_tier

SCHEMA_VERSION = 1
ANSWERS_FILE = ".copier-answers.yml"
Status = Literal["pass", "fail", "exception", "n/a"]
SYMBOLS: dict[str, str] = {"pass": "✓", "fail": "✗", "exception": "!", "n/a": "·"}
COLUMNS = {
    Stage.HYGIENE: "hyg",
    Stage.INTENT: "int",
    Stage.DESIGN: "dsg",
    Stage.PLAN: "pln",
    Stage.BUILD: "bld",
    Stage.REVIEW: "rev",
    Stage.INTEGRATE: "itg",
    Stage.RELEASE: "rel",
    Stage.DEPLOY: "dpl",
    Stage.OBSERVE: "obs",
    Stage.LEARN: "lrn",
}


@dataclass(frozen=True, slots=True)
class StageScore:
    stage: str
    status: Status
    passed: int
    total: int


@dataclass(frozen=True, slots=True)
class ProjectAudit:
    name: str
    path: str
    declared_tier: str | None
    tier_used: str
    template_version: str | None
    stages: list[StageScore]
    passed: int
    applicable: int
    exceptions: list[str]
    gates: list[dict[str, object]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def template_version(repo: Path) -> str | None:
    path = repo / ANSWERS_FILE
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return None
    commit = data.get("_commit") if isinstance(data, dict) else None
    return str(commit) if commit else None


def is_project(path: Path) -> bool:
    return path.is_dir() and ((path / ".git").exists() or (path / "rail.yaml").is_file())


def discover(root: Path) -> list[Path]:
    """Direct children that are projects (a `.git` or a `rail.yaml`), symlinks followed."""
    return sorted(
        (child for child in root.iterdir() if not child.name.startswith(".") and is_project(child)),
        key=lambda p: p.name,
    )


def audit_project(repo: Path, *, ci: bool = False) -> ProjectAudit:
    results = run_gates(repo, ci=ci)
    tier = declared_tier(repo)
    applicable = set(applicable_stages(repo))
    stages: list[StageScore] = []
    passed = total = 0
    exceptions: list[str] = []
    for stage in Stage:
        mine = [r for r in results if r.stage is stage and not r.skipped]
        if stage not in applicable:
            stages.append(StageScore(stage.value, "n/a", 0, len(mine)))
            continue
        ok = sum(1 for r in mine if r.passed)
        passed += ok
        total += len(mine)
        if ok == len(mine):
            status: Status = "exception" if any(r.exception for r in mine) else "pass"
        else:
            status = "fail"
        exceptions.extend(f"{r.gate_id}: {r.exception}" for r in mine if r.exception)
        stages.append(StageScore(stage.value, status, ok, len(mine)))
    return ProjectAudit(
        name=repo.name,
        path=str(repo),
        declared_tier=tier.value if tier else None,
        tier_used=(tier or Tier.BOOTSTRAP).value,
        template_version=template_version(repo),
        stages=stages,
        passed=passed,
        applicable=total,
        exceptions=exceptions,
        gates=[r.to_dict() for r in results],
    )


def audit_paths(paths: Iterable[Path], *, ci: bool = False) -> list[ProjectAudit]:
    """Each path is a project, or a directory whose project children are audited."""
    audits: list[ProjectAudit] = []
    for path in paths:
        if is_project(path):
            audits.append(audit_project(path, ci=ci))
        elif path.is_dir():
            audits.extend(audit_project(child, ci=ci) for child in discover(path))
    return audits


def matrix(audits: list[ProjectAudit]) -> dict[str, Any]:
    """The golden shape: statuses and scores, no free-text details, no absolute paths."""
    return {
        "schema_version": SCHEMA_VERSION,
        "projects": [
            {
                "name": a.name,
                "declared_tier": a.declared_tier,
                "tier_used": a.tier_used,
                "template_version": a.template_version,
                "score": {"passed": a.passed, "applicable": a.applicable},
                "stages": {s.stage: s.status for s in a.stages},
                "exceptions": a.exceptions,
            }
            for a in audits
        ],
    }


def to_json(audits: list[ProjectAudit]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "projects": [a.to_dict() for a in audits]}


def render_table(audits: list[ProjectAudit]) -> str:
    width = max((len(a.name) for a in audits), default=7)
    header = f"{'project':<{width}}  {'tier':<10} {'template':<10} " + " ".join(COLUMNS.values()) + "  score"
    lines = [header, "-" * len(header)]
    for a in audits:
        tier = a.tier_used + ("" if a.declared_tier else "?")
        cells = " ".join(f"{SYMBOLS[s.status]:^3}" for s in a.stages)
        lines.append(
            f"{a.name:<{width}}  {tier:<10} {(a.template_version or '-'):<10} {cells}  {a.passed}/{a.applicable}"
        )
    lines.append("")
    lines.append("✓ pass   ✗ fail   ! declared exception   · not applicable to the tier   tier? undeclared")
    for a in audits:
        for exc in a.exceptions:
            lines.append(f"  {a.name}: {exc}")
    return "\n".join(lines)
```

- [ ] **Step 5: Create `src/rail/commands/audit.py`**

```python
"""`rail audit [PATHS...]`: the matrix over projects (default: the parent directory)."""

from __future__ import annotations

import json
from pathlib import Path

import click

from rail.audit import audit_paths, matrix, render_table, to_json
from rail.commands._options import json_option


@click.command("audit")
@click.argument("paths", nargs=-1, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--ci", is_flag=True, help="Skip workstation-only gates (remotes, roster).")
@click.option("--matrix", "as_matrix", is_flag=True, help="Only statuses and scores (the golden shape).")
@json_option
def command(paths: tuple[Path, ...], ci: bool, as_matrix: bool, as_json: bool) -> None:
    """Score every project against its declared tier. A report: the exit code is always 0."""
    targets = list(paths) or [Path.cwd().parent]
    audits = audit_paths(targets, ci=ci)
    if not audits:
        raise click.UsageError("no project found (a project has a .git or a rail.yaml)")
    if as_matrix:
        click.echo(json.dumps(matrix(audits), indent=2, sort_keys=True))
    elif as_json:
        click.echo(json.dumps(to_json(audits), indent=2))
    else:
        click.echo(render_table(audits))
```

- [ ] **Step 6: Generate the golden file, then review it against the expected matrix**

```bash
mkdir -p tests/golden && RAIL_UPDATE_GOLDEN=1 uv run pytest tests/test_audit.py::test_matrix_matches_the_golden_snapshot -q
cat tests/golden/audit-matrix.json
```
Expected content (order and values must match exactly; if not, the fixture or a gate is wrong — fix the code, not the golden):
```json
{
  "projects": [
    {
      "declared_tier": "bootstrap",
      "exceptions": [],
      "name": "red-alpha",
      "score": {"applicable": 10, "passed": 10},
      "stages": {"build": "n/a", "deploy": "n/a", "design": "pass", "hygiene": "pass", "integrate": "n/a", "intent": "pass", "learn": "n/a", "observe": "n/a", "plan": "n/a", "release": "n/a", "review": "n/a"},
      "template_version": null,
      "tier_used": "bootstrap"
    },
    {
      "declared_tier": "dev",
      "exceptions": [],
      "name": "red-beta",
      "score": {"applicable": 17, "passed": 17},
      "stages": {"build": "pass", "deploy": "n/a", "design": "pass", "hygiene": "pass", "integrate": "pass", "intent": "pass", "learn": "n/a", "observe": "n/a", "plan": "pass", "release": "n/a", "review": "pass"},
      "template_version": "v0.1.0",
      "tier_used": "dev"
    },
    {
      "declared_tier": null,
      "exceptions": [],
      "name": "red-delta",
      "score": {"applicable": 10, "passed": 3},
      "stages": {"build": "n/a", "deploy": "n/a", "design": "fail", "hygiene": "fail", "integrate": "n/a", "intent": "fail", "learn": "n/a", "observe": "n/a", "plan": "n/a", "release": "n/a", "review": "n/a"},
      "template_version": null,
      "tier_used": "bootstrap"
    },
    {
      "declared_tier": "prod",
      "exceptions": ["review.verdict: reviewer arrives in phase 2"],
      "name": "red-gamma",
      "score": {"applicable": 21, "passed": 17},
      "stages": {"build": "pass", "deploy": "fail", "design": "pass", "hygiene": "pass", "integrate": "pass", "intent": "pass", "learn": "fail", "observe": "fail", "plan": "pass", "release": "fail", "review": "exception"},
      "template_version": null,
      "tier_used": "prod"
    }
  ],
  "schema_version": 1
}
```
(red-delta's 3 hygiene passes are `remotes`, `roster_entry` and `receipts`; the JSON on disk is pretty-printed with one key per line — the content is what matters.)

- [ ] **Step 7: Run the tests, expect PASS**

```bash
uv run pytest tests/test_audit.py tests/test_helpers.py -q
```
Expected: `11 passed`.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run pytest -q
git add src/rail/audit.py src/rail/commands/audit.py tests/test_audit.py tests/golden/audit-matrix.json tests/helpers.py
git commit -m "feat(audit): repository x stage matrix scored against the declared tier, golden-tested"
```

### Task 4.2: `rail metrics` — four DORA metrics and conformance from the ledger

**Files:**
- Create: `src/rail/metrics.py`
- Create: `src/rail/commands/metrics.py`
- Create: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_metrics.py`:
```python
"""DORA from evidence timestamps, nothing else: no separate instrumentation."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.gates import build as build_gates
from rail.ledger import RECEIPTS_DIR, AttestationKind, Contract, Deliverable
from rail.ledger.file import FileLedger
from rail.metrics import compute_metrics
from tests.helpers import conforming_tree, git, write_roster

T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # == the fixture commit timestamp


@pytest.fixture(autouse=True)
def _no_real_gitleaks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (0, "no leaks found"))


def _ledger_at(repo: Path, when: datetime) -> FileLedger:
    return FileLedger(repo / RECEIPTS_DIR, clock=lambda: when)


def _history(tmp_path: Path) -> Path:
    """contract T0; deployed T0+48h then rolled back (real) at +49h; deployed again at +72h;
    incident at +80h restored at +82h; a drill rollback at +90h that must not count."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"])
    head = git(repo, "rev-parse", "HEAD")
    h = timedelta(hours=1)
    _ledger_at(repo, T0).contract_set(
        "red-alpha",
        Contract(objective="x", deliverables=[Deliverable(key="m", repository="hawkixs/red-alpha")]),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c1",
    )
    _ledger_at(repo, T0 + 48 * h).attest("red-alpha", AttestationKind.DEPLOYED, {"sha": head, "digest": "sha256:a"}, issuer="op", idempotency_key="d1")
    _ledger_at(repo, T0 + 49 * h).attest("red-alpha", AttestationKind.ROLLED_BACK, {"drill": False}, issuer="op", idempotency_key="rb1")
    _ledger_at(repo, T0 + 72 * h).attest("red-alpha", AttestationKind.DEPLOYED, {"sha": head, "digest": "sha256:b"}, issuer="op", idempotency_key="d2")
    _ledger_at(repo, T0 + 80 * h).attest("red-alpha", AttestationKind.INCIDENT_DETECTED, {}, issuer="op", idempotency_key="inc1")
    _ledger_at(repo, T0 + 82 * h).attest("red-alpha", AttestationKind.RESTORED, {"drill": False}, issuer="op", idempotency_key="rs1")
    _ledger_at(repo, T0 + 90 * h).attest("red-alpha", AttestationKind.ROLLED_BACK, {"drill": True}, issuer="op", idempotency_key="rb2")
    return repo


def test_metrics_from_the_history(tmp_path: Path) -> None:
    repo = _history(tmp_path)
    m = compute_metrics(FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0 + timedelta(hours=100))
    assert m.deployments == 2
    assert m.deployment_frequency_per_week == pytest.approx(2 / (30 / 7), abs=1e-3)  # rounded to 3 places
    assert m.lead_time_commit_to_deploy_hours == 60.0  # median of 48 and 72
    assert m.lead_time_contract_to_deploy_hours == 60.0
    assert m.change_failure_rate == 0.5  # the drill does not count
    assert m.recovery_time_hours == 2.0
    assert (m.conformance.passed, m.conformance.applicable, m.conformance.exceptions) == (10, 10, 0)


def test_metrics_window_excludes_old_deployments(tmp_path: Path) -> None:
    repo = _history(tmp_path)
    m = compute_metrics(FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0 + timedelta(days=60), window_days=30)
    assert m.deployments == 0 and m.deployment_frequency_per_week == 0.0
    assert m.lead_time_commit_to_deploy_hours is None and m.change_failure_rate is None


def test_metrics_without_evidence_are_explicit(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    m = compute_metrics(FileLedger(repo / RECEIPTS_DIR), "red-alpha", repo, now=T0)
    assert m.deployments == 0 and m.recovery_time_hours is None
    assert m.conformance.passed < m.conformance.applicable  # no contract yet


def test_cli_metrics(tmp_path: Path) -> None:
    repo = _history(tmp_path)
    out = CliRunner().invoke(main, ["metrics", "--repo", str(repo), "--json"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.output)
    assert payload["project"] == "red-alpha" and payload["deployments"] == 2
    assert set(payload["conformance"]) == {"passed", "applicable", "exceptions", "score"}
    out = CliRunner().invoke(main, ["metrics", "--repo", str(repo)])
    assert out.exit_code == 0 and "change failure rate" in out.output
```

- [ ] **Step 2: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_metrics.py -q
```
Expected: `ModuleNotFoundError: No module named 'rail.metrics'`.

- [ ] **Step 3: Create `src/rail/metrics.py`**

```python
"""`rail metrics`: the four DORA metrics and the conformance score, computed by red-rail from
the ledger (brain never computes a delivery metric — ADR-0001). The timestamps are those of
the evidence; a rollback marked `drill` never counts as a failure."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from rail import gitrepo
from rail.gates import run_gates
from rail.ledger import AttestationKind, Ledger, RecordKind
from rail.policy import applicable_stages

FAILURE_WINDOW = timedelta(hours=24)


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 2)


@dataclass(frozen=True, slots=True)
class Conformance:
    passed: int
    applicable: int
    exceptions: int

    @property
    def score(self) -> float | None:
        return round(self.passed / self.applicable, 3) if self.applicable else None


@dataclass(frozen=True, slots=True)
class Metrics:
    project: str
    window_days: int
    since: str
    deployments: int
    deployment_frequency_per_week: float
    lead_time_commit_to_deploy_hours: float | None
    lead_time_contract_to_deploy_hours: float | None
    change_failure_rate: float | None
    recovery_time_hours: float | None
    conformance: Conformance

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["conformance"]["score"] = self.conformance.score
        return data


def compute_metrics(
    ledger: Ledger,
    project: str,
    repo: Path,
    *,
    now: datetime,
    window_days: int = 30,
    ci: bool = False,
) -> Metrics:
    since = now - timedelta(days=window_days)
    deployed = [r for r in ledger.list(project, attestation=AttestationKind.DEPLOYED) if r.recorded_at >= since]
    rollbacks = [
        r for r in ledger.list(project, attestation=AttestationKind.ROLLED_BACK) if not r.data.get("drill")
    ]
    incidents = ledger.list(project, attestation=AttestationKind.INCIDENT_DETECTED)
    restores = [r for r in ledger.list(project, attestation=AttestationKind.RESTORED) if not r.data.get("drill")]
    contracts = ledger.list(project, kind=RecordKind.CONTRACT)

    commit_leads: list[float] = []
    for d in deployed:
        sha = str(d.data.get("sha", ""))
        committed = gitrepo.commit_timestamp(repo, sha) if sha else None
        if committed is not None:
            commit_leads.append(_hours(d.recorded_at - committed))
    contract_leads = [_hours(d.recorded_at - contracts[0].recorded_at) for d in deployed] if contracts else []
    failures = sum(
        1
        for d in deployed
        if any(d.recorded_at < r.recorded_at <= d.recorded_at + FAILURE_WINDOW for r in rollbacks)
    )
    recoveries: list[float] = []
    for incident in incidents:
        after = [r for r in restores if r.recorded_at > incident.recorded_at]
        if after:
            recoveries.append(_hours(after[0].recorded_at - incident.recorded_at))

    results = [r for r in run_gates(repo, stages=applicable_stages(repo), ci=ci) if not r.skipped]
    conformance = Conformance(
        passed=sum(1 for r in results if r.passed),
        applicable=len(results),
        exceptions=sum(1 for r in results if r.exception),
    )
    return Metrics(
        project=project,
        window_days=window_days,
        since=since.isoformat(),
        deployments=len(deployed),
        deployment_frequency_per_week=round(len(deployed) / (window_days / 7), 3),
        lead_time_commit_to_deploy_hours=median(commit_leads) if commit_leads else None,
        lead_time_contract_to_deploy_hours=median(contract_leads) if contract_leads else None,
        change_failure_rate=round(failures / len(deployed), 3) if deployed else None,
        recovery_time_hours=median(recoveries) if recoveries else None,
        conformance=conformance,
    )
```

- [ ] **Step 4: Create `src/rail/commands/metrics.py`**

```python
"""`rail metrics`: DORA + conformance for one repository, from its ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import click
from pydantic import ValidationError

from rail.commands._options import json_option, repo_option
from rail.ledger import LedgerError, open_ledger
from rail.metrics import compute_metrics
from rail.model import load_rail_config


def _fmt(value: float | None, unit: str) -> str:
    return "n/a" if value is None else f"{value:g} {unit}".rstrip()


@click.command("metrics")
@repo_option
@click.option("--window", type=int, default=30, show_default=True, help="Window in days.")
@click.option("--ci", is_flag=True, help="Skip workstation-only gates in the conformance score.")
@json_option
def command(repo: Path, window: int, ci: bool, as_json: bool) -> None:
    """Lead time, deployment frequency, change failure rate, recovery time, conformance."""
    try:
        cfg = load_rail_config(repo)
        ledger = open_ledger(repo)
        metrics = compute_metrics(
            ledger, cfg.project, repo, now=datetime.now(UTC), window_days=window, ci=ci
        )
    except (FileNotFoundError, ValidationError, LedgerError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    if as_json:
        click.echo(json.dumps(metrics.to_dict(), indent=2))
        return
    c = metrics.conformance
    click.echo(f"{metrics.project}  window {window}d since {metrics.since}")
    click.echo(f"deployments                 {metrics.deployments} ({metrics.deployment_frequency_per_week:g}/week)")
    click.echo(f"lead time commit → deploy   {_fmt(metrics.lead_time_commit_to_deploy_hours, 'h')}")
    click.echo(f"lead time contract → deploy {_fmt(metrics.lead_time_contract_to_deploy_hours, 'h')}")
    click.echo(f"change failure rate         {_fmt(metrics.change_failure_rate, '')}")
    click.echo(f"recovery time               {_fmt(metrics.recovery_time_hours, 'h')}")
    click.echo(f"conformance                 {c.passed}/{c.applicable} ({c.exceptions} declared exception(s))")
```

- [ ] **Step 5: Run the tests, expect PASS**

```bash
uv run pytest tests/test_metrics.py -q
```
Expected: `4 passed`.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run pytest -q
git add src/rail/metrics.py src/rail/commands/metrics.py tests/test_metrics.py
git commit -m "feat(metrics): four DORA metrics and conformance computed from the ledger"
```

### Task 4.3: copier template, `rail new` (tree + contract + bootstrap spec + gates + remotes via gh/glab) and `rail upgrade`

**Files:**
- Modify: `pyproject.toml` (add `copier>=9.18` to `dependencies`), `uv.lock` (regenerated)
- Create: `copier.yml` (repository root — copier reads it there, `_subdirectory` points at the files)
- Create: `template/project/…` (listed in Step 3)
- Create: `src/rail/scaffold.py`
- Create: `src/rail/remotes.py`
- Create: `src/rail/commands/new.py`
- Create: `src/rail/commands/upgrade.py`
- Create: `tests/test_scaffold.py`
- Create: `tests/test_remotes.py`

Verified on 2026-09-15 with copier 9.18.2: conditional path segments (`{% if stack == 'python' %}src{% endif %}/…`) skip the subtree when empty, `when:` questions take their default silently, `regex_search` is available in validators, `{% raw %}` protects `${{ }}`, tabs in `Makefile.jinja` survive, and `run_update` moves `_commit` from `v0.1.0` to `v0.2.0` on a git-tagged template. Signatures: `run_copy(src_path, dst_path, data, *, defaults, overwrite, quiet, unsafe, vcs_ref, …)`, `run_update(dst_path, data, *, defaults, overwrite, skip_answered, quiet, unsafe, vcs_ref, …)`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml` `dependencies`, add `"copier>=9.18",` after `"click>=8.1",`. Then:
```bash
uv sync --extra dev && uv run python -c "import copier; print(copier.__version__)"
```
Expected: `9.18.2` (or newer 9.x) and an updated `uv.lock`.

- [ ] **Step 2: Write the failing tests**

`tests/test_scaffold.py`:
```python
"""A fresh scaffold passes its own `rail check` at `bootstrap`; `rail upgrade` follows the tags."""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail import gitrepo
from rail.cli import main
from rail.ledger import RECEIPTS_DIR, RecordKind
from rail.ledger.file import FileLedger
from rail.model import Stack, Tier
from rail.scaffold import NewProject, ScaffoldError, new_project, render, upgrade

ROOT = Path(__file__).resolve().parents[1]
CLOCK = lambda: datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # noqa: E731
GIT = ["git", "-c", "user.name=rail", "-c", "user.email=rail@example.invalid"]


@pytest.fixture
def template_dir(tmp_path: Path) -> Path:
    """A plain-directory copy of the template: uncommitted edits are visible, no git needed."""
    src = tmp_path / "template-src"
    src.mkdir()
    shutil.copy(ROOT / "copier.yml", src / "copier.yml")
    shutil.copytree(ROOT / "template", src / "template")
    return src


def _project(template: Path, dest: Path, **overrides: object) -> NewProject:
    fields: dict[str, object] = {
        "slug": "red-probe",
        "description": "A disposable HTTP probe.",
        "tier": Tier.BOOTSTRAP,
        "stack": Stack.PYTHON,
        "brain_key": "red-probe",
        "dest": dest,
        "template": str(template),
    }
    fields.update(overrides)
    return NewProject(**fields)  # type: ignore[arg-type]


def test_render_python_bootstrap(template_dir: Path, tmp_path: Path) -> None:
    dest = render(_project(template_dir, tmp_path / "red-probe"))
    assert (dest / "rail.yaml").read_text().startswith("rail: 1\nproject: red-probe\n")
    assert "tier: bootstrap" in (dest / "rail.yaml").read_text()
    assert "`red-probe`" in (dest / "CLAUDE.md").read_text()
    assert (dest / "src" / "red_probe" / "__init__.py").is_file()
    assert (dest / "tests" / "test_smoke.py").is_file()
    assert (dest / ".copier-answers.yml").is_file()
    assert "rail-ci.yml@main" in (dest / ".github" / "workflows" / "continuous-integration.yml").read_text()
    assert not (dest / "go.mod").exists()
    for rel in ("docs/specs", "docs/plans", "docs/adr", "docs/receipts"):
        assert (dest / rel).is_dir(), rel
    recipes = [line for line in (dest / "Makefile").read_text().splitlines() if line.startswith(("\t", " "))]
    assert recipes and all(line.startswith("\t") for line in recipes)


def test_render_go_prod_and_docs(template_dir: Path, tmp_path: Path) -> None:
    go = render(_project(template_dir, tmp_path / "red-gopher", slug="red-gopher", tier=Tier.PROD, stack=Stack.GO))
    assert (go / "go.mod").is_file() and (go / "main_test.go").is_file()
    assert "target: vps-traefik" in (go / "rail.yaml").read_text()
    assert "healthcheck: https://gopher.hawkixs.com/healthz" in (go / "rail.yaml").read_text()
    assert not (go / "pyproject.toml").exists()
    docs = render(_project(template_dir, tmp_path / "red-notes", slug="red-notes", stack=Stack.DOCS))
    assert not (docs / "src").exists() and not (docs / "go.mod").exists()
    assert "ci: lint test check" in (docs / "Makefile").read_text()


def test_render_refuses_an_existing_destination(template_dir: Path, tmp_path: Path) -> None:
    (tmp_path / "red-probe").mkdir()
    with pytest.raises(ScaffoldError, match="already exists"):
        render(_project(template_dir, tmp_path / "red-probe"))


def test_new_project_passes_bootstrap_without_remotes(template_dir: Path, tmp_path: Path) -> None:
    project = _project(template_dir, tmp_path / "red-probe")
    results = new_project(project, publish=False, clock=CLOCK)
    assert all(r.passed for r in results), [r for r in results if not r.passed]
    dest = project.dest
    assert gitrepo.recent_subjects(dest, 1) == ["chore: bootstrap red-probe with the ReD rail"]
    assert len(FileLedger(dest / RECEIPTS_DIR).list("red-probe", kind=RecordKind.CONTRACT)) == 1
    spec = next((dest / "docs" / "specs").glob("*-red-probe-bootstrap-design.md"))
    assert "A disposable HTTP probe." in spec.read_text()
    out = CliRunner().invoke(main, ["check", "--repo", str(dest), "--ci"])
    assert out.exit_code == 0, out.output


def test_new_project_reports_failing_gates(template_dir: Path, tmp_path: Path) -> None:
    (template_dir / "template" / "project" / "Makefile.jinja").unlink()
    with pytest.raises(ScaffoldError, match="hygiene.task_runner"):
        new_project(_project(template_dir, tmp_path / "red-probe"), publish=False, clock=CLOCK)


def test_upgrade_requires_a_versioned_template(template_dir: Path, tmp_path: Path) -> None:
    project = _project(template_dir, tmp_path / "red-probe")
    new_project(project, publish=False, clock=CLOCK)
    with pytest.raises(ScaffoldError, match="_commit"):
        upgrade(project.dest)
    (tmp_path / "plain").mkdir()
    with pytest.raises(ScaffoldError, match="copier-answers"):
        upgrade(tmp_path / "plain")


def test_upgrade_follows_the_template_tags(template_dir: Path, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(template_dir)], check=True)
    subprocess.run([*GIT, "-C", str(template_dir), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(template_dir), "commit", "-q", "-m", "chore: v0.1.0"], check=True)
    subprocess.run(["git", "-C", str(template_dir), "tag", "v0.1.0"], check=True)
    project = _project(template_dir, tmp_path / "red-probe", template_ref="v0.1.0")
    new_project(project, publish=False, clock=CLOCK)
    assert "_commit: v0.1.0" in (project.dest / ".copier-answers.yml").read_text()
    readme = template_dir / "template" / "project" / "README.md.jinja"
    readme.write_text(readme.read_text() + "\nUpgraded line.\n")
    subprocess.run([*GIT, "-C", str(template_dir), "commit", "-q", "-am", "feat: v0.2.0"], check=True)
    subprocess.run(["git", "-C", str(template_dir), "tag", "v0.2.0"], check=True)
    upgrade(project.dest)
    assert "_commit: v0.2.0" in (project.dest / ".copier-answers.yml").read_text()
    assert "Upgraded line." in (project.dest / "README.md").read_text()


def test_cli_new_and_upgrade(template_dir: Path, tmp_path: Path) -> None:
    out = CliRunner().invoke(
        main,
        [
            "new", "red-probe",
            "--description", "A disposable HTTP probe.",
            "--dest", str(tmp_path / "red-probe"),
            "--template", str(template_dir),
            "--no-remotes",
        ],
    )
    assert out.exit_code == 0, out.output
    assert "| red-probe |" in out.output and "PASS  hygiene.rail_config" in out.output
    out = CliRunner().invoke(main, ["new", "Bad_Name", "--description", "x", "--no-remotes"])
    assert out.exit_code == 2 and "red-<kebab-case>" in out.output
    out = CliRunner().invoke(main, ["upgrade", "--repo", str(tmp_path / "red-probe")])
    assert out.exit_code == 1 and "_commit" in out.output
```

`tests/test_remotes.py`:
```python
"""Publishing uses the host's gh and glab; here they are fakes that create local bare repos."""

import subprocess
from pathlib import Path

import pytest

from rail import remotes
from rail.remotes import RemoteError, publish
from tests.helpers import commit_all, init_repo


class FakeHosts:
    """`gh`/`glab` calls are simulated; every other command runs for real."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[list[str]] = []

    def _bare(self, tool: str, path: str) -> Path:
        return self.root / tool / f"{path.rsplit('/', 1)[-1]}.git"

    def __call__(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[0] in ("gh", "glab"):
            self.calls.append(args)
            bare = self._bare(args[0], args[3])
            if args[2] == "view":
                return subprocess.CompletedProcess(args, 0 if bare.exists() else 1, "", "not found")
            if args[2] == "create":
                subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
                return subprocess.CompletedProcess(args, 0, str(bare), "")
            raise AssertionError(args)
        return subprocess.run(args, **kwargs)  # type: ignore[call-overload]


@pytest.fixture
def hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeHosts:
    fake = FakeHosts(tmp_path / "hosts")
    monkeypatch.setattr(remotes, "CANONICAL_URL", str(tmp_path / "hosts" / "gh" / "{slug}.git"))
    monkeypatch.setattr(remotes, "MIRROR_URL", str(tmp_path / "hosts" / "glab" / "{slug}.git"))
    return fake


def _local(tmp_path: Path) -> Path:
    repo = init_repo(tmp_path / "red-probe", remotes=False)
    (repo / "README.md").write_text("# red-probe\n")
    commit_all(repo, "chore: bootstrap red-probe with the ReD rail")
    return repo


def test_publish_creates_both_repositories_and_pushes_main(tmp_path: Path, hosts: FakeHosts) -> None:
    repo = _local(tmp_path)
    publish(repo, "red-probe", "A probe.", run=hosts)
    assert [c[:3] for c in hosts.calls] == [
        ["gh", "repo", "view"],
        ["glab", "repo", "view"],
        ["gh", "repo", "create"],
        ["glab", "repo", "create"],
    ]
    assert hosts.calls[2][3:] == ["hawkixs/red-probe", "--private", "--description", "A probe."]
    assert hosts.calls[3][3:5] == ["hawkixs_project/red/red-probe", "--private"]
    assert "--skipGitInit" in hosts.calls[3] and "--defaultBranch" in hosts.calls[3]
    for tool in ("gh", "glab"):
        head = subprocess.run(
            ["git", "-C", str(tmp_path / "hosts" / tool / "red-probe.git"), "rev-parse", "main"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert head == subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    assert subprocess.run(["git", "-C", str(repo), "config", "remote.pushDefault"], capture_output=True, text=True).stdout.strip() == "origin"


def test_publish_refuses_a_taken_slug(tmp_path: Path, hosts: FakeHosts) -> None:
    repo = _local(tmp_path)
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "hosts" / "gh" / "red-probe.git")], check=True)
    with pytest.raises(RemoteError, match="GitHub already has red-probe"):
        publish(repo, "red-probe", "A probe.", run=hosts)
    assert len(hosts.calls) == 1  # stopped at the first collision, nothing created


def test_second_push_failure_never_rewrites_the_first(tmp_path: Path, hosts: FakeHosts, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _local(tmp_path)
    monkeypatch.setattr(remotes, "MIRROR_URL", str(tmp_path / "nowhere" / "{slug}.git"))
    with pytest.raises(RemoteError, match="do not rewrite"):
        publish(repo, "red-probe", "A probe.", run=hosts)
    assert (tmp_path / "hosts" / "gh" / "red-probe.git").is_dir()
```

- [ ] **Step 3: Create `copier.yml` at the repository root and the template files**

`copier.yml`:
```yaml
# copier template for a ReD sub-project. `rail new` drives it; `rail upgrade` is `copier update`.
# The template files live under template/project; this file must sit at the repository root.
_min_copier_version: "9.0.0"
_subdirectory: template/project
_answers_file: .copier-answers.yml
_templates_suffix: .jinja

project:
  type: str
  help: Project slug (red-<name>, kebab-case)
  validator: >-
    {% if not (project | regex_search('^red-[a-z0-9]+(-[a-z0-9]+)*$')) %}
    project must match red-<kebab-case>
    {% endif %}
description:
  type: str
  help: One sentence — what the project does
brain_key:
  type: str
  default: "{{ project }}"
  help: brain-v42 project key (group red)
tier:
  type: str
  choices: [bootstrap, dev, prod]
  default: bootstrap
  help: Maturity tier the project is scored against
stack:
  type: str
  choices: [python, go, docs]
  default: python
deploy_target:
  type: str
  choices: [vps-traefik, pc-server-systemd]
  default: vps-traefik
  when: "{{ tier == 'prod' }}"
healthcheck:
  type: str
  default: "https://{{ project[4:] }}.hawkixs.com/healthz"
  when: "{{ tier == 'prod' }}"
```

`template/project/{{ _copier_conf.answers_file }}.jinja` (this exact file name, braces included):
```
# Changes here will be overwritten by Copier; NEVER EDIT MANUALLY
{{ _copier_answers|to_nice_yaml -}}
```

`template/project/rail.yaml.jinja`:
```yaml
rail: 1
project: {{ project }}
brain_key: {{ brain_key }}
tier: {{ tier }}
stack: {{ stack }}
ledger: file
{%- if tier == 'prod' %}
deploy:
  target: {{ deploy_target }}
  healthcheck: {{ healthcheck }}
{%- endif %}
gates: {}
```

`template/project/README.md.jinja`:
```markdown
# {{ project }}

{{ description }}

Delivered on the ReD rail (tier `{{ tier }}`): `make ci` is what CI runs, `rail check` is the verdict,
`docs/receipts/` is the evidence ledger. Private repository — canonical remote GitHub
`hawkixs/{{ project }}`, mirror GitLab `hawkixs_project/red/{{ project }}`.
```

`template/project/CLAUDE.md.jinja`:
````markdown
# {{ project }} — ReD sub-project

## Project

{{ description }}

- **Repo**: `~/hawkixs_infra/git_repo/ReD_v1/projects/{{ project }}/`
- **GitHub (`origin`)**: `git@github.com:hawkixs/{{ project }}.git` (private, canonical)
- **GitLab (`gitlab`)**: `ssh://git@gitlab.hawkixs.local:2222/hawkixs_project/red/{{ project }}.git` (private mirror)
- **Brain MCP project key**: `{{ brain_key }}` (group `red`)
- **Parent project**: ReD v1 (`~/hawkixs_infra/git_repo/ReD_v1/CLAUDE.md` — roster, cross-project rules)
- **Rail**: tier `{{ tier }}`, stack `{{ stack }}`, ledger `file` — see `rail.yaml`; `rail check` is the verdict

Published branches are pushed to both remotes and compared SHA by SHA. GitHub and GitLab share
no atomic transaction: a push to one remote is a deliberate divergence until the second is synced.

## Language

Everything pushed to a remote is written in **English**: commits, branches, PRs, docs, code
comments, test names. The conversation with the operator stays in French.

## Architecture

Describe the modules as they appear. Durable decisions go to `docs/adr/`.

## Stack

{% if stack == 'python' -%}
Python 3.12+, uv, pytest, ruff.
{%- elif stack == 'go' -%}
Go 1.22+, `go test`, `go vet`, `gofmt`.
{%- else -%}
Documentation only (Markdown).
{%- endif %}

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
{%- if stack == 'python' %}
├── src/{{ project | replace('-', '_') }}/
└── tests/
{%- elif stack == 'go' %}
├── go.mod
└── *_test.go
{%- else %}
└── README.md
{%- endif %}
```

## Working principles

### Workflow
- Brainstorm → spec → plan → implement: `rail-design` and `rail-plan` skills for the rail stages,
  `sdd-brainstorm` / `writing-plans-parallel` / `executing-plans-parallel` for the work itself.
  Never the built-in plan mode.
- If it derails mid-way, **stop and re-plan immediately**.
- TDD: write the failing test first, watch it fail, implement the minimum.

### Quality pipeline before every commit
```
implementation done
  → /tdd-write-tests                    (missing tests)
  → /reflexion-reflect                  (non-trivial change only)
  → /code-review-review-local-changes   (multi-agent review)
  → /git-commit                         (conventional commit, English)
```
Skip the review for docs-only commits.

### Verification before "done"
- Never declare a task done without proof: run the tests, read the summary line, capture the
  exit code. `rail check` must pass on this repository.

### Self-improvement (brain MCP)
- After any correction from the operator: `brain_learn(topic, insight, project_key="{{ brain_key }}")`.
- Before solving a problem: `brain_search(query, project_key="{{ brain_key }}")`.

### Core principles
- **Build to last**: solid, tested, documented.
- **No shortcuts**: no quick fixes, no unnecessary dependencies.
- **Scalable**: think about twenty repositories even while proving one.
- **From scratch**: prefer building the small thing over adopting the big platform.

## Brain MCP — proactive use

Project key: `{{ brain_key }}`. Use it without being asked.

- **Session start**: `brain_session_start("{{ brain_key }}")`
- **During work**: `brain_log_decision` for choices, `brain_save_snippet` for reusable code,
  `brain_create_runbook` for procedures, `brain_learn` only for pure insights.
- **Session end**: `brain_update_project_focus("{{ brain_key }}", current_focus="summary + next steps")`

## Related projects

| Project | Path | Relation |
|---|---|---|
| ReD (root) | `~/hawkixs_infra/git_repo/ReD_v1/` | parent, roster, cross-project rules |
| red-rail | `projects/red-rail` | the delivery rail: `rail check`, `rail audit`, `rail upgrade` |
````

`template/project/Makefile.jinja` — **recipe lines start with a real TAB** (verify after writing with `grep -c $'^\t' template/project/Makefile.jinja`, expected ≥ 7):
```make
# {{ project }} task runner. Every target is what CI runs, nothing more.

.PHONY: sync lint test check ci

RAIL_FLAGS ?=

{% if stack == 'python' -%}
## Install the project and its dev extras
sync:
	uv sync --extra dev

## Lint and format check
lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

## Run the test suite
test:
	uv run pytest -q
{% elif stack == 'go' -%}
## Download the modules
sync:
	go mod download

## Vet and format check
lint:
	go vet ./...
	test -z "$$(gofmt -l .)"

## Run the test suite
test:
	go test ./...
{% else -%}
## Nothing to install for a documentation project
sync:
	@true

## Nothing to lint yet
lint:
	@true

## Nothing to test yet
test:
	@true
{% endif %}
## Run the rail gates against this repository (RAIL_FLAGS=--ci in CI)
check:
	rail check $(RAIL_FLAGS)

## What CI runs, in order
ci: lint test check
```

`template/project/.gitignore.jinja`:
```
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
{% endif -%}
.env
```

`template/project/.claude/settings.json.jinja`:
```json
{
  "permissions": {
    "allow": [
      "Bash(make:*)",
      "Bash(git:*)",
      "Bash(rail:*)"{% if stack == 'python' %},
      "Bash(uv sync:*)",
      "Bash(uv run:*)"{% elif stack == 'go' %},
      "Bash(go:*)"{% endif %}
    ]
  }
}
```

`template/project/.github/workflows/continuous-integration.yml.jinja`:
```yaml
name: continuous-integration

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: {% raw %}${{ github.workflow }}-${{ github.ref }}{% endraw %}
  cancel-in-progress: true

jobs:
  rail:
    # The reusable rail workflow: the project's `make ci`, then `rail check --ci --json`.
    uses: hawkixs/red-rail/.github/workflows/rail-ci.yml@main
    with:
      stack: {{ stack }}
    secrets: inherit
```

Empty keep-files (plain, no suffix): `template/project/docs/specs/.gitkeep`, `template/project/docs/plans/.gitkeep`, `template/project/docs/adr/.gitkeep`, `template/project/docs/receipts/.gitkeep`.

Python-only files:

`template/project/{% if stack == 'python' %}pyproject.toml{% endif %}.jinja`:
```toml
[build-system]
requires = ["setuptools>=75.0"]
build-backend = "setuptools.build_meta"

[project]
name = "{{ project }}"
version = "0.1.0"
description = "{{ description }}"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.8"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`template/project/{% if stack == 'python' %}.python-version{% endif %}.jinja`: one line, `3.12`.

`template/project/{% if stack == 'python' %}src{% endif %}/{{ project | replace('-', '_') }}/__init__.py.jinja`:
```python
"""{{ description }}"""

__version__ = "0.1.0"
```

`template/project/{% if stack == 'python' %}tests{% endif %}/test_smoke.py.jinja`:
```python
"""The package imports and carries a version."""

from {{ project | replace('-', '_') }} import __version__


def test_version() -> None:
    assert __version__
```

Go-only files:

`template/project/{% if stack == 'go' %}go.mod{% endif %}.jinja`:
```
module github.com/hawkixs/{{ project }}

go 1.22
```

`template/project/{% if stack == 'go' %}main.go{% endif %}.jinja`:
```go
// Command {{ project }}: {{ description }}
package main

import "fmt"

func main() {
	fmt.Println("{{ project }}")
}
```

`template/project/{% if stack == 'go' %}main_test.go{% endif %}.jinja`:
```go
package main

import "testing"

func TestSmoke(t *testing.T) {
	t.Log("{{ project }}")
}
```

- [ ] **Step 4: Create `src/rail/remotes.py`**

```python
"""Publishing a new repository on both remotes with the host's `gh` and `glab` — the tools the
operator already uses and authenticates; red-rail never handles a token (spec §7)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

CANONICAL_OWNER = "hawkixs"
MIRROR_GROUP = "hawkixs_project/red"
CANONICAL_URL = "git@github.com:hawkixs/{slug}.git"
MIRROR_URL = "ssh://git@gitlab.hawkixs.local:2222/hawkixs_project/red/{slug}.git"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class RemoteError(Exception):
    """A remote step failed; the message says what was done and what not to do next."""


def _run(args: list[str], *, run: Runner, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "check": False}
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    return run(args, **kwargs)


def _ok(args: list[str], *, run: Runner, what: str, cwd: Path | None = None) -> str:
    done = _run(args, run=run, cwd=cwd)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        raise RemoteError(f"{what} failed (exit {done.returncode}): {detail}")
    return done.stdout


def ensure_absent(slug: str, *, run: Runner) -> None:
    for args, where in (
        (["gh", "repo", "view", f"{CANONICAL_OWNER}/{slug}"], "GitHub"),
        (["glab", "repo", "view", f"{MIRROR_GROUP}/{slug}"], "GitLab"),
    ):
        if _run(args, run=run).returncode == 0:
            raise RemoteError(f"{where} already has {slug}; pick another slug")


def create_github(slug: str, description: str, *, run: Runner) -> None:
    _ok(
        ["gh", "repo", "create", f"{CANONICAL_OWNER}/{slug}", "--private", "--description", description],
        run=run,
        what="gh repo create",
    )


def create_gitlab(slug: str, description: str, *, run: Runner) -> None:
    _ok(
        [
            "glab", "repo", "create", f"{MIRROR_GROUP}/{slug}",
            "--private", "--defaultBranch", "main", "--skipGitInit", "--description", description,
        ],
        run=run,
        what="glab repo create",
    )


def configure(repo: Path, slug: str, *, run: Runner) -> None:
    _ok(["git", "remote", "add", "origin", CANONICAL_URL.format(slug=slug)], run=run, cwd=repo, what="git remote add origin")
    _ok(["git", "remote", "add", "gitlab", MIRROR_URL.format(slug=slug)], run=run, cwd=repo, what="git remote add gitlab")
    _ok(["git", "config", "remote.pushDefault", "origin"], run=run, cwd=repo, what="git config")


def push_both(repo: Path, *, run: Runner) -> None:
    _ok(["git", "push", "-u", "origin", "main"], run=run, cwd=repo, what="git push origin main")
    done = _run(["git", "push", "gitlab", "main"], run=run, cwd=repo)
    if done.returncode != 0:
        raise RemoteError(
            "git push gitlab main failed after GitHub succeeded — do not rewrite GitHub; fix the mirror "
            f"and run `git push gitlab main`: {(done.stderr or done.stdout).strip()}"
        )


def parity(repo: Path, *, run: Runner) -> bool:
    heads = []
    for remote in ("origin", "gitlab"):
        out = _ok(["git", "ls-remote", remote, "refs/heads/main"], run=run, cwd=repo, what=f"git ls-remote {remote}")
        heads.append(out.split()[0] if out.split() else "")
    return heads[0] != "" and heads[0] == heads[1]


def publish(repo: Path, slug: str, description: str, *, run: Runner = subprocess.run) -> None:
    """Kickstart runbook `a050e6ec`, steps 2 and 8–12, as one call."""
    ensure_absent(slug, run=run)
    create_github(slug, description, run=run)
    create_gitlab(slug, description, run=run)
    configure(repo, slug, run=run)
    push_both(repo, run=run)
    if not parity(repo, run=run):
        raise RemoteError("main differs between GitHub and GitLab after the push; compare `git ls-remote`")
```

- [ ] **Step 5: Create `src/rail/scaffold.py`**

```python
"""`rail new` and `rail upgrade`: the copier template (this repository, `copier.yml` at its
root) drives both. A new project is rendered, given its bootstrap contract and spec, committed,
checked at its tier, then published — the 15-step kickstart runbook `a050e6ec` as one command."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import copier
import yaml

from rail import remotes
from rail.gates import GateResult, run_gates
from rail.ledger import RECEIPTS_DIR, Contract, Deliverable, Record
from rail.ledger.file import FileLedger
from rail.model import Stack, Tier
from rail.policy import stages_for

TEMPLATE_SOURCE = "git@github.com:hawkixs/red-rail.git"
ANSWERS_FILE = ".copier-answers.yml"


class ScaffoldError(Exception):
    """The scaffold could not be completed; the tree is left in place for inspection."""


@dataclass(frozen=True, slots=True)
class NewProject:
    slug: str
    description: str
    tier: Tier
    stack: Stack
    brain_key: str
    dest: Path
    template: str = TEMPLATE_SOURCE
    template_ref: str | None = None
    deploy_target: str = "vps-traefik"
    healthcheck: str | None = None

    @property
    def answers(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "project": self.slug,
            "description": self.description,
            "brain_key": self.brain_key,
            "tier": self.tier.value,
            "stack": self.stack.value,
        }
        if self.tier is Tier.PROD:
            data["deploy_target"] = self.deploy_target
            data["healthcheck"] = self.healthcheck or f"https://{self.slug[4:]}.hawkixs.com/healthz"
        return data


BOOTSTRAP_SPEC = """# {slug} — Bootstrap design

- **Date**: {today}
- **Status**: bootstrap, written by `rail new` — replace it with the real design before
  leaving tier `bootstrap`

## 1. Problem

{description}

## 2. Decisions

| # | Decision |
|---|---|
| 1 | Tier `{tier}`, stack `{stack}`, ledger `file` (`rail.yaml`) |
| 2 | Canonical remote GitHub `hawkixs/{slug}`, mirror GitLab `hawkixs_project/red/{slug}` |

## 3. Non-goals

Nothing beyond the bootstrap: no feature is designed here.

## 4. Success criteria

`rail check` passes at tier `{tier}` on a fresh clone.
"""


def render(project: NewProject, *, copy: Callable[..., Any] = copier.run_copy) -> Path:
    if project.dest.exists():
        raise ScaffoldError(f"{project.dest} already exists")
    copy(
        project.template,
        project.dest,
        data=project.answers,
        defaults=True,
        quiet=True,
        unsafe=False,
        vcs_ref=project.template_ref,
    )
    return project.dest


def write_bootstrap_spec(project: NewProject, *, today: date | None = None) -> Path:
    today = today or datetime.now(UTC).date()
    path = project.dest / "docs" / "specs" / f"{today.isoformat()}-{project.slug}-bootstrap-design.md"
    path.write_text(
        BOOTSTRAP_SPEC.format(
            slug=project.slug,
            today=today.isoformat(),
            description=project.description,
            tier=project.tier.value,
            stack=project.stack.value,
        )
    )
    return path


def record_contract(project: NewProject, *, clock: Callable[[], datetime] | None = None) -> Record:
    ledger = FileLedger(project.dest / RECEIPTS_DIR, clock=clock)
    contract = Contract(
        objective=project.description,
        acceptance_criteria=[f"`rail check` passes at tier {project.tier.value}"],
        deliverables=[Deliverable(key="main", repository=f"{remotes.CANONICAL_OWNER}/{project.slug}")],
    )
    return ledger.contract_set(
        project.slug,
        contract,
        reason="bootstrap",
        issuer="rail new",
        idempotency_key=f"contract:{project.slug}:1",
    )


def _git(dest: Path, *args: str) -> str:
    identity: list[str] = []
    probe = subprocess.run(["git", "-C", str(dest), "config", "--get", "user.email"], capture_output=True, text=True)
    if probe.returncode != 0:
        identity = ["-c", "user.name=rail", "-c", "user.email=rail@localhost"]
    done = subprocess.run(["git", *identity, "-C", str(dest), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise ScaffoldError(f"git {' '.join(args)} failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout.strip()


def init_git(project: NewProject) -> str:
    subprocess.run(["git", "init", "-q", "-b", "main", str(project.dest)], check=True)
    _git(project.dest, "add", "-A")
    _git(project.dest, "commit", "-q", "-m", f"chore: bootstrap {project.slug} with the ReD rail")
    return _git(project.dest, "rev-parse", "HEAD")


def verify(project: NewProject) -> list[GateResult]:
    """The gates of the declared tier, CI scope (no remotes yet, no roster from a fresh tree)."""
    return run_gates(project.dest, stages=stages_for(project.tier), ci=True)


def new_project(
    project: NewProject,
    *,
    publish: bool = True,
    copy: Callable[..., Any] = copier.run_copy,
    run: remotes.Runner = subprocess.run,
    clock: Callable[[], datetime] | None = None,
) -> list[GateResult]:
    render(project, copy=copy)
    write_bootstrap_spec(project)
    record_contract(project, clock=clock)
    init_git(project)
    results = verify(project)
    failing = [r for r in results if not r.passed]
    if failing:
        detail = "; ".join(f"{r.gate_id}: {r.details}" for r in failing)
        raise ScaffoldError(f"the fresh scaffold fails its own gates — {detail}")
    if publish:
        remotes.publish(project.dest, project.slug, project.description, run=run)
    return results


def upgrade(repo: Path, *, update: Callable[..., Any] = copier.run_update) -> str:
    """`copier update` towards the template's latest tag; returns the new `_commit`."""
    answers = repo / ANSWERS_FILE
    if not answers.is_file():
        raise ScaffoldError(f"{ANSWERS_FILE} missing: not scaffolded by copier")
    data = yaml.safe_load(answers.read_text()) or {}
    if not data.get("_commit") or not data.get("_src_path"):
        raise ScaffoldError(
            f"{ANSWERS_FILE} has no _src_path/_commit: the template was not versioned; "
            "re-scaffold from a tagged red-rail before upgrading"
        )
    update(repo, defaults=True, overwrite=True, skip_answered=True, quiet=True, unsafe=False)
    return str((yaml.safe_load(answers.read_text()) or {}).get("_commit", ""))
```

- [ ] **Step 6: Create `src/rail/commands/new.py` and `src/rail/commands/upgrade.py`**

`src/rail/commands/new.py`:
```python
"""`rail new SLUG`: the intent stage in one command (spec §6, step 1)."""

from __future__ import annotations

import re
from pathlib import Path

import click

from rail.model import DeployTarget, Stack, Tier
from rail.remotes import RemoteError
from rail.scaffold import TEMPLATE_SOURCE, NewProject, ScaffoldError, new_project

SLUG = re.compile(r"^red-[a-z0-9]+(-[a-z0-9]+)*$")


def _slug(ctx: click.Context, param: click.Parameter, value: str) -> str:
    if not SLUG.match(value):
        raise click.BadParameter("must match red-<kebab-case>")
    return value


@click.command("new")
@click.argument("slug", callback=_slug)
@click.option("--description", required=True, help="One sentence — what the project does.")
@click.option("--tier", type=click.Choice([t.value for t in Tier]), default="bootstrap", show_default=True)
@click.option("--stack", type=click.Choice([s.value for s in Stack]), default="python", show_default=True)
@click.option("--brain-key", default=None, help="brain-v42 project key (default: the slug).")
@click.option("--deploy-target", type=click.Choice([d.value for d in DeployTarget]), default="vps-traefik", show_default=True)
@click.option("--healthcheck", default=None, help="prod only (default: https://<name>.hawkixs.com/healthz).")
@click.option("--dest", type=click.Path(path_type=Path), default=None, help="Destination (default: ./SLUG).")
@click.option("--template", default=TEMPLATE_SOURCE, show_default=True, help="copier source: git URL or directory.")
@click.option("--template-ref", default=None, help="red-rail tag to pin (git sources only).")
@click.option("--remotes/--no-remotes", "publish", default=True, show_default=True, help="Create and push GitHub + GitLab with gh/glab.")
def command(
    slug: str,
    description: str,
    tier: str,
    stack: str,
    brain_key: str | None,
    deploy_target: str,
    healthcheck: str | None,
    dest: Path | None,
    template: str,
    template_ref: str | None,
    publish: bool,
) -> None:
    """Scaffold a ReD project: tree, contract, bootstrap spec, first commit, gates, remotes."""
    project = NewProject(
        slug=slug,
        description=description,
        tier=Tier(tier),
        stack=Stack(stack),
        brain_key=brain_key or slug,
        dest=(dest or Path.cwd() / slug),
        template=template,
        template_ref=template_ref,
        deploy_target=deploy_target,
        healthcheck=healthcheck,
    )
    try:
        results = new_project(project, publish=publish)
    except ScaffoldError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    except RemoteError as exc:
        click.echo(f"error: {exc}\nthe local tree is intact under {project.dest}", err=True)
        raise SystemExit(1) from exc
    click.echo(f"created {project.dest}")
    for r in results:
        click.echo(f"PASS  {r.gate_id:<22} {r.details}")
    click.echo("")
    click.echo("Add this row to the ReD root roster (CLAUDE.md, operator's gesture — the root is not under git):")
    click.echo(f"| {slug} | <domain> | bootstrap (tier {tier}) | n/a | `{project.brain_key}` |")
```

`src/rail/commands/upgrade.py`:
```python
"""`rail upgrade`: re-apply the template's latest tag to a scaffolded project (`copier update`)."""

from __future__ import annotations

from pathlib import Path

import click

from rail.commands._options import repo_option
from rail.scaffold import ScaffoldError, upgrade


@click.command("upgrade")
@repo_option
def command(repo: Path) -> None:
    """Resorb template drift: bring the repository to the template's latest version."""
    try:
        version = upgrade(repo)
    except ScaffoldError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    click.echo(f"{repo}: template {version}; review the diff, then commit it")
```

- [ ] **Step 7: Run the tests, expect PASS**

```bash
grep -c $'^\t' template/project/Makefile.jinja   # expected: 7 or more (real tabs in recipes)
uv run pytest tests/test_scaffold.py tests/test_remotes.py -q
```
Expected: `11 passed`. If `test_render_python_bootstrap` fails on the Makefile assertion, the recipes were written with spaces: rewrite them with tabs.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/ && uv run pytest -q
git add pyproject.toml uv.lock copier.yml template src/rail/scaffold.py src/rail/remotes.py src/rail/commands/new.py src/rail/commands/upgrade.py tests/test_scaffold.py tests/test_remotes.py
git commit -m "feat(scaffold): copier template, rail new (contract, spec, gates, gh/glab remotes) and rail upgrade"
```

### Task 4.4: Reusable `rail-ci.yml` and gitleaks in red-rail's own CI

**Files:**
- Create: `.github/workflows/rail-ci.yml`
- Modify: `.github/workflows/continuous-integration.yml` (gitleaks install step, `rail check --ci --json`)
- Create: `tests/test_workflows.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_workflows.py`:
```python
"""The workflows are data: callable, pinned, and they run the same gates as the workstation."""

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def _load(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    data["on"] = data.pop(True, data.get("on"))  # PyYAML reads the bare key `on` as boolean True
    return data


def _steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def test_rail_ci_is_a_reusable_workflow_with_a_stack_input() -> None:
    wf = _load("rail-ci.yml")
    call = wf["on"]["workflow_call"]
    assert call["inputs"]["stack"]["required"] is True
    assert call["inputs"]["rail-ref"]["default"] == "main"
    assert "RAIL_READ_TOKEN" in call["secrets"]
    runs = [s["run"] for s in _steps(wf) if "run" in s]
    assert any("make ci" in r for r in runs)
    assert any("rail check --ci --json" in r for r in runs)


def test_every_action_is_pinned_to_a_commit_sha() -> None:
    for name in ("rail-ci.yml", "continuous-integration.yml"):
        for step in _steps(_load(name)):
            if "uses" in step:
                assert PINNED.match(step["uses"]), f"{name}: {step['uses']} is not pinned to a SHA"


def test_gitleaks_is_installed_with_a_checksum_in_both_workflows() -> None:
    for name in ("rail-ci.yml", "continuous-integration.yml"):
        installs = [s for s in _steps(_load(name)) if "gitleaks" in s.get("run", "")]
        assert installs, f"{name}: no gitleaks install step"
        assert "sha256sum -c" in installs[0]["run"]
        assert installs[0]["env"]["GITLEAKS_VERSION"] == "8.30.1"


def test_red_rail_ci_runs_rail_check_in_ci_scope() -> None:
    runs = [s["run"] for s in _steps(_load("continuous-integration.yml")) if "run" in s]
    assert any("rail check --ci --json" in r for r in runs)
```

- [ ] **Step 2: Run the tests, expect FAIL**

```bash
uv run pytest tests/test_workflows.py -q
```
Expected: `FileNotFoundError: … rail-ci.yml` and the `--ci` assertion failing.

- [ ] **Step 3: Create `.github/workflows/rail-ci.yml`**

The gitleaks checksum below is the official one for `gitleaks_8.30.1_linux_x64.tar.gz` (from `https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_checksums.txt`, read on 2026-09-15).

```yaml
name: rail-ci

# Reusable workflow: a project's `make ci`, then the rail gates in CI scope. Called by the
# template's continuous-integration.yml. CI never holds ledger credentials (spec §5): the
# gates are exposed as this check, attestations happen on the host.
on:
  workflow_call:
    inputs:
      stack:
        description: "python | go | docs — must match rail.yaml"
        type: string
        required: true
      rail-ref:
        description: "Tag or branch of hawkixs/red-rail to install"
        type: string
        default: main
      runs-on:
        description: "Runner label as a JSON string, e.g. '[\"self-hosted\",\"Linux\",\"X64\",\"red-ci\"]'"
        type: string
        default: '"ubuntu-latest"'
    secrets:
      RAIL_READ_TOKEN:
        description: "Read token for hawkixs/red-rail while it is private (not needed once public)"
        required: false

permissions:
  contents: read

jobs:
  rail:
    name: make ci + rail check
    runs-on: ${{ fromJSON(inputs.runs-on) }}
    timeout-minutes: 20
    steps:
      - name: Check out the project (full history for the build gate and gitleaks)
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          persist-credentials: false
          fetch-depth: 0

      - name: Check out red-rail
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          repository: hawkixs/red-rail
          ref: ${{ inputs.rail-ref }}
          path: .rail
          token: ${{ secrets.RAIL_READ_TOKEN || github.token }}
          persist-credentials: false

      - name: Install uv
        uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2
        with:
          version: "0.11.29"
          checksum: "04f8b82f5d47f0512dcd32c67a4a6f16a0ea27c81537c338fd0ad6b23cebe829"

      - name: Install gitleaks (pinned, checksum verified)
        env:
          GITLEAKS_VERSION: "8.30.1"
          GITLEAKS_SHA256: "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
        run: |
          set -euo pipefail
          curl -sSfL -o /tmp/gitleaks.tar.gz "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
          echo "${GITLEAKS_SHA256}  /tmp/gitleaks.tar.gz" | sha256sum -c -
          tar -xzf /tmp/gitleaks.tar.gz -C /tmp gitleaks
          mkdir -p "$HOME/.local/bin"
          install -m 0755 /tmp/gitleaks "$HOME/.local/bin/gitleaks"
          echo "$HOME/.local/bin" >> "$GITHUB_PATH"

      - name: Install rail
        run: uv tool install ./.rail

      - name: Project CI (make ci, rail gates in CI scope)
        env:
          RAIL_FLAGS: --ci
        run: make ci

      - name: Rail check (machine-readable)
        run: rail check --ci --json
```

- [ ] **Step 4: Update `.github/workflows/continuous-integration.yml`**

The job runs in `python:3.12-slim`, which has neither `git`, `curl` nor `gitleaks`. The test
suite now drives git (fixture repositories, `rail.gitrepo`) and `actions/checkout` only keeps
the history when git is present, so git is installed **before** the checkout. Insert as the
first step of the job:
```yaml
      - name: Install git and curl (slim image)
        run: |
          set -euo pipefail
          apt-get update -qq
          apt-get install -y -qq --no-install-recommends git curl ca-certificates >/dev/null
```
Change the checkout step to fetch the full history (the build gate reads commit subjects,
gitleaks scans the history, the integrate gate walks ancestry):
```yaml
        with:
          persist-credentials: false
          fetch-depth: 0
```
Insert after the "Install project dependencies" step:
```yaml
      - name: Install gitleaks (pinned, checksum verified)
        env:
          GITLEAKS_VERSION: "8.30.1"
          GITLEAKS_SHA256: "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
        run: |
          set -euo pipefail
          curl -sSfL -o /tmp/gitleaks.tar.gz "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
          echo "${GITLEAKS_SHA256}  /tmp/gitleaks.tar.gz" | sha256sum -c -
          tar -xzf /tmp/gitleaks.tar.gz -C /tmp gitleaks
          mkdir -p "$HOME/.local/bin"
          install -m 0755 /tmp/gitleaks "$HOME/.local/bin/gitleaks"
          echo "$HOME/.local/bin" >> "$GITHUB_PATH"
```
and change the last step to CI scope:
```yaml
      - name: Rail check (dogfooding, CI scope)
        run: uv run rail check --ci --json
```

- [ ] **Step 5: Run the tests, expect PASS**

```bash
uv run pytest tests/test_workflows.py -q
```
Expected: `4 passed`.

- [ ] **Step 6: Commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/
git add .github/workflows/rail-ci.yml .github/workflows/continuous-integration.yml tests/test_workflows.py
git commit -m "ci: reusable rail-ci workflow; gitleaks pinned by checksum; rail check in CI scope"
```

### Task 4.5: Facade skills — `rail-design`, `rail-plan`, `rail-check`, `rail-attest`, `rail-audit`

**Files:**
- Create: `skills/rail-design/SKILL.md`, `skills/rail-plan/SKILL.md`, `skills/rail-check/SKILL.md`, `skills/rail-attest/SKILL.md`, `skills/rail-audit/SKILL.md`
- Create: `tests/test_skills.py`

- [ ] **Step 1: Write the failing test**

`tests/test_skills.py`:
```python
"""Skills are facades: they guide, then call the CLI. They carry no rule of their own."""

import re
from pathlib import Path

from rail.policy import REQUIRED_SPEC_SECTIONS

SKILLS = Path(__file__).resolve().parents[1] / "skills"
EXPECTED = {"rail-design", "rail-plan", "rail-check", "rail-attest", "rail-audit"}
FRONTMATTER = re.compile(r"^---\nname: (?P<name>[\w-]+)\ndescription: (?P<description>.+?)\n---\n", re.DOTALL)


def _skills() -> dict[str, str]:
    return {d.name: (d / "SKILL.md").read_text() for d in SKILLS.iterdir() if d.is_dir()}


def test_the_five_phase_1_skills_exist() -> None:
    assert set(_skills()) == EXPECTED


def test_frontmatter_name_matches_the_directory() -> None:
    for name, text in _skills().items():
        match = FRONTMATTER.match(text)
        assert match, f"{name}: missing frontmatter"
        assert match["name"] == name
        assert len(match["description"]) > 40


def test_every_skill_calls_the_cli_and_says_the_cli_is_right() -> None:
    for name, text in _skills().items():
        assert re.search(r"`rail (check|attest|audit|contract)\b", text), f"{name}: no CLI call"
        assert "the CLI is right" in text, f"{name}: must defer to the CLI"


def test_skills_do_not_restate_the_design_rule() -> None:
    body = _skills()["rail-design"].lower()
    for alias in ("non-goal", "success criteri"):
        assert alias not in body, f"rail-design lists the mandatory sections ({alias}); the gate owns that rule"
    assert all(alias not in body for alias in REQUIRED_SPEC_SECTIONS["non-goals"])
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
uv run pytest tests/test_skills.py -q
```
Expected: `FileNotFoundError` (no `skills/` directory).

- [ ] **Step 3: Write the five skills**

`skills/rail-design/SKILL.md`:
```markdown
---
name: rail-design
description: Guide the design conversation of a ReD project (stage 2 of the rail), write the dated spec, then validate it with `rail check design`. Use when a project enters design or when `rail check design` fails.
---

# rail-design

You guide a design conversation; the rail decides whether the result is a valid spec.

1. Read `rail.yaml` and the latest `docs/specs/*.md` if any. Ask what problem the project
   solves and what would make it a success, one question at a time; propose alternatives
   before settling; write the choices down as they are made.
2. Write `docs/specs/<yyyy-mm-dd>-<topic>.md` in English. Durable choices also get an ADR in
   `docs/adr/`.
3. Run `rail check design --repo <path>`. Fix every item it reports and run it again until it
   passes. When this skill and the CLI disagree, the CLI is right — do not paraphrase its
   rules here.
4. Report the final `rail check design` output to the operator.
```

`skills/rail-plan/SKILL.md`:
```markdown
---
name: rail-plan
description: Turn a validated spec into a dated implementation plan (stage 3 of the rail) and validate it with `rail check plan`. Use after `rail check design` passes, before any code.
---

# rail-plan

You write the plan; the rail decides whether it is a valid plan.

1. Confirm `rail check design --repo <path>` passes; the plan must reference that spec by its
   `docs/specs/…` path.
2. Write `docs/plans/<yyyy-mm-dd>-<topic>.md` with the `writing-plans-parallel` skill (or
   `sdd-plan`): one `### Task` per unit of work, each with the command that verifies it and
   what to expect.
3. Run `rail check plan --repo <path>`; fix what it reports until it passes. When this skill
   and the CLI disagree, the CLI is right.
4. Hand the plan to `executing-plans-parallel`.
```

`skills/rail-check/SKILL.md`:
```markdown
---
name: rail-check
description: Run the rail gates of a ReD project against its declared tier and explain the result. Use before a commit, before opening a PR, or when CI's rail check is red.
---

# rail-check

1. Run `rail check --repo <path>` (add `--json` when you need the machine-readable report,
   `STAGE` to focus on one stage, `--all` to look beyond the declared tier).
2. Read each `FAIL` line: the details say what is missing. `EXC` lines are declared
   exceptions from `rail.yaml` — visible on purpose, never to be added silently; a new one
   needs a reason and the operator's agreement.
3. Fix the repository, not the gate. When this skill and the CLI disagree, the CLI is right;
   a wrong gate is fixed in red-rail, never bypassed in a project.
```

`skills/rail-attest/SKILL.md`:
```markdown
---
name: rail-attest
description: Record delivery evidence (integrated, released, deployed, rolled_back, restored, incident_detected, fulfilled) in the project's ledger with `rail attest`, or replay a receipt after a failed attestation. Use from the host, never from CI.
---

# rail-attest

Evidence is written where the operator stands, on the host — CI never holds ledger credentials.

1. Choose the kind and gather the facts it needs (`sha`, `version`, `digest`, `drill`).
2. Run `rail attest <kind> --repo <path> --data key=value …`. The receipt lands in
   `docs/receipts/`; commit it with the change it evidences.
3. If an attestation failed after the fact it describes happened, replay it from its receipt:
   `rail attest <kind> --from docs/receipts/<file>.json` — safe, the key makes it idempotent.
4. Read back with `rail ledger list --repo <path>`. When this skill and the CLI disagree, the
   CLI is right.
```

`skills/rail-audit/SKILL.md`:
```markdown
---
name: rail-audit
description: Produce and read the repository × stage matrix of the ReD projects with `rail audit`, and turn a drift into concrete next steps per project. Use for a periodic drift review or before choosing what to standardise next.
---

# rail-audit

1. Run `rail audit <projects-root>` (or a list of project paths); `--json` for the full
   report, `--matrix` for the diffable shape.
2. Read the table: a project is scored against its own tier; `!` marks a declared exception,
   `·` a stage outside the tier, `tier?` an undeclared manifest.
3. For each `✗`, run `rail check <stage> --repo <project>` to get the details, and propose the
   smallest change that turns it green — or a tier change if the declared tier is wrong. When
   this skill and the CLI disagree, the CLI is right.
```

- [ ] **Step 4: Run the test, expect PASS**

```bash
uv run pytest tests/test_skills.py -q
```
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add skills tests/test_skills.py
git commit -m "feat(skills): facade skills for design, plan, check, attest and audit — no rule inside"
```

--- checkpoint ---

## Batch 5: Dogfooding, day-0 snapshot and the phase-1 proof (sequential)

### Task 5.1: red-rail on its own rail at tier `dev`, file ledger

**Files:**
- Modify: `rail.yaml`
- Modify: `Makefile` (`RAIL_FLAGS`, `audit`, `skills-install`)
- Modify: `docs/specs/2026-09-14-red-rail-design.md` (§8 success criteria; note on `rail-ci.yml` location and `--ci`)
- Create: `docs/receipts/*.json` (through the CLI, never by hand)
- Modify: `README.md`, `CLAUDE.md`
- Create: `tests/test_dogfood.py`

- [ ] **Step 1: Write the failing test**

`tests/test_dogfood.py`:
```python
"""red-rail passes its own rail at tier dev, with the review verdict as a declared exception
until the independent reviewer exists (ADR-0003)."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.model import LedgerBackend, Tier, load_rail_config

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_declares_dev_on_the_file_ledger_with_one_exception() -> None:
    cfg = load_rail_config(ROOT)
    assert cfg.tier is Tier.DEV and cfg.ledger is LedgerBackend.FILE
    assert set(cfg.gates) == {"review.verdict"}
    assert cfg.gates["review.verdict"].value is False
    assert "ADR-0003" in cfg.gates["review.verdict"].reason


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_rail_check_passes_on_this_repository_in_ci_scope() -> None:
    out = CliRunner().invoke(main, ["check", "--repo", str(ROOT), "--ci"])
    assert out.exit_code == 0, out.output
    assert "EXC   review.verdict" in out.output
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
uv run pytest tests/test_dogfood.py -q
```
Expected: the manifest assertion fails (`ledger: brain`, no `gates`), the check exits 1.

- [ ] **Step 3: Rewrite `rail.yaml`**

```yaml
rail: 1
project: red-rail
brain_key: red-rail
tier: dev
stack: python
# Standalone first (ADR-0002): the receipts under docs/receipts/ are the ledger in phase 1;
# `ledger: brain` returns with BrainLedger in phase 2.
ledger: file
gates:
  review.verdict:
    value: false
    reason: "independent reviewer arrives in phase 2 (ADR-0003); until then no verdict can be independent"
```

- [ ] **Step 4: Record red-rail's own evidence through the CLI**

```bash
uv run rail contract set \
  --objective "One delivery standard for the ReD ecosystem: executable gates, measured drift, evidence in a ledger" \
  --criterion "rail audit projects/* outputs the 24-repository matrix" \
  --criterion "a fresh scaffold passes rail check at tier bootstrap" \
  --criterion "red-rail passes rail check at tier dev" \
  --criterion "attestations and DORA metrics work on a repository with ledger: file" \
  --reason "phase 1 — the rail without network (spec §8)" \
  --key contract:red-rail:1
uv run rail attest integrated --data sha=d7a11a99a91964073be0af7d0ec7399b475f9f0a --issuer operator
uv run rail ledger list
```
The `integrated` sha is `main@d7a11a9` expanded — the last commit merged on `main`, published on both remotes with a green CI, before this branch. Expected: two receipts under `docs/receipts/`, `rail ledger list` shows `contract` then `integrated`.

If `git rev-parse d7a11a9` prints a different 40-character SHA than the one above, use the printed one — the value must be the real commit.

- [ ] **Step 5: Update the `Makefile`**

```make
# red-rail task runner. Every target is what CI runs, nothing more.

.PHONY: sync lint test check ci audit skills-install

RAIL_FLAGS ?=
DATE ?= $(shell date +%F)

## Install the project and its dev extras
sync:
	uv sync --extra dev

## Lint and format check
lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

## Run the test suite
test:
	uv run pytest -q

## Run the rail gates against this repository (dogfooding; RAIL_FLAGS=--ci in CI)
check:
	uv run rail check $(RAIL_FLAGS)

## What CI runs, in order
ci: lint test check

## Audit every ReD project and keep the dated snapshot (the drift table, versioned)
audit:
	mkdir -p docs/audits
	uv run rail audit .. --json > docs/audits/$(DATE)-projects.json
	uv run rail audit .. > docs/audits/$(DATE)-projects.md
	uv run rail audit ..

## Symlink the facade skills into ~/.claude/skills (operator's workstation)
skills-install:
	mkdir -p $(HOME)/.claude/skills
	for d in skills/*/; do ln -sfn "$(CURDIR)/$$d" "$(HOME)/.claude/skills/$$(basename $$d)"; done
```
(recipe lines are real tabs.)

- [ ] **Step 6: Amend the spec (two small, dated notes)**

In `docs/specs/2026-09-14-red-rail-design.md`, after the "Phases — each with a measured proof" table, add:
```markdown
### Success criteria

The phase proofs above are the success criteria of the POC: each phase is done when its
proof line is observed, and decision 6 (four DORA metrics, conformance score, human
gestures — all derived from ledger evidence) is the measure of the whole.
```
In section 5, after the `red-rail/` tree, add:
```markdown
> Implementation note (phase 1, 2026-09-15): GitHub only calls reusable workflows from
> `.github/workflows/`, so `rail-ci.yml` lives there; `workflows/` keeps `pre-review.js`.
> `rail check --ci` reports the workstation-only gates (`hygiene.remotes`,
> `hygiene.roster_entry`) as skipped, explicitly, because a CI checkout has one remote and no
> ReD root.
```

- [ ] **Step 7: Update `README.md` and `CLAUDE.md`**

`README.md` — replace the Usage and Development sections:
````markdown
## Usage

```bash
uv sync --extra dev
uv run rail check                 # the gates of the declared tier; exit code is the verdict
uv run rail check design --json   # one stage, machine-readable
uv run rail audit ..              # repository × stage matrix over the sibling projects
uv run rail contract set --objective "…" --reason "…"   # stage 1: the delivery contract
uv run rail attest deployed --data sha=<sha> --data digest=<digest>   # evidence, from the host
uv run rail ledger list           # read the evidence back
uv run rail metrics               # four DORA metrics + conformance, from the ledger
uv run rail new red-probe --description "…" --tier prod --stack python   # scaffold + remotes
uv run rail upgrade --repo ../red-probe   # re-apply the template's latest tag
```

## Development

```bash
make ci                      # lint, test, check — exactly what CI runs
make audit                   # dated drift snapshot under docs/audits/
make skills-install          # facade skills into ~/.claude/skills
````
```

`CLAUDE.md` — in **Architecture**, replace the "Planned" bullet with:
```markdown
- `src/rail/policy.py` — tier defaults (`TIER_STAGES`, `GATE_DEFAULTS`, spec section aliases)
  and `effective(repo, key)`: a `gates:` override in `rail.yaml` is a declared exception,
  reported, never hidden.
- `src/rail/gates/` — `hygiene`, `intent`, `design`, `plan`, `build`, `evidence` (stages
  5–10 read the ledger). `rail check --ci` skips the workstation-only gates explicitly.
- `src/rail/ledger/` — the `Ledger` protocol and `FileLedger` (`docs/receipts/*.json`,
  append-only, digest + idempotency key). `BrainLedger` is phase 2.
- `src/rail/commands/` — one module per command, auto-discovered: `check`, `attest`,
  `contract`, `ledger`, `audit`, `metrics`, `new`, `upgrade`.
- `src/rail/audit.py`, `src/rail/metrics.py`, `src/rail/scaffold.py` (+ `copier.yml` at the
  root, files under `template/project/`), `src/rail/remotes.py` (`gh` + `glab`, no token).
- Phase 2: `ledger/brain.py`, `reviewer/`, `workflows/pre-review.js`. Phase 3: `deploy/`.
```
In **Commands**, add `make audit` and `make skills-install` with one-line comments; in **Structure**, add `copier.yml`, `docs/receipts/`, `docs/audits/`, `skills/` (facades, no rule inside) and note `.github/workflows/rail-ci.yml` (reusable). Keep every `make` target cited resolvable (the `hygiene.claude_md` gate checks it).

- [ ] **Step 8: Run the whole rail on itself**

```bash
uv run pytest tests/test_dogfood.py -q && make ci; echo "exit=$?"
uv run rail check
```
Expected: tests pass, `make ci` ends with `exit=0`; `rail check` prints `red-rail  tier=dev  stages=hygiene,intent,design,plan,build,review,integrate`, every gate `PASS` except `EXC   review.verdict  declared exception: independent reviewer arrives in phase 2 (ADR-0003)…`, and `passed 17/17`.

- [ ] **Step 9: Commit (receipts included — they are the ledger)**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
git add rail.yaml Makefile docs/receipts docs/specs/2026-09-14-red-rail-design.md README.md CLAUDE.md tests/test_dogfood.py
git commit -m "chore(rail): dogfood red-rail at tier dev on the file ledger; contract and integration receipts"
```

### Task 5.2: Day-0 audit of the 24 projects, the phase-1 proof, brain records, pull request

**Files:**
- Create: `docs/audits/<today>-projects.json`, `docs/audits/<today>-projects.md` (by `make audit`)
- Create: `tests/test_day0_snapshot.py`

- [ ] **Step 1: Write the failing test**

`tests/test_day0_snapshot.py`:
```python
"""The day-0 snapshot of the real ReD projects is committed evidence: its shape is tested,
its content is the drift table of that day and is expected to change."""

import json
from pathlib import Path

AUDITS = Path(__file__).resolve().parents[1] / "docs" / "audits"


def _latest() -> dict:
    snapshots = sorted(AUDITS.glob("*-projects.json"))
    assert snapshots, "no snapshot under docs/audits/ — run `make audit`"
    return json.loads(snapshots[-1].read_text())


def test_snapshot_shape() -> None:
    data = _latest()
    assert data["schema_version"] == 1
    names = [p["name"] for p in data["projects"]]
    assert len(names) >= 20 and names == sorted(names)
    for project in data["projects"]:
        assert {"name", "declared_tier", "tier_used", "template_version", "stages", "passed", "applicable", "exceptions", "gates"} <= set(project)
        assert 0 <= project["passed"] <= project["applicable"]


def test_red_rail_is_in_the_snapshot_at_tier_dev() -> None:
    rail = next(p for p in _latest()["projects"] if p["name"] == "red-rail")
    assert rail["declared_tier"] == "dev"
    assert rail["passed"] == rail["applicable"]
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
uv run pytest tests/test_day0_snapshot.py -q
```
Expected: `no snapshot under docs/audits/`.

- [ ] **Step 3: Produce the snapshot over the real projects**

```bash
make audit
ls docs/audits/
uv run python -c "import json,glob; d=json.load(open(sorted(glob.glob('docs/audits/*-projects.json'))[-1])); print(len(d['projects']), [p['name'] for p in d['projects']])"
```
Expected: 24 projects (every git repository under `projects/` except `experteam`, which is neither a repository nor a manifest holder), `red-rail` at `dev` with `passed == applicable`. This runs gitleaks over 24 histories — a few minutes. The `.md` table is the human-readable day-0 drift table (23 projects without `rail.yaml` show `bootstrap?`).

- [ ] **Step 4: Run the tests, expect PASS**

```bash
uv run pytest tests/test_day0_snapshot.py -q && make ci; echo "exit=$?"
```
Expected: `2 passed`, `exit=0`.

- [ ] **Step 5: The phase-1 proof, end to end, in the scratchpad (nothing committed from here)**

```bash
S=$(mktemp -d)
uv run rail new red-probe-demo --description "Phase-1 proof: a disposable scaffold." --dest "$S/red-probe-demo" --template "$(pwd)" --no-remotes
uv run rail check --repo "$S/red-probe-demo" --ci; echo "scaffold bootstrap exit=$?"
SHA=$(git -C "$S/red-probe-demo" rev-parse HEAD)
uv run rail attest released --repo "$S/red-probe-demo" --data sha=$SHA --data version=0.1.0 --data digest=sha256:demo
uv run rail attest deployed --repo "$S/red-probe-demo" --data sha=$SHA --data digest=sha256:demo
uv run rail attest rolled_back --repo "$S/red-probe-demo" --data drill=true --key drill-1-back
uv run rail attest restored --repo "$S/red-probe-demo" --data drill=true --key drill-1-restored
uv run rail metrics --repo "$S/red-probe-demo"
uv run rail check --all --repo "$S/red-probe-demo" --ci | grep -E "released|deployed|drill"
```
Expected: `scaffold bootstrap exit=0`; `rail metrics` shows `deployments 1`, a lead time in hours, `change failure rate 0` (the drill does not count), `conformance 10/10`; the `--all` check shows `PASS  release.released`, `PASS  deploy.deployed`, `PASS  observe.drill`. Paste these outputs in the PR description as the proof. (`--template "$(pwd)"` uses the working tree's git checkout: copier clones it and reads the *committed* template, so commit before running this step.)

- [ ] **Step 6: Commit the snapshot**

```bash
git add docs/audits tests/test_day0_snapshot.py
git commit -m "docs(audit): day-0 snapshot of the 24 ReD projects and its shape test"
```

- [ ] **Step 7: Brain records (the operator's session does this — MCP tools are not in the executor)**

- `brain_log_decision` (project `red-rail`): red-rail dogfoods `ledger: file` in phase 1, `review.verdict` declared exception until ADR-0003's reviewer exists; `rail-ci.yml` under `.github/workflows/`; `rail new` shells out to `gh`/`glab`; workstation-scoped gates under `--ci`.
- `brain_create_runbook`: "Create a ReD sub-project with `rail new`" superseding runbook `a050e6ec` (15 manual steps → one command + the roster row + `make skills-install`).
- `brain_update_project_focus("red-rail", …)`: phase 1 delivered (numbers: tests, gates, projects audited), phase 2 next (BrainLedger on ticket `04bc1f4a`, reviewer on `headless-agents`), open items (roster edits are manual, red-rail still private, template tag `v0.2.0` to cut after merge so `rail upgrade` has a target).

- [ ] **Step 8: Publish the branch and open the PR (English), then mirror after merge**

```bash
git push -u origin feat/phase-1-rail-without-network
gh pr create --title "feat: phase 1 — the rail without network" --body-file - <<'PR'
## Summary

Phase 1 of the design spec (`docs/specs/2026-09-14-red-rail-design.md` §8): gates as pure functions scored against the declared tier, `Ledger` protocol + `FileLedger`, `rail attest` / `rail contract set` / `rail ledger list` / `rail metrics`, `rail audit` with a golden matrix and the day-0 snapshot of the 24 projects, copier template + `rail new` / `rail upgrade`, reusable `rail-ci.yml`, five facade skills. red-rail runs on its own rail at tier `dev` with a file ledger.

## Proof (spec §8, phase 1)

- `rail audit ..` → 24 projects: `docs/audits/<date>-projects.md`
- fresh scaffold passes `bootstrap`: (paste the output of Batch 5 / Task 5.2 / Step 5)
- red-rail passes `dev`: `make ci` exit 0, `rail check` 17/17 with one declared exception
- attestations + DORA on a `ledger: file` repository: (paste `rail metrics` output)

## Test plan

- [ ] `make ci` green locally and in CI
- [ ] golden matrix unchanged (`tests/golden/audit-matrix.json`)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
PR
```
After the operator merges: `git switch main && git pull --ff-only origin main && git push gitlab main && git ls-remote origin refs/heads/main && git ls-remote gitlab refs/heads/main` — the two SHAs must match. Then tag `v0.2.0` on `main` and push it to both remotes so `rail new` can pin `--template-ref v0.2.0` and `rail upgrade` has a target.
