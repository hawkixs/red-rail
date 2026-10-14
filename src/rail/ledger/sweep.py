"""Collect verified receipts from another checkout without rewriting or removing evidence."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rail.ledger import RECEIPTS_DIR, AttestationKind, LedgerError
from rail.ledger.file import load_receipt


@dataclass(frozen=True, slots=True)
class SweepReport:
    """Names and counts stay together so refused evidence is never silently dropped."""

    copied: tuple[str, ...] = ()
    already_present: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {key: len(names) for key, names in self.files.items()}

    @property
    def files(self) -> dict[str, tuple[str, ...]]:
        return {
            "copied": self.copied,
            "already_present": self.already_present,
            "refused": self.refused,
            "conflicts": self.conflicts,
            "skipped": self.skipped,
        }

    def as_dict(self) -> dict[str, Any]:
        return {**self.counts, "files": self.files}


def _existing(path: Path, content: bytes) -> str:
    """Unreadable destinations are conflicts, because they must never be overwritten."""
    try:
        return "already_present" if path.read_bytes() == content else "conflicts"
    except OSError:
        return "conflicts"


def sweep_receipts(
    source: Path,
    target: Path,
    *,
    project: str,
    reviewer_identity: str,
    dry_run: bool = False,
) -> SweepReport:
    """Copy only verified verdicts and rulings from this project's reviewer.

    Other verified receipts are skipped; bytes and both checkouts' evidence are preserved.
    `dry_run` applies the same checks without creating anything; `copied` then names what
    would be copied. Exclusive creation also prevents overwriting a concurrent arrival.
    """
    found: dict[str, list[str]] = {
        "copied": [],
        "already_present": [],
        "refused": [],
        "conflicts": [],
        "skipped": [],
    }
    for path in sorted((source / RECEIPTS_DIR).glob("*.json")):
        try:
            content = path.read_bytes()
            record = load_receipt(path, text=content.decode("utf-8"))
            if not record.verify():
                found["refused"].append(path.name)
                continue
        except (OSError, UnicodeError, LedgerError):
            found["refused"].append(path.name)
            continue
        destination = target / RECEIPTS_DIR / path.name
        # a receipt the target already holds is reported as such, whoever issued it: the
        # reviewer's checkout also carries every committed receipt of its own history
        if os.path.lexists(destination):
            found[_existing(destination, content)].append(path.name)
            continue
        if (
            record.attestation
            not in (AttestationKind.REVIEW_VERDICT, AttestationKind.REVIEW_RULING)
            or record.issuer != reviewer_identity
            or record.project != project
        ):
            found["skipped"].append(path.name)
            continue
        if not dry_run:
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                handle = destination.open("xb")
            except FileExistsError:
                found[_existing(destination, content)].append(path.name)
                continue
            except OSError:
                found["refused"].append(path.name)
                continue
            try:
                with handle:
                    handle.write(content)
            except OSError:
                destination.unlink(missing_ok=True)
                found["refused"].append(path.name)
                continue
        found["copied"].append(path.name)
    return SweepReport(**{key: tuple(names) for key, names in found.items()})
