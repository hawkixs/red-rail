# ADR-0001: The delivery ledger lives in brain-v42; policy, execution and review live in red-rail

- **Status**: accepted (2026-09-14), pending refutation by a brain-v42 session
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
