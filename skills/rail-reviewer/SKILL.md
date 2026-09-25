---
name: rail-reviewer
description: Run the independent PR reviewer of the ReD rail (`rail reviewer once|run`) from the host — GitHub App `red-rail-reviewer`, judges on headless-agents in an isolated seat, verdict published as the `red-rail/review` check and attested `review_verdict`. Use to review one PR on demand or to start the polling service; never from CI.
---

# rail-reviewer

The reviewer runs on the host, in pull mode, with the operator's private config
(`~/.config/red-rail/reviewer.yaml`, the App key outside any tree).

1. One PR on demand: `rail reviewer once --repository <owner/name> --pr <n>`.
2. Everything pending once: `rail reviewer once`; the service: `rail reviewer run`.
3. Re-run a review on the same head SHA by adding the label `rail-review:rerun` to the PR (it
   forces the full diff instead of the delta; the round counter itself never resets).
4. After a first verdict, a new push is judged `incremental` on the delta since the last head.
5. Read the result with `rail check review --repo <path>` and `rail ledger list --repo <path>`.
   The verdict is what the ledger says; when this skill and the CLI disagree, the CLI is right.

## Rounds, rulings and closure

A review loop closes; it has no pass budget. This section holds no rule of its own — the rule
is `docs/specs/2026-09-25-review-loop-closure.md`; read it for anything not covered here.

- **Round 1 — normal.** Light or deep by size, the whole diff.
- **Round 2 — exhaustive.** The last round that raises new findings.
- **Round 3 — closure.** Verifies the open blockers and classifies the rest; a new finding
  outside the delta since round 2 is demoted mechanically, never kept.
- **Awaiting ruling.** With a blocker still open after round 3, every later head gets the same
  `request_changes` verdict with no judge run, until every open blocker has a ruling. There is
  no round 4. The verdict names the exact command for each blocker:
  `rail reviewer rule --repository <owner/name> --pr <n> --finding <F-id> --as fix|carry-forward
  --decision "<text>"`.
- **Closure check.** Once every open blocker has a ruling recorded, the next pass verifies only
  the `fix` rulings and approves when they all come back `fixed`; a `still_open` answer returns
  to awaiting ruling.

## Carry-forwards

A code pull request accounts for the repository's open carry-forwards in its own body, under a
`## Carry-forwards` heading, one line per id (`carry.LINE_FORMAT`):
`CF-<pr>-<n>: addressed` or `CF-<pr>-<n>: deferred: <reason>`. An id missing from that section,
or deferred with no reason, becomes a mechanical blocker with no judge involved; an `addressed`
id still needs the judge to confirm it. The `review.carry_forward` gate fails only when an
approving code verdict left an open id unaccounted for — a receipt written some other way, or a
reviewer regression.
