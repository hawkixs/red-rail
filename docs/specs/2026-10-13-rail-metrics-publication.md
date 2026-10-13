# Rail metrics publication: delivery (DORA) and autonomy blocks for red-monitor

Status: draft — 2026-10-13. Scope ruled by the operator ("ok reco", decision `35427fd3`,
typed in the coordination session under delegation `d47ac557`): no conformance in the v1
block, windows of 7 and 30 days, a red-monitor tab first, user units with a single stale alarm
beyond 2 h. Method `spec`. The open questions of the design review were ruled the same way
("Ok, go pour tes recos", decision `69ee7d9c`): per-repository 30-day windows with a 7-day
total, no contract-to-deploy lead time, the reviewer's repository list and GitHub App, the
gesture kinds below, `since` = 2026-10-04, and a `null` total on a partial read.

Motivation: makes the operator load the process induces visible (ticket `c5231125`, T1), next to
the delivery performance the rail already measures, where the operator already looks
(red-monitor). The retro of 2026-09-25 (learning `570503a3`) found that what the rail measured
got optimised, and that the measure of operator-free delivery was never built.

## Problem

`rail metrics --repo <r>` computes the four DORA metrics and a conformance score for ONE
repository, from its ledger, and prints them. Nothing aggregates the rail's repositories,
nothing publishes, and no metric describes autonomy, although the evidence for part of it
exists: since red-rail#78 (2026-10-04) every attestation carries its actor
(`operator`, `agent:<harness>[:<session>]`, `service:<name>`), and the independent reviewer
records every verdict and ruling.

red-monitor shows other sources through one pattern (`contracts/programmes-v1`): a publisher
answers an unauthenticated JSON GET behind the host's private network. red-monitor's agent
probes it, the server allowlists the (agent, service) pair and relays one block per
`/api/v1/<name>`, read-only and computing nothing. Brain cannot be the publisher, since ADR-0001
says brain never computes a delivery metric. The rail must publish its own.

## Decisions

D1 — **Two blocks, one endpoint.** `GET /rail-metrics` answers a JSON object with at most two
members: `delivery` (version `rail-delivery/v1`) and `autonomy` (version `rail-autonomy/v1`,
carrying `"experimental": true`). Each is present or absent independently, and the answer is `{}`
when nothing was ever published. red-monitor relays `delivery` at `/api/v1/rail-delivery` and
`autonomy` at `/api/v1/rail-autonomy`. The block contracts below are owned by red-rail, and their
fixtures (`full.json`, `delivery-only.json`, `partial.json`, `empty.json`, `unknown-version.json`) and a
README live in this repository, under `contracts/rail-metrics-v1/` at the repository root (`src/rail/contracts/` stays the
vendored brain contracts). They ship WITH this spec,
so the contract is frozen before any consumer is coded. red-monitor copies them from a pinned
red-rail revision and owns its relay envelope.

D2 — **Block envelope.** Every block carries `version` (the major is checked by consumers),
`generated_at` (RFC 3339 UTC, the end of the `publish` computation; it is the freshness of the
data, distinct from red-monitor's `source_age_s`), and `windows_days: [7, 30]`. No block's top
level ever carries `served_at`, `source_age_s`, `source`, `available`, `reason` or `payload`:
those keys are reserved to red-monitor's relay. Units are in the field names (`_hours`,
`_minutes`, `_per_week`, `_ratio`, or a plain count). An unknown value is `null`, never `0`.

D3 — **`rail-delivery/v1`.** One entry per repository whose `rail.yaml` declares tier `prod` and
a deploy target. Today that is red-probe, red-life, red-alerts, brain-v42 and red-backup.
`projects` is a list sorted by project name, of
`{"project", "target", "d7": W, "d30": W}`, where `W` holds:
- `deployments` (count);
- `deployment_frequency_per_week`;
- `lead_time_commit_to_deploy_hours` (median);
- `change_failure_rate`;
- `recovery_time_hours` (median);
- `drill_recovery_time_minutes`.

Each value is what the existing `compute_metrics` returns for that window. Its semantics are kept
as they are and stated here, not changed:
- `change_failure_rate` is failed artefacts over attempts, both from the window's records.
  An artefact fails when a non-drill `rolled_back` recorded in the window names it by
  `from_digest`, whatever its age; the 24 h window applies only to a rollback without a digest,
  which then fails the window's latest deployment before it, if that deployment is at most
  24 h older. Attempts are `len(deployed) + len(failed - went_live)`, where `deployed` counts
  the window's deployment records (two deployments of one digest are two attempts) and
  `went_live` is the set of their distinct digests: the window's deployments, plus every
  failed artefact the window did not deploy, a candidate that never went live as well as an
  artefact deployed before the window. A deployment on 30 September rolled back by name on
  10 October therefore reads, in the 7-day window of 13 October, `deployments: 0` and
  `change_failure_rate: 1.0`.
- `compute_metrics` also runs the gates for its conformance score. `publish` computes the
  delivery values without running any gate, through a split of that function that leaves
  `rail metrics` unchanged.
- `lead_time_contract_to_deploy` is left out of v1: contracts are written or revised after the
  work (`570503a3`), so the number misleads.
- Conformance is left out (ruling `35427fd3` (i)).

D4 — **`rail-autonomy/v1`** (experimental). It covers every repository on the rail (D5), and its
top level holds `since`, `coverage`, `total`, `projects` and `not_measured`:
- `since` is the earliest date from which the actor is recorded (2026-10-04T00:00:00Z). Nothing
  recorded earlier is counted, and each window reports its effective start as `window_start`.
- `coverage` is `{"expected": n, "read": n, "missing": [project, ...]}`, the repositories this
  run could not read.
- `total` holds `{"d7": A, "d30": A}` over all repositories, or `null` when `missing` is not
  empty: a total over part of the rail would read like an inactive project.
- `projects` lists `{"project", "d30": A}` for each repository read with any counted activity in
  the last 30 days, sorted by name. The 7-day window appears only in the total, to hold the size
  budget (D6).
- `not_measured` is a fixed list naming what v1 cannot measure: operator questions and
  decisions, active and waiting human time, and the justified-versus-avoidable classification.
  T1 covers them, and Q1 is still open. v1 publishes their absence rather than a proxy.

Shared rules for `A`:
- A ledger count that is not a review metric (`releases`, gestures, `rollbacks`, `incidents`)
  takes a record when its `recorded_at` is at or after the window's `window_start`, which is
  max(now − N days, `since`).
- A *gesture* is an attestation of kind `released`, `deployed`, `rolled_back`, `restored` or
  `review_ruling`, drills included: a drill also costs the operator's attention. A ruling the
  operator issued is therefore one of their gestures, in the window where it was recorded. Contracts and
  `fulfilled` are excluded, because the ledger does not keep their actor (brain returns the
  contract's author project and attributes `fulfilled` to itself).
- Its actor class comes from the attestation's `issuer`: `operator`, `agent:*`, or other
  (`service:*`, the reviewer), which counts for neither.
- Review metrics select pull requests, then read their whole history. The *population* is the
  set of pull requests merged in the window (by merge time) whose first verdict was recorded at
  or after `since`; a pull request whose history began earlier is left out, because its first
  pass is not visible. Each selected pull request then brings every verdict and ruling recorded
  for it since `since`, including those before `window_start`: a pull request first judged on
  5 October and merged on 10 October counts its first verdict in the 7-day window that starts
  on 6 October. `reviewed_prs` is the population's size; `merged_prs` (GitHub) also counts the
  pull requests left out.
- A *judged verdict* is a `review_verdict` whose round is 1, 2, 3 or `closure` and whose
  providers list is not empty. Mechanical, `no_verdict` and `awaiting_ruling` verdicts are not
  judged verdicts, and a judge-less closure is not one either.
- A median is the statistics median and p90 is the nearest-rank 90th percentile, both over the
  population. Ratios are rounded to 3 decimals and hours to 2. An empty population or
  denominator gives `null`.
- The total aggregates per-repository samples: counts sum, and medians, p90 and ratios are
  recomputed over the union of samples, never averaged.

| field | definition | source |
|---|---|---|
| `window_start` | effective start of the window | — |
| `releases` | `released` attestations | ledger |
| `operator_gestures` | gestures issued by `operator` | ledger |
| `operator_gestures_per_release` | `operator_gestures` / `releases` | ledger |
| `agent_gesture_ratio` | `agent:*` gestures / (`agent:*` + `operator` gestures), over `released`, `deployed`, `rolled_back` and `restored` | ledger |
| `merged_prs` | pull requests merged into the default branch in the window | GitHub |
| `reviewed_prs` | size of the population | ledger, GitHub |
| `rulings_per_reviewed_pr` | `review_ruling` attestations of the population's pull requests / `reviewed_prs` | ledger, GitHub |
| `agent_rulings` | those rulings issued by `agent:*` (fix rulings under a delegation) | ledger |
| `review_rounds_median`, `review_rounds_p90` | judged verdicts per pull request of the population | ledger |
| `first_pass_approval_ratio` | pull requests of the population whose first judged verdict approved / those with at least one judged verdict | ledger |
| `awaiting_ruling_passes` | `awaiting_ruling` verdicts of the population's pull requests | ledger |
| `ready_to_merged_hours_median` | from the last ready-for-review event (or creation for a pull request never drafted) to merge, over `merged_prs` | GitHub |
| `rollbacks` | `rolled_back` not marked `drill` | ledger |
| `reverts` | default-branch commits in the window whose message starts with `Revert "` | GitHub |
| `incidents` | `incident_detected` not marked `drill` | ledger |

`incidents`, `rollbacks`, `first_pass_approval_ratio` and the delivery block's
`change_failure_rate` are the quality guards a consumer shows next to the autonomy measures
(note `17f0d06a`): a drop in supervision obtained by weaker quality must be visible.

D5 — **Sources.** The repository list and checkouts are those the reviewer already watches
(`~/.config/red-rail/reviewer.yaml`: slug and path; red-rail's own checkout included). The
ledger is opened with `open_ledger` read-only, as the reviewer's identity and token (red-rail#97).
GitHub is read with the reviewer App's installation token. The minimal client gains read
calls: merged pull requests with their dates, ready-for-review events, the default branch and
its commits in the window. Pagination is complete, and an incomplete answer is a failed read,
never a short count. The same holds for the ledger: a page of `brain_delivery_attestation_list`
without an `items` list, or with a malformed one, fails the repository's read (today
`BrainLedger` reads `page.get("items", [])`, which turns a malformed `{}` into no activity). `publish` never writes to a checkout, a ledger or GitHub.

D6 — **Size.** The serialized answer, both blocks together, is at most 12 KiB (red-monitor stores
it whole on every probe, with a 16 KiB per-service budget). `publish` measures the compact JSON
before writing and refuses to write a larger one: the previous file is kept, and the command
exits non-zero naming the size. It never truncates silently.

D7 — **Publish, and a strict contract.** Both blocks are Pydantic models with `extra="forbid"`
and strict, typed, bounded fields (`strict=True`: a string or a boolean is never coerced into a
number): names match `^[a-z0-9][a-z0-9.-]{0,63}$`; counts are integers ≥ 0; proportions
(`change_failure_rate`, `agent_gesture_ratio`, `first_pass_approval_ratio`) lie in [0, 1];
quotients (`deployment_frequency_per_week`, `operator_gestures_per_release`,
`rulings_per_reviewed_pr`), medians, p90, hours and minutes are ≥ 0; any of them may be `null`. `rail metrics publish` builds both blocks
through these models, so an unexpected field, a reserved relay key or an invalid value fails the
run. It then writes them atomically (temporary file, `fsync`, rename) to
`$XDG_STATE_HOME/red-rail/metrics.json`, mode 0644, in a 0700 directory. A repository whose
ledger or GitHub read fails is listed in `coverage.missing` and left out of `projects` and of
the delivery block. If no repository can be read, nothing is written. A user timer runs it
hourly.

D8 — **Serve.** `rail metrics serve` binds `127.0.0.1:9230` only: the host is a tailnet node, and
any wildcard bind would expose it there. red-monitor's agent on the same host runs under systemd
and reaches loopback. It reads the file, validates it strictly against the D7 models and the D6 cap, and answers
`GET /rail-metrics` with the compact serialization of the validated models, never the file's
own bytes, with `Content-Type: application/json` and `Cache-Control: no-store`, or `{}` when the
file is absent. A file that fails answers 503 with `{}`: it never serves a field or a type the
contract does not name. Any other path
or method answers 404 or 405. There is no authentication, no query parameter, no directory
listing, and the file is read with a size cap. It is a user service with `Restart=on-failure`.

D9 — **Freshness alarm.** Units are user units (ruling `35427fd3` (iv)), invisible to
red-monitor's systemd collector. The single alarm is staleness: a consumer shows a block as stale
when `generated_at` is older than 2 h (two timer periods). red-monitor owns that display.

D10 — **Failure and threat model.**
- The publisher exposes only aggregate counts about the rail's own process. No path, address,
  token, PR body or finding text goes into a block, only project names, dates and numbers:
  no identifier of a session, an agent, a turn, brain or a client, no credential id, no
  secret, no prompt and no log line (red-monitor's cockpit rule, ticket 6b59a753).
- The data is as trustworthy as the ledger: a forged attestation could skew an autonomy count.
  That is the ledger's integrity model (digest-checked receipts, brain authority), not a new one.
- A crash of `serve` shows as red-monitor's `no_publisher`. A dead timer shows as staleness (D9).
  A partial read shows in `coverage.missing`, with a `null` total.

## Non-goals

- Conformance in a published block (later, from CI).
- Operator questions, decisions and human time (T1, after Q1).
- Any computation in brain-v42 or red-monitor.
- System units or a red-alerts alarm for the publisher in v1.
- The red-cockpit mod (later, pinning the same contracts).

## Success criteria

1. The fixture `full.json` (eleven active repositories, the worst case today) is 8,470 bytes compact.
   `rail metrics publish` writes a two-block file. The blocks match the fixtures' schema (full,
   empty, unknown version), and the compact size of a real run on this host is measured and at
   most 12 KiB.
2. A run where one repository's ledger fails still publishes the others, and names the failure.
3. `rail metrics serve` answers the file on 127.0.0.1:9230 only. Tests show 404 and 405 for
   other paths and methods, `{}` without a file, and 503 for an oversized or invalid file.
4. Every autonomy metric is computed by a pure function over records and GitHub facts, each
   tested on fixtures, including the `since` cut-off and `null` for an empty denominator.
5. No block's top level carries a reserved relay key (tested).
6. `make ci` green; `rail check` passes on this repository.
