# The `private-timers` target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. In this lot each task is built by an `ha` worker (codex) and reviewed by a Claude subagent before the next one starts.

**Goal:** `rail deploy` delivers a project made of systemd timers and oneshot services, from a payload directory copied out of the released image by digest, verified without HTTP, observed through red-monitor's unit rows.

**Architecture:** a new `DeployTarget.PRIVATE_TIMERS` with its own manifest keys (`payload`, `units`, `version_command`), a new module `src/rail/deploy/private_timers.py` that reuses the unit parser of `private_systemd.py` and the shared mechanics of `remote.py`, a verification that runs the release's version command over ssh instead of GET `/version`, and an `observe.visible` branch that reads timers and the services they trigger from red-monitor. The flows (`forward`, `rollback`, `drill`) do not change.

**Tech Stack:** Python 3.12, Pydantic 2, pytest, ruff; bash for the remote script.

**Spec:** `docs/specs/2026-10-06-private-timers-target.md` (approved 2026-10-06). Executors read it first; the decision numbers below refer to it.

## Global Constraints

- `private-systemd`, `private-compose` and `vps-traefik` behave exactly as before: their test files pass unchanged (`tests/test_deploy_private_systemd.py`, `tests/test_deploy_private_compose.py`, `tests/test_deploy_vps_traefik.py`).
- No address and no infrastructure name in this public repository: documentation addresses only (192.0.2.x, 2001:db8::), anything else built at run time (`tests/test_no_machine_address.py`).
- The rail never edits sudoers and never runs as root; the privileged commands are exactly `sudo -n /usr/bin/systemctl daemon-reload` and `sudo -n /usr/bin/systemctl restart <timer>` per declared timer.
- A refusal happens before the first ssh whenever it can be decided from the released files.
- Everything pushed is English. No `rail bind` in this repository. Never `git stash`. Do not run `git commit` (the session commits).
- Commands: `unset VIRTUAL_ENV; uv run pytest -q <file>`, then `unset VIRTUAL_ENV; uv run pytest -q`, then `unset VIRTUAL_ENV; uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`.

## Review Focus

1. A declared unit already running (`activating` or `active`) at deploy time: the script stops before writing anything, naming the unit (Task 3 test `test_a_running_unit_stops_the_script_before_any_change`).
2. Redeploying the same version: the existing `app/` directory is replaced atomically by rename, never written into (Task 3 test `test_a_redeploy_replaces_the_payload_by_rename`).
3. A version command whose output is not JSON, or JSON that is not an object, or misses a field: a `DeployError` naming the field, never a traceback (Task 3 test `test_an_unreadable_identity_fails_the_verification`).
4. A timer whose service is absent from `deploy.units`, or a timer carrying `Unit=`: refused before the first ssh (Task 2 tests).
5. red-monitor rows for a timer service with a success that predates the newest deployment: the gate stays red and names the service and its next elapse (Task 4 test `test_a_success_before_the_deployment_does_not_count`).

---

### Task 1: The manifest learns `private-timers`

**Files:**
- Modify: `src/rail/model.py` (`DeployTarget`, `PRIVATE_TARGETS`, `DeployConfig`)
- Test: `tests/test_model.py`

**Interfaces:**
- Produces: `DeployTarget.PRIVATE_TIMERS = "private-timers"`; `PRIVATE_TARGETS` includes it; `DeployConfig.healthcheck: str | None = Field(default=None, pattern=r"^https?://")`; `DeployConfig.payload: str | None`; `DeployConfig.units: tuple[str, ...] | None`; `DeployConfig.version_command: tuple[str, ...] | None`; module constant `TIMER_UNIT_NAME_PATTERN = r"[a-z0-9]+(?:-[a-z0-9]+)*\.(?:service|timer)"`.

Rules (spec decision 3):
- `private-timers` requires `payload`, `units` and `version_command`, and refuses `healthcheck`, `unit` and `binary`. Every other target requires `healthcheck` (as today) and refuses `payload`, `units`, `version_command`. Messages: `deploy.<key> is required by target private-timers`, `deploy.<key> applies to target private-timers only`, `deploy.<key> does not apply to target private-timers`, `deploy.healthcheck is required by target <target>`.
- `payload`: absolute path of safe characters, no `.`/`..` segment (reuse `_BINARY_PATH` and `_has_dot_segment`).
- `units`: 1 to 32 entries, each a relative path inside the repository whose file name matches `TIMER_UNIT_NAME_PATTERN`, no dot segment, file names unique; every `X.timer` has `X.service` in the list; no file name equals `release.env` or `app`.
- `version_command`: 1 to 16 words, each matching `^[A-Za-z0-9._/=-]+$`; the first word is a relative path with no dot segment (it runs from `current/app/`).
- The site validator keeps its rules, with `healthcheck` now optional: the token checks only apply when a healthcheck is declared; `deploy.site` remains allowed on every private target.

- [ ] **Step 1: Write the failing tests** in `tests/test_model.py`:

```python
TIMERS = {
    "target": "private-timers",
    "site": "private-1",
    "payload": "/opt/red-backup",
    "units": [
        "deploy/systemd/red-backup.service",
        "deploy/systemd/red-backup.timer",
        "deploy/systemd/red-backup-alert.service",
    ],
    "version_command": ["bin/python3", "-m", "backup", "version", "--json"],
}


def test_a_private_timers_manifest_loads() -> None:
    cfg = DeployConfig.model_validate(TIMERS)
    assert cfg.target is DeployTarget.PRIVATE_TIMERS and cfg.healthcheck is None
    assert cfg.units[1] == "deploy/systemd/red-backup.timer"
    assert DeployTarget.PRIVATE_TIMERS in PRIVATE_TARGETS


@pytest.mark.parametrize("key", ["payload", "units", "version_command"])
def test_private_timers_requires_its_keys(key: str) -> None:
    with pytest.raises(ValidationError, match=f"deploy.{key} is required by target private-timers"):
        DeployConfig.model_validate({k: v for k, v in TIMERS.items() if k != key})


@pytest.mark.parametrize(
    ("key", "value"),
    [("healthcheck", "http://${BIND_ADDRESS}:9/health"), ("unit", "deploy/a.service"), ("binary", "/usr/bin/a")],
)
def test_private_timers_refuses_the_keys_of_other_targets(key: str, value: str) -> None:
    with pytest.raises(ValidationError, match=f"deploy.{key} does not apply to target private-timers"):
        DeployConfig.model_validate({**TIMERS, key: value})


@pytest.mark.parametrize("key", ["payload", "units", "version_command"])
def test_other_targets_refuse_the_timer_keys(key: str) -> None:
    other = {"target": "private-compose", "site": "private-1", "healthcheck": "http://${BIND_ADDRESS}:9/health"}
    with pytest.raises(ValidationError, match=f"deploy.{key} applies to target private-timers only"):
        DeployConfig.model_validate({**other, key: TIMERS[key]})


def test_other_targets_still_require_a_healthcheck() -> None:
    with pytest.raises(ValidationError, match="deploy.healthcheck is required by target private-compose"):
        DeployConfig.model_validate({"target": "private-compose", "site": "private-1"})


@pytest.mark.parametrize(
    "units",
    [
        ["deploy/systemd/red-backup.timer"],  # its service is not declared
        ["deploy/../x.service"],
        ["deploy/systemd/Red.service"],
        ["deploy/systemd/a.socket"],
        ["deploy/a.service", "other/a.service"],  # duplicate file name
        [],
    ],
)
def test_bad_unit_lists_are_refused(units: list[str]) -> None:
    with pytest.raises(ValidationError, match="deploy.units"):
        DeployConfig.model_validate({**TIMERS, "units": units})


@pytest.mark.parametrize(
    "command", [[], ["../bin/python3"], ["/abs/python3"], ["bin/python3", "a;b"], ["bin/python3", "$(x)"]]
)
def test_bad_version_commands_are_refused(command: list[str]) -> None:
    with pytest.raises(ValidationError, match="deploy.version_command"):
        DeployConfig.model_validate({**TIMERS, "version_command": command})


@pytest.mark.parametrize("payload", ["opt/red-backup", "/opt/../etc", "/opt/red backup"])
def test_bad_payloads_are_refused(payload: str) -> None:
    with pytest.raises(ValidationError, match="deploy.payload"):
        DeployConfig.model_validate({**TIMERS, "payload": payload})
```

- [ ] **Step 2: Run them, observe them fail**

Run: `unset VIRTUAL_ENV; uv run pytest -q tests/test_model.py -k "timers or timer_keys or healthcheck or unit_lists or version_commands or payloads"`
Expected: FAIL (`private-timers` is not a valid `DeployTarget`).

- [ ] **Step 3: Implement** in `src/rail/model.py`: the enum member, `PRIVATE_TARGETS`, the optional `healthcheck`, the three fields (`units` and `version_command` typed `tuple[str, ...] | None`), and one `@model_validator(mode="after")` named `_a_timers_target_names_its_payload_its_units_and_its_version` applying the rules above. Adapt `_a_site_and_its_token_go_together` (healthcheck may be `None`; the "applies to a private target only" message lists the three private targets) and `_a_systemd_target_names_its_unit_and_its_binary` (its "applies to target private-systemd only" message stays for `vps-traefik`/`private-compose`; for `private-timers` the message is `does not apply to target private-timers`). Any code reading `cfg.deploy.healthcheck` as a `str` keeps working for the three HTTP targets (they still require it).

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `unset VIRTUAL_ENV; uv run pytest -q tests/test_model.py` then `unset VIRTUAL_ENV; uv run pytest -q`
Expected: PASS, and the three existing target test files unchanged and green.

---

### Task 2: The unit rules of `private-timers`

**Files:**
- Create: `src/rail/deploy/private_timers.py` (rules only in this task)
- Test: `tests/test_deploy_private_timers.py`

**Interfaces:**
- Consumes: `parse_unit`, `_effective`, `_program`, `_prefix`, `_words`, `_memory_ok`, `_invisible_character_refusal`, `_COMMAND_KEYS`, `_PRIVILEGED`, `_UNSAFE_IN_COMMAND`, `_USERNAME`, `_DURATION` from `rail.deploy.private_systemd` (import them; do not copy them).
- Produces: `service_refusals(text: str, *, unit: str, current: str) -> list[str]`; `timer_refusals(text: str, *, unit: str, declared: frozenset[str]) -> list[str]`; `TIMER_TRIGGERS = ("OnCalendar", "OnBootSec", "OnUnitActiveSec", "OnUnitInactiveSec", "OnActiveSec", "OnStartupSec")`.

Rules (spec decision 7), each refusal a sentence starting with the unit's name:
- service: an invisible character → that refusal alone; no `[Service]` → that alone; `Type=` must be `oneshot`; `User=` set, a plain name, not `root`/`0`; `MemoryMax=` set and `_memory_ok`; at least one of `TimeoutStartSec=`/`TimeoutSec=`, each a positive duration (`_DURATION`); at least one `ExecStart=`; every effective value of every `_COMMAND_KEYS` key: no unsafe character or `;` word, no `+`/`!` prefix, and its program (`_program`) starts with `f"{current}/app/"`; `EnvironmentFile=` includes `f"{current}/release.env"` without `-`; no `PermissionsStartOnly=`.
- timer: invisible character → alone; no `[Timer]` section → alone; at least one key of `TIMER_TRIGGERS` with a non-empty value; no `Unit=` key; `unit[:-len(".timer")] + ".service"` in `declared`.

- [ ] **Step 1: Write the failing tests** in `tests/test_deploy_private_timers.py`:

```python
"""Target `private-timers` (spec 2026-10-06-private-timers-target). Documentation addresses only."""

import pytest

from rail.deploy.private_timers import service_refusals, timer_refusals

CURRENT = "/opt/red-backup/current"

SERVICE = """\
[Unit]
Description=ReD Backup - daily run

[Service]
Type=oneshot
User=red-backup
SupplementaryGroups=docker
EnvironmentFile=/opt/red-backup/current/release.env
ExecStart=/opt/red-backup/current/app/bin/python3 -m backup --config /etc/red-backup/backup.yaml run
ExecStopPost=/opt/red-backup/current/app/bin/python3 -m backup cleanup
LoadCredential=discord-webhook:/etc/red-backup/discord-webhook
MemoryMax=1G
TimeoutStartSec=2400
NoNewPrivileges=yes
ProtectSystem=strict
"""

TIMER = """\
[Unit]
Description=ReD Backup - daily run at 03:02

[Timer]
OnCalendar=*-*-* 03:02:00
Persistent=true

[Install]
WantedBy=timers.target
"""


def _service(text: str = SERVICE) -> list[str]:
    return service_refusals(text, unit="red-backup.service", current=CURRENT)


def test_the_hardened_oneshot_service_is_accepted() -> None:
    assert _service() == []


@pytest.mark.parametrize(
    ("old", "new", "rule"),
    [
        ("Type=oneshot", "Type=simple", "Type=oneshot"),
        ("User=red-backup", "User=root", "User="),
        ("MemoryMax=1G\n", "", "MemoryMax="),
        ("TimeoutStartSec=2400\n", "", "TimeoutStartSec="),
        ("TimeoutStartSec=2400", "TimeoutStartSec=infinity", "TimeoutStartSec="),
        ("ExecStopPost=/opt/red-backup/current/app/bin/python3", "ExecStopPost=/usr/bin/docker", "/opt/red-backup/current/app/"),
        ("ExecStart=/opt/red-backup/current/app/bin/python3", "ExecStart=+/opt/red-backup/current/app/bin/python3", "full privileges"),
        ("EnvironmentFile=/opt/red-backup/current/release.env", "EnvironmentFile=-/opt/red-backup/current/release.env", "EnvironmentFile="),
        ("NoNewPrivileges=yes", "PermissionsStartOnly=yes", "PermissionsStartOnly="),
    ],
)
def test_each_service_rule_refuses_and_says_which(old: str, new: str, rule: str) -> None:
    refusals = _service(SERVICE.replace(old, new))
    assert any(rule in r and r.startswith("red-backup.service") for r in refusals), refusals


def test_an_execstartpre_outside_the_release_is_refused() -> None:
    text = SERVICE.replace(
        "ExecStart=", "ExecStartPre=/usr/bin/docker image inspect x\nExecStart=", 1
    )
    assert any("ExecStartPre" in r for r in _service(text))


def test_the_hardened_timer_is_accepted() -> None:
    assert timer_refusals(TIMER, unit="red-backup.timer", declared=frozenset({"red-backup.service"})) == []


def test_a_timer_whose_service_is_not_declared_is_refused() -> None:
    refusals = timer_refusals(TIMER, unit="red-backup.timer", declared=frozenset())
    assert refusals and "red-backup.service" in refusals[0]


@pytest.mark.parametrize(
    ("old", "new", "rule"),
    [
        ("OnCalendar=*-*-* 03:02:00\n", "", "trigger"),
        ("Persistent=true", "Persistent=true\nUnit=other.service", "Unit="),
        ("[Timer]", "[Service]", "[Timer]"),
    ],
)
def test_each_timer_rule_refuses_and_says_which(old: str, new: str, rule: str) -> None:
    refusals = timer_refusals(
        TIMER.replace(old, new), unit="red-backup.timer", declared=frozenset({"red-backup.service"})
    )
    assert any(rule in r for r in refusals), refusals


def test_an_invisible_character_is_refused_alone() -> None:
    assert len(_service(SERVICE.replace("User=red-backup", "User=red​-backup"))) == 1
```

- [ ] **Step 2: Run them, observe them fail**

Run: `unset VIRTUAL_ENV; uv run pytest -q tests/test_deploy_private_timers.py`
Expected: FAIL (`No module named rail.deploy.private_timers`).

- [ ] **Step 3: Implement** `service_refusals` and `timer_refusals` in `src/rail/deploy/private_timers.py` with a module docstring citing the spec, following the allow-list stance of `private_systemd.unit_refusals` (read its docstring and comments first).

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `unset VIRTUAL_ENV; uv run pytest -q tests/test_deploy_private_timers.py` then `unset VIRTUAL_ENV; uv run pytest -q`
Expected: PASS.

---

### Task 3: The target: remote script, verification without HTTP, flows

**Files:**
- Modify: `src/rail/deploy/private_timers.py` (add the target), `src/rail/deploy/remote.py` (`RemoteTarget.__init__` tolerates no healthcheck), `src/rail/deploy/flow.py` (`implementations`)
- Modify: `CLAUDE.md` (§ Architecture, the `src/rail/deploy/` bullet: one sentence for `private_timers.py`), `skills/rail-deploy/SKILL.md` (name the third private target in its description and body, one sentence)
- Test: `tests/test_deploy_private_timers.py`

**Interfaces:**
- Consumes: Task 1's `DeployConfig` fields; Task 2's `service_refusals`, `timer_refusals`; `RemoteTarget`, `Parameters`, `heredoc`, `lock_preamble`, `ssh_argv` from `rail.deploy.remote`; `release_env`, `loaded_unit_checks` from `rail.deploy.private_systemd`; `Artefact`, `LiveVersion`, `DeployError`, `Step` from `rail.deploy`.
- Produces: `class PrivateTimers(RemoteTarget)` with `ssh_host_must_be_declared = True`, properties `current: str`, `services: tuple[str, ...]`, `timers: tuple[str, ...]`, methods `script_for(artefact) -> str`, `describe(artefact) -> str`, `steps(artefact) -> list[Step]`, `verify(artefact) -> LiveVersion`, `version_script() -> str`; module function `remote_script(project: str, artefact: Artefact, units: dict[str, str], payload: str, timers: tuple[str, ...], params: Parameters) -> str`.

`RemoteTarget.__init__`: when `cfg.deploy.healthcheck is None`, set `self.healthcheck = None` and `self.domain = self.binding.site if self.binding else self.params.ssh_host`; nothing else in it changes. `origin`, `_wait_healthy` and `live_version` are never called by `PrivateTimers` (it overrides `steps` and `verify`).

The remote script (spec decision 5), in order, with `root=<stack_root>/<project>`, `release=$root/releases/<version>`:
1. `lock_preamble(root, release)`.
2. For every declared unit: `state=$(systemctl show --property=ActiveState --value <unit>)`; when `$state` is `active` or `activating`: `echo "<unit> is $state: a run is in progress, deploy again once it has finished" >&2; exit 1`.
3. `heredoc(<unit>, text)` for every unit; `heredoc("release.env", release_env(artefact))`; `chmod 0644` on them.
4. `docker pull --quiet <image>`; `container=$(docker create <image> /bin/true)`; the same `trap … docker rm --force` as `private_systemd`; `rm -rf "$release/.app.new"`; `docker cp "$container:<payload>/." "$release/.app.new"`; then `rm -rf "$release/.app.old"`; `if [ -e "$release/app" ]; then mv "$release/app" "$release/.app.old"; fi`; `mv "$release/.app.new" "$release/app"`; `rm -rf "$release/.app.old"`.
5. `ln -sfn "$release" "$root/current"`.
6. `sudo -n /usr/bin/systemctl daemon-reload`; `loaded_unit_checks(<unit>)` for every declared unit.
7. `sudo -n /usr/bin/systemctl restart <timer>` for every declared timer.
8. For every declared timer: `systemctl is-active <timer>` and `next=$(systemctl show --property=NextElapseUSecRealtime --value <timer>)`; `[ -n "$next" ] && [ "$next" != "n/a" ] || { echo "<timer> has no next elapse" >&2; exit 1; }`.

`script_for`: reads every unit at `artefact.sha` with `self.file_at`; applies `service_refusals`/`timer_refusals` (with `declared` = the declared service names); raises `DeployError("the released units are refused before the first ssh — " + "; ".join(refusals))` when any; returns `remote_script(...)`.

`steps`: two steps — `Step(self.describe(artefact), ssh_argv(self.params), script)` and `Step(f"ssh {ssh_host}: {' '.join(version_command)} == {version} / {sha[:12]} / {digest}", ssh_argv(self.params), self.version_script())`.

`version_script`: `set -euo pipefail`, `cd <root>/current/app`, then `exec ./<word0> <shlex.quote(word) ...>` (the model already restricted the words).

`verify`: runs the second step through `self._run` (same timeout handling as `RemoteTarget.apply`), parses stdout as JSON; a non-zero exit, non-JSON, non-object, or a missing `project`/`version`/`git_sha`/`image_digest` raises `DeployError` naming what is wrong (redacted with `self.redact`); then compares `version`, `git_sha`, `image_digest` with the artefact exactly as `RemoteTarget.verify` does and returns the `LiveVersion`.

`describe`: `f"ssh {ssh_host}: release {version} under {root}, {len(units)} unit(s), restart {', '.join(timers)}"`.

`flow.implementations()`: add `DeployTarget.PRIVATE_TIMERS: PrivateTimers`.

- [ ] **Step 1: Write the failing tests**, appended to `tests/test_deploy_private_timers.py`. Build the repository fixture the way `tests/test_deploy_private_systemd.py` does (`_systemd_repo`, `RecordingHost`, `_artefact`, `_target`: read them and mirror them), with a manifest of target `private-timers`, site `private-1` in a sites file under `tmp_path` (address `192.0.2.10`), `deploy.ssh_host` declared under `gates:`, and the units `red-backup.service`, `red-backup.timer`, `red-backup-alert.service` committed under `deploy/systemd/` (the alert service is the `SERVICE` text with another description). Tests:
  - `test_the_remote_script_runs_the_spec_sequence_in_order`: the indexes of `lock`, the running check, the heredocs, `docker pull`, `docker cp`, `ln -sfn`, `daemon-reload`, the `FragmentPath` check, `restart red-backup.timer`, `NextElapseUSecRealtime` are strictly increasing.
  - `test_a_running_unit_stops_the_script_before_any_change`: the `ActiveState` check of every declared unit comes before the first heredoc, and exits non-zero on `active` and `activating` (assert on the script text).
  - `test_the_only_privileged_commands_are_daemon_reload_and_one_restart_per_timer`: every line containing `sudo` is one of those, and `restart red-backup.service` / `restart red-backup-alert.service` never appear.
  - `test_a_redeploy_replaces_the_payload_by_rename`: the script never `docker cp`s into `$release/app` directly and moves `.app.new` to `app`.
  - `test_the_container_is_removed_on_every_exit_path`: the `trap … docker rm --force` line precedes `docker cp`.
  - `test_a_refused_unit_never_reaches_the_machine`: a committed service with `User=root` raises `DeployError` from `steps()` and the recording runner saw no `ssh`.
  - `test_the_deployment_is_verified_by_the_release_version_command`: a runner returning exit 0 for the script and `{"project": "red-backup", "version": "<v>", "git_sha": "<sha>", "image_digest": "<digest>"}` for the version script makes `apply` return a `LiveVersion` equal to the artefact; the version script contains `cd /opt/red-backup/current/app` and `exec ./bin/python3 -m backup version --json`.
  - `test_an_unreadable_identity_fails_the_verification` (parametrized: `not json`, `[]`, an object without `git_sha`, a mismatching `image_digest`): `DeployError`, the message names the problem, never contains `192.0.2.10`.
  - `test_the_flows_build_the_timers_target_from_the_manifest`: `make_target(repo, cfg)` is a `PrivateTimers`; `implementations()` covers every `DeployTarget` member.
  - `test_plan_names_the_site_and_the_version_command` through `CliRunner` and `rail deploy --plan`, mirroring the `private-systemd` test of the same shape.

- [ ] **Step 2: Run them, observe them fail**

Run: `unset VIRTUAL_ENV; uv run pytest -q tests/test_deploy_private_timers.py`
Expected: FAIL (`PrivateTimers` not defined).

- [ ] **Step 3: Implement** the target, the `RemoteTarget.__init__` change, the registry entry and the two documentation sentences.

- [ ] **Step 4: Run the tests, then the whole suite and lint**

Run: `unset VIRTUAL_ENV; uv run pytest -q tests/test_deploy_private_timers.py tests/test_deploy_private_systemd.py tests/test_deploy_private_compose.py tests/test_deploy_vps_traefik.py tests/test_cli_deploy.py` then the full suite and ruff.
Expected: PASS, the three existing target files unchanged.

---

### Task 4: `observe.visible` for timers

**Files:**
- Modify: `src/rail/monitor.py` (`Unit` gains fields), `src/rail/gates/evidence.py` (`visible` branch and `_timers_visible`)
- Test: `tests/test_monitor.py`, `tests/test_gates_evidence.py`

**Interfaces:**
- Consumes: Task 1's `DeployConfig.units`; red-monitor's `systemd` rows: `name`, `load_state`, `active_state`, `sub_state`, `result`, `exec_main_status`, `exec_main_exited_at`, `next_elapse_at` (ISO 8601, optional).
- Produces: `monitor.Unit` with added fields `load_state: str = ""`, `result: str = ""`, `exec_main_status: int | None = None`, `exec_main_exited_at: datetime | None = None`, `next_elapse_at: datetime | None = None` (defaults keep existing constructions valid); `evidence._timers_visible(view, agent, units: tuple[str, ...], deployed_at: datetime) -> GateResult`.

Rule (spec decision 9): for each declared `X.timer`: the row exists, `load_state` is `loaded`, state `active/waiting`, `next_elapse_at` set; its `X.service` row exists, `result == "success"`, `exec_main_status == 0`, and `exec_main_exited_at` is strictly after `deployed_at` (the newest `deployed` attestation's `recorded_at`). The first failing unit is named: `red-monitor lists no unit X on agent A`, `timer X on agent A is S`, `X has not run successfully since the deployment of <deployed_at ISO> (next run <next_elapse_at ISO or unknown>)`. Services not triggered by a declared timer are not required. Passing detail: `N timer(s) waiting on A, each triggered service succeeded after the deployment`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_monitor.py`, a payload with a `systemd` row carrying all the fields above parses into a `Unit` with them (a missing or unparsable timestamp gives `None`; a non-integer status gives `None`). In `tests/test_gates_evidence.py`, mirroring the existing `private-systemd` observe tests in that file (find them by `PRIVATE_SYSTEMD` / `_unit_visible` and copy their fixtures): a `private-timers` manifest with one timer and its service, a `deployed` attestation, and a fake red-monitor `/api/latest`:
  - `test_timers_visible_when_every_triggered_service_succeeded_after_the_deployment`;
  - `test_a_success_before_the_deployment_does_not_count` (the detail names the service and the next elapse);
  - `test_a_timer_not_waiting_fails_naming_it`;
  - `test_a_failed_triggered_service_fails_naming_it` (`result: "exit-code"`, status 1);
  - `test_a_service_without_a_timer_is_not_required` (the alert service has no row: still green).

- [ ] **Step 2: Run them, observe them fail**

Run: `unset VIRTUAL_ENV; uv run pytest -q tests/test_monitor.py tests/test_gates_evidence.py -k "timer or systemd_row"`
Expected: FAIL.

- [ ] **Step 3: Implement** the `Unit` fields and their parsing in `read_agent` (reuse `_instant`), the `private-timers` branch in `visible` next to the `private-systemd` one, and `_timers_visible`.

- [ ] **Step 4: Run the tests, then the whole suite and lint**

Run: `unset VIRTUAL_ENV; uv run pytest -q tests/test_monitor.py tests/test_gates_evidence.py` then the full suite and ruff.
Expected: PASS; the `private-systemd` and compose observe tests unchanged.
