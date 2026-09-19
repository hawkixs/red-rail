---
name: rail-reviewer
description: Run the independent PR reviewer of the ReD rail (`rail reviewer once|run`) from the host — GitHub App `red-rail-reviewer`, judges on headless-agents in an isolated seat, verdict published as the `red-rail/review` check and attested `review_verdict`. Use to review one PR on demand or to start the polling service; never from CI.
---

# rail-reviewer

The reviewer runs on the host, in pull mode, with the operator's private config
(`~/.config/red-rail/reviewer.yaml`, the App key outside any tree).

1. One PR on demand: `rail reviewer once --repository <owner/name> --pr <n>`.
2. Everything pending once: `rail reviewer once`; the service: `rail reviewer run`.
3. Re-run a review on the same head SHA by adding the label `rail-review:rerun` to the PR.
4. Read the result with `rail check review --repo <path>` and `rail ledger list --repo <path>`.
   The verdict is what the ledger says; when this skill and the CLI disagree, the CLI is right.
