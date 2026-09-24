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
    lock_preamble,
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
    release = f"{root}/releases/{version}"
    compose = f'docker compose --project-name {project} --project-directory "$release"'
    return "\n".join(
        [
            *lock_preamble(root, release),
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
