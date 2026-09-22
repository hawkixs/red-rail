---
name: rail-plan
description: Use when a ReD project needs its dated implementation plan (stage 3 of the rail) under the `spec` or `graph` method, before any code, or when `rail check plan` fails.
---

# rail-plan

The operator chose the session's method. The plan is written and executed with that
method's tools; the rail decides whether it is a valid plan.

| Method | Write the plan with | Then execute it with |
|---|---|---|
| `spec` | `superpowers:writing-plans` | `superpowers:subagent-driven-development` |
| `graph` | `gitnexus-plan` | `gitnexus-work` |

Under `graph`, `gitnexus-lfg` chains the two with its checkpoint between them. `direct` writes
no plan: this skill does not apply.

1. Under `spec`, confirm `rail check design --repo <path>` passes first: the plan cites that
   spec.
2. Write the plan with the method's planning tool; it lands in `docs/plans/`.
3. Run `rail check plan --repo <path>`; fix what it reports until it passes. When this skill
   and the CLI disagree, the CLI is right.
4. Execute the plan with the method's executor.
