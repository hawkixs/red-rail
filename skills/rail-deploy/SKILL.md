---
name: rail-deploy
description: Deploy a released ReD project to the border VPS behind Traefik with `rail deploy` (digest-pinned compose over ssh, /healthz then /version verified through the public route), roll back with `rail deploy --rollback`, exercise the drill with `rail drill`, then confirm with `rail check observe`. Use from the host, never from CI, never `docker compose` by hand.
---

# rail-deploy

Stages 8 and 9 run from the host over the operator's ssh; the ledger is the rollback source.

A private target behind a site (`deploy.site` in `rail.yaml`) needs its address on this host,
never in the repository: `~/.config/red-rail/sites.yaml`, mode 0600, holding
`sites: {<site>: {address: "<ip>"}}` (`RAIL_SITES_FILE` names another path). Without it,
`rail deploy --plan` stops before printing a step, naming the site and the file. In
`rail.yaml`, `deploy.site: <site>` names the site, and its healthcheck uses the rail's token
as the host (`http://${BIND_ADDRESS}:<port>/<path>`); records redact that site's address
only. The same file declares the site `red-monitor` (`observe.monitor_site`), which
`rail check observe` needs.

A `private-systemd` target (`deploy.unit`, `deploy.binary`) delivers a binary that systemd
runs. The release is the usual image: the target copies the binary out of it by digest. The
host needs two things, once: `/etc/systemd/system/<unit>` linked to
`/opt/<project>/current/<unit>`, and a sudoers rule allowing exactly
`systemctl daemon-reload` and `systemctl restart <unit>` to the deploy account (red-watcher
installs it). `--plan` shows the restart in the ssh step.

1. Preview: `rail deploy --repo <path> --plan` (the remote script and the checks, nothing runs).
2. Deploy: `rail deploy --repo <path>` — refuses without a `released` attestation, asks for
   confirmation, verifies `/version` equals the artefact, attests `deployed`. A failed
   deployment puts the previous artefact back by itself when one exists (else the incident stays open) and exits 1. Exit 2 = live but unattested: run every
   `rail attest … --from` line printed. Exit 3 = another deployment holds the lock.
3. Observe: `rail check observe --repo <path>` — red-monitor sees the container with the deployed
   digest; the drill gate needs step 4.
4. Drill (two deployed artefacts needed): `rail drill --repo <path>` — incident simulated,
   rollback, recovery measured, roll-forward; every record is marked `drill`.
5. Real incident: `rail attest incident_detected --repo <path> --data …`, then
   `rail deploy --repo <path> --rollback`. Never `docker compose` on the VPS by hand: the rail
   would not know. When this skill and the CLI disagree, the CLI is right.
