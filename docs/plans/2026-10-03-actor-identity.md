# Actor identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every rail gesture records its real actor (`operator`, `agent:<harness>[:<session>]` or
`service:<name>`), resolved once per command and never defaulted to the operator.

**Architecture:** A pure module `rail.actor` resolves the actor from the environment and the
terminal; its marker table is policy data in `rail.policy`. A small CLI helper
(`commands/_actor.py`) resolves it before any side effect and replaces `--issuer` by a refusal
that names `RAIL_ACTOR`. The deploy flows lose their `"operator"` default. `rail reviewer rule`
trusts the resolution instead of a terminal. Replay already keeps the receipt's issuer.

**Tech Stack:** Python 3.12, Click, pytest, ruff.

**Spec:** `docs/specs/2026-10-03-actor-identity.md`. Read it whole first. This plan cites its
decisions by section number (§1–§6) and its success criteria by number (C1–C6).

## Global Constraints

- Labels: 1 to 64 characters from `[A-Za-z0-9._:-]`; `<harness>` and `<name>` are lowercase
  `[a-z0-9-]+`; `<session>` is `[A-Za-z0-9._-]+` (spec §1).
- Resolution order: `RAIL_ACTOR` > agent marker > terminal (`operator`) > refusal, exit 2
  (spec §2). `RAIL_ACTOR=operator` with a marker is refused.
- Resolution happens before any ledger read, network call, ssh or spool write (spec §3).
- `--issuer` is removed everywhere and fails with a usage error naming `RAIL_ACTOR` (spec §3).
- `deploy/flow.py`: `issuer` is a required parameter, no default (spec §3).
- The reviewer identity `red-rail-reviewer` is not renamed (spec §1).
- Public repository: no IP, no host or infra name in code, tests or docs.
- English everywhere; one commit per task, `/git-commit` format, below 3,000 changed lines.
- Tests: `unset VIRTUAL_ENV && uv run pytest -q`; lint: `uv run ruff check src/ tests/`.

## Divergences from the spec found while reading the code

1. **`rail new` writes a ledger record with `issuer="rail new"`** (`scaffold.py`), a label with a
   space, outside the §1 grammar, and prints a copy-pasteable `rail contract set … --issuer
   "rail new"` command. Decision for this plan: the birth contract is issued by
   `service:rail-new`, and the printed command drops `--issuer` (the operator's own shell
   resolves the actor when they run it). Task 6.
2. **C6 (`grep '"operator"'`) also matches `"reason": "operator"`** in the rollback data of
   `deploy/flow.py`, a recorded reason and not an issuer. C6 is read as "no issuer default":
   Task 7 pins it as a test on `issuer` defaults and `--issuer` only.
3. **`rail ledger replay`** is not in §3's command list; it resends spooled records with their own
   issuer and resolves no actor (spec §4). Unchanged.

## Review Focus

- `RAIL_ACTOR=""` (a profile leftover) is read as unset, not as an invalid label (Task 1).
- `AI_AGENT` with free text such as `Some Tool/2.1` becomes a valid label, it does not crash
  (Task 1).
- A `RAIL_ACTOR` over 64 characters is refused, never silently truncated; only a session id read
  from a marker is truncated (Task 1).
- `rail deploy --plan` attests nothing and must work with no terminal and no marker (Task 4).
- An agent running `rail reviewer rule` behind a pseudo-terminal is refused, with nothing written
  (Task 5).

## File Structure

- Create `src/rail/actor.py`: `Actor`, `ActorKind`, `ActorRefused`, `resolve_actor`,
  `current_actor`. Pure; reads nothing but its arguments except in `current_actor`.
- Modify `src/rail/policy.py`: `AGENT_MARKERS`, `AGENT_NAME_VARIABLE`, `ACTOR_VARIABLE`.
- Create `src/rail/commands/_actor.py`: `no_issuer_option`, `resolve_or_exit`.
- Modify `src/rail/commands/{bind,accept,attest,contract,release,deploy,drill}.py`,
  `src/rail/commands/reviewer.py`, `src/rail/deploy/flow.py`, `src/rail/scaffold.py`.
- Modify `tests/conftest.py` (actor isolation) and the tests listed per task.
- Create `tests/test_actor.py`, `tests/test_cli_actor.py`.

### Task 1: `rail.actor`, the pure resolution

**Files:**
- Create: `src/rail/actor.py`
- Modify: `src/rail/policy.py`
- Test: `tests/test_actor.py`

**Interfaces:**
- Produces: `ActorKind` (StrEnum `operator|agent|service`), `Actor(kind, label)` frozen dataclass,
  `ActorRefused(ValueError)`, `resolve_actor(environ: Mapping[str, str], stdin_is_tty: bool) ->
  Actor`, `current_actor() -> Actor`; policy `ACTOR_VARIABLE = "RAIL_ACTOR"`,
  `AGENT_NAME_VARIABLE = "AI_AGENT"`, `AGENT_MARKERS: tuple[HarnessMarker, ...]`.

- [ ] **Step 1: Measure the markers on this host.** Run `env | grep -iE 'CLAUDE|AI_AGENT|CODEX|OPENCODE|AGY'`
  in this session and note what a Claude Code tool call exports. For Codex, OpenCode and agy, run
  `env` through each harness's headless provider only if one is already installed and the run
  needs no credential beyond the host's own; otherwise leave them out of the table and record in
  the commit message that they are unmeasured (spec §2: the table grows when measured).
  Expected: `CLAUDECODE=1`, `CLAUDE_CODE_SESSION_ID=<uuid>` and `AI_AGENT` are present.

- [ ] **Step 2: Write the failing table test** in `tests/test_actor.py`:

```python
import pytest

from rail.actor import Actor, ActorKind, ActorRefused, resolve_actor

SESSION = "e7fb11aa-215b-5ca3-82df-65e4c7a9650d"


def resolve(env: dict[str, str], tty: bool = False) -> Actor:
    return resolve_actor(env, tty)


def test_claude_code_marker_gives_an_agent_with_its_session() -> None:
    actor = resolve({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": SESSION})
    assert actor == Actor(ActorKind.AGENT, f"agent:claude-code:{SESSION}")


def test_a_marker_without_session_gives_the_bare_harness() -> None:
    assert resolve({"CLAUDECODE": "1"}).label == "agent:claude-code"


def test_the_marker_wins_over_the_terminal() -> None:
    assert resolve({"CLAUDECODE": "1"}, tty=True).kind is ActorKind.AGENT


def test_a_terminal_without_marker_is_the_operator() -> None:
    assert resolve({}, tty=True) == Actor(ActorKind.OPERATOR, "operator")


def test_neither_marker_nor_terminal_is_refused_with_the_fix_named() -> None:
    with pytest.raises(ActorRefused, match="RAIL_ACTOR=agent:<name>"):
        resolve({})


@pytest.mark.parametrize(
    "value", ["operator", "agent:codex", "agent:codex:s-1.2_3", "service:cron-backup"]
)
def test_a_valid_declared_actor_is_taken_as_is(value: str) -> None:
    assert resolve({"RAIL_ACTOR": value}).label == value


@pytest.mark.parametrize(
    "value",
    ["root", "agent:", "agent:Codex", "service:a/b", "service:${X}", "agent:x:" + "s" * 64, "a b"],
)
def test_an_invalid_declared_actor_is_refused(value: str) -> None:
    with pytest.raises(ActorRefused):
        resolve({"RAIL_ACTOR": value})


def test_a_declared_label_of_64_characters_is_accepted_and_65_refused() -> None:
    ok = "agent:x:" + "s" * (64 - len("agent:x:"))
    assert resolve({"RAIL_ACTOR": ok}).label == ok
    with pytest.raises(ActorRefused):
        resolve({"RAIL_ACTOR": ok + "s"})


def test_operator_declared_while_an_agent_marker_is_present_is_refused() -> None:
    with pytest.raises(ActorRefused, match="inherited"):
        resolve({"RAIL_ACTOR": "operator", "CLAUDECODE": "1"}, tty=True)


def test_an_empty_declaration_is_read_as_unset() -> None:
    assert resolve({"RAIL_ACTOR": ""}, tty=True).label == "operator"


def test_an_over_long_session_id_is_truncated_and_stays_valid() -> None:
    label = resolve({"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s" * 200}).label
    assert len(label) == 64 and label.startswith("agent:claude-code:")


def test_ai_agent_alone_is_reduced_to_the_grammar() -> None:
    assert resolve({"AI_AGENT": "Some Tool/2.1"}).label == "agent:some-tool-2-1"


def test_ai_agent_of_nothing_usable_is_unknown() -> None:
    assert resolve({"AI_AGENT": "///"}).label == "agent:unknown"
```

- [ ] **Step 3: Run it to verify it fails**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_actor.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rail.actor'`.

- [ ] **Step 4: Add the policy data** at the end of the constants of `src/rail/policy.py`:

```python
ACTOR_VARIABLE = "RAIL_ACTOR"
# a harness that names itself without a table entry: `AI_AGENT=<value>` gives `agent:<value>`
AGENT_NAME_VARIABLE = "AI_AGENT"


@dataclass(frozen=True)
class HarnessMarker:
    harness: str  # lowercase [a-z0-9-]+, the <harness> of the label
    presence: tuple[str, ...]  # any of these variables, non-empty, marks the harness
    session: str | None  # the variable carrying its session id


# measured on this host (spec 2026-10-03-actor-identity §2); a harness is added once measured
AGENT_MARKERS: tuple[HarnessMarker, ...] = (
    HarnessMarker("claude-code", ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID"), "CLAUDE_CODE_SESSION_ID"),
)
```

  Add `from dataclasses import dataclass` if absent.

- [ ] **Step 5: Write `src/rail/actor.py`:**

```python
"""Who runs a rail gesture, resolved once and never defaulted (spec 2026-10-03-actor-identity).

The threat is error and honesty: a harness declares itself, the rail never records an agent as
the operator by default. A deliberately lying agent is out of scope and declared as such."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from rail.policy import ACTOR_VARIABLE, AGENT_MARKERS, AGENT_NAME_VARIABLE

MAX_LABEL = 64
_DECLARED = re.compile(
    r"operator|agent:[a-z0-9-]+(?::[A-Za-z0-9._-]+)?|service:[a-z0-9-]+"
)


class ActorKind(StrEnum):
    OPERATOR = "operator"
    AGENT = "agent"
    SERVICE = "service"


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    label: str


class ActorRefused(ValueError):
    """The rail cannot tell who runs the gesture: nothing has been written."""


def _kind(label: str) -> ActorKind:
    return ActorKind(label.split(":", 1)[0])


def _marked(environ: Mapping[str, str]) -> Actor | None:
    for marker in AGENT_MARKERS:
        if any(environ.get(name) for name in marker.presence):
            session = environ.get(marker.session or "", "")
            if not session:
                return Actor(ActorKind.AGENT, f"agent:{marker.harness}")
            prefix = f"agent:{marker.harness}:"
            safe = re.sub(r"[^A-Za-z0-9._-]", "-", session)
            return Actor(ActorKind.AGENT, (prefix + safe)[:MAX_LABEL])
    named = environ.get(AGENT_NAME_VARIABLE)
    if named:
        name = re.sub(r"[^a-z0-9-]+", "-", named.lower()).strip("-") or "unknown"
        return Actor(ActorKind.AGENT, f"agent:{name}"[:MAX_LABEL])
    return None


def resolve_actor(environ: Mapping[str, str], stdin_is_tty: bool) -> Actor:
    marked = _marked(environ)
    declared = environ.get(ACTOR_VARIABLE, "")
    if declared:
        if len(declared) > MAX_LABEL or not _DECLARED.fullmatch(declared):
            raise ActorRefused(
                f"{ACTOR_VARIABLE}={declared!r} is not an actor label: "
                "operator, agent:<harness>[:<session>] or service:<name>, 64 characters at most"
            )
        if declared == "operator" and marked is not None:
            raise ActorRefused(
                f"{ACTOR_VARIABLE}=operator while {marked.label} is running: an operator "
                "variable inherited by an agent. Unset it for the agent, or name the agent"
            )
        return Actor(_kind(declared), declared)
    if marked is not None:
        return marked
    if stdin_is_tty:
        return Actor(ActorKind.OPERATOR, "operator")
    raise ActorRefused(
        "cannot tell who runs this gesture (no agent marker, no terminal): set "
        f"{ACTOR_VARIABLE}=agent:<name> or {ACTOR_VARIABLE}=service:<name>"
    )


def current_actor() -> Actor:
    return resolve_actor(os.environ, sys.stdin.isatty())
```

- [ ] **Step 6: Run the tests and the linter**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_actor.py -q && uv run ruff check src/ tests/`
Expected: all pass, ruff clean (`ruff format` the two files if it asks).

- [ ] **Step 7: Commit** with `/git-commit`: `✨ feat(actor): resolve who runs a gesture, never defaulted`.

### Task 2: The CLI helper and the tests' actor isolation

**Files:**
- Create: `src/rail/commands/_actor.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_cli_actor.py`

**Interfaces:**
- Consumes: `rail.actor.current_actor`, `ActorRefused`.
- Produces: `no_issuer_option` (a click decorator, hidden `--issuer` that raises `UsageError`),
  `resolve_or_exit() -> str` (the label; on refusal prints `error: …` to stderr, exit 2).

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_actor.py`:

```python
import click
import pytest
from click.testing import CliRunner

from rail.commands._actor import no_issuer_option, resolve_or_exit


@click.command()
@no_issuer_option
def probe() -> None:
    click.echo(resolve_or_exit())


def test_the_resolved_label_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "service:probe")
    out = CliRunner().invoke(probe, [])
    assert out.exit_code == 0 and out.output.strip() == "service:probe"


def test_a_refusal_exits_2_and_names_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAIL_ACTOR")
    out = CliRunner().invoke(probe, [])
    assert out.exit_code == 2 and "RAIL_ACTOR=agent:<name>" in out.output


def test_issuer_is_a_usage_error_naming_the_variable() -> None:
    out = CliRunner().invoke(probe, ["--issuer", "operator"])
    assert out.exit_code == 2 and "RAIL_ACTOR" in out.output
```

- [ ] **Step 2: Add the isolation fixture** to `tests/conftest.py` (every existing test then
  resolves to the operator, whatever harness runs the suite):

```python
@pytest.fixture(autouse=True)
def _operator_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test never inherits the harness running the suite: no agent marker, and the actor is
    the operator unless the test declares another one with `RAIL_ACTOR`."""
    from rail.policy import AGENT_MARKERS, AGENT_NAME_VARIABLE

    for marker in AGENT_MARKERS:
        for name in (*marker.presence, marker.session or ""):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(AGENT_NAME_VARIABLE, raising=False)
    monkeypatch.setenv("RAIL_ACTOR", "operator")
```

- [ ] **Step 3: Run to verify failure**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_cli_actor.py -q`
Expected: FAIL with `ModuleNotFoundError: rail.commands._actor`.

- [ ] **Step 4: Write `src/rail/commands/_actor.py`:**

```python
"""The actor of a gesture command, resolved before any side effect (spec 2026-10-03 §3)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click

from rail.actor import ActorRefused, current_actor
from rail.policy import ACTOR_VARIABLE


def _issuer_is_gone(ctx: click.Context, param: click.Parameter, value: Any) -> None:
    if value is not None:
        raise click.UsageError(
            f"--issuer is gone: the actor is resolved from the environment; "
            f"set {ACTOR_VARIABLE}=agent:<name> or {ACTOR_VARIABLE}=service:<name> to name it"
        )


def no_issuer_option(func: Callable[..., Any]) -> Callable[..., Any]:
    """`--issuer` stays declared, hidden, so that using it fails loudly instead of being
    taken for an unknown option or silently ignored."""
    return click.option(
        "--issuer", hidden=True, is_eager=True, expose_value=False, callback=_issuer_is_gone
    )(func)


def resolve_or_exit() -> str:
    """The actor's label, or exit 2 with the fix named. Call it first in a gesture."""
    try:
        return current_actor().label
    except ActorRefused as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(2) from exc
```

- [ ] **Step 5: Run the new tests, then the whole suite** (the fixture must not break anything)

Run: `unset VIRTUAL_ENV && uv run pytest -q > /tmp/actor-t2.log 2>&1; echo $?; tail -3 /tmp/actor-t2.log`
Expected: exit 0, summary line shows no failure.

- [ ] **Step 6: Commit** with `/git-commit`: `✨ feat(actor): a CLI helper that resolves the actor first`.

### Task 3: `bind`, `accept`, `attest`, `contract`

**Files:**
- Modify: `src/rail/commands/bind.py:45-65`, `accept.py:22-43`, `attest.py:74-130`,
  `contract.py:124-232`
- Test: `tests/test_cli_bind.py`, `tests/test_cli_accept.py`, `tests/test_cli_ledger.py`,
  `tests/test_cli_actor.py`

**Interfaces:**
- Consumes: `no_issuer_option`, `resolve_or_exit` from Task 2.
- Produces: each command sends `resolve_or_exit()`'s label where `issuer` went; `attest --from`
  keeps `source.issuer`.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_actor.py`. For each of the four
  commands, using the fixtures of the existing CLI tests (`conforming_tree`, `FakeBrain`,
  `BrainClient`, as `tests/test_cli_accept.py` and `tests/test_cli_bind.py` do):
  (a) with `RAIL_ACTOR=agent:claude-code:s1` the fake brain's recorded `issuer_identity` is
  `agent:claude-code:s1`; (b) with `RAIL_ACTOR` unset, no terminal and no marker, exit 2, the fake
  brain received no call and `RAIL_SPOOL_DIR` is empty; (c) `--issuer x` exits 2 naming
  `RAIL_ACTOR`; (d) `attest --from <receipt>` run with `RAIL_ACTOR=agent:claude-code:s1` keeps the
  receipt's own issuer (C3). Copy the arrangement of the neighbouring test of each command for the
  tree and the ledger; do not invent new fixtures.

- [ ] **Step 2: Run them to verify they fail**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_cli_actor.py -q`
Expected: FAIL on (a)–(c) for every command (the label is `operator`, `--issuer` is accepted).

- [ ] **Step 3: Edit the four commands.** In each: remove `@click.option("--issuer", …)` and the
  `issuer` parameter, add `@no_issuer_option` in its place, and make the first statement of the
  function `issuer = resolve_or_exit()` (before `load_rail_config`, `open_ledger` or any read).
  In `attest.py` the replay branch already passes `source.issuer`; leave it, but still resolve
  first. Import `from rail.commands._actor import no_issuer_option, resolve_or_exit`.

- [ ] **Step 4: Update the existing tests** that pass `--issuer` (`tests/test_cli_accept.py`,
  `tests/test_cli_ledger.py`) to set `monkeypatch.setenv("RAIL_ACTOR", <label>)` instead, with a
  label inside the grammar (`service:test`, `agent:test`); adjust any assertion on the old value.

- [ ] **Step 5: Run the four test files and the linter**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_cli_actor.py tests/test_cli_bind.py tests/test_cli_accept.py tests/test_cli_ledger.py tests/test_cli.py -q && uv run ruff check src/ tests/`
Expected: all pass, ruff clean.

- [ ] **Step 6: Commit** with `/git-commit`: `✨ feat(actor): bind, accept, attest and contract record the resolved actor`.

### Task 4: `release`, `deploy`, `drill` and the flows

**Files:**
- Modify: `src/rail/commands/release.py:25-64`, `deploy.py:57-106`, `drill.py:22-40`,
  `src/rail/release.py:333-340`, `src/rail/deploy/flow.py:219,328,399`
- Test: `tests/test_release.py`, `tests/test_cli_deploy.py`, `tests/test_deploy_*.py`,
  `tests/test_cli_actor.py`

**Interfaces:**
- Consumes: Task 2 helpers.
- Produces: `flow.forward/rollback/drill(…, issuer: str, …)` and `release.run(…, issuer: str)`
  with no default; `deploy --plan` resolves no actor.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_actor.py`: for `release --dry-run`
  and `deploy --plan` under `RAIL_ACTOR` unset with no terminal and no marker, both exit 0 (no
  actor needed; use the fixtures of `tests/test_release.py` and `tests/test_cli_deploy.py`); for
  `release`, `deploy`, `deploy --rollback` and `drill` with the actor unresolvable, exit 2 before
  the first ssh (assert the runner double or ssh fake received no call) and with an empty spool;
  with `RAIL_ACTOR=agent:claude-code:s1` the attested records carry that issuer; `--issuer x`
  exits 2 naming `RAIL_ACTOR`. And in `tests/test_deploy_refusals.py` add:

```python
import inspect

from rail.deploy import flow


@pytest.mark.parametrize("fn", [flow.forward, flow.rollback, flow.drill])
def test_no_flow_falls_back_to_an_issuer(fn) -> None:
    assert inspect.signature(fn).parameters["issuer"].default is inspect.Parameter.empty
```

- [ ] **Step 2: Run to verify failure**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_cli_actor.py tests/test_deploy_refusals.py -q`
Expected: FAIL (defaults still `"operator"`, `--issuer` still accepted).

- [ ] **Step 3: Edit.** `flow.py`: replace `issuer: str = "operator",` by `issuer: str,` in the
  three signatures (a parameter without default must precede none with one: move `issuer` before
  any defaulted keyword, keeping them keyword-only if they already are). `release.py` (module):
  `issuer: str` with no default. The three commands: `@no_issuer_option`, and
  `issuer = resolve_or_exit()` as first statement; for `deploy`, only on the branches that
  attest (not `--plan`); for `release`, not under `--dry-run`.

- [ ] **Step 4: Fix the callers in tests** that relied on the defaults: run the suite, and for each
  failure of the form `missing 1 required … 'issuer'` pass `issuer="operator"` explicitly.

Run: `unset VIRTUAL_ENV && uv run pytest -q > /tmp/actor-t4.log 2>&1; echo $?; tail -3 /tmp/actor-t4.log`
Expected: exit 0.

- [ ] **Step 5: Commit** with `/git-commit`: `✨ feat(actor): release, deploy and drill record the resolved actor`.

### Task 5: `rail reviewer rule`

**Files:**
- Modify: `src/rail/commands/reviewer.py:187,225-267`
- Test: `tests/test_cli_reviewer.py:261,365`, `tests/test_cli_actor.py`

**Interfaces:**
- Consumes: `current_actor`, `ActorRefused`, `ActorKind`.
- Produces: `rule` runs only for an `operator` actor and attests under the resolved label.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_actor.py`, arranged like the
  `rule` tests of `tests/test_cli_reviewer.py`: (a) an agent marker with a terminal-like stdin
  (`CLAUDECODE=1`, `RAIL_ACTOR` unset) is refused with exit 2 and the ledger, spool and ruling
  list are untouched (C4); (b) `RAIL_ACTOR=operator` with the awaiting finding writes the ruling
  with `issuer_identity == "operator"`; (c) `RAIL_ACTOR=service:cron` is refused.

- [ ] **Step 2: Run to verify failure**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_cli_actor.py -q -k rule`
Expected: FAIL on (a) and (c).

- [ ] **Step 3: Edit `reviewer.py`.** Delete `_interactive()` and the
  `if not _interactive(): raise click.UsageError(...)` check. After the `CI` check, add:

```python
    actor = resolve_or_exit()
    if not actor.startswith("operator"):
        raise click.UsageError(f"a ruling is the operator's gesture, not {actor}'s")
```

  and pass `issuer=actor` instead of `issuer="operator"` to `ledger.attest`. The typed
  confirmation (`click.prompt`) stays: the operator still types the finding id.

- [ ] **Step 4: Update the two tests** that monkeypatch `rail.commands.reviewer._interactive`:
  drop the patch; the conftest actor (`operator`) replaces the `tty=True` case, and the
  `tty=False` case becomes `RAIL_ACTOR` unset with no marker (refused, exit 2).

- [ ] **Step 5: Run the reviewer CLI tests and the linter**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_cli_reviewer.py tests/test_cli_actor.py -q && uv run ruff check src/ tests/`
Expected: all pass.

- [ ] **Step 6: Commit** with `/git-commit`: `✨ feat(actor): a ruling is the operator's, resolved and not guessed from a terminal`.

### Task 6: `rail new` and the printed birth command

**Files:**
- Modify: `src/rail/scaffold.py:257,366`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Produces: the birth contract is issued by `service:rail-new`; the command printed for an
  interrupted birth no longer contains `--issuer`.

- [ ] **Step 1: Write the failing tests** in `tests/test_scaffold.py`: the record returned by
  `record_contract` has `issuer == "service:rail-new"`, and the string built at `scaffold.py:366`
  contains neither `--issuer` nor a space-bearing label (find the test that already covers the
  interrupted-birth message and extend it).

- [ ] **Step 2: Run to verify failure**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_scaffold.py -q`
Expected: FAIL (`issuer == "rail new"`, `--issuer` present).

- [ ] **Step 3: Edit.** `issuer="service:rail-new"` at line 257; remove `"--issuer", "rail new",`
  at line 366 (keep `--reason bootstrap` and `--key`). Update any test that asserted the old text.

- [ ] **Step 4: Run scaffold, template and CLI tests**

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_scaffold.py tests/test_template_go.py tests/test_template_service.py tests/test_cli.py -q`
Expected: all pass.

- [ ] **Step 5: Commit** with `/git-commit`: `🩹 fix(scaffold): the birth contract names a valid service actor`.

### Task 7: The sweep and the last proof

**Files:**
- Modify: `AGENTS.md` (only if it mentions `--issuer`; check with grep), docs of the skills
  (`skills/*/SKILL.md` if they name `--issuer`)
- Test: `tests/test_actor_sweep.py`

- [ ] **Step 1: Write the failing sweep test** (C6, read as "no issuer default, no `--issuer`"):

```python
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "rail"
DEFAULTED = re.compile(r"issuer\s*(?::\s*str)?\s*=\s*[\"']operator[\"']")


def test_no_command_or_flow_defaults_an_issuer_to_the_operator() -> None:
    hits = [
        f"{p.relative_to(SRC)}:{n}"
        for folder in ("commands", "deploy")
        for p in (SRC / folder).rglob("*.py")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if DEFAULTED.search(line)
    ]
    assert hits == []


def test_issuer_survives_only_as_the_hidden_refusal() -> None:
    declared = [
        p.name
        for p in (SRC / "commands").glob("*.py")
        if '"--issuer"' in p.read_text()
    ]
    assert declared == ["_actor.py"]
```

- [ ] **Step 2: Run it** — Expected: PASS if Tasks 3-5 were complete; a FAIL names the file left
  behind, fix it there.

Run: `unset VIRTUAL_ENV && uv run pytest tests/test_actor_sweep.py -q`

- [ ] **Step 3: Grep the docs for a leftover `--issuer`**

Run: `grep -rn -e '--issuer' AGENTS.md CLAUDE.md skills template README.md 2>/dev/null`
Expected: no output; otherwise replace each mention by the `RAIL_ACTOR` instruction. Files under
`docs/plans/` and `docs/specs/` of earlier dates are history, never edited.

- [ ] **Step 4: Final proof.** Full CI, redirected to a file (the exit code first, then the
  summaries):

Run: `unset VIRTUAL_ENV && make ci > /tmp/actor-ci.log 2>&1; echo $?; grep -E "passed|failed|PASS|FAIL" /tmp/actor-ci.log | tail -8`
Expected: exit 0, `N passed`, `rail check` all PASS.

- [ ] **Step 5: Commit** with `/git-commit`: `✅ test(actor): no gesture defaults its issuer to the operator`.

## After the plan

- `red-review` on the whole diff before the PR (spec method). An adversarial opus review is
  mandatory: this change adds a path that refuses, and one that attributes a ruling.
- Reinstall the global rail after the merge (snippet `efdec356`, to the letter).
- The cut-over of spec §6 is the `released` attestation of the next rail release: operator.
