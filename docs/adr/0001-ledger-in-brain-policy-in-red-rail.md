# ADR-0001: The delivery ledger lives in brain-v42; policy, execution and review live in red-rail

- **Status**: accepted (2026-09-14); refuted by a brain-v42 session on 2026-09-15 (ticket `04bc1f4a`, code read on `main@e11e3660`) — amendments below
- **Brain decision**: `41d8b3ef`

## Context

Delivery logic grew inside brain-v42 — migration 053 (eight `delivery_*` tables), six MCP
tools, a GitHub observer, eight in-flight `codex/delivery-*` branches — because the delivery
workflow was improved from the brain-v42 session. brain-v42 is public (Apache-2.0) and is a
memory product. A delivery rail for all ReD projects is being built (red-rail).

## Decision

brain-v42 keeps **only the ledger**: tickets, contracts, PR bindings, observed evidence,
receipts, refusal to resolve without proof, plus one generic append-only *attestation* record.
red-rail owns the **policy** (stages, gates, tiers, templates), the **execution** (scaffold,
audit, CI, deploy) and the **review** automation; verdicts are written to the ledger as
evidence. Adding policy to brain-v42 is frozen.

Two testable rules: brain never learns a new gate (its evaluator error-code list is frozen by
a red-rail test); red-rail stores no durable fact outside the ledger (repository receipts are
mirrors linked by digest).

## Alternatives

- Keep everything in brain-v42, red-rail as a client — rejected: the public product becomes a
  ReD delivery tool in disguise; twenty repositories coupled to brain's release cadence.
- Move everything out of brain-v42 into red-rail with its own database — rejected: discards
  053, six tools, the observer and the canary; blocks the POC for weeks.

## Consequences

- Industry-aligned (SLSA provenance/verifier/expectations, in-toto layout/links, Rekor as a
  policy-free ledger, OPA decision/enforcement decoupling, Kosli change-evidence gates).
- The split is logical, not physical: brain is already a service; red-rail is a repository
  and a CLI, not a new daemon. No over-engineering for a single operator.
- brain-v42 must deliver `brain_delivery_attest` before red-rail phase 2.

## Amendments (2026-09-15, after brain-v42's refutation on ticket `04bc1f4a`)

1. **Rule 1 freezes a list, not prefixes.** "Any `review_*` / `spec_*` / `deploy_*` code
   violates the contract" was already false: `review_changes_requested` and
   `review_approval_missing` are legitimate, the contract declares the approvals they check.
   The rule becomes: the evaluator's finding codes are a **closed list**, published by brain-v42
   as `DELIVERY_FINDING_CODES` (frozenset in `brain_v42.models.delivery_evaluator`, with a brain
   test that every `_finding(` belongs to it) and frozen on this side by a red-rail test that
   never imports `brain_v42` (it reads the list as published data). Any change to the list is a
   change of contract, justified on both sides. The list on 2026-09-15 (43 codes):
   `base_mismatch`, `binding_identity_invalid`, `binding_identity_mismatch`, `binding_missing`,
   `binding_unobserved`, `check_cancelled`, `check_failed`, `check_missing`, `check_neutral`,
   `check_pending`, `check_skipped`, `completion_action_invalid`, `context_changed`,
   `context_digest_missing`, `context_error`, `context_missing`, `context_predicate_duplicate`,
   `context_predicate_missing`, `context_predicate_unexpected`, `context_proof_invalid`,
   `delivery_disabled`, `delivery_disposition_terminal`, `delivery_terminal`,
   `dependency_generation_mismatch`, `dependency_predicate_duplicate`,
   `dependency_predicate_missing`, `dependency_predicate_unexpected`,
   `dependency_receipt_mismatch`, `dependency_receipt_missing`, `dependency_unsuccessful`,
   `head_mismatch`, `integration_identity_missing`, `merge_conflict`, `mergeability_unknown`,
   `observation_error`, `observation_incomplete`, `observation_missing`, `observation_stale`,
   `pr_draft`, `pr_not_merged`, `reopen_required`, `review_approval_missing`,
   `review_changes_requested`. The `check_<conclusion>` family is closed because
   `CheckAttempt.conclusion` is a six-value literal and the observer normalises the rest to
   `failure`.
2. **What brain still interprets, named.** The `integration` evaluator is not the only place
   brain judges, and the ADR now says so: `_eligible_work` (findings → who acts next) is a
   **derived, non-authoritative hint, never a gate**; the ticket lifecycle codes
   (`reopen_required`, `delivery_terminal`, `delivery_disposition_terminal`,
   `completion_action_invalid`) are **coordination** — the policy of tickets, which are brain's —
   distinct from delivery; `_stage` is derived; the freshness threshold (`observation_stale`)
   is hard-coded today and must become a contract field (brain ticket `bd1879f6`), which
   red-rail will then declare per project.
3. **One policy leaves brain.** The guard `review.reviewer != executor_identity` compared a
   GitHub login to a brain project key (broken, ticket `e31f9ad6`) and encoded "the reviewer is
   not the executor" inside brain. brain removes it; "never the producer's provider" is red-rail
   policy, carried by the reviewer itself and by `allowed_reviewers` in the contract.
4. **Third-party checks are first-class.** `RequiredCheck` = kind (`check_run` |
   `commit_status`) + name + the publishing App (`app_slug` or numeric `provider_id`); the
   evaluator wires no App identity. red-rail's contracts therefore name the reviewer's check as
   `check_run` / `red-rail/review` / `app_slug: red-rail-reviewer`; a `commit_status` carries a
   `provider_id` only. red-rail's `Deliverable.required_checks` uses the same shape from
   phase 1 so the file ledger maps 1:1 onto brain in phase 2.
5. **Admission.** No `contract-admission` branch exists on any remote. Rule agreed: validation
   of **form** is brain's strict pydantic contract models (ledger); any **admissibility** rule
   beyond form ("at least one required check", "a spec context is mandatory") is policy and
   lives in red-rail.
6. **Context corrected.** Six `codex/delivery-*` branches exist on `origin`, all already
   merged (0 commits ahead), all ledger-side; "eight in-flight branches" was wrong on
   2026-09-15 and nothing is left to triage.
7. **Attestations get their own table** (migration 054, `brain_delivery_attest` + list +
   exposure in `brain_delivery_get` with digests), not `delivery_receipts`, whose `milestone`
   literal and structured proofs are compared field by field. Delivered by brain-v42 in a lot
   after `DELIVERY_FINDING_CODES` and the `headless-agents` changelog.
8. **`headless-agents` is versioned on its own tag.** `headless-agents-v0.2.0` is pushed on
   `hawkixs/brain-v42` at release `e11e3660`; red-rail pins
   `git+https://github.com/hawkixs/brain-v42.git@headless-agents-v0.2.0#subdirectory=packages/headless-agents`
   and freezes in a contract test: `AgentProvider` (`build_command`, `child_environment`,
   `prepare_home`, `tool_call_completed`, `run`), `RunSpec`, `RunResult` / `TokenUsage`,
   `CapabilityProfile` / `McpServer` / `ToolGuard` / `Credentials`, `chain.run_chain`,
   `envelope.unwrap`, `PROVIDER_FALLBACK_EXIT_CODE = 3`, `TIMEOUT_EXIT_CODE = 124`.
