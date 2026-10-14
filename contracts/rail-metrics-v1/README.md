# Rail metrics blocks — `rail-delivery/v1`, `rail-delivery/v2`, `rail-autonomy/v1`

The contract red-rail publishes at `GET http://127.0.0.1:9230/rail-metrics` (`rail metrics
serve`) and red-monitor relays at `/api/v1/rail-delivery` and `/api/v1/rail-autonomy`.
Design: `docs/specs/2026-10-13-rail-metrics-publication.md`. red-rail owns the blocks and these
fixtures; red-monitor copies them from a pinned red-rail revision and owns its relay envelope.

## Answer

A JSON object with at most three members, `delivery`, `delivery_v2` and `autonomy`, each
present or absent independently; `{}` when nothing was published. Compact size at most
12 KiB for the whole answer (`full.json`, eleven active repositories with v1 delivery:
8,470 bytes compact). The size cap also applies during the transition.

## Every block

- `version`: `rail-delivery/v1`, `rail-delivery/v2` or `rail-autonomy/v1`; a consumer checks
  the major and treats any other value as unknown (`unknown-version.json`).
- `generated_at`: RFC 3339 UTC, the end of the computation; freshness is read from it. A block
  older than 2 h is stale.
- `windows_days`: `[7, 30]`.
- Never at a block's top level: `served_at`, `source_age_s`, `source`, `available`, `reason`,
  `payload` (red-monitor's relay keys).
- Units are in field names; an unknown value is `null`, never `0`.
- No block carries an identifier of a session, an agent, a turn, brain or a client, a
  credential id, a secret, a prompt or a log line: only project names, dates and numbers.

## `delivery` (`rail-delivery/v1`)

`projects`: one entry per prod-tier repository with a deploy target, sorted by `project`:
`project`, `target`, and `d7` / `d30`, each holding `deployments`,
`deployment_frequency_per_week`, `lead_time_commit_to_deploy_hours` (median),
`change_failure_rate` (failed artefacts over attempts; the 24 h window applies only to a rollback
without a digest, spec D3), `recovery_time_hours` (median),
`drill_recovery_time_minutes`.

## `delivery_v2` (`rail-delivery/v2`)

For one release the answer publishes `delivery` and `delivery_v2` together. Every v1 field
keeps its existing shape and meaning; consumers must continue reading every v1 field they
depend on. The following release removes `delivery`; `delivery_v2` keeps its key.

`projects` includes every successfully read repository, sorted by `project`. Each entry
has `project`, `status`, `target`, `d7` and `d30`. With a deploy target, `status` is `measured`
and both windows contain figures. Without one, `status` is `no_deployment` and `target`,
`d7` and `d30` are all `null`. Unread repositories are absent and named in `coverage.missing`;
stderr reports only the slug-derived name for `fetch` and `manifest`, or the manifest's
project name for `ledger` and `github`, followed by the failing step and exception class.

Each measured window contains:

| Field | Definition and unit |
|---|---|
| `changes` | Count of first deployments per artefact digest. |
| `redeployments` | Count of later deployments of the same digest. |
| `deployment_frequency_per_week` | Changes per week. |
| `lead_time_for_changes_hours` | Median hours from the oldest included commit to deployment. |
| `release_to_deploy_hours` | Median hours from release to the change's deployment. |
| `change_failure_rate` | Failed attempts / attempts; `null` with no attempts. |
| `failed_changes` | Count of failed changes plus failed candidates in this window. |
| `time_to_restore_minutes` | Median recovery minutes, rounded to two decimals. |
| `open_incidents` | Count of this window's incidents still open at publication time. |
| `drill_recovery_time_minutes` | Drill recovery minutes, measured with the v1 rule. |

Changes and redeployments include only release-mode deployments on the declared target.
Their first deployment is determined over the whole ledger; a redeployment remains one
even after another digest went live. Window membership follows the change's first deployment.
Lead time uses the oldest non-merge committer date in the previous change's SHA..current SHA
range read on `origin/main`. No previous change, non-ancestry, an unreadable or empty range
gives no sample. A negative lead time caused by clock skew is also skipped, never clamped
to zero. Release delay uses the released record carrying the same artefact digest at or
before deployment. Both lead times are median durations converted to hours by dividing
their total seconds by 3,600, then rounded to two decimal places.

Durations are nonnegative numbers or `null` when no sample exists. Counts are nonnegative
integers; the failure rate is between zero and one. A non-drill incident naming `digest`,
or rollback naming `from_digest`, fails that artefact once. Digestless failures are
attributed to the newest preceding release-mode deployment within 24 hours, including
redeployments. A failed digest with no release-mode deployment is one failed candidate
attempt, placed in the window of its first failure. Later failures still belong to a
change's first-deployment window.

Recovery matches over the whole non-drill history: a named restored record closes only its
incident; a legacy unnamed one closes the oldest earlier open incident. Its UTC `restored_at`
is used only between incident time and the restored record time, otherwise record time is
used. Incident time selects the recovery window. Future evidence does not close an incident
at the publication time.

`time_to_restore_minutes` and `drill_recovery_time_minutes` use second-precision times and
round durations to two decimal places of a minute. Time to restore rounds the median;
drill recovery rounds each sample before taking the median, using `recovery_seconds` when
available and otherwise the incident and restored record times.

## `autonomy` (`rail-autonomy/v1`, `"experimental": true`)

- `since`: the date from which actors are recorded; nothing earlier is counted.
- `coverage`: `{expected, read, missing: [project]}`, the repositories this run could not read.
- `total`: `d7` and `d30` over all repositories, or `null` when `coverage.missing` is not empty
  (`partial.json`); `projects`: `project` and `d30` for every repository read with counted
  activity in the last 30 days, sorted by `project`.
- Each window: `window_start` (effective start, never before `since`), `releases`,
  `operator_gestures`, `operator_gestures_per_release`, `agent_gesture_ratio`, `merged_prs`,
  `reviewed_prs`, `rulings_per_reviewed_pr`, `agent_rulings`, `review_rounds_median`, `review_rounds_p90`,
  `first_pass_approval_ratio`, `awaiting_ruling_passes`, `ready_to_merged_hours_median`,
  `rollbacks`, `reverts`, `incidents`. Definitions: the spec, D4.
- `not_measured`: `[{what, why}]`, what v1 does not measure (operator questions and decisions,
  human time, the justified-versus-avoidable classification).

## Fixtures

`full.json` (both blocks; total counts are the sums of the projects'), `delivery-only.json`,
`partial.json` (one repository unread, `total: null`), `partial-delivery.json` (a
prod-tier repository unread: absent from both blocks), `empty.json` (`{}`),
`unknown-version.json`.

`delivery-v2.json` adds both delivery versions, including a targetless repository.
