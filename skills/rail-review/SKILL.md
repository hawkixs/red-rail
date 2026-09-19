---
name: rail-review
description: Pre-review the current branch from the producing session with the tiered workflow `workflows/pre-review.js` (scan on haiku, review on sonnet, verify on opus), fix the confirmed findings, then check `rail check review`. A pre-review improves the PR; it never satisfies the review gate — the independent verdict comes from `rail reviewer`.
---

# rail-review

A pre-review is launched by the session that wrote the code. It improves the branch before
the independent verdict and **never satisfies** the review gate (ADR-0003).

1. Run the Workflow tool on `workflows/pre-review.js` (the `red-rail` checkout); it returns
   the confirmed findings only.
2. Fix them with a failing test first; commit.
3. Open or update the PR, then ask the operator for the independent verdict:
   `rail reviewer once --repository <owner/name> --pr <n>` (host only, App `red-rail-reviewer`).
4. `rail check review --repo <path>` passes only with that verdict on HEAD's history.
   When this skill and the CLI disagree, the CLI is right.
