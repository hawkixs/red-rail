"""`FileLedger`: the repository's receipts are the ledger.

One operator, one repository, no network.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rail.ledger import (
    AttestationKind,
    Contract,
    IdempotencyConflict,
    LedgerError,
    PullRequestRef,
    Record,
    RecordKind,
)


def receipt_filename(record: Record) -> str:
    label = record.attestation.value if record.attestation else record.kind.value
    stamp = record.recorded_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{label}-{record.digest[7:19]}.json"


def load_receipt(path: Path) -> Record:
    try:
        return Record.model_validate(json.loads(path.read_text()))
    except (OSError, ValueError, ValidationError) as exc:
        raise LedgerError(f"unreadable receipt {path.name}: {exc}") from exc


class FileLedger:
    def __init__(self, root: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self.root = root
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- protocol -------------------------------------------------------------------------

    def contract_set(
        self, project: str, contract: Contract, *, reason: str, issuer: str, idempotency_key: str
    ) -> Record:
        payload = {"contract": contract.model_dump(mode="json"), "reason": reason}
        return self._append(RecordKind.CONTRACT, project, payload, issuer, idempotency_key)

    def bind(
        self, project: str, pr: PullRequestRef, *, issuer: str, idempotency_key: str
    ) -> Record:
        return self._append(
            RecordKind.BINDING, project, pr.model_dump(mode="json"), issuer, idempotency_key
        )

    def attest(
        self,
        project: str,
        kind: AttestationKind,
        data: dict[str, Any],
        *,
        issuer: str,
        idempotency_key: str,
    ) -> Record:
        payload = {"kind": kind.value, "data": data}
        return self._append(RecordKind.ATTESTATION, project, payload, issuer, idempotency_key)

    def list(
        self,
        project: str,
        *,
        kind: RecordKind | None = None,
        attestation: AttestationKind | None = None,
    ) -> list[Record]:
        return [
            r
            for r in self._records()
            if r.project == project
            and (kind is None or r.kind is kind)
            and (attestation is None or r.attestation is attestation)
        ]

    def get(self, project: str, digest: str) -> Record | None:
        return next(
            (r for r in self._records() if r.project == project and r.digest == digest), None
        )

    # -- internals ------------------------------------------------------------------------

    def _records(self) -> list[Record]:
        if not self.root.is_dir():
            return []
        records = [load_receipt(p) for p in sorted(self.root.glob("*.json"))]
        return sorted(records, key=lambda r: (r.recorded_at, r.digest))

    def _append(
        self,
        kind: RecordKind,
        project: str,
        payload: dict[str, Any],
        issuer: str,
        idempotency_key: str,
    ) -> Record:
        for existing in self._records():
            if existing.project == project and existing.idempotency_key == idempotency_key:
                if existing.kind is kind and existing.payload == payload:
                    return existing
                raise IdempotencyConflict(
                    f"idempotency key {idempotency_key!r} already used by {existing.digest}"
                )
        record = Record.build(
            kind=kind,
            project=project,
            issuer=issuer,
            idempotency_key=idempotency_key,
            payload=payload,
            recorded_at=self._clock(),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / receipt_filename(record)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
        return record
