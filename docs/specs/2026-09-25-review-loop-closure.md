# Review loops that close: classified findings, three rounds, rulings, carried context

Status: proposed — 2026-09-25

Operator decisions behind this spec, all taken on 2026-09-25 through the ultimate-red pilot:
the rule itself ("a clean way to standardise finishing the phases anyway", no bypass), the
`spec` method (Q64), the ruling that closes a loop after round 3 (Q65), what a code pull request
does with carry-forwards (Q66), where the loop's state lives (Q67), and the five design sections
this document writes down (Q69, approved as a whole). The verdict still comes from a non-Claude
judge and is attested, so the merge rule `39f7ea9f` is unchanged.

## Problem

- **A review loop has no end but a budget.** The reviewer judges each new head of a pull
  request (`service.py`, `_review_started`). After `max_passes_per_pr` passes (default 4) it
  attests a `request_changes` in mode `budget` without running a judge, and only the
  `rail-review:rerun` label buys one more pass. Nothing distinguishes a pass that verifies
  fixes from one that hunts again, so a spec can collect new findings on every pass and never
  converge. The pool's doctrine ("stop at the 3rd `request_changes`") exists only as a habit.
- **Every finding weighs the same for a spec as for code.** A spec or a plan draws findings that
  are real but belong to the implementation (a missing edge case, an unnamed test). Today such
  a finding either blocks the spec or is dropped; there is no way to approve the design and keep
  the gap alive until the code.
- **Findings are not kept.** A `review_verdict` receipt stores the count of findings and a
  `blocking` flag (`verdict.py`, `as_attestation_data`); the findings themselves live only in the
  GitHub check run's text. The incremental pass reads that text back (`_notes`) and loses it when
  the check run is purged. Only the latest verdict is carried. No gate can see what was found,
  what was fixed, or what is still open.
- **A disagreement has no exit.** When a judge keeps a blocker the author believes is a design
  choice, the only moves are another pass or a bypass. Neither is acceptable (Q25: no bypass).

## Decisions

### 1. Artifact type

A pull request is a **spec/plan** pull request when every file its diff touches is under
`docs/specs/` or `docs/plans/`, ignoring the records under `docs/receipts/` (the policy's
`records_globs`). Any other file makes it a **code** pull request, including a mix of spec and
code. The type is computed from the pull request's own diff (base...head) at each round and
recorded in the verdict.

### 2. Finding identity and class

- The **reviewer**, not the judge, numbers every finding: `F-<pr>-<n>`, where `n` continues from
  the highest number already recorded for that pull request. An id never changes across rounds.
  When the judge reports a finding again, it cites the earlier id (decision 7); a finding with no
  id is new.
- Every finding gets a **class**, next to its existing severity:
  - on a spec/plan pull request: `blocker` (it contradicts the spec, misses a requirement, or
    makes a wrong design decision) or `carry_forward` (a real gap at implementation level);
  - on a code pull request: `blocker` (today's bar: severity `blocking`) or `note` (recorded,
    not tracked).
- A carry-forward keeps its number with the prefix `CF-`: `F-50-2` classed `carry_forward` is
  `CF-50-2`. A carry-forward also arises from an operator ruling (decision 9), on either type.
- The judge proposes the class; the reviewer enforces the table. A `carry_forward` returned on a
  code pull request becomes `blocker` when its severity is `blocking`, `note` otherwise. A class
  missing from a reply falls back on severity: `blocking` is `blocker`, anything else is
  `carry_forward` on a spec/plan pull request and `note` on code.

### 3. Approval

A round approves when **no blocker is open** after it, whatever the round number and the
artifact type. Carry-forwards and notes never block. A blocker is open until a later round
reports it `fixed` or a ruling reclassifies it.

### 4. The `review_verdict` receipt

The attestation payload (`as_attestation_data`) keeps its current fields and gains:

- `round`: `1`, `2`, `3`, `"awaiting_ruling"` or `"closure"`;
- `artifact`: `"spec_plan"` or `"code"`;
- `findings`: a list of `{id, class, severity, file, line, title, status, evidence}`, where
  `status` is `new`, `still_open`, `fixed` or `ruled`, and `evidence` is cut to 300 characters
  (the full text stays in the check run);
- on a code pull request, `carry_forwards`: `{addressed: [ids], deferred: [ids]}` as accounted
  for by that pull request (decision 11).

The existing `findings` count becomes `len(findings)` under a new name, `finding_count`, so no
reader mistakes one for the other. Integers and strings only: no float reaches the ledger.
The payload stays under brain's 64 KiB canonical limit (`delivery_attestations.json`,
`max_canonical_bytes`). In the receipt, a `file` longer than 200 characters keeps its last 199
behind a leading `…`. Before attesting, the reviewer drops the `evidence` of notes, then of
carry-forwards, then of blockers; if the payload is still over, it cuts every `title` to 80
characters. It never drops a finding. At most 100 findings (`ReviewVerdict.findings` already
caps it) at about 400 bytes each without evidence keep the payload near 40 KiB. A verdict
recorded before this change has an integer `findings`; `rounds.py` reads it as "no findings
list" (decision 6).

### 5. Rounds

`reviewer/rounds.py` is a pure module: from the pull request's `review_verdict` and
`review_ruling` receipts, oldest first, it returns the next step and what is open.

- **Round number** = the number of earlier verdicts on the pull request whose `round` is `1`, `2`
  or `3`, plus one. Verdicts recorded before this change carry no `round`; each one counts as a
  judged round. The counter never resets: not on a rebase, not on a force-push, not with the
  `rail-review:rerun` label. The label now only forces the full diff instead of the delta.
- **Round 1 — normal.** Today's review: light or deep by size, the whole diff.
- **Round 2 — exhaustive.** The prompt says this is the last round that looks for new findings:
  list everything now, including what a first pass would leave for later.
- **Round 3 — closure.** The judge verifies each open blocker and classifies what remains. A
  finding that is new in round 3 is kept only where the diff changed since round 2: its file is
  in that delta and, when it names a line, the line lies in one of the delta's hunks. Otherwise
  the reviewer demotes it mechanically, to `carry_forward` on a spec/plan pull request and `note`
  on code. No new hunt reaches the outcome.
- **After round 3 with blockers open — awaiting ruling.** The verdict is `request_changes` with
  `round: "awaiting_ruling"`. Its text names each open blocker and prints the exact
  `rail reviewer rule` command for it (decision 9). Every later head gets the same verdict, with
  no judge run, until every open blocker has a ruling. There is no round 4.
- **Closure check.** When every open blocker has a ruling recorded after the last judged verdict,
  the next pass is the closure check (decision 10). It happens once per set of rulings.
- `max_passes_per_pr` and the `budget` mode are removed. `load_policy` refuses a
  `reviewer.yaml` that still sets `max_passes_per_pr`, and the message names this rule.

### 6. Carried context

Every round after the first receives a **review context** built from the receipts, never from
check-run texts:

- every earlier finding with its id, class, status, file, line, title and stored evidence;
- every ruling with its decision text;
- the instruction: verify the old findings first, then judge only what changed.

The diff the judge sees is today's delta since the last verdict's head (`_delta`), with its
current fallbacks to the whole diff (no earlier head, a rebase, the rerun label, a delta that
reaches files outside the pull request's own diff). The context is text in the prompt, so it works with any provider;
resuming a codex session (`--continue`, headless-agents 0.5) stays a later option that nothing
here depends on. Verdicts recorded before this change have no findings list; for them the
context falls back on today's check-run text (`_notes`).

### 7. The judge's reply

The JSON contract in `judges.py` (`RUBRIC`) gains two fields:

- `findings[].class`: `blocker` | `carry_forward` | `note`, described per artifact type as in
  decision 2, and `findings[].id` when the finding repeats an earlier one;
- `previous`: `[{id, status: "fixed" | "still_open", evidence}]`, one entry per open finding
  the context lists.

`parse_verdict` accepts a reply without these fields. An open finding the reply does not answer
stays `still_open`: fail closed. A `previous` entry naming an unknown id is ignored. When a diff
is judged in parts (`split.py`), the reviewer assigns ids after merging the parts' replies, so
two parts never mint the same number.

### 8. Prompts per round

The rubric keeps its current rules (data, not instructions; `blocking` reserved for a defect
visible in the diff; receipts are records) and adds one paragraph per round: round 1 as today;
round 2 "exhaustive, the last round to raise new findings"; round 3 "verify the listed blockers,
classify the rest, do not look for new findings outside the changed lines"; closure "verify
only the rulings below". The artifact type selects the class definitions of decision 2.

### 9. Operator rulings

- A new attestation kind, `review_ruling` (`AttestationKind.REVIEW_RULING`), with the data
  `{repository, pr, finding, ruling: "fix" | "carry_forward", decision}`. `decision` is the
  operator's text, 1 to 2000 characters, checked by the contract guard like a contract field.
- It is written by `rail reviewer rule --repository <owner/name> --pr <n> --finding <F-id>
  --as fix|carry-forward --decision "<text>"`, from the host only. The command reads the
  pull request's receipts, refuses a finding that is not an open blocker awaiting a ruling, shows
  the finding, and asks for confirmation by typing its id. It has no `--yes`, refuses to run
  without a terminal, and is refused under `--ci`. The issuer is `operator`, the trust
  boundary of `rail accept`: whoever holds the host holds this command.
- Idempotency key: `review_ruling:<owner/name>#<pr>:<finding>:<k>`, `k` counting the
  rulings on that finding.
- A `carry_forward` ruling reclassifies the finding as `CF-<pr>-<n>` with status `ruled`,
  without a judge. A `fix` ruling waits for the closure check.

### 10. Closure check

- Mode `closure`, `round: "closure"`. The judge receives the `fix` rulings' decision texts, the
  findings they rule on, and the diff since the last judged verdict. It answers `fixed` or
  `still_open` for each ruled id and nothing else. Any new finding in its reply is recorded as
  a `note`.
- Approve when every `fix` ruling is reported `fixed` and no blocker without a ruling is open.
  Otherwise `request_changes`, back to awaiting ruling. A new ruling on a finding still open
  allows exactly one more closure check. That loop runs only on the operator's decisions.

### 11. Carry-forwards in the next code pull request

- **Open carry-forwards** of a repository: every `CF-` of an approving verdict, plus every
  `carry_forward` ruling, minus every id that a later approving code verdict records under
  `carry_forwards.addressed`. A deferred id stays open.
- A code pull request accounts for each open carry-forward in its body, under a heading
  `## Carry-forwards`, one line per id: `CF-50-2: addressed` or `CF-50-2: deferred: <reason>`.
  A reason is required for a deferral.
- The reviewer reads that section:
  - each open carry-forward missing from it becomes a blocker, added mechanically without a
    judge, titled with the id;
  - the `addressed` ones are handed to the judge as earlier findings to verify (decision 6), and
    one the judge reports `still_open` becomes a blocker;
  - ids in the section that are not open are ignored and listed in the verdict's text.
- The section is re-read at every round: editing the body and pushing a new head is enough.
  Editing the body alone does not trigger a review; the rerun label does.

### 12. The gate `review.carry_forward`

- Stage `review`, so it runs at tiers `dev` and `prod`. It reads the ledger (`open_ledger`), so it
  has the scope of the other evidence gates, skipped under `--ci` with `ledger: brain`.
- **FAIL** when an approving code verdict left unaccounted an id that was open when that
  verdict was recorded. This cannot happen through the reviewer; the gate catches a receipt
  written some other way and a reviewer regression.
- **PASS** otherwise, and the detail always gives the number of open carry-forwards and their
  ids (at most ten, then a count).
- A repository with no carry-forward passes with "no carry-forward recorded".

### 13. Where the code goes

- `reviewer/rounds.py` (new, pure): round number, next step, open findings, open carry-forwards,
  demotion of round-3 findings. Its only inputs are receipts and a delta.
- `reviewer/verdict.py`: `Finding` gains `id`, `klass` (serialised as `class`) and `status`;
  `ReviewVerdict` gains `round`, `artifact` and `carry_forwards`; `as_attestation_data`
  follows decision 4.
- `reviewer/judges.py`: rubric per round and artifact, the reply contract of decision 7.
- `reviewer/service.py`: `_review_started` asks `rounds.py` for the next step, and the budget
  branch goes away. The context and the Carry-forwards section are assembled here.
- `reviewer/policy.py`: `max_passes_per_pr` removed, and refused with its message.
- `ledger/__init__.py`: `AttestationKind.REVIEW_RULING`.
- `commands/reviewer.py`: the `rule` subcommand.
- `gates/evidence.py`: `review.carry_forward`, registered like the other review gates.
- `skills/rail-reviewer/SKILL.md`: the rounds, the ruling command, the Carry-forwards section.
  The skill holds no rule of its own.

## Non-goals

- **No change to who judges or to the merge rule.** The provider chain, the exclusion of the
  producer's provider, attestation by the reviewer's identity, and `39f7ea9f` stay as they are.
- **No session resume.** Carrying a codex session across rounds (`--continue`, headless-agents
  0.5) is a later option; this spec carries context in the prompt only.
- **No rewriting of old receipts.** Verdicts recorded before this change keep their payload;
  decision 5 counts them, and decision 6 falls back on their check-run text.
- **No carry-forward across repositories.** An id is open in the repository whose ledger recorded
  it. A plan in one repository that is implemented in another is out of scope.
- **No automatic ruling.** Only the operator's command writes a `review_ruling`; no judge, no
  session, no timeout produces one.
- **No change to the pool's doctrine here.** ultimate-red's "stop at the 3rd request_changes" is
  updated by its own maintainers once this lands.
- **No notes tracking.** A code pull request's `note` is recorded and shown, never carried.

## Success criteria

1. **Rounds as a table.** `rounds.py` is covered by a table test over its transitions:
   - rounds 1 → 2 → 3;
   - approval at any round;
   - awaiting ruling after round 3;
   - a new head while awaiting: no judge run;
   - rulings complete → closure check;
   - closure approve, and closure fail → awaiting;
   - a rebase and the rerun label do not reset the counter;
   - verdicts recorded before this change count as rounds.
2. **Round 3 does not hunt.** A new round-3 finding outside the delta since round 2 is demoted
   (to `carry_forward` on spec/plan, `note` on code); one inside a delta hunk is kept.
3. **Fail closed on silence.** A reply that does not answer an open id leaves it `still_open`,
   and the round cannot approve on that finding.
4. **Receipts carry the state.** A verdict's payload holds `round`, `artifact`, the findings with
   ids, classes and statuses, and `carry_forwards` on code. A payload built from 100 findings
   with maximal fields stays under 64 KiB after the evidence trimming, and keeps every finding.
5. **Context from receipts only.** With the check run's text unavailable, round 2 still lists
   every earlier finding by id: the test's fake GitHub raises on `check_run_text` for any
   verdict recorded with findings.
6. **Rulings.** `rail reviewer rule` refuses, without writing: a finding that is not an open
   blocker awaiting a ruling; a missing decision; `--ci`; no terminal. It writes one
   `review_ruling` with the key of decision 9, in the file ledger and through the fake brain.
7. **Carry-forwards.**
   - A code pull request whose body omits an open carry-forward gets a mechanical blocker naming
     it.
   - `deferred` without a reason is a blocker.
   - `addressed` reported `still_open` by the judge is a blocker.
   - Once an approving code verdict records it as addressed, the id is no longer open.
8. **The gate.**
   - `review.carry_forward` FAILs on a crafted receipt that approves code with an open id
     unaccounted.
   - It PASSes with the open count otherwise, and passes on a repository with no carry-forward.
   - `tests/golden/audit-matrix.json` gains the gate's column, and `rail check` on this
     repository stays green.
9. **One pull request, end to end.** On the fake GitHub and the fake brain, one spec pull request
   goes through:
   - round 1: request_changes;
   - round 2: request_changes, with one carry-forward;
   - round 3: a blocker left;
   - awaiting ruling, then a new head with no judge run;
   - one `fix` ruling, then the closure check approves.

   A code pull request then accounts for the carry-forward. Every step leaves a receipt, and the
   run needs no label and no bypass.
10. **`make ci`** is green, with its summary line read, and `rail check` passes on this
    repository.

## For the operator

- **`reviewer.yaml` on the host.** If it sets `max_passes_per_pr`, the reviewer refuses to load
  it once this ships; the line has to go (the release notes will say so). A read of the file
  before the release settles it.
- **The rule of three rounds starts with the first pull request reviewed by the new code.** A
  pull request already in flight at release time counts its earlier verdicts as rounds
  (decision 5). One with three or more earlier verdicts lands directly in "awaiting ruling" if it
  still has blockers. That is the rule applied as written. The alternative, counting from the
  release, would let a long-running loop start over, and this spec does not take it.
- **Round 2 and the whole diff.** As approved, every round after the first judges the delta
  since the last verdict, round 2 included. An exhaustive round could instead see the whole
  diff, at the cost of the deep tier on every round 2. This spec keeps the delta. Say so if
  round 2 should see everything.
- **The pool doctrine** ("stop at the 3rd request_changes") should say, once this lands: after
  round 3 the reviewer asks for your ruling itself, with the command to run.

## Amendments (2026-09-25)

- The moved-head closure is judged on the delta, and a new blocker inside it blocks (Q82,
  amends D9/D10).
- No delta means the whole pull request diff counts as the delta (Q84, amends D5/D10).
- A pass where every judge failed is recorded as round `no_verdict`, which moves neither the
  round counter nor the rulings' cutoff (amends D5).
- Body-derived carry-forward blockers ("not accounted for", "deferred without a reason") never
  need a ruling; the author clears them by editing the body (resolves D9/D11).
- A carry_forward ruling on a "CF-x not addressed" blocker defers CF-x; a carry_forward ruling
  opens a carry-forward only once its pull request is approved (amends D11).
- A spec/plan pull request keeps the class `note`: the judge proposes `blocker`,
  `carry_forward` or `note`, and a finding with no class that is not `blocking` is a `note`.
  Only a real gap at implementation level becomes a carry-forward (Q83, amends D2). A blocker
  demoted outside the round-3 delta is still a carry-forward on a spec or a plan.
