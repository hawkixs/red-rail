# `private-systemd` target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fourth way to deliver with `rail deploy`, the target `private-systemd`. It delivers a binary that systemd runs: the red-monitor agent first. It is attested, verified and drilled like the other targets.

**Architecture:** The mechanics every ssh target shares move out of `deploy/compose.py` into
`deploy/remote.py`: the lock, the remote script, the verification, the site. The new target
supplies its own remote script. That script copies the binary out of the released image by
digest, writes the unit and `release.env`, points `current` at the release, and restarts
through two fixed sudo commands. The unit is refused before the first ssh when it would run as
root, run unbounded, or run outside the release. `observe.visible` reads the unit's state from
red-monitor on this target.

**Tech Stack:** Python 3.12, Pydantic 2, Click, PyYAML, pytest, ruff; bash on the target.

**Spec:** `docs/specs/2026-09-24-private-systemd-target.md` (read it first: every decision this
plan cites by number is there).

Work in the worktree `.claude/worktrees/private-systemd-target`, branch
`feat/hawixs/private-systemd-target`, from its root. `uv sync --all-extras` once before Task 1.

## Global Constraints

- Python 3.12+, ruff `line-length = 100`, lint `select = ["E", "F", "I", "UP", "B"]`: every task runs `uv run ruff format src/ tests/` and then ends with `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` clean.
- English for code, comments, test names and commits. A `git commit  # <subject>` line in a step gives the subject. The message is written with `/git-commit`: conventional, with a leading emoji, ending with the two trailer lines of the session.
- No machine address in any tracked file: only RFC 5737 (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) and RFC 3849 (`2001:db8::/32`). `tests/test_no_machine_address.py` enforces it.
- No machine name, deploy account name or ssh alias in docs or code: fixtures use `private-1` and `private-1-deploy`.
- A gate never raises. A deployment refusal raises `DeployError` before the first ssh.
- `tests/test_deploy_vps_traefik.py` and `tests/test_deploy_private_compose.py` stay unchanged, except `test_an_unimplemented_target_is_still_refused_by_name` (Task 1). That is the proof the extraction in Tasks 2 and 3 is refactoring.
- The only privileged commands the script runs are exactly `sudo -n /usr/bin/systemctl daemon-reload` and `sudo -n /usr/bin/systemctl restart <unit>`. The script never writes outside `<stack_root>/<project>`.
- `release.env` holds exactly `VERSION`, `GIT_SHA`, `IMAGE_DIGEST`, `IMAGE_REFERENCE`, and never a secret.
- Tests never read the developer's host files: `tests/conftest.py` already points `RAIL_SITES_FILE` into `tmp_path`. A test that needs a site passes `sites=` or sets the variable itself.

## Review Focus

1. The unit sets `User=` twice, the last one `root`, or writes `User = root` with spaces: systemd runs it as root, so the target must refuse it (Task 4, `test_the_last_assignment_wins_as_it_does_for_systemd`).
2. A `+` prefix hidden behind a line continuation with a comment in between: systemd drops the comment and runs the command with full privileges, so the target must refuse it (Task 4, `test_a_privilege_prefix_behind_a_continuation_and_a_comment_is_seen`).
3. Redeploying the version that is running: writing into the executable systemd is running fails with `Text file busy`, so the binary must be swapped in by a rename (Task 5, `test_a_running_binary_is_replaced_by_rename_never_written_in_place`).
4. `docker cp` fails after `docker create`: the container must still be removed, and nothing restarted (Task 5, `test_the_container_is_removed_even_when_the_copy_fails`, run under bash with stubs).
5. red-monitor answers for an agent that reports no `systemd` rows: `observe.visible` must fail naming the unit, never raise (Task 6, the empty case of `test_visible_reads_the_unit_on_a_systemd_target`).

---

### Task 1: `private-systemd` becomes declarable, with its unit and its binary

**Files:**
- Modify: `src/rail/model.py` (`DeployTarget`, `DeployConfig`)
- Modify: `copier.yml` (`deploy_target`, `healthcheck`)
- Modify: `src/rail/scaffold.py` (`NewProject.answers`)
- Test: `tests/test_model.py`, `tests/test_scaffold.py`, `tests/test_deploy_private_compose.py` (one test replaced)

**Interfaces:**
- Produces: `DeployTarget.PRIVATE_SYSTEMD == "private-systemd"`, and `PC_SERVER_SYSTEMD` removed;
  `DeployConfig.unit: str | None`, `DeployConfig.binary: str | None`;
  `model.UNIT_NAME_PATTERN: str` (`[a-z0-9]+(?:-[a-z0-9]+)*\.service`);
  `model.PRIVATE_TARGETS: frozenset[DeployTarget]`.

- [ ] **Step 1: Write the failing model tests**

In `tests/test_model.py`, add `DeployTarget` to the `from rail.model import …` line. In
`test_a_site_is_refused_on_a_public_target`, change `match="private-compose only"` to
`match="a private target"`. Then append:

```python
# -- private-systemd (spec 2026-09-24-private-systemd-target) ----------------------------

SYSTEMD_DEPLOY = {
    "target": "private-systemd",
    "site": "private-1",
    "healthcheck": "http://${BIND_ADDRESS}:9100/health",
    "unit": "deploy/red-agent.service",
    "binary": "/usr/local/bin/red",
}


def test_a_systemd_target_names_its_unit_and_its_binary() -> None:
    cfg = RailConfig.model_validate(_prod(SYSTEMD_DEPLOY))
    assert cfg.deploy is not None and cfg.deploy.target is DeployTarget.PRIVATE_SYSTEMD
    assert cfg.deploy.unit == "deploy/red-agent.service"
    assert cfg.deploy.binary == "/usr/local/bin/red"


@pytest.mark.parametrize("missing", ["unit", "binary"])
def test_a_systemd_target_without_its_unit_or_binary_is_refused(missing: str) -> None:
    deploy = {key: value for key, value in SYSTEMD_DEPLOY.items() if key != missing}
    with pytest.raises(ValidationError, match=f"deploy.{missing} is required"):
        RailConfig.model_validate(_prod(deploy))


@pytest.mark.parametrize("field", ["unit", "binary"])
def test_a_compose_target_refuses_a_unit_or_a_binary(field: str) -> None:
    with pytest.raises(ValidationError, match=f"deploy.{field} applies"):
        RailConfig.model_validate(_prod({**SITE_DEPLOY, field: SYSTEMD_DEPLOY[field]}))


@pytest.mark.parametrize(
    "unit",
    [
        "/etc/systemd/system/red-agent.service",  # absolute: outside the repository
        "deploy/../red-agent.service",  # escapes through `..`
        "deploy/red-agent.timer",  # not a service
        "deploy/red_agent.service",  # not a plain unit name
        "deploy/red-agent@.service",  # a template
        "deploy/red agent.service",  # a space would reach the remote script
    ],
)
def test_the_unit_is_a_plain_service_file_inside_the_repository(unit: str) -> None:
    with pytest.raises(ValidationError, match="deploy.unit must be"):
        RailConfig.model_validate(_prod({**SYSTEMD_DEPLOY, "unit": unit}))


@pytest.mark.parametrize(
    "binary",
    [
        "usr/local/bin/red",  # relative
        "/usr/local/bin/../red",  # a `..` component
        "/usr/local/bin/",  # no file name
        "/usr/local/bin/r d",  # a space would reach the remote script
        "/opt/$(id)",  # a substitution would too
    ],
)
def test_the_binary_is_an_absolute_path_of_safe_characters(binary: str) -> None:
    with pytest.raises(ValidationError, match="deploy.binary must be"):
        RailConfig.model_validate(_prod({**SYSTEMD_DEPLOY, "binary": binary}))


def test_a_site_is_accepted_on_the_systemd_target_too() -> None:
    cfg = RailConfig.model_validate(_prod(SYSTEMD_DEPLOY))
    assert cfg.deploy is not None and cfg.deploy.site == "private-1"


def test_the_retired_target_name_no_longer_loads() -> None:
    with pytest.raises(ValidationError, match="pc-server-systemd"):
        RailConfig.model_validate(_prod({**SITE_DEPLOY, "target": "pc-server-systemd"}))
```

- [ ] **Step 2: Replace the one private-compose test that used the retired name**

In `tests/test_deploy_private_compose.py`, replace the whole function
`test_an_unimplemented_target_is_still_refused_by_name` with:

```python
def test_a_target_the_rail_does_not_know_is_refused_when_the_manifest_loads(
    tmp_path: Path,
) -> None:
    """`pc-server-systemd` was declarable and unimplemented. Its successor is implemented
    (spec 2026-09-24-private-systemd-target), so the old name no longer loads at all."""
    from pydantic import ValidationError

    repo = _private_repo(tmp_path, SAFE)
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text().replace("private-compose", "pc-server-systemd")
    )
    with pytest.raises(ValidationError, match="pc-server-systemd"):
        load_rail_config(repo)
```

- [ ] **Step 3: Write the failing scaffold test**

Append to `tests/test_scaffold.py`:

```python
def test_a_systemd_target_is_written_by_hand_not_scaffolded(
    template_dir: Path, tmp_path: Path
) -> None:
    """The unit and the binary are the project's own facts (spec
    2026-09-24-private-systemd-target). The scaffold has nothing to put there, so it refuses
    rather than render a manifest that does not load."""
    project = _project(
        template_dir,
        tmp_path / "red-monitor",
        slug="red-monitor",
        tier=Tier.PROD,
        deploy_target="private-systemd",
    )
    with pytest.raises(ScaffoldError, match="deploy.unit"):
        _ = project.answers
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_model.py tests/test_scaffold.py tests/test_deploy_private_compose.py`
Expected: FAIL. `private-systemd` is not a valid target, `unit`/`binary` are extra fields, and
the copier choices test still lists `pc-server-systemd`.

- [ ] **Step 5: Implement the model**

In `src/rail/model.py`, next to `SITE_PATTERN` and `ADDRESS_TOKEN`, add:

```python
# A unit file inside the repository: relative, no `.`/`..` segment, and a plain service name
# (no template, no timer). The file name is the unit's name for systemd and for the sudoers
# rule alike.
UNIT_NAME_PATTERN = r"[a-z0-9]+(?:-[a-z0-9]+)*\.service"
_UNIT_PATH = re.compile(rf"^(?:[A-Za-z0-9._-]+/)*{UNIT_NAME_PATTERN}$")
# The binary's path inside the released image: absolute and of safe characters, because it
# reaches the remote script.
_BINARY_PATH = re.compile(r"^(?:/[A-Za-z0-9._-]+)+$")


def _has_dot_segment(path: str) -> bool:
    return any(part in {".", ".."} for part in path.split("/"))
```

Replace `DeployTarget` and `DeployConfig` with:

```python
class DeployTarget(StrEnum):
    VPS_TRAEFIK = "vps-traefik"
    PRIVATE_COMPOSE = "private-compose"
    PRIVATE_SYSTEMD = "private-systemd"


PRIVATE_TARGETS = frozenset({DeployTarget.PRIVATE_COMPOSE, DeployTarget.PRIVATE_SYSTEMD})


class DeployConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: DeployTarget
    healthcheck: str = Field(pattern=r"^https?://")
    site: str | None = Field(default=None, pattern=SITE_PATTERN)
    unit: str | None = None  # private-systemd: the unit file, read at the released commit
    binary: str | None = None  # private-systemd: the binary's path inside the released image

    @model_validator(mode="after")
    def _a_site_and_its_token_go_together(self) -> DeployConfig:
        if self.site is not None and self.target not in PRIVATE_TARGETS:
            raise ValueError(
                "deploy.site applies to a private target only (private-compose, private-systemd)"
            )
        if self.site is None and ADDRESS_TOKEN in self.healthcheck:
            raise ValueError(
                f"deploy.healthcheck uses {ADDRESS_TOKEN} but no deploy.site says whose "
                "address it is"
            )
        if self.site is not None and not token_is_the_host(self.healthcheck):
            raise ValueError(
                f"behind deploy.site the healthcheck host is {ADDRESS_TOKEN}, filled from the "
                f"host's sites file (got {self.healthcheck})"
            )
        return self

    @model_validator(mode="after")
    def _a_systemd_target_names_its_unit_and_its_binary(self) -> DeployConfig:
        systemd = self.target is DeployTarget.PRIVATE_SYSTEMD
        for name, value in (("unit", self.unit), ("binary", self.binary)):
            if systemd and value is None:
                raise ValueError(f"deploy.{name} is required by target private-systemd")
            if not systemd and value is not None:
                raise ValueError(f"deploy.{name} applies to target private-systemd only")
        if self.unit is not None and (
            not _UNIT_PATH.fullmatch(self.unit) or _has_dot_segment(self.unit)
        ):
            raise ValueError(
                "deploy.unit must be a relative path inside the repository to a plain service "
                f"file ({UNIT_NAME_PATTERN}), got {self.unit!r}"
            )
        if self.binary is not None and (
            not _BINARY_PATH.fullmatch(self.binary) or _has_dot_segment(self.binary)
        ):
            raise ValueError(
                "deploy.binary must be an absolute path of safe characters inside the image, "
                f"got {self.binary!r}"
            )
        return self
```

- [ ] **Step 6: Implement the scaffold refusal and the copier choice**

In `src/rail/scaffold.py`, `NewProject.answers`, right after
`data["deploy_target"] = self.deploy_target`, insert:

```python
            if self.deploy_target == DeployTarget.PRIVATE_SYSTEMD:
                raise ScaffoldError(
                    "target private-systemd is written by hand: rail.yaml needs deploy.unit and "
                    "deploy.binary, which only the project knows "
                    "(spec 2026-09-24-private-systemd-target)"
                )
```

In `copier.yml`, set `deploy_target` to:

```yaml
deploy_target:
  type: str
  choices: [vps-traefik, private-compose, private-systemd]
  default: vps-traefik
  when: "{{ tier == 'prod' }}"
  help: Where it lands — the border VPS behind Traefik, or a machine with no public route
  validator: >-
    {% if deploy_target == 'private-systemd' %}
    private-systemd is written by hand: rail.yaml needs deploy.unit and deploy.binary
    {% endif %}
```

In the `healthcheck` default of `copier.yml`, replace
`{% if deploy_target == 'private-compose' %}` with
`{% if deploy_target in ['private-compose', 'private-systemd'] %}`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_model.py tests/test_scaffold.py tests/test_deploy_private_compose.py`
Expected: PASS. Then run `uv run pytest -q`: expected all pass, and the summary line is read.

- [ ] **Step 8: Commit**

```bash
git add src/rail/model.py src/rail/scaffold.py copier.yml tests/test_model.py tests/test_scaffold.py tests/test_deploy_private_compose.py
git commit  # ✨ feat(model): private-systemd replaces pc-server-systemd, with its unit and binary
```

---

### Task 2: What every ssh target shares moves to `deploy/remote.py`

A move, not a rewrite. `ComposeTarget` keeps only what is compose, and every name `compose.py`
published stays importable from it.

**Files:**
- Create: `src/rail/deploy/remote.py`
- Modify: `src/rail/deploy/compose.py` (whole file below)
- Test: `tests/test_deploy_vps_traefik.py`, `tests/test_deploy_private_compose.py`, `tests/test_cli_deploy.py`. All three stay unchanged: they are the proof.

**Interfaces:**
- Produces, in `rail.deploy.remote`:
  - `LOCKED`, `POLL_SECONDS`, `TAIL_LIMIT`, `Runner`, `Parameters`;
  - `env_lines(pairs) -> str`, `heredoc(name: str, text: str) -> str`,
    `ssh_argv(params) -> tuple[str, ...]`;
  - `class RemoteTarget` with the seams `script_for(artefact) -> str`,
    `describe(artefact) -> str` and the `origin` property;
  - the shared methods `file_at(sha: str, path: str, what: str) -> str`,
    `steps(artefact) -> list[Step]`, `apply(artefact) -> LiveVersion`,
    `verify(artefact) -> LiveVersion`, `live_version() -> LiveVersion | None`,
    `redact(text) -> str`.
- `rail.deploy.compose` still exports `LOCKED`, `POLL_SECONDS`, `Parameters`, `Runner`,
  `env_lines`, `ssh_argv`, `common_env`, `remote_script`, `ComposeTarget`, and
  `ComposeTarget.compose_at(sha)`.

- [ ] **Step 1: Run the proof suites before touching anything**

Run: `uv run pytest -q tests/test_deploy_vps_traefik.py tests/test_deploy_private_compose.py tests/test_cli_deploy.py`
Expected: PASS. Note the count: it must be identical after the move.

- [ ] **Step 2: Create `src/rail/deploy/remote.py`**

```python
"""What every target reached over ssh does identically, written once (spec
2026-09-24-private-systemd-target, decision 10).

One bash script runs on the machine under the target's lock: `LOCKED` when another deployment
holds it, killed with its ssh session at the timeout. The result is then verified from
outside: the healthcheck until it answers 200, then `/version` equal to the artefact. A shape
supplies its script (`script_for`, which may refuse before anything reaches the machine), the
title of that step (`describe`) and the origin its verification talks to (`origin`).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rail.deploy import Artefact, DeployError, LiveVersion, Locked, Step
from rail.http import Http, HttpError, http_get
from rail.model import RailConfig
from rail.policy import parameter

LOCKED = 75  # the remote script's exit code when the lock is taken (EX_TEMPFAIL)
POLL_SECONDS = 3.0
TAIL_LIMIT = 2000
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True, slots=True)
class Parameters:
    ssh_host: str
    stack_root: str
    healthcheck_timeout: int
    compose_path: str
    remote_timeout: int  # the whole remote phase; its ssh session is killed after it

    @classmethod
    def read(cls, repo: Path) -> Parameters:
        return cls(
            ssh_host=str(parameter(repo, "deploy.ssh_host")),
            stack_root=str(parameter(repo, "deploy.stack_root")),
            healthcheck_timeout=int(parameter(repo, "deploy.healthcheck_timeout_seconds")),
            compose_path=str(parameter(repo, "deploy.compose_path")),
            remote_timeout=int(parameter(repo, "deploy.remote_timeout_seconds")),
        )


def env_lines(pairs: tuple[tuple[str, str], ...]) -> str:
    return "".join(f"{key}={value}\n" for key, value in pairs)


def _tail(text: str) -> str:
    """The last `TAIL_LIMIT` characters of the remote's stderr/stdout. When the cut actually
    removes a prefix, the partial first token — text up to and including the first
    whitespace — goes with it: an address contains no whitespace, so it can never survive
    split in half at the start of what remains (review finding)."""
    stripped = text.strip()
    if len(stripped) <= TAIL_LIMIT:
        return stripped
    tail = stripped[-TAIL_LIMIT:]
    for index, char in enumerate(tail):
        if char.isspace():
            return tail[index + 1 :]
    return ""  # the whole visible window is one token: no boundary to cut at safely


def heredoc(name: str, text: str) -> str:
    """`cat > name` fed by a quoted here-document: nothing in `text` is expanded."""
    marker = f"__RAIL_{name.upper()}__"
    if marker in text:
        raise DeployError(f"{name} contains the heredoc marker {marker}")
    body = text if text.endswith("\n") else text + "\n"
    return f"cat > {name} <<'{marker}'\n{body}{marker}"


def ssh_argv(params: Parameters) -> tuple[str, ...]:
    return ("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", params.ssh_host, "bash", "-s")


class RemoteTarget:
    """The shared half of every target. A shape supplies `script_for`, `describe` and
    `origin`, and may say what its records must hide in `redact`."""

    def __init__(
        self,
        repo: Path,
        cfg: RailConfig,
        *,
        run: Runner = subprocess.run,
        http: Http = http_get,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if cfg.deploy is None:
            raise DeployError(f"{cfg.project} declares no deploy target")
        self.repo = repo
        self.cfg = cfg
        self.params = Parameters.read(repo)
        self.healthcheck = cfg.deploy.healthcheck
        self._run, self._http, self._sleep, self._clock = run, http, sleep, clock

    # -- the seams ------------------------------------------------------------------------

    @property
    def origin(self) -> str:
        """Scheme and authority the verification talks to, without a trailing slash."""
        raise NotImplementedError

    def script_for(self, artefact: Artefact) -> str:
        """The remote phase as one bash script. Raising here refuses the deployment before
        anything reaches the machine."""
        raise NotImplementedError

    def describe(self, artefact: Artefact) -> str:
        """The title of the remote step, as `--plan` and the confirmation print it."""
        raise NotImplementedError

    def redact(self, text: str) -> str:
        """What a record may say about this target. The default hides nothing."""
        return text

    # -- the shared mechanics -------------------------------------------------------------

    def file_at(self, sha: str, path: str, what: str) -> str:
        """A file exactly as released: `git show <sha>:<path>`, never the working tree."""
        done = self._run(
            ["git", "-C", str(self.repo), "show", f"{sha}:{path}"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": "C"},  # git's own words, one language
        )
        if done.returncode != 0:
            raise DeployError(
                f"{path} is not committed at {sha[:12]} — {what} is read at the released "
                "commit, never from the working tree"
            )
        return done.stdout

    def steps(self, artefact: Artefact) -> list[Step]:
        script = self.script_for(artefact)
        return [
            Step(self.describe(artefact), ssh_argv(self.params), script),
            Step(
                f"GET {self.healthcheck} until 200 (≤ {self.params.healthcheck_timeout}s)",
                ("GET", self.healthcheck),
            ),
            Step(
                f"GET {self.origin}/version == {artefact.version} / "
                f"{artefact.sha[:12]} / {artefact.digest}",
                ("GET", f"{self.origin}/version"),
            ),
        ]

    def apply(self, artefact: Artefact) -> LiveVersion:
        step = self.steps(artefact)[0]
        try:
            done = self._run(
                list(step.argv),
                input=step.stdin,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.params.remote_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # the lock lives in the ssh session: killing it releases the target
            raise DeployError(
                f"remote deployment of {artefact.version} timed out after "
                f"{self.params.remote_timeout}s; the ssh session was killed and the lock released"
            ) from exc
        except (FileNotFoundError, OSError) as exc:
            raise DeployError(f"ssh is not available on this host: {exc}") from exc
        if done.returncode == LOCKED:
            raise Locked((done.stderr or done.stdout).strip())
        if done.returncode != 0:
            detail = _tail(done.stderr or done.stdout)
            raise DeployError(
                f"remote deployment of {artefact.version} failed (exit {done.returncode}): {detail}"
            )
        return self.verify(artefact)

    def verify(self, artefact: Artefact) -> LiveVersion:
        """From outside: healthy, then `/version` equal to the artefact."""
        self._wait_healthy()
        live = self.live_version()
        if live is None:
            raise DeployError(f"{self.origin}/version does not answer a version")
        mismatch = [
            f"{field}: live {getattr(live, field)!r} ≠ artefact {value!r}"
            for field, value in (
                ("version", artefact.version),
                ("git_sha", artefact.sha),
                ("image_digest", artefact.digest),
            )
            if getattr(live, field) != value
        ]
        if mismatch:
            raise DeployError("the live service differs from the artefact — " + "; ".join(mismatch))
        return live

    def _wait_healthy(self) -> None:
        deadline = self._clock() + self.params.healthcheck_timeout
        last = "no answer"
        while True:
            try:
                status, _ = self._http(self.healthcheck, 5.0)
                last = f"HTTP {status}"
                if status == 200:
                    return
            except HttpError as exc:
                last = str(exc)
            if self._clock() >= deadline:
                raise DeployError(
                    f"{self.healthcheck}: not healthy within "
                    f"{self.params.healthcheck_timeout}s ({last})"
                )
            self._sleep(POLL_SECONDS)

    def live_version(self) -> LiveVersion | None:
        try:
            status, body = self._http(f"{self.origin}/version", 5.0)
        except HttpError:
            return None
        if status != 200:
            return None
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return LiveVersion(
            project=str(data.get("project", "")),
            version=str(data.get("version", "")),
            git_sha=str(data.get("git_sha", "")),
            image_digest=str(data.get("image_digest", "")),
        )
```

- [ ] **Step 3: Rewrite `src/rail/deploy/compose.py` to keep only the compose half**

Replace the whole file with the following. `common_env` and `remote_script` are unchanged,
except that `remote_script` calls `heredoc` instead of `_heredoc`.

```python
"""What every compose target does identically, written once.

One stack per project under `<stack_root>/<project>` — `releases/<version>/{compose.yaml,.env}`
and a `current` symlink — the compose file read at the **released commit**, the image pulled by
digest, and the container's own healthcheck as the gate. Only the `.env` changes between
releases. The lock, the ssh handling and the verification are what every remote target shares:
they live in `deploy/remote.py`.

A concrete compose target supplies exactly two things: the environment its deployment needs
(`env_file`) and the origin its verification talks to (`origin`).
"""

from __future__ import annotations

from rail.deploy import Artefact
from rail.deploy.remote import (
    LOCKED,
    POLL_SECONDS,
    Parameters,
    RemoteTarget,
    Runner,
    env_lines,
    heredoc,
    ssh_argv,
)

# `vps_traefik` and its tests import these names from here: they stay published
__all__ = [
    "LOCKED",
    "POLL_SECONDS",
    "ComposeTarget",
    "Parameters",
    "Runner",
    "common_env",
    "env_lines",
    "remote_script",
    "ssh_argv",
]


def common_env(project: str, artefact: Artefact) -> tuple[tuple[str, str], ...]:
    """What a deployment needs whatever its shape: who it is, and exactly what it is running."""
    return (
        ("COMPOSE_PROJECT_NAME", project),
        ("IMAGE_REFERENCE", artefact.image),
        ("IMAGE_DIGEST", artefact.digest),
        ("GIT_SHA", artefact.sha),
        ("VERSION", artefact.version),
    )


def remote_script(
    project: str, version: str, compose_text: str, env_text: str, params: Parameters
) -> str:
    """The whole remote phase as one bash script: lock, files, pull by digest, up with the
    container's healthcheck as the gate, `current` symlink, the stack as JSON on stdout."""
    root = f"{params.stack_root}/{project}"
    compose = f'docker compose --project-name {project} --project-directory "$release"'
    return "\n".join(
        [
            "set -euo pipefail",
            f"root={root}",
            f"release={root}/releases/{version}",
            'mkdir -p "$release"',
            'exec 9>"$root/.deploy.lock"',
            f'flock -n 9 || {{ echo "another deployment holds $root/.deploy.lock" >&2; '
            f"exit {LOCKED}; }}",
            'cd "$release"',
            heredoc("compose.yaml", compose_text),
            heredoc(".env", env_text),
            "chmod 600 .env",
            f"{compose} pull --quiet",
            f"{compose} up --detach --remove-orphans --wait "
            f"--wait-timeout {params.healthcheck_timeout}",
            'ln -sfn "$release" "$root/current"',
            f"{compose} ps --format json",
            "",
        ]
    )


class ComposeTarget(RemoteTarget):
    """The compose half. A subclass supplies `env_file` and `origin`, and may refuse a
    deployment in `precheck` before anything reaches the machine."""

    def env_file(self, artefact: Artefact) -> str:
        raise NotImplementedError

    def precheck(self, compose_text: str) -> None:
        """Refuse the deployment before the first ssh. The default refuses nothing."""

    def compose_at(self, sha: str) -> str:
        """The compose file exactly as released: `git show <sha>:<compose_path>`."""
        return self.file_at(sha, self.params.compose_path, "the compose file")

    def script_for(self, artefact: Artefact) -> str:
        compose_text = self.compose_at(artefact.sha)
        self.precheck(compose_text)
        return remote_script(
            self.cfg.project, artefact.version, compose_text, self.env_file(artefact), self.params
        )

    def describe(self, artefact: Artefact) -> str:
        stack = f"{self.params.stack_root}/{self.cfg.project}"
        return f"ssh {self.params.ssh_host}: release {artefact.version} under {stack}"
```

- [ ] **Step 4: Verify nothing else imported the moved private names**

Run: `grep -rn "_heredoc\|compose import.*_tail" src tests`
Expected: no output.

- [ ] **Step 5: Run the proof suites and the whole suite**

Run: `uv run pytest -q tests/test_deploy_vps_traefik.py tests/test_deploy_private_compose.py tests/test_cli_deploy.py`
Expected: PASS, with the same count as Step 1 and none of these files modified.
Then run `uv run pytest -q` and `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`.
Expected: all pass, lint clean.

- [ ] **Step 6: Commit**

```bash
git add src/rail/deploy/remote.py src/rail/deploy/compose.py
git commit  # ♻️ refactor(deploy): what every ssh target shares moves to deploy/remote.py
```

---

### Task 3: A site is resolved once, in `RemoteTarget`, for every private target

`private-compose` did the site work itself: loading the site, filling the healthcheck, naming
the site in records and redacting the address. It moves into `RemoteTarget`, where the
healthcheck already lives, so `private-systemd` inherits it instead of copying it (spec
decision 9).

**Files:**
- Modify: `src/rail/deploy/sites.py` (add `SiteBinding`)
- Modify: `src/rail/deploy/remote.py` (`RemoteTarget.__init__`, `origin`, `redact`)
- Modify: `src/rail/deploy/private_compose.py` (drop `Parameters`, keep `bind_address`)
- Test: `tests/test_sites.py` (one new test). `tests/test_deploy_private_compose.py` stays unchanged: it is the proof.

**Interfaces:**
- Consumes: `load_site`, `substitute_address`, `redact_address` (`rail.deploy.sites`), `domain_of` (`rail.deploy`).
- Produces:
  - `SiteBinding(site: str, address: Address)` with `.load(site, path=None)`, `.fill(url) -> str`
    and `.redact(text) -> str`;
  - `RemoteTarget(repo, cfg, *, sites: Path | None = None, run=…, http=…, sleep=…, clock=…)`,
    which sets `self.binding: SiteBinding | None`, `self.healthcheck` (filled) and
    `self.domain` (the site behind one);
  - the default `origin` is the healthcheck's scheme and authority;
  - the default `redact` replaces the site's address with its name.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sites.py`, and add `SiteBinding` to its `from rail.deploy.sites import …` line:

```python
def test_a_binding_fills_urls_and_redacts_with_the_site_name(tmp_path: Path) -> None:
    path = _sites(tmp_path, f'sites:\n  private-1:\n    address: "{V4}"\n')
    binding = SiteBinding.load("private-1", path)
    assert binding.fill("http://${BIND_ADDRESS}:9100/health") == f"http://{V4}:9100/health"
    assert binding.redact(f"connect to host {V4} port 22") == "connect to host private-1 port 22"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_sites.py`
Expected: FAIL with `ImportError: cannot import name 'SiteBinding'`.

- [ ] **Step 3: Add `SiteBinding` to `src/rail/deploy/sites.py`**

Add `from dataclasses import dataclass` to the imports, and append at the end of the module:

```python
@dataclass(frozen=True, slots=True)
class SiteBinding:
    """A private target's site as a deployment uses it: its name is what records say, its
    address is what URLs reach. Loaded once, before any step is planned."""

    site: str
    address: Address

    @classmethod
    def load(cls, site: str, path: Path | None = None) -> SiteBinding:
        return cls(site=site, address=load_site(site, path).address)

    def fill(self, url: str) -> str:
        return substitute_address(url, self.address)

    def redact(self, text: str) -> str:
        return redact_address(text, self.address, self.site)
```

- [ ] **Step 4: Resolve the site in `RemoteTarget`**

In `src/rail/deploy/remote.py`:
- add `from urllib.parse import urlsplit`;
- add `domain_of` to the `from rail.deploy import …` line;
- add `from rail.deploy.sites import SiteBinding`.

Then replace `RemoteTarget.__init__`, the `origin` property and `redact` with:

```python
    def __init__(
        self,
        repo: Path,
        cfg: RailConfig,
        *,
        sites: Path | None = None,
        run: Runner = subprocess.run,
        http: Http = http_get,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if cfg.deploy is None:
            raise DeployError(f"{cfg.project} declares no deploy target")
        self.repo = repo
        self.cfg = cfg
        self.params = Parameters.read(repo)
        self._run, self._http, self._sleep, self._clock = run, http, sleep, clock
        # behind a site the token becomes the site's address here and nowhere earlier: the
        # manifest carries a label, this host's sites file carries the address
        site = cfg.deploy.site
        self.binding = SiteBinding.load(site, sites) if site is not None else None
        self.healthcheck = cfg.deploy.healthcheck
        if self.binding is not None:
            self.healthcheck = self.binding.fill(self.healthcheck)
        # `Field(pattern=r"^https?://")` is match-at-start, so `https://` passes validation:
        # the host is checked here, before the first ssh, not after the healthcheck timeout
        self.domain = domain_of(self.healthcheck)
        if self.binding is not None:
            self.domain = self.binding.site  # what records and prompts name: never the address

    @property
    def origin(self) -> str:
        """Scheme and authority of the healthcheck: a private service is verified over the
        address and port it was given. A public shape overrides it."""
        split = urlsplit(self.healthcheck)
        return f"{split.scheme}://{split.netloc}"  # netloc, not hostname: the port matters

    def redact(self, text: str) -> str:
        """What a record may say about this target: behind a site, its name, never its address."""
        return text if self.binding is None else self.binding.redact(text)
```

- [ ] **Step 5: Shrink `PrivateCompose` to what is compose-and-private**

In `src/rail/deploy/private_compose.py`, delete the `Parameters` dataclass and the
`PrivateCompose` class. Replace them with:

```python
def declared_bind_address(repo: Path) -> str:
    """Without a site, the address a private compose target publishes on is declared in
    rail.yaml; there is no default, because a target that cannot say where it publishes cannot
    refuse a compose file that publishes everywhere."""
    value = parameter(repo, "deploy.bind_address")
    if not value:
        raise DeployError(
            "deploy.bind_address is not set: a private target must say which address it "
            "publishes on — name a `deploy.site` this host declares in its sites file, or "
            "declare the address in rail.yaml under `gates:` with its reason"
        )
    return str(value)


class PrivateCompose(ComposeTarget):
    def __init__(self, repo: Path, cfg: RailConfig, **kwargs: Any) -> None:
        super().__init__(repo, cfg, **kwargs)
        # behind a site the host gives the address; without one, rail.yaml declares it
        self.bind_address = (
            str(self.binding.address) if self.binding is not None else declared_bind_address(repo)
        )

    def env_file(self, artefact: Artefact) -> str:
        return env_lines(
            common_env(self.cfg.project, artefact) + (("BIND_ADDRESS", self.bind_address),)
        )

    def precheck(self, compose_text: str) -> None:
        offenders = published_ports_are_private(compose_text, self.bind_address)
        if offenders:
            listed = ", ".join(f"{service} → {entry}" for service, entry in offenders)
            raise DeployError(
                f"the released compose file publishes outside {self.bind_address} "
                f"and loopback: {listed}. Docker bypasses the firewall, so a published port's "
                f"host address must EQUAL {self.bind_address} (or ${{BIND_ADDRESS}}, "
                "which the rail writes), not merely be present — 0.0.0.0 names an address and "
                "publishes everywhere"
            )
```

Remove the imports this leaves unused: `dataclass`, `urlsplit`, `domain_of`, and the whole
`from rail.deploy.sites import …` line. `uv run ruff check --fix src/rail/deploy/private_compose.py`
reports them. Keep the module docstring, `LOOPBACK`, `BIND_VARIABLE`, `_host_of` and
`published_ports_are_private` as they are.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_sites.py tests/test_deploy_private_compose.py tests/test_deploy_vps_traefik.py tests/test_cli_deploy.py`
Expected: PASS. `git diff origin/main -- tests/test_deploy_vps_traefik.py` prints nothing, and
`git diff origin/main -- tests/test_deploy_private_compose.py` shows only the Task 1 replacement.
Then run `uv run pytest -q` and ruff. Expected: all pass, lint clean.

- [ ] **Step 7: Commit**

```bash
git add src/rail/deploy/sites.py src/rail/deploy/remote.py src/rail/deploy/private_compose.py tests/test_sites.py
git commit  # ♻️ refactor(deploy): a site is resolved once, in RemoteTarget, for every private target
```

---

### Task 4: The unit refusals of `private-systemd`

A pure function over the unit's text: spec decision 7, and Review Focus 1 and 2.

**Files:**
- Create: `src/rail/deploy/private_systemd.py`
- Test: `tests/test_deploy_private_systemd.py` (new)

**Interfaces:**
- Produces:
  - `parse_unit(text: str) -> dict[str, dict[str, list[str]]]`;
  - `unit_refusals(text: str, *, unit: str, current: str, binary_name: str) -> list[str]`.
    Every sentence starts with the unit's name, and an empty list means the unit is accepted.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_deploy_private_systemd.py`:

```python
"""Target `private-systemd`: a binary that systemd runs (spec
2026-09-24-private-systemd-target). Addresses are RFC 5737 documentation addresses only."""

from pathlib import Path

import pytest

from rail.deploy.private_systemd import parse_unit, unit_refusals

CURRENT = "/opt/red-monitor/current"
UNIT = "red-agent.service"

GOOD = """\
[Unit]
Description=ReD Monitoring Agent
After=network-online.target docker.service

[Service]
Type=simple
User=red-monitor
Group=red-monitor
SupplementaryGroups=docker
EnvironmentFile=/opt/red-monitor/current/release.env
ExecStart=/opt/red-monitor/current/red agent -config /etc/red-monitor/agent.yaml
Restart=on-failure
TimeoutStopSec=15
MemoryMax=64M
NoNewPrivileges=true
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
"""


def _refusals(text: str) -> list[str]:
    return unit_refusals(text, unit=UNIT, current=CURRENT, binary_name="red")


def test_the_hardened_unit_is_accepted() -> None:
    assert _refusals(GOOD) == []


def test_the_parser_reads_sections_keys_and_continuations() -> None:
    unit = parse_unit(
        "[Service]\n# a comment\nExecStart=/bin/a \\\n  --flag\n; another\nUser = svc\n"
    )
    assert unit["Service"]["ExecStart"] == ["/bin/a --flag"]
    assert unit["Service"]["User"] == ["svc"]


@pytest.mark.parametrize(
    ("old", "new", "rule"),
    [
        ("User=red-monitor\n", "", "User="),
        ("User=red-monitor\n", "User=root\n", "User="),
        ("User=red-monitor\n", "User=0\n", "User="),
        ("MemoryMax=64M\n", "", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=infinity\n", "MemoryMax="),
        ("TimeoutStopSec=15\n", "", "TimeoutStopSec="),
        ("TimeoutStopSec=15\n", "TimeoutStopSec=infinity\n", "TimeoutStopSec="),
        ("TimeoutStopSec=15\n", "TimeoutStopSec=0\n", "TimeoutStopSec="),
        ("/opt/red-monitor/current/red agent", "/opt/red-monitor/red agent", "ExecStart="),
        ("ExecStart=/opt", "ExecStart=/opt/red-monitor/current/red\nExecStart=/opt", "ExecStart="),
        ("EnvironmentFile=/opt", "EnvironmentFile=-/opt", "EnvironmentFile="),
        ("EnvironmentFile=/opt/red-monitor/current/release.env\n", "", "EnvironmentFile="),
        ("Type=simple\n", "Type=simple\nExecStartPre=+/bin/chmod 777 /etc\n", "full privileges"),
        ("Type=simple\n", "Type=simple\nExecStartPost=!!/bin/true\n", "full privileges"),
        ("ExecStart=/opt", "ExecStart=+/opt", "full privileges"),
        ("Type=simple\n", "Type=simple\nPermissionsStartOnly=yes\n", "PermissionsStartOnly"),
    ],
)
def test_each_rule_refuses_the_unit_and_says_which(old: str, new: str, rule: str) -> None:
    assert old in GOOD, "the fixture must contain what the case replaces"
    refusals = _refusals(GOOD.replace(old, new, 1))
    assert any(rule in refusal for refusal in refusals), refusals
    assert all(refusal.startswith(UNIT) for refusal in refusals), refusals


def test_a_unit_without_a_service_section_is_refused() -> None:
    assert _refusals("[Unit]\nDescription=x\n") == [f"{UNIT} has no [Service] section"]


def test_the_last_assignment_wins_as_it_does_for_systemd() -> None:
    """Review focus 1: `User=` twice, the root one last and spaced — systemd runs as root."""
    text = GOOD.replace("User=red-monitor\n", "User=red-monitor\nUser = root\n")
    assert any("User=" in refusal for refusal in _refusals(text))


def test_a_privilege_prefix_behind_a_continuation_and_a_comment_is_seen() -> None:
    """Review focus 2: systemd drops a comment inside a continuation, then runs `+…` as root."""
    hidden = "Type=simple\nExecStartPre=\\\n# looks harmless\n  +/bin/sh -c id\n"
    refusals = _refusals(GOOD.replace("Type=simple\n", hidden, 1))
    assert any("full privileges" in refusal for refusal in refusals), refusals
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_deploy_private_systemd.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'rail.deploy.private_systemd'`.

- [ ] **Step 3: Write `src/rail/deploy/private_systemd.py`**

```python
"""Target `private-systemd`: a binary that systemd runs, on a host with no public route (spec
2026-09-24-private-systemd-target).

The artefact is the project's released image. The target copies the binary out of it by
digest, writes the project's unit (read at the released commit) and `release.env` next to
it, points `current` at the release, and restarts the unit through the two commands the
host's sudoers file allows. systemd loads the unit through a link to `current`, made once at
migration, so the unit on disk is always the live release's.

Before anything reaches the machine, the unit is refused when it would run as root, run
unbounded, or run something other than the release (decision 7). That guards against a
mistake, not against a compromised deploy account: that account is in the docker group, which
is root already.
"""

from __future__ import annotations

from collections.abc import Iterator

# `+`, `!` and `!!` run a command with full privileges whatever `User=` says; `@`, `-` and `:`
# change how it runs, not who runs it (systemd.service, "Command lines")
_EXEC_PREFIXES = frozenset("@-:+!")
_PRIVILEGED = frozenset("+!")
_TRUE = frozenset({"1", "yes", "true", "on"})


def _logical_lines(text: str) -> Iterator[str]:
    """Lines as systemd reads them: comments dropped, inside a continuation too, and a trailing
    backslash joining a line to the next with a space."""
    parts: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped[:1] in ("#", ";"):
            continue
        if stripped.endswith("\\"):
            parts.append(stripped[:-1].strip())
            continue
        parts.append(stripped)
        line = " ".join(part for part in parts if part)
        parts = []
        if line:
            yield line
    line = " ".join(part for part in parts if part)
    if line:
        yield line


def parse_unit(text: str) -> dict[str, dict[str, list[str]]]:
    """Section → key → values, in file order. Only what the refusals read is modelled: this is
    not a systemd parser and does not pretend to be one."""
    sections: dict[str, dict[str, list[str]]] = {}
    section: dict[str, list[str]] | None = None
    for line in _logical_lines(text):
        if line.startswith("[") and line.endswith("]"):
            section = sections.setdefault(line[1:-1].strip(), {})
        elif section is not None and "=" in line:
            key, _, value = line.partition("=")
            section.setdefault(key.strip(), []).append(value.strip())
    return sections


def _prefix(value: str) -> str:
    """The run-mode prefix characters at the start of an `Exec*=` value."""
    end = 0
    while end < len(value) and value[end] in _EXEC_PREFIXES:
        end += 1
    return value[:end]


def _program(value: str) -> str:
    words = value[len(_prefix(value)) :].split()
    return words[0] if words else ""


def unit_refusals(text: str, *, unit: str, current: str, binary_name: str) -> list[str]:
    """Every rule of spec decision 7 the unit breaks, one sentence each, each starting with the
    unit's name; empty means accepted. `current` is `<stack_root>/<project>/current`."""
    service = parse_unit(text).get("Service")
    if service is None:
        return [f"{unit} has no [Service] section"]

    def last(key: str) -> str | None:
        values = service.get(key)
        return values[-1] if values else None  # systemd keeps the last assignment

    refusals: list[str] = []
    user = last("User")
    if user in (None, "", "root", "0"):
        refusals.append(f"{unit}: User= must name a user other than root (got {user!r})")
    memory = last("MemoryMax")
    if memory in (None, "", "infinity"):
        refusals.append(
            f"{unit}: MemoryMax= must bound the service, the host has no swap (got {memory!r})"
        )
    stop = last("TimeoutStopSec")
    if stop in (None, "", "0", "infinity"):
        refusals.append(f"{unit}: TimeoutStopSec= must bound the stop (got {stop!r})")
    program = f"{current}/{binary_name}"
    starts = [value for value in service.get("ExecStart", []) if value]
    if len(starts) != 1 or _program(starts[0]) != program:
        refusals.append(f"{unit}: exactly one ExecStart= must run {program} (got {starts!r})")
    environment = f"{current}/release.env"
    if environment not in service.get("EnvironmentFile", []):
        refusals.append(
            f"{unit}: EnvironmentFile={environment} is missing, without the `-` prefix: "
            "the file must exist"
        )
    for key, values in service.items():
        if key.startswith("Exec"):
            refusals.extend(
                f"{unit}: {key}={value} runs with full privileges (prefix + or !)"
                for value in values
                if _PRIVILEGED & set(_prefix(value))
            )
    if (last("PermissionsStartOnly") or "").lower() in _TRUE:
        refusals.append(f"{unit}: PermissionsStartOnly= runs the Exec*Pre/Post commands as root")
    return refusals
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_deploy_private_systemd.py`
Expected: PASS. Then run `uv run pytest -q` and ruff. Expected: all pass, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/rail/deploy/private_systemd.py tests/test_deploy_private_systemd.py
git commit  # 🦺 feat(deploy): the unit refusals of private-systemd
```

---

### Task 5: The `private-systemd` target

The remote script (spec decisions 5, 6, 8 and 12), its dispatch from the manifest, and
`--plan`. Review Focus 3 and 4 are run under bash with stubbed `docker`, `sudo` and
`systemctl`, not only read.

**Files:**
- Modify: `src/rail/deploy/private_systemd.py` (append the target)
- Modify: `src/rail/deploy/flow.py` (`implementations`, `make_target`)
- Test: `tests/test_deploy_private_systemd.py`

**Interfaces:**
- Consumes: `RemoteTarget`, `Parameters`, `LOCKED`, `env_lines`, `heredoc` (Task 2); the site handling of `RemoteTarget` (Task 3); `unit_refusals` (Task 4).
- Produces:
  - `release_env(artefact) -> str` and
    `remote_script(project, artefact, unit, unit_text, binary, params) -> str`;
  - `class PrivateSystemd(RemoteTarget)`, with `unit` (the unit's name), `unit_path`,
    `binary` and `current`;
  - `flow.implementations() -> dict[DeployTarget, type]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_deploy_private_systemd.py`:

```python
# -- the target ---------------------------------------------------------------------------

import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

from click.testing import CliRunner  # noqa: E402

from rail.cli import main  # noqa: E402
from rail.deploy import Artefact, DeployError  # noqa: E402
from rail.deploy.flow import implementations, make_target  # noqa: E402
from rail.deploy.private_systemd import PrivateSystemd  # noqa: E402
from rail.ledger import RECEIPTS_DIR, AttestationKind  # noqa: E402
from rail.ledger.file import FileLedger  # noqa: E402
from rail.model import DeployTarget, load_rail_config  # noqa: E402
from tests.helpers import commit_all, conforming_tree, write_manifest  # noqa: E402

BIND = "192.0.2.10"  # RFC 5737 TEST-NET-1: a documentation address, never a real host
DIGEST = "sha256:" + "c" * 64
IMAGE = f"ghcr.io/hawkixs/red-monitor@{DIGEST}"


def _unit_for(stack_root: str) -> str:
    return GOOD.replace("/opt/red-monitor", f"{stack_root}/red-monitor")


def _systemd_repo(
    tmp_path: Path, unit_text: str = GOOD, *, stack_root: str | None = None
) -> Path:
    repo = conforming_tree(tmp_path, "red-monitor", "prod")
    gates: dict[str, tuple[object, str]] = {
        "deploy.ssh_host": ("private-1-deploy", "the host's ssh alias for the site")
    }
    if stack_root is not None:
        gates["deploy.stack_root"] = (stack_root, "a scratch root for a test run")
    write_manifest(repo, project="red-monitor", tier="prod", gates=gates, deploy=True)
    manifest = (
        (repo / "rail.yaml")
        .read_text()
        .replace(
            "  target: vps-traefik\n",
            "  target: private-systemd\n  site: private-1\n"
            "  unit: deploy/red-agent.service\n  binary: /usr/local/bin/red\n",
        )
    )
    manifest = "\n".join(
        '  healthcheck: "http://${BIND_ADDRESS}:9100/health"'
        if line.strip().startswith("healthcheck:")
        else line
        for line in manifest.splitlines()
    )
    (repo / "rail.yaml").write_text(manifest + "\n")
    (repo / "deploy").mkdir(exist_ok=True)
    (repo / "deploy" / "red-agent.service").write_text(unit_text)
    commit_all(repo, "feat: the agent's unit")
    return repo


def _host(tmp_path: Path) -> Path:
    path = tmp_path / "sites.yaml"
    path.write_text(f'sites:\n  private-1:\n    address: "{BIND}"\n')
    path.chmod(0o600)
    return path


class RecordingHost:
    def __init__(self) -> None:
        self.argv: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.argv.append(list(args))
        if args[0] == "ssh":
            return subprocess.CompletedProcess(args, 0, stdout="active\n", stderr="")
        return subprocess.run(args, **kwargs)


def _artefact(repo: Path) -> Artefact:
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return Artefact(version="0.1.0", sha=head, digest=DIGEST, image=IMAGE)


def _target(repo: Path, tmp_path: Path, *, run=None, http=None) -> PrivateSystemd:
    kwargs: dict[str, object] = {"run": run or RecordingHost()}
    if http is not None:
        kwargs["http"] = http
    return PrivateSystemd(repo, load_rail_config(repo), sites=_host(tmp_path), **kwargs)


def _script(tmp_path: Path) -> str:
    repo = _systemd_repo(tmp_path / "repo")
    return _target(repo, tmp_path).steps(_artefact(repo))[0].stdin or ""


def test_the_steps_name_the_site_and_restart_the_unit(tmp_path: Path) -> None:
    repo = _systemd_repo(tmp_path / "repo")
    steps = _target(repo, tmp_path).steps(_artefact(repo))
    assert "private-1-deploy" in steps[0].title and "restart red-agent.service" in steps[0].title
    assert steps[0].argv[0] == "ssh"
    assert steps[1].argv == ("GET", f"http://{BIND}:9100/health")
    assert steps[2].argv == ("GET", f"http://{BIND}:9100/version")


def test_the_remote_script_runs_the_spec_sequence_in_order(tmp_path: Path) -> None:
    script = _script(tmp_path)
    sequence = [
        "flock -n 9",
        "cat > red-agent.service",
        "cat > release.env",
        f"docker pull --quiet {IMAGE}",
        f"container=$(docker create {IMAGE} /usr/local/bin/red)",
        "trap ",
        'docker cp --follow-link "$container:/usr/local/bin/red"',
        'mv --force "$release/.red.new" "$release/red"',
        'ln -sfn "$release" "$root/current"',
        "sudo -n /usr/bin/systemctl daemon-reload",
        "sudo -n /usr/bin/systemctl restart red-agent.service",
        "systemctl is-active red-agent.service",
    ]
    positions = [script.index(fragment) for fragment in sequence]
    assert positions == sorted(positions), list(zip(sequence, positions, strict=True))


def test_the_only_privileged_commands_are_the_two_the_sudoers_file_allows(tmp_path: Path) -> None:
    script = _script(tmp_path)
    assert [line for line in script.splitlines() if "sudo" in line] == [
        "sudo -n /usr/bin/systemctl daemon-reload",
        "sudo -n /usr/bin/systemctl restart red-agent.service",
    ]
    assert "/etc/systemd" not in script, "the unit is linked at migration, never copied"


def test_release_env_carries_what_version_reads_and_no_secret(tmp_path: Path) -> None:
    script = _script(tmp_path)
    body = script.split("cat > release.env <<'__RAIL_RELEASE.ENV__'\n", 1)[1].split("__RAIL_")[0]
    keys = [line.split("=", 1)[0] for line in body.splitlines()]
    assert keys == ["VERSION", "GIT_SHA", "IMAGE_DIGEST", "IMAGE_REFERENCE"]


def test_a_running_binary_is_replaced_by_rename_never_written_in_place(tmp_path: Path) -> None:
    """Review focus 3: redeploying the running version must not write into the executable
    systemd is running (`Text file busy`). The copy lands under a temporary name, then a
    rename swaps it in."""
    script = _script(tmp_path)
    assert 'docker cp --follow-link "$container:/usr/local/bin/red" "$release/.red.new"' in script
    assert '"$release/red"' not in script.split("mv --force", 1)[0]


def test_a_refused_unit_never_reaches_the_machine(tmp_path: Path) -> None:
    repo = _systemd_repo(tmp_path / "repo", GOOD.replace("User=red-monitor\n", "User=root\n"))
    host = RecordingHost()
    with pytest.raises(DeployError, match="User="):
        _target(repo, tmp_path, run=host).apply(_artefact(repo))
    assert [argv for argv in host.argv if argv[0] == "ssh"] == []


def test_the_deployment_is_verified_like_every_other_target(tmp_path: Path) -> None:
    repo = _systemd_repo(tmp_path / "repo")
    artefact = _artefact(repo)
    asked: list[str] = []

    def web(url: str, timeout: float) -> tuple[int, bytes]:
        asked.append(url)
        if url.endswith("/health"):
            return 200, b'{"status":"ok"}'
        return 200, json.dumps(
            {
                "project": "red-monitor",
                "version": artefact.version,
                "git_sha": artefact.sha,
                "image_digest": artefact.digest,
            }
        ).encode()

    live = _target(repo, tmp_path, http=web).apply(artefact)
    assert live.image_digest == DIGEST
    assert asked == [f"http://{BIND}:9100/health", f"http://{BIND}:9100/version"]


def test_records_name_the_site_never_its_address(tmp_path: Path) -> None:
    repo = _systemd_repo(tmp_path / "repo")
    target = _target(repo, tmp_path)
    assert target.domain == "private-1"
    assert target.redact(f"connect to host {BIND} port 22") == "connect to host private-1 port 22"


def test_the_flows_build_the_systemd_target_from_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _systemd_repo(tmp_path / "repo")
    monkeypatch.setenv("RAIL_SITES_FILE", str(_host(tmp_path)))
    assert isinstance(make_target(repo, load_rail_config(repo)), PrivateSystemd)


def test_every_target_the_manifest_can_declare_is_implemented() -> None:
    """513e109b, criterion 1: a declarable target without an implementation must show. With
    `private-systemd` none is left, and this keeps it so."""
    assert set(implementations()) == set(DeployTarget)


def _released(repo: Path) -> None:
    artefact = _artefact(repo)
    FileLedger(repo / RECEIPTS_DIR).attest(
        "red-monitor",
        AttestationKind.RELEASED,
        {
            "version": artefact.version,
            "sha": artefact.sha,
            "digest": artefact.digest,
            "image": artefact.image,
            "tag": "v0.1.0",
        },
        issuer="op",
        idempotency_key="released:0.1.0",
    )


def test_plan_names_the_site_and_prints_the_resolved_steps(tmp_path: Path) -> None:
    repo = _systemd_repo(tmp_path / "repo")
    _released(repo)
    env = {"RAIL_SITES_FILE": str(_host(tmp_path))}
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--plan"], env=env)
    assert out.exit_code == 0, out.output
    assert "on private-1" in out.output and "private-1-deploy" in out.output
    assert f"GET http://{BIND}:9100/health" in out.output
    assert f"GET http://{BIND}:9100/version" in out.output
    assert BIND not in (repo / "rail.yaml").read_text()


# -- the script, run under bash with stubs ---------------------------------------------------

STUB = """#!/bin/sh
echo "$(basename "$0") $*" >> "$RAIL_TEST_LOG"
if [ "$(basename "$0")" = docker ]; then
  case "$1" in
    create) echo fake-container ;;
    cp)
      [ -n "$RAIL_TEST_CP_FAILS" ] && exit 1
      for last in "$@"; do :; done
      echo binary > "$last" ;;
  esac
fi
exit 0
"""


def _run_remote(
    tmp_path: Path, *, cp_fails: bool
) -> tuple[subprocess.CompletedProcess, list[str], Path]:
    if shutil.which("flock") is None or shutil.which("bash") is None:
        pytest.skip("the remote script needs bash and flock (util-linux)")
    root = tmp_path / "opt"
    repo = _systemd_repo(tmp_path / "repo", _unit_for(str(root)), stack_root=str(root))
    script = _target(repo, tmp_path).steps(_artefact(repo))[0].stdin or ""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name in ("docker", "sudo", "systemctl"):
        (stubs / name).write_text(STUB)
        (stubs / name).chmod(0o755)
    log = tmp_path / "calls.log"
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "RAIL_TEST_LOG": str(log)}
    if cp_fails:
        env["RAIL_TEST_CP_FAILS"] = "1"
    done = subprocess.run(
        ["bash", "-s"], input=script, env=env, capture_output=True, text=True, timeout=30
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return done, calls, root / "red-monitor"


def test_the_remote_script_delivers_the_binary_and_restarts_through_sudo(tmp_path: Path) -> None:
    done, calls, project = _run_remote(tmp_path, cp_fails=False)
    assert done.returncode == 0, done.stderr
    release = project / "releases" / "0.1.0"
    assert (project / "current").resolve() == release.resolve()
    assert (release / "red").read_text() == "binary\n"
    assert (release / "red").stat().st_mode & 0o777 == 0o755
    assert not (release / ".red.new").exists()
    assert "VERSION=0.1.0" in (release / "release.env").read_text()
    reload = calls.index("sudo -n /usr/bin/systemctl daemon-reload")
    assert reload < calls.index("sudo -n /usr/bin/systemctl restart red-agent.service")
    assert calls[-1] == "docker rm --force fake-container"


def test_the_container_is_removed_even_when_the_copy_fails(tmp_path: Path) -> None:
    """Review focus 4, run rather than read: with a failing `docker cp` the script exits
    non-zero, the trap still removes the container, and nothing is restarted."""
    done, calls, project = _run_remote(tmp_path, cp_fails=True)
    assert done.returncode != 0
    assert calls[-1] == "docker rm --force fake-container"
    assert not any(call.startswith("sudo") for call in calls)
    assert not (project / "current").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_deploy_private_systemd.py`
Expected: FAIL with `ImportError: cannot import name 'implementations' from 'rail.deploy.flow'`.

- [ ] **Step 3: Append the target to `src/rail/deploy/private_systemd.py`**

Add these imports next to the existing one:

```python
from pathlib import Path, PurePosixPath
from typing import Any

from rail.deploy import Artefact, DeployError
from rail.deploy.remote import LOCKED, Parameters, RemoteTarget, env_lines, heredoc
from rail.model import RailConfig
```

Append to the module:

```python
def release_env(artefact: Artefact) -> str:
    """What the binary reads through `EnvironmentFile=` to answer `/version`, never a secret."""
    return env_lines(
        (
            ("VERSION", artefact.version),
            ("GIT_SHA", artefact.sha),
            ("IMAGE_DIGEST", artefact.digest),
            ("IMAGE_REFERENCE", artefact.image),
        )
    )


def remote_script(
    project: str, artefact: Artefact, unit: str, unit_text: str, binary: str, params: Parameters
) -> str:
    """The remote phase as one bash script, in the order of spec decision 5."""
    root = f"{params.stack_root}/{project}"
    name = PurePosixPath(binary).name
    staged = f'"$release/.{name}.new"'
    return "\n".join(
        [
            "set -euo pipefail",
            f"root={root}",
            f"release={root}/releases/{artefact.version}",
            'mkdir -p "$release"',
            'exec 9>"$root/.deploy.lock"',
            f'flock -n 9 || {{ echo "another deployment holds $root/.deploy.lock" >&2; '
            f"exit {LOCKED}; }}",
            'cd "$release"',
            heredoc(unit, unit_text),
            heredoc("release.env", release_env(artefact)),
            f"chmod 0644 {unit} release.env",
            f"docker pull --quiet {artefact.image}",
            # an explicit command: `docker create` refuses an image without CMD otherwise
            f"container=$(docker create {artefact.image} {binary})",
            # set before the first command that can fail with a container to clean up
            "trap 'docker rm --force \"$container\" >/dev/null 2>&1 || true' EXIT",
            f'docker cp --follow-link "$container:{binary}" {staged}',
            f"chmod 0755 {staged}",
            # a rename, never a write into the file: redeploying the running version must not
            # hit `Text file busy`
            f'mv --force {staged} "$release/{name}"',
            'ln -sfn "$release" "$root/current"',
            "sudo -n /usr/bin/systemctl daemon-reload",
            f"sudo -n /usr/bin/systemctl restart {unit}",
            f"systemctl is-active {unit}",
            "",
        ]
    )


class PrivateSystemd(RemoteTarget):
    def __init__(self, repo: Path, cfg: RailConfig, **kwargs: Any) -> None:
        super().__init__(repo, cfg, **kwargs)
        deploy = cfg.deploy
        if deploy is None or deploy.unit is None or deploy.binary is None:
            # unreachable through a manifest: the model requires both for this target
            raise DeployError("target private-systemd needs deploy.unit and deploy.binary")
        self.unit_path = deploy.unit
        self.unit = PurePosixPath(deploy.unit).name
        self.binary = deploy.binary

    @property
    def current(self) -> str:
        return f"{self.params.stack_root}/{self.cfg.project}/current"

    def script_for(self, artefact: Artefact) -> str:
        unit_text = self.file_at(artefact.sha, self.unit_path, "the unit file")
        refusals = unit_refusals(
            unit_text,
            unit=self.unit,
            current=self.current,
            binary_name=PurePosixPath(self.binary).name,
        )
        if refusals:
            raise DeployError(
                "the released unit is refused before the first ssh — " + "; ".join(refusals)
            )
        return remote_script(
            self.cfg.project, artefact, self.unit, unit_text, self.binary, self.params
        )

    def describe(self, artefact: Artefact) -> str:
        stack = f"{self.params.stack_root}/{self.cfg.project}"
        return (
            f"ssh {self.params.ssh_host}: release {artefact.version} under {stack}, "
            f"restart {self.unit}"
        )
```

- [ ] **Step 4: Dispatch it from the manifest**

In `src/rail/deploy/flow.py`, replace `make_target` with:

```python
def implementations() -> dict[DeployTarget, type]:
    """Every shape the rail can build, by the manifest value that names it."""
    from rail.deploy.private_compose import PrivateCompose
    from rail.deploy.private_systemd import PrivateSystemd
    from rail.deploy.vps_traefik import VpsTraefik

    return {
        DeployTarget.VPS_TRAEFIK: VpsTraefik,
        DeployTarget.PRIVATE_COMPOSE: PrivateCompose,
        DeployTarget.PRIVATE_SYSTEMD: PrivateSystemd,
    }


def make_target(repo: Path, cfg: RailConfig, **kwargs: Any) -> Target:
    """The manifest names the shape; the flows never branch on it again."""
    implemented = implementations()
    shape = implemented.get(cfg.deploy.target) if cfg.deploy else None
    if shape is None:
        name = cfg.deploy.target.value if cfg.deploy else "none"
        known = ", ".join(sorted(t.value for t in implemented))
        raise DeployError(f"deploy target {name} is not implemented in this rail ({known})")
    return cast("Target", shape(repo, cfg, **kwargs))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_deploy_private_systemd.py`
Expected: PASS, including the two bash runs. They skip only where `flock` is absent; the
runner VM has it, and a skip there is a failure to investigate. Then run `uv run pytest -q`
and ruff. Expected: all pass, lint clean.

- [ ] **Step 6: Commit**

```bash
git add src/rail/deploy/private_systemd.py src/rail/deploy/flow.py tests/test_deploy_private_systemd.py
git commit  # ✨ feat(deploy): the private-systemd target
```

---

### Task 6: On `private-systemd`, `observe.visible` reads the unit

Spec decision 11, and Review Focus 5.

**Files:**
- Modify: `src/rail/monitor.py` (`Unit`, `AgentView.units`, `read_agent`, `find_unit`)
- Modify: `src/rail/gates/evidence.py` (`visible`, new `_unit_visible`)
- Test: `tests/test_monitor.py`, `tests/test_gates_evidence.py`

**Interfaces:**
- Consumes: `DeployConfig.unit` (Task 1).
- Produces:
  - `monitor.Unit(name: str, active_state: str, sub_state: str)`;
  - `AgentView.units: tuple[Unit, ...] = ()`, a default, so every existing constructor
    still works;
  - `monitor.find_unit(view, name) -> Unit | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_monitor.py`, add `Unit` and `find_unit` to the `from rail.monitor import …` line,
and append:

```python
def test_read_agent_reads_the_systemd_units_too() -> None:
    snapshot = json.loads(json.dumps(LATEST))
    snapshot["agents"]["vps"]["systemd"] = [
        {
            "name": "red-agent.service",
            "load_state": "loaded",
            "active_state": "active",
            "sub_state": "running",
        },
        "not a row",
    ]
    view = read_agent("http://192.0.2.2:8081", "vps", http=_http(body=snapshot))
    running = Unit(name="red-agent.service", active_state="active", sub_state="running")
    assert view.units == (running,)
    assert find_unit(view, "red-agent.service") == view.units[0]
    assert find_unit(view, "other.service") is None


def test_an_agent_without_systemd_rows_has_no_units() -> None:
    assert read_agent("http://192.0.2.2:8081", "vps", http=_http()).units == ()
```

In `tests/test_gates_evidence.py`, append:

```python
def _systemd_deployed_tree(tmp_path: Path) -> Path:
    repo = _deployed_tree(tmp_path)
    manifest = (repo / "rail.yaml").read_text().replace(
        "  target: vps-traefik\n",
        "  target: private-systemd\n  unit: deploy/red-agent.service\n"
        "  binary: /usr/local/bin/red\n",
    )
    (repo / "rail.yaml").write_text(manifest)
    return repo


@pytest.mark.parametrize(
    ("units", "passed", "detail"),
    [
        (
            (monitor.Unit("red-agent.service", "active", "running"),),
            True,
            "unit red-agent.service active/running on vps",
        ),
        (
            (monitor.Unit("red-agent.service", "failed", "failed"),),
            False,
            "unit red-agent.service on agent vps is failed/failed",
        ),
        # review focus 5: an agent that reports no systemd rows at all
        ((), False, "red-monitor lists no unit red-agent.service on agent vps"),
    ],
)
def test_visible_reads_the_unit_on_a_systemd_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    units: tuple[monitor.Unit, ...],
    passed: bool,
    detail: str,
) -> None:
    _sites(monkeypatch, tmp_path, f'sites:\n  red-monitor:\n    address: "{MONITOR}"\n')
    repo = _systemd_deployed_tree(tmp_path)
    view = AgentView(agent="vps", status="up", last_seen=T0, containers=(), units=units)
    monkeypatch.setattr(monitor, "read_agent", lambda base_url, agent, **kwargs: view)
    result = visible(repo)
    assert result.passed is passed and detail in result.details, result.details
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_monitor.py tests/test_gates_evidence.py`
Expected: FAIL with `ImportError: cannot import name 'Unit'`, then, in the gate tests, the
details of a container check (`no running container of stack red-beta`).

- [ ] **Step 3: Read the units in `src/rail/monitor.py`**

After `class Container`, add:

```python
@dataclass(frozen=True, slots=True)
class Unit:
    name: str
    active_state: str
    sub_state: str
```

Add the field at the end of `AgentView`:

```python
    units: tuple[Unit, ...] = ()  # systemd units, for a target that runs no container
```

In `read_agent`, just before `return AgentView(`, add:

```python
    unit_rows = data.get("systemd")
    units = tuple(
        Unit(
            name=str(row.get("name", "")),
            active_state=str(row.get("active_state", "")),
            sub_state=str(row.get("sub_state", "")),
        )
        for row in (unit_rows if isinstance(unit_rows, list) else [])
        if isinstance(row, dict)
    )
```

and pass `units=units` to the `AgentView(...)` it returns. Append:

```python
def find_unit(view: AgentView, name: str) -> Unit | None:
    return next((unit for unit in view.units if unit.name == name), None)
```

- [ ] **Step 4: Branch the gate on the target in `src/rail/gates/evidence.py`**

Change `from pathlib import Path` to `from pathlib import Path, PurePosixPath`, and add
`DeployTarget` to the `from rail.model import (…)` block. In `visible`, right after the
`if view.status != "up": …` return, insert:

```python
    shape = decl.cfg.deploy if decl.cfg is not None else None
    if (
        shape is not None
        and shape.target is DeployTarget.PRIVATE_SYSTEMD
        and shape.unit is not None
    ):
        return _unit_visible(view, agent, PurePosixPath(shape.unit).name)
```

Add before `def drill`:

```python
def _unit_visible(view: monitor.AgentView, agent: str, unit: str) -> GateResult:
    """A systemd target runs no container: red-monitor's view of the unit is the observation
    (spec 2026-09-24-private-systemd-target, decision 11). The digest was proven at deployment
    by `/version`; this proves the service stayed up."""
    found = monitor.find_unit(view, unit)
    if found is None:
        return GateResult(
            Stage.OBSERVE, "visible", False, f"red-monitor lists no unit {unit} on agent {agent}"
        )
    state = f"{found.active_state}/{found.sub_state}"
    if state != "active/running":
        return GateResult(
            Stage.OBSERVE, "visible", False, f"unit {unit} on agent {agent} is {state}"
        )
    return GateResult(
        Stage.OBSERVE,
        "visible",
        True,
        f"unit {unit} active/running on {agent}; its digest was verified at deployment by "
        "/version",
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_monitor.py tests/test_gates_evidence.py`
Expected: PASS, and every existing `visible` test still passes: compose targets are untouched.
Then run `uv run pytest -q` and ruff. Expected: all pass, lint clean.

- [ ] **Step 6: Commit**

```bash
git add src/rail/monitor.py src/rail/gates/evidence.py tests/test_monitor.py tests/test_gates_evidence.py
git commit  # ✨ feat(observe): on private-systemd, observe.visible reads the unit
```

---

### Task 7: The docs name the new shape, and the branch proves itself

**Files:**
- Modify: `CLAUDE.md` (the `src/rail/deploy/` and `src/rail/monitor.py` bullets)
- Modify: `skills/rail-deploy/SKILL.md`

- [ ] **Step 1: Update `CLAUDE.md`**

Replace the `src/rail/deploy/` bullet, from `` - `src/rail/deploy/` — `compose.py` `` down to
`— ADR-0004).`, with:

```markdown
- `src/rail/deploy/` — `remote.py` (what every target reached over ssh does identically:
  one remote script under the target's lock, the timeout that releases it, a file read at
  the released commit, the verification — the healthcheck, then `/version` — and, behind a
  `deploy.site`, the address from this host's `~/.config/red-rail/sites.yaml` (`sites.py`, a
  private file): the manifest and every attestation carry the site's name, never the
  address), `compose.py` (what every compose target adds: the `releases/<version>` layout
  and `current` symlink, the compose file read at the released commit, the digest-pinned
  pull; a target supplies only the `.env` it writes), `vps_traefik.py` (Traefik's routing
  and the public route), `private_compose.py` (a machine with no public route:
  `deploy.bind_address` with no default, and a refusal, before the first ssh, of a released
  compose file that would publish outside that address, `network_mode: host` included,
  because Docker bypasses the firewall), `private_systemd.py` (a binary that systemd runs:
  copied out of the released image by digest; the project's unit read at the released commit
  and refused before the first ssh when it would run as root, unbounded or outside the
  release; systemd loads it through a link to `current`; two fixed sudo commands — spec
  2026-09-24-private-systemd-target) and `flow.py` (forward / rollback / drill, the target
  chosen from the manifest, and the attestation sequences they write — ADR-0004).
```

At the end of the `src/rail/monitor.py` bullet, append: `` On `private-systemd`,
`observe.visible` reads the unit's state instead of a container. ``

- [ ] **Step 2: Update `skills/rail-deploy/SKILL.md`**

After the paragraph that ends with `` `rail check observe` needs. ``, add:

```markdown
A `private-systemd` target (`deploy.unit`, `deploy.binary`) delivers a binary that systemd
runs. The release is the usual image: the target copies the binary out of it by digest. The
host needs two things, once: `/etc/systemd/system/<unit>` linked to
`/opt/<project>/current/<unit>`, and a sudoers rule allowing exactly
`systemctl daemon-reload` and `systemctl restart <unit>` to the deploy account (red-watcher
installs it). `--plan` shows the restart in the ssh step.
```

- [ ] **Step 3: Verify nothing still names the retired target outside history**

Run: `git grep -n "pc-server-systemd" -- src tests copier.yml template CLAUDE.md skills`
Expected: only the test added in Task 1 (`test_the_retired_target_name_no_longer_loads`) and
the replaced private-compose test that loads the old name on purpose.

Run: `grep -n "DeployTarget\." src/rail/deploy/flow.py`
Expected: only the three lines inside `implementations()`. The flows never branch on the
target (spec success criterion 5).

- [ ] **Step 4: Run the whole proof**

Run: `make ci`
Expected: exit code 0. `ruff` is clean, the pytest summary line reports every test passing,
and `rail check` prints `passed 18/18`. Read the summary line; do not infer it.
Then run `uv run pytest -q tests/test_no_machine_address.py`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md skills/rail-deploy/SKILL.md
git commit  # 📝 docs: remote.py and private-systemd in CLAUDE.md and the rail-deploy skill
```

---

## After the last task (not part of the task gates)

1. The whole-branch review of the `spec` method (`red-review`), then the fixes.
2. PR on `hawkixs/red-rail`, then `rail reviewer once --repository hawkixs/red-rail --pr <n>`.
   Merge on the operator's approval, then reinstall the global rail (snippet `efdec356`).
3. Spec success criterion 7, live on the private service host. It waits for red-monitor
   (ticket `f91396ef`: root Dockerfile, `/version`, versioned unit, manifest) and red-watcher
   (ticket `42fb2260`: sudoers, migration). Then `rail deploy --plan`, the unit link, `rail deploy`,
   `rail check observe`, and a second release before `rail drill`.
