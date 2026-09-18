---
name: rail-check
description: Run the rail gates of a ReD project against its declared tier and explain the result. Use before a commit, before opening a PR, or when CI's rail check is red.
---

# rail-check

1. Run `rail check --repo <path>` (add `--json` when you need the machine-readable report,
   `STAGE` to focus on one stage, `--all` to look beyond the declared tier).
2. Read each `FAIL` line: the details say what is missing. `EXC` lines are declared
   exceptions from `rail.yaml` — visible on purpose, never to be added silently; a new one
   needs a reason and the operator's agreement.
3. Fix the repository, not the gate. When this skill and the CLI disagree, the CLI is right;
   a wrong gate is fixed in red-rail, never bypassed in a project.
