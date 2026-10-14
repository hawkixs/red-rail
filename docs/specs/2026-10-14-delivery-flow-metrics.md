# Delivery flow metrics: where the time between push and merge goes

Status: draft — 2026-10-14. Scope and design validated by the operator through the
coordination (decision `ad491597`; answers "ok go b" on the scope, "ok go pour a partt" on
Q2–Q6, "1 ok metric flux" on the five design sections); awaiting the operator's review of
this written spec.

Motivation: lowers the operator load of finding delivery bottlenecks by hand (ticket
`c5231125`, measurement class): the merge-queue analysis of 2026-10-14 had to be gathered by
a one-off script, and the judge-independence rule `f9063da2` is to be reviewed around
2026-10-21 on measured judge latency.

## Problem

Throughput on the busiest repository is bounded by the serial merge gates, not by code
generation: on 2026-10-13/14, 17 brain-v42 pull requests held the merge queue for a median
22.3 min (p90 62.5 min). The core was CI (critical job ≈ 19 min); the tail, 51 % of all
holder minutes, was the wait before the reviewer and the independent judge started. That
measurement came from a script run once by hand. Nothing measures it continuously, for any
repository, so a change to CI, to the review loop or to the merge rules cannot be judged by
its effect on flow.

The delivery block measures deployments (DORA). It says nothing about the life of a pull
request between its last push and its merge.

## Decisions

D1 — Population and scope. Every pull request merged into `main` inside the window, in every
repository the reviewer configuration watches (11 today). The window is the delivery
window (`--window`, 30 days by default). One pull request yields one timeline, measured on
its final head: the head that was merged.

D2 — Sources. Everything is read with the reviewer App's installation token, through the
existing bounded GitHub client; nothing needs the Actions permission.

- Push: the earliest `created_at` among the check suites of the final head.
- Merge: the pull request's `merged_at`.
- CI: the check runs of the final head whose source is the CI app (GitHub Actions), with
  their `started_at` and `completed_at`.
- Reviewer: our `red-rail/review` check run on the final head (start, end), and the
  `review_verdict` receipts of the pull request in the project's ledger (round count,
  `carried_from`).
- Judge: the most recent `independent-review` status with state success on the final head,
  through the commit statuses endpoint. Its `created_at` is the verdict time. When its
  description contains `(ha <run_id>)`, the run id's timestamp is the judge start and the
  provider named before it is recorded.
- Holder: the merge-queue journal of the host, for a repository whose entry in the private
  reviewer configuration declares `merge_queue: <path>`. The holder interval of a pull
  request runs from the previous `.merged[]` entry's `merged_at` to its own. The path is
  host configuration and never appears in this public repository.

Reading statuses needs the App's `statuses: read` permission on private repositories (it
answers 403 there without it, measured 2026-10-14; ticket `b7c8b823`). Without it the judge
segment is null for that repository and the reason is counted in coverage (D6).

D3 — Segments, in minutes, per pull request:

| Segment | From → to |
|---|---|
| `flow` | push → merge |
| `ci_start` | push → first CI check run start (runner wait included) |
| `ci_critical` | duration of the CI check run that completes last |
| `review_start` | push → `red-rail/review` start |
| `review` | `red-rail/review` start → end |
| `judge_wait` | push → `independent-review` success |
| `judge` | judge start → `independent-review` success, only when the description gives the run id |
| `idle` | `flow` minus the union of the CI, reviewer and judge intervals, each clipped to [push, merge]; the judge interval enters the union only when its start is known |
| `holder` | holder interval, only where a merge queue is declared |

Plus `rounds` (the number of non-carried `review_verdict` receipts of the pull request),
`carried` (the final head's verdict has `carried_from`) and `judge_provider` when known.
A segment whose evidence is absent is null, never 0. A carried verdict has `review` 0 and is
marked `carried`.

D4 — Contract `rail-flow/v1`, a new block `flow` of the `/rail-metrics` answer, present or
absent independently of `delivery`, `delivery_v2` and `autonomy`. It carries:

- per repository and per segment: `median`, `p90` and `n` over the window. Carried verdicts
  are excluded from the `review` percentiles, and each repository publishes `carry_rate`
  (carried final heads over merged pull requests);
- `recent`: the timelines of the 20 most recently merged pull requests across repositories
  (repository, number, short head SHA, merge time, every segment, `rounds`, `carried`,
  `judge_provider`);
- `coverage`: the repositories that could not be read (`missing`) and the number of pull
  requests left out (`unread_prs`), as in the delivery block.

Fixtures for the contract live in `contracts/rail-metrics-v1/` and are validated by the
contract tests; the README states the version rule (a consumer checks the major). A ticket
asks red-monitor to display the block.

D5 — Cost. A merged pull request no longer changes, so its timeline is cached on the host
under `~/.local/state/red-rail/`, keyed by repository, number and merge SHA. Each hourly
publication reads only the pull requests merged since the last one: about six bounded API
calls per pull request, paginated with the client's page limit. A cache entry that fails to
load is dropped and read again.

D6 — Failure. A read that fails for one pull request leaves that pull request out and
increments `unread_prs`; a repository whose list of merged pull requests cannot be read is
listed in `coverage.missing`. The publication never fails because of the flow block. The
failing step (`pulls`, `checks`, `statuses`, `ledger`, `queue`) is logged on stderr, never
in the public answer.

## Non-goals

- No runner wait isolated from the CI start: it needs the Actions permission, and it measured
  0.1 min.
- No timeline of intermediate heads: rounds count them, segments describe the final head.
- No pull request that was closed without a merge.
- No change to any gate, review rule or merge rule: this block measures, it decides nothing.
- No private analysis data in this repository: the acceptance check against the
  2026-10-14 analysis is run on the host.

## Success criteria

- On the host, for the 17 brain-v42 pull requests of the 2026-10-14 analysis, the published
  `holder` median is 22.3 min ± 1 and the `ci_critical` median 19.7 min ± 1.
- Every watched repository appears in `flow` or in `coverage.missing`; a judge segment that
  cannot be read is null with its reason counted, never 0.
- A second publication with no new merge makes no per-pull-request API call (cache).
- Tests pin D3 on synthetic timelines (overlapping intervals, clipping, carried verdict,
  absent evidence, holder), D5 and D6 on a fake GitHub, and D4 on golden contract fixtures;
  `make ci` green; an independent non-Claude, non-producer judge approves.
