# Delivery metrics: definitions that make the four DORA figures mean something

Status: approved — 2026-10-14 (operator decision `23ab89af`: "j'aimerais bien qu'on fasse bah,
toutes tes recos là, parce qu'il y a pas mal de trous en fait"; D7 decision `3a45ddce`:
"Ok, B.")

Motivation: lowers the operator load of reading delivery figures that cannot be trusted
(ticket `c5231125`, measurement class); follows the first analysis of the published metrics
(2026-10-14) and ticket `915a4ffa`.

## Problem

The first real publication (2026-10-14, 11/11 repositories) showed that only the deployment
count is reliable:

- lead time starts at the committer date of the deployed SHA, which is the release commit:
  brain-v42 reads 3 minutes while 0.6.10 waited 55 h between release and deployment;
- a redeployment of the same artefact counts as a new deployment (21 events for 16
  artefacts over 30 days);
- the change failure rate counts only rollbacks: two production failures with no rollback
  (brain-v42 0.6.10, red-alerts 0.1.0) leave it at 0;
- a failed deployment whose rollback is impossible leaves its incident open for ever, and a
  `restored` attested later would carry the attestation time, not the recovery time;
- six repositories have no deploy target and simply vanish from the delivery block.

## Decisions

D1 — Change. A change is the first release-mode `deployed` of an artefact digest on the
manifest's target, over the whole ledger. A later release-mode `deployed` of the same digest
is a redeployment: counted in `redeployments`, never in `deployments`, lead time or CFR.

D2 — Lead time for changes (DORA). For a change C whose previous change on the target is P,
the commits included are `git rev-list --no-merges P.sha..C.sha` read on
`refs/remotes/origin/main`; lead time = C's time minus the committer date of the oldest of
them. With no P, a P that is not an ancestor of C, or a range git cannot read, the change has
no lead time (never a guess).
The window publishes the median. Release-to-deploy (C's time minus the `released` record of
the same digest) is published beside it.

D3 — Change failure rate. A change failed when a non-drill `incident_detected` names its
digest, or a non-drill `rolled_back` names it (`from_digest`), or, for records without a
digest, a non-drill incident or rollback falls within 24 h after it: such a record is
attributed to one digest only, that of the newest release-mode deployment (a first
deployment or a redeployment) preceding it by at most 24 h, and fails that digest's change
without adding an attempt. A failure is counted once
per digest: an incident raised while a digest is redeployed counts against that digest's
change. A failed candidate is a digest named by a non-drill incident or rollback that has no
release-mode `deployed`; it counts as one attempt and one failure. Window membership: a
change belongs to the window holding its first deployment, a failed candidate to the window
holding its first incident or rollback. CFR of a window = its failed changes and failed
candidates / its changes and failed candidates; a failure recorded later still fails its
change in that change's window (DORA: the share of changes that failed).

D4 — Recovery. A non-drill `restored` may carry `incident` (the record digest of the
incident it closes), `restored_at` (event time, UTC) and `evidence` (the record digest of
the healthy deployment that proves it). `restored_at` is used only when
incident time ≤ `restored_at` ≤ the record's `recorded_at`, else the record time. A
`restored` naming an incident closes that one; otherwise the oldest open incident before
it. Matching runs over the whole ledger; window membership follows the incident's
`recorded_at`. Time to restore of a window = the median over its closed incidents; its
`open_incidents` = its incidents still open at publication time. Durations keep the second: the contract carries minutes, not hours.

D5 — `rail deploy` closes what it opened. A successful forward deployment closes every open
non-drill incident of the project with a `restored` carrying `incident`, `restored_at` (its
own deployment time) and `evidence` (its `deployed` record). Automatic rollback keeps its
current records and gains `incident`. A manifest declares one deploy target, so the project and its target are the same unit.
Same flow on every target; red-backup
(`private-timers`) is the first to be checked by a deploy after the merge.

D6 — No target, no invention. A repository without a deploy target is published in the
delivery block as `status: no_deployment` with null figures; a repository that could not be
read stays in `coverage.missing` with the failing step (`fetch`, `manifest`, `ledger`,
`github`) logged on stderr, never in the public answer (ticket `915a4ffa`).

D7 — Contract. D1–D6 add fields and change meanings, so the delivery block becomes
`rail-delivery/v2` (autonomy stays `rail-autonomy/v1`). For one release the answer carries
both blocks: `delivery` stays v1, unchanged in shape and meaning, and `delivery_v2` carries
v2; the release after removes the v1 block and `delivery_v2` keeps its key (decision
`3a45ddce`, option B). red-monitor reads v2 under its own ticket.

D8 — The two open incidents (brain-v42 `9039917f`, red-alerts `8c53dc8f`) are closed by hand
after the merge with `rail attest restored` carrying `incident`, `restored_at` and
`evidence` = the healthy deployment that followed (`8b42bdbcd0e4` at 18:35:19Z,
`2380b37baf6a` at 18:29:13Z). The brain-v42 receipt rides the next brain receipts pull
request.

## Non-goals

- No incident detection outside `rail deploy`: a runtime outage is attested by hand with
  `rail attest incident_detected` until red-monitor or red-alerts attest it (later spec).
- No backfill or rewrite of existing records: they are append-only.
- No deploy target invented for the six repositories without one.

## Success criteria

- Republished on the 2026-10-14 ledgers: brain-v42 shows 7 changes and 1 redeployment over
  30 days, a lead time in hours from its oldest included commit, CFR > 0; red-alerts shows
  0.1.0 counted once.
- After D8, time to restore reads 18.07 min (brain-v42) and 1.18 min (red-alerts), never
  the attestation date.
- Every repository appears in the v2 delivery block, with `no_deployment` where it applies.
- Tests pin D1–D6 on fixtures, and D7: the answer carries `delivery` unchanged beside
  `delivery_v2`, both validated by the contract; `make ci` green; an independent non-codex judge approves.
