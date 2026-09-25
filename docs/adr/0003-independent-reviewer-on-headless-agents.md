# ADR-0003: The independent PR reviewer lives in red-rail and runs on the headless-agents library

- **Status**: accepted (2026-09-15)
- **Brain**: red-rail decision `ff7686c3`; brain-v42 decisions `a06f57be` (delivery review,
  2026-09-12) and `1800f901` (headless-agents naming)

## Context

brain-v42 decision `a06f57be` designed an in-house PR reviewer as lot 4 of its agents work:
pull-mode service on the host, one review per head SHA, read-only sandbox without Brain
access, JSON verdict, a dedicated GitHub App publishing a required check, fail-closed,
provider order agy → codex → claude with the rule "never the producer's provider". Since then
brain-v42 extracted its agent runtime into `headless-agents`, a generic uv workspace member:
`pydantic` only, never imports `brain_v42` (guarded by a test), "executes, never decides".

The first red-rail spec satisfied the review gate with a tiered Workflow launched from the
producing session — which `a06f57be` rejects as not independent.

## Decision

- The independent reviewer is a red-rail component (`rail reviewer`), with its own GitHub App
  `red-rail-reviewer` and its own minimal GitHub client.
- Judges run through the `headless-agents` **library pinned to a brain-v42 tag** — never through
  `brain_v42.agents` (the Dream adapter), never by importing `brain_v42`.
- red-rail, as the caller, owns the review policy as data: provider chain and the
  "never the producer's provider" rule, model tiers per case (light mode for docs and small
  PRs, deep judge only on an Important finding or a disagreement), `CapabilityProfile(mcp=None)`
  for an isolated read-only seat, the tool guard, the `ReviewVerdict` schema, fail-closed
  semantics, re-run by label.
- The verdict is published as a check named in the contract's `required_checks` (a generic
  ledger mechanism) and attested `review_verdict`; brain learns no review gate.
- A review launched from the producing session is a pre-review: it improves the PR and never
  satisfies the gate. The gate needs the independent verdict and a human approval.

## Alternatives

- Reviewer inside brain-v42 (lot 4 as planned) — rejected: review policy in the ledger, a ReD
  reviewer in a public memory product, brain learning a gate.
- Calling the Dream chain CLI as a subprocess — rejected: `brain_v42.agents` is an adapter, not
  the contract; `headless-agents` is the library meant for external consumers.
- A session-launched tiered Workflow as the gate — rejected: not independent; kept as pre-review.
- Importing `brain_v42.github` — rejected: drags brain's dependencies; a shared member only if
  both clients converge.

## Consequences

- Phase 2 gains `reviewer/`, a pinned dependency with a contract test on the
  `headless-agents` API, and a `review` gate that distinguishes pre-review from verdict.
- The operator creates and installs the `red-rail-reviewer` GitHub App; its private key lives
  outside any tree. Subscriptions are welded to the host HOME, so the reviewer never runs in CI.
- brain-v42's lot 4 shrinks to its own GitHub client for the observer.

## Amendment (2026-09-20)

The reviewer converges: after a first verdict on a pull request, a new head is judged on the
delta since the last judged head (`GET /compare`, the earlier verdict's text carried into the
prompt, mode `incremental`, light when the delta is small), rather than re-reading the whole
diff on every push. A rebase or force-push, or the label `rail-review:rerun`, discards the
delta and triggers a full review again. `max_passes_per_pr` (4) caps the passes on one pull
request: beyond it the check fails without running a judge — the verdict is attested in mode
`budget` — until the label `rail-review:rerun` grants one more pass. The ledger is the pass
counter (no in-memory state); drafts are never reviewed.

## Amendment (2026-09-25)

The pass budget above (`max_passes_per_pr`, mode `budget`) is replaced by the review-loop
closure rule: three rounds, then an operator ruling on any blocker still open, then one closure
check — no pass count, no bypass. `load_policy` refuses a `reviewer.yaml` that still sets
`max_passes_per_pr`. See `docs/specs/2026-09-25-review-loop-closure.md`.
