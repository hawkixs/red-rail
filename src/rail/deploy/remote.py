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


def lock_preamble(root: str, release: str) -> list[str]:
    """The first lines of every remote script: strict mode, the release directory, the
    target's lock — `LOCKED` when another deployment holds it — then into the release."""
    return [
        "set -euo pipefail",
        f"root={root}",
        f"release={release}",
        'mkdir -p "$release"',
        'exec 9>"$root/.deploy.lock"',
        f'flock -n 9 || {{ echo "another deployment holds $root/.deploy.lock" >&2; '
        f"exit {LOCKED}; }}",
        'cd "$release"',
    ]


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
