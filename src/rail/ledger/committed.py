"""A read-only snapshot of main's receipts and the reviewer's local evidence."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from rail import gitrepo
from rail.ledger import RECEIPTS_DIR, LedgerError, Record, RecordKind
from rail.ledger.file import FileLedger, load_receipt

MAIN_REF = "refs/remotes/origin/main"


class CommittedLedger(FileLedger):
    """Union verified main and working-tree receipts by digest so old checkouts stay useful.

    Local receipts include untracked reviewer verdicts. Every copy is verified before
    deduplication, so a valid committed copy never hides a tampered local receipt.
    """

    def __init__(self, repo: Path) -> None:
        super().__init__(repo / RECEIPTS_DIR)
        files = gitrepo.files_at(repo, MAIN_REF, RECEIPTS_DIR)
        if files is None:
            raise LedgerError("unreadable committed receipts on origin/main")
        records: dict[str, Record] = {}
        for name, text in files.items():
            record = load_receipt(Path(name), text=text)
            if not record.verify():
                raise LedgerError(
                    f"tampered receipt {Path(name).name}: digest does not match content"
                )
            records[record.digest] = record
        for record in super()._records():
            records[record.digest] = record
        self._snapshot = sorted(records.values(), key=lambda r: (r.recorded_at, r.digest))

    def _records(self) -> list[Record]:
        return list(self._snapshot)

    def _append(
        self,
        kind: RecordKind,
        project: str,
        payload: dict[str, Any],
        issuer: str,
        idempotency_key: str,
        recorded_at: datetime | None = None,
    ) -> Record:
        """Refuse every protocol write: publication must never change its source checkout."""
        raise LedgerError("committed ledger is read-only")

    def mirror(self, record: Record) -> Record:
        raise LedgerError("committed ledger is read-only")
