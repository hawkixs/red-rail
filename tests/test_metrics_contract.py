import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from rail.metrics_contract import (
    MAX_BYTES,
    Answer,
    DeliveryProjectV2,
    DeliveryWindowV2Model,
    TooLarge,
    encode,
)

FIXTURES = Path(__file__).parents[1] / "contracts" / "rail-metrics-v1"
VALID = ("full.json", "partial.json", "partial-delivery.json", "delivery-only.json", "empty.json")
RESERVED = ("served_at", "source_age_s", "source", "available", "reason", "payload")


@pytest.mark.parametrize("name", VALID)
def test_fixtures_validate_and_round_trip_compact_bytes(name: str) -> None:
    raw = (FIXTURES / name).read_bytes()
    answer = Answer.model_validate_json(raw)
    compact = json.dumps(json.loads(raw), separators=(",", ":")).encode()
    assert encode(answer) == compact


def test_full_fixture_has_contract_size() -> None:
    assert len(encode(Answer.model_validate_json((FIXTURES / "full.json").read_bytes()))) == 8470


def test_partial_delivery_excludes_unread_project_everywhere() -> None:
    answer = Answer.model_validate_json((FIXTURES / "partial-delivery.json").read_bytes())
    assert answer.delivery is not None
    assert "red-probe" not in [project.project for project in answer.delivery.projects]
    assert answer.autonomy is not None
    assert "red-probe" not in [project.project for project in answer.autonomy.projects]
    assert answer.autonomy.coverage.missing == ("red-probe",)
    assert answer.autonomy.total is None


def test_unknown_version_is_refused() -> None:
    raw = (FIXTURES / "unknown-version.json").read_bytes()
    with pytest.raises(ValidationError):
        Answer.model_validate_json(raw)


@pytest.mark.parametrize("block", ("delivery", "autonomy"))
@pytest.mark.parametrize("key", RESERVED)
def test_reserved_relay_keys_are_refused(block: str, key: str) -> None:
    data = json.loads((FIXTURES / "delivery-only.json").read_bytes())
    if block == "autonomy":
        data = json.loads((FIXTURES / "empty.json").read_bytes())
        data["autonomy"] = json.loads((FIXTURES / "full.json").read_bytes())["autonomy"]
    data[block][key] = None
    with pytest.raises(ValidationError):
        Answer.model_validate_json(json.dumps(data))


def test_ratio_is_strict_and_bounded_but_quotient_may_exceed_one() -> None:
    data = json.loads((FIXTURES / "delivery-only.json").read_bytes())
    data["delivery"]["projects"][0]["d7"]["change_failure_rate"] = "0.5"
    with pytest.raises(ValidationError):
        Answer.model_validate_json(json.dumps(data))
    data["delivery"]["projects"][0]["d7"]["change_failure_rate"] = 1.1
    with pytest.raises(ValidationError):
        Answer.model_validate_json(json.dumps(data))
    data["delivery"]["projects"][0]["d7"]["change_failure_rate"] = 0.25
    data["delivery"]["projects"][0]["d7"]["deployment_frequency_per_week"] = 1.1
    Answer.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("block", ("delivery", "autonomy"))
def test_projects_must_be_sorted(block: str) -> None:
    data = json.loads((FIXTURES / "full.json").read_bytes())
    data[block]["projects"].reverse()
    with pytest.raises(ValidationError):
        Answer.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("missing,total", ((["red-probe"], "present"), ([], None)))
def test_total_must_match_coverage(missing: list[str], total: object) -> None:
    data = json.loads((FIXTURES / "full.json").read_bytes())
    data["autonomy"]["coverage"]["missing"] = missing
    data["autonomy"]["total"] = (
        json.loads((FIXTURES / "full.json").read_bytes())["autonomy"]["total"]
        if total == "present"
        else None
    )
    with pytest.raises(ValidationError):
        Answer.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "block,path",
    (
        ("delivery", ("generated_at",)),
        ("autonomy", ("generated_at",)),
        ("autonomy", ("since",)),
        ("autonomy", ("projects", 0, "d30", "window_start")),
    ),
)
def test_naive_datetime_is_refused(block: str, path: tuple[str | int, ...]) -> None:
    data = json.loads((FIXTURES / "full.json").read_bytes())
    target = data[block]
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = "2026-10-13T15:00:00"
    with pytest.raises(ValidationError):
        Answer.model_validate_json(json.dumps(data))


def test_encode_refuses_payload_over_limit() -> None:
    data = json.loads((FIXTURES / "full.json").read_bytes())
    data["delivery"]["projects"][0]["project"] = "a" * 64
    # Repeated contract text forces the full serialization beyond the cap.
    data["autonomy"]["not_measured"] *= 30
    answer = Answer.model_validate_json(json.dumps(data))
    assert MAX_BYTES == 12 * 1024
    with pytest.raises(TooLarge):
        encode(answer)


def v2_block() -> dict[str, Any]:
    return {
        "version": "rail-delivery/v2",
        "generated_at": "2026-10-14T12:00:00+00:00",
        "windows_days": [7, 30],
        "projects": [
            {
                "project": "red-probe",
                "status": "measured",
                "target": "private-compose",
                "d7": v2_window(),
                "d30": v2_window(),
            }
        ],
    }


def v2_window() -> dict[str, int | float | None]:
    return {
        "changes": 1,
        "redeployments": 1,
        "deployment_frequency_per_week": 1.0,
        "lead_time_for_changes_hours": None,
        "release_to_deploy_hours": 24.0,
        "change_failure_rate": 1.0,
        "failed_changes": 1,
        "time_to_restore_minutes": 1.18,
        "open_incidents": 0,
        "drill_recovery_time_minutes": None,
    }


V2_WINDOW = DeliveryWindowV2Model.model_validate(v2_window())


def test_v2_beside_v1_preserves_existing_fixture_bytes() -> None:
    data = json.loads((FIXTURES / "delivery-only.json").read_bytes())
    v1 = encode(Answer.model_validate_json(json.dumps(data)))
    data["delivery_v2"] = v2_block()
    answer = Answer.model_validate_json(json.dumps(data))
    assert answer.delivery_v2 is not None
    assert encode(Answer(delivery=answer.delivery)) == v1
    assert set(json.loads(encode(answer))) == {"delivery", "delivery_v2"}


@pytest.mark.parametrize(
    "status,target,d7,d30",
    [
        ("no_deployment", "private-compose", None, None),
        ("no_deployment", None, V2_WINDOW, None),
        ("no_deployment", None, None, V2_WINDOW),
        ("measured", None, V2_WINDOW, V2_WINDOW),
        ("measured", "private-compose", None, V2_WINDOW),
        ("measured", "private-compose", V2_WINDOW, None),
    ],
)
def test_v2_refuses_inconsistent_deployment_status(
    status: str,
    target: str | None,
    d7: DeliveryWindowV2Model | None,
    d30: DeliveryWindowV2Model | None,
) -> None:
    with pytest.raises(
        ValidationError, match="deployment status must match target and both windows"
    ) as refused:
        DeliveryProjectV2.model_validate(
            {"project": "red-probe", "status": status, "target": target, "d7": d7, "d30": d30}
        )
    assert len(refused.value.errors()) == 1
    assert refused.value.errors()[0]["loc"] == ()


@pytest.mark.parametrize(
    "status,target,d7,d30",
    [
        ("no_deployment", None, None, None),
        ("measured", "private-compose", V2_WINDOW, V2_WINDOW),
    ],
)
def test_v2_accepts_consistent_deployment_status(
    status: str,
    target: str | None,
    d7: DeliveryWindowV2Model | None,
    d30: DeliveryWindowV2Model | None,
) -> None:
    project = DeliveryProjectV2.model_validate(
        {"project": "red-probe", "status": status, "target": target, "d7": d7, "d30": d30}
    )
    assert (project.status, project.target, project.d7, project.d30) == (status, target, d7, d30)


def test_v2_accepts_no_deployment_and_requires_sorted_projects() -> None:
    block = v2_block()
    block["projects"].append(
        {"project": "red-zeta", "status": "no_deployment", "target": None, "d7": None, "d30": None}
    )
    answer = Answer.model_validate_json(json.dumps({"delivery_v2": block}))
    assert answer.delivery_v2 is not None
    assert answer.delivery_v2.projects[1].status == "no_deployment"
    block["projects"].reverse()
    with pytest.raises(ValidationError):
        Answer.model_validate_json(json.dumps({"delivery_v2": block}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("changes", -1),
        ("redeployments", True),
        ("failed_changes", 1.5),
        ("open_incidents", "1"),
        ("deployment_frequency_per_week", None),
        ("lead_time_for_changes_hours", -1.0),
        ("release_to_deploy_hours", "24"),
        ("change_failure_rate", 1.1),
        ("time_to_restore_minutes", -1.0),
        ("drill_recovery_time_minutes", -1.0),
        ("unexpected", 1),
    ],
)
def test_v2_window_fields_are_strict_and_bounded(field: str, value: object) -> None:
    block = v2_block()
    block["projects"][0]["d7"][field] = value
    with pytest.raises(ValidationError):
        Answer.model_validate_json(json.dumps({"delivery_v2": block}))


def test_eleven_repository_answer_with_both_delivery_versions_fits() -> None:
    data = json.loads((FIXTURES / "full.json").read_bytes())
    measured = {p["project"]: p for p in data["delivery"]["projects"]}
    block = v2_block()
    block["projects"] = [
        {
            "project": project["project"],
            "status": "measured" if project["project"] in measured else "no_deployment",
            "target": measured[project["project"]]["target"]
            if project["project"] in measured
            else None,
            "d7": v2_window() if project["project"] in measured else None,
            "d30": v2_window() if project["project"] in measured else None,
        }
        for project in data["autonomy"]["projects"]
    ]
    data["delivery_v2"] = block
    assert len(encode(Answer.model_validate_json(json.dumps(data)))) <= MAX_BYTES


def test_delivery_v2_example_validates_and_round_trips() -> None:
    raw = (FIXTURES / "delivery-v2.json").read_bytes()
    answer = Answer.model_validate_json(raw)
    assert answer.delivery_v2 is not None
    assert encode(answer) == json.dumps(json.loads(raw), separators=(",", ":")).encode()
