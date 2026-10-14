# Plan: delivery flow metrics, where the time between push and merge goes

Spec: `docs/specs/2026-10-14-delivery-flow-metrics.md` (D1–D7)

Method: spec. Operator decision: `ad491597`.

Every implementation task starts with its named failing tests, then the minimum change
that makes them pass. Record the failure reason and both exit codes. Run `make ci` after
each task and before each pull request. The acceptance procedure is a host check, not a
synthetic substitute for the measured population. This plan changes no delivery gate.

## Global constraints

- Public repository: no machine address, infrastructure name, private queue path, private
  analysis data, credential, raw status description or log line in committed examples or
  the public answer. Repository slugs, PR numbers, short head SHAs and provider labels are
  the public identifiers explicitly added by D4; run ids remain private inputs.
- Everything is read with the reviewer App's installation token, through the existing
  bounded GitHub client; nothing needs the Actions permission. Reading private commit
  statuses requires `statuses: read` (ticket `b7c8b823`). Its absence leaves judge evidence
  unknown and retryable; publication neither grants permissions nor posts statuses.
- A segment whose evidence is absent is null, never 0. An unread ledger does not mean
  zero rounds or an uncarried verdict. A proved carried reviewer verdict has `review = 0`
  by D3, but does not enter review percentiles.
- **BLOCKING DEPENDENCY for the release that publishes flow:** measured on 2026-10-14,
  red-monitor's two relays cap their reads of `/rail-metrics` at 16,384 and 20,480 bytes.
  Ticket `af2ba8e7` must confirm that both caps have been raised before any release may
  publish an answer above 16 KiB. D5's 32 KiB contract ceiling is not that confirmation.
  Task 7 assembles the answer without flow first. If adding flow would exceed the cap in
  force, leave flow absent, log the step name and both byte sizes to stderr, and publish
  delivery/delivery_v2/autonomy as before. Never trim repositories, segments, coverage or
  recent within flow. The release waits for the confirmation; Task 8 verifies it before
  selecting a larger publication cap.
- Evidence is bounded by the merge. Ignore post-merge starts, statuses and recorded
  receipts. A run active at the merge ends at the merge for computation, even when its
  actual completion is later. Cache completeness still uses its actual completion.
- Population: all PRs merged into `main` in `[now - window_days, now]`, default 30 days,
  from every watched repository, using the final head. No intermediate-head timelines,
  closed-unmerged PRs, separate runner-wait metric, or changes to review or merge rules.
- Cache only GitHub evidence on the host. Re-read local receipts and the queue journal
  each publication. A failed, incomplete or corrupt cache entry never proves absence.
- Flow read failures never prevent the other metrics blocks from being published. Keep
  existing delivery/autonomy semantics and their own error policy; autonomy's code path,
  population and calls stay exactly as on main. Flow has its own discovery reader, cache
  and coverage. Emit failing step names on stderr, never in the answer.

## Read before planning

The spec, preceding plan, source files and requested test files were read in full. Line
numbers below refer to this checkout, before implementation.

- `docs/specs/2026-10-14-delivery-flow-metrics.md:28`: D1–D7 through line 146;
  non-goals at line 148; success criteria at line 158.
- `docs/plans/2026-10-13-rail-metrics-publication.md:1`: header and test cycle;
  read list at line 12, Task 1 at line 35, review focus at line 323.
- `src/rail/metrics.py:44`: `DeliveryWindow`; `DeliveryWindowV2` at line 57;
  `compute_metrics(ledger, project, repo, *, now, window_days=30, ci=False)` at line 114;
  `delivery_window` at line 166; `delivery_window_v2` at line 253. None measures PR flow.
- `src/rail/metrics_contract.py:17`: `MAX_BYTES`; `StrictModel` at line 39;
  `UtcDatetime` at line 32; `Answer` at line 186; `encode(answer) -> bytes` at line 192.
- `src/rail/serve.py:12`: `create_server(file, *, port=9230)`;
  `Handler._respond` at line 22 reads the cap plus one and validates before serializing.
- `src/rail/commands/metrics.py:31`: `command`; `publish(config_path, out)` at line 85;
  `serve(file_path, port)` at line 140. The group's `--window` is not currently passed to
  publication, and the success message assumes an autonomy block at line 124.
- `src/rail/publish.py:63`: `default_output() -> Path`; `collect(config, *, now, clock,
  open_ledger, github) -> Answer` at line 70; `write(answer, out) -> int` at line 244.
  The shared read guard at lines 103–162 currently drops a repository after a ledger error.
- `src/rail/autonomy.py:24`: `MergedPull(number, merged_at, ready_at)`;
  `ProjectFacts` at line 33; `window_samples` at line 78, including its different judged
  predicate at lines 103–107; `window_values` at line 178 uses nearest-rank p90.
- `contracts/rail-metrics-v1/README.md:10`: independent blocks and 12 KiB size;
  major-version rule at line 17; exclusion clause at line 25; fixture list at line 109.
  Read `full.json`, `partial.json`, `partial-delivery.json`, `delivery-only.json`,
  `delivery-v2.json`, `empty.json` and `unknown-version.json` in full. `full.json` contains
  eleven repositories and measures 8,470 compact bytes; it does not include delivery v2.
- `src/rail/reviewer/github.py:199`: `GitHubApp._request`; `_request_page` at line 241;
  `_page_response` at line 263; `_paged_list` at line 280. Bounds are at lines 26–30:
  2 MiB per response, 100 pages, 10 seconds per request, no retries, same-origin links.
- `src/rail/reviewer/github.py:301`: `ready_for_review_at`; `merged_pulls` at line 308
  calls it for every PR at line 338; `pull` at line 370 returns `PullRequest`, whose fields
  at line 67 omit creation and merge timestamps. Its head SHA is not the merge commit SHA.
- `src/rail/reviewer/github.py:549`: `check_runs` uses `filter=latest`;
  `CheckRun` at line 87 omits timestamps. `commit_statuses` at line 573 reduces history
  per context and creator; `CommitStatus` at line 96 omits creation time.
- `src/rail/reviewer/config.py:18`: `RepositoryConfig(slug: str, path: Path)` and its
  path expansion validator; `ReviewerConfig` at line 30; `load_reviewer_config` at line 62.
- `src/rail/reviewer/rounds.py:22`: `JUDGED_ROUNDS = (1, 2, 3, None)`;
  `_judged` at line 80; `loop_state` at line 84 excludes mechanical and carried receipts
  from `judged`, and does not count `closure`, `awaiting_ruling` or `no_verdict`.
- `src/rail/reviewer/service.py:259`: `previous_verdicts` matches repository AND PR;
  `rulings_of` at line 268 does likewise. `carry_review` at line 693 records `carried_from`,
  `check_run_id` and `source_check_run_id` at lines 743–753. `_publish` at line 781 attests
  the check id before completing the check at lines 809–867. `_review_started` at line 1061
  produces mechanical, awaiting, closure and no-verdict receipts.
- `src/rail/ledger/__init__.py:239`: `Record`, `Record.build` at line 265,
  `recorded_at`, `attestation` at line 293 and `data` at line 299;
  `Ledger.list(project, *, kind=None, attestation=None) -> list[Record]` at line 348;
  `open_ledger(repo, *, client=None, agent="red-rail", token_file=DEFAULT_TOKEN_FILE)` at
  line 381. `src/rail/ledger/committed.py:16`: `CommittedLedger` combines verified main
  and local receipts by digest, sorted by time and digest at line 38, and refuses writes.
- `tests/test_metrics_contract.py:51`: fixture round trips; size at line 59;
  cap assertion at line 144; v2 coexistence at line 190; eleven-repository sizing at line 282.
- `tests/test_metrics_v2.py:40`: `emit` constructs ledger fixtures; `window` at line 55;
  missing evidence at line 192; future evidence at line 307.
- `tests/test_reviewer_github.py:27`: `Recorder`; `_app` at line 43;
  status reduction at lines 97 and 157; pagination bounds at line 249;
  paged/error fakes at lines 540–583; discovery/timeline fakes at lines 586–662;
  `_contents_app` at line 665; malformed entries at line 765.
- `tests/test_cli_metrics_publish.py:32`: `Clock`, `ReadLedger` at line 48,
  `ReadGitHub` at line 71 and `publication` at line 131 are the integration-fake pattern.

## Pull request split

- **PR 1 — evidence and computation:** Tasks 1–5. New modules and readers are dormant;
  existing publication and reviewer behaviour stay green. Dependencies: 2 and 3 consume
  Task 1's types; 4 consumes 2; 5 consumes 1 and 3.
- **PR 2 — contract and publication:** Tasks 6–8, in order. Task 6 installs models without
  enabling collection. Task 7 wires collection and enforces the 16 KiB default. Task 8
  adds the acceptance runbook. Run `make ci` on both PRs, with no intentionally failing
  intermediate commit. Release PR 2 only after the relay confirmation and host acceptance.
  The size increase does not require a change to any gate or vendored brain contract.

## Tasks

### Task 1 — pure final-head segments (`src/rail/flow.py`, `tests/test_flow.py`)

Create both files. Define `Segment = Literal["flow", "ci_start", "ci", "ci_longest",
"review_start", "review", "judge_wait", "judge", "idle", "holder"]` and `SEGMENTS`, the
tuple of those ten names in that order. All following data classes are frozen and slotted;
timestamps are aware UTC, and durations stay unrounded until contract assembly.

- `FlowPull(repository: str, number: int, head_sha: str, merge_sha: str,
  created_at: datetime, merged_at: datetime)`: full final head and merge commit separately.
- `FlowDiscovery(pulls: tuple[FlowPull, ...], unread_prs: int)`.
- `SuiteEvidence(id: int, app_id: int | None, app_slug: str | None, created_at: datetime)`.
- `RunEvidence(id: int, name: str, app_id: int | None, app_slug: str | None,
  status: str, started_at: datetime | None, completed_at: datetime | None)`.
- `StatusEvidence(id: int, context: str, state: str, description: str,
  created_at: datetime)`; descriptions are private inputs, never output fields.
- `GitHubEvidence(pull: FlowPull, suites: tuple[SuiteEvidence, ...] | None,
  runs: tuple[RunEvidence, ...] | None, statuses: tuple[StatusEvidence, ...] | None)`:
  `None` means failed read; an empty tuple means a successful empty read.
- `ReviewFacts(rounds: int | None, carried: bool | None, check_run_id: int | None)`.
- `HolderEvidence(started_at: datetime, merged_at: datetime, approximate: bool)`.
- `Segments`: ten `float | None` fields with exactly the names in `SEGMENTS`.
- `FlowTimeline(pull: FlowPull, push_at: datetime, segments: Segments,
  rounds: int | None, carried: bool | None, judge_carried: bool | None,
  judge_provider: str | None, holder_approx: bool | None)`.

Add `push_proxy(pull: FlowPull, suites: tuple[SuiteEvidence, ...] | None) -> datetime | None`.
Select suites whose `app_slug == "github-actions"` and creation is at or before merge;
take their earliest creation and then `max(creation, pull.created_at)`. No qualifying suite,
an unread list, or a result after merge gives `None`; this excludes the PR in Task 7. Do not
use commit dates, another App's suite, PR creation alone or the merge commit's checks.

Add `judge_facts(statuses: tuple[StatusEvidence, ...] | None, *, push: datetime,
merged_at: datetime) -> tuple[datetime | None, datetime | None, bool | None, str | None]`,
returning verdict time, start time, carried flag and provider. Select the latest success
for `independent-review` at or before merge, breaking equal timestamps by id. Check the
`Carried from ` prefix before parsing `(ha <run_id>)`: a carry has no start on this head.
For a fresh verdict, parse a run id beginning with `YYYYMMDDTHHMMSS-` and a nonempty
alphanumeric suffix as UTC second precision; reject an invalid calendar time or a start
after the verdict. Extract the single provider label immediately before `(ha ...)`, bounded
to 32 ASCII letters, digits, underscores or hyphens. Unknown formats retain the verdict
time but give no start/provider; no subprocess, run-directory read or network lookup.

Add `union_minutes(intervals: tuple[tuple[datetime, datetime], ...], *, start: datetime,
end: datetime) -> float`. Clip each valid interval to `[start, end]`, drop disjoint or
reversed intervals, sort and merge touching/overlapping intervals, then sum seconds / 60.
Add `compute_timeline(evidence: GitHubEvidence, review: ReviewFacts,
holder: HolderEvidence | None, *, reviewer_app_id: int) -> FlowTimeline | None`.

Use the push proxy and merge as the execution bounds. CI runs belong to `github-actions`;
reviewer runs have both this App's id and name `red-rail/review`. Include every attempt
started before merge, clip starts to push, and use `min(completed_at or merge, merge)` for
ends. `ci_start` is push to first CI start; `ci` spans first start to last end, including
gaps; `ci_longest` is the maximum single duration. Review start/duration use only the run
whose id matches `ReviewFacts.check_run_id`, not the newest check by name; a carried
receipt sets duration to 0 even if its check timing is absent. `review_start` still needs
that matching run. All reviewer attempts enter the idle union, including the carried
check's actual interval. Judge wait ends at the selected success; judge duration needs
its parsed start. Clip its execution interval to push/merge; a verdict before push makes
the duration segments unknown rather than inventing a zero wait.

Idle is flow minus the union of all CI and reviewer intervals and the judge interval
when its start is known. A successful status read without a parseable judge start adds
no judge interval, as D3 states; a failed run or status read makes idle unknown. Missing
start timestamps on otherwise relevant runs also make their dependent segments and idle
unknown. A successful empty run list proves no execution intervals, but yields no CI
duration sample. Holder uses its own start through merge; it may start before the final
push and is not shortened to the execution window. Reject a holder start after merge.
Absent holder evidence gives `holder` and `holder_approx` as `None`.

Tests (`tests/test_flow.py`):

- `test_push_proxy_selects_ci_suites_and_clamps_to_creation`: other Apps, reused heads,
  post-merge suites and an unread/empty suite list cannot manufacture a push.
- `test_segments_include_every_attempt_and_union_overlaps`: two CI attempts and overlapping
  reviewer/judge execution give hand-calculated wall time, longest job and idle without
  double counting; a gap counts in CI wall time but remains idle if nothing else ran.
- `test_runs_are_clipped_and_post_merge_evidence_is_ignored`: absent/late completion ends
  at merge; post-merge starts/statuses change no value; reversed intervals produce no sample.
- `test_review_uses_receipt_check_id_and_app_identity`: a later retry and a foreign App's
  same-named check cannot replace the attested run; missing id/timing produces nulls.
- `test_carried_reviewer_and_judge_do_not_reuse_source_execution`: reviewer duration is
  proved zero; judge carry retains wait and has null duration/start/provider even with a
  source run id in its description; idle includes only execution on the final head.
- `test_missing_evidence_is_unknown_and_true_zero_is_preserved`: failed statuses leave
  CI measured and judge/idle unknown; a measured zero-duration run remains a zero sample.
- `test_judge_run_timestamp_and_provider_are_bounded`: valid timestamp, invalid date,
  unknown description, unsafe provider and start after verdict have explicit results.
- `test_holder_can_begin_before_push_but_not_after_merge`: holder duration uses its own
  interval and keeps the approximation flag.

Verify: `uv run pytest -q tests/test_flow.py`; the tests fail before the change because
`rail.flow` and its functions are absent, and pass after with exit code 0.

### Task 2 — bounded GitHub history and discovery (`src/rail/reviewer/github.py`, `tests/test_reviewer_github.py`)

Modify both files, consuming the evidence types created in Task 1. Keep existing
`CheckRun`, `CommitStatus`, `check_runs`, `commit_statuses`, `pull`, `merged_pulls` and
`ready_for_review_at` defaults and return types intact (read list above). Add these methods:

- `check_suites(repository: str, sha: str) -> list[SuiteEvidence]`: GET the commit's
  `check-suites`, with `per_page=100`; parse id, App id/slug and `created_at`.
- `flow_check_runs(repository: str, sha: str) -> list[RunEvidence]`: GET the commit's
  `check-runs`, with `filter=all`, `per_page=100`, no App or check-name filter. Parse all
  fields of `RunEvidence`, allowing missing start/completion as `None`.
- `status_history(repository: str, sha: str) -> list[StatusEvidence]`: GET the commit's
  `statuses?per_page=100` through `_paged_list`; preserve every entry and timestamp,
  including repeated context/creator pairs. Do not use combined/latest status APIs.
- `flow_merged_pulls(repository: str, *, base: str, since: datetime,
  until: datetime) -> FlowDiscovery`: GET closed pulls sorted by update descending with
  the requested base and `per_page=100`. Build `FlowPull` directly from `number`,
  `head.sha`, `merge_commit_sha`, `created_at` and `merged_at` in the list response.

Suites and runs have object-shaped pages with `check_suites`/`check_runs` lists and
`total_count`. Follow `_request_page` links under `MAX_PAGES`, refuse repeated cursors,
wrong shapes, inconsistent totals and a final unique-id count unequal to `total_count`.
History uses `_paged_list`'s existing limits. Preserve actual completion times for cache
use; merge filtering belongs to Task 1. Require valid UTC dates and positive ids, reject
booleans as ids, and turn malformed entries into `GitHubError`, not a partial success.

Discovery stops only after processing a page whose last update predates `since`. A
`merged_at: null` entry is unmerged and ignored. Include both window boundaries and
exclude future merges. A recognizable merged entry with missing/invalid head, creation,
merge time or merge commit is counted once in `unread_prs`; it does not hide valid PRs on
the page. Validate both SHAs as 40 or 64 hexadecimal characters and normalize to lowercase;
a nonempty invalid SHA is still unread. An entry missing merge information is unread,
not proved unmerged. Invalid
pagination/order metadata fails discovery for the repository. Deduplicate by PR number.
No call to `pull`, `ready_for_review_at` or an issue timeline occurs during discovery.
No Actions endpoint, new transport, retry policy or authentication path is introduced.

Tests (`tests/test_reviewer_github.py`, routed `httpx.MockTransport` with captured queries):

- `test_flow_checks_read_all_attempts_with_app_and_timestamps`: two pages include retries,
  null completion and multiple Apps; query is `filter=all`, with no App filter.
- `test_flow_suites_paginate_and_keep_ci_identity`: id, slug and creation survive every page.
- `test_flow_status_history_keeps_older_success`: a post-merge status on page one cannot
  erase a pre-merge success on page two; old `commit_statuses` still reduces contexts.
- `test_flow_readers_refuse_partial_or_unbounded_pages`: parameterize all four readers
  for second-page 403/500, external links, repeated/endless cursors, oversized response,
  malformed dates/shapes and mismatched totals where applicable; no partial list escapes.
- `test_flow_discovery_has_no_per_pr_call`: capture only list requests; main/window filtering,
  full final head versus merge SHA, closed-unmerged exclusion and early stop are pinned.
- `test_flow_discovery_counts_bad_pr_without_losing_good_pr`: bad detail and missing merge
  fields increase `unread_prs`; a valid neighbour remains, with no fallback detail request.

Verify: `uv run pytest -q tests/test_reviewer_github.py`; the new tests fail before the
change because the four readers are absent, and pass after; existing reader tests remain green.

### Task 3 — receipt and queue evidence (`src/rail/flow_sources.py`, `src/rail/reviewer/config.py`, `tests/test_flow_sources.py`)

Create `flow_sources.py` and its test file; modify `RepositoryConfig` at
`src/rail/reviewer/config.py:18` with `merge_queue: Path | None = None`. Expand `~` for a
non-null path with a field validator; do not resolve or open it while loading configuration.
This path is private configuration and never enters a contract model or a public fixture.

Add `review_facts(records: list[Record] | None, pull: FlowPull) -> ReviewFacts`, consuming
Task 1's types and `Record` at `src/rail/ledger/__init__.py:239`. `None` is a failed ledger
read and returns three null fields. For a successful read, filter `REVIEW_VERDICT` records
by repository AND PR, keep `recorded_at <= merged_at`, deduplicate by digest, sort by
`(recorded_at, digest)`, and count rounds over all heads of this PR. Use exactly
`rounds.JUDGED_ROUNDS` (`src/rail/reviewer/rounds.py:22`) and exclude `carried_from`, as
`loop_state` does at lines 85–88. Do not require `providers`, import autonomy's predicate,
or parse findings just to count. Mechanical, closure, awaiting-ruling and no-verdict
receipts do not add a round; successful empty history proves rounds 0.

For final-verdict metadata select the latest matching receipt whose `data["sha"]` is
the final head. Its `carried_from` determines `carried`; its `check_run_id` identifies the
run to measure. Accept positive integer ids and legacy decimal-string ids, rejecting
booleans. Missing final receipt gives unknown carried/id, not false; a missing check id
does not erase a known carried flag or the round count. Never follow `source_check_run_id`
for a carried run, or use a receipt recorded after merge, including a late replay.

Define `QueueTurn(pr: int, sha: str, merged_at: datetime, holder_since: datetime | None)`
as a frozen slotted dataclass and `QueueReadError(ValueError)`. Add
`read_queue(path: Path) -> tuple[QueueTurn, ...]`: one bounded binary read of at most
`MAX_QUEUE_BYTES + 1`, with `MAX_QUEUE_BYTES = 8 * 1024 * 1024`, strict UTF-8 JSON and a
`.merged` list; ignore unrelated top-level journal state. Validate positive PR numbers,
nonempty SHAs, aware UTC timestamps and merge order. Unreadable, oversized, malformed or
out-of-order data raises `QueueReadError`, without including content or the path in its
message. Do not silently shorten the journal to the publication window.

Add `holder_evidence(turns: tuple[QueueTurn, ...], pull: FlowPull) -> HolderEvidence | None`.
Match both PR and final head; choose the latest matching merged turn. End the returned
interval at the GitHub merge, even if the journal's recording of that turn is later.
The journal timestamp establishes turn order, not a substitute delivery endpoint.
Prefer its `holder_since`;
otherwise use the immediately preceding journal turn's merge, even outside the window,
and set `approximate=True`. The first turn without a start is unknown. Refuse a start
after merge; never use the current holder's state to reconstruct an old turn. Task 7
reads this journal anew for each declared repository; absence of a declaration is
not an unread queue. Document the request for the queue owner to persist `holder_since`;
this task neither edits the queue owner nor writes its journal.

Tests (`tests/test_flow_sources.py`, `Record.build` and temporary synthetic journals):

- `test_rounds_match_loop_state_across_heads`: legacy round `None`, rounds 1–3, repeated
  round 3 and carried/mechanical/closure/awaiting/no-verdict receipts match the existing rule.
- `test_receipts_match_repository_pr_head_and_pre_merge_time`: same-number PRs in two
  repositories cannot share metadata; newest receipt/check id on the final head wins;
  a late replay and a post-merge carried receipt are ignored.
- `test_unread_ledger_is_not_empty_history`: failed read gives nulls; an empty successful
  read gives rounds 0 and unknown carry; a missing check id keeps known receipt metadata.
- `test_queue_prefers_holder_since_and_marks_previous_merge_fallback`: both interval
  sources and the predecessor outside the window give exact expected durations/flags.
- `test_queue_first_turn_wrong_head_and_current_holder_are_unknown`: no fabricated start.
- `test_queue_bad_order_bad_dates_and_size_are_failed_reads`: parameterize malformed,
  unreadable, oversized and reverse-time input; errors reveal neither path nor journal data.
- `test_merge_queue_config_is_optional_and_expands_path`: existing configuration works;
  an explicit path expands and is not opened during validation.

Verify: `uv run pytest -q tests/test_flow_sources.py`; the tests fail before the change
because the module and configuration field are absent, and pass after with exit code 0.

### Task 4 — GitHub evidence cache on the host (`src/rail/flow_cache.py`, `tests/test_flow_cache.py`)

Create both files. Add `default_cache_root() -> Path`, using
`$XDG_STATE_HOME/red-rail/flow` or `~/.local/state/red-rail/flow`. Add
`cache_key(pull: FlowPull, *, version: str = "rail-flow/v1") -> str`: SHA-256 of the
canonical JSON tuple `(repository, number, merge_sha, version)`. Store the full final
head in the entry and reject a head mismatch; never substitute merge SHA for the head
when requesting checks. Filenames are digest-derived, not raw repository or PR strings.

Add `CachedGitHub(evidence: GitHubEvidence, fetched_at: datetime, complete: bool)` as a
frozen slotted dataclass; serialize only these fields, their Task 1 nested fields, and
the key/version. Add `is_complete(evidence: GitHubEvidence, *, now: datetime) -> bool`:
all three reads succeeded; merge is strictly older than 24 hours; every run with a
start before merge has status `completed` and a non-null actual completion at or before
`now`. Runs starting after merge cannot prevent completeness. A null start on a relevant
run cannot prove completeness. Never mark an early fetch complete merely because time
passes; an incomplete entry must be fetched again after the 24-hour threshold.

Add `FlowCache(root: Path)` with `get_or_read(pull: FlowPull, *, github: GitHubApp,
now: datetime) -> GitHubEvidence`. Reuse only a validated complete entry with the correct
key, version, head and pull metadata. Otherwise read Task 2's suites, runs and status
history independently on `pull.head_sha`, converting a failed read to the corresponding
`None` while keeping successful siblings. Write a refreshed entry, including incomplete
ones. A failure must not preserve an old complete claim. Catch filesystem, decoding,
shape and version errors on load, drop the bad entry and fetch again. Cache I/O errors
are misses and do not stop collection. Write atomically in a 0700 directory with 0600
files; cache content may include private descriptions, so it is never copied into the tree.
When catching a GitHub read failure, emit only the slug, `checks` or `statuses`, and the
exception class to stderr before converting the field to `None`. This preserves the
diagnostic without adding error strings or exception objects to cached/public data.

Use no receipts, queue turns, computed segments or coverage in a cache entry. The whole
window is discovered on every publication; cache absence cannot remove a merged PR.
Use the timestamp of the current publication, not the wall clock inside a pure function.

Tests (`tests/test_flow_cache.py`, counting fake GitHub and `tmp_path`):

- `test_complete_cache_reuses_without_any_per_pr_call`: mature completed evidence is read
  once; a second call performs zero suite/run/status calls and returns identical inputs.
- `test_cache_key_separates_repository_pr_merge_sha_and_version`: also reject mismatched
  head/metadata; same final head in two PRs cannot collide.
- `test_incomplete_cache_is_reread_until_completed_and_older_than_24h`: pin exactly 24 h,
  a run still active at merge and later completion, and an early entry aging into eligibility.
- `test_status_permission_recovery_refetches_incomplete_entry`: 403 then success supplies
  judge evidence; the first read keeps CI, but is never complete.
- `test_corrupt_wrong_version_and_unwritable_cache_are_misses`: truncated JSON, wrong
  types/key and I/O failure cannot hide discovery or fail a publication.
- `test_cache_contains_only_github_inputs_and_has_private_modes`: no receipts/queue/metrics
  are persisted; modes and atomic replacement are checked.

Verify: `uv run pytest -q tests/test_flow_cache.py`; the tests fail before the change
because `rail.flow_cache` is absent, and pass after with exit code 0.

### Task 5 — repository distributions and recent timelines (`src/rail/flow.py`, `tests/test_flow.py`)

Modify Task 1's files. Add frozen slotted data classes
`Percentiles(median: float | None, p90: float | None, n: int)` and
`RepositorySummary(repository: str, merged_prs: int, carry_rate: float | None,
segments: dict[Segment, Percentiles])`. Add
`segment_percentiles(values: tuple[float, ...]) -> Percentiles`: statistics median and
nearest-rank p90 (`sorted[ceil(0.9 * n) - 1]`), unrounded; empty gives `(None, None, 0)`.
Add `summarize_repository(repository: str, timelines: tuple[FlowTimeline, ...]) -> RepositorySummary`.
Compute every segment independently from its non-null values. Exclude `carried is True`
from review and `judge_carried is True` from judge distributions; no other segment is
excluded for carry. Carry rate uses only PRs with a known final carried flag; an unread
ledger or missing final receipt leaves that PR out of its denominator. Empty denominator
gives null. `merged_prs` counts kept timelines; Task 7 reports excluded PRs separately.
No median is averaged from other medians, and there is no aggregate across repositories.

Add `recent_timelines(timelines: tuple[FlowTimeline, ...], *, limit: int = 20) -> tuple[FlowTimeline, ...]`.
Require `0 <= limit <= 20`; select globally by merge descending, then repository and PR
ascending for deterministic ties. Summary membership uses all kept window timelines,
not just recent. Include a read repository with no merges as an empty summary rather
than treating it as missing or omitting it for inactivity.

Tests (`tests/test_flow.py`):

- `test_percentiles_use_per_segment_samples_and_nearest_rank`: missing values change only
  that segment's n; [1, 2, 2, 3] gives median 2 and p90 3; zero is a valid sample.
- `test_carry_exclusions_are_local_to_review_and_judge`: synthetic fresh/carried heads
  change only the two specified distributions; wait/flow/holder remain included.
- `test_carry_rate_excludes_unknown_ledger_and_empty_denominator`: known true/false values
  determine the rate; unknown flags neither count as false nor lower the rate.
- `test_recent_is_global_twenty_with_deterministic_ties`: shuffled multi-repository input
  yields exactly the latest twenty, while summaries still include older observations.
- `test_read_empty_repository_has_null_distributions_and_zero_samples`: all ten keys
  are present with n 0, no duration fabricated, carry rate null.

Verify: `uv run pytest -q tests/test_flow.py`; the new tests fail before the change
because the aggregation functions are absent, and pass after with exit code 0.

### Task 6 — strict rail-flow/v1 contract and size budget (`src/rail/metrics_contract.py`, `contracts/rail-metrics-v1/`, `tests/test_metrics_contract.py`)

Modify the model and test files and README; create `flow-only.json`, `flow-full.json`,
`flow-partial.json`, `flow-empty.json` and `flow-unknown-version.json`. Keep existing
fixtures byte-equivalent after compact serialization. Reuse `StrictModel`, `Count`,
`NonNegative`, `Ratio` and `UtcDatetime` from `metrics_contract.py:19–40`. Define these
strict frozen Pydantic models, with no source-specific fields. Their configuration is
`ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)` so all flow
durations and ratios reject nonfinite numbers:

- `RepositorySlug = Annotated[str, Field(pattern="^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
  max_length=201)]`; unlike `Name`, it preserves the owner/name identity.
- `PositiveCount = Annotated[int, Field(gt=0)]`, used for PR numbers and window days.
- `FlowPercentiles(median: NonNegative | None, p90: NonNegative | None, n: Count)`;
  n 0 requires both percentiles null, n positive requires both measured.
- `FlowSegments`: the ten `NonNegative | None` fields named by `SEGMENTS` in Task 1.
- `FlowRepository(repository: RepositorySlug, merged_prs: Count, carry_rate: Ratio | None,
  segments: dict[Segment, FlowPercentiles])`; require exactly all ten segment keys.
- `FlowRecent(repository: RepositorySlug, number: PositiveCount, head_sha: str,
  merged_at: UtcDatetime, segments: FlowSegments, rounds: Count | None,
  carried: bool | None, judge_carried: bool | None, judge_provider: str | None,
  holder_approx: bool | None)`; head is exactly twelve lowercase hex characters, provider
  matches Task 1's 32-character label bound. No full SHA, run id or description.
- `FlowCoverage(missing: tuple[RepositorySlug, ...],
  unread_prs: dict[RepositorySlug, Count],
  unread_segments: dict[RepositorySlug, dict[Segment | Literal["ledger"], Count]])`.
- `FlowBlock(version: Literal["rail-flow/v1"], generated_at: UtcDatetime,
  window_days: PositiveCount, window_start: UtcDatetime,
  repositories: tuple[FlowRepository, ...], recent: tuple[FlowRecent, ...],
  coverage: FlowCoverage)`; repository names unique/sorted, missing unique/sorted/disjoint
  from repositories, recent at most twenty in Task 5 order, and each recent repository
  belongs to the read population. Coverage map keys belong to read repositories.
- `Answer.flow: FlowBlock | None = None`, independently optional beside the three existing blocks.

Minutes round to two decimals and carry rates to three only on conversion into models.
`encode` continues omitting null top-level blocks while keeping nested nulls and writing
UTC `+00:00` timestamps. Set `MAX_BYTES = 32 * 1024` and measure compact bytes for every
fixture, asserting the resulting exact lengths. Update the previous cap test's hard-coded
12 KiB and construct oversize data from the new cap so it really exceeds 32 KiB.
Current compact byte baselines, measured while planning: `full.json` 8,470;
`partial.json` 7,196; `partial-delivery.json` 6,771; `delivery-only.json` 2,262;
`delivery-v2.json` 1,365; `empty.json` 2; `unknown-version.json` 8,470.

`flow-full.json` combines the eleven-repository existing population, both delivery
versions, autonomy, all ten flow distributions and twenty synthetic recent entries with
non-null fields. Build its expected numbers deliberately from a stated synthetic population,
including carry exclusions; do not weaken fields or drop delivery v2 just to fit. Partial
fixtures distinguish repository discovery failure, one omitted PR, statuses refusal,
ledger failure and undeclared queue. The flow-empty fixture has read repositories with
no merges; flow-only proves independence. Unknown version uses `rail-flow/v2`.

The README states four independently optional blocks, the 32 KiB format ceiling and the
separate 16 KiB publication limit pending `af2ba8e7`. Its common `windows_days: [7, 30]`
rule must explicitly apply to existing blocks; flow instead carries one `window_days` and
`window_start`. Explain field shapes, minute units, the check-suite push proxy, nulls,
carry exclusions, coverage counts, recent ordering and checking the major version before
reading a block. Amend the identifier exclusion clause only for D4's repository/PR/short
SHA/provider fields; raw run ids, descriptions, paths and diagnostics stay excluded.

Tests (`tests/test_metrics_contract.py`):

- `test_flow_fixtures_validate_and_round_trip`: all new valid fixtures and independent
  combinations serialize compactly; existing fixture bytes and the 8,470-byte baseline remain.
- `test_flow_eleven_repositories_twenty_recent_and_delivery_v2_fit_32k`: assert exact
  measured compact lengths and the full-answer ceiling; no claimed size based on estimates.
- `test_flow_strict_fields_order_and_coverage`: reject negative/nonfinite minutes, string
  counts, boolean numbers, bad/naive dates, inconsistent percentile n, bad repository/short
  SHA/provider, duplicates, missing segment keys, unsorted/overlong recent and invalid coverage.
- `test_flow_unknown_major_and_private_or_relay_fields_are_refused`: reject unknown major,
  raw run id/description/path, arbitrary error text and all reserved relay-envelope fields.
- `test_answer_size_bound_is_32k`: exact bound succeeds; one byte above fails with `TooLarge`.

Verify: `uv run pytest -q tests/test_metrics_contract.py`; the new tests fail before the
change because flow is an extra field and `MAX_BYTES` is 12 KiB, and pass after.

### Task 7 — independent collection, capped publish and serve (`src/rail/flow_publish.py`, `src/rail/publish.py`, `src/rail/commands/metrics.py`, `src/rail/serve.py`, `tests/test_flow_publish.py`, `tests/test_cli_metrics_publish.py`, `tests/test_publish_origin_main.py`, `tests/test_serve.py`)

Create `flow_publish.py` and `test_flow_publish.py`; modify the remaining named files.
Add `FlowCollection(repositories: tuple[RepositorySummary, ...],
recent: tuple[FlowTimeline, ...], coverage: FlowCoverage)` as a frozen slotted dataclass in
`flow_publish.py`. Add `collect_flow(config: ReviewerConfig, *, now: datetime,
window_days: int, github: GitHubApp, cache: FlowCache,
records_for: Callable[[RepositoryConfig], list[Record] | None]) -> FlowCollection` and
`flow_block(collection: FlowCollection, *, generated_at: datetime, now: datetime,
window_days: int) -> FlowBlock`. These consume Tasks 1–6 and produce no ledger writes.
`flow_block` converts each summary's ten percentile entries to `FlowPercentiles`, sorted
by repository, and converts each selected timeline to `FlowRecent` in Task 5 order.
Take `pull.head_sha[:12]` for the public head and copy the nullable metadata unchanged;
never serialize `FlowPull`, `GitHubEvidence` or status descriptions wholesale. Round
only measured minutes to two decimals and carry rates to three. Set window_start to
`now - timedelta(days=window_days)` and retain the supplied shared generated_at.

For each configured slug, call flow's own `flow_merged_pulls` reader with `base="main"`,
the flow window start and `until=now`: one paginated list per repository, with no per-PR
detail or timeline call during discovery. Discovery failure puts only that
slug in `coverage.missing` and logs `pulls`. Never skip flow discovery because a local
fetch, manifest or ledger failed for delivery/autonomy. Discovery unread counts go into
`unread_prs[slug]`. Fetch/reuse GitHub evidence once per selected PR, read receipts once
per repository for this publication, and read the declared queue once per publication.
Run Task 3's matching and Task 1's computation; an unknown push drops that PR and increments
unread_prs once. Suites and runs fail under `checks`; statuses under `statuses`. Do not
increase unread_prs for recoverable evidence failures when push and merge are known.

Kept timelines carry null affected segments. `unread_segments[slug]` counts distinct kept
PRs per unknown applicable segment, not failed calls/attempts. Count CI absence/failure
under its three segment names; review absence/failure under review_start/review; status
absence/refusal under judge_wait/judge as applicable; a failed execution source also counts
idle. A known carried judge's null fresh duration is an exclusion, not an unread segment.
A declared unread/missing queue counts holder; an undeclared queue does not. Ledger read
failure additionally counts `ledger` once per kept PR, makes rounds/carried null and
prevents receipt-dependent review measurement. Actual GitHub reviewer runs still contribute
to idle if their timestamps were read. A successful empty ledger is not a ledger failure.
Omit zero counts from coverage maps. Keep an empty summary for a successful empty discovery.

For source read failures, log only slug, one of `pulls`, `checks`, `statuses`, `ledger`,
`queue`, and exception class to stderr; never stringify source errors, journal paths or
descriptions. Read failures are localized. `FlowCache.get_or_read` emits GitHub errors
before returning failed fields;
`records_for` emits the actual local read error before returning `None`. Do not duplicate
those diagnostics as inferred errors merely because a segment is absent.
At the flow-only assembly boundary, catch an unexpected ordinary exception,
log its current step/class and omit flow rather than abort the existing publication;
do not catch process interruption. Existing non-flow computed-contract errors still fail
the whole command. All-source flow failure can still publish a valid block with all slugs
missing, while successful delivery/autonomy blocks survive.

Extend `publish.collect` (`src/rail/publish.py:70`) with
`window_days: int = 30, flow_cache: FlowCache | None = None`. Perform flow independently
of the existing shared read guards, and call `clock()` once after every block computation.
Reuse a successful local ledger snapshot for `records_for`; retain a failed read as `None`.
Keep `CommittedLedger` for file backends and reviewer credentials for brain backends; if
the manifest cannot be read, flow continues with unknown ledger metadata. Never fall back
to unverified receipts. Do not add a new `SINCE` cut to the flow population.

Keep autonomy's code path, population and calls exactly as on main, including its existing
`merged_pulls` and per-PR `ready_for_review_at` calls, default branch, SINCE bound and error
handling. Flow uses its own discovery and Task 4 cache; do not share discovery with autonomy
or add a readiness cache. The repeated-publication no-per-PR-call criterion counts only
requests made by the flow collector, not autonomy or other existing blocks.

Pass the group's `--window` via `ctx.parent.params["window"]` in the publish callback
(`commands/metrics.py:85`); the historical single-repository command retains its output.
Add `--max-bytes` to publish and serve with `click.IntRange(1, MAX_BYTES)`, default
`16 * 1024`. This is the consumer budget, never a probe that infers confirmation. Its
use above 16 KiB is restricted by the release dependency and documented procedure.
Extend `publish.write(answer: Answer, out: Path, *, max_bytes: int = 16 * 1024) -> int`:
validate `1 <= max_bytes <= MAX_BYTES`, then assemble and encode the answer without flow
first. Preserve the existing fatal policy for invalid non-flow blocks; if that base payload
alone exceeds max_bytes, raise `ValueError` naming only size and budget, keep the old file
untouched and exit 1. Measure the compact candidate with the complete flow block added.
If its size exceeds max_bytes, leave flow absent from this publication and log `flow_size`,
the base byte size, the candidate byte size and the cap in force to stderr. Publish the
base answer with delivery/delivery_v2/autonomy byte-identical to publication without flow. Otherwise validate
and encode the full answer. Use the existing atomic write for the selected payload. Never
trim recent, repositories, segments or coverage inside flow, and never fail publication
because adding flow exceeds the cap in force, including the contract ceiling.

Extend `create_server(file: Path, *, port: int = 9230,
max_bytes: int = 16 * 1024) -> ThreadingHTTPServer` at `serve.py:12`. Validate the budget,
read at most `max_bytes + 1`, require both that budget and contract MAX_BYTES, validate
`Answer`, and re-encode. The server never collects, reaches GitHub, opens a ledger or
exposes raw bytes; preserve existing status codes, method/path handling and headers.
The publication success message must not assume autonomy exists: use flow read/expected
when only flow was collected, otherwise the existing autonomy coverage. If no existing
block is readable but flow discovery succeeds, publish flow alone; if no block is readable,
retain the existing refusal. An empty successful discovery counts as readable.
Move the existing no-repository refusal at `publish.py:200` after flow collection. Build
the existing blocks only when their own reads succeeded, and leave them `None` otherwise;
do not construct a fictitious autonomy total to enable flow-only output. The final
readability check considers successful discovery, not a flow block containing only missing
repositories, and precedes size-based flow omission; omission alone introduces no new
readability refusal. Flow's independent discovery still runs when autonomy's reads fail.

Tests (`tests/test_flow_publish.py`, read-only fakes, frozen now, temporary cache):

- `test_flow_covers_every_watched_repository_independently`: a missing manifest/failed
  delivery ledger still permits GitHub flow, and list failure affects only flow coverage.
- `test_flow_failure_matrix_keeps_or_excludes_exactly_once`: parameterize bad PR detail,
  suites, runs, statuses, ledger and queue; assert unread_prs, every affected null segment,
  per-segment counts, carried denominator and stderr step names with no source text.
- `test_flow_second_publication_has_no_per_pr_requests`: prefill mature flow evidence
  entries, publish twice with no new merge, and count only requests made by the flow
  collector. Its second run makes only bounded discovery list requests, with no per-PR
  detail/timeline/check/status calls. Autonomy's existing calls stay unchanged and are
  excluded from this count. An incomplete flow entry is fetched again.
- `test_flow_reloads_local_sources_even_on_complete_github_cache`: changed pre-merge
  receipt availability and a newly declared queue affect results without cache invalidation;
  a receipt actually recorded after merge remains ignored.
- `test_flow_window_and_main_are_independent_of_autonomy_since_and_default_branch`:
  custom window, inclusive boundaries, default branch other than main, and a merge before
  autonomy SINCE pin D1 without changing existing autonomy values.
- `test_flow_assembly_failure_does_not_abort_other_blocks`: injected bad flow data omits
  only flow and logs safely; injected invalid non-flow values keep the existing fatal policy.

Tests (modify existing integration suites):

- `test_publish_window_reaches_flow_and_preserves_historical_metrics` in
  `test_cli_metrics_publish.py`: Click window wiring and existing command byte outputs.
- `test_publish_leaves_flow_absent_when_it_would_exceed_the_cap` in that file: a base
  answer fitting the default cap publishes successfully when adding the complete flow
  block would put it between 16 and 32 KiB; flow is absent and stderr has `flow_size` and
  both measured byte sizes and the cap in force. Explicit confirmed-budget setup includes
  the full flow block; one byte above either cap omits flow without trimming it, including
  above MAX_BYTES.
- `test_publish_flow_omission_preserves_other_blocks_byte_identically` in that file:
  delivery/delivery_v2/autonomy and the resulting base payload match publication without
  flow byte for byte, with frozen timestamps, unchanged autonomy population and calls.
- `test_publish_non_flow_consumer_cap_refuses_before_atomic_replace` in that file: a base
  answer alone above the selected cap retains the fatal policy and keeps the old file.
- `test_flow_only_publication_and_all_blocks_unread` in that file: flow-only success prints
  valid coverage without an autonomy assertion; no readable block refuses with no replacement.
- `test_flow_keeps_committed_and_local_receipt_integrity` in `test_publish_origin_main.py`:
  verified main/local deduplication is retained; corrupt receipts make ledger metadata unknown.
- `test_serve_flow_and_consumer_cap` in `test_serve.py`: valid flow alone/coexisting blocks
  are reserialized; 16–32 KiB is 503 at the default and 200 under a confirmed 32 KiB setup;
  strict invalid/oversized flow remains 503; absent file remains `{}`.

Verify: `uv run pytest -q tests/test_flow_publish.py tests/test_cli_metrics_publish.py tests/test_publish_origin_main.py tests/test_serve.py`; the new tests fail before the change
because collection/window/budget interfaces are absent and serve refuses flow; they pass
after with exit code 0. Update existing size-sensitive fixtures to use the selected consumer
budget rather than assuming it equals MAX_BYTES, then run `make ci`.

### Task 8 — host acceptance and release dependency (`docs/metrics-publication.md`)

Modify the runbook with the following named acceptance procedures. It consumes the final
CLI, cache and contract interfaces from Tasks 1–7; it produces a private acceptance report
and only a redacted pass/fail, measured byte size and test exit codes for the release record.
No new code, automatic host operation, private fixture or analysis report is committed.

`acceptance_relay_budget`: check ticket `af2ba8e7` for confirmation of both deployed relay
read caps, not merely a planned change. Record the confirmation privately. Until both are
at least 32 KiB, use `rail metrics publish --max-bytes 16384` and
`rail metrics serve --max-bytes 16384`; do not release a publisher producing a larger
answer. After confirmation, use `--max-bytes 32768` consistently for publisher and server,
including their host unit configuration. Check the actual served and relayed response
byte lengths and successful JSON validation, not just the local file or schema limit.
The runbook replaces its old unconditional 12 KiB acceptance sentence with this two-stage rule.

`acceptance_historical_population`: use a private directory outside all checkouts for the
existing 2026-10-14 analysis, journal and report. Load the exact seventeen brain-v42 PR
numbers and their analysis push times from that private analysis; do not choose a sample
from recent alone. Run `rail metrics --window 30 publish --max-bytes 32768` after the budget
confirmation, with the installed private reviewer configuration. Match those PRs against
discovery/cache, re-read local evidence and call `compute_timeline`, then
`summarize_repository` on exactly those seventeen timelines. Independently recompute
the same cohort's medians from its emitted segment values; require holder 22.3 ± 1 min,
CI wall 19.8 ± 1 min, and longest CI job 19.7 ± 1 min. Check each `push_proxy` against the
analysis push, requiring absolute difference at most one minute on at least fifteen PRs.
Report reused-SHA discrepancies and exact versus approximate holders privately. The
published thirty-day repository median may include other PRs: never compare it to the
seventeen-PR target without isolating the same cohort. If another twenty recent merges
have displaced cohort entries, reconstruct them from private cached GitHub inputs and
fresh local sources; do not enlarge public recent or publish an arbitrary cohort option.

`acceptance_coverage_and_permissions`: compare configured repository slugs with the union
of flow repositories and coverage.missing. Require every slug exactly once. Confirm the
reviewer App's private status read grant under `b7c8b823`; where evidence still cannot
be read, inspect null judge segments and their counts, with diagnostic steps only on
stderr. Verify no journal path, description, run id or machine identifier occurs in the
served payload. No secret is printed or placed on a command line.

`acceptance_cache_cost`: once all flow GitHub evidence entries for the measured population
are complete, run the same publication twice with no new merge. Use a private counting
wrapper around `GitHubApp._request_page` and `_request`, scoped to requests made by the
flow collector, to classify GET paths without auth headers or response content. Require
zero per-PR detail/timeline/check/status reads by flow on the second run; its paginated
discovery remains expected. Autonomy's existing requests are unchanged and excluded from
this measurement. Then use a separate temporary cache with one failed status read followed
by success and prove it is retried and filled; do not modify the host's real permissions
just to induce a failure.

Tests (manual procedures, with evidence kept privately):

- `acceptance_relay_budget`: before confirmation, adding flow above 16 KiB leaves flow
  absent, logs the step and both sizes to stderr, and publishes the other blocks unchanged;
  the confirmed 32 KiB answer includes flow and survives both relays without truncation.
- `acceptance_historical_population`: all seventeen identities, three target medians and
  the fifteen-of-seventeen push-proxy bound are checked against the original cohort.
- `acceptance_coverage_and_permissions`: complete slug accounting, unknown judge values
  with counts, and public-field exclusions are checked on the actual served answer.
- `acceptance_cache_cost`: the flow collector makes no per-PR calls on the second mature
  publication; the failed status entry is retried; each publication still reads local
  ledger/queue evidence. Autonomy's code path, population and calls stay exactly as on main.

Verify: `make ci` and, on the host after relay confirmation,
`rail metrics --window 30 publish --max-bytes 32768`; record exit code 0 and run the four
named procedures. Before implementation these acceptance checks fail because no flow
block exists; afterwards each must pass its stated numerical or observable criterion.
The runbook change alone does not claim host acceptance. Release requires the measured
evidence and an independent non-Claude, non-producer judge, as the spec states.

## Planning interpretations to preserve

- D4 names required contents but does not freeze JSON field names or window layout. This
  plan fixes a single `window_days`/`window_start`, owner/name repository keys, nested
  segment values and per-repository coverage maps; it does not add a second flow window.
- D3's holder interval can predate the final push. Only active execution intervals are
  clipped to push/merge for idle; holder retains D2's independently defined queue interval.
- D6's discovery and cache belong only to flow: one paginated list per repository, without
  a per-PR timeline call. The second-publication no-per-PR-call criterion measures only
  the flow collector's requests; autonomy's code path, population and calls remain exactly
  as on main, with no shared discovery or readiness cache.
- D3 explicitly omits a judge interval from idle when no start is available. Successful
  history with an unknown description follows that rule; a failed status read makes idle
  unknown because it did not establish the execution evidence. Keep these cases distinct.

## Review focus

- Reused final heads, overlapping retries and checks running beyond merge: Task 1 adds `test_push_proxy_selects_ci_suites_and_clamps_to_creation` and `test_runs_are_clipped_and_post_merge_evidence_is_ignored`.
- Full history hidden by latest-only reads or partial pagination: Task 2 adds `test_flow_status_history_keeps_older_success` and `test_flow_readers_refuse_partial_or_unbounded_pages`.
- Cross-repository receipt collisions, carried source ids and late replay: Task 3 adds `test_receipts_match_repository_pr_head_and_pre_merge_time`; Task 1 adds `test_carried_reviewer_and_judge_do_not_reuse_source_execution`.
- Historical queue turns without a start, empty periods and corrupt order: Task 3 adds `test_queue_prefers_holder_since_and_marks_previous_merge_fallback` and `test_queue_bad_order_bad_dates_and_size_are_failed_reads`.
- Permission recovery, falsely complete caches and larger-schema answers truncated by consumers: Task 4 adds `test_status_permission_recovery_refetches_incomplete_entry`; Task 7 adds `test_flow_second_publication_has_no_per_pr_requests`, `test_publish_leaves_flow_absent_when_it_would_exceed_the_cap` and `test_publish_flow_omission_preserves_other_blocks_byte_identically`.
