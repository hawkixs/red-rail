"""A brain-v42 delivery ledger in memory, faithful to the published contract
(`src/rail/contracts/delivery_attestations.json`, brain-v42 `delivery_tools.py` at the
pinned ref): the six tools the rail calls, with the same parameter names, the same stable
error codes and the same rules — uniqueness triple, replay equality, form validation,
scopes, keyset cursor. The identity the real server reads from `X-Brain-Agent` is `agent`
here (the in-memory transport carries no HTTP header)."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

KIND = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PREFIX = b"brain-delivery-attestation:v1\n"
MAX_PAYLOAD = 65536


def refuse(code: str, message: str = "") -> ToolError:
    return ToolError(f"{code}: {message or code}")


def normalize_agent(label: str | None) -> str:
    value = (label or "").strip()
    if not value:
        return "unknown"
    if "${" in value:
        return "_unexpanded"
    if value.startswith("/"):
        value = value.rsplit("/", 1)[-1]
    return value[:64]


def _check_payload(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise refuse("invalid_payload", "too deep")
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return
    if isinstance(value, float):
        raise refuse("invalid_payload", "float")
    if isinstance(value, str):
        if "\x00" in value:
            raise refuse("invalid_payload", "nul")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise refuse("invalid_payload", "surrogate") from exc
        return
    if isinstance(value, list):
        for item in value:
            _check_payload(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise refuse("invalid_payload", "non-string key")
            _check_payload(item, depth + 1)
        return
    raise refuse("invalid_payload", type(value).__name__)


def digest_of(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    if len(canonical) > MAX_PAYLOAD:
        raise refuse("invalid_payload", "too large")
    return hashlib.sha256(PREFIX + canonical).hexdigest()


def _instant(value: Any, code: str) -> datetime:
    if not isinstance(value, str):
        raise refuse("invalid_arguments", "instant must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise refuse(code, "unparsable") from exc
    if parsed.tzinfo is None:
        raise refuse(code, "naive")
    return parsed.astimezone(UTC)


@dataclass
class Ticket:
    id: str
    from_project: str
    to_project: str
    revisions: list[dict[str, Any]] = field(default_factory=list)
    assessment_version: int = 1
    bindings: list[dict[str, Any]] = field(default_factory=list)
    integration_receipt: dict[str, Any] | None = None
    fulfillment_receipt: dict[str, Any] | None = None
    status: str = "open"  # brain's raw ticket status; {wontfix, closed, acked} are terminal

    def participant(self, project: str) -> bool:
        return project in (self.from_project, self.to_project)


class FakeBrain:
    """Build one per test; `server` is the FastMCP instance to hand to the client."""

    def __init__(self, *, agent: str = "operator", enabled: bool = True) -> None:
        self.agent = agent  # what the real server reads from X-Brain-Agent
        self.enabled = enabled
        self.tickets: dict[str, Ticket] = {}
        self.attestations: list[dict[str, Any]] = []
        self.repositories: dict[tuple[str, int], str] = {}  # (project, repository_id) -> slug
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.server = FastMCP("fake-brain")
        self._register()

    # -- test-side setup ---------------------------------------------------------------

    def add_ticket(self, from_project: str, to_project: str, ticket_id: str | None = None) -> str:
        ticket = Ticket(ticket_id or str(uuid4()), from_project, to_project)
        self.tickets[ticket.id] = ticket
        return ticket.id

    def register_repository(self, project: str, repository_id: int, slug: str) -> None:
        self.repositories[(project, repository_id)] = slug

    def integrate(self, ticket_id: str, integration_sha: str, *, issued_at: datetime) -> None:
        ticket = self._ticket(ticket_id)
        ticket.integration_receipt = {
            "id": str(uuid4()),
            "ticket_id": ticket_id,
            "milestone": "integration",
            "contract_revision": len(ticket.revisions),
            "attempt": 1,
            "contract_digest": "0" * 64,
            "delivery_digest": hashlib.sha256(integration_sha.encode()).hexdigest(),
            "issued_at": issued_at.isoformat(),
            "proof": {"artifact_proofs": [{"integration_sha": integration_sha}]},
        }
        ticket.assessment_version += 1

    def fulfil(self, ticket_id: str, *, issued_at: datetime) -> None:
        ticket = self._ticket(ticket_id)
        ticket.fulfillment_receipt = {
            **(ticket.integration_receipt or {"proof": {"artifact_proofs": []}}),
            "id": str(uuid4()),
            "milestone": "fulfilled",
            "issued_at": issued_at.isoformat(),
        }

    # -- internals -----------------------------------------------------------------------

    def _ticket(self, ticket_id: str) -> Ticket:
        try:
            UUID(ticket_id)
        except (ValueError, TypeError) as exc:
            raise refuse("invalid_arguments", "ticket_id") from exc
        if ticket_id not in self.tickets:
            raise refuse("ticket_not_found", "ticket was not found")  # brain's exact wording
        return self.tickets[ticket_id]

    def _identity(self) -> str:
        label = normalize_agent(self.agent)
        if label in ("unknown", "_unexpanded"):
            raise refuse("invalid_issuer")
        return label

    def _view(self, ticket: Ticket, history_limit: int) -> dict[str, Any]:
        if not ticket.revisions:
            raise refuse("contract_not_found", "delivery contract was not found")  # brain's text
        rows = sorted(
            (a for a in self.attestations if a["ticket_id"] == ticket.id),
            key=lambda a: (a["emitted_at"], a["id"]),
            reverse=True,
        )
        return {
            "contract": ticket.revisions[-1],
            "assessment": {
                "assessment_id": "a" * 64,
                "assessment_version": ticket.assessment_version,
                "assessed_at": datetime.now(UTC).isoformat(),
                "coordination_status": ticket.status,
                "delivery_stage": "integrated" if ticket.integration_receipt else "proposed",
                "observation_health": "fresh",
                "acceptance_state": "accepted" if ticket.status == "closed" else "pending",
                "requirements_satisfied": ticket.integration_receipt is not None,
                "integration_receipt_eligible": False,
                "completion_eligible_now": False,
                "contract_fulfilled": ticket.fulfillment_receipt is not None,
                "delivery_digest": "b" * 64,
                "blockers": [],
                "deliverables": [],
                "eligible_work": [],
            },
            "bindings": [{"binding": b} for b in ticket.bindings],
            "contexts": [],
            "integration_receipt": ticket.integration_receipt,
            "fulfillment_receipt": ticket.fulfillment_receipt,
            "history": None,
            "attestations": {
                "items": rows[:history_limit],
                "next_cursor": None,
                "omitted_count": max(0, len(rows) - history_limit),
            },
        }

    @staticmethod
    def _cursor(scope: str, row: dict[str, Any]) -> str:
        raw = json.dumps({"v": 1, "scope": scope, "e": row["emitted_at"], "id": row["id"]})
        return base64.urlsafe_b64encode(raw.encode()).decode()

    @staticmethod
    def _decode_cursor(cursor: str, scope: str) -> tuple[str, str]:
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            if data["v"] != 1 or data["scope"] != scope:
                raise ValueError
            return data["e"], data["id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise refuse("invalid_cursor") from exc

    def _register(self) -> None:
        brain = self

        @self.server.tool
        def brain_delivery_get(
            ticket_id: str,
            actor_project: str,
            history_limit: int = 20,
            history_cursor: str | None = None,
        ) -> dict[str, Any]:
            brain.calls.append(("brain_delivery_get", {"ticket_id": ticket_id}))
            ticket = brain._ticket(ticket_id)
            if not ticket.participant(actor_project):
                raise refuse("not_allowed")
            if not 1 <= history_limit <= 100:
                raise refuse("invalid_limit")
            return brain._view(ticket, history_limit)

        @self.server.tool
        def brain_delivery_list(
            actor_project: str,
            limit: int = 20,
            cursor: str | None = None,
            work: str | None = None,
            blocker: str | None = None,
            stage: str | None = None,
        ) -> dict[str, Any]:
            brain.calls.append(("brain_delivery_list", {"actor_project": actor_project}))
            if not 1 <= limit <= 100:
                raise refuse("invalid_limit")
            items = [
                {**brain._view(t, 1), "attestations": None}
                for t in brain.tickets.values()
                if t.participant(actor_project) and t.revisions
            ]
            return {"items": items[:limit], "next_cursor": None, "omitted_count": 0}

        @self.server.tool
        def brain_delivery_contract_set(
            ticket_id: str,
            actor_project: str,
            contract: dict[str, Any],
            expected_revision: int,
            idempotency_key: str,
            reason: str,
        ) -> dict[str, Any]:
            brain.calls.append(
                ("brain_delivery_contract_set", {"ticket_id": ticket_id, "actor": actor_project})
            )
            ticket = brain._ticket(ticket_id)
            if actor_project != ticket.from_project:  # brain: "only the requester may set"
                raise refuse("not_allowed", "only the requester may set a delivery contract")
            for revision in ticket.revisions:
                if revision["idempotency_key"] == idempotency_key:
                    return revision
            if expected_revision != len(ticket.revisions):
                raise refuse("revision_conflict")
            for required in ("objective", "deliverables", "acceptance_mode"):
                if required not in contract:
                    raise refuse("invalid_arguments", f"contract.{required}")
            stored = json.loads(json.dumps(contract))
            for deliverable in stored.get("deliverables", []):  # brain fills the numeric id
                if deliverable.get("repository_id") is None:
                    for (project, repository_id), slug in brain.repositories.items():
                        if slug == deliverable.get("repository") and project == ticket.to_project:
                            deliverable["repository_id"] = repository_id
            revision = {
                **stored,
                "ticket_id": ticket.id,
                "contract_revision": len(ticket.revisions) + 1,
                "content_digest": digest_of(contract),
                "author_project": actor_project,
                "created_at": datetime.now(UTC).isoformat(),
                "amendment_reason": reason,
                "idempotency_key": idempotency_key,
            }
            ticket.revisions.append(revision)
            ticket.assessment_version += 1
            return revision

        @self.server.tool
        def brain_delivery_bind_pr(
            ticket_id: str,
            actor_project: str,
            deliverable_key: str,
            repository_id: int,
            pr_number: int,
            expected_revision: int,
            expected_workflow_version: int,
            idempotency_key: str,
        ) -> dict[str, Any]:
            brain.calls.append(("brain_delivery_bind_pr", {"pr_number": pr_number}))
            ticket = brain._ticket(ticket_id)
            if actor_project != ticket.to_project:
                raise refuse("not_allowed")
            if not ticket.revisions:
                raise refuse("contract_not_found")
            for binding in ticket.bindings:
                if binding["idempotency_key"] == idempotency_key:
                    return binding
            if expected_revision != len(ticket.revisions):
                raise refuse("revision_conflict")
            if expected_workflow_version != ticket.assessment_version:
                raise refuse("revision_conflict", "workflow version")
            if (actor_project, repository_id) not in brain.repositories:
                raise refuse("repository_not_registered")
            binding = {
                "id": str(uuid4()),
                "ticket_id": ticket.id,
                "contract_revision": expected_revision,
                "attempt": len(ticket.bindings) + 1,
                "deliverable_key": deliverable_key,
                "repository_id": repository_id,
                "pr_number": pr_number,
                "state": "proposed",
                "head_sha": None,
                "base_sha": None,
                "integration_sha": None,
                "binding_version": 1,
                "idempotency_key": idempotency_key,
            }
            ticket.bindings.append(binding)
            ticket.assessment_version += 1
            return binding

        @self.server.tool
        def brain_delivery_attest(
            ticket_id: str,
            actor_project: str,
            kind: str,
            payload: dict[str, Any],
            idempotency_key: str,
            emitted_at: str,
            contract_revision: int | None = None,
        ) -> dict[str, Any]:
            brain.calls.append(("brain_delivery_attest", {"kind": kind, "key": idempotency_key}))
            identity = brain._identity()
            if not brain.enabled:
                raise refuse("delivery_disabled")
            ticket = brain._ticket(ticket_id)
            if not ticket.participant(actor_project):
                raise refuse("not_allowed")
            if not ticket.revisions:
                raise refuse("contract_not_found")
            if not KIND.match(kind or ""):
                raise refuse("invalid_kind")
            if not isinstance(payload, dict):
                raise refuse("invalid_arguments", "payload")
            _check_payload(payload)
            digest = digest_of(payload)
            when = _instant(emitted_at, "invalid_emitted_at")
            if contract_revision is not None and not 1 <= contract_revision <= len(
                ticket.revisions
            ):
                raise refuse("revision_not_found")
            for row in brain.attestations:
                if (row["ticket_id"], row["issuer_project"], row["idempotency_key"]) == (
                    ticket.id,
                    actor_project,
                    idempotency_key,
                ):
                    same = (
                        row["kind"] == kind
                        and row["digest"] == digest
                        and row["issuer_identity"] == identity
                        and row["contract_revision"] == contract_revision
                        and datetime.fromisoformat(row["emitted_at"]) == when
                    )
                    if same:
                        return row
                    raise refuse("idempotency_key_reused")
            row = {
                "id": str(uuid4()),
                "ticket_id": ticket.id,
                "contract_revision": contract_revision,
                "kind": kind,
                "payload": payload,
                "digest": digest,
                "issuer_project": actor_project,
                "issuer_identity": identity,
                "idempotency_key": idempotency_key,
                "emitted_at": when.isoformat(),
                "recorded_at": datetime.now(UTC).isoformat(),
            }
            brain.attestations.append(row)
            return row

        @self.server.tool
        def brain_delivery_attestation_list(
            actor_project: str,
            ticket_id: str | None = None,
            issuer_project: str | None = None,
            kind: str | None = None,
            since: str | None = None,
            until: str | None = None,
            limit: int = 20,
            cursor: str | None = None,
        ) -> dict[str, Any]:
            brain.calls.append(
                ("brain_delivery_attestation_list", {"kind": kind, "ticket_id": ticket_id})
            )
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
                raise refuse("invalid_limit")
            if kind is not None and not KIND.match(kind):
                raise refuse("invalid_kind")
            lower = _instant(since, "invalid_window") if since is not None else None
            upper = _instant(until, "invalid_window") if until is not None else None
            if lower and upper and lower > upper:
                raise refuse("invalid_window")
            if ticket_id is not None:
                ticket = brain._ticket(ticket_id)
                if not ticket.participant(actor_project):
                    raise refuse("not_allowed")
                scope = f"ticket:{ticket.id}"
                rows = [a for a in brain.attestations if a["ticket_id"] == ticket.id]
                if issuer_project is not None:
                    rows = [a for a in rows if a["issuer_project"] == issuer_project]
            else:
                if issuer_project is None:
                    raise refuse("invalid_scope")
                if issuer_project != actor_project:
                    raise refuse("not_allowed")
                scope = f"issuer:{issuer_project}"
                rows = [a for a in brain.attestations if a["issuer_project"] == issuer_project]
            if kind is not None:
                rows = [a for a in rows if a["kind"] == kind]
            if lower is not None:
                rows = [a for a in rows if datetime.fromisoformat(a["emitted_at"]) >= lower]
            if upper is not None:
                rows = [a for a in rows if datetime.fromisoformat(a["emitted_at"]) <= upper]
            rows.sort(key=lambda a: (a["emitted_at"], a["id"]), reverse=True)
            if cursor is not None:
                after_e, after_id = brain._decode_cursor(cursor, scope)
                rows = [a for a in rows if (a["emitted_at"], a["id"]) < (after_e, after_id)]
            page, rest = rows[:limit], rows[limit:]
            return {
                "items": page,
                "next_cursor": brain._cursor(scope, page[-1]) if rest and page else None,
                "omitted_count": len(rest),
            }

        @self.server.tool
        def brain_delivery_accept(
            ticket_id: str,
            actor_project: str,
            rationale: str,
            expected_revision: int,
            expected_attempt: int,
            expected_delivery_digest: str,
        ) -> dict[str, Any]:
            brain.calls.append(("brain_delivery_accept", {"ticket_id": ticket_id}))
            brain._identity()
            ticket = brain._ticket(ticket_id)
            if actor_project != ticket.from_project:
                raise refuse("not_allowed", "only the requester accepts a delivery")
            if not ticket.revisions:
                raise refuse("contract_not_found")
            receipt = ticket.integration_receipt
            if receipt is None:
                raise refuse("revision_conflict", "no integration evidence to accept")
            expected = (receipt["contract_revision"], receipt["attempt"], "b" * 64)
            if (expected_revision, expected_attempt, expected_delivery_digest) != expected:
                raise refuse("revision_conflict", "the evidence moved: read the view again")
            if ticket.fulfillment_receipt is None:
                brain.fulfil(ticket_id, issued_at=datetime.now(UTC))
                ticket.fulfillment_receipt["acceptance_basis"] = "explicit"
                ticket.fulfillment_receipt["explicit_acceptance"] = {
                    "requester_project": actor_project,
                    "rationale": rationale,
                }
            return ticket.fulfillment_receipt
