# red-rail — Design: a delivery rail for the ReD ecosystem

- **Date**: 2026-09-14
- **Status**: validated in a brainstorm session at the ReD root; pending review by a brain-v42
  session for the boundary contract (section 3)
- **Brain project key**: `red-rail` (group `red`)
- **Related**: ReD decision `41d8b3ef` (boundary), learning `8c417379` (root cause),
  ADR-0001 in this repository

## 1. Problem

ReD is ~20 repositories delivered by one operator plus AI agents (Claude Code sessions,
tiered Workflows, opencode/Codex workers). Delivery practice has drifted:

- CI is heterogeneous: 5 repositories run GitHub Actions only, 6 GitLab CI only, 3 both,
  10 none. No repository has pre-commit hooks or an ADR directory.
- The project template (`templates/CLAUDE.md` at the ReD root) itself drifted from the
  root conventions it is supposed to propagate.
- `red-e2e-target` exists on disk but not in the roster, which is declared the source of truth.
- Delivery logic grew inside brain-v42 (migration 053, eight `delivery_*` tables, six MCP
  tools, a GitHub observer, eight `codex/delivery-*` branches) because the workflow was
  improved from the brain-v42 session. brain-v42 is public (Apache-2.0) and sold as a memory.

The goal is one standard for the whole lifecycle — idea to production — that every current
and future ReD project follows with constancy, where drift is **measured** rather than noticed.

## 2. Decisions taken during the brainstorm

| # | Question | Decision |
|---|---|---|
| 1 | Test subject of the POC | A greenfield, disposable service (`red-probe`), built end to end |
| 2 | Who drives the rail | Humans + sessions for reflection, design and arbitration; agents for execution and verification; the rail defines the handoffs and every gate is executable |
| 3 | Where "production" ends | Deployed for real on the VPS behind Traefik, visible in red-monitor, rollback exercised |
| 4 | Form of the standard | A dedicated sub-project (`red-rail`) owning the spec, the templates, a scaffold, an audit, and the review automation; dogfooded on itself |
| 5 | Boundary with brain-v42 | brain keeps the **ledger**; red-rail owns **policy, execution, review** (section 3) |
| 6 | Success criterion | Measured: the four DORA metrics + a conformance score + the number of human gestures per delivery, all derived from ledger evidence |
| 7 | Approach | Declarative rail + a single CLI, borrowing GitHub *environments* (later) and Claude Code skills as facades |

Industry alignment was checked on 2026-09-14: the evidence / policy / execution split is the
supply-chain consensus (SLSA provenance vs verifier vs expectations; in-toto layout vs links;
Sigstore Rekor as a policy-free ledger; OPA decoupling decision from enforcement; Kosli
"change evidence" gates that refuse promotion without evidence). Golden-path literature
(Backstage, Spotify) says a standard is only adopted when packaged as an executable template,
a re-application tool and a conformance scorecard. Single-standard scorecards over-constrain
stubs, hence maturity tiers. DORA 2025 reports AI adoption increasing delivery instability,
hence human gates are not optional.

## 3. Boundary contract: brain-v42 ↔ red-rail

The ledger today is PR-centric: ticket → contract revisions → PR bindings → observed
snapshots (`merged`, `checks.conclusion`, `approved`, `head_sha`, `integration_sha`) →
receipts with two milestones only, `integration` and `fulfilled`. Nothing exists after merge.

### brain-v42 owns (ledger, nothing else)

- Tickets, delivery contracts and revisions, dependencies, PR bindings, GitHub observation,
  receipts, the idempotent event journal.
- The evaluator of the `integration` milestone — *merged + green checks + approval*. This is
  the minimal, generic definition of an integration and stays defensible in a public product.
  It does not grow.
- **One extension, ledger-side**: a generic, append-only **attestation** record — declared
  `kind` (`released`, `deployed`, `rolled_back`, `incident_detected`, `restored`,
  `review_verdict`, `gate_passed`, …), JSON `payload`, `issuer`, `digest`, timestamp,
  idempotency key. brain stores; brain never evaluates these.

### red-rail owns

- Policy: `rail.yaml`, stages, gates, maturity tiers, templates.
- Execution: scaffold, audit, reusable CI, deployment.
- Review automation and **all interpretation**: DORA and conformance are computed by
  `rail metrics` by reading the ledger. brain never computes a delivery metric.

### Two testable boundary rules

1. **brain never learns a new gate.** If a brain-v42 change adds an evaluator error code of
   the form `review_*`, `spec_*`, `deploy_*`, it violates the contract. A red-rail test reads
   brain's error-code list and fails when it moves.
2. **red-rail stores no durable fact outside the ledger.** A `docs/receipts/*` file in a
   repository is a *mirror* of an attestation, linked by digest, never the source.

The eight `codex/delivery-*` branches are triaged with these two rules (most are ledger-side:
freshness, dependencies, API pin; `contract-admission` is the one to examine).

## 4. The rail model

Ten stages. Each is defined by what enters, what leaves, the command that validates it and
the evidence it leaves.

| # | Stage | Produces | Gate `rail check <stage>` | Evidence (ledger) |
|---|---|---|---|---|
| 1 | intent | brain ticket + contract | ticket bound to the repository | contract |
| 2 | design | `docs/specs/<date>-<topic>.md` (+ ADR for durable decisions) | mandatory sections present: problem, decisions, non-goals, success criteria | `gate_passed` |
| 3 | plan | `docs/plans/<date>-<topic>.md` | references the spec; every task has a verification | `gate_passed` |
| 4 | build | commits | tests cover the diff, lint/format, gitleaks, conventional commits in English | CI check |
| 5 | review | verdict | tiered judges + human approval on the PR | `review_verdict` |
| 6 | integrate | merge | brain milestone `integration` (merged + checks + approval) | brain receipt |
| 7 | release | tag + immutable artefact + `docs/receipts/` | version, changelog, artefact digest | `released` |
| 8 | deploy | live service | explicit approval, post-deploy healthcheck | `deployed` |
| 9 | observe | metric visible in red-monitor, rollback exercised | rollback done and measured | `rolled_back` / `restored` |
| 10 | learn | brain learnings/decisions, focus, ticket `fulfilled` | at least one brain entry linked to the ticket | receipt `fulfilled` |

A `hygiene` floor precedes stage 1: valid `rail.yaml`, conforming `CLAUDE.md`, two remotes,
roster entry, `docs/{specs,plans,adr}` layout.

### Maturity tiers

Each tier includes the previous one. The audit scores a project **against its declared tier**,
never against the maximum; changing tier is an explicit edit of `rail.yaml`.

- `bootstrap`: hygiene + stages 1–2 — red-api, red-phone, red-gift.
- `dev`: + stages 3–6 (plan, CI, tests, review) — red-games, red-cli, red-rail itself.
- `prod`: + stages 7–10 (release, deploy, observe, learn) — red-writer, brain-v42, red-monitor,
  and the POC.

### `rail.yaml`

```yaml
rail: 1
project: red-probe
brain_key: red-probe
tier: prod
stack: python        # python | go | docs
deploy:
  target: vps-traefik
  healthcheck: https://probe.hawkixs.com/healthz
gates: {}            # overrides only, each with a mandatory reason; defaults come from the tier
```

Everything absent from this file is a tier default versioned in red-rail. That is what keeps
twenty manifests from diverging.

## 5. Components

```
red-rail/
├── rail.yaml
├── src/rail/
│   ├── cli.py         # rail new | check | audit | attest | deploy | metrics | upgrade
│   ├── model.py       # RailConfig, Tier, Stage, GateResult
│   ├── gates/         # one module per stage: fn(repo) -> GateResult
│   ├── ledger.py      # brain client (MCP Streamable HTTP :8765)
│   ├── scaffold.py    # copier: new + upgrade
│   ├── audit.py       # score per repository × stage, against the tier
│   ├── metrics.py     # DORA + conformance, read from the ledger
│   └── deploy/        # targets: vps-traefik, pc-server-systemd
├── template/          # copier: CLAUDE.md, rail.yaml, docs/{specs,plans,adr}, justfile,
│                      #   .github/workflows/rail.yml, python/go skeletons
├── workflows/         # rail-ci.yml (reusable GitHub workflow), review.js (tiered judges)
├── skills/            # Claude Code facades: rail-design, rail-plan, rail-review, rail-release, rail-deploy
└── tests/
```

Four mechanisms carry everything:

1. **A gate is a pure function of the repository state** → `GateResult{stage, code, passed,
   details}` as JSON, non-zero exit on failure. The *same* code runs locally (pre-push), in CI
   and behind a skill. Policy exists once.
2. **`rail audit projects/*`** produces the repository × stage matrix against each tier — the
   drift table becomes reproducible, versioned, diffable. It also reads `.copier-answers.yml`:
   a repository behind the template is a quantified drift, and `rail upgrade`
   (= `copier update`) resorbs it.
3. **Attestations only come from the server host.** The runner VM cannot reach brain
   (127.0.0.1:8765 on the host) and will not be given access: CI exposes its gates as GitHub
   *checks*, which the brain observer already reads. Post-merge stages (`release`, `deploy`,
   rollback) run from the host, where `rail attest` talks to brain directly.
4. **The review verdict is a schema, not a runner.** red-rail defines `ReviewVerdict` (JSON)
   and `rail attest review_verdict`. The default runner is `workflows/review.js` (a tiered
   Workflow: sonnet fan-out, `wf-judge` on opus to confirm, tiering markers accepted by the
   PreToolUse hook) launched by the `rail-review` skill; an opencode/Codex worker producing
   the same schema is interchangeable.

Skills contain **no rule**: `rail-design` guides the conversation, then calls
`rail check design`. When a skill and the CLI disagree, the CLI is right.

## 6. One delivery end to end: `red-probe` → VPS

`red-probe` is a tiny HTTP service (Python, stdlib or FastAPI — nothing more) with three
routes: `/healthz`, `/version` (git SHA + image digest), `/metrics`. Non-root container behind
Traefik at `probe.hawkixs.com`, no port published directly (red-watcher perimeter). `/version`
is what allows **measuring the drift between git and what is deployed**.

1. **intent** — from the ReD root: `rail new red-probe --tier prod --stack python
   --deploy vps-traefik` creates the tree (copier), both remote repositories (`gh` + GitLab
   API — the 15-step manual kickstart runbook becomes one command), the brain ticket and the
   contract (`brain_delivery_contract_set`). *Human: runs the command.*
2. **design / plan** — skills `rail-design`, `rail-plan`; `rail check design|plan` attests
   `gate_passed`. *Human: arbitrates the design.*
3. **build** — TDD in session or by a worker; PR opened → `brain_delivery_bind_pr`; CI
   (`rail-ci.yml` on the `red-ci` runner) publishes `rail check build` as a check.
4. **review** — `rail-review` runs `review.js` → verdict JSON → `rail attest review_verdict`.
   *Human: approves the PR.* Without an attested verdict, `rail check review` fails.
5. **integrate** — merge; the brain observer sees merged + checks + approval → automatic
   `integration` receipt.
6. **release** — from the host: `rail release` → tag, image built and pushed with digest,
   `docs/receipts/`, `rail attest released`.
7. **deploy** — `rail deploy` refuses without a `released` attestation and asks for explicit
   confirmation. For the POC, **the human approval gate is the operator running the command
   from the host**; GitHub *environments* arrive when deployment moves into CI (phase 2).
   Then SSH to the VPS, `docker compose` on the pinned digest, wait for `/healthz`, compare
   `/version` with the attested digest → `rail attest deployed`.
8. **observe** — `rail check observe` queries red-monitor: the container must appear in the
   VPS Docker collection. Then the **drill**: `rail deploy --rollback` → previous digest →
   `/healthz` → `rail attest rolled_back` + `restored` with `drill: true` so it does not
   pollute the change failure rate.
9. **learn** — brain learnings/decision, `brain_delivery_accept` → `fulfilled`.

`rail metrics red-probe` then reads contract, binding, receipts and attestations and outputs:
lead time (commit → deployed *and* contract → deployed), deployment frequency, change failure
rate (deployed followed by a non-drill rollback within 24 h), recovery time (incident →
restored). No separate instrumentation: the timestamps are those of the evidence.

## 7. Failures, security, degraded modes

Principle: **a gate fails explicitly, never silently; an exception is declared, never hidden.**

- **brain unreachable.** Local gates run without brain. `rail attest` fails with a non-zero
  exit — no spool, no "later": that would be memory outside the ledger in disguise. Sensitive
  case: deployment succeeded, attestation failed. `rail deploy` writes the local receipt first
  (`docs/receipts/…json`, mirror with digest), attests, and on failure exits with a distinct
  `deployed_unattested` code and the exact replay command: `rail attest deployed --from <receipt>`.
  Replay is safe: every attestation carries an idempotency key.
- **GitHub or observer stale.** `rail check integrate` distinguishes *not merged* from
  *stale evidence* (the ledger carries freshness) and triggers `brain_delivery_refresh`.
  `rail release` refuses without a fresh `integration` milestone.
- **VPS deployment breaks.** Bounded sequence: pull → up on digest → `/healthz` (timeout) →
  `/version` equals the attested digest. Any failing step → automatic return to the previous
  digest → `rail attest rolled_back` (*not* a drill) → counts in the change failure rate.
  Never a half-deployed state.
- **Concurrency.** Two sessions deploying the same project: `rail deploy` first takes a brain
  claim (`brain_delivery_claim`, token + fencing epoch — it exists, nothing is reinvented);
  a stale claim cannot release its successor.
- **Security.** No secret in the red-rail tree; tokens through stdin as `runnerctl` does;
  gitleaks in the `build` gate; brain stays on the host loopback; the runner VM has no brain
  or VPS access. Judges read PR content as **data**: read-only tools, enum-valued verdicts,
  no free-form executable text. `review.js` must pass `tiering_gate.py --check` — that is a
  red-rail test.
- **The rail is wrong (false positive).** Fix in red-rail, then `rail upgrade` propagates.
  The only bypass is a `gates:` override in `rail.yaml` with a mandatory `reason`, which
  `rail audit` shows as a *declared exception*. No `# rail: ignore` in code.

## 8. Proof, phasing, non-goals

### red-rail's own tests (TDD)

Every gate tested on fixture repositories (conforming / drifting); the audit matrix as a
golden test over the 24 real projects (day-0 snapshot); the ledger client against a fake
brain (contract tests on tool schemas); **the boundary test** (brain evaluator error-code
list frozen); a fresh scaffold must pass its own `rail check` at `bootstrap`; `review.js`
passes `tiering_gate.py --check`; `rail deploy --plan` dry-run.

### Critical dependency

`brain_delivery_attest` does not exist. It is an append-only table, one tool and one list —
small and purely ledger — but **brain-v42 delivers it**, in its own session, after refuting
the boundary contract. Without it there are no attestations, hence no stages 6–9.

### Phases — each with a measured proof

| Phase | Content | Proof |
|---|---|---|
| 0 — Framing | validated spec; ticket `red → brain-v42` (boundary review + `attest` spec); red-rail bootstrapped (two remotes) | ticket open, repository created |
| 1 — The rail without network | model, gates, audit, scaffold, template, `rail-ci.yml`, skills | `rail audit projects/*` outputs the 24-repository matrix; a fresh scaffold passes `bootstrap`; red-rail passes `dev` |
| 2 — The ledger | brain client, `rail attest`, `review.js` + `ReviewVerdict`, `rail metrics` | idempotent attestations; boundary test green |
| 3 — red-probe | release, VPS deployment, observation, drill, metrics | `/version` equals the attested digest; red-monitor sees the container; recovery time measured; four DORA + 10/10 at `prod`; `brain_delivery_get` shows the full chain to `fulfilled` |

### Non-goals of the POC

Retrofitting the 20 repositories (`rail upgrade` per tier — the standardisation phase, after);
triaging the `codex/delivery-*` branches; GitLab CI parity (GitHub is the authority, GitLab a
mirror); deployment from CI with *environments*; automatic incident evidence from
red-monitor/red-watcher (in the POC, `rail attest incident_detected` is an operator gesture).

### Raised for separate decisions

The ReD root (`ReD_v1/`) is not a git repository: the roster, declared source of truth, is
unversioned and `rail audit` will read it. `red-e2e-target` is on disk but not in the roster.
Both are the operator's call (root work in progress).
