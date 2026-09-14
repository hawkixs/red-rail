# ADR-0002: The ledger is a pluggable backend; red-rail works alone, brain-v42 is the upgrade

- **Status**: accepted (2026-09-15)
- **Brain**: ReD ADR #15 (public bricks work alone), red-rail decision `43f85b5d`

## Context

Without brain-v42, red-rail kept `check`, `audit`, `new` and `upgrade` but lost evidence, DORA
metrics, the integration gate and persisted review verdicts. A public tool that needs another
server to be complete is not adopted. Hosting red-rail as a plugin inside brain-v42 would
recreate the policy/ledger coupling cut by ADR-0001. brain-v42 already works fully without
red-rail: its delivery ledger is an optional module, off by default.

## Decision

`rail.ledger` is a protocol — `contract_set`, `bind`, `attest`, `list`, `get` — with two
implementations selected by `ledger:` in `rail.yaml` (default `file`):

- `FileLedger`: the repository's `docs/receipts/*.json` **are** the ledger. Append-only,
  committed, each record carries a digest and an idempotency key. One operator, one
  repository, no network.
- `BrainLedger`: brain-v42 is the shared, cross-project, observed authority; the receipts
  become mirrors linked by digest.

Same files, only the authority changes. `rail metrics` reads the protocol, so DORA metrics
exist in both modes. The protocol is the versioned contract between the two bricks, tested on
both sides. red-rail is never hosted inside brain-v42.

## Alternatives

- brain-v42 as the only ledger — rejected: red-rail incomplete without a third-party server,
  unusable outside ReD, mute when brain is down.
- A file ledger outside the repository (`~/.local/state/…`) — rejected: not shared, lost with
  the machine, invisible in review; committed receipts are versioned and re-read by CI.
- red-rail as a brain-v42 plugin — rejected (ReD ADR #15).

## Consequences

- Phase 1 gains a `hygiene.receipts` gate (well-formed receipts, consistent digests, no
  duplicate idempotency key) and delivers `FileLedger`, so attestations and DORA are testable
  without brain. `brain_delivery_attest` gates the shared ledger (phase 2), not the POC.
- In `brain` mode, a local receipt whose digest matches no attestation is a drift reported by
  `rail audit`.
- Two backends to maintain against one contract test suite.
