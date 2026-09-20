# red-rail

The delivery rail of the ReD ecosystem: one standard for the whole lifecycle — idea to
production — with executable gates and measured drift.

- **Policy** (stages, gates, maturity tiers, templates), **execution** (scaffold, audit, CI,
  deploy) and **review** automation live here.
- Evidence lives in a **ledger**. By default (`ledger: file`) the repository's own receipts are
  the ledger and red-rail needs no other service. With `ledger: brain`, brain-v42 becomes the
  shared, cross-project, observed ledger. See
  [ADR-0001](docs/adr/0001-ledger-in-brain-policy-in-red-rail.md) and
  [ADR-0002](docs/adr/0002-pluggable-ledger-standalone-first.md).
- Design: [docs/specs/2026-09-14-red-rail-design.md](docs/specs/2026-09-14-red-rail-design.md).

## Usage

```bash
uv sync --all-extras              # core + dev + brain (MCP client) + reviewer (GitHub App, judges)
uv run rail check                 # the gates of the declared tier; exit code is the verdict
uv run rail check design --json   # one stage, machine-readable
uv run rail check --ci            # CI scope: workstation-only and, with ledger: brain, ledger gates are skipped
uv run rail audit ..              # repository × stage matrix over the sibling projects
uv run rail contract set --objective "…" --reason "…" \
  --required-check check_run:red-rail/review@red-rail-reviewer --allowed-reviewer "red-rail-reviewer[bot]"
uv run rail attest deployed --data sha=<sha> --data digest=<digest>   # evidence, from the host
uv run rail attest deployed --from docs/receipts/<receipt>.json       # replay after exit 2 (mirror written, brain refused)
uv run rail ledger list           # read the evidence back (brain in brain mode)
uv run rail metrics               # four DORA metrics + conformance, from the ledger
uv run rail brain ping            # is brain-v42 reachable with the private token, as this project?
uv run rail reviewer once --repository hawkixs/red-rail --pr 7   # the independent reviewer, on the host
uv run rail new red-probe --description "…" --tier prod --stack python   # scaffold + remotes
uv run rail upgrade --repo ../red-probe   # re-apply the template's latest tag
```

### The shared ledger (`ledger: brain`)

`rail.yaml` names the delivery ticket (`ticket: <uuid>`, the canonical ticket is
`red → <project>`). The bearer token is read from a private file only — `~/.config/red-rail/brain-token`
(0600, one line), path overridable with `RAIL_BRAIN_TOKEN_FILE` — never from an environment
variable holding the value. Every attestation is written as a receipt in `docs/receipts/` first
(the mirror), then sent to brain; a refusal leaves the mirror and exits 2 with the exact replay
command. `integrated` and `fulfilled` are brain milestones read from the ticket, never attested
from the rail. brain-v42's published contracts are vendored under `src/rail/contracts/` at the tag
`delivery-attestations-v1.0` and frozen by `tests/test_boundary.py`.

### The independent reviewer

`rail reviewer` runs on the host, in pull mode, as the GitHub App `red-rail-reviewer`
(`~/.config/red-rail/reviewer.yaml` and `reviewer-app.pem`, both 0600, outside any tree). One
review per head SHA; judges run through `headless-agents` in an isolated seat, never the
producer's provider; the verdict is the `red-rail/review` check plus a PR review, attested
`review_verdict`. A review launched from the producing session (`rail-review` skill,
`workflows/pre-review.js`) is a pre-review and never satisfies the gate.

## Development

```bash
make ci                      # lint, test, check — exactly what CI runs
make audit                   # dated drift snapshot, written to the ReD root (outside this repository)
make skills-install          # facade skills into ~/.claude/skills
make contracts-check         # vendored brain-v42 contracts == the pinned tag (needs the sibling checkout)
```

Public repository under the Apache License 2.0 (`LICENSE`, `NOTICE`). Canonical remote:
GitHub `hawkixs/red-rail`.
