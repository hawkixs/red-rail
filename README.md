# red-rail

The delivery rail of the ReD ecosystem: one standard for the whole lifecycle — idea to
production — with executable gates and measured drift.

- **Policy** (stages, gates, maturity tiers, templates), **execution** (scaffold, audit, CI,
  deploy) and **review** automation live here.
- The **ledger** (tickets, contracts, evidence, attestations) lives in brain-v42. See
  [ADR-0001](docs/adr/0001-ledger-in-brain-policy-in-red-rail.md).
- Design: [docs/specs/2026-09-14-red-rail-design.md](docs/specs/2026-09-14-red-rail-design.md).

## Usage

```bash
uv sync --extra dev
uv run rail check            # run the gates against the current repository
uv run rail check --json     # machine-readable report; exit code is the verdict
```

## Development

```bash
just ci                      # lint, test, check — exactly what CI runs
```

Private repository. Canonical remote: GitHub `hawkixs/red-rail`; mirror: GitLab
`hawkixs_project/red/red-rail`.
