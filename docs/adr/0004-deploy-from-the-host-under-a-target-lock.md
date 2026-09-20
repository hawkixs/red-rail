# ADR-0004: Deployment runs from the host over ssh, pins the artefact by digest, and is fenced by a lock on the target

- **Status**: accepted (2026-09-19)
- **Brain**: red-rail decisions `8faab5a3` (artefacts on GHCR), `c8b0ea45` (receipts in a dedicated PR)

## Context

Spec §6 steps 6–8 and §7 describe release, deployment, the rollback drill and their failure
modes, and name `brain_delivery_claim` as the fence between two concurrent deployments.
Measured on 2026-09-19: the border VPS runs Traefik v2.11 as the only public entry
(`exposedByDefault=false`, network `pls_project_default`, certresolver `letsencrypt`); the
neighbours deploy a compose file per release under `/opt/<name>` and pin their image by
digest (red-gift); red-monitor reports the image reference of every container; the brain
claim's `work_kind` vocabulary is `implement | repair | review | integrate | accept` — no
deployment; GitLab does not follow the move to red-base and its registry lives on a home
machine.

## Decision

- The artefact is an OCI image on GHCR (`ghcr.io/hawkixs/<project>`), named by its manifest
  digest; `rail release` builds and pushes it from the host, the token on stdin, and tags
  `v<version>` on both remotes. The `released` attestation is the only source `rail deploy`
  reads.
- `rail deploy` runs from the host with the operator's ssh: one bash script on stdin per
  phase, under `flock` on `/opt/<project>/.deploy.lock` (exit 75 → `rail deploy` exit 3).
  The script writes the project's `deploy/compose.yaml` at the released commit and a `.env`
  with the digest, pulls by digest, `up --wait` on the container's healthcheck, moves
  `current`. Verification is external: `/healthz` then `/version` through Traefik must equal
  the artefact (version, commit, digest).
- Never a half-deployed state: a failed forward deployment puts the previous artefact back
  and writes `incident_detected(automatic)` → `rolled_back(automatic)` → `deployed(rollback)`
  → `restored`. The newest `deployed` record always names the live digest (`mode`
  = `release | rollback | drill`); metrics count release-mode deployments only.
- `brain_delivery_claim` is not used for deployments: it has no deployment work kind, and a
  fence must live where the state lives. `brain_delivery_refresh` is not used: receipts are
  immutable; `rail release` requires an `integrated` record on HEAD's history.
- Post-merge receipts travel in a dedicated docs-only pull request (decision `c8b0ea45`).

## Consequences

- Easier: one target definition as data (`GATE_DEFAULTS`), overridable with a reason; the
  same command deploys, rolls back and drills; every state change is evidence.
- Harder: one `docker login ghcr.io` per VPS; the host needs docker and ssh; a second target
  (`pc-server-systemd`) is a new module, not a new rule.
- Spec §7 "Concurrency" is amended by this ADR; §6 step 7 keeps its meaning.
