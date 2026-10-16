# Metrics publication

Install the units from this checkout and start the publisher timer and local server:

```sh
systemctl --user link "$PWD"/contrib/systemd/user/rail-metrics-publish.service
systemctl --user link "$PWD"/contrib/systemd/user/rail-metrics-publish.timer
systemctl --user link "$PWD"/contrib/systemd/user/rail-metrics-serve.service
systemctl --user enable --now rail-metrics-publish.timer
systemctl --user enable --now rail-metrics-serve.service
```

The publisher runs hourly and after a missed scheduled run. A block is stale when its
`generated_at` timestamp is more than two hours old (spec D9).

red-monitor's agent probes the local `/rail-metrics` endpoint and relays the published
blocks. The server listens on loopback only.

## Host acceptance and release dependency

Keep the acceptance report, analysis, journal, cache inputs and any counting wrapper in
`<private-dir>`, outside every checkout. Do not commit them. The release record contains only
redacted pass/fail results, measured byte sizes and test exit codes. Do not include secrets,
private paths, machine identifiers, run ids, journal paths or raw diagnostics in that record.

### `acceptance_relay_budget`

Check ticket `af2ba8e7` for confirmation that **both deployed relays** can read at least 32 KiB;
a planned change is not confirmation. Record the confirmation privately. Until both caps are
confirmed, run the publisher and server with `--max-bytes 16384`, including the corresponding
host unit configuration. Do not release a publisher that can produce an answer larger than
16 KiB. Once both caps are confirmed, set `--max-bytes 32768` consistently for `rail metrics
publish`, `rail metrics serve` and their host unit configuration.

Check the actual served response and the response received after each relay: measure their
byte lengths and validate their JSON. A local publication's size or the schema ceiling alone
does not establish that the relays preserved the answer. Before confirmation, exercise a
publication whose flow block would push the answer above 16 KiB: flow must be absent, its
failing step and both sizes must appear on stderr, and the other blocks must be unchanged.
After confirmation, verify that the 32 KiB answer includes flow and passes both relays without
truncation. Record measured sizes and pass/fail privately.

### `acceptance_historical_population`

Use the existing 2026-10-14 analysis in `<private-dir>` to load its exact seventeen brain-v42
PR identities and analysis push times; do not select a sample from `recent`. After relay budget
confirmation, run `rail metrics --window 30 publish --max-bytes 32768` with the installed
private reviewer configuration. Match all seventeen PRs against discovery/cache, re-read local
ledger and queue evidence, compute their timelines with `compute_timeline`, then call
`summarize_repository` on exactly those seventeen timelines.

Independently recompute cohort medians from the emitted segment values. Require holder 22.3
minutes ± 1 minute, CI wall 19.8 minutes ± 1 minute, and longest CI job 19.7 minutes ± 1
minute. Compare each `push_proxy` with its analysis push and require an absolute difference of
at most one minute on at least fifteen of the seventeen PRs. Record reused-SHA discrepancies
and exact versus approximate holders privately. The published thirty-day repository median
may contain other PRs; compare it with these targets only after isolating the same cohort.
If other recent merges have displaced cohort entries, reconstruct those entries from private
cached GitHub inputs and fresh local sources. Do not enlarge public `recent` or add a public
arbitrary-cohort option.

### `acceptance_coverage_and_permissions`

Compare configured repository slugs with the union of `flow.repositories` and
`coverage.missing`; require every configured slug exactly once. Confirm the reviewer's private
status-read grant under ticket `b7c8b823`. If evidence remains unreadable, inspect null judge
segments and their coverage counts; diagnostic steps go to stderr only. Inspect the actual
served answer and verify it contains no journal path, status description, run id or machine
identifier. Never print a secret or place one on a command line.

### `acceptance_cache_cost`

Once all flow GitHub evidence entries for the measured population are complete, run the same
publication twice with no new merge. Use a private counting wrapper around
`GitHubApp._request_page` and `_request`, scoped to flow collector requests. Classify GET paths
only; capture neither authorization headers nor response content. On the second run require
zero flow per-PR detail, timeline, check or status reads. Paginated flow discovery is expected.
Exclude autonomy from this count; its code path, population and calls remain exactly as on main.

Then use a separate temporary cache: make one status read fail, publish, then allow it to
succeed and publish again. Verify the failed entry is retried and filled without changing the
host's real permissions. Confirm each publication re-reads local ledger and queue evidence.

### Acceptance record

Run `make ci` and record its exit code. After relay confirmation, run
`rail metrics --window 30 publish --max-bytes 32768` on the host and record exit code 0.
Complete and privately retain evidence for all four named procedures above. Before
implementation, these acceptance checks fail because no flow block exists; after
implementation, each must meet its stated numerical or observable criterion. This runbook
change alone does not claim host acceptance. Release requires the measured evidence and an
independent non-Claude, non-producer judge, as specified in the delivery flow metrics spec.
