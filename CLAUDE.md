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
- `src/rail/policy.py` — tier defaults (`TIER_STAGES`, `GATE_DEFAULTS`, spec section aliases)
  and `effective(repo, key)`: a `gates:` override in `rail.yaml` is a declared exception,
  reported by `rail check` and `rail audit`, never hidden.
- `src/rail/gates/` — `hygiene`, `intent`, `design`, `plan`, `build`, `evidence` (stages 5–10
  read the ledger). Three scopes: `repo`; `workstation` (`hygiene.remotes`,
  `hygiene.roster_entry`, skipped under `--ci`); `ledger` (`intent.contract`, `hygiene.mirrors`,
  every evidence gate — skipped under `--ci` when `ledger: brain`, because CI never holds a
  ledger credential). `hygiene.mirrors` reports a receipt whose digest is absent from the shared
  ledger (drift). `review.verdict` accepts only an independent, approving verdict issued by
  `review.reviewer_identity` (default `red-rail-reviewer`).
- `src/rail/ledger/` — the `Ledger` protocol, `FileLedger` (`docs/receipts/*.json`, append-only,
  digest + idempotency key, fails closed on a tampered receipt) and `BrainLedger`
  (`ledger/brain.py`: brain-v42 is the authority, the receipts are mirrors written BEFORE the
  call; a refusal raises `Unattested` and `rail attest` exits 2 with the replay command;
  `integrated`/`fulfilled` are brain milestones read from the ticket; the contract is set as the
  requester `red`, attestations as the project). `ledger:` in `rail.yaml` selects the
  authority (`file` default, `brain` needs `ticket:`) — ADR-0002. `idempotency_key_for`
  derives deterministic keys (`<kind>:<subject>[:<occurrence>]`); `brain_digest` recomputes
  brain's payload digest.
- `src/rail/brain/` — `client.py` (one synchronous MCP call, stable refusal codes, the
  `X-Brain-Agent` label sent per call = the record's issuer) and `settings.py` (loopback URL;
  the bearer is read from a private file only — `RAIL_BRAIN_TOKEN_FILE`, default
  `~/.config/red-rail/brain-token` — never from an environment variable holding the value).
- `src/rail/contracts/` — brain-v42's published contracts vendored as data at the tag in
  `pins.py` (`delivery-attestations-v1.0`: 43 finding codes, the attestation API v1.0), frozen
  by `tests/test_boundary.py` (digests in CI, parity with the sibling checkout on the host).
- `src/rail/reviewer/` — the independent reviewer (ADR-0003): `policy.py` (data: provider chain,
  models per tier, light/deep thresholds, producer read from `Co-Authored-By` trailers),
  `verdict.py` (enum-valued `ReviewVerdict`), `judges.py` (one headless run per judge, isolated
  seat, `guard.sh` for agy), `github.py` (minimal App client), `service.py` (one review per head
  SHA, fail-closed, verdict attested), `config.py` (`~/.config/red-rail/reviewer.yaml`).
- `src/rail/commands/` — one module per command, auto-discovered by `src/rail/cli.py`:
  `check`, `attest`, `contract`, `ledger`, `audit`, `metrics`, `new`, `upgrade`, `brain`
  (`ping`), `reviewer` (`once`, `run`). Exit code is the verdict; `--json` is the contract for
  machines.
- `src/rail/audit.py` (repository × stage matrix, golden-tested), `src/rail/metrics.py` (four
  DORA metrics + conformance from the ledger), `src/rail/scaffold.py` (copier: `copier.yml` at
  the root, files under `template/project/`), `src/rail/remotes.py` (`gh` + `glab`, no token).
- `workflows/pre-review.js` — a tiered Workflow (wf-scan → red-reviewer on sonnet → wf-judge)
  launched by the `rail-review` skill from the producing session: a pre-review, never the gate.
  Phase 3: `deploy/`.

Boundary rules with brain-v42, both testable: brain never learns a new gate; red-rail stores
no durable fact outside the ledger.

## Stack

Python 3.12+, uv, Click, Pydantic 2, PyYAML. copier for the template. pytest + ruff. Extras:
`brain` (fastmcp 3.x, the MCP client and the in-memory fake brain of the tests), `reviewer`
(httpx, PyJWT[crypto], `headless-agents` pinned to the brain-v42 tag `headless-agents-v0.2.0`).

## Commands

```bash
uv sync --all-extras           # install (core + dev + brain + reviewer)
make ci                        # what CI runs: lint, test, check
uv run pytest -q               # tests
uv run ruff check src/ tests/  # lint
uv run rail check              # the rail gates against this repository
uv run rail brain ping         # brain-v42 reachable with the private token, as this project?
uv run rail reviewer once      # one pass of the independent reviewer (host only)
make audit                     # dated drift snapshot of the sibling projects (docs/audits/)
make contracts-check           # vendored brain-v42 contracts equal the pinned tag
make skills-install            # symlink the facade skills into ~/.claude/skills
```

## Structure

```
red-rail/
├── Makefile               # sync, lint, test, check, ci, audit, contracts-check, skills-install
├── rail.yaml              # this project's manifest (tier dev, ledger brain + ticket, declared exceptions)
├── copier.yml             # the project template's questions; files live in template/project/
├── src/rail/              # package `rail`: gates/, ledger/, brain/, contracts/, reviewer/, commands/
├── tests/                 # incl. tests/golden/audit-matrix.json and the deterministic fixtures
├── docs/specs/            # design specs (dated)
├── docs/plans/            # implementation plans (dated)
├── docs/adr/              # architecture decision records (numbered)
├── docs/receipts/         # the file ledger: written by `rail attest` / `rail contract`, never by hand
├── docs/audits/           # dated snapshots of `rail audit ..` (the drift table, versioned)
├── template/project/      # copier template (CLAUDE.md, rail.yaml, Makefile, CI, docs, skeletons)
├── .github/workflows/     # continuous-integration.yml + rail-ci.yml (reusable, called by projects)
├── workflows/             # pre-review.js (tiered pre-review, passes the tiering gate)
└── skills/                # Claude Code facades, no rules inside (rail-design … rail-reviewer)
```

## Key technical decisions

- ADR-0001 — ledger in brain-v42, policy/execution/review in red-rail.
- ADR-0002 — pluggable ledger, standalone first; never a plugin inside brain-v42.
- ADR-0003 — the independent PR reviewer lives here and runs on the `headless-agents` library
  (brain-v42 workspace member, pinned to a tag); a session-launched review is only a pre-review.
- Design spec: `docs/specs/2026-09-14-red-rail-design.md` (ten stages, three tiers,
  `rail.yaml`, end-to-end flow, failure modes, phasing).
- Attestations come only from the server host; the runner VM never reaches brain or the VPS.
- Secrets (the brain bearer, the App key) live in `~/.config/red-rail/` in 0600 files, never in
  a tree, never in an environment variable holding the value, never on a command line.
- The brain API is v1.0 (frozen 2026-09-18, decision `4e7c2545`; tag `delivery-attestations-v1.0`
  vendored under `src/rail/contracts/`); one subject per ticket — `actor_project` is the
  project for every attestation, the requester `red` for the contract.
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
