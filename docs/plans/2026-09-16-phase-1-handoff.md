# Phase 1 handoff (2026-09-16) — read this first at the next session

Written at the end of the phase-1 session because brain-v42 was unreachable after the host
reboot (`ConnectionRefused` on the MCP): the session focus, the decision and the runbook
below could not be recorded in the brain. First gesture of the next session: start brain-v42,
then `brain_session_start("red-rail")` and record what this file says.

## State

- Branch `feat/phase-1-rail-without-network`, HEAD after the review fixes: `6264c76`, pushed
  to `origin` (GitHub). **PR #1** open: <https://github.com/hawkixs/red-rail/pull/1>.
  `main` on both remotes is still `d7a11a9`.
- The plan `docs/plans/2026-09-15-phase-1-rail-without-network.md` is fully executed:
  18 tasks, 5 batches, every checkpoint green; 172 tests; `make ci` exit 0; `rail check`
  on red-rail: 17/17 at tier `dev` with the declared exception `review.verdict`.
- Phase-1 proof observed (spec §8): day-0 matrix of the 24 projects in `docs/audits/`,
  fresh scaffold 8/8 at `bootstrap`, red-rail 17/17 at `dev`, attestations + DORA on a
  file-ledger repository (`rail metrics`, `rail check --all`).
- Review: 15 findings, all fixed with a reproducing test first (commit `6264c76`).

## To do next, in order

1. Start brain-v42; record in the brain (project `red-rail`): decision — the phase-1
   implementation choices (see the plan header) plus the review fixes; runbook "Create a
   ReD sub-project with `rail new`" superseding `a050e6ec`; focus = this file.
2. Check CI on PR #1 (`gh pr checks 1`): the slim container now installs git, curl and
   gitleaks before checkout; `rail check --ci --json` runs in CI scope. Fix if red.
3. Operator merges PR #1 (ruleset `protect-main`), then:
   `git switch main && git pull --ff-only origin main && git push gitlab main`,
   compare `git ls-remote origin refs/heads/main` with `gitlab`, tag `v0.2.0` on `main`
   and push the tag to both remotes so `rail new --template-ref v0.2.0` / `rail upgrade`
   have a target.
4. `make skills-install` on the workstation (facade skills into `~/.claude/skills`).
5. Reply on brain ticket `04bc1f4a` only if brain-v42 answers the request for
   `DELIVERY_FINDING_CODES` as data; phase 2 waits for that list and for
   `brain_delivery_attest` (migration 054).
6. Phase 2 plan (`writing-plans-parallel` from spec §8): `BrainLedger` on the same contract
   suite (`tests/ledger_contract.py`), boundary test against the 43 frozen codes (ADR-0001
   amendment 1), `reviewer/` on `headless-agents-v0.2.0`, `workflows/pre-review.js`.

## Known gaps and open items

- The go stub of the template is never compiled by a test (no go toolchain on the dev host).
- `docs/audits/*.json` carries relative `../<project>` paths; no absolute path anywhere.
- Public flip still deferred: scrub list = `gitlab.hawkixs.local` (policy defaults, remotes,
  template), `~/hawkixs_infra` paths (template CLAUDE.md), `probe.hawkixs.com`, `red-ci`
  label, `tiering_gate.py` mention; LICENSE/SECURITY/CONTRIBUTING; commit rewrite.
- The roster row for a new project is still a manual edit of the ReD root `CLAUDE.md`
  (the root is not under git); `red-e2e-target` fails the roster gate — operator's call.
- Execution lesson: the Agent tool's isolated worktrees branch from `main`, not from the
  integration branch — every task prompt started with `git reset --hard <branch>`; the
  `red-reviewer` agents hit a 15-turn limit and had to be told to report; a host reboot
  wipes `/tmp` (scratchpad) but not `.claude/worktrees/`.
