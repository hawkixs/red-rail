# red-rail — guidance for Codex and OpenCode

Read [CLAUDE.md](CLAUDE.md) first: it is the source of this project's architecture, stack,
commands, layout and key technical decisions. This file does not duplicate it — it covers
what changes when the harness is not Claude Code, and the things that can hurt.

Then read the ReD root `AGENTS.md`, in the directory that `CLAUDE.md` names as "Parent project".

## This repository is public

`hawkixs/red-rail` is public (Apache-2.0) so that every project's CI can install `rail`
without a token. **Anything you write here is published.**

- No private IP, no hostname, no token, no path from a personal machine, no internal
  topology detail beyond what the code already needs.
- Drift snapshots go to the ReD root (`make audit` writes into `ReD_v1/docs/audits/`), never
  into this repository — `.gitignore` enforces it.
- Everything is written in **English**: commits, branches, PRs, docs, code comments, test
  names. Conversation with the operator stays in French.

## What this project is, in one paragraph

red-rail owns the **policy** (stages, gates, maturity tiers, templates), the **execution**
(scaffold, audit, CI, deploy) and the **review** automation of the ReD delivery cycle.
brain-v42 owns the **ledger** (tickets, contracts, evidence). red-rail is delivered by its own
rail — dogfooding — and `rail.yaml` declares tier `dev`.

## The invariants you must not break

| Invariant | Why |
|---|---|
| **A gate is a pure function `fn(repo: Path) -> GateResult` and never raises** | The same function runs locally, in CI and behind a skill, so policy exists exactly once. A gate that raises, reads the network, or depends on ambient state breaks all three at once. |
| **brain never learns a new gate; red-rail stores no durable fact outside the ledger** | The two boundary rules with brain-v42. Both are testable — keep them that way. |
| **No `# rail: ignore`** | The only bypass is a `gates:` override in `rail.yaml` with a mandatory reason, reported by `rail check` and `rail audit`. Never hidden. |
| **Secrets live in `~/.config/red-rail/` as 0600 files** | Never in the tree, never in an environment variable holding the value, never on a command line. The brain bearer is read from `RAIL_BRAIN_TOKEN_FILE` only. |
| **Attestations come only from the server host** | The runner VM never reaches brain or the VPS. |
| **One bounded GET, in `src/rail/http.py`** | The only way the rail reaches a network URL that is not brain or GitHub. Timeout and size capped. Do not add a second network path. |
| **Vendored brain contracts are frozen at a tag** | `src/rail/contracts/pins.py`, digest-checked by `tests/test_boundary.py`. Changing them without moving the tag fails CI. |

Deployment specifics worth knowing before touching `src/rail/deploy/`: a private target
refuses — *before the first ssh* — a released compose file that would publish outside
`deploy.bind_address`, `network_mode: host` included, because **Docker bypasses the
firewall**. That refusal is the point of the module, not an edge case.

## Gates

```bash
uv sync --all-extras           # core + dev + brain + reviewer
make ci                        # exactly what CI runs: lint, test, check
uv run pytest -q
uv run ruff check src/ tests/
uv run rail check              # the rail's own gates against this repository
make contracts-check           # vendored brain-v42 contracts equal the pinned tag
```

`make ci` green is the bar. `rail check` must pass on this repository — it is delivered by
its own rail, so a red gate here is a real policy failure, not a nuisance.

Exit code is the verdict; `--json` is the contract for machines. Prefer it when you are
parsing.

## Working style

- Brainstorm → spec → plan → implement. Specs in `docs/specs/`, plans in `docs/plans/`, ADRs
  numbered in `docs/adr/`, all dated `YYYY-MM-DD-<slug>.md`.
- TDD: write the failing test first, watch it fail, implement the minimum. The audit matrix
  is golden-tested (`tests/golden/audit-matrix.json`) — update the golden deliberately, never
  to make a test pass.
- If it derails mid-way, **stop and re-plan immediately**.
- Preserve unrelated user changes. Never stash, reset or overwrite them.
- Never declare a task done without proof: run the tests, read the summary line, capture the
  exit code.
- `docs/receipts/` is the file ledger, written by `rail attest` / `rail contract`. **Never
  edit a receipt by hand** — it is append-only, digest-checked, and fails closed on tampering.

## Brain MCP

Project key `red-rail` (group `red`). Reachable over Streamable HTTP at `127.0.0.1:8765/mcp`
from the home server only; Codex has it wired in `~/.codex/config.toml`.

```bash
uv run rail brain ping   # is brain reachable with the private token, as this project?
```

Start material work with
`brain_session_start("red-rail", client_key="<harness>-red-rail-<YYYY-MM-DD>")`, where
`<harness>` is codex or opencode here (claude-code under Claude Code). Reuse the key for every
retry of that session, and give a parallel session its own suffix. Persist knowledge with the
specific tool, never `brain_learn` by default: `brain_log_decision` for a choice,
`brain_save_snippet` for a reusable pattern, `brain_create_runbook` for a procedure,
`brain_propose_adr` for durable architecture, `brain_learn` only as a last resort.

## Subagents

Every subagent prompt names its perimeter — a concrete path or glob, an explicit budget, or
an explicit output contract. Cost tracks the number of subagents spawned and the size each
one accumulates, not session length. Name the files you already know instead of asking an
agent to find them.

`workflows/pre-review.js` is a tiered pre-review launched from the producing session. **A
pre-review improves the PR; it never satisfies the review gate.** `review.verdict` accepts
only an independent, approving verdict issued by `review.reviewer_identity` — by default
`red-rail-reviewer`, running as its own service, never the session that wrote the code.
