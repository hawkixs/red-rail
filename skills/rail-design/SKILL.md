---
name: rail-design
description: Guide the design conversation of a ReD project (stage 2 of the rail), write the dated spec, then validate it with `rail check design`. Use when a project enters design or when `rail check design` fails.
---

# rail-design

You guide a design conversation; the rail decides whether the result is a valid spec.

1. Read `rail.yaml` and the latest `docs/specs/*.md` if any. Ask what problem the project
   solves and what would make it a success, one question at a time; propose alternatives
   before settling; write the choices down as they are made.
2. Write `docs/specs/<yyyy-mm-dd>-<topic>.md` in English. Durable choices also get an ADR in
   `docs/adr/`.
3. Run `rail check design --repo <path>`. Fix every item it reports and run it again until it
   passes. When this skill and the CLI disagree, the CLI is right — do not paraphrase its
   rules here.
4. Report the final `rail check design` output to the operator.
