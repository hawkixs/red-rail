# red-rail — ReD sub-project

## Project

The delivery rail of the ReD ecosystem: one standard for the whole lifecycle (idea →
production), executable gates, measured drift. red-rail owns the **policy** (stages, gates,
maturity tiers, templates), the **execution** (scaffold, audit, CI, deploy) and the **review**
automation. brain-v42 owns the **ledger** (tickets, contracts, evidence) — see ADR-0001.
red-rail is delivered by its own rail (dogfooding): `rail.yaml` declares tier `dev`.

- **Repo**: `~/hawkixs_infra/git_repo/ReD_v1/projects/red-rail/`
- **GitHub (`origin`)**: `git@github.com:hawkixs/red-rail.git` (private, canonical)
- **GitLab (`gitlab`)**: `ssh://git@gitlab.hawkixs.local:2222/hawkixs_project/red/red-rail.git` (private mirror)
- **Brain MCP project key**: `red-rail` (group `red`)
- **Parent project**: ReD v1 (`~/hawkixs_infra/git_repo/ReD_v1/CLAUDE.md` — roster, cross-project rules)

Published branches are pushed to both remotes and compared SHA by SHA. GitHub and GitLab share
no atomic transaction: a push to one remote is a deliberate divergence until the second is synced.

## Language

Everything pushed to a remote is written in **English**: commits, branches, PRs, docs, code
comments, test names. The conversation with the operator stays in French.

## Architecture

- `src/rail/model.py` — `RailConfig`, the per-project manifest (`rail.yaml`). Anything not in
  the manifest is a tier default versioned here.
- `src/rail/gates/` — one module per stage. A gate is a pure function
  `fn(repo: Path) -> GateResult`; it never raises. The same function runs locally, in CI and
  behind a Claude Code skill, so policy exists once.
- `src/rail/cli.py` — `rail`. Exit code is the verdict; `--json` is the contract for machines.
- Planned (see the design spec): `ledger.py` (brain client), `scaffold.py` (copier),
  `audit.py`, `metrics.py`, `deploy/`, `template/`, `workflows/`, `skills/`.

Boundary rules with brain-v42, both testable: brain never learns a new gate; red-rail stores
no durable fact outside the ledger.

## Stack

Python 3.12+, uv, Click, Pydantic 2, PyYAML. copier for the template. pytest + ruff.

## Commands

```bash
uv sync --extra dev            # install
just ci                        # what CI runs: lint, test, check
uv run pytest -q               # tests
uv run ruff check src/ tests/  # lint
uv run rail check              # the rail gates against this repository
```

## Structure

```
red-rail/
├── rail.yaml              # this project's manifest (tier dev)
├── src/rail/              # package `rail`
├── tests/
├── docs/specs/            # design specs (dated)
├── docs/plans/            # implementation plans (dated)
├── docs/adr/              # architecture decision records (numbered)
├── template/              # copier template (phase 1)
├── workflows/             # reusable CI workflow + review Workflow script (phases 1-2)
└── skills/                # Claude Code facades, no rules inside (phase 1)
```

## Key technical decisions

- ADR-0001 — ledger in brain-v42, policy/execution/review in red-rail.
- Design spec: `docs/specs/2026-09-14-red-rail-design.md` (ten stages, three tiers,
  `rail.yaml`, end-to-end flow, failure modes, phasing).
- Attestations come only from the server host; the runner VM never reaches brain or the VPS.
- No `# rail: ignore`. The only bypass is a `gates:` override in `rail.yaml` with a mandatory reason.

## Working principles

### Workflow
- Brainstorm → spec → plan → implement, with the skills: `sdd-brainstorm`, `writing-plans-parallel`
  / `sdd-plan`, `executing-plans-parallel` / `sdd-implement`. Never the built-in plan mode.
- If it derails mid-way, **stop and re-plan immediately**.
- TDD: write the failing test first, watch it fail, implement the minimum.

### Quality pipeline before every commit
```
implementation done
  → /tdd-write-tests                    (missing tests)
  → /reflexion-reflect                  (non-trivial change only)
  → /code-review-review-local-changes   (multi-agent review)
  → /git-commit                         (conventional commit, English)
```
Skip the review for docs-only commits.

### Subagents and workflows
- Offload research, exploration and parallel analysis to subagents; one task per subagent.
- Every Workflow `agent()` carries an explicit tier (`wf-scan` / `red-researcher` /
  `red-implementer` / `red-reviewer` / `wf-judge` / `wf-design`); never an implicit Fable agent.

### Verification before "done"
- Never declare a task done without proof: run the tests, read the summary line, capture the
  exit code. `rail check` must pass on this repository.

### Self-improvement (brain MCP)
- After any correction from the operator: `brain_learn(topic, insight, project_key="red-rail")`.
- Before solving a problem: `brain_search(query, project_key="red-rail")`.

### Core principles
- **Build to last**: solid, tested, documented.
- **No shortcuts**: no quick fixes, no unnecessary dependencies.
- **Scalable**: think about twenty repositories even while proving one.
- **From scratch**: prefer building the small thing over adopting the big platform.

## Brain MCP — proactive use

Project key: `red-rail`. Use it without being asked.

- **Session start**: `brain_session_start("red-rail")`
- **During work**: `brain_log_decision` for choices, `brain_save_snippet` for reusable code,
  `brain_create_runbook` for procedures, `brain_learn` only for pure insights.
- **Session end**: `brain_update_project_focus("red-rail", current_focus="summary + next steps")`

## Related projects

| Project | Path | Relation |
|---|---|---|
| ReD (root) | `~/hawkixs_infra/git_repo/ReD_v1/` | parent, roster, cross-project rules |
| brain-v42 | `projects/brain-v42` | the ledger (tickets, contracts, evidence, attestations) |
| red-runners | `projects/red-runners` | the `red-ci` self-hosted runners (repo-scoped, on demand) |
| red-monitor | `projects/red-monitor` | observation of deployed services |
| red-watcher | `projects/red-watcher` | VPS perimeter rules for deployments |
