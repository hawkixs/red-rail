---
name: rail-plan
description: Turn a validated spec into a dated implementation plan (stage 3 of the rail) and validate it with `rail check plan`. Use after `rail check design` passes, before any code.
---

# rail-plan

You write the plan; the rail decides whether it is a valid plan.

1. Confirm `rail check design --repo <path>` passes; the plan must reference that spec by its
   `docs/specs/…` path.
2. Write `docs/plans/<yyyy-mm-dd>-<topic>.md` with the `superpowers:writing-plans` skill: one
   `### Task` per unit of work, each with the command that verifies it and what to expect.
3. Run `rail check plan --repo <path>`; fix what it reports until it passes. When this skill
   and the CLI disagree, the CLI is right.
4. Hand the plan to `superpowers:subagent-driven-development`.

Under the graph method the plan is the one `gitnexus-plan` writes
(`docs/plans/<date>-gitnexus-plan-<slug>.md`): write no second plan beside it. `rail check
plan` reads it in its own form and says what it lacks.
