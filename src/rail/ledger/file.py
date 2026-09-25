"""`FileLedger`: the repository's receipts are the ledger.

One operator, one repository, no network.
"""

from __future__ import annotations

import json
import os
import uuid
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
    brain_digest,
    idempotency_key_for,
)


def receipt_filename(record: Record) -> str:
    label = record.attestation.value if record.attestation else record.kind.value
    stamp = record.recorded_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{label}-{record.digest[7:19]}.json"


def load_receipt(path: Path, *, text: str | None = None) -> Record:
    """A receipt's `Record`. `text`, when given, is content already read by the caller (so a
    vanished file is a `FileNotFoundError` the caller sees before this wraps anything); the
    default reads `path` itself. Any other read or parse failure is a `LedgerError`."""
    try:
        content = path.read_text() if text is None else text
        return Record.model_validate(json.loads(content))
    except (OSError, ValueError, ValidationError) as exc:
        raise LedgerError(f"unreadable receipt {path.name}: {exc}") from exc


class FileLedger:
    def __init__(self, root: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self.root = root
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- protocol -------------------------------------------------------------------------

    def coordination_status(self) -> str | None:
        """Receipts carry no ticket, so this ledger has no disposition to report."""
        return None

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
        emitted_at: datetime | None = None,
    ) -> Record:
        try:
            brain_digest(data)
        except ValueError as exc:
            raise LedgerError(str(exc)) from exc
        payload = {"kind": kind.value, "data": data}
        return self._append(
            RecordKind.ATTESTATION,
            project,
            payload,
            issuer,
            idempotency_key,
            recorded_at=emitted_at,
        )

    def accept(
        self, project: str, *, rationale: str, issuer: str, sha: str | None = None
    ) -> Record:
        """Without brain the acceptance is a `fulfilled` attestation, keyed by the commit."""
        data: dict[str, Any] = {"rationale": rationale}
        if sha:
            data["sha"] = sha
        return self.attest(
            project,
            AttestationKind.FULFILLED,
            data,
            issuer=issuer,
            idempotency_key=idempotency_key_for(AttestationKind.FULFILLED, data),
        )

    def list(
        self,
        project: str | None,
        *,
        kind: RecordKind | None = None,
        attestation: AttestationKind | None = None,
    ) -> list[Record]:
        """`project=None`: every receipt of the directory — how a gate observes the default
        file ledger of a repository that declares no project."""
        return [
            r
            for r in self._records()
            if (project is None or r.project == project)
            and (kind is None or r.kind is kind)
            and (attestation is None or r.attestation is attestation)
        ]

    def get(self, project: str, digest: str) -> Record | None:
        return next(
            (r for r in self._records() if r.project == project and r.digest == digest), None
        )

    def path_of(self, record: Record) -> Path:
        return self.root / receipt_filename(record)

    def mirror(self, record: Record) -> Record:
        """Write an already-built record (a row read from brain) as a receipt; a receipt with
        the same digest is left alone, the same key with another content is a conflict."""
        for existing in self._records():
            if existing.digest == record.digest:
                return existing
            if (
                existing.project == record.project
                and existing.idempotency_key == record.idempotency_key
            ):
                raise IdempotencyConflict(
                    f"idempotency key {record.idempotency_key!r} already used by {existing.digest}"
                )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_of(record)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(
            json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return record

    # -- internals ------------------------------------------------------------------------

    def _records(self) -> list[Record]:
        """Every receipt, verified: a digest that no longer matches its content is a tampered
        ledger and stops the read — the gates never build evidence on it (fail closed). A
        receipt removed after the listing (the spool drains on every successful attest, replay
        and `--from`) is a routine race, not corruption: it is skipped, not raised."""
        if not self.root.is_dir():
            return []
        records = []
        for path in sorted(self.root.glob("*.json")):
            try:
                text = path.read_text()
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                # UnicodeDecodeError is a ValueError; any other read failure (permission, a
                # directory named `*.json`) stays fail-closed, unlike a vanished file
                raise LedgerError(f"unreadable receipt {path.name}: {exc}") from exc
            record = load_receipt(path, text=text)
            if not record.verify():
                raise LedgerError(
                    f"tampered receipt {path.name}: digest does not match its content"
                )
            records.append(record)
        return sorted(records, key=lambda r: (r.recorded_at, r.digest))

    def _append(
        self,
        kind: RecordKind,
        project: str,
        payload: dict[str, Any],
        issuer: str,
        idempotency_key: str,
        recorded_at: datetime | None = None,
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
            recorded_at=recorded_at or self._clock(),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / receipt_filename(record)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(
            json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return record
