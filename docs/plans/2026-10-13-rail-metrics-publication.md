# Plan: publish the rail's delivery and autonomy metrics for red-monitor

Spec: `docs/specs/2026-10-13-rail-metrics-publication.md` (D1–D10). Contract:
`contracts/rail-metrics-v1/` (frozen at `da33559`, red-rail#100, plus the additive revision of
this pull request: the exclusion clause and the `partial-delivery.json` variant). Operator
rulings `35427fd3` and `69ee7d9c`. Method: spec.

Every task is test first: the test is written, run, and seen failing for the stated reason
before the code changes. `make ci` is green after each task. Nothing here writes to a ledger,
a checkout or GitHub: `publish` reads, `serve` reads one file.

Read before planning (main `068f4f3`):
- `metrics.py`: `compute_metrics`, l.72–164. The DORA part reads the ledger, `gitrepo.commit_timestamp` and the manifest; the conformance part, l.156–164, runs the gates.
- `commands/metrics.py`: a plain `click.command`.
- `cli.py`: every module of `rail.commands` exposes `command`.
- `ledger/__init__.py`: `Record`, `AttestationKind`, `open_ledger(repo, *, client, agent, token_file)`.
- `ledger/brain.py`:
  - `record_from_row`, l.73: `row["kind"]` read outside any guard;
  - `_attestation_records`, l.471–496: every attestation of the project, all tickets, `page.get("items", [])`;
  - `_row_by_key`.
- `tests/fake_brain.py:487`: a last page carries `"next_cursor": None`.
- `reviewer/config.py`: `load_reviewer_config`, `RepositoryConfig(slug, path)`.
- `reviewer/github.py`: `GitHubApp._request` returns the decoded body only, no headers.
- `reviewer/verdict.py`: `as_attestation_data` writes `repository`, `pr`, `round`, `mode`, `providers`, `verdict`.
- `reviewer/service.py:254–268`: verdicts and rulings are matched on `repository` AND `pr`.
- `commands/reviewer.py`: the `review_ruling` payload: `repository`, `pr`, `finding`, `ruling`.
- `actor.py`: `operator`, `agent:*`, `service:*`.

Two pull requests: tasks 1–6 (`rail metrics publish`), then tasks 7–8 (`rail metrics serve`
and the user units). Builds go to ha codex, one task per run. The session repeats red/green and
`make ci` itself, because the ha sandbox does not run pytest.

## Tasks

### Task 1 — delivery values without the gates (`src/rail/metrics.py`)

Split `compute_metrics` without changing its output. Add a frozen dataclass `DeliveryWindow`
with the six delivery fields: `deployments: int`, `deployment_frequency_per_week: float`,
`lead_time_commit_to_deploy_hours: float | None`, `change_failure_rate: float | None`,
`recovery_time_hours: float | None` and `drill_recovery_time_minutes: float | None`. Add a pure
`delivery_window(ledger, project, repo, *, now, window_days) -> DeliveryWindow` holding today's
l.81–154 verbatim: same windowing, same `went_live`/`failed`/`attempts`, same drill pairing.
`compute_metrics` calls it, adds the contract lead time and the conformance exactly as today,
and builds `Metrics` as before. `delivery_window` never imports `rail.gates`.

Tests (`tests/test_metrics.py`):
- `compute_metrics(...).to_dict()` on the existing fixtures, with a frozen `now`, is equal before and after;
- `delivery_window` gives the same six values as `compute_metrics`;
- `delivery_window` never calls the gates: monkeypatch `rail.metrics.run_gates` to raise;
- two deployments of one digest then a named rollback give attempts 2, CFR 0.5 (spec D3);
- a named rollback of an artefact deployed before the window gives `deployments: 0`, CFR 1.0.

Verify: the new tests fail before the change (no `delivery_window`) and pass after; `uv run pytest -q tests/test_metrics.py` exit code 0.

### Task 2 — the contract as strict models (`src/rail/metrics_contract.py`, `contracts/rail-metrics-v1/`)

Pydantic models with `ConfigDict(extra="forbid", strict=True, frozen=True)`:
- `DeliveryWindowModel` and `DeliveryProject(project, target, d7, d30)`;
- `DeliveryBlock(version: Literal["rail-delivery/v1"], generated_at: datetime, windows_days: tuple[Literal[7], Literal[30]], projects)`;
- `AutonomyWindow`, every D4 field plus `window_start`, with `reviewed_prs` and `rulings_per_reviewed_pr`;
- `Coverage(expected, read, missing)`;
- `AutonomyTotal(d7, d30)` and `AutonomyProject(project, d30)`;
- `NotMeasured(what, why)`;
- `AutonomyBlock(version: Literal["rail-autonomy/v1"], generated_at, experimental: Literal[True], windows_days, since, coverage, total: AutonomyTotal | None, projects, not_measured)`;
- `Answer(delivery: DeliveryBlock | None = None, autonomy: AutonomyBlock | None = None)`.

Strict datetimes are validated from JSON text through `Answer.model_validate_json` (strict mode
accepts an RFC 3339 string there); `model_validate` on Python objects is used only with
`datetime` values.

Bounds (spec D7):
- names match `^[a-z0-9][a-z0-9.-]{0,63}$`;
- counts are `int ≥ 0`;
- proportions (`change_failure_rate`, `agent_gesture_ratio`, `first_pass_approval_ratio`) lie in [0, 1];
- quotients, medians, p90, hours and minutes are ≥ 0;
- every metric is nullable; every datetime is timezone-aware UTC;
- `projects` are sorted by name (validator);
- `total` is null exactly when `coverage.missing` is non-empty (validator).

Add `MAX_BYTES = 12 * 1024`, `TooLarge(size)`, and `encode(answer: Answer) -> bytes`:
- compact JSON (`separators=(",", ":")`);
- top-level members that are `None` are omitted; nested `None` values stay `null`;
- every datetime is written as `value.isoformat()` (`+00:00`, never `Z`), through a field serializer, so the frozen fixtures round-trip byte for byte;
- above `MAX_BYTES` it raises `TooLarge` and never truncates.

The additive contract revision (6b59a753, F-100-3) ships with this plan, not with the task:
`README.md` carries the exclusion clause under "Every block", and the new fixture
`partial-delivery.json` is `full.json` with `red-probe` unread. `red-probe` is absent from the
delivery block and from `autonomy.projects`, with `coverage {expected: 11, read: 10, missing:
["red-probe"]}` and `total: null`. The task tests both.

Tests (`tests/test_metrics_contract.py`):
- `full.json`, `partial.json`, `partial-delivery.json`, `delivery-only.json` and `empty.json` validate;
- `unknown-version.json` is refused;
- for each valid fixture, `encode(Answer.model_validate_json(raw))` equals the fixture's compact JSON byte for byte, and `full.json` measures 8,470 bytes;
- a reserved relay key at a block's top level (`served_at`, `source_age_s`, `source`, `available`, `reason`, `payload`) is refused;
- the string `"0.5"` where a ratio is expected is refused (strict);
- a ratio above 1 is refused, a quotient above 1 accepted;
- unsorted projects are refused;
- a non-null total with `missing` non-empty is refused;
- a naive datetime is refused;
- an answer over 12 KiB raises `TooLarge`.

Verify: the tests fail before the change (module absent) and pass after; `uv run pytest -q tests/test_metrics_contract.py` exit code 0.

### Task 3 — autonomy as pure functions (`src/rail/autonomy.py`)

Inputs are plain data, so every rule is testable without brain or GitHub:
- `SINCE = datetime(2026, 10, 4, tzinfo=UTC)`;
- `MergedPull(number: int, merged_at: datetime, ready_at: datetime)`, where `ready_at` is the last ready-for-review event or the creation;
- `ProjectFacts(project: str, repository: str, records: list[Record], merged: list[MergedPull], reverts: list[datetime])`. `repository` is the `owner/name` slug: verdicts and rulings count only when `data["repository"] == repository` and `data["pr"]` is the pull request's number. Brain lists the attestations of every ticket of the project.

`Samples` (frozen dataclass) holds one window's raw observations, before any rounding. Ints:
- `releases`, `operator_gestures`;
- `operator_core` and `agent_core`: gestures over `released`, `deployed`, `rolled_back`, `restored`;
- `merged_prs`, `reviewed_prs`, `rulings`, `agent_rulings`, `awaiting_ruling_passes`;
- `rollbacks`, `reverts`, `incidents`.

Tuples:
- `rounds: tuple[int, ...]`: judged verdicts per pull request of the population, one entry per
  reviewed pull request, `0` for one that has none (spec D4: the median and p90 run over the
  whole population);
- `first_pass: tuple[bool, ...]`: per pull request with at least one judged verdict, whether the first approved;
- `ready_to_merged_hours: tuple[float, ...]`: over all merged pull requests.

Functions:
- `issuer_class(issuer) -> Literal["operator", "agent", "other"]`: `operator`; the `agent:` prefix; anything else is `other`, never an exception (`red-rail-reviewer`, a project name, `service:*`).
- `window_start(now, days) -> datetime = max(now − timedelta(days), SINCE)`, the same for a project and for the total.
- `window_samples(facts, *, now, days) -> Samples`:
  - gesture kinds `released`, `deployed`, `rolled_back`, `restored`, `review_ruling`, drills included, counted when `recorded_at ≥ window_start`;
  - `rollbacks` and `incidents` exclude `data["drill"]`;
  - `reverts` are the commits with dates ≥ `window_start`.
  - Population: `facts.merged` with `merged_at ≥ window_start` whose first matching `review_verdict` has `recorded_at ≥ SINCE`.
  - Each selected pull request brings all its matching verdicts and rulings since SINCE, including those before `window_start`.
  - Judged verdicts: `data["round"]` in {1, 2, 3, "closure"} and a non-empty `providers`.
- `merge(*samples) -> Samples`: integers sum, tuples concatenate.
- `window_values(samples, *, start) -> dict[str, object]`: the `AutonomyWindow` fields.
  - The median is `statistics.median`; p90 is nearest-rank `sorted[ceil(0.9·n) − 1]`.
  - `operator_gestures_per_release = operator_gestures / releases`; `rulings_per_reviewed_pr = rulings / reviewed_prs`; `agent_gesture_ratio = agent_core / (agent_core + operator_core)`; `first_pass_approval_ratio = sum(first_pass) / len(first_pass)`.
  - Ratios are rounded to 3 decimals, hours to 2; an empty denominator gives `None`.
- `active(samples) -> bool`: any of `releases`, `operator_gestures`, `agent_core`, `merged_prs`, `rulings`, `rollbacks`, `reverts` or `incidents` is non-zero.

The total of a window is `window_values(merge(*per_project), start=window_start(now, days))`.
Values are never re-derived from rounded ratios.

Tests (`tests/test_autonomy.py`), one per D4 rule:
- the `since` cut;
- the window edge of spec D4: first verdict on 5 October, merge on 10 October, 7-day window from 6 October;
- a PR whose first verdict predates SINCE is left out of `reviewed_prs` but counted in `merged_prs`;
- two repositories of one project with the same PR number never share verdicts or rulings (collision fixture);
- mechanical, `no_verdict`, `awaiting_ruling` and judge-less closure verdicts are not judged;
- drills are counted as gestures but not as rollbacks or incidents;
- an operator ruling counts as an operator gesture;
- `issuer_class` on `red-rail-reviewer`, `service:x` and `agent:claude-code:s1`;
- the p90 of [1, 2, 2, 3] is 3;
- a population of one PR with two judged verdicts and one with only a mechanical verdict gives
  rounds [0, 2], median 1.0, and `first_pass_approval_ratio` over the first PR only;
- a population with no judged verdict at all gives rounds of zeros, median 0.0, and
  `first_pass_approval_ratio` `None`;
- an empty population gives `None`, never 0;
- the total over two projects equals recomputation over the merged samples, not the mean of their medians;
- a record at exactly `window_start` is counted.

Verify: the tests fail before the change (module absent) and pass after; `uv run pytest -q tests/test_autonomy.py` exit code 0.

### Task 4 — GitHub reads, complete or failed (`src/rail/reviewer/github.py`)

`_request` stays as it is for every existing call. Add `_request_page(path) -> tuple[Any, str | None]`.
It issues the same GET (same auth, timeout and size cap) and returns the decoded body and the
`rel="next"` URL parsed from the `Link` header (`None` when absent). A next URL outside `API` is
refused. Add `_paged_list(path) -> list[Any]`, which follows `next` until absent and raises
`GitHubError("incomplete answer")` when a page is not a JSON list. Add:
- `default_branch(repository) -> str`: `GET /repos/{r}` through `_request`. The body must be an object with a string `default_branch`, otherwise `GitHubError`.
- `merged_pulls(repository, *, base, since) -> list[MergedPull]`: `GET /repos/{r}/pulls?state=closed&base=<base>&sort=updated&direction=desc&per_page=100`, page by page. It stops after the first page whose last `updated_at` is before `since`, and keeps entries with a non-null `merged_at ≥ since`. `ready_at` is `ready_for_review_at(...)`, or `created_at` when the pull request was never drafted (no event).
- `ready_for_review_at(repository, number) -> datetime | None`: `GET /repos/{r}/issues/{n}/timeline?per_page=100` through `_paged_list`, the last `ready_for_review` event.
- `commits_since(repository, *, branch, since) -> list[tuple[datetime, str]]`: `GET /repos/{r}/commits?sha=<branch>&since=<iso>&per_page=100` through `_paged_list`, giving the committer date and the message's first line.

`MergedPull` lives in `rail.autonomy` (Task 3); `github.py` imports it. A token is never logged.

Tests (`tests/test_reviewer_github.py`, fake transport returning headers):
- two pages are followed through `Link`;
- a page that is an object fails;
- a 500 on page two fails the whole read and never returns page one alone;
- a `next` URL on another host is refused;
- `default_branch` accepts the repository object and refuses one without `default_branch`;
- pagination stops at `since`;
- a never-drafted pull request gets `ready_at = created_at`;
- a commit message starting with `Revert "` is returned on its first line;
- the existing `open_pulls` and `pull` behave as before.

Verify: the tests fail before the change (methods absent) and pass after; `uv run pytest -q tests/test_reviewer_github.py` exit code 0.

### Task 5 — a malformed ledger page fails the read (`src/rail/ledger/brain.py`)

In `_attestation_records` and `_row_by_key`, check every page:
- it is a dict;
- `items` is a list whose every element is a dict;
- `next_cursor` is absent, `None` (the last page, as `tests/fake_brain.py` writes it) or a non-empty string.

Anything else raises `LedgerError("brain returned a malformed page")`. `record_from_row` turns a
`KeyError`, `TypeError` or `AttributeError` raised by a row into
`LedgerError("brain returned a malformed attestation")`; an unknown kind still returns `None`.
Every caller already treats a `LedgerError` as a failed read (spec D5).

Tests (`tests/test_ledger_brain.py`):
- pages `{}`, `{"items": null}`, `{"items": {}}` and `{"items": [null]}` each raise `LedgerError`;
- `{"items": [{}]}` as the SECOND page, after a valid first page, raises `LedgerError` rather than returning the first page;
- a two-page answer whose last page has `"next_cursor": None` lists every record;
- the existing ledger suite passes unchanged.

Verify: the tests fail before the change (a `{}` page lists nothing, without error) and pass after; `uv run pytest -q tests/test_ledger_brain.py` exit code 0.

### Task 6 — `rail metrics publish` (`src/rail/commands/metrics.py`, `src/rail/publish.py`)

Turn `metrics` into `click.group("metrics", invoke_without_command=True)` with today's options
on the group. Its callback starts with `if ctx.invoked_subcommand is not None: return`, so
`rail metrics --repo X [--window] [--ci] [--json]` keeps its exact output, and a subcommand never
runs the historical command. Add the subcommand `publish [--config PATH] [--out PATH]`. The
defaults are `reviewer.config.DEFAULT_CONFIG` and `$XDG_STATE_HOME/red-rail/metrics.json`
(`~/.local/state/red-rail/metrics.json` when unset).

`publish.collect(config, *, now, clock, open_ledger, github) -> Answer` (all injectable).
`now` fixes the windows; `clock()` is called once, after every computation, for both blocks'
`generated_at` (spec D2: the end of the computation). For each repository of `reviewer.yaml`,
in order:
1. Load its manifest.
2. Open the ledger read-only as the reviewer: `open_ledger(path, agent="red-rail-reviewer", token_file=DEFAULT_REVIEWER_TOKEN_FILE)`.
3. List the project's records.
4. Read GitHub through the reviewer App: `default_branch`, `merged_pulls` and `commits_since` from SINCE; reverts are the commits whose first line starts with `Revert "`.
5. Build `ProjectFacts` and compute `window_samples` for 7 and 30 days.
6. When the manifest declares tier `prod` and a deploy target, compute `delivery_window` for 7 and 30 days.

Error classes are kept apart. A READ error for one repository (`LedgerError`, `GitHubError`,
`FileNotFoundError`, or the manifest's own `ValidationError` while loading `rail.yaml`) puts it
in `coverage.missing`. It is then absent from `projects` and from the delivery block, and the
total is `None`. A CONTRACT error (the `ValidationError` raised while building Task 2's models
from computed values) fails the whole run, exit 1, and the previous file is kept: an unexpected
or invalid value is never published (spec D7).

Assembly:
- `autonomy.projects` lists the read repositories whose 30-day samples are `active`, sorted by name;
- `autonomy.total` is `{d7, d30}` from `merge` over every read repository (inactive ones included) when `missing` is empty, else `None`;
- `not_measured` is a fixed constant equal to the fixtures' list (no ticket or other brain
  identifier in its text: the exclusion clause);
- `windows_days` is `[7, 30]` and `since` is SINCE;
- `delivery.projects` covers the prod-tier repositories read, sorted by name.

If no repository is read, nothing is written and the command exits 1.

`publish.write(answer, out)`:
1. `encode` the answer. On `TooLarge`, keep the previous file and exit 1, naming the size.
2. Create the directory with mode 0700.
3. Write a temporary file in it, `fsync`, then `os.replace`, mode 0644.

The command prints one line: the path, the size, and read/expected.

Tests (`tests/test_cli_metrics_publish.py`, fakes for ledger and GitHub, frozen `now`, a `clock` that advances by one second per call):
- two repositories give a file valid against Task 2's models;
- `generated_at` is the clock's value after the computation, not `now`;
- one failing ledger gives `coverage.missing == [name]`, `total` null, and the other still published;
- a GitHub 403 on one repository is a read error;
- a read repository with no counted activity is absent from `projects` but counted in the total;
- a repository with `ledger: file` reads its records from its checkout's `docs/receipts/`;
- an invalid computed value (a ratio of 1.5 injected through a fake) exits 1 and keeps the previous file;
- every read failing writes nothing and exits 1;
- an oversized answer keeps the previous file;
- the file mode is 0644 and the directory 0700;
- `rail metrics publish` and `rail metrics serve --help` run from a directory without `rail.yaml`;
- `rail metrics --repo` text, `--json` and error outputs are byte-identical to main's, with a frozen clock;
- no write reaches the fake ledger or GitHub (the fakes raise on any write).

Verify: the tests fail before the change (no `publish` subcommand) and pass after; `uv run pytest -q tests/test_cli_metrics_publish.py tests/test_metrics.py` exit code 0.

### Task 7 — `rail metrics serve` (`src/rail/serve.py`, `src/rail/commands/metrics.py`)

Add the subcommand `serve [--file PATH] [--port 9230]`. The host is fixed to `127.0.0.1`, with
no option to change it (spec D8: the host is a tailnet node). It is a
`http.server.ThreadingHTTPServer` whose handler answers:
- `GET /rail-metrics`:
  - read the file with a cap of `MAX_BYTES + 1` bytes;
  - absent file: `200 {}`;
  - over the cap, or failing `Answer.model_validate_json` (strict): `503 {}`;
  - otherwise `200` with `encode(validated)`, the re-serialization, never the file's bytes;
  - headers `Content-Type: application/json` and `Cache-Control: no-store`;
- any other path: `404 {}`;
- any other method on `/rail-metrics`, HEAD included: `405 {}`, through a `send_error`-free handler covering every method name (an unknown method like `BREW` too);
- a query string is ignored and never parsed.

No authentication, no directory listing, and the default access log line is turned off.

Tests (`tests/test_serve.py`, a real server on an ephemeral port in a thread):
- each answer above;
- HEAD, POST and `BREW` on `/rail-metrics` answer 405;
- a file whose integer is the string `"3"` answers 503;
- an extra unknown field answers 503;
- the served bytes equal `encode` of the validated model;
- the file replaced between two requests gives two valid answers;
- the bound address is `127.0.0.1` (`server.server_address[0]`).

Verify: the tests fail before the change (no `serve` subcommand) and pass after; `uv run pytest -q tests/test_serve.py` exit code 0.

### Task 8 — user units and the runbook (`contrib/systemd/user/`, `docs/metrics-publication.md`)

Add these unit files:
- `rail-metrics-publish.service`: `Type=oneshot`, `ExecStart=%h/.local/bin/rail metrics publish`;
- `rail-metrics-publish.timer`: `OnCalendar=hourly`, `Persistent=true`;
- `rail-metrics-serve.service`: `ExecStart=%h/.local/bin/rail metrics serve`, `Restart=on-failure`, `NoNewPrivileges=yes`.

None of them carries an address other than 127.0.0.1. Add a short document covering:
- the install commands (`systemctl --user link/enable --now`);
- the freshness rule (`generated_at` older than 2 h is stale, spec D9);
- how red-monitor's agent probes `127.0.0.1:9230`;
- the first real run of success criterion 1: run `rail metrics publish` once on the host, then check that both blocks are present, that `coverage` reads what is expected, and that the compact size of the served answer is at most 12 KiB. The measured size is recorded in the pull request that installs the units.

Tests (`tests/test_contrib_units.py`):
- each unit parses with `configparser` (strict=False);
- the serve unit has `Restart=on-failure`;
- the timer is hourly;
- no unit names an address outside 127.0.0.1 (`test_no_machine_address` covers the tree as well).

Verify: the tests fail before the change (files absent) and pass after; `make ci` exit code 0.

## Review focus

- A brain page that ends early without saying so: Task 5 accepts an absent or `None` cursor as
  the last page only. A server-side truncation that drops the cursor cannot be told apart.
  Accepted: brain's contract defines an absent cursor as the end.
- GitHub secondary rate limit (403 with `retry-after`) during publish: the repository is read
  as missing, never retried (Task 6 test).
- Clock skew at a window edge: windows compare `recorded_at` as brain wrote it,
  timezone-aware; a record at exactly `window_start` is included (Task 3 test).
- A `ledger: file` repository among brain ones (Task 6 test).
- The served file replaced during a read: `os.replace` is atomic, and serve reads the whole file
  under the cap before validating (Task 7 test).
