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

## Amendment (2026-09-18/19): the brain API v1.0 and the mapping of the protocol onto it

- **Status of the dependency**: `brain_delivery_attest` / `brain_delivery_attestation_list` v1.0
  were frozen with brain-v42 on 2026-09-18 (red-rail decision `4e7c2545`, brain-v42 ticket
  `04bc1f4a`) and are in production since 2026-09-18 12:19Z (migration 054, PR #151, release
  `9bdb3812`). The contract is data: `docs/contracts/delivery_attestations.json` and
  `docs/contracts/delivery_finding_codes.json` at the annotated tag **`delivery-attestations-v1.0`**
  (it names the contract version, never the package version, and moves only on a contract break).
  red-rail vendors both files under `src/rail/contracts/` at that tag (`pins.py`: tag and
  sha256 of each file) and freezes them in `tests/test_boundary.py`; a change on either side is
  a change of contract, justified on both sides.
- **One subject per ticket.** `Record.project` is the ticket's `to_project` and the
  `actor_project` of every attestation and binding, incidents included; the contract is set by
  the ticket's requester (`red`, the canonical ticket being `red → <project>`) because brain lets
  only the requester set a delivery contract. `Record.issuer` is the `X-Brain-Agent` label sent
  per call (`operator` from the CLI, `red-rail-reviewer` from the reviewer), which becomes
  brain's `issuer_identity`; brain keeps no registry of labels — who may speak for whom is
  red-rail policy (the verdict gate names its issuer). `Record.recorded_at` is `emitted_at`
  (the evidence time); `Record.payload["data"]` is brain's `payload`. The record digest is
  computed locally from those fields, so the mirror written before the call and the row read
  back from brain carry the same digest; brain's payload digest
  (`sha256("brain-delivery-attestation:v1\n" + canonical(payload))`, bare hex) is recomputed
  (`rail.ledger.brain_digest`) and cross-checked on every read.
- **Mirror first, attestation second, replay from the mirror.** In `brain` mode `attest()` writes
  the receipt exactly as `FileLedger` does, then calls brain with `emitted_at` = the receipt's
  `recorded_at`. A refusal or an unreachable brain raises `Unattested(receipt, cause)`;
  `rail attest` exits 2 with the replay command. A replay carries the same key, payload and
  instant, so brain returns the same row. `list()` and `get()` read brain, never the mirrors.
- **Milestones.** `integrated` and `fulfilled` are receipts brain issues (`integration_receipt`,
  `fulfillment_receipt` of the ticket), listed by `BrainLedger` with `issuer = brain-v42` and
  never attested from the rail in `brain` mode (brain lists both kinds as reserved).
- **Deterministic keys** (`rail.ledger.idempotency_key_for`): `gate_passed:<sha>:<stage>.<code>`,
  `review_verdict:<head_sha>:<check_run_id>`, `integrated:<sha>`, `released:<version>`, and for
  recurring events `deployed|rolled_back|restored|incident_detected:<target>:<digest>:<emitted_at>`.
- **Manifest.** `rail.yaml` gains `ticket:` (the delivery ticket UUID), required with
  `ledger: brain` and forbidden otherwise.
- **CI never holds a ledger credential** (spec §5 rule 3): gates that read the ledger carry the
  scope `ledger` and are reported as skipped under `rail check --ci` when the ledger is brain;
  `hygiene.mirrors` (a mirror whose digest is absent from the shared ledger is drift) is one of
  them and is vacuous on the file ledger.
- **Transport.** Streamable HTTP on the host loopback, `Authorization: Bearer` mandatory,
  `X-Brain-Tool-Profile: native`, `X-Brain-Agent`. The bearer is read from a private file
  (`RAIL_BRAIN_TOKEN_FILE`, default `~/.config/red-rail/brain-token`, 0600, one line) and from
  nowhere else — the reference client's rule.

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
- **The file ledger is unchanged**: receipts are committed. A pull request that only adds
  receipts will get a mechanical verdict instead of a judge (spec decision 5, delivered by the
  next pull request).
