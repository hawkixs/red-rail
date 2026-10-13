# Rail metrics blocks — `rail-delivery/v1`, `rail-autonomy/v1`

The contract red-rail publishes at `GET http://127.0.0.1:9230/rail-metrics` (`rail metrics
serve`) and red-monitor relays at `/api/v1/rail-delivery` and `/api/v1/rail-autonomy`.
Design: `docs/specs/2026-10-13-rail-metrics-publication.md`. red-rail owns the blocks and these
fixtures; red-monitor copies them from a pinned red-rail revision and owns its relay envelope.

## Answer

A JSON object with at most two members, `delivery` and `autonomy`, each present or absent
independently; `{}` when nothing was published. Compact size at most 12 KiB, both blocks
together (`full.json`, eleven active repositories: 8,487 bytes compact).

## Every block

- `version`: `rail-delivery/v1` or `rail-autonomy/v1`; a consumer checks the major and treats
  any other value as unknown (`unknown-version.json`).
- `generated_at`: RFC 3339 UTC, the end of the computation; freshness is read from it. A block
  older than 2 h is stale.
- `windows_days`: `[7, 30]`.
- Never at a block's top level: `served_at`, `source_age_s`, `source`, `available`, `reason`,
  `payload` (red-monitor's relay keys).
- Units are in field names; an unknown value is `null`, never `0`.

## `delivery` (`rail-delivery/v1`)

`projects`: one entry per prod-tier repository with a deploy target, sorted by `project`:
`project`, `target`, and `d7` / `d30`, each holding `deployments`,
`deployment_frequency_per_week`, `lead_time_commit_to_deploy_hours` (median),
`change_failure_rate` (failed artefacts over attempts; the 24 h window applies only to a rollback
without a digest, spec D3), `recovery_time_hours` (median),
`drill_recovery_time_minutes`.

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
`partial.json` (one repository unread, `total: null`), `empty.json` (`{}`),
`unknown-version.json`.
