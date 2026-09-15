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
"""

from __future__ import annotations

import hashlib
import json
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


class RequiredCheck(BaseModel):
    """A trusted check selector as brain-v42's evaluator compares it: kind + name + the App
    that publishes it (`app_slug` or numeric `provider_id`) — no App identity is ever wired
    in code, so a third-party App's check is first-class (ticket 04bc1f4a, ADR-0001 am. 4)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["check_run", "commit_status"] = "check_run"
    name: str = Field(min_length=1, max_length=200)
    app_slug: str | None = Field(default=None, min_length=1, max_length=200)
    provider_id: int | None = Field(default=None, gt=0)

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
    required_checks: list[RequiredCheck] = Field(default_factory=list, max_length=100)
    review: ReviewPolicy = Field(default_factory=ReviewPolicy)


class Contract(BaseModel):
    """Mirrors the `contract` argument of `brain_delivery_contract_set`, so phase 2 maps 1:1."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    objective: str = Field(min_length=1, max_length=8000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=200)
    constraints: list[str] = Field(default_factory=list, max_length=200)
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


class Ledger(Protocol):
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
    ) -> Record: ...

    def list(
        self,
        project: str,
        *,
        kind: RecordKind | None = None,
        attestation: AttestationKind | None = None,
    ) -> list[Record]: ...

    def get(self, project: str, digest: str) -> Record | None: ...


def open_ledger(repo: Path) -> Ledger:
    """The backend declared in `rail.yaml`. Raises like `load_rail_config` on a bad manifest."""
    from rail.ledger.file import FileLedger
    from rail.model import LedgerBackend, load_rail_config

    cfg = load_rail_config(repo)
    if cfg.ledger is LedgerBackend.FILE:
        return FileLedger(repo / RECEIPTS_DIR)
    raise LedgerUnavailable(
        "ledger 'brain' arrives in phase 2 (ADR-0002); declare `ledger: file` for now"
    )
