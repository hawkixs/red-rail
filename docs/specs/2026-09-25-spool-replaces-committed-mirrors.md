# Receipts leave the repository under `ledger: brain`: a local spool of pending attestations, and a mechanical verdict for receipts pull requests

Status: proposed — 2026-09-25

Motivation: lowers the operator load induced by paperwork. Receipts pull requests are 28 of the
120 pull requests merged across the six rail repositories (23 %). Each one is reviewed, and each
produces a verdict that has to wait for the next one (learning `570503a3`, ticket `53e7a7fe`,
autonomy retro T2).

Operator decisions behind this spec: BQ1 of the 2026-09-25 retro, recorded with the ticket
(nothing is retroactive, the committed receipts stay, the quality floor is unchanged); the `spec`
method for this session; and the design below, approved as a whole in the same session. Merge
rule `39f7ea9f` does not change: the verdict on a code pull request still comes from a non-Claude
judge.

## Problem

- **A receipts pull request carries no signal and still costs a review.** Receipts are the only
  content of 28 of the 120 merged pull requests, and 5 more are version bumps. The light judge
  approved 68 pull requests out of 68. No judge can tell a true receipt from a false one. It can
  only tell a well-formed receipt from a malformed one, and that check is mechanical.
- **The chain never ends.** A receipts pull request gets reviewed, and its verdict is a new
  receipt that waits for the next receipts pull request (red-life #19: "its own receipt goes in
  the next"). Four of these receipts are waiting in red-rail's main checkout today.
- **Under `ledger: brain`, the committed mirror duplicates the authority.** Every read already
  goes to brain. Evidence gates, `rail metrics` and `rail audit` go through `open_ledger`, and the
  reviewer's loop state goes through `Ledger.list` (`previous_verdicts`, `rulings_of`). The
  mirror is used for three things only:
  - replaying a refused attestation (`rail attest --from`);
  - `hygiene.mirrors`;
  - giving review a copy it reads but cannot judge.
- **Uncommitted mirrors pile up in checkouts.** Each mirror is written into `docs/receipts/` of
  whichever checkout ran the command. red-life had 23 of them to delete before a `git pull` would
  run.
- **A receipts pull request is judged as code.** `rounds.artifact_of` returns `code` when every
  file is a record. A receipts pull request must therefore account for every open carry-forward
  of the repository under `## Carry-forwards`, or it collects mechanical blockers.
- Decision `c8b0ea45` (2026-09-19) chose dedicated receipts pull requests because mirrors had to
  be committed at the time. This spec removes that premise under `ledger: brain`.

## Decisions

### 1. Under `ledger: brain`, pending attestations wait in a local spool, never in the repository

- **Location.** The spool lives in `$RAIL_SPOOL_DIR/<project>/`, and `RAIL_SPOOL_DIR` defaults to
  `~/.local/state/red-rail/spool`. There is one spool per host and per project (the `project:` of
  `rail.yaml`). Every checkout and worktree of that project on the host shares it, and it never
  sits inside a repository. The project's spool directory is created with mode 0700. The
  directories above it keep the host's defaults; the only thing they show is project names,
  which are public anyway.
- **Files.** A spool file is exactly today's receipt: the `Record` JSON, named by
  `receipt_filename`, written atomically. The spool's storage is a `FileLedger` rooted in that
  directory, so the digest, the verification and the reuse of a pending receipt with the same key
  behave as they do today.
- **Only `attest` writes to the spool**, because an attestation is the only write that has a
  replay path. `contract_set`, `bind` and `accept` wrote their mirror as a copy, after brain had
  answered. In brain mode they write no file at all.
- **The spool is host state.** Host state is what the `ledger` gate scope exists for, and like
  brain itself the spool is never read by a `repo`-scope gate.

### 2. The lifecycle of an attestation

`BrainLedger.attest` runs these steps:

1. It validates the payload with `brain_digest`: no float, and string keys only.
2. It writes the receipt to the spool. A pending receipt with the same key and payload is reused,
   so every retry keeps the first instant.
3. It calls `brain_delivery_attest` with `emitted_at` set to the receipt's `recorded_at`.
4. If brain records the payload with the expected digest, the spool file is removed and the
   record is returned.
5. If brain answers `idempotency_key_reused`, the rail reads brain's row for that key on the
   ticket (`brain_delivery_attestation_list`, filtered by ticket and kind). Brain's replay
   equality also covers the instant, the issuer label and the contract revision
   (`replay_equality` in `delivery-attestations-v1.0`). The rail therefore compares payload
   digests only:
   - Equal digests: the fact is recorded. The spool file is removed and brain's record is
     returned.
   - Different digests: `IdempotencyConflict`, naming both digests. The spool file is removed,
     because it can never be recorded and would keep `hygiene.mirrors` red forever.
   - No row found: `Unattested`, and the file stays.
6. If brain refuses for any other reason, or cannot be reached, the file stays.
   `Unattested(<spool file>, cause)` names the file and both replay commands.
7. If brain records a different payload digest from ours, the rail raises `LedgerError` and the
   file stays. This is an anomaly to investigate, and `hygiene.mirrors` shows it.

### 3. Replay

- `rail attest KIND --from FILE` keeps its contract: same key, payload, issuer and instant. In
  brain mode, a successful replay removes the matching spool file. A `FILE` outside the spool,
  such as a committed receipt, is replayed and left where it is.
- New command: `rail ledger replay`. It replays every receipt in the project's spool, oldest
  first, and prints one line per receipt: `recorded`, `already recorded`, `conflict` or
  `unattested: <cause>`.
  - It exits 0 when the spool ends empty.
  - It exits 2 when a receipt is still unattested.
  - It exits 1 on a conflict.
  - Under `ledger: file` there is no spool: it says so and exits 0.

### 4. Gates

`hygiene.mirrors` keeps the `ledger` scope and is still skipped under `--ci` in brain mode:

- Brain mode, spool: an empty spool passes. Otherwise the gate fails with the count, the oldest
  pending instant and `rail ledger replay`.
- Brain mode, committed receipts under `docs/receipts/`: they are still matched against brain
  rows by record digest. This is the history that BQ1 keeps, and the check does not change.
- File mode: unchanged ("the receipts are the ledger").

The other gates and readers do not change:

- `hygiene.receipts` still checks that committed receipts are well formed, in both modes.
- The evidence gates, `rail metrics` and `rail audit` already read the ledger through
  `open_ledger`.

### 5. The reviewer

- **Brain mode.** A verdict follows the path of decision 2. `ReviewOutcome.receipt` names the
  spool file only when the verdict is unattested. The check run and the review keep today's
  message, now with the spool path and `rail ledger replay`.
- **Mechanical verdict for records-only pull requests, under both ledgers.** When every path in a
  pull request's diff matches the policy's `records_globs` (default `docs/receipts/*`), no judge
  runs. The reviewer checks, from the diff (the diff of a new file is its whole content), that:
  1. every file is added. A modified, deleted or renamed receipt is refused, because the ledger
     is append-only;
  2. every added file loads as a `Record` whose digest verifies;
  3. its name is `receipt_filename(record)`;
  4. it names the repository's project (from `rail.yaml`);
  5. no two added receipts share a project and an idempotency key.

  A collision with a receipt already on the base branch is caught by `hygiene.receipts`, which
  runs in CI on the merge ref as a required check.
- **The mechanical verdict itself.**
  - It is `approve` when every check holds. Otherwise it is `request_changes`, with one blocking
    finding per failure and the receipt as the finding's file.
  - It carries `mode: mechanical`, no provider, round `mechanical` and artifact `records`.
    `mechanical` is not a judged round, so it moves no round counter.
  - It is attested as a `review_verdict` under the reviewer's identity with `independent: true`,
    so `review.verdict` accepts it unchanged.
  - No carry-forward accounting applies, because `artifact_of` returns `records` for such a pull
    request.
- **In flight at the switch.** Under brain mode no receipts pull request is expected any more. One
  that is still open when the change lands passes the same way.

### 6. `rail new`

- **Brain mode.** The contract is set in brain as it is today. There is no mirror file, no second
  commit ("mirror the delivery contract") and no second push. The message printed when a birth is
  interrupted drops its "commit the mirror receipt" step.
- **File mode.** Unchanged: the contract receipt is part of the bootstrap commit.

### 7. Migration

- Committed receipts stay where they are, and `hygiene.mirrors` keeps checking them (BQ1).
- Mirrors that the old path left uncommitted in a checkout are moved into the project's spool,
  and then `rail ledger replay` runs. Each mirror leaves the spool only once brain holds the same
  fact, and a mirror that was never attested gets recorded.
  - For red-rail's main checkout, which holds four `review_verdict` receipts, this is the last
    step of the delivery.
  - The other brain-ledger projects get an FYI with the two commands.
- The reviewer keeps writing mirrors into the checkout until the global rail is reinstalled after
  the merge (snippet `efdec356`). The reinstall is part of the delivery.

### 8. Documents

- **ADR-0002** gains an amendment dated 2026-09-25: in brain mode, the mirror is the spool of
  pending attestations, not a committed copy.
  - The ADR's rejection of "a file ledger outside the repository" still holds for the file
    ledger. The spool is not a ledger: it holds only what brain has not recorded yet, and it
    empties itself.
  - The amendment states the consequence: brain holds the only copy of the facts recorded after
    the switch.
- **Boundary rule 2** in CLAUDE.md and AGENTS.md now reads: "red-rail stores no durable fact
  outside the ledger; its spool holds only attestations brain has not recorded yet."
- **Decision `c8b0ea45`** is superseded for brain mode by a decision logged at merge. Receipts
  pull requests exist only under `ledger: file`, and they are judged mechanically.
- **Other updates:**
  - CLAUDE.md: the architecture of `ledger/` and `reviewer/`;
  - AGENTS.md: the `docs/receipts/` bullet;
  - the skills `rail-attest`, `rail-release` and `rail-deploy`: where a receipt lands, how to
    replay it, and that brain mode has no receipts pull request.

## Failure and threat model

- **Brain refuses or cannot be reached during an attestation.**
  - The receipt waits in the spool, and the flow continues ("a mirror never stops a flow",
    `deploy/flow.py`).
  - `rail check` stays red on `hygiene.mirrors` until `rail ledger replay` empties the spool.
  - This is today's behaviour. Only the place where the file lives changes.
- **The process dies between brain's answer and the removal.** The receipt stays even though it
  is recorded. The replay gets the same row back and removes it.
- **The same key is attested again later with a new instant, outside `--from`.** Decision 2,
  step 5 settles it on the payload, so no receipt ever stays stuck in the spool.
- **The host is lost with a non-empty spool.** Those attestations are lost, just as an
  uncommitted mirror is lost today. Attestations come only from the server host.
- **Brain loses data.**
  - After the switch there is no git copy of new facts. Brain's backup is their only protection,
    and its restore drill fails today (brain-v42 ticket `867a0519`).
  - This is accepted. The delivery sends an FYI to red-backup and brain-v42.
  - Rejected mitigation: a local journal of recorded receipts. It would be a durable fact outside
    the ledger, against boundary rule 2, and today it would sit on the same host as brain.
- **A forged receipt in a file-ledger receipts pull request.** The mechanical verdict checks form,
  not truth. So did the LLM judge, which cannot verify a fact either. A merge still needs the
  App's `red-rail/review` check, and no receipt can produce it.
- **`rail check` runs on another host.** It sees an empty spool. This is acceptable because
  attestations leave only from the server host.
- **Concurrent writers.** The reviewer service and sessions write distinct files, because the
  digest is in the name. Writes use an atomic replace, and removal uses the exact path. No file is
  shared.

## Non-goals

- Rewriting, moving or deleting committed receipts (BQ1).
- Changing the file ledger: receipts stay committed there, and receipts pull requests still exist.
- An automatic replay by the reviewer service. Also out of scope: the red check that an
  unattested verdict leaves behind (ticket `43267468`).
- Any change on brain-v42's side.
- The pull request size gate (T3, `d5803bbf`), the real actor of a gesture (T1, `c5231125`), and
  autonomy levels (T4, `8f0dce93`).

## Success criteria

1. **A full delivery in brain mode, against the fake brain.** The delivery runs pull request,
   merge, release, deploy, drill and acceptance. It creates or modifies no file under
   `docs/receipts/`, it leaves the spool empty, and `hygiene.mirrors` passes.
2. **A brain refusal during an attestation.**
   - It leaves exactly one receipt in the spool.
   - `hygiene.mirrors` fails and names `rail ledger replay`.
   - `rail attest --from <that file>` or `rail ledger replay` records the receipt and empties the
     spool, and the gate then passes.
3. **Attesting a key that brain already holds.** With the same payload and a new instant, it
   succeeds as already recorded. With a different payload, it exits 1 and names both digests. The
   spool is empty in both cases.
4. **A records-only pull request under `ledger: file`.**
   - Valid receipts get an approving mechanical verdict.
   - A tampered receipt, a modified or deleted existing receipt, and a receipt of another project
     each get `request_changes`.
   - No judge runs in any of these cases.
5. `rail new --ledger brain` produces one commit.
6. red-rail's main checkout has no untracked receipt left, and the spool is empty.

## Delivery

Three pull requests. Each is measured before it opens and must stay below 3,000 changed lines,
excluding lockfiles, generated files, receipts and vendored code.

1. This spec and its plan, and nothing else. Any file outside `docs/specs/` and `docs/plans/`
   would make the reviewer judge the pull request as code.
2. The spool, the replay, `hygiene.mirrors`, `rail new`, the reviewer's brain path, the documents
   of decision 8, and the rail-change rule of ticket `a3cb8271` (T6) in CLAUDE.md and AGENTS.md.
3. The mechanical verdict.

The migration of decision 7 follows the second pull request, once the global rail is reinstalled.
