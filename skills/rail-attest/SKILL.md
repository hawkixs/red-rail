---
name: rail-attest
description: Record delivery evidence (integrated, released, deployed, rolled_back, restored, incident_detected, fulfilled) in the project's ledger with `rail attest`, or replay a receipt after a failed attestation. Use from the host, never from CI.
---

# rail-attest

Evidence is written where the operator stands, on the host — CI never holds ledger credentials.

1. Choose the kind and gather the facts it needs (`sha`, `version`, `digest`, `drill`).
2. Run `rail attest <kind> --repo <path> --data key=value …`. With `ledger: file` the receipt
   lands in `docs/receipts/`: commit it in a receipts pull request. With `ledger: brain`
   nothing lands in the repository.
3. If an attestation failed after the fact it describes happened, replay it with
   `rail attest <kind> --from <the file the error printed>`, or every waiting one with
   `rail ledger replay` — safe, the key makes it idempotent.
4. Read back with `rail ledger list --repo <path>`. When this skill and the CLI disagree,
   the CLI is right.
