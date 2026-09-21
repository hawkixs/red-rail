"""Target `private-compose`: a machine with no public route — red-base today, the home server
tomorrow. Same layout, same lock, same digest-pinned artefact as `vps-traefik`; what changes is
that there is no reverse proxy to configure and no public domain to ask.

The verification keeps the scheme, host and port of the declared `deploy.healthcheck`, so a
service reachable only over WireGuard is checked over WireGuard.

And one property this shape must hold that the border VPS never needed: **Docker bypasses the
firewall**. A `-p 8080:8080` publishes on every interface whatever ufw says. The machine
defends itself — the daemon is pinned to its private address, `DOCKER-USER` drops on the public
interface — but both defences live in its configuration, where the rail cannot see them and a
later change can remove them without a trace. So the target reads the compose file it is about
to deploy and refuses one that would publish where the firewall is not the thing standing in
the way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from rail.deploy import Artefact, DeployError, domain_of
from rail.deploy.compose import ComposeTarget, common_env, env_lines
from rail.model import RailConfig
from rail.policy import parameter

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})

# The rail writes BIND_ADDRESS into the `.env` it generates, so a compose file may publish on
# `${BIND_ADDRESS}` instead of repeating the machine's address: one source of truth, and no
# machine address committed to the repository. Safe because the value is the rail's to write,
# not the project's — and matched exactly, so `${BIND_ADDRESS:-0.0.0.0}` is a different string
# and stays refused, as does any other variable the rail does not control.
BIND_VARIABLE = frozenset({"${BIND_ADDRESS}", "$BIND_ADDRESS"})


def _host_of(mapping: str) -> str | None:
    """The host address of a short-form `ports:` entry, or None when it names none.

    Compose short form is `[HOST_IP:][HOST:]CONTAINER[/PROTO]`, and an IPv6 host address is
    bracketed. Only an entry with three colon-separated parts (or a bracketed address) names a
    host address at all: `"8080:8080"` is host:container and publishes on every interface.
    """
    entry = mapping.split("/", 1)[0].strip()
    if entry.startswith("["):
        closing = entry.find("]")
        return entry[1:closing] if closing > 0 else None
    parts = entry.split(":")
    return parts[0] if len(parts) >= 3 else None


def published_ports_are_private(compose_text: str, bind_address: str) -> list[tuple[str, str]]:
    """The `(service, entry)` pairs that would publish outside `bind_address` and loopback.

    A parse error is refused rather than assumed safe: a check that fails open on malformed
    input is decorative. `expose:` is not publishing, and a service without `ports:` is fine.
    """
    try:
        document = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise DeployError(f"the released compose file does not parse: {exc}") from exc
    if document is None:
        return []
    if not isinstance(document, dict):
        raise DeployError("the released compose file is not a mapping")
    services = document.get("services") or {}
    if not isinstance(services, dict):
        raise DeployError("the released compose file declares no `services:` mapping")

    allowed = LOOPBACK | BIND_VARIABLE | {bind_address}
    offenders: list[tuple[str, str]] = []
    for name, service in services.items():
        if not isinstance(service, dict):
            continue

        # Host networking is the way to publish that never touches `ports:`: the container
        # shares the host's network namespace and binds every interface directly, outside
        # Docker's NAT. A guard reading only `ports:` is bypassed by it completely.
        if str(service.get("network_mode", "")) == "host":
            offenders.append((str(name), "network_mode: host"))
            continue

        ports = service.get("ports") or []
        if isinstance(ports, str) or not isinstance(ports, list):
            raise DeployError(
                f"service {name}: `ports:` must be a list, not {type(ports).__name__} — "
                "a scalar would be inspected character by character and find nothing"
            )
        for entry in ports:
            if isinstance(entry, dict):
                if "published" not in entry:
                    # not "nothing is published": Docker picks an ephemeral host port and
                    # publishes it on every interface (measured: 0.0.0.0:32768->8080/tcp)
                    offenders.append((str(name), f"target {entry.get('target')}, no published"))
                elif str(entry.get("host_ip", "")) not in allowed:
                    offenders.append((str(name), str(entry["published"])))
                continue
            text = str(entry)
            host = _host_of(text)
            if host is None or host not in allowed:
                offenders.append((str(name), text))
    return offenders


@dataclass(frozen=True, slots=True)
class Parameters:
    bind_address: str

    @classmethod
    def read(cls, repo: Path) -> Parameters:
        value = parameter(repo, "deploy.bind_address")
        if not value:
            raise DeployError(
                "deploy.bind_address is not set: a private target must say which address it "
                "publishes on, so declare it in rail.yaml under `gates:` with its reason"
            )
        return cls(bind_address=str(value))


class PrivateCompose(ComposeTarget):
    def __init__(self, repo: Path, cfg: RailConfig, **kwargs: Any) -> None:
        super().__init__(repo, cfg, **kwargs)
        self.private = Parameters.read(repo)
        # `Field(pattern=r"^https?://")` is match-at-start, so `https://` passes validation:
        # the host has to be checked here, exactly as `vps-traefik` checks it. Without this
        # the failure surfaces only after the healthcheck timeout, once ssh has already
        # changed the machine, and an empty domain reaches the attestation.
        self.domain = domain_of(self.healthcheck)

    @property
    def origin(self) -> str:
        split = urlsplit(self.healthcheck)
        return f"{split.scheme}://{split.netloc}"  # netloc, not hostname: the port matters

    def env_file(self, artefact: Artefact) -> str:
        return env_lines(
            common_env(self.cfg.project, artefact) + (("BIND_ADDRESS", self.private.bind_address),)
        )

    def precheck(self, compose_text: str) -> None:
        offenders = published_ports_are_private(compose_text, self.private.bind_address)
        if offenders:
            listed = ", ".join(f"{service} → {entry}" for service, entry in offenders)
            raise DeployError(
                f"the released compose file publishes outside {self.private.bind_address} "
                f"and loopback: {listed}. Docker bypasses the firewall, so a published port's "
                f"host address must EQUAL {self.private.bind_address} (or ${{BIND_ADDRESS}}, "
                "which the rail writes), not merely be present — 0.0.0.0 names an address and "
                "publishes everywhere"
            )
