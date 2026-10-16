# Rail metrics blocks — delivery, autonomy and `rail-flow/v1`

The contract red-rail publishes at `GET http://127.0.0.1:9230/rail-metrics` (`rail metrics
serve`) and red-monitor relays at `/api/v1/rail-delivery` and `/api/v1/rail-autonomy`.
Design: `docs/specs/2026-10-13-rail-metrics-publication.md`. red-rail owns the blocks and these
fixtures; red-monitor copies them from a pinned red-rail revision and owns its relay envelope.

## Answer

A JSON object with four independently optional blocks: `delivery`, `delivery_v2`,
`autonomy` and `flow`; `{}` when nothing was published. The format ceiling is 32 KiB
(32,768 compact UTF-8 bytes) for the whole answer, including both delivery versions during
the transition. `full.json` remains 8,470 bytes; `flow-full.json`, with eleven repositories,
both delivery versions, autonomy and twenty recent flow entries, is 25,868 bytes.

The publication limit is separately 16 KiB pending ticket `af2ba8e7`: both downstream
relay read caps must be confirmed raised before a release may publish a larger answer.
The 32 KiB format ceiling alone does not authorize that publication. Collection and
publication wiring are delivered separately from these contract definitions.

## Every block

- `version`: `rail-delivery/v1`, `rail-delivery/v2`, `rail-autonomy/v1` or `rail-flow/v1`.
  Before reading a block, a consumer checks its major version and treats an unsupported
  value as unknown (`unknown-version.json`, `flow-unknown-version.json`).
- `generated_at`: RFC 3339 UTC, the end of the computation; freshness is read from it. A block
  older than 2 h is stale.
- The existing `delivery`, `delivery_v2` and `autonomy` blocks use `windows_days: [7, 30]`.
  Flow instead has one positive `window_days` and its UTC `window_start`.
- Never at a block's top level: `served_at`, `source_age_s`, `source`, `available`, `reason`,
  `payload` (red-monitor's relay keys).
- Existing blocks name their units in fields; all flow segment values are minutes.
  An unknown value is `null`, never `0`; measured zero remains zero.
- No block carries an identifier of a session, an agent, a turn, brain or a client, a
  credential id, a secret, a prompt or a log line. Flow adds only repository slugs, PR
  numbers, twelve-character lowercase head SHAs and bounded provider labels to the existing
  project names, dates and numbers. Raw run ids, descriptions, paths and diagnostics remain
  excluded; arbitrary error text and relay-envelope fields are refused at every level.

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

## `flow` (`rail-flow/v1`)

Flow describes final heads merged into `main` during the single window. Its push time is
a proxy: the earliest GitHub Actions check-suite creation on that head, clamped to PR
creation. A reused head can make this differ from the actual push. A PR with no readable
proxy or merge is omitted and counted once in `unread_prs`. Evidence after merge is ignored;
execution still active at merge ends there for measurement.

- `repositories`: entries sorted uniquely by owner/name `repository`, preserving case and
  the owner identity. Each contains `merged_prs` (kept timelines), `carry_rate` and
  `segments`, a map containing exactly the ten keys below. Every distribution has `median`,
  nearest-rank `p90` and sample count `n`. When `n` is zero both values are null; otherwise
  both are measured. Read repositories with no merges remain present with empty samples.
- `recent`: at most twenty timelines globally, ordered by `merged_at` descending, then
  repository and PR `number` ascending for ties. Each has `repository`, positive `number`,
  `head_sha` (exactly twelve lowercase hexadecimal characters), UTC `merged_at`, a
  `segments` object with all ten nullable minute values, and nullable `rounds`, `carried`,
  `judge_carried`, `judge_provider`, `holder_approx`. Providers contain 1–32 ASCII letters,
  digits, underscores or hyphens. Recent is a view; distributions use the entire window.
- `coverage`: `missing` is the unique sorted list of repositories whose discovery failed,
  disjoint from the read population. `unread_prs` maps read repository slugs to omitted PR
  counts. `unread_segments` maps read slugs to counts by segment or `ledger`: distinct kept
  PRs with unknown applicable evidence, rather than failed calls or attempts. Zero counts
  are omitted. Ledger failure counts once per kept PR under `ledger`, leaves receipt-based
  review values and metadata unknown, and excludes the PR from the carry-rate denominator.
  An undeclared queue gives null holder values without an unread count; a declared queue
  that cannot be read counts under `holder`. Diagnostics belong on stderr only.

| Segment | Interval or duration in minutes |
|---|---|
| `flow` | Push proxy to merge. |
| `ci_start` | Push proxy to first CI start, including runner wait. |
| `ci` | First CI start to last CI end, including gaps and every attempt. |
| `ci_longest` | Longest single CI run. |
| `review_start` | Push proxy to the run carrying the attested final verdict. |
| `review` | That run's duration; proved carried review has duration zero. |
| `judge_wait` | Push proxy to the latest successful pre-merge judge verdict. |
| `judge` | Parsed judge start to that verdict, when a fresh start is known. |
| `idle` | Flow minus the union of CI, reviewer and known judge execution intervals. |
| `holder` | Queue-holder start to merge; it can begin before the final push. |

All durations are finite and nonnegative. Minutes round to two decimals and carry rates
to three when converting computed observations into the contract; computation retains
its precision. Nested nulls remain in JSON; only absent top-level blocks are omitted.
Timestamps serialize with UTC `+00:00`.

Carried reviewer verdicts are excluded only from `review` distributions; carried judge
verdicts only from `judge` distributions. Waits, flow and holder remain included. A carried
judge has no fresh duration or provider. `carry_rate` divides known carried final heads
by all heads whose carried flag is known; an empty denominator gives null. Missing ledger
evidence is not an uncarried verdict or zero rounds. Successful status history with no
parseable start adds no judge interval to idle, while a failed status read leaves idle
unknown. `holder_approx` marks use of the preceding queue merge instead of an exact start.

## Fixtures

`full.json` (both blocks; total counts are the sums of the projects'), `delivery-only.json`,
`partial.json` (one repository unread, `total: null`), `partial-delivery.json` (a
prod-tier repository unread: absent from both blocks), `empty.json` (`{}`),
`unknown-version.json`.

`delivery-v2.json` adds both delivery versions, including a targetless repository.

`flow-only.json` proves that flow is independent; `flow-empty.json` retains a successfully
read repository with no merges. `flow-unknown-version.json` changes only the major to
`rail-flow/v2`. `flow-partial.json` distinguishes failed discovery (`example/missing`),
one omitted PR (`example/omitted`), refused statuses (`example/statuses`), an unread ledger
(`example/ledger`) and an undeclared queue (`example/undeclared`). These are synthetic
examples; no fixture reads host configuration or reaches the network.

`flow-full.json` retains the existing eleven-project population and both existing blocks,
adds delivery v2 for all eleven projects, and uses `example/<project>` flow slugs. Each
repository has four synthetic heads with scale k = 1, 2, 3, 4. Before the exceptions below,
the minute values are:

| Segment | Value |
|---|---|
| `flow`, `ci_start`, `ci`, `ci_longest` | 10k, k, 4k, 3k |
| `review_start`, `review` | 2k, 2k |
| `judge_wait`, `judge` | 8k, k |
| `holder` | 12k |

CI runs occupy [k, 4k] and [3k, 5k], the reviewer occupies [2k, 4k], and a fresh judge
occupies [7k, 8k]. Thus idle is 5k. Head 2 carries review: its public review is zero and
is excluded from review percentiles, while its actual run still enters the idle union.
Head 3 carries judge: its judge duration is null and excluded; without fresh judge
execution its idle is 18. Head 4 has an unread ledger: review_start/review and carried
metadata are null, although its actual reviewer interval still enters idle. Carry rate is
1 / 3 known flags, rounded to 0.333. Idle samples are [5, 10, 18, 20], median 14, p90 20.
Review samples [2, 6] give median 4, p90 6; judge samples [1, 2, 4] give median 2, p90 4.
All other distributions follow the table, using independent non-null samples and
nearest-rank p90. `flow-only.json` reuses one of these four-head repository summaries.

Heads 1 and 2 in all eleven repositories tie at the newer merge time; heads 3 and 4 are
older. Sorting globally by repository and PR selects heads 1 and 2 in the first ten
repositories for twenty fully populated recent entries. Summaries still use all 44 heads.

The contract tests assert exact compact UTF-8 lengths, including the invalid-version
examples, and pin hashes of the existing fixtures so their bytes cannot change silently:

| Fixture | Compact bytes |
|---|---:|
| `full.json` | 8,470 |
| `partial.json` | 7,196 |
| `partial-delivery.json` | 6,771 |
| `delivery-only.json` | 2,262 |
| `delivery-v2.json` | 1,365 |
| `empty.json` | 2 |
| `unknown-version.json` | 8,470 |
| `flow-only.json` | 1,154 |
| `flow-full.json` | 25,868 |
| `flow-partial.json` | 3,799 |
| `flow-empty.json` | 734 |
| `flow-unknown-version.json` | 1,154 |
