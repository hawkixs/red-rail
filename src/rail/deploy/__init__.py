"""Deployment targets (spec §5, §6 steps 7–8): a released artefact becomes the live service,
and the previous one comes back on a rollback. A target runs from the host with the
operator's ssh — one bash script on stdin per phase, under a lock on the target — and
verifies the result from outside, through the public route. CI never deploys (spec §5
rule 3). The vocabulary the flows write is documented in `rail.ledger`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA = re.compile(r"[0-9a-f]{7,64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_IMAGE = re.compile(
    r"[a-z0-9]([a-z0-9._-]*[a-z0-9])?(/[a-z0-9]([a-z0-9._-]*[a-z0-9])?)*@sha256:[0-9a-f]{64}"
)


class DeployError(Exception):
    """The target refused, a step failed or the verification failed; the message says which."""


class Locked(DeployError):
    """Another deployment holds the target's lock."""


@dataclass(frozen=True, slots=True)
class Artefact:
    version: str
    sha: str
    digest: str  # sha256:… — the manifest digest the registry gave at the push
    image: str  # <repository>@<digest>

    def __post_init__(self) -> None:
        # the fields reach a bash script and an .env file on the target: only the characters
        # a version, a commit, a digest and an image reference are made of (pre-review of PR #6)
        for name, pattern in (
            ("version", _VERSION),
            ("sha", _SHA),
            ("digest", _DIGEST),
            ("image", _IMAGE),
        ):
            value = getattr(self, name)
            if not pattern.fullmatch(value):
                raise DeployError(f"artefact {name} {value!r} is not safe for the target")

    @classmethod
    def from_release(cls, data: dict[str, object]) -> Artefact:
        missing = [k for k in ("version", "sha", "digest", "image") if not data.get(k)]
        if missing:
            raise DeployError(f"attestation misses {', '.join(missing)}")
        return cls(str(data["version"]), str(data["sha"]), str(data["digest"]), str(data["image"]))


@dataclass(frozen=True, slots=True)
class Step:
    """One step of a `--plan`: what runs, on what, with which stdin."""

    title: str
    argv: tuple[str, ...]
    stdin: str | None = None


@dataclass(frozen=True, slots=True)
class LiveVersion:
    project: str
    version: str
    git_sha: str
    image_digest: str


def domain_of(healthcheck: str) -> str:
    host = urlparse(healthcheck).hostname
    if not host:
        raise DeployError(f"deploy.healthcheck has no host: {healthcheck}")
    return host
