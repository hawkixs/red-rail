---
name: rail-release
description: Cut a release of a prod-tier ReD project with `rail release --version X.Y.Z` — image built on the host and pushed to GHCR by digest, annotated tag on both remotes, `released` attestation — then open the receipts PR. Use from the host, on main, after the integration receipt.
---

# rail-release

Stage 7 happens where the operator stands, on the host: CI never releases.

1. Be on `main`, pushed, with an `integrated` record on HEAD (`rail check integrate --repo <path>`).
2. Preview: `rail release --repo <path> --version X.Y.Z --plan`.
3. Run it: `rail release --repo <path> --version X.Y.Z`. It logs in to GHCR with `gh auth token`
   on stdin, builds, pushes, reads the digest, tags `vX.Y.Z` on `origin` then `gitlab`, attests.
   Exit 2 = published but unattested: replay with `rail attest released --from docs/receipts/<file>`.
4. Commit the receipt in a dedicated receipts PR (decision c8b0ea45), never with code.
5. `rail check release --repo <path>`. When this skill and the CLI disagree, the CLI is right.
