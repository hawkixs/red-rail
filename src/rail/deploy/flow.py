"""The flows on top of a target — forward, rollback, drill — and their attestations. Every
state change of the target is followed by its record, in the order of the vocabulary in
`rail.ledger`; the newest `deployed` always names the live digest. A brain refusal never
stops a flow: the waiting receipts and their replay commands are reported together at the
end."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from rail.deploy import Artefact, DeployError, LiveVersion, Locked, Step
from rail.ledger import (
    AttestationKind,
    Ledger,
    LedgerError,
    Record,
    Unattested,
    idempotency_key_for,
)
from rail.model import DeployTarget, RailConfig

# a `reason` is capped only here, AFTER redaction — capping first (the previous shape, in
# three places) could cut a matched address in half and leave a fragment the redaction below
# never sees (review finding)
MAX_REASON_LENGTH = 1000


class Target(Protocol):
    domain: str

    def steps(self, artefact: Artefact) -> list[Step]: ...
    def apply(self, artefact: Artefact) -> LiveVersion: ...
    def redact(self, text: str) -> str: ...


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


def _redacted(value: Any, redact: Callable[[str], str]) -> Any:
    """Every string of an attestation payload, as the target allows it to be recorded."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _redacted(item, redact) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redacted(item, redact) for item in value]
    return value


def _head(text: str) -> str:
    """The first `MAX_REASON_LENGTH` characters of `text`. When the cut actually removes a
    suffix, the partial last token — everything from the last whitespace of what is kept
    onward — goes with it: an address contains no whitespace, so it can never survive split
    in half at the end of what remains (review finding)."""
    if len(text) <= MAX_REASON_LENGTH:
        return text
    head = text[:MAX_REASON_LENGTH]
    if text[MAX_REASON_LENGTH].isspace():
        return head.rstrip()
    for index in range(len(head) - 1, -1, -1):
        if head[index].isspace():
            return head[:index].rstrip()
    return ""  # the whole visible window is one token: no boundary to cut at safely


@dataclass
class Attester:
    ledger: Ledger
    project: str
    target: str
    issuer: str
    # a private target behind a site replaces its address with the site's name: every string
    # of every record passes here before the key, the mirror and the ledger see it. No
    # default: a construction that forgets this argument must fail to build, not record the
    # address silently (review finding)
    redact: Callable[[str], str]
    records: list[Record] = field(default_factory=list)
    unattested: list[Unattested] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)  # a refusal that left no mirror
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
        payload = _redacted({"target": self.target, **data}, self.redact)
        reason = payload.get("reason")
        if isinstance(reason, str):
            payload["reason"] = _head(reason)
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
        except LedgerError as exc:
            self.failures.append(f"{kind.value}: {exc}")


@dataclass(frozen=True, slots=True)
class Outcome:
    live: LiveVersion | None
    records: tuple[Record, ...]
    unattested: tuple[Unattested, ...]
    failed: str | None = None  # the forward deployment failed and was rolled back
    recovery_seconds: int | None = None
    ledger_failures: tuple[str, ...] = ()  # refusals that left no mirror (exit 2, like unattested)

    def to_dict(self) -> dict[str, Any]:
        return {
            "live": None if self.live is None else asdict(self.live),
            "records": [r.model_dump(mode="json") for r in self.records],
            "unattested": [str(u.receipt) for u in self.unattested],
            "ledger_failures": list(self.ledger_failures),
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


def build_or_refuse(target: Target, *artefacts: Artefact) -> None:
    """Build the remote step of every artefact a flow is about to apply, before its first
    side effect. A target refuses what it will not ship (a unit, a compose file) while it
    builds that step, so the refusal propagates from here as a plain `DeployError`: no
    attestation, no incident, no rollback, nothing on the machine (spec
    2026-09-24-private-systemd-target, success criterion 2). Caught inside a flow, the same
    refusal would be taken for a failed deployment and answered with a rollback over ssh."""
    for artefact in artefacts:
        target.steps(artefact)


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
    attester = Attester(ledger, cfg.project, cfg.deploy.target.value, issuer, redact=target.redact)
    artefact = newest_release(ledger, cfg.project, version)
    previous = previous_artefact(ledger, cfg.project, artefact.digest)
    # the previous artefact is not built here: it is applied only if this one fails, and a
    # refusal of it then fails that rollback, before its ssh, without blocking a deployment
    # that may be the fix
    build_or_refuse(target, artefact)
    try:
        live = target.apply(artefact)
    except Locked:
        raise
    except DeployError as exc:
        # spec §7: never a half-deployed state — the previous artefact comes back and the
        # change counts as failed: incident, rollback, the live digest, the recovery
        started = clock()
        reason = str(exc)  # the whole text: the Attester redacts it, then caps it
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
        # a `rolled_back` record means a completed rollback (ledger vocabulary, ADR-0004):
        # without a previous artefact, or when putting it back fails, the incident stays open
        # and nothing else is claimed (pre-review of PR #6)
        if previous is None:
            failure += " — nothing to roll back to"
            return Outcome(
                None,
                tuple(attester.records),
                tuple(attester.unattested),
                ledger_failures=tuple(attester.failures),
                failed=failure,
            )
        try:
            target.apply(previous)
        except Locked as lock:
            # the lock holder decides what is live; the incident is already in the ledger
            raise Locked(
                f"{lock} — while rolling back {artefact.version} after: {reason}"
            ) from lock
        except DeployError as back:
            failure += f" — and the rollback to {previous.version} failed too: {back}"
            return Outcome(
                None,
                tuple(attester.records),
                tuple(attester.unattested),
                ledger_failures=tuple(attester.failures),
                failed=failure,
            )
        failure += f" — rolled back to {previous.version}"
        attester.attest(
            AttestationKind.ROLLED_BACK,
            {
                "drill": False,
                "automatic": True,
                "from_digest": artefact.digest,
                "to_digest": previous.digest,
                "version": artefact.version,
                "reason": reason,
            },
        )
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
        return Outcome(
            None,
            tuple(attester.records),
            tuple(attester.unattested),
            ledger_failures=tuple(attester.failures),
            failed=failure,
        )
    attester.attest(
        AttestationKind.DEPLOYED,
        deployed_data(artefact, mode="release", domain=target.domain, previous=previous),
    )
    return Outcome(
        live,
        tuple(attester.records),
        tuple(attester.unattested),
        ledger_failures=tuple(attester.failures),
    )


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
    attester = Attester(ledger, cfg.project, cfg.deploy.target.value, issuer, redact=target.redact)
    live = live_artefact(ledger, cfg.project)
    previous = previous_artefact(ledger, cfg.project, live.digest) if live else None
    if live is None or previous is None:
        raise DeployError("nothing to roll back to: the ledger names no earlier deployed artefact")
    build_or_refuse(target, previous)
    started = clock()
    try:
        restored = target.apply(previous)
    except Locked:
        raise
    except DeployError as exc:
        # the previous artefact could not be put back: the live state is in doubt, and the
        # ledger says so (pre-review of PR #6)
        attester.attest(
            AttestationKind.INCIDENT_DETECTED,
            {
                "drill": False,
                "automatic": False,
                "digest": live.digest,
                "version": live.version,
                "reason": f"rollback to {previous.version} failed: {exc}",
            },
        )
        return Outcome(
            None,
            tuple(attester.records),
            tuple(attester.unattested),
            ledger_failures=tuple(attester.failures),
            failed=f"rollback to {previous.version} failed: {exc} — run `rail check observe`",
        )
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
        restored,
        tuple(attester.records),
        tuple(attester.unattested),
        ledger_failures=tuple(attester.failures),
        recovery_seconds=seconds,
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
    attester = Attester(ledger, cfg.project, cfg.deploy.target.value, issuer, redact=target.redact)
    live = live_artefact(ledger, cfg.project)
    previous = previous_artefact(ledger, cfg.project, live.digest) if live else None
    if live is None or previous is None:
        raise DeployError("a drill needs two deployed artefacts: deploy a second release first")
    build_or_refuse(target, previous, live)  # the rollback, then the roll-forward
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
    try:
        target.apply(previous)
    except Locked:
        raise
    except DeployError as exc:
        # the drill's rollback failed: the service may be down on either artefact — that is
        # a real incident now, named in the ledger (independent reviewer, PR #6)
        attester.attest(
            AttestationKind.INCIDENT_DETECTED,
            {
                "drill": False,
                "automatic": True,
                "digest": live.digest,
                "version": live.version,
                "reason": f"drill: rollback to {previous.version} failed: {exc}",
            },
        )
        return Outcome(
            None,
            tuple(attester.records),
            tuple(attester.unattested),
            ledger_failures=tuple(attester.failures),
            failed=f"drill aborted: rollback to {previous.version} failed: {exc} — run "
            "`rail check observe`, then `rail deploy` or `rail deploy --rollback`",
        )
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
            ledger_failures=tuple(attester.failures),
            failed=f"roll-forward to {live.version} failed: {exc}",
            recovery_seconds=seconds,
        )
    attester.attest(
        AttestationKind.DEPLOYED,
        deployed_data(live, mode="drill", domain=target.domain, previous=previous),
    )
    return Outcome(
        forward_live,
        tuple(attester.records),
        tuple(attester.unattested),
        ledger_failures=tuple(attester.failures),
        recovery_seconds=seconds,
    )
