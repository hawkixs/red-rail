"""The fake brain implements the published contract: tool names and parameter names as
brain-v42 declares them (`delivery_tools.py` at the pinned ref), the stable codes, the
uniqueness triple and the replay equality of `delivery_attestations.json`."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from rail.contracts import ATTESTATION_CONTRACT, TOOL_ERROR_CODES
from tests.fake_brain import FakeBrain

TOOL_PARAMETERS = {
    "brain_delivery_get": {"ticket_id", "actor_project", "history_limit", "history_cursor"},
    "brain_delivery_list": {"actor_project", "limit", "cursor", "work", "blocker", "stage"},
    "brain_delivery_contract_set": {
        "ticket_id",
        "actor_project",
        "contract",
        "expected_revision",
        "idempotency_key",
        "reason",
    },
    "brain_delivery_bind_pr": {
        "ticket_id",
        "actor_project",
        "deliverable_key",
        "repository_id",
        "pr_number",
        "expected_revision",
        "expected_workflow_version",
        "idempotency_key",
    },
    "brain_delivery_attest": {
        "ticket_id",
        "actor_project",
        "kind",
        "payload",
        "idempotency_key",
        "emitted_at",
        "contract_revision",
    },
    "brain_delivery_attestation_list": {
        "actor_project",
        "ticket_id",
        "issuer_project",
        "kind",
        "since",
        "until",
        "limit",
        "cursor",
    },
}
T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
CONTRACT = {
    "schema_version": 1,
    "objective": "ship",
    "acceptance_criteria": [],
    "constraints": [],
    "priority": 0,
    "acceptance_mode": "explicit",
    "deliverables": [
        {
            "key": "main",
            "repository": "hawkixs/red-probe",
            "target_branch": "main",
            "required_checks": [],
            "review": {"required_approvals": 1, "allowed_reviewers": []},
        }
    ],
}


def call(brain: FakeBrain, name: str, **arguments):
    async def go():
        async with Client(brain.server) as client:
            return (await client.call_tool(name, arguments)).structured_content

    return asyncio.run(go())


def code_of(exc: ToolError) -> str:
    return str(exc).split(":", 1)[0].strip()


def _ready(brain: FakeBrain) -> str:
    ticket = brain.add_ticket("red", "red-probe")
    call(
        brain,
        "brain_delivery_contract_set",
        ticket_id=ticket,
        actor_project="red-probe",
        contract=CONTRACT,
        expected_revision=0,
        idempotency_key="c1",
        reason="bootstrap",
    )
    return ticket


def test_tool_names_and_parameters_are_the_published_ones() -> None:
    brain = FakeBrain()

    async def tools():
        async with Client(brain.server) as client:
            return {t.name: set(t.inputSchema["properties"]) for t in await client.list_tools()}

    listed = asyncio.run(tools())
    assert set(listed) == set(TOOL_PARAMETERS)
    assert set(ATTESTATION_CONTRACT["tools"]) <= set(listed)
    for name, parameters in TOOL_PARAMETERS.items():
        assert listed[name] == parameters, name


def test_attest_enforces_the_uniqueness_triple_and_replay_equality() -> None:
    brain = FakeBrain(agent="red-rail")
    ticket = _ready(brain)
    args = dict(
        ticket_id=ticket,
        actor_project="red-probe",
        kind="deployed",
        payload={"sha": "b" * 40},
        idempotency_key="deployed:x",
        emitted_at=T0.isoformat(),
    )
    first = call(brain, "brain_delivery_attest", **args)
    again = call(
        brain, "brain_delivery_attest", **{**args, "emitted_at": T0.astimezone(UTC).isoformat()}
    )
    assert again["id"] == first["id"] and len(brain.attestations) == 1
    with pytest.raises(ToolError) as reused:
        call(brain, "brain_delivery_attest", **{**args, "payload": {"sha": "c" * 40}})
    assert code_of(reused.value) == "idempotency_key_reused"
    with pytest.raises(ToolError) as later:
        call(
            brain,
            "brain_delivery_attest",
            **{**args, "emitted_at": (T0 + timedelta(seconds=1)).isoformat()},
        )
    assert code_of(later.value) == "idempotency_key_reused"


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"kind": "Deployed"}, "invalid_kind"),
        ({"payload": {"ratio": 1.5}}, "invalid_payload"),
        ({"emitted_at": "2026-09-18T12:00:00"}, "invalid_emitted_at"),
        ({"contract_revision": 9}, "revision_not_found"),
        ({"actor_project": "red-other"}, "not_allowed"),
    ],
)
def test_attest_form_and_precondition_codes(override: dict, code: str) -> None:
    brain = FakeBrain(agent="red-rail")
    ticket = _ready(brain)
    args = dict(
        ticket_id=ticket,
        actor_project="red-probe",
        kind="deployed",
        payload={},
        idempotency_key="k",
        emitted_at=T0.isoformat(),
    )
    with pytest.raises(ToolError) as exc:
        call(brain, "brain_delivery_attest", **{**args, **override})
    assert code_of(exc.value) == code
    assert code in TOOL_ERROR_CODES


def test_attest_without_contract_ticket_or_identity_is_refused() -> None:
    brain = FakeBrain(agent="")
    ticket = brain.add_ticket("red", "red-probe")
    args = dict(
        ticket_id=ticket,
        actor_project="red-probe",
        kind="deployed",
        payload={},
        idempotency_key="k",
        emitted_at=T0.isoformat(),
    )
    with pytest.raises(ToolError) as exc:
        call(brain, "brain_delivery_attest", **args)
    assert code_of(exc.value) == "invalid_issuer"
    brain.agent = "red-rail"
    with pytest.raises(ToolError) as exc:
        call(brain, "brain_delivery_attest", **args)
    assert code_of(exc.value) == "contract_not_found"
    brain.enabled = False
    _ready(brain)
    with pytest.raises(ToolError) as exc:
        call(brain, "brain_delivery_attest", **{**args, "ticket_id": list(brain.tickets)[-1]})
    assert code_of(exc.value) == "delivery_disabled"


def test_list_scopes_order_window_and_cursor() -> None:
    brain = FakeBrain(agent="red-rail")
    ticket = _ready(brain)
    for i in range(5):
        call(
            brain,
            "brain_delivery_attest",
            ticket_id=ticket,
            actor_project="red-probe",
            kind="deployed" if i % 2 else "released",
            payload={"n": i},
            idempotency_key=f"k{i}",
            emitted_at=(T0 + timedelta(minutes=i)).isoformat(),
        )
    page = call(
        brain,
        "brain_delivery_attestation_list",
        actor_project="red-probe",
        issuer_project="red-probe",
        limit=2,
    )
    assert [r["payload"]["n"] for r in page["items"]] == [4, 3] and page["omitted_count"] == 3
    rest = call(
        brain,
        "brain_delivery_attestation_list",
        actor_project="red-probe",
        issuer_project="red-probe",
        limit=2,
        cursor=page["next_cursor"],
    )
    assert [r["payload"]["n"] for r in rest["items"]] == [2, 1]
    by_kind = call(
        brain,
        "brain_delivery_attestation_list",
        actor_project="red-probe",
        ticket_id=ticket,
        kind="released",
    )
    assert [r["payload"]["n"] for r in by_kind["items"]] == [4, 2, 0]
    window = call(
        brain,
        "brain_delivery_attestation_list",
        actor_project="red-probe",
        ticket_id=ticket,
        since=(T0 + timedelta(minutes=1)).isoformat(),
        until=(T0 + timedelta(minutes=3)).isoformat(),
    )
    assert [r["payload"]["n"] for r in window["items"]] == [3, 2, 1]
    for arguments, code in [
        (dict(actor_project="red-probe"), "invalid_scope"),
        (dict(actor_project="red-probe", issuer_project="red"), "not_allowed"),
        (dict(actor_project="red-probe", issuer_project="red-probe", limit=0), "invalid_limit"),
        (
            dict(actor_project="red-probe", ticket_id=ticket, cursor=page["next_cursor"]),
            "invalid_cursor",
        ),
        (dict(actor_project="red-probe", ticket_id=ticket, since="nope"), "invalid_window"),
    ]:
        with pytest.raises(ToolError) as exc:
            call(brain, "brain_delivery_attestation_list", **arguments)
        assert code_of(exc.value) == code, arguments


def test_get_exposes_contract_revision_receipts_and_newest_attestations() -> None:
    brain = FakeBrain(agent="red-rail")
    ticket = _ready(brain)
    brain.integrate(ticket, "d" * 40, issued_at=T0)
    call(
        brain,
        "brain_delivery_attest",
        ticket_id=ticket,
        actor_project="red-probe",
        kind="gate_passed",
        payload={"gate": "unit"},
        idempotency_key="g",
        emitted_at=T0.isoformat(),
    )
    view = call(brain, "brain_delivery_get", ticket_id=ticket, actor_project="red-probe")
    assert view["contract"]["contract_revision"] == 1
    assert view["integration_receipt"]["proof"]["artifact_proofs"][0]["integration_sha"] == "d" * 40
    assert view["attestations"]["items"][0]["digest"] == (
        "7fdcbddf961e6f3efc75d3edfbc198efd9d830200da818c75f6500c35d7e95d7"
    )
    assert {"issuer_project", "issuer_identity", "emitted_at", "recorded_at"} <= set(
        view["attestations"]["items"][0]
    )
