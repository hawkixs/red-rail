# Receipts spool and mechanical receipts verdict — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Under `ledger: brain`, a pending attestation waits in a spool on the host and no receipt
is ever written into the repository. Under both ledgers, a records-only pull request gets a
mechanical verdict instead of a judge.

**Architecture:** `BrainLedger` keeps its mirror-first rule. The mirror is now a `FileLedger`
rooted in `$RAIL_SPOOL_DIR/<project>`, and the file leaves once brain has recorded its payload.
`hygiene.mirrors` fails while the spool is not empty, and `rail ledger replay` empties it. When
every changed path is a record, the reviewer branches before any round logic and judges the diff
mechanically.

**Tech Stack:** Python 3.12, uv, Click, Pydantic 2, pytest, ruff. Brain is faked in memory
(`tests/fake_brain.py`, through `BrainClient.in_memory`).

**Spec:** `docs/specs/2026-09-25-spool-replaces-committed-mirrors.md`

## Global Constraints

- Everything is written in English. The repository is public: write no address, hostname or
  personal path. `~/.local/state/red-rail/spool` is generic and is the only path this plan adds.
- Run tools as `env -u VIRTUAL_ENV uv run …`. In a fresh worktree, run
  `env -u VIRTUAL_ENV uv sync --all-extras` once first.
- A gate never raises. `hygiene.mirrors` keeps the scope `ledger`.
- No test touches the developer's `~/.local/state`. Task A1 makes `tests/conftest.py` point
  `RAIL_SPOOL_DIR` at a temporary directory for every test.
- `make ci > /tmp/ci.log 2>&1; echo "exit=$?"`, then read the summary lines. Never pipe
  `make ci` through grep (learning `341e7d19`).
- Two pull requests: PR A is Tasks A1–A6, PR B is Tasks B1–B2. Before opening each one, measure
  `git diff --numstat origin/main...HEAD`. Excluding lockfiles, generated files, receipts and
  vendored code, it must stay below 3,000 changed lines.
- Commit through `/git-commit`. Never use `git stash`.

## Review Focus

1. **A receipt that brain can never accept.** When a pending receipt has the same key as a row in
   brain but a different payload, it must leave the spool, or `hygiene.mirrors` stays red forever.
   Task A2 pins it.
2. **One spool per project, not per checkout.** Two checkouts of one project on one host share one
   spool: a verdict the reviewer spooled from the main checkout is replayed from a session's
   worktree. Task A1 pins that the spool depends on the project, never on a repository path.
3. **A tampered spool file fails closed.** `hygiene.mirrors` fails and names the file; the file is
   never skipped. Task A4 pins it.
4. **An edited or deleted receipt is always refused.** A records-only pull request that also
   deletes or edits an existing receipt is refused, even when every added receipt is valid.
   Task B1 pins it.
5. **A mechanical verdict stays out of the review loop.** It never becomes the "open findings" of
   a later judged round of the same pull request. Task B2 pins it.

---

## PR A — the spool

### Task A1: the spool location; `attest` spools, then drains

**Files:**
- Create: `src/rail/ledger/spool.py`
- Modify: `src/rail/ledger/brain.py` (the docstring, `__init__`, `contract_set`, `bind`, `attest`,
  `accept`)
- Modify: `src/rail/ledger/__init__.py` (the docstring, the `Unattested` message, `open_ledger`)
- Modify: `tests/conftest.py`, `tests/test_ledger_brain.py`, `tests/test_cli_bind.py`,
  `tests/test_cli_accept.py`. In these tests the `receipts_dir=` argument becomes `spool_dir=`.

**Interfaces:**
- Produces: `rail.ledger.spool.SPOOL_VARIABLE = "RAIL_SPOOL_DIR"` and
  `spool_directory(project: str, env: Mapping[str, str] | None = None) -> Path`.
- Produces: `BrainLedger(client, *, ticket, project, spool_dir: Path, clock=None,
  repository_id=None, requester=REQUESTER)`, with the attribute `spool: FileLedger` (it replaces
  `mirrors`).

- [ ] **Step 1: isolate every test from the host spool.** Append to `tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _no_host_spool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pending attestations wait in `$RAIL_SPOOL_DIR/<project>`: a test never sees the
    developer's own `~/.local/state/red-rail/spool`."""
    monkeypatch.setenv("RAIL_SPOOL_DIR", str(tmp_path / "spool"))
```

- [ ] **Step 2: write the failing tests.** In `tests/test_ledger_brain.py`:
  - make `_ledger` pass `spool_dir=tmp_path / "spool" / "red-probe"`;
  - rename the other `receipts_dir=` arguments to `spool_dir=`;
  - replace `test_attest_writes_the_mirror_first_and_sends_the_same_instant` and
    `test_a_refusal_after_the_mirror_is_unattested_and_the_replay_lands` with the tests below;
  - in `test_contract_set_uses_cas_and_mirrors_the_revision`,
    `test_contract_set_is_idempotent_on_content_and_mirrors_by_ticket_and_revision` and
    `test_accept_calls_brain_as_the_requester_and_mirrors_the_receipt`, replace every assertion on
    a mirror file with `assert not list(tmp_path.rglob("*.json"))`, and drop "mirrors" from their
    names.

```python
from rail.ledger.spool import spool_directory


def test_attest_spools_first_then_drains_once_brain_recorded(tmp_path: Path) -> None:
    ledger, brain, _ = _ledger(tmp_path)
    ledger.contract_set("red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0")
    seen: list[list[Path]] = []
    real = ledger.client.call

    def spy(name, arguments, *, agent=None):
        if name == "brain_delivery_attest":
            seen.append(sorted(ledger.spool.root.glob("*.json")))
        return real(name, arguments, agent=agent)

    ledger.client.call = spy
    record = ledger.attest(
        "red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1"
    )
    assert [load_receipt(p) for p in seen[0]] == [record], "written before the call"
    assert not list(ledger.spool.root.glob("*.json")), "gone once brain recorded it"
    assert ledger.spool.root.stat().st_mode & 0o777 == 0o700, "the project's spool is private"
    assert datetime.fromisoformat(brain.attestations[-1]["emitted_at"]) == record.recorded_at
    assert ledger.list("red-probe", attestation=AttestationKind.DEPLOYED) == [record]


def test_a_refusal_leaves_the_receipt_in_the_spool_and_a_replay_drains_it(tmp_path: Path) -> None:
    ledger, brain, _ = _ledger(tmp_path)
    ledger.contract_set("red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0")
    brain.enabled = False
    with pytest.raises(Unattested) as exc:
        ledger.attest(
            "red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1"
        )
    assert exc.value.cause == "delivery_disabled"
    assert exc.value.receipt.parent == ledger.spool.root and exc.value.receipt.is_file()
    assert "rail ledger replay" in str(exc.value)
    brain.enabled = True
    waiting = load_receipt(exc.value.receipt)
    again = ledger.attest(
        "red-probe",
        AttestationKind.DEPLOYED,
        waiting.data,
        issuer=waiting.issuer,
        idempotency_key=waiting.idempotency_key,
        emitted_at=waiting.recorded_at,
    )
    assert again == waiting and len(brain.attestations) == 1
    assert not exc.value.receipt.exists()


def test_the_spool_belongs_to_the_project_not_to_a_checkout() -> None:
    assert spool_directory("red-probe", {"RAIL_SPOOL_DIR": "/s"}) == Path("/s/red-probe")
    assert spool_directory("red-probe", {}) == (
        Path("~/.local/state/red-rail/spool").expanduser() / "red-probe"
    )
```

- [ ] **Step 3: run them.** Run
  `env -u VIRTUAL_ENV uv run pytest tests/test_ledger_brain.py -q`. Expected: FAIL, with a
  `TypeError` (unexpected keyword `spool_dir`) and an `ImportError` for `rail.ledger.spool`.

- [ ] **Step 4: implement.** Create `src/rail/ledger/spool.py`:

```python
"""Where an attestation waits for brain under `ledger: brain` (spec
2026-09-25-spool-replaces-committed-mirrors, decision 1): one directory per host and per
project, never inside a repository, holding only what brain has not recorded yet."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

SPOOL_VARIABLE = "RAIL_SPOOL_DIR"
DEFAULT_SPOOL = "~/.local/state/red-rail/spool"


def spool_directory(project: str, env: Mapping[str, str] | None = None) -> Path:
    """`$RAIL_SPOOL_DIR/<project>`, the root defaulting to `~/.local/state/red-rail/spool`."""
    source = os.environ if env is None else env
    return Path(source.get(SPOOL_VARIABLE) or DEFAULT_SPOOL).expanduser() / project
```

  Then make these changes in `src/rail/ledger/brain.py`:
  - **`__init__`:** take `spool_dir: Path` instead of `receipts_dir`, and set
    `self.spool = FileLedger(spool_dir, clock=clock)`.
  - **`contract_set`:** end with `return self._contract_record(row)`.
  - **`bind`:** end with `return record`.
  - **`accept`:** end with `return self._milestone_record(row, AttestationKind.FULFILLED)`.
    None of these three writes a file any more.
  - **Module docstring:** "the repository's receipts are mirrors" becomes "an attestation waits in
    the host's spool (`ledger/spool.py`) until brain has recorded it".
  - **`attest`:** its body after the milestone check becomes:

```python
        self.spool.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = self.spool.attest(
            project, kind, data, issuer=issuer, idempotency_key=idempotency_key, emitted_at=emitted_at
        )
        receipt = self.spool.path_of(record)
        try:
            row = self._call(
                "brain_delivery_attest",
                {
                    "ticket_id": str(self.ticket),
                    "actor_project": project,
                    "kind": kind.value,
                    "payload": data,
                    "idempotency_key": idempotency_key,
                    "emitted_at": record.recorded_at.isoformat(),
                },
                agent=issuer,
            )
        except BrainToolError as exc:
            raise Unattested(receipt, exc.code) from exc
        except BrainUnreachable as exc:
            raise Unattested(receipt, "unreachable") from exc
        if str(row.get("digest")) != brain_digest(data):
            raise LedgerError("brain stored a different payload digest than the spooled receipt's")
        receipt.unlink(missing_ok=True)
        return record
```

  In `src/rail/ledger/__init__.py`:
  - **`open_ledger`:** import `from rail.ledger.spool import spool_directory` and build
    `BrainLedger(client, ticket=cfg.ticket, project=cfg.project, spool_dir=spool_directory(cfg.project))`.
  - **Module docstring:** "the receipts become mirrors" becomes "a pending attestation waits in the
    host's spool".
  - **`Unattested`:** its message becomes:

```python
        super().__init__(
            f"attestation not recorded ({cause}); the receipt {receipt.name} waits in "
            f"{receipt.parent} — replay it with: rail attest {label} --from {receipt}, "
            "or every waiting one with: rail ledger replay"
        )
```

- [ ] **Step 5: run the three suites.** Run `env -u VIRTUAL_ENV uv run pytest tests/test_ledger_brain.py tests/test_cli_bind.py
  tests/test_cli_accept.py -q`. Expected: all pass.
- [ ] **Step 6: commit.** Use `/git-commit` with `✨ feat(ledger): a pending attestation waits in a
  host spool, never in the repository`.

### Task A2: settling a reused key, and listing and replaying the spool

**Files:**
- Modify: `src/rail/ledger/brain.py`
- Test: `tests/test_ledger_brain.py`

**Interfaces:**
- Consumes: `BrainLedger.spool` (A1).
- Produces:
  - `BrainLedger.pending() -> list[Record]`, oldest first;
  - `BrainLedger.replay(record: Record) -> Literal["recorded", "already recorded"]`, which raises
    `Unattested` or `IdempotencyConflict`;
  - private helpers:
    - `_row_by_key(kind, key) -> dict | None`, which lets `BrainToolError` and
      `BrainUnreachable` through;
    - `_lookup(receipt, kind, key) -> dict | None`, which turns them into `Unattested`, so that
      an unreachable brain always means "still waiting";
    - `_settle(record, receipt, row) -> Record`.

- [ ] **Step 1: write the failing tests.**

```python
from rail.ledger import IdempotencyConflict

RELEASE = {"version": "1.0.0", "digest": "sha256:" + "a" * 64}


def test_a_key_brain_holds_with_the_same_payload_is_already_recorded(tmp_path: Path) -> None:
    ledger, brain, _ = _ledger(tmp_path)
    ledger.contract_set("red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0")
    first = ledger.attest("red-probe", AttestationKind.RELEASED, RELEASE, issuer="op", idempotency_key="r1")
    later = ledger.attest("red-probe", AttestationKind.RELEASED, RELEASE, issuer="op", idempotency_key="r1")
    assert later == first, "brain's row, although the clock gave the retry a new instant"
    assert len(brain.attestations) == 1 and ledger.pending() == []


def test_a_key_brain_holds_with_another_payload_is_a_conflict_that_leaves_the_spool(
    tmp_path: Path,
) -> None:
    ledger, brain, _ = _ledger(tmp_path)
    ledger.contract_set("red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0")
    ledger.attest("red-probe", AttestationKind.RELEASED, RELEASE, issuer="op", idempotency_key="r1")
    other = {**RELEASE, "digest": "sha256:" + "b" * 64}
    with pytest.raises(IdempotencyConflict) as exc:
        ledger.attest("red-probe", AttestationKind.RELEASED, other, issuer="op", idempotency_key="r1")
    assert brain_digest(RELEASE) in str(exc.value) and brain_digest(other) in str(exc.value)
    assert ledger.pending() == []


def test_pending_is_oldest_first_and_replay_says_what_happened(tmp_path: Path) -> None:
    ledger, brain, _ = _ledger(tmp_path)
    ledger.contract_set("red-probe", CONTRACT, reason="bootstrap", issuer="op", idempotency_key="c0")
    done = ledger.attest(
        "red-probe", AttestationKind.DEPLOYED, {"sha": "a" * 40}, issuer="op", idempotency_key="d0"
    )
    brain.enabled = False
    for key, sha in (("d1", "b"), ("d2", "c")):
        with pytest.raises(Unattested):
            ledger.attest(
                "red-probe", AttestationKind.DEPLOYED, {"sha": sha * 40}, issuer="op", idempotency_key=key
            )
    brain.enabled = True
    ledger.spool.mirror(done)  # the crash window: recorded by brain, not yet removed
    waiting = ledger.pending()
    assert [r.idempotency_key for r in waiting] == ["d0", "d1", "d2"]
    assert [ledger.replay(r) for r in waiting] == ["already recorded", "recorded", "recorded"]
    assert ledger.pending() == [] and len(brain.attestations) == 3
```

- [ ] **Step 2: run them.** Run `env -u VIRTUAL_ENV uv run pytest tests/test_ledger_brain.py -q`. Expected: FAIL. The first
  two fail on `Unattested ... idempotency_key_reused`; the third fails with `AttributeError`
  (`pending`).
- [ ] **Step 3: implement.** In `attest`, the `BrainToolError` branch becomes:

```python
        except BrainToolError as exc:
            if exc.code == "idempotency_key_reused":
                row = self._lookup(receipt, kind, idempotency_key)
                if row is not None:
                    return self._settle(record, receipt, row)
            raise Unattested(receipt, exc.code) from exc
```

  and add to `BrainLedger` (import `Literal` from `typing`, `IdempotencyConflict` from
  `rail.ledger`):

```python
    def pending(self) -> list[Record]:
        """The attestations waiting in this project's spool, oldest first."""
        return self.spool.list(self.project, kind=RecordKind.ATTESTATION)

    def replay(self, record: Record) -> Literal["recorded", "already recorded"]:
        """Send one waiting receipt again; it leaves the spool once brain holds its payload."""
        kind = record.attestation
        if kind is None:
            raise LedgerError(f"{record.idempotency_key}: not an attestation")
        receipt = self.spool.path_of(record)
        row = self._lookup(receipt, kind, record.idempotency_key)
        if row is not None:
            self._settle(record, receipt, row)
            return "already recorded"
        self.attest(
            self.project,
            kind,
            record.data,
            issuer=record.issuer,
            idempotency_key=record.idempotency_key,
            emitted_at=record.recorded_at,
        )
        return "recorded"

    def _lookup(self, receipt: Path, kind: AttestationKind, key: str) -> dict[str, Any] | None:
        """Brain's row for a key; a brain that cannot answer leaves the receipt waiting."""
        try:
            return self._row_by_key(kind, key)
        except BrainToolError as exc:
            raise Unattested(receipt, exc.code) from exc
        except BrainUnreachable as exc:
            raise Unattested(receipt, "unreachable") from exc

    def _row_by_key(self, kind: AttestationKind, key: str) -> dict[str, Any] | None:
        cursor: str | None = None
        while True:
            arguments: dict[str, Any] = {
                "actor_project": self.project,
                "ticket_id": str(self.ticket),
                "kind": kind.value,
                "limit": PAGE,
                "cursor": cursor,
            }
            page = self._call("brain_delivery_attestation_list", arguments)
            for row in page.get("items", []):
                if row.get("idempotency_key") == key and row.get("issuer_project") == self.project:
                    return row
            cursor = page.get("next_cursor")
            if not cursor:
                return None

    def _settle(self, record: Record, receipt: Path, row: dict[str, Any]) -> Record:
        """Brain holds this key already (spec decision 2, step 5). Its replay equality also
        compares the instant, the issuer label and the contract revision, so only the payload
        digest decides: the same payload is recorded; another can never be. Either way the
        receipt leaves the spool, so it never holds `hygiene.mirrors` red forever."""
        receipt.unlink(missing_ok=True)
        ours = brain_digest(record.data)
        if str(row["digest"]) != ours:
            raise IdempotencyConflict(
                f"brain holds {record.idempotency_key!r} with payload digest {row['digest']}; "
                f"this attestation's is {ours}: it cannot be recorded and left the spool"
            )
        settled = record_from_row(self.project, row)
        if settled is None:
            raise LedgerError(f"brain row {row.get('id')}: unknown kind {row.get('kind')!r}")
        return settled
```

- [ ] **Step 4: run the suite again.** Run `env -u VIRTUAL_ENV uv run pytest tests/test_ledger_brain.py -q`. Expected: all pass.
- [ ] **Step 5: commit.** Use `/git-commit` with `✨ feat(ledger): settle a reused key on its payload, list and
  replay the spool`.

### Task A3: `rail attest --from` and `rail ledger replay`

**Files:**
- Modify: `src/rail/commands/attest.py` (`echo_record`)
- Modify: `src/rail/commands/ledger.py` (new `replay` subcommand)
- Test: `tests/test_cli_ledger.py`

**Interfaces:**
- Consumes: `BrainLedger.pending()` and `BrainLedger.replay(record)` (A2), and
  `spool_directory` (A1).

- [ ] **Step 1: write the failing tests.** Add them to `tests/test_cli_ledger.py`:

```python
import pytest

from rail.brain.client import BrainClient
from rail.commands import attest as attest_command
from rail.commands import ledger as ledger_command
from rail.ledger import Contract, Deliverable, Unattested
from rail.ledger.brain import BrainLedger
from rail.ledger.spool import spool_directory
from tests.fake_brain import FakeBrain
from tests.helpers import conforming_tree

PROBE = Contract(
    objective="ship the probe",
    deliverables=[
        Deliverable(key="probe", repository="hawkixs/red-probe", repository_id=4242,
                    no_checks_reason="fixture: no check declared")
    ],
)


def _brain_repo(tmp_path: Path) -> tuple[Path, BrainLedger, FakeBrain]:
    repo = conforming_tree(tmp_path, "red-probe", "dev")
    manifest = (repo / "rail.yaml").read_text()
    (repo / "rail.yaml").write_text(
        manifest.replace("ledger: file\n", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n")
    )
    brain = FakeBrain(agent="operator")
    ticket = brain.add_ticket("red", "red-probe")
    brain.register_repository("red-probe", 4242, "hawkixs/red-probe")
    ledger = BrainLedger(
        BrainClient.in_memory(brain, agent="operator"),
        ticket=ticket,
        project="red-probe",
        spool_dir=spool_directory("red-probe"),
        repository_id=lambda slug: 4242,
    )
    ledger.contract_set("red-probe", PROBE, reason="r", issuer="red", idempotency_key="c1")
    return repo, ledger, brain


def _refused(ledger: BrainLedger, brain: FakeBrain) -> Path:
    brain.enabled = False
    with pytest.raises(Unattested) as exc:
        ledger.attest("red-probe", AttestationKind.DEPLOYED, {"sha": "b" * 40}, issuer="op", idempotency_key="d1")
    brain.enabled = True
    return exc.value.receipt


def test_ledger_replay_empties_the_spool_and_says_what_happened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, ledger, brain = _brain_repo(tmp_path)
    monkeypatch.setattr(ledger_command, "open_ledger", lambda repo: ledger)
    _refused(ledger, brain)
    brain.enabled = False
    out = CliRunner().invoke(main, ["ledger", "replay", "--repo", str(repo)])
    assert out.exit_code == 2 and "unattested: delivery_disabled" in out.output
    brain.enabled = True
    out = CliRunner().invoke(main, ["ledger", "replay", "--repo", str(repo)])
    assert out.exit_code == 0 and "recorded" in out.output and ledger.pending() == []


def test_ledger_replay_on_the_file_ledger_has_no_spool(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    out = CliRunner().invoke(main, ["ledger", "replay", "--repo", str(repo)])
    assert out.exit_code == 0 and "no spool" in out.output


def test_attest_from_a_spooled_receipt_records_it_and_drains_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, ledger, brain = _brain_repo(tmp_path)
    monkeypatch.setattr(attest_command, "open_ledger", lambda repo: ledger)
    receipt = _refused(ledger, brain)
    out = CliRunner().invoke(main, ["attest", "deployed", "--repo", str(repo), "--from", str(receipt)])
    assert out.exit_code == 0 and "recorded in brain" in out.output
    assert not receipt.exists() and not list((repo / RECEIPTS_DIR).glob("*-deployed-*.json"))
```

- [ ] **Step 2: run them.** Run `env -u VIRTUAL_ENV uv run pytest tests/test_cli_ledger.py -q`. Expected: FAIL. `replay` is not
  a command, and the output says `docs/receipts/…` instead of `recorded in brain`.
- [ ] **Step 3: implement.** In `src/rail/commands/attest.py`, `echo_record` prints where the record
  is kept (import `RECEIPTS_DIR`):

```python
    kept = repo / RECEIPTS_DIR / receipt_filename(record)
    where = f"{RECEIPTS_DIR}/{kept.name}" if kept.is_file() else "recorded in brain"
    click.echo(f"{label}  {record.idempotency_key}  {record.digest}  {where}")
```

  In `src/rail/commands/ledger.py`, add the following. Import `IdempotencyConflict` and
  `Unattested` from `rail.ledger`, `BrainLedger` from `rail.ledger.brain`, `receipt_filename`
  from `rail.ledger.file`, and `LedgerBackend` from `rail.model`.

```python
@command.command("replay")
@repo_option
@json_option
def replay(repo: Path, as_json: bool) -> None:
    """Replay every attestation waiting in this project's spool, oldest first. Exit 0: the
    spool is empty; 2: one still waits (brain refused or is down); 1: a conflict or an error."""
    try:
        cfg = load_rail_config(repo)
        if cfg.ledger is LedgerBackend.FILE:
            click.echo("file ledger: the receipts are the ledger, there is no spool")
            return
        ledger = open_ledger(repo)
        if not isinstance(ledger, BrainLedger):
            raise LedgerError("ledger: brain did not open a brain ledger")
        waiting = ledger.pending()
    except (FileNotFoundError, ValidationError, LedgerError) as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from exc
    lines: list[dict[str, str]] = []
    code = 0
    for record in waiting:
        try:
            outcome = ledger.replay(record)
        except Unattested as exc:
            outcome, code = f"unattested: {exc.cause}", code or 2
        except IdempotencyConflict as exc:
            outcome, code = f"conflict: {exc}", 1
        except LedgerError as exc:
            outcome, code = f"error: {exc}", 1
        lines.append({"receipt": receipt_filename(record), "outcome": outcome})
    if as_json:
        click.echo(json.dumps(lines, indent=2))
    else:
        for line in lines:
            click.echo(f"{line['receipt']}  {line['outcome']}")
        if not lines:
            click.echo("the spool is empty")
    if code:
        raise SystemExit(code)
```

- [ ] **Step 4: run the suite again.** Run `env -u VIRTUAL_ENV uv run pytest tests/test_cli_ledger.py -q`. Expected: all pass.
- [ ] **Step 5: commit.** Use `/git-commit` with `✨ feat(ledger): rail ledger replay sends every waiting attestation`.

### Task A4: `hygiene.mirrors` reads the spool

**Files:**
- Modify: `src/rail/gates/hygiene.py` (`mirrors`)
- Test: `tests/test_gates_hygiene.py`

- [ ] **Step 1: write the failing tests.** Add them to `tests/test_gates_hygiene.py`:

```python
def _brain_manifest(repo: Path) -> None:
    text = (repo / "rail.yaml").read_text()
    (repo / "rail.yaml").write_text(
        text.replace("ledger: file\n", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n")
    )


class _EmptyShared:
    def list(self, project, *, kind=None, attestation=None):
        return []


def test_mirrors_fails_while_an_attestation_waits_in_the_spool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail.gates import hygiene
    from rail.ledger import AttestationKind
    from rail.ledger.file import FileLedger
    from rail.ledger.spool import spool_directory

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    _brain_manifest(repo)
    monkeypatch.setattr(hygiene, "open_ledger", lambda repo: _EmptyShared())
    assert hygiene.mirrors(repo).passed
    FileLedger(spool_directory("red-alpha")).attest(
        "red-alpha", AttestationKind.DEPLOYED, {"sha": "a" * 40}, issuer="op", idempotency_key="d1"
    )
    result = hygiene.mirrors(repo)
    assert not result.passed
    assert "1 attestation(s) waiting in the spool" in result.details
    assert "rail ledger replay" in result.details


def test_mirrors_fails_closed_on_a_tampered_spool_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail.gates import hygiene
    from rail.ledger import AttestationKind
    from rail.ledger.file import FileLedger
    from rail.ledger.spool import spool_directory

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    _brain_manifest(repo)
    monkeypatch.setattr(hygiene, "open_ledger", lambda repo: _EmptyShared())
    spool = FileLedger(spool_directory("red-alpha"))
    record = spool.attest(
        "red-alpha", AttestationKind.DEPLOYED, {"sha": "a" * 40}, issuer="op", idempotency_key="d1"
    )
    path = spool.path_of(record)
    path.write_text(path.read_text().replace("a" * 40, "b" * 40))
    result = hygiene.mirrors(repo)
    assert not result.passed and path.name in result.details
```

- [ ] **Step 2: run them.** Run `env -u VIRTUAL_ENV uv run pytest tests/test_gates_hygiene.py -q`. Expected: FAIL, because both
  gates pass.
- [ ] **Step 3: implement.** In `mirrors`, right after the `cfg` assertion and before reading
  `docs/receipts` (import `spool_directory`), add the block below. Then change the docstring's
  first sentence to: "`ledger: brain`: nothing waits in the host's spool, and every attestation
  receipt kept in the checkout (history, spec 2026-09-25-spool-replaces-committed-mirrors)
  mirrors a row in the shared ledger".

```python
    try:
        waiting = FileLedger(spool_directory(cfg.project)).list(
            cfg.project, kind=RecordKind.ATTESTATION
        )
    except LedgerError as exc:
        return GateResult(Stage.HYGIENE, "mirrors", False, f"spool: {exc}")
    if waiting:
        since = waiting[0].recorded_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        return GateResult(
            Stage.HYGIENE,
            "mirrors",
            False,
            f"{len(waiting)} attestation(s) waiting in the spool since {since}: "
            "replay with `rail ledger replay`",
        )
```

- [ ] **Step 4: run the suite again.** Run `env -u VIRTUAL_ENV uv run pytest tests/test_gates_hygiene.py -q`. Expected: all pass,
  including the three existing mirror tests.
- [ ] **Step 5: commit.** Use `/git-commit` with `✨ feat(gates): hygiene.mirrors fails while an attestation waits in
  the spool`.

### Task A5: the callers that still promised a file — the reviewer, `rail release`, `rail new`, the template

**Files:**
- Modify: `src/rail/reviewer/service.py` (`_publish`)
- Modify: `src/rail/commands/release.py` (the docstring and the last hint)
- Modify: `src/rail/scaffold.py` (the brain branch of `new_project`, and `_interrupted_birth`)
- Modify: `src/rail/deploy/flow.py` (the module docstring)
- Modify: `template/project/CLAUDE.md.jinja` (lines 120–122)
- Test: `tests/test_reviewer_service.py`, `tests/test_scaffold.py`

- [ ] **Step 1: write the failing tests.**
  - In `tests/test_scaffold.py`, rename
    `test_brain_mode_records_the_contract_after_the_remotes_and_mirrors_it` to
    `test_brain_mode_records_the_contract_after_the_remotes_and_commits_nothing_more`. Its end
    becomes:

```python
    assert gitrepo.recent_subjects(project.dest, 2) == [
        "chore: bootstrap red-probe with the ReD rail"
    ]
    assert not list((project.dest / RECEIPTS_DIR).glob("*-contract-*.json"))
    assert calls == []
```

  - Update every test that asserts the text "commit the mirror receipt" or "and its mirror
    receipt" of an interrupted birth. Its steps are now 1 and 2 only.
  - Add to `tests/test_reviewer_service.py`:

```python
def test_a_verdict_on_the_brain_ledger_leaves_no_file_in_the_checkout(tmp_path: Path) -> None:
    from rail.brain.client import BrainClient
    from rail.ledger.brain import BrainLedger
    from rail.ledger.spool import spool_directory
    from tests.fake_brain import FakeBrain

    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    brain = FakeBrain(agent="red-rail-reviewer")
    ticket = brain.add_ticket("red", "red-alpha")
    brain.register_repository("red-alpha", 4242, "hawkixs/red-alpha")
    ledger = BrainLedger(
        BrainClient.in_memory(brain, agent="red-rail-reviewer"),
        ticket=ticket,
        project="red-alpha",
        spool_dir=spool_directory("red-alpha"),
        repository_id=lambda slug: 4242,
    )
    ledger.contract_set(
        "red-alpha",
        Contract(objective="x", deliverables=[Deliverable(
            key="main", repository="hawkixs/red-alpha", repository_id=4242,
            no_checks_reason="fixture: no check declared")]),
        reason="bootstrap", issuer="red", idempotency_key="c1",
    )
    outcome = review_pull(
        PR, github=FakeGitHub(), policy=default_policy(), ledger=ledger, project="red-alpha",
        repo_path=repo, run_judge=lambda pr, diff, policy, *, provider, tier, criteria, **_: approve(provider, tier),
        root=tmp_path,
    )
    assert outcome.attested and outcome.receipt is None
    assert not list((repo / RECEIPTS_DIR).glob("*review_verdict*")) and ledger.pending() == []
```

- [ ] **Step 2: run them.** Run `env -u VIRTUAL_ENV uv run pytest tests/test_scaffold.py tests/test_reviewer_service.py -q`.
  Expected: FAIL. `rail new` still makes the mirror commit, and `outcome.receipt` names a path
  that does not exist.
- [ ] **Step 3: implement.**
  - **`_publish`:** set the path only when the file exists (a file ledger):
    `kept = repo_path / RECEIPTS_DIR / receipt_filename(record)` and
    `outcome.receipt = kept if kept.is_file() else None`.
  - **`new_project`**, brain branch: keep the `record_contract` call and its `LedgerError`
    handling, and delete the `_git(... "add" ...)`, `_git(... "commit" ...)` and `remotes.push`
    lines that follow it.
  - **`_interrupted_birth`:** say "the contract is missing", and keep steps 1 and 2 only.
  - **`commands/release.py`:** the docstring says the receipt lands in `docs/receipts/` only
    under `ledger: file`, and the last hint is printed only when the receipt exists:
    `if not as_json and (repo / RECEIPTS_DIR / receipt_filename(record)).is_file():`.
  - **`deploy/flow.py` docstring:** "A brain refusal never stops a flow: the waiting receipts and
    their replay commands are reported together at the end."
  - **`template/project/CLAUDE.md.jinja`:** "…with `ledger: brain` it is the attestations against
    the ticket named in `rail.yaml`, the receipts being their mirrors." becomes "…with
    `ledger: brain` it is the attestations against the ticket named in `rail.yaml`; one brain has
    not recorded yet waits in the host's spool until `rail ledger replay`."
- [ ] **Step 4: run the full suite.** Run `env -u VIRTUAL_ENV uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: commit.** Use `/git-commit` with `♻️ refactor: under ledger brain no caller writes or promises a
  receipt in the repository`.

### Task A6: a whole delivery writes nothing, and the documents

**Files:**
- Test: `tests/test_ledger_brain.py`
- Modify: `docs/adr/0002-pluggable-ledger-standalone-first.md`, `CLAUDE.md`, `AGENTS.md`,
  `skills/rail-attest/SKILL.md`, `skills/rail-release/SKILL.md`, `skills/rail-deploy/SKILL.md`

- [ ] **Step 1: write the delivery test (spec success criterion 1).** It exercises every writer a
  delivery calls: contract, binding, verdict, release, deploy, drill and acceptance.

```python
def test_a_whole_delivery_in_brain_mode_leaves_no_file_anywhere(tmp_path: Path) -> None:
    from rail.deploy.flow import Attester
    from rail.ledger import PullRequestRef

    ledger, brain, ticket = _ledger(tmp_path)
    ledger.contract_set("red-probe", CONTRACT, reason="bootstrap", issuer="red", idempotency_key="c0")
    sha, digest = "c" * 40, "sha256:" + "d" * 64
    ledger.bind(
        "red-probe", PullRequestRef(repository="hawkixs/red-probe", number=3, head_sha=sha),
        issuer="op", idempotency_key="bind:hawkixs/red-probe:3",
    )
    ledger.attest(
        "red-probe", AttestationKind.REVIEW_VERDICT,
        {"sha": sha, "independent": True, "verdict": "approve", "check_run_id": 99,
         "repository": "hawkixs/red-probe", "pr": 3},
        issuer="red-rail-reviewer", idempotency_key=f"review_verdict:{sha}:99",
    )
    brain.integrate(ticket, sha, issued_at=T0 + timedelta(minutes=5))
    ledger.attest(
        "red-probe", AttestationKind.RELEASED, {"version": "1.0.0", "sha": sha, "digest": digest},
        issuer="op", idempotency_key="released:1.0.0",
    )
    flow = Attester(ledger=ledger, project="red-probe", target="private-compose", issuer="op",
                    redact=lambda text: text)
    flow.attest(AttestationKind.DEPLOYED, {"digest": digest, "version": "1.0.0"})
    flow.attest(AttestationKind.ROLLED_BACK, {"from_digest": digest, "to_digest": digest, "drill": True})
    flow.attest(AttestationKind.RESTORED, {"recovery_seconds": 8, "drill": True})
    flow.attest(AttestationKind.DEPLOYED, {"digest": digest, "version": "1.0.0", "mode": "drill"})
    ledger.accept("red-probe", rationale="every criterion holds", issuer="red")
    assert flow.unattested == [] and flow.failures == []
    assert ledger.pending() == [] and not list(tmp_path.rglob("*.json"))
    assert len(brain.attestations) == 6
```

- [ ] **Step 2: run it.** Run
  `env -u VIRTUAL_ENV uv run pytest tests/test_ledger_brain.py -k whole_delivery -q`. Expected:
  PASS. It pins Tasks A1–A5 together. If it fails, fix the task it names before going on.
- [ ] **Step 3: write the documents.**
  - **ADR-0002.** Append this section:

```markdown
## Amendment (2026-09-25): under `ledger: brain`, the mirror is a spool of pending attestations

Spec `docs/specs/2026-09-25-spool-replaces-committed-mirrors.md`, ticket `53e7a7fe`.

- **Mirror first, in the spool.** `attest()` still writes the receipt before calling brain, into
  `$RAIL_SPOOL_DIR/<project>` (default `~/.local/state/red-rail/spool`): one spool per host and
  project, never in a repository. The receipt leaves once brain has recorded its payload; a
  refusal leaves it there for `rail attest --from` or `rail ledger replay`. `contract_set`,
  `bind` and `accept` write no file: their mirror was a copy made after brain's answer.
- **The rejected alternative still stands for the file ledger.** "A file ledger outside the
  repository" was rejected as not shared, lost with the machine and invisible in review. The
  spool is not a ledger: it holds only what brain has not recorded yet and empties itself.
- **What is given up.** The committed mirror was an off-host copy of every fact, visible in
  review. Review could not judge it, and it cost a receipts pull request per milestone (23 % of
  merged pull requests). Brain now holds the only copy of the facts recorded after the switch;
  their protection is brain's backup. Receipts committed before stay, and `hygiene.mirrors` still
  matches them against brain. "Same files, only the authority changes" no longer holds in brain
  mode.
- **The file ledger is unchanged**: receipts are committed, and a pull request that only adds
  receipts gets a mechanical verdict instead of a judge.
```

  - **CLAUDE.md** (four edits):
    1. In the `src/rail/ledger/` bullet, replace "the receipts are mirrors written BEFORE the
       call; a refusal raises `Unattested` and `rail attest` exits 2 with the replay command;"
       with "an attestation waits BEFORE the call in the host's spool (`ledger/spool.py`,
       `$RAIL_SPOOL_DIR/<project>`, never in a repository) and leaves it once brain has recorded
       it; a refusal raises `Unattested`, `rail attest` exits 2, and `rail attest --from` or
       `rail ledger replay` sends it again;".
    2. In the gates bullet, replace "`hygiene.mirrors` reports a receipt whose digest is absent
       from the shared ledger (drift)" with "`hygiene.mirrors` fails while the spool holds an
       attestation, and reports a committed receipt whose digest is absent from the shared ledger
       (drift)".
    3. In the commands list, `` `ledger` `` becomes `` `ledger` (`list`, `replay`) ``, and the
       structure line of `docs/receipts/` gains "(under `ledger: brain`: history only)".
    4. The boundary rules sentence ends: "red-rail stores no durable fact outside the ledger —
       its spool holds only attestations brain has not recorded yet."
    5. The rail-change rule of ticket `a3cb8271` (T6). Two bullets go at the end of "What this
       repository adds" in § "How we work":

```markdown
- A change to the rail names what pulls it, with the effect it expects: the product delivery it
  unblocks (a ticket), or the operator load it lowers among the classes the process induces
  (ticket `c5231125`: method choice, review loop, rail or reviewer defect, paperwork,
  permission for a gesture whose gates are all green, plan approval). A red-rail spec says
  which in a one-line `Motivation:` under its title.
- No gate checks that rule. Once `rail metrics` measures the induced load, a change that raised
  it is revisited: the harness grows only when that pays back in autonomy.
```

  - **AGENTS.md** (three edits; the third is T6's):
    1. In the invariant row "brain never learns a new gate; red-rail stores no durable fact
       outside the ledger", the "Why" cell gains: "The spool (`$RAIL_SPOOL_DIR/<project>`) holds
       only attestations brain has not recorded yet."
    2. The `docs/receipts/` bullet gains: "Under `ledger: brain` the rail writes nothing there:
       a pending attestation waits in the host's spool until `rail ledger replay`."
    3. After the "Brainstorm → spec → plan → implement" bullet of § "Working style": "- A change
       to the rail names what pulls it: the product delivery it unblocks (a ticket), or the
       process-induced operator load it lowers, with the expected effect. A spec states which in
       a one-line `Motivation:` under its title. The full rule is in CLAUDE.md, § "How we
       work"."
  - **`skills/rail-attest/SKILL.md`:**
    - Step 2: "With `ledger: file` the receipt lands in `docs/receipts/`: commit it in a receipts
      pull request. With `ledger: brain` nothing lands in the repository."
    - Step 3: "…replay it with `rail attest <kind> --from <the file the error printed>`, or every
      waiting one with `rail ledger replay` — safe, the key makes it idempotent."
  - **`skills/rail-release/SKILL.md`:**
    - The description ends: "`released` attestation — then, under `ledger: file` only, the
      receipts PR."
    - Step 3's exit-2 line: "replay with `rail attest released --from <file printed>` or
      `rail ledger replay`."
    - Step 4: "Under `ledger: file`, commit the receipt in a dedicated receipts PR (decision
      c8b0ea45), never with code. Under `ledger: brain` there is nothing to commit."
  - **`skills/rail-deploy/SKILL.md`:** the exit-2 sentence ends "run every `rail attest … --from`
    line printed, or `rail ledger replay`."
- [ ] **Step 4: verify the whole PR.**
  - Run `make ci > /tmp/ci-a.log 2>&1; echo "exit=$?"`. Expected: `exit=0`, and the pytest
    summary line reports no failure.
  - Run `env -u VIRTUAL_ENV uv run rail check`. Expected: exit code 0.
  - Measure the size: `git diff --numstat origin/main...HEAD`. Expected: below 3,000 changed
    lines.
- [ ] **Step 5: commit and open the PR.** Use `/git-commit` with `📝 docs: the spool replaces the
  committed mirror under ledger brain (ADR-0002 amendment)`, then open PR A.

---

## PR B — the mechanical verdict

### Task B1: `rail.reviewer.records`

**Files:**
- Create: `src/rail/reviewer/records.py`
- Modify: `src/rail/reviewer/verdict.py`: add `"mechanical"` to `RoundLabel` and to
  `ReviewVerdict.mode`, and `"records"` to `Artifact`.
- Modify: `src/rail/reviewer/rounds.py` (`artifact_of`)
- Modify: `tests/helpers.py`, which gains `added_receipt_diff`.
- Test: `tests/test_reviewer_records.py`, `tests/test_reviewer_rounds.py`

**Interfaces:**
- Produces: `mechanical_verdict(diff: str, *, project: str) -> ReviewVerdict`.
- Produces: `artifact_of(...)`, which returns `"records"` when every path is a record.
- Produces: `tests.helpers.added_receipt_diff(path: Path, name: str | None = None) -> str`.

- [ ] **Step 1: write the failing tests.** Add the helper to `tests/helpers.py`:

```python
def added_receipt_diff(path: Path, name: str | None = None) -> str:
    """The pull-request diff that adds the receipt at `path` under docs/receipts/."""
    lines = path.read_text().splitlines()
    target = f"docs/receipts/{name or path.name}"
    body = "\n".join("+" + line for line in lines)
    return (
        f"diff --git a/{target} b/{target}\nnew file mode 100644\nindex 0000000..1111111\n"
        f"--- /dev/null\n+++ b/{target}\n@@ -0,0 +1,{len(lines)} @@\n{body}\n"
    )
```

  Create `tests/test_reviewer_records.py`:

```python
from pathlib import Path

from rail.ledger import AttestationKind
from rail.ledger.file import FileLedger
from rail.reviewer.records import mechanical_verdict
from tests.helpers import added_receipt_diff

DELETED = (
    "diff --git a/docs/receipts/old.json b/docs/receipts/old.json\ndeleted file mode 100644\n"
    "index 1111111..0000000\n--- a/docs/receipts/old.json\n+++ /dev/null\n@@ -1 +0,0 @@\n-{}\n"
)


def _receipt(root: Path, *, project: str = "red-alpha", key: str = "d1", sha: str = "a") -> Path:
    ledger = FileLedger(root)
    record = ledger.attest(project, AttestationKind.DEPLOYED, {"sha": sha * 40}, issuer="op", idempotency_key=key)
    return ledger.path_of(record)


def test_well_formed_receipts_are_approved(tmp_path: Path) -> None:
    verdict = mechanical_verdict(added_receipt_diff(_receipt(tmp_path)), project="red-alpha")
    assert verdict.verdict == "approve" and verdict.findings == []
    assert (verdict.mode, verdict.round, verdict.artifact, verdict.providers) == (
        "mechanical", "mechanical", "records", ()
    )


def test_a_tampered_receipt_is_refused(tmp_path: Path) -> None:
    path = _receipt(tmp_path)
    path.write_text(path.read_text().replace("a" * 40, "b" * 40))
    verdict = mechanical_verdict(added_receipt_diff(path), project="red-alpha")
    assert verdict.verdict == "request_changes"
    assert [f.title for f in verdict.findings] == ["digest does not match the content"]


def test_a_deleted_receipt_is_refused_next_to_a_valid_one(tmp_path: Path) -> None:
    verdict = mechanical_verdict(added_receipt_diff(_receipt(tmp_path)) + DELETED, project="red-alpha")
    assert verdict.verdict == "request_changes"
    assert [(f.file, f.title) for f in verdict.findings] == [
        ("docs/receipts/old.json", "an existing receipt is deleted")
    ]


def test_another_project_a_wrong_name_and_a_reused_key_are_refused(tmp_path: Path) -> None:
    foreign = added_receipt_diff(_receipt(tmp_path / "f", project="red-beta"))
    misnamed = added_receipt_diff(_receipt(tmp_path / "m", key="d2"), name="x.json")
    twice = added_receipt_diff(_receipt(tmp_path / "one", key="k")) + added_receipt_diff(
        _receipt(tmp_path / "two", key="k", sha="c")
    )
    titles = [f.title for f in mechanical_verdict(foreign + misnamed + twice, project="red-alpha").findings]
    assert titles == ["receipt of another project", "misnamed receipt", "idempotency key used twice"]
```

  In `tests/test_reviewer_rounds.py`, add `(["docs/receipts/r.json"], "records")` to the
  `test_artifact_of` cases.

- [ ] **Step 2: run them.** Run `env -u VIRTUAL_ENV uv run pytest tests/test_reviewer_records.py tests/test_reviewer_rounds.py -q`.
  Expected: FAIL. There is an `ImportError` for `rail.reviewer.records`, and `artifact_of`
  returns `code`.
- [ ] **Step 3: implement.** In `rounds.artifact_of`, before the spec/plan test:
  `paths = list(paths)`, then `if paths and not own: return "records"`. Its docstring gains
  "records when every file is a record (spec 2026-09-25-spool-replaces-committed-mirrors, D5)".
  Create `src/rail/reviewer/records.py`:

```python
"""A records-only pull request, judged on its form with no judge (spec
2026-09-25-spool-replaces-committed-mirrors, decision 5): every file is an added receipt that
loads, verifies, is named after its digest, names this project, and uses a key no other added
receipt uses. A collision with a receipt already on the base branch is `hygiene.receipts`' in CI."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from rail.ledger import Record
from rail.ledger.file import receipt_filename
from rail.reviewer.verdict import Finding, ReviewVerdict

_FILE = re.compile(r"^diff --git a/\S+ b/(?P<path>\S+)$", re.MULTILINE)
_MAX_FINDINGS = 100  # ReviewVerdict.findings' own bound


def _patches(diff: str) -> list[tuple[str, str]]:
    starts = list(_FILE.finditer(diff))
    ends = [m.start() for m in starts[1:]] + [len(diff)]
    return [(m.group("path"), diff[m.start() : end]) for m, end in zip(starts, ends, strict=True)]


def _added_content(patch: str) -> str:
    _, _, hunks = patch.partition("\n@@")
    lines = hunks.split("\n")[1:]  # the rest of the first hunk header goes
    return "\n".join(line[1:] for line in lines if line.startswith("+"))


def _change(patch: str) -> str:
    if "\ndeleted file mode " in patch:
        return "deleted"
    return "renamed" if "\nrename from " in patch else "modified"


def mechanical_verdict(diff: str, *, project: str) -> ReviewVerdict:
    findings: list[Finding] = []
    seen: dict[tuple[str, str], str] = {}

    def refuse(path: str, title: str, evidence: str) -> None:
        findings.append(Finding(severity="blocking", file=path, title=title, evidence=evidence[:2000]))

    patches = _patches(diff)
    for path, patch in patches:
        if "\nnew file mode " not in patch:
            refuse(path, f"an existing receipt is {_change(patch)}",
                   "the ledger is append-only: a pull request may only add receipts")
            continue
        try:  # a JSON or a validation error is a ValueError
            record = Record.model_validate(json.loads(_added_content(patch)))
        except ValueError as exc:
            refuse(path, "not a receipt", str(exc))
            continue
        if not record.verify():
            refuse(path, "digest does not match the content", f"it claims {record.digest}")
            continue
        expected = receipt_filename(record)
        if PurePosixPath(path).name != expected:
            refuse(path, "misnamed receipt", f"its content names it {expected}")
        if record.project != project:
            refuse(path, "receipt of another project", f"{record.project!r}, not {project!r}")
        key = (record.project, record.idempotency_key)
        if key in seen:
            refuse(path, "idempotency key used twice", f"{record.idempotency_key!r}, also in {seen[key]}")
        seen.setdefault(key, path)
    shown = findings[:_MAX_FINDINGS]
    summary = (
        f"mechanical: {len(patches)} receipt(s) added, all well formed"
        if not findings
        else f"mechanical: {len(findings)} problem(s) in the receipts"
        + (f", the first {_MAX_FINDINGS} listed" if len(findings) > _MAX_FINDINGS else "")
    )
    return ReviewVerdict(
        verdict="request_changes" if findings else "approve",
        summary=summary,
        findings=shown,
        mode="mechanical",
        providers=(),
        round="mechanical",
        artifact="records",
    )
```

- [ ] **Step 4: run the suites again.** Run
  `env -u VIRTUAL_ENV uv run pytest tests/test_reviewer_records.py tests/test_reviewer_rounds.py tests/test_reviewer_verdict.py -q`.
  Expected: all pass.
- [ ] **Step 5: commit.** Use `/git-commit` with `✨ feat(reviewer): judge a records-only pull request mechanically`.

### Task B2: the reviewer takes the mechanical path, and the loop ignores it

**Files:**
- Modify: `src/rail/reviewer/service.py` (`_review_started`), `src/rail/reviewer/rounds.py`
  (`loop_state`)
- Test: `tests/test_reviewer_service.py`, `tests/test_reviewer_rounds.py`

- [ ] **Step 1: write the failing tests.** Add to `tests/test_reviewer_service.py`:

```python
def _no_judge(*args, **kwargs):
    raise AssertionError("no judge runs on a records-only pull request")


def test_a_records_only_pull_request_is_judged_mechanically(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    other = FileLedger(tmp_path / "made")
    made = other.attest("red-alpha", AttestationKind.DEPLOYED, {"sha": "e" * 40}, issuer="op", idempotency_key="d9")
    github = FakeGitHub(diff_text=added_receipt_diff(other.path_of(made)))
    outcome = review_pull(PR, github=github, policy=default_policy(), ledger=ledger,
                          project="red-alpha", repo_path=repo, run_judge=_no_judge, root=tmp_path)
    assert outcome.verdict.verdict == "approve" and outcome.verdict.mode == "mechanical"
    assert ("complete", 99, "success", "approve") in github.calls
    assert ("review", 7, "APPROVE") in github.calls
    record = ledger.list("red-alpha", attestation=AttestationKind.REVIEW_VERDICT)[-1]
    assert record.issuer == "red-rail-reviewer" and record.data["independent"] is True
    assert (record.data["mode"], record.data["round"]) == ("mechanical", "mechanical")


def test_a_tampered_receipt_gets_request_changes_and_still_no_judge(tmp_path: Path) -> None:
    repo, ledger = _repo(tmp_path)
    other = FileLedger(tmp_path / "made")
    made = other.attest("red-alpha", AttestationKind.DEPLOYED, {"sha": "e" * 40}, issuer="op", idempotency_key="d9")
    path = other.path_of(made)
    path.write_text(path.read_text().replace("e" * 40, "f" * 40))
    github = FakeGitHub(diff_text=added_receipt_diff(path))
    outcome = review_pull(PR, github=github, policy=default_policy(), ledger=ledger,
                          project="red-alpha", repo_path=repo, run_judge=_no_judge, root=tmp_path)
    assert outcome.verdict.verdict == "request_changes"
    assert ("review", 7, "REQUEST_CHANGES") in github.calls
```

  Add to `tests/test_reviewer_rounds.py`:

```python
def test_a_mechanical_verdict_is_not_part_of_the_loop() -> None:
    mechanical = _record(
        "review_verdict",
        {"verdict": "request_changes", "round": "mechanical", "mode": "mechanical",
         "artifact": "records", "findings": [_finding(1)]},
        minutes=1,
    )
    state = rounds.loop_state([mechanical], [])
    assert state.judged == 0 and state.findings == () and state.last_judged is None
```

- [ ] **Step 2: run them.** Run `env -u VIRTUAL_ENV uv run pytest tests/test_reviewer_service.py tests/test_reviewer_rounds.py -q`.
  Expected: FAIL. The judge is called (`AssertionError`), and `loop_state` counts the mechanical
  finding as open.
- [ ] **Step 3: implement.**
  - **`_review_started`:** fetch the diff first, and branch before any loop logic (import
    `records` from `rail.reviewer`). Delete the later lines that assigned `whole` and `artifact`.

```python
    whole = github.diff(pr.repository, pr.number)
    artifact = rounds.artifact_of(_files(whole), policy.records_globs)
    if artifact == "records":
        # spec 2026-09-25-spool-replaces-committed-mirrors, D5: form only, no judge, no round
        verdict = records.mechanical_verdict(whole, project=project)
        return _publish(
            pr, check, verdict, verdict.verdict, github=github, policy=policy, ledger=ledger,
            project=project, repo_path=repo_path, failures=[],
        )
```

  - **`rounds.loop_state`:** its first line is
    `verdicts = [v for v in verdicts if v.data.get("round") != "mechanical"]`, with the comment
    "a mechanical verdict judged the form of receipts, never a round (D5)".
- [ ] **Step 4: verify the whole PR.**
  - Run `make ci > /tmp/ci-b.log 2>&1; echo "exit=$?"`. Expected: `exit=0`, and the summary line
    reports no failure.
  - Measure the size: `git diff --numstat origin/main...HEAD`. Expected: below 3,000 changed
    lines.
- [ ] **Step 5: commit and open the PR.** Use `/git-commit` with `✨ feat(reviewer): a records-only pull request
  gets a mechanical verdict, outside the review loop`, then open PR B.

---

## After the merges

These steps run on the host, in this order. Each one is verified before the next.

1. **After PR A:**
   - Reinstall the global rail (snippet `efdec356`). Until then, the reviewer writes mirrors
     into the checkout.
   - Log the decision that supersedes `c8b0ea45` for brain mode.
   - Move red-rail's four untracked receipts from the main checkout's `docs/receipts/` into
     `~/.local/state/red-rail/spool/red-rail/`, then run `rail ledger replay --repo <main checkout>`.
     Expected: exit code 0, each line `already recorded` or `recorded`, and `git status` shows no
     untracked receipt (spec success criterion 6).
   - Send an FYI to `red`, covering the other brain-ledger projects and the two migration
     commands.
   - Send an FYI to red-backup and brain-v42: brain is now the only copy.
2. **After PR B:** reinstall the global rail again, because the reviewer's code changed. Expected:
   the next records-only pull request of a file-ledger project shows a `mechanical` verdict.
