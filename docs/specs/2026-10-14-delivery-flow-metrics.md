# Delivery flow metrics: where the time between push and merge goes

Status: approved — 2026-10-14. Scope and design validated by the operator through the
coordination (decision `ad491597`; answers "ok go b" on the scope, "ok go pour a partt" on
Q2–Q6, "1 ok metric flux" on the five design sections), then the written spec: "et le reste
go btw", after three design-review rounds (codex, closure APPROVE).

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

- Push (a proxy, stated as such): the earliest `created_at` among the check suites that the
  CI app (GitHub Actions) created on the final head, but never earlier than the pull
  request's creation. GitHub records no push time without the Actions permission; a check
  suite is created when the push reaches the CI. A final head that was already pushed
  elsewhere (a SHA reused across branches) is the known error of this proxy; the acceptance
  check (success criteria) measures it against the 2026-10-14 analysis, which read workflow
  run creation times.
- Merge: the pull request's `merged_at`.
- Evidence is bounded by the merge. Only what happened before `merged_at` describes the
  delivery: a check run that started after the merge, a status or a receipt recorded after
  it, is ignored. A check run still running at the merge (no `completed_at`, or one after
  `merged_at`) ends at `merged_at` for every segment.
- CI: every check run of the CI app on the final head that started before the merge, every
  attempt included (the client reads them with `filter=all`), with `started_at` and
  `completed_at`.
- Reviewer: every `red-rail/review` check run of our App on the final head that started
  before the merge, every attempt included; the run that carries the final verdict is the
  one whose id is the `check_run_id` of the latest `review_verdict` receipt for that head
  recorded before the merge. The
  receipts also give `rounds` and `carried_from` (D3).
- Judge: the commit status history of the final head (every status, not the latest per
  context), filtered to context `independent-review`, state success and `created_at` at or
  before `merged_at`; the latest of those is the judge's verdict. A status posted after the
  merge is ignored. When its description starts with `Carried from ` it is a carried
  verdict (`rail verdict carry`): `judge_carried` is true and its run id, if any, belongs to
  the source head, so no judge start is read from it. Otherwise, when the description
  contains `(ha <run_id>)`, that run id's timestamp is the judge start and the provider named
  before it is recorded.
- Holder: the merge-queue journal of the host, for a repository whose entry in the private
  reviewer configuration declares `merge_queue: <path>`. The journal lists merged turns in
  merge order (`.merged[]`: `pr`, `sha`, `merged_at`) and records no start for a past turn.
  The holder interval therefore runs from the latest `holder_since` the entry carries, when
  the journal provides it, else from the previous entry's `merged_at`, with
  `holder_approx` set: that fallback counts a period with an empty queue as held. A request
  goes to the queue's owner to record `holder_since` in each merged entry. The path is host
  configuration and never appears in this public repository.

Reading statuses needs the App's `statuses: read` permission on private repositories (it
answers 403 there without it, measured 2026-10-14; ticket `b7c8b823`; the operator agreed to
grant it). Without it the judge segments of that repository are null (D6).

D3 — Segments, in minutes, per pull request:

| Segment | From → to |
|---|---|
| `flow` | push → merge |
| `ci_start` | push → first CI check run start (runner wait included) |
| `ci` | first CI check run start → last CI check run end (CI wall time) |
| `ci_longest` | longest single CI check run, the bottleneck job when jobs run in parallel |
| `review_start` | push → start of the reviewer run that carries the final verdict |
| `review` | that run's start → end; 0 for a carried verdict |
| `judge_wait` | push → the judge's verdict (D2) |
| `judge` | judge start → judge's verdict, only when a judge start is read (D2) |
| `idle` | `flow` minus the union of every CI attempt, every reviewer run and the judge interval, each clipped to [push, merge]; the judge interval enters the union only when its start is read |
| `holder` | holder interval (D2), only where a merge queue is declared |

Plus, per pull request: `rounds`, the number of judged rounds as `rounds.py` counts them
(mechanical, closure, carried and awaiting-ruling receipts are not rounds); `carried`, the
final verdict has `carried_from`; `judge_carried`; `judge_provider` when known;
`holder_approx`. A segment whose evidence is absent is null, never 0.

D4 — Contract `rail-flow/v1`, a new block `flow` of the `/rail-metrics` answer, present or
absent independently of `delivery`, `delivery_v2` and `autonomy`. It carries:

- per repository and per segment: `median`, `p90` and `n` over the window. Carried reviewer
  verdicts are excluded from the `review` percentiles and carried judge verdicts from the
  `judge` percentiles; each repository publishes `carry_rate` (carried final heads over
  merged pull requests);
- `recent`: the timelines of the 20 most recently merged pull requests across repositories
  (repository, number, short head SHA, merge time, every segment, `rounds`, `carried`,
  `judge_carried`, `judge_provider`, `holder_approx`);
- `coverage`: the repositories that could not be read (`missing`), the pull requests left
  out (`unread_prs`), and per repository the pull requests kept with a segment that could
  not be read, by segment (`unread_segments`, e.g. `{"judge_wait": 4}`).

Fixtures for the contract live in `contracts/rail-metrics-v1/` and are validated by the
contract tests; the README states the version rule (a consumer checks the major). A ticket
asks red-monitor to display the block.

D5 — Size. The answer is capped at 12 KiB today (`metrics_contract.MAX_BYTES`), and the
eleven-repository fixture already takes about 8.5 KiB; the flow block for eleven
repositories and twenty timelines needs about 9 KiB more. The cap rises to 32 KiB, in the
contract README and in `MAX_BYTES`, with the fixtures re-measured. The cap guards a
consumer against an unbounded answer; red-monitor's own read limit is confirmed through its
ticket before the release that publishes the block, and the release waits for it.

D6 — Cost and cache. Each hourly publication lists the pull requests merged in the window
(one paginated list per repository, without a per-pull-request call). Only the GitHub
evidence is cached; the ledger receipts and the merge-queue journal are local reads and are
read again at every publication, so a receipt replayed late, or a `merge_queue` declared
later, is taken into account without invalidating anything. The cache lives on the host
under `~/.local/state/red-rail/`, keyed by repository, number, merge SHA and the contract
version. Because D2 bounds the GitHub evidence by the merge, it can no longer change once
every check run that started before the merge has completed and the merge is older than
24 h; such an entry is complete and is reused without a call. An entry with a GitHub read
that failed (a 403, a transport error) stays incomplete and is read again at the next
publication, so granting `statuses: read` later fills it in. An entry that fails to load is
dropped and read again; because discovery lists the whole window, nothing is lost by
dropping it.

D7 — Failure. A pull request whose detail, check suites or merge cannot be read (push or
merge unknown) is left out and counted in `unread_prs`. A pull request whose other evidence
cannot be read is kept: the affected segments are null and counted in `unread_segments`.
When the ledger cannot be read, `rounds`, `carried` and the review segments that depend on
the receipt are null, the pull request leaves the `carry_rate` denominator, and
`unread_segments` counts it under `ledger`: an unread verdict is never taken as not carried,
an unread round count never as zero.
A repository whose list of merged pull requests cannot be read is listed in
`coverage.missing`. The publication never fails because of the flow block. The failing step
(`pulls`, `checks`, `statuses`, `ledger`, `queue`) is logged on stderr, never in the public
answer.

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
  `holder` median is 22.3 min ± 1, the `ci` median 19.8 min ± 1 and the `ci_longest`
  median 19.7 min ± 1; the push proxy differs from the analysis's push by at most 1 min on
  at least 15 of the 17.
- Every watched repository appears in `flow` or in `coverage.missing`; a judge segment that
  cannot be read is null with its reason counted, never 0.
- A second publication, with no new merge and only complete cache entries, makes no
  per-pull-request API call; an incomplete entry is read again.
- Tests pin D3 on synthetic timelines (overlapping intervals, clipping, every attempt,
  carried reviewer and judge verdicts, a status posted after the merge, absent evidence,
  holder with and without `holder_since`), D6 and D7 on a fake GitHub, and D4 and D5 on
  golden contract fixtures;
  `make ci` green; an independent non-Claude, non-producer judge approves.
