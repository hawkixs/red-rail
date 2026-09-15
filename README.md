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
uv sync --extra dev
uv run rail check                 # the gates of the declared tier; exit code is the verdict
uv run rail check design --json   # one stage, machine-readable
uv run rail audit ..              # repository × stage matrix over the sibling projects
uv run rail contract set --objective "…" --reason "…"   # stage 1: the delivery contract
uv run rail attest deployed --data sha=<sha> --data digest=<digest>   # evidence, from the host
uv run rail ledger list           # read the evidence back
uv run rail metrics               # four DORA metrics + conformance, from the ledger
uv run rail new red-probe --description "…" --tier prod --stack python   # scaffold + remotes
uv run rail upgrade --repo ../red-probe   # re-apply the template's latest tag
```

## Development

```bash
make ci                      # lint, test, check — exactly what CI runs
make audit                   # dated drift snapshot under docs/audits/
make skills-install          # facade skills into ~/.claude/skills
```

Private repository. Canonical remote: GitHub `hawkixs/red-rail`; mirror: GitLab
`hawkixs_project/red/red-rail`.
