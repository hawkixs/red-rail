import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rail.metrics_contract import MAX_BYTES, Answer, TooLarge, encode

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
