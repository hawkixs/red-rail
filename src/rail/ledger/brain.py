"""`BrainLedger`: brain-v42 is the shared, observed authority (ADR-0002); an attestation waits
in the host's spool (`ledger/spool.py`) until brain has recorded it.

Mapping (decided with brain-v42 on 2026-09-18, decision 4e7c2545): one subject per
ticket — `project` is the ticket's `to_project` and the `actor_project` of every call;
`issuer` is the `X-Brain-Agent` label; `recorded_at` is `emitted_at`; `data` is the payload.
Milestones (`integrated`, `fulfilled`) are receipts brain issues and are read from the
ticket, never attested from here. Stage 10 is `accept`, the requester's call; a project's
attestations are listed across its tickets.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from rail.brain.client import BrainClient, BrainToolError, BrainUnreachable
from rail.ledger import (
    BRAIN_MILESTONES,
    AttestationKind,
    Contract,
    IdempotencyConflict,
    LedgerError,
    PullRequestRef,
    Record,
    RecordKind,
    Unattested,
    brain_digest,
)
from rail.ledger.file import FileLedger

MILESTONE_ISSUER = "brain-v42"
# The canonical delivery ticket is `red → <project>`: brain lets only the requester set the
# contract, while attestations and bindings are the executor's (the project's).
REQUESTER = "red"
PAGE = 100
KNOWN_REFUSALS: frozenset[str] = frozenset(
    {
        "contract_not_found",
        "delivery_disabled",
        "idempotency_key_reused",
        "invalid_cursor",
        "invalid_emitted_at",
        "invalid_issuer",
        "invalid_kind",
        "invalid_payload",
        "invalid_scope",
        "invalid_window",
        "not_allowed",
        "revision_not_found",
        "ticket_not_found",
        "revision_conflict",
        "repository_not_registered",
        "invalid_arguments",
        "invalid_limit",
        "delivery_unavailable",
    }
)


def _instant(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise LedgerError(f"brain returned a naive instant: {value!r}")
    return parsed.astimezone(UTC)


def record_from_row(project: str, row: dict[str, Any]) -> Record | None:
    """A brain attestation row as a red-rail record; None for a kind the rail does not
    know. The digest brain computed is cross-checked against the payload."""
    try:
        kind = AttestationKind(str(row["kind"]))
    except ValueError:
        return None
    try:
        data = dict(row["payload"])
        if brain_digest(data) != str(row["digest"]):
            raise LedgerError(
                f"brain row {row.get('id')}: digest {row['digest']} does not match its payload"
            )
        return Record.build(
            kind=RecordKind.ATTESTATION,
            project=project,
            issuer=str(row["issuer_identity"]),
            idempotency_key=str(row["idempotency_key"]),
            payload={"kind": kind.value, "data": data},
            recorded_at=_instant(row["emitted_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LedgerError(f"brain row out of shape: {exc}") from exc


class BrainLedger:
    def __init__(
        self,
        client: BrainClient,
        *,
        ticket: UUID | str,
        project: str,
        spool_dir: Path,
        clock: Callable[[], datetime] | None = None,
        repository_id: Callable[[str], int] | None = None,
        requester: str = REQUESTER,
    ) -> None:
        self.client = client
        self.ticket = UUID(str(ticket))
        self.project = project
        self.requester = requester
        self.spool = FileLedger(spool_dir, clock=clock)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._repository_id = repository_id or _gh_repository_id

    # -- protocol -------------------------------------------------------------------------

    def coordination_status(self) -> str | None:
        """What brain says about the ticket the manifest declares. Measured on the real
        ledger: a fulfilled ticket reads `closed` / `accepted` / `integrated`, an open one
        `open` / `pending` / `awaiting_artifact`."""
        view = self._view(required=False)
        if view is None:
            return None
        status = view.get("assessment", {}).get("coordination_status")
        return str(status) if status else None

    def contract_set(
        self, project: str, contract: Contract, *, reason: str, issuer: str, idempotency_key: str
    ) -> Record:
        """Set as the requester (brain: "only the requester may set a delivery contract").
        Idempotent on content: the same contract as the current revision creates no revision.
        The record is keyed `contract:<ticket>:<revision>` and carries what brain records — the
        requester as issuer, the revision's `created_at` — so the row read back rebuilds it."""
        self._same(project)
        view = self._view(required=False)
        current = view["contract"] if view else None
        if current is not None and self._contract_fields(current) == self._as_stored(
            contract, current
        ):
            row = current
        else:
            row = self._call_checked(
                "brain_delivery_contract_set",
                {
                    "ticket_id": str(self.ticket),
                    "actor_project": self.requester,
                    "contract": contract.model_dump(mode="json"),
                    "expected_revision": int(current["contract_revision"]) if current else 0,
                    "idempotency_key": idempotency_key,
                    "reason": reason,
                },
                agent=issuer,
            )
        return self._contract_record(row)

    def bind(
        self, project: str, pr: PullRequestRef, *, issuer: str, idempotency_key: str
    ) -> Record:
        self._same(project)
        view = self._view(required=True)
        deliverable = next(
            (d for d in view["contract"]["deliverables"] if d["repository"] == pr.repository), None
        )
        if deliverable is None:
            raise LedgerError(f"{pr.repository} is not a deliverable of the contract")
        repository_id = deliverable.get("repository_id") or self._repository_id(pr.repository)
        row = self._call_checked(
            "brain_delivery_bind_pr",
            {
                "ticket_id": str(self.ticket),
                "actor_project": project,
                "deliverable_key": deliverable["key"],
                "repository_id": int(repository_id),
                "pr_number": pr.number,
                "expected_revision": int(view["contract"]["contract_revision"]),
                "expected_workflow_version": int(view["assessment"]["assessment_version"]),
                "idempotency_key": idempotency_key,
            },
            agent=issuer,
        )
        record = Record.build(
            kind=RecordKind.BINDING,
            project=project,
            issuer=issuer,
            idempotency_key=idempotency_key,
            payload={**pr.model_dump(mode="json"), "binding_id": str(row["id"])},
            recorded_at=self._clock(),
        )
        return record

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
        self._same(project)
        if kind.value in BRAIN_MILESTONES:
            raise LedgerError(
                f"{kind.value} is a brain milestone in ledger: brain — it is read from the "
                "ticket, never attested"
            )
        self.spool.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = self.spool.attest(
            project,
            kind,
            data,
            issuer=issuer,
            idempotency_key=idempotency_key,
            emitted_at=emitted_at,
        )
        receipt = self.spool.path_of(record)
        try:
            row = self._call(
                "brain_delivery_attest",
                {
                    "ticket_id": str(self.ticket),
                    "actor_project": project,
                    "kind": kind.value,
                    "payload": data,
                    "idempotency_key": idempotency_key,
                    "emitted_at": record.recorded_at.isoformat(),
                },
                agent=issuer,
            )
        except BrainToolError as exc:
            if exc.code == "idempotency_key_reused":
                # The spool drains on success (decision 1): a retry after the receipt is
                # already gone gets a fresh `emitted_at`, which brain's own replay equality
                # (contract `delivery-attestations-v1.0`) never matches. Brain itself is then
                # the only place left holding the original event, so it settles the replay.
                receipt.unlink(missing_ok=True)
                existing = next(
                    (
                        r
                        for r in self._attestation_records(None)
                        if r.idempotency_key == idempotency_key
                    ),
                    None,
                )
                if existing is not None and existing.attestation is kind and existing.data == data:
                    return existing
                raise IdempotencyConflict(
                    f"idempotency key {idempotency_key!r} already used"
                ) from exc
            raise Unattested(receipt, exc.code) from exc
        except BrainUnreachable as exc:
            raise Unattested(receipt, "unreachable") from exc
        if str(row.get("digest")) != brain_digest(data):
            raise LedgerError("brain stored a different payload digest than the spooled receipt's")
        receipt.unlink(missing_ok=True)
        return record

    def accept(
        self, project: str, *, rationale: str, issuer: str, sha: str | None = None
    ) -> Record:
        """`brain_delivery_accept` as the requester, against the exact integration evidence
        the view shows (revision, attempt, delivery digest); the fulfilment receipt brain
        returns is rebuilt like the other milestone. `sha` is brain's, never the caller's."""
        self._same(project)
        view = self._view(required=True)
        receipt = view.get("integration_receipt")
        if not receipt:
            raise LedgerError("no integration receipt to accept: the delivery is not integrated")
        row = self._call_checked(
            "brain_delivery_accept",
            {
                "ticket_id": str(self.ticket),
                "actor_project": self.requester,
                "rationale": rationale,
                "expected_revision": int(receipt["contract_revision"]),
                "expected_attempt": int(receipt["attempt"]),
                "expected_delivery_digest": str(view["assessment"]["delivery_digest"]),
            },
            agent=issuer,
        )
        return self._milestone_record(row, AttestationKind.FULFILLED)

    def list(
        self,
        project: str,
        *,
        kind: RecordKind | None = None,
        attestation: AttestationKind | None = None,
    ) -> list[Record]:
        self._same(project)
        records: list[Record] = []
        view = self._view(required=False)
        if view is None:
            return []
        if kind in (None, RecordKind.CONTRACT) and attestation is None:
            records.append(self._contract_record(view["contract"]))
        if kind in (None, RecordKind.BINDING) and attestation is None:
            records.extend(self._binding_records(view))
        if kind in (None, RecordKind.ATTESTATION):
            records.extend(self._attestation_records(attestation))
            records.extend(self._milestone_records(view, attestation))
        return sorted(records, key=lambda r: (r.recorded_at, r.digest))

    def get(self, project: str, digest: str) -> Record | None:
        return next((r for r in self.list(project) if r.digest == digest), None)

    # -- internals ------------------------------------------------------------------------

    def _same(self, project: str) -> None:
        if project != self.project:
            raise LedgerError(f"this ledger is bound to {self.project}, not {project}")

    def _call(
        self, name: str, arguments: dict[str, Any], *, agent: str | None = None
    ) -> dict[str, Any]:
        return self.client.call(name, arguments, agent=agent)

    def _call_checked(
        self, name: str, arguments: dict[str, Any], *, agent: str | None = None
    ) -> dict[str, Any]:
        """A refusal or an unreachable brain is a `LedgerError` for the callers that have
        nothing to replay (contract, binding); `attest` keeps the codes for `Unattested`."""
        try:
            return self._call(name, arguments, agent=agent)
        except BrainToolError as exc:
            raise LedgerError(f"{name}: {exc}") from exc
        except BrainUnreachable as exc:
            raise LedgerError(f"brain unreachable: {exc}") from exc

    def _view(self, *, required: bool) -> dict[str, Any] | None:
        try:
            return self._call(
                "brain_delivery_get",
                {"ticket_id": str(self.ticket), "actor_project": self.project, "history_limit": 1},
            )
        except BrainToolError as exc:
            if exc.code == "contract_not_found" and not required:
                return None
            raise LedgerError(f"brain_delivery_get: {exc}") from exc
        except BrainUnreachable as exc:
            raise LedgerError(f"brain unreachable: {exc}") from exc

    @staticmethod
    def _as_stored(contract: Contract, current: dict[str, Any]) -> dict[str, Any]:
        """Our contract as brain would store it: brain fills a deliverable's `repository_id`
        from its registry, so a `None` on our side compares equal to its enrichment."""
        mine = contract.model_dump(mode="json")
        theirs = {
            d.get("repository"): d.get("repository_id") for d in current.get("deliverables", [])
        }
        for deliverable in mine["deliverables"]:
            if deliverable.get("repository_id") is None:
                deliverable["repository_id"] = theirs.get(deliverable["repository"])
        return mine

    @staticmethod
    def _contract_fields(row: dict[str, Any]) -> dict[str, Any]:
        """The contract as red-rail models it, out of a brain revision row."""
        fields = {k: row[k] for k in Contract.model_fields if k in row}
        return Contract.model_validate(fields).model_dump(mode="json")

    def _contract_record(self, row: dict[str, Any]) -> Record:
        return Record.build(
            kind=RecordKind.CONTRACT,
            project=self.project,
            issuer=str(row.get("author_project") or self.requester),
            idempotency_key=f"contract:{self.ticket}:{int(row['contract_revision'])}",
            payload={
                "contract": self._contract_fields(row),
                "reason": str(row.get("amendment_reason") or ""),
            },
            recorded_at=_instant(row["created_at"]),
        )

    def _binding_records(self, view: dict[str, Any]) -> list[Record]:
        records = []
        for evidence in view.get("bindings", []):
            binding = evidence["binding"]
            records.append(
                Record.build(
                    kind=RecordKind.BINDING,
                    project=self.project,
                    issuer=self.project,
                    idempotency_key=str(
                        binding.get("idempotency_key") or f"binding:{binding['id']}"
                    ),
                    payload={
                        "repository": next(
                            (
                                d["repository"]
                                for d in view["contract"]["deliverables"]
                                if d["key"] == binding["deliverable_key"]
                            ),
                            "",
                        ),
                        "number": int(binding["pr_number"]),
                        "head_sha": str(binding.get("head_sha") or "0" * 40),
                        "binding_id": str(binding["id"]),
                    },
                    recorded_at=_instant(view["assessment"]["assessed_at"]),
                )
            )
        return records

    def _attestation_records(self, attestation: AttestationKind | None) -> list[Record]:
        """The project's facts across its tickets: issuer scope, not restricted to one
        ticket — one ticket per delivery, one ledger per project — mirrors, metrics and
        evidence survive a ticket change; milestones stay the manifest ticket's."""
        records: list[Record] = []
        cursor: str | None = None
        while True:
            arguments: dict[str, Any] = {
                "actor_project": self.project,
                "issuer_project": self.project,
                "limit": PAGE,
                "cursor": cursor,
            }
            if attestation is not None:
                arguments["kind"] = attestation.value
            try:
                page = self._call("brain_delivery_attestation_list", arguments)
            except (BrainToolError, BrainUnreachable) as exc:
                raise LedgerError(f"brain_delivery_attestation_list: {exc}") from exc
            for row in page.get("items", []):
                record = record_from_row(self.project, row)
                if record is not None:
                    records.append(record)
            cursor = page.get("next_cursor")
            if not cursor:
                return records

    def _milestone_record(self, receipt: dict[str, Any], kind: AttestationKind) -> Record:
        proofs = (receipt.get("proof") or {}).get("artifact_proofs") or []
        sha = str((proofs[0].get("integration_sha") if proofs else "") or "")
        return Record.build(
            kind=RecordKind.ATTESTATION,
            project=self.project,
            issuer=MILESTONE_ISSUER,
            idempotency_key=f"{kind.value}:{sha or receipt['id']}",
            payload={
                "kind": kind.value,
                "data": {
                    "sha": sha,
                    "receipt_id": str(receipt["id"]),
                    "delivery_digest": str(receipt.get("delivery_digest") or ""),
                    **(
                        {"rationale": str(acceptance.get("rationale") or "")}
                        if (acceptance := receipt.get("explicit_acceptance"))
                        else {}
                    ),
                },
            },
            recorded_at=_instant(receipt["issued_at"]),
        )

    def _milestone_records(
        self, view: dict[str, Any], attestation: AttestationKind | None
    ) -> list[Record]:
        records = []
        for key, kind in (
            ("integration_receipt", AttestationKind.INTEGRATED),
            ("fulfillment_receipt", AttestationKind.FULFILLED),
        ):
            receipt = view.get(key)
            if not receipt or (attestation is not None and attestation is not kind):
                continue
            records.append(self._milestone_record(receipt, kind))
        return records


def _gh_repository_id(slug: str) -> int:
    from rail.remotes import github_repository_id

    return github_repository_id(slug, run=subprocess.run)
