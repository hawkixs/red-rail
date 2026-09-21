# `private-compose`: deploying where there is no public route

Status: proposed — 2026-09-21

## Problem

red-base (OVH VPS-4, `10.100.0.4`) is the first machine where the rail can be the only door
from the start, and the operator requires it: everything deployed there goes through red-rail,
without exception, and the requirement must be *verifiable, not declarative* (decision
`5dd6da0e`). Two projects are queued behind it — red-alerts, rewritten in Go as the pilot, and
brain-v42, whose port the operator is preparing.

The rail cannot deploy there. `deploy/flow.py:35` refuses every target but `vps-traefik`:
`pc-server-systemd` is an enum value with no module behind it, and its name describes a
mechanism (systemd) that neither project needs — both want compose.

`vps-traefik` cannot be bent into the job, and bending it would be the declarative conformance
the requirement forbids. It is coupled to the border VPS in two ways that do not survive the
move:

- it writes Traefik routing into the deployment's `.env` (`TRAEFIK_NETWORK`,
  `TRAEFIK_CERT_RESOLVER`, `DOMAIN`), and red-base runs no Traefik;
- it verifies the result **through the public route** — `GET https://<domain>/healthz`, then
  `GET https://<domain>/version` compared to the artefact. red-base has no public port by
  construction. That is the point of the machine, not an omission.

Overriding `deploy.ssh_host` reaches the machine and changes nothing about either: the
verification would still ask the internet about a service the internet cannot see.

There is a third problem the border VPS never had. **Docker bypasses ufw**: a `-p 8080:8080`
in a compose file publishes on every interface, including the public one, whatever the
firewall says. red-base defends against this twice already (`daemon.json` pins the daemon's
address, the `DOCKER-USER` chain drops on the public interface), but both defences live in the
machine's configuration, where the rail cannot see them and a future change can silently
remove them. A deployment that publishes publicly would satisfy every gate the rail has today.

## Decisions

1. **A new target named for its shape, not for the machine: `private-compose`.** The property
   that defines it is "compose, no public route, verification over the private address". red-base
   satisfies it; the home server will satisfy it too. Naming it `red-base` would produce one
   module per machine sharing almost all of their code, with the standing question of which copy
   to fix. The machine stays what it already is — the `deploy.ssh_host` parameter, overridden per
   project in `rail.yaml` with its mandatory reason.

2. **The shared mechanics move to `deploy/compose.py`; the two targets keep only what differs.**
   The lock on the target, the `releases/<version>/{compose.yaml,.env}` layout, the `current`
   symlink, reading the compose file at the released commit, the digest-pinned pull,
   `up --detach --wait`, and the whole `apply` / `verify` / `live_version` sequence are identical
   and stay written once. A target supplies two things: the environment its deployment needs, and
   the origin its verification talks to. Duplicating the remote script instead would recreate the
   very failure mode this rail exists to prevent — two copies of a deployment path, drifting.

3. **Verification uses the origin of `deploy.healthcheck` itself.** `vps-traefik` derives
   `https://<domain>` from the healthcheck's hostname; `private-compose` keeps the scheme, host
   and port it was given, so `http://10.100.0.4:9100/healthz` is followed by
   `http://10.100.0.4:9100/version`. The `/version` contract is unchanged — HTTP 200, a JSON
   object with `project`, `version`, `git_sha`, `image_digest`, the last three compared strictly
   against the artefact. `rail.http` already accepts `http://` and does not filter private
   addresses, and `deploy.healthcheck` already validates against `^https?://`.

4. **The target refuses a compose file that would publish on every interface.** Before anything
   is written to the machine, every published port of the released compose file must carry an
   explicit host address, and that address must be the target's `deploy.bind_address` or a
   loopback. A bare `"8080:8080"` is refused by name — service and port — and nothing is
   deployed. This is what turns "red-base has no public port" from a property of the machine's
   configuration into a property the rail checks on every deployment, which is what the operator
   asked for. It is also the strongest argument that this is a distinct shape and not a
   parameterisation of the existing one.

5. **`deploy.bind_address` is a new policy parameter, with no default.** A target that cannot
   say which address it publishes on cannot make decision 4, so its absence is an error at
   deployment time rather than a silent fallback. `deploy.traefik_network` and
   `deploy.cert_resolver` are read only by `vps-traefik` and gain no meaning here.

6. **`observe.monitor_agent` is declared per project.** `observe.visible` compares the digest
   red-monitor reports with the one the ledger says is live; on red-base that means an agent
   named for the machine rather than the default `vps`. The inverted witness is already in the
   scope of `5dd6da0e`, so this is a prerequisite already planned, not a new one.

## Non-goals

- **`pc-server-systemd` is not implemented, and is not renamed.** It stays an enum value with
  no module. Nothing in flight needs systemd; when something does, it will be a third shape
  under decision 1, judged by the same test — does the verification step change?
- **No fourth tier.** "Production but not public" is this target combined with `tier: prod`,
  which is already settled (decision `e23d92d7`). `TIER_STAGES` is untouched.
- **Stateful services and schema migrations are out of scope.** The rollback model here is the
  one the rail already has: re-apply the previous digest. For a service carrying a database
  with a forward migration — brain-v42 and its Alembic head — that is the old code against the
  new schema, and a stage-9 drill would prove it at the worst moment. brain-v42 must not be
  deployed by this target until that is designed. red-alerts is stateless, which is what makes
  it the right pilot.
- **No gate reads compose files.** Decision 4 lives in the target, where the bind address is
  known. Moving it earlier, into `rail check`, is plausible later and is not attempted now.
- **No change to the attestation vocabulary.** `deployed`, `rolled_back`, `incident_detected`,
  `restored` keep the meanings the deploy flows already give them.

## Success criteria

- `rail deploy --plan` against a `private-compose` manifest prints the ssh step, the healthcheck
  step and the `/version` step, all three naming the private address, and no Traefik label or
  certificate resolver appears anywhere in the rendered `.env`.
- A compose file publishing `"8080:8080"` is refused before the first ssh, naming the service
  and the port; the same file bound to `deploy.bind_address` or to `127.0.0.1` is accepted.
- `tests/test_deploy_vps_traefik.py` passes **unchanged**. The existing target is deployed and
  proven end to end on red-probe; the extraction is refactoring, and its own tests are the
  proof it changed nothing.
- `deploy/flow.py` builds either target from the manifest, and its forward, rollback and drill
  sequences write the same attestations for both — no branch on the target inside the flows.
- `rail check` stays 18/18 on this repository, and `make ci` is green.
