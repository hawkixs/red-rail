"""The ledger protocol: where evidence is authoritative (ADR-0001, ADR-0002).

Two backends implement the same protocol. `FileLedger` (default): the repository's
`docs/receipts/*.json` are the ledger — append-only, committed, no network. `BrainLedger`
(phase 2): brain-v42 is the shared authority and the receipts become mirrors.

Every record carries a digest (sha256 over its canonical JSON) and an idempotency key:
replaying a write with the same key and the same content returns the existing record; the
same key with different content is a conflict, never a silent overwrite.

Attestation payload conventions read by the gates and by `rail metrics`:
  sha          git commit the evidence is about (review_verdict, integrated, released, deployed)
  independent  bool — review_verdict only; a pre-review from the producing session is False
  verdict      "approve" | "request_changes" — review_verdict only
  version      released; digest — released and deployed (artefact digest)
  drill        bool — rolled_back / restored produced by the rollback drill
  target       deployed / rolled_back / restored / incident_detected — the manifest's deploy target
  mode         deployed only: "release" (default — a change), "rollback" (the previous artefact
               put back), "drill" (the roll-forward closing a drill); the newest `deployed`
               record always names the live digest
  version, image, domain, previous_digest   deployed — what went live and what it replaced
  from_digest, to_digest, automatic, reason  rolled_back — the failed artefact names itself
  recovery_seconds  restored — integer seconds since the incident (drill or real)

Idempotency keys are deterministic per event (`idempotency_key_for`): facts about a commit
are keyed by the commit, an artefact by its version, a recurring event by target, digest
and emission time — a replay reuses the key written in the mirror.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

RECEIPTS_DIR = "docs/receipts"
SCHEMA_VERSION = 1


class RecordKind(StrEnum):
    CONTRACT = "contract"
    BINDING = "binding"
    ATTESTATION = "attestation"


class AttestationKind(StrEnum):
    GATE_PASSED = "gate_passed"
    REVIEW_VERDICT = "review_verdict"
    INTEGRATED = "integrated"
    RELEASED = "released"
    DEPLOYED = "deployed"
    ROLLED_BACK = "rolled_back"
    INCIDENT_DETECTED = "incident_detected"
    RESTORED = "restored"
    FULFILLED = "fulfilled"


class LedgerError(Exception):
    """The ledger cannot answer: unreadable receipt, unreachable backend."""


class LedgerUnavailable(LedgerError):
    """The configured backend is not available in this rail version."""


class IdempotencyConflict(LedgerError):
    """An idempotency key was reused with different content."""


BRAIN_DIGEST_PREFIX = b"brain-delivery-attestation:v1\n"
BRAIN_MILESTONES = frozenset({"integrated", "fulfilled"})


class Unattested(LedgerError):
    """The mirror is written, the shared ledger refused or was unreachable. Replay it."""

    def __init__(self, receipt: Path, cause: str) -> None:
        self.receipt = receipt
        self.cause = cause
        parts = receipt.stem.split("-")
        label = parts[1] if len(parts) > 1 else parts[0]
        super().__init__(
            f"attestation not recorded ({cause}); the receipt {receipt.name} is written — "
            f"replay with: rail attest {label} --from {receipt}"
        )


def _reject_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        raise ValueError(f"float at {path}: brain refuses floats (use integers or strings)")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"non-string keys at {path}: brain refuses them")
            _reject_floats(item, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _reject_floats(item, f"{path}[{index}]")


def brain_digest(data: dict[str, Any]) -> str:
    """brain-v42's payload digest (contract v1): sha256 over a domain prefix and the
    canonical JSON of the payload, rendered as bare lowercase hex."""
    if not isinstance(data, dict):
        raise ValueError("the payload is a JSON object")
    _reject_floats(data)
    canonical = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(BRAIN_DIGEST_PREFIX + canonical).hexdigest()


def idempotency_key_for(
    kind: AttestationKind, data: dict[str, Any], *, emitted_at: datetime | None = None
) -> str:
    """`<kind>:<subject>[:<occurrence>]` — deterministic per event, random only when the data
    names no subject at all (the mirror then carries the key for any replay)."""
    sha = str(data.get("sha") or "")
    if kind is AttestationKind.GATE_PASSED and sha and data.get("gate"):
        return f"gate_passed:{sha}:{data['gate']}"
    if kind is AttestationKind.REVIEW_VERDICT and sha and data.get("check_run_id") is not None:
        return f"review_verdict:{sha}:{data['check_run_id']}"
    if kind in (AttestationKind.INTEGRATED, AttestationKind.FULFILLED) and sha:
        return f"{kind.value}:{sha}"
    if kind is AttestationKind.RELEASED and (data.get("version") or sha):
        return f"released:{data.get('version') or sha}"
    if kind in (
        AttestationKind.DEPLOYED,
        AttestationKind.ROLLED_BACK,
        AttestationKind.RESTORED,
        AttestationKind.INCIDENT_DETECTED,
    ):
        stamp = (emitted_at or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        subject = str(data.get("digest") or sha or "-")
        return f"{kind.value}:{data.get('target') or '-'}:{subject}:{stamp}"
    return f"{kind.value}:{uuid.uuid4()}"


class RequiredCheck(BaseModel):
    """A trusted check selector as brain-v42's evaluator compares it: kind + name + the App
    that publishes it (`app_slug` or numeric `provider_id`) — no App identity is ever wired
    in code, so a third-party App's check is first-class (ticket 04bc1f4a, ADR-0001 am. 4)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["check_run", "commit_status"] = "check_run"
    name: str = Field(min_length=1, max_length=200)
    app_slug: str | None = Field(default=None, min_length=1, max_length=200)
    provider_id: int | None = Field(default=None, gt=0)

    def selector(self) -> tuple[str, str, str | None, int | None]:
        """What brain compares two required checks by (`models/delivery.py`)."""
        return (self.kind, self.name, self.app_slug, self.provider_id)

    @model_validator(mode="after")
    def _publisher_named(self) -> RequiredCheck:
        if self.app_slug is None and self.provider_id is None:
            raise ValueError("a required check names its publisher: app_slug or provider_id")
        return self


class ReviewPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_approvals: int = Field(default=1, ge=0, le=100)
    allowed_reviewers: list[str] = Field(default_factory=list, max_length=200)


class Deliverable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    repository: str = Field(min_length=3, max_length=201)  # owner/name, as brain-v42 expects
    target_branch: str = "main"
    repository_id: int | None = Field(default=None, gt=0)  # GitHub numeric id, brain binds by it
    required_checks: list[RequiredCheck] = Field(default_factory=list, max_length=100)
    no_checks_reason: str | None = Field(default=None, min_length=1, max_length=2000)
    review: ReviewPolicy = Field(default_factory=ReviewPolicy)

    @model_validator(mode="after")
    def _explicit_check_policy(self) -> Deliverable:
        """Mirrors brain-v42's method of the same name (`models/delivery.py`), BOTH halves:
        no required check is a declared exception rather than a default, and a selector may
        not be listed twice. Enforced here so a `file` ledger never stores a contract
        `brain` would refuse — the wall must not wait for the switch."""
        if not self.required_checks and self.no_checks_reason is None:
            raise ValueError("no_checks_reason is required when required_checks is empty")
        selectors = [check.selector() for check in self.required_checks]
        if len(selectors) != len(set(selectors)):
            raise ValueError("duplicate required check selector")
        return self


class Contract(BaseModel):
    """Mirrors the `contract` argument of `brain_delivery_contract_set`, so phase 2 maps 1:1."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    objective: str = Field(min_length=1, max_length=8000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=200)
    constraints: list[str] = Field(default_factory=list, max_length=200)
    priority: int = Field(default=0, ge=0, le=10000)
    acceptance_mode: Literal["automatic", "explicit"] = "explicit"  # `fulfilled` is explicit
    deliverables: list[Deliverable] = Field(min_length=1, max_length=20)


class PullRequestRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=3)
    number: int = Field(gt=0)
    head_sha: str = Field(min_length=7, max_length=64)


def canonical_json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def compute_digest(fields: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(fields)).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = SCHEMA_VERSION
    kind: RecordKind
    project: str = Field(min_length=1)
    recorded_at: datetime
    issuer: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    payload: dict[str, Any]
    digest: str

    @classmethod
    def build(
        cls,
        *,
        kind: RecordKind,
        project: str,
        issuer: str,
        idempotency_key: str,
        payload: dict[str, Any],
        recorded_at: datetime,
    ) -> Record:
        draft = cls(
            kind=kind,
            project=project,
            recorded_at=recorded_at.astimezone(UTC),
            issuer=issuer,
            idempotency_key=idempotency_key,
            payload=payload,
            digest="sha256:pending",
        )
        return draft.model_copy(update={"digest": compute_digest(draft.digest_fields())})

    def digest_fields(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"digest"})

    def verify(self) -> bool:
        return self.digest == compute_digest(self.digest_fields())

    @property
    def attestation(self) -> AttestationKind | None:
        if self.kind is not RecordKind.ATTESTATION:
            return None
        return AttestationKind(self.payload["kind"])

    @property
    def data(self) -> dict[str, Any]:
        """The attestation's own payload (`payload["data"]`), or the whole payload otherwise."""
        if self.kind is RecordKind.ATTESTATION:
            return dict(self.payload.get("data", {}))
        return dict(self.payload)


# What brain treats as terminal for delivery evidence — mirrored from
# `repositories/pg_delivery_evidence._TERMINAL_STATUSES`, not guessed. `resolved` is NOT
# among them: a resolved ticket still awaits the requester's confirmation. Matching `closed`
# alone left a ticket ended by `wontfix` or `acked` covering new work, which is the very
# thing the intent gate exists to refuse.
TERMINAL_TICKET_STATUSES = frozenset({"wontfix", "closed", "acked"})


class Ledger(Protocol):
    def coordination_status(self) -> str | None:
        """Brain's raw ticket status for a ledger whose contract lives on a ticket, None for
        one that has no such notion. Brain's own enum is `open`, `in_progress`, `resolved`,
        `wontfix`, `closed`, `acked`; `TERMINAL_TICKET_STATUSES` says which of them end a
        delivery. The status is returned verbatim so a caller reports what brain said rather
        than asserting a disposition it never read."""
        ...

    def contract_set(
        self, project: str, contract: Contract, *, reason: str, issuer: str, idempotency_key: str
    ) -> Record: ...

    def bind(
        self, project: str, pr: PullRequestRef, *, issuer: str, idempotency_key: str
    ) -> Record: ...

    def attest(
        self,
        project: str,
        kind: AttestationKind,
        data: dict[str, Any],
        *,
        issuer: str,
        idempotency_key: str,
        emitted_at: datetime | None = None,
    ) -> Record: ...

    def accept(
        self, project: str, *, rationale: str, issuer: str, sha: str | None = None
    ) -> Record:
        """Stage 10: the requester accepts the integrated delivery (`fulfilled`)."""
        ...

    def list(
        self,
        project: str,
        *,
        kind: RecordKind | None = None,
        attestation: AttestationKind | None = None,
    ) -> list[Record]: ...

    def get(self, project: str, digest: str) -> Record | None: ...


def bindings_of(ledger: Ledger, project: str, repository: str, number: int) -> list[Record]:
    """The bindings `rail bind` recorded for one pull request, oldest first. Both backends
    carry the pull request as `repository` and `number` in the binding's payload."""
    return [
        r
        for r in ledger.list(project, kind=RecordKind.BINDING)
        if r.payload.get("repository") == repository and int(r.payload.get("number") or 0) == number
    ]


def open_ledger(repo: Path, *, client: Any = None) -> Ledger:
    """The backend declared in `rail.yaml`. Raises like `load_rail_config` on a missing or bad
    manifest — this is a write path, and it stays fail-closed without one. `ledger: brain`
    needs the `brain` extra, the operator's token and the ticket. Gates that must still work
    without a manifest observe the default file ledger themselves
    (`FileLedger(repo / RECEIPTS_DIR)`), never through here."""
    from rail.ledger.file import FileLedger
    from rail.model import LedgerBackend, load_rail_config

    cfg = load_rail_config(repo)
    if cfg.ledger is LedgerBackend.FILE:
        return FileLedger(repo / RECEIPTS_DIR)
    import importlib.util

    # the client imports fastmcp lazily (found by the independent reviewer on PR #3): the
    # extra is checked here, so a missing dependency is a LedgerUnavailable, not a traceback
    if importlib.util.find_spec("fastmcp") is None:
        raise LedgerUnavailable("ledger 'brain' needs the extra: uv sync --extra brain")
    from rail.brain.client import BrainClient
    from rail.brain.settings import BrainSettings
    from rail.ledger.brain import BrainLedger

    if client is None:
        from rail.private import PrivateFileError

        try:
            settings = BrainSettings.from_environment()
        except PrivateFileError as exc:
            raise LedgerError(f"brain token: {exc}") from exc
        client = BrainClient.http(settings.url, token=settings.token, agent="red-rail")
    assert cfg.ticket is not None  # guaranteed by the manifest validator
    return BrainLedger(
        client, ticket=cfg.ticket, project=cfg.project, receipts_dir=repo / RECEIPTS_DIR
    )
