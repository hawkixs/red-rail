# Every rail gesture records its real actor: the operator, an agent or a service, resolved once and never defaulted

Status: proposed — 2026-10-03

Motivation: the foundation of the autonomy retro. Measuring the operator's load (T1, `c5231125`),
enforcing autonomy levels (T4, `8f0dce93`) and attesting automatic rulings (T7, `510d475c`) all
need to know who ran a gesture, and the rail cannot tell today. It lowers no operator load by
itself; it makes every later change measurable against that load (decision `8b6cb79e`: measure
first).

Operator decisions behind this spec, taken on 2026-10-03 with the operator driving the session
directly: the `spec` method and the foundation-first order of the autonomy batch (decision
`55fbaafd`); the threat model is error and honesty, not a deliberately lying agent; approach A,
detection then refusal of the unknown; and the three design sections below, approved one by one.
Merge rule `39f7ea9f` does not change.

## Problem

- **Every gesture is the operator's by default.** `release`, `deploy`, `drill`, `bind`,
  `attest`, `accept` and `contract` take `--issuer` with the default `operator`
  (`src/rail/commands/*.py`), and the deploy flows default `issuer="operator"`
  (`src/rail/deploy/flow.py`). An agent that runs `rail release` is recorded as the operator.
  The ledger cannot count human gestures, the third success measure of design decision 6 of
  `2026-09-14-red-rail-design.md`.
- **`rail reviewer rule` trusts a terminal.** It is the operator's gesture because stdin is a
  TTY (`commands/reviewer.py`). An agent obtains a pseudo-terminal in one line.
- **Agents and the operator are the same Unix user.** Every session, every judge and the
  reviewer service run as the same user, read the same `~/.config/red-rail/` and inherit the
  operator's shell profile. No file or secret on the host can separate them.
- **Harnesses already say who they are.** A Claude Code session exports `AI_AGENT`,
  `CLAUDECODE=1` and `CLAUDE_CODE_SESSION_ID` (measured 2026-10-03), and its tool calls run
  without a terminal on stdin.
- **Brain stores the label as declared.** `X-Brain-Agent` becomes `issuer_identity`;
  brain-v42's `provenance.normalize_agent` truncates it to 64 characters, collapses a value
  holding `${` and keeps the basename of a value starting with `/`. A label rewritten by brain no
  longer matches the digest of the spooled receipt.

## Decisions

### 1. Three kinds of actor, one label each

| Kind | Label | Example |
|---|---|---|
| operator | `operator` | `operator` |
| agent | `agent:<harness>[:<session>]` | `agent:claude-code:e7fb11aa-215b-5ca3-82df-65e4c7a9650d` |
| service | `service:<name>`, or a configured service identity | `red-rail-reviewer` |

The label is what the rail sends as `issuer`. Its grammar is closed: 1 to 64 characters from
`[A-Za-z0-9._:-]`, so it never contains `/` or `$` and brain stores it unchanged. `<harness>` and
`<name>` are lowercase `[a-z0-9-]+`; `<session>` is `[A-Za-z0-9._-]+`. A session id that would
push the label past 64 characters is truncated by the rail itself, deterministically, before the
label is used anywhere. A label outside the grammar is refused before any side effect.

The reviewer keeps its existing identity `red-rail-reviewer` (`reviewer/service.py`): the
`review.verdict` gate trusts it (`review.reviewer_identity`), and it is not renamed. It is
classified as a service. The reviewer runs no gesture command, so nothing else changes on its
side.

### 2. Resolution: once per command, a pure function

`src/rail/actor.py` exposes `resolve_actor(environ, stdin_is_tty) -> Actor`, pure, and
`current_actor()`, which reads `os.environ` and `sys.stdin.isatty()`. The order:

1. **`RAIL_ACTOR` is set.** It is validated against the grammar. `RAIL_ACTOR=operator` while
   an agent marker is present is refused: it is the shape of an operator variable inherited by
   an agent from the shell profile.
2. **An agent marker is present.** The result is `agent:<harness>:<session>`, or
   `agent:<harness>` when the harness exports no session id.
3. **stdin is a terminal.** The result is `operator`.
4. **Otherwise** the command is refused, exit 2, before any side effect: the rail cannot tell
   who runs the gesture, and the message names `RAIL_ACTOR=agent:<name>` and
   `RAIL_ACTOR=service:<name>`.

The marker table is policy data in `src/rail/policy.py`: per harness, the variables whose
presence marks it and the variable that carries its session id. Claude Code is verified
(`CLAUDECODE` or `CLAUDE_CODE_SESSION_ID`, session `CLAUDE_CODE_SESSION_ID`). The plan measures
the markers of Codex, OpenCode and agy on this host and adds those that exist. `AI_AGENT` alone,
from a harness absent from the table, gives `agent:<value>`, the value reduced to the grammar.

### 3. The gesture commands

- `release`, `deploy` (including `--rollback`), `drill`, `bind`, `attest`, `accept` and
  `contract` resolve the actor first, before any ledger read, any network call or any ssh, and
  pass its label where `issuer` went. `deploy --plan` attests nothing and resolves no actor.
- `--issuer` is removed. A command given `--issuer` fails with a usage error that says the actor
  is resolved and names `RAIL_ACTOR`; it is never silently ignored.
- In `deploy/flow.py`, `issuer` becomes a required parameter of the forward, rollback and drill
  flows: no code path can fall back to `operator`.
- `rail contract` sends the resolved label as `X-Brain-Agent`; the requester project `red`
  does not change.

### 4. Replay keeps the original issuer

`rail attest --from` and `rail ledger replay` send a receipt again with the issuer it was
written with. A replay is not a new gesture, and the digest depends on the issuer. An agent that
replays an operator's receipt does not become its author, and neither does the operator.

### 5. `rail reviewer rule`

The terminal check is replaced by the resolution: only an `operator` actor may rule, and the
ruling is attested under the resolved label. Today's behaviour is kept; an agent behind a
pseudo-terminal no longer passes, because its marker wins over the terminal. The `auto` mode of
T7 comes later and is not part of this spec.

### 6. History

Records issued before the release that ships this spec carry `operator` whoever ran them. The
rail does not rewrite them. Before that release's `released` attestation, `operator` means "actor
unknown"; T1 uses this cut-over so that those gestures are not counted as the operator's.

## Failure and threat model

The threat is error and honesty: agents cooperate, harnesses declare themselves, and the rail
must never attribute an agent's gesture to the operator by default.

What the design defends against:

- **An agent falling back to `operator`.** There is no default any more: an agent is recognised
  by its marker, and an actor that is neither marked nor at a terminal is refused.
- **An operator variable inherited by an agent.** `RAIL_ACTOR=operator` set in a shell profile
  reaches every agent's tool call; the conflict with the agent marker is refused, so the mistake
  surfaces at the first gesture instead of corrupting the measure.
- **An unknown harness without a terminal** (a cron job, a new tool): refused with the fix named.
- **A label rewritten by brain.** The grammar keeps every label within what brain stores
  unchanged, so the spooled receipt's digest still matches the recorded row.
- **A refusal half-way.** Resolution happens before any side effect: a refused gesture writes no
  spool receipt, opens no ssh and calls no brain tool.

What it does not defend against, declared:

- **A deliberately lying agent.** One that unsets its markers and allocates a pseudo-terminal,
  or sets `RAIL_ACTOR=operator` without a marker, is recorded as the operator. Closing this needs
  a separate Unix user for agents or an out-of-band confirmation of the operator's presence; the
  operator chose not to, for now.
- **A harness with no marker running in a real terminal** is counted as the operator until its
  marker is added to the table.
- **CI** runs no gesture; nothing changes there.

## Non-goals

- Enforcing autonomy levels, `--yes`, caps per target (T4 `8f0dce93`).
- Measuring gestures and questions in `rail metrics`, and the taxonomy of research note
  `17f0d06a` (T1 `c5231125`).
- Automatic review rulings (T7 `510d475c`).
- A separate Unix user for agents, hardware keys, any out-of-band confirmation.
- Rewriting historical records, any change on brain-v42's side, renaming the reviewer identity.

## Success criteria

1. A table of cases on `resolve_actor`: each known marker; a valid and an invalid `RAIL_ACTOR`;
   `RAIL_ACTOR=operator` with a marker (refused); a terminal without a marker (`operator`);
   neither (refused); a 64-character boundary, `/` and `$` (refused); an over-long session id
   (truncated, still valid).
2. For each gesture command, against the fake brain: the resolved label arrives as
   `issuer_identity`, and a refused resolution leaves the spool and the ledger untouched and opens
   no ssh.
3. `rail attest --from` run by an agent keeps the receipt's original issuer.
4. `rail reviewer rule` is refused for an agent behind a pseudo-terminal and accepted for
   `operator`.
5. Each gesture command given `--issuer` exits with a usage error naming `RAIL_ACTOR`.
6. `grep -rn '"operator"' src/rail/commands src/rail/deploy` finds no default issuer.

## Delivery

Two pull requests, each below 3,000 changed lines excluding lockfiles, generated files, receipts
and vendored code.

1. This spec and its plan, nothing else.
2. `actor.py`, the marker table, the gesture commands, the deploy flows, `reviewer rule`, and the
   tests above.

The global rail is reinstalled after the second merge. The cut-over of decision 6 is the
`released` attestation of the next rail release.
