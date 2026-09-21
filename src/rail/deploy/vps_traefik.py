"""Target `vps-traefik`: the border VPS, Traefik v2 docker provider (`exposedByDefault=false`),
external network and certresolver from the policy, one stack per project under
`<stack_root>/<project>` — `releases/<version>/{compose.yaml,.env}` and a `current` symlink,
the layout red-gift already uses. The compose file is the project's `deploy/compose.yaml`
**at the released commit**; only the `.env` changes between releases.

Everything this shape shares with any other compose deployment lives in `deploy/compose.py`.
What is left here is what makes it *this* shape: Traefik's routing in the `.env`, and a
verification that goes through the public route.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rail.deploy import Artefact, domain_of
from rail.deploy.compose import (  # re-exported: the names this module has always published
    LOCKED,
    POLL_SECONDS,
    ComposeTarget,
    Runner,
    common_env,
    env_lines,
    remote_script,
    ssh_argv,
)
from rail.model import RailConfig
from rail.policy import parameter

__all__ = [
    "LOCKED",
    "POLL_SECONDS",
    "Parameters",
    "Runner",
    "VpsTraefik",
    "env_file",
    "remote_script",
    "ssh_argv",
]


@dataclass(frozen=True, slots=True)
class Parameters:
    """The shared parameters plus Traefik's two, read together so one call covers the shape."""

    ssh_host: str
    stack_root: str
    traefik_network: str
    cert_resolver: str
    healthcheck_timeout: int
    compose_path: str
    remote_timeout: int

    @classmethod
    def read(cls, repo: Path) -> Parameters:
        return cls(
            ssh_host=str(parameter(repo, "deploy.ssh_host")),
            stack_root=str(parameter(repo, "deploy.stack_root")),
            traefik_network=str(parameter(repo, "deploy.traefik_network")),
            cert_resolver=str(parameter(repo, "deploy.cert_resolver")),
            healthcheck_timeout=int(parameter(repo, "deploy.healthcheck_timeout_seconds")),
            compose_path=str(parameter(repo, "deploy.compose_path")),
            remote_timeout=int(parameter(repo, "deploy.remote_timeout_seconds")),
        )


def env_file(project: str, artefact: Artefact, domain: str, params: Parameters) -> str:
    return env_lines(
        common_env(project, artefact)
        + (
            ("DOMAIN", domain),
            ("TRAEFIK_NETWORK", params.traefik_network),
            ("TRAEFIK_CERT_RESOLVER", params.cert_resolver),
        )
    )


class VpsTraefik(ComposeTarget):
    def __init__(self, repo: Path, cfg: RailConfig, **kwargs: Any) -> None:
        super().__init__(repo, cfg, **kwargs)
        self.params = Parameters.read(repo)  # type: ignore[assignment]  # the Traefik superset
        self.domain = domain_of(self.healthcheck)

    @property
    def origin(self) -> str:
        return f"https://{self.domain}"

    def env_file(self, artefact: Artefact) -> str:
        return env_file(self.cfg.project, artefact, self.domain, self.params)
