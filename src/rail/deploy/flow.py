"""The flows on top of a target — forward, rollback, drill — and their attestations. Every
state change of the target is followed by its record, in the order of the vocabulary in
`rail.ledger`; the newest `deployed` always names the live digest. A brain refusal after a
mirror never stops a flow: the replay commands are reported together at the end."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from rail.deploy import Artefact, DeployError, LiveVersion, Locked, Step
from rail.ledger import AttestationKind, Ledger, Record, Unattested, idempotency_key_for
from rail.model import DeployTarget, RailConfig


class Target(Protocol):
    domain: str

    def steps(self, artefact: Artefact) -> list[Step]: ...
    def apply(self, artefact: Artefact) -> LiveVersion: ...


def make_target(repo: Path, cfg: RailConfig, **kwargs: Any) -> Target:
    if cfg.deploy is None or cfg.deploy.target is not DeployTarget.VPS_TRAEFIK:
        name = cfg.deploy.target.value if cfg.deploy else "none"
        raise DeployError(
            f"deploy target {name} is not implemented in this rail (vps-traefik only)"
        )
    from rail.deploy.vps_traefik import VpsTraefik

    return VpsTraefik(repo, cfg, **kwargs)


@dataclass
class Attester:
    ledger: Ledger
    project: str
    target: str
    issuer: str
    records: list[Record] = field(default_factory=list)
    unattested: list[Unattested] = field(default_factory=list)
    _floor: datetime | None = field(default=None, init=False, repr=False)

    def _emitted_at(self) -> datetime:
        """Strictly increasing, second-resolution timestamps. `idempotency_key_for` truncates
        emission to the second; a flow can write several records for the same digest (a
        rollback redeploys the previous one) faster than the clock ticks, which would collide
        on the same key. Walking past the ledger's newest record — and past this Attester's own
        previous call — keeps every key distinct without touching the ledger's key scheme."""
        if self._floor is None:
            newest = max((r.recorded_at for r in self.ledger.list(self.project)), default=None)
            self._floor = newest + timedelta(seconds=1) if newest else datetime.now(UTC)
        emitted = max(datetime.now(UTC), self._floor)
        self._floor = emitted + timedelta(seconds=1)
        return emitted

    def attest(self, kind: AttestationKind, data: dict[str, Any]) -> None:
        payload = {"target": self.target, **data}
        emitted = self._emitted_at()
        try:
            self.records.append(
                self.ledger.attest(
                    self.project,
                    kind,
                    payload,
                    issuer=self.issuer,
                    idempotency_key=idempotency_key_for(kind, payload, emitted_at=emitted),
                    emitted_at=emitted,
                )
            )
        except Unattested as exc:
            self.unattested.append(exc)


@dataclass(frozen=True, slots=True)
class Outcome:
    live: LiveVersion | None
    records: tuple[Record, ...]
    unattested: tuple[Unattested, ...]
    failed: str | None = None  # the forward deployment failed and was rolled back
    recovery_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "live": None if self.live is None else asdict(self.live),
            "records": [r.model_dump(mode="json") for r in self.records],
            "unattested": [str(u.receipt) for u in self.unattested],
            "failed": self.failed,
            "recovery_seconds": self.recovery_seconds,
        }


def newest_release(ledger: Ledger, project: str, version: str | None = None) -> Artefact:
    releases = ledger.list(project, attestation=AttestationKind.RELEASED)
    if version is not None:
        releases = [r for r in releases if r.data.get("version") == version]
    if not releases:
        wanted = f" for version {version}" if version else ""
        raise DeployError(f"no released attestation{wanted}: run `rail release` first")
    return Artefact.from_release(releases[-1].data)


def live_artefact(ledger: Ledger, project: str) -> Artefact | None:
    """What the newest `deployed` says is live, whatever its mode."""
    deployed = ledger.list(project, attestation=AttestationKind.DEPLOYED)
    return Artefact.from_release(deployed[-1].data) if deployed else None


def previous_artefact(ledger: Ledger, project: str, other_than: str) -> Artefact | None:
    """The newest deployed artefact whose digest is not `other_than`."""
    for record in reversed(ledger.list(project, attestation=AttestationKind.DEPLOYED)):
        if record.data.get("digest") and record.data["digest"] != other_than:
            return Artefact.from_release(record.data)
    return None


def deployed_data(
    artefact: Artefact, *, mode: str, domain: str, previous: Artefact | None
) -> dict[str, Any]:
    return {
        "mode": mode,
        "version": artefact.version,
        "sha": artefact.sha,
        "digest": artefact.digest,
        "image": artefact.image,
        "domain": domain,
        "previous_digest": previous.digest if previous else "",
    }


def forward(
    repo: Path,
    cfg: RailConfig,
    ledger: Ledger,
    *,
    version: str | None = None,
    issuer: str = "operator",
    target: Target | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    assert cfg.deploy is not None
    target = target or make_target(repo, cfg)
    attester = Attester(ledger, cfg.project, cfg.deploy.target.value, issuer)
    artefact = newest_release(ledger, cfg.project, version)
    previous = previous_artefact(ledger, cfg.project, artefact.digest)
    try:
        live = target.apply(artefact)
    except Locked:
        raise
    except DeployError as exc:
        # spec §7: never a half-deployed state — the previous artefact comes back and the
        # change counts as failed: incident, rollback, the live digest, the recovery
        started = clock()
        reason = str(exc)[:1000]
        attester.attest(
            AttestationKind.INCIDENT_DETECTED,
            {
                "drill": False,
                "automatic": True,
                "digest": artefact.digest,
                "version": artefact.version,
                "reason": reason,
            },
        )
        failure = f"{artefact.version} failed: {reason}"
        restored = False
        if previous is not None:
            try:
                target.apply(previous)
                restored = True
                failure += f" — rolled back to {previous.version}"
            except DeployError as back:
                failure += f" — and the rollback to {previous.version} failed too: {back}"
        else:
            failure += " — nothing to roll back to"
        attester.attest(
            AttestationKind.ROLLED_BACK,
            {
                "drill": False,
                "automatic": True,
                "from_digest": artefact.digest,
                "to_digest": previous.digest if previous else "",
                "version": artefact.version,
                "reason": reason,
            },
        )
        if restored and previous is not None:
            attester.attest(
                AttestationKind.DEPLOYED,
                deployed_data(previous, mode="rollback", domain=target.domain, previous=artefact),
            )
            attester.attest(
                AttestationKind.RESTORED,
                {
                    "drill": False,
                    "digest": previous.digest,
                    "recovery_seconds": int(clock() - started),
                },
            )
        return Outcome(None, tuple(attester.records), tuple(attester.unattested), failed=failure)
    attester.attest(
        AttestationKind.DEPLOYED,
        deployed_data(artefact, mode="release", domain=target.domain, previous=previous),
    )
    return Outcome(live, tuple(attester.records), tuple(attester.unattested))


def rollback(
    repo: Path,
    cfg: RailConfig,
    ledger: Ledger,
    *,
    issuer: str = "operator",
    target: Target | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    assert cfg.deploy is not None
    target = target or make_target(repo, cfg)
    attester = Attester(ledger, cfg.project, cfg.deploy.target.value, issuer)
    live = live_artefact(ledger, cfg.project)
    previous = previous_artefact(ledger, cfg.project, live.digest) if live else None
    if live is None or previous is None:
        raise DeployError("nothing to roll back to: the ledger names no earlier deployed artefact")
    started = clock()
    restored = target.apply(previous)
    attester.attest(
        AttestationKind.ROLLED_BACK,
        {
            "drill": False,
            "automatic": False,
            "from_digest": live.digest,
            "to_digest": previous.digest,
            "version": previous.version,
            "reason": "operator",
        },
    )
    attester.attest(
        AttestationKind.DEPLOYED,
        deployed_data(previous, mode="rollback", domain=target.domain, previous=live),
    )
    seconds = int(clock() - started)
    attester.attest(
        AttestationKind.RESTORED,
        {"drill": False, "digest": previous.digest, "recovery_seconds": seconds},
    )
    return Outcome(
        restored, tuple(attester.records), tuple(attester.unattested), recovery_seconds=seconds
    )


def drill(
    repo: Path,
    cfg: RailConfig,
    ledger: Ledger,
    *,
    issuer: str = "operator",
    target: Target | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    """Simulate an incident on the live artefact, roll back to the previous one, measure the
    recovery, roll forward. Every record is marked `drill`; the roll-forward is a `deployed`
    in mode `drill` so the ledger keeps naming the live digest."""
    assert cfg.deploy is not None
    target = target or make_target(repo, cfg)
    attester = Attester(ledger, cfg.project, cfg.deploy.target.value, issuer)
    live = live_artefact(ledger, cfg.project)
    previous = previous_artefact(ledger, cfg.project, live.digest) if live else None
    if live is None or previous is None:
        raise DeployError("a drill needs two deployed artefacts: deploy a second release first")
    started = clock()
    attester.attest(
        AttestationKind.INCIDENT_DETECTED,
        {
            "drill": True,
            "automatic": False,
            "digest": live.digest,
            "version": live.version,
            "reason": "drill",
        },
    )
    target.apply(previous)
    attester.attest(
        AttestationKind.ROLLED_BACK,
        {
            "drill": True,
            "automatic": False,
            "from_digest": live.digest,
            "to_digest": previous.digest,
            "version": previous.version,
            "reason": "drill",
        },
    )
    seconds = int(clock() - started)
    attester.attest(
        AttestationKind.RESTORED,
        {"drill": True, "digest": previous.digest, "recovery_seconds": seconds},
    )
    try:
        forward_live = target.apply(live)
    except DeployError as exc:
        # the drill leaves the previous artefact live: say so in the ledger
        attester.attest(
            AttestationKind.DEPLOYED,
            deployed_data(previous, mode="rollback", domain=target.domain, previous=live),
        )
        return Outcome(
            None,
            tuple(attester.records),
            tuple(attester.unattested),
            failed=f"roll-forward to {live.version} failed: {exc}",
            recovery_seconds=seconds,
        )
    attester.attest(
        AttestationKind.DEPLOYED,
        deployed_data(live, mode="drill", domain=target.domain, previous=previous),
    )
    return Outcome(
        forward_live, tuple(attester.records), tuple(attester.unattested), recovery_seconds=seconds
    )
