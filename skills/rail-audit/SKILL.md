---
name: rail-audit
description: Produce and read the repository × stage matrix of the ReD projects with `rail audit`, and turn a drift into concrete next steps per project. Use for a periodic drift review or before choosing what to standardise next.
---

# rail-audit

1. Run `rail audit <projects-root>` (or a list of project paths); `--json` for the full
   report, `--matrix` for the diffable shape.
2. Read the table: a project is scored against its own tier; `!` marks a declared exception,
   `·` a stage outside the tier, `tier?` an undeclared manifest.
3. For each `✗`, run `rail check <stage> --repo <project>` to get the details, and propose the
   smallest change that turns it green — or a tier change if the declared tier is wrong. When
   this skill and the CLI disagree, the CLI is right.
