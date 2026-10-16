from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from rail.flow import SEGMENTS
from rail.metrics_contract import (
    MAX_BYTES,
    Answer,
    DeliveryBlock,
    DeliveryProjectV2,
    DeliveryWindowV2Model,
    TooLarge,
    encode,
)

FIXTURES = Path(__file__).parents[1] / "contracts" / "rail-metrics-v1"
VALID = ("full.json", "partial.json", "partial-delivery.json", "delivery-only.json", "empty.json")
RESERVED = ("served_at", "source_age_s", "source", "available", "reason", "payload")


def test_zero_offset_zone_is_normalized_before_serialization() -> None:
    at = datetime(2026, 1, 15, tzinfo=timezone(timedelta(0), "GMT"))
    block = DeliveryBlock(
        version="rail-delivery/v1", generated_at=at, windows_days=(7, 30), projects=()
    )
    utc = DeliveryBlock(
        version="rail-delivery/v1",
        generated_at=at.astimezone(UTC),
        windows_days=(7, 30),
        projects=(),
    )
    assert block.generated_at.tzinfo is UTC
    assert encode(Answer(delivery=block)) == encode(Answer(delivery=utc))
    assert block.model_dump(mode="json")["generated_at"] == "2026-01-15T00:00:00+00:00"


@pytest.mark.parametrize(
    "at",
    [datetime(2026, 1, 15), datetime(2026, 1, 15, tzinfo=timezone(timedelta(hours=1)))],
)
def test_naive_and_nonzero_offset_datetimes_are_refused(at: datetime) -> None:
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        DeliveryBlock(
            version="rail-delivery/v1", generated_at=at, windows_days=(7, 30), projects=()
        )


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
    data["autonomy"]["not_measured"] *= MAX_BYTES // 100
    answer = Answer.model_validate_json(json.dumps(data))
    assert MAX_BYTES == 32 * 1024
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


# Pin the original compact bytes as well as lengths: adding flow cannot rewrite history.
EXISTING_BYTES = {
    "full.json": (8470, "d75d1f0ee99a9d4ef7800ff2832c7177bc5f75ef727075fd95994c40b29a3cfe"),
    "partial.json": (7196, "c03877d57a1d572fe09715b00ac211916235c3168658e6a0361c48a01662bbb6"),
    "partial-delivery.json": (
        6771,
        "44c973786475798b62b9fc6c2713d47542c3b947b7974438fb31f1026a639c05",
    ),
    "delivery-only.json": (
        2262,
        "c09804fae4b4b73b71d5109e80e6b05eb4dea172fdce969b18cb1fb8de92cd7c",
    ),
    "delivery-v2.json": (1365, "78911b401b2373957db7aa4f6866bb4fa3a2664a98908f64166fa21cfbe2f922"),
    "empty.json": (2, "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"),
    "unknown-version.json": (
        8470,
        "06154cecbea7a7a4053d06d4d174ddef156de64ec739bb2bdfb33e8f95c4bdc1",
    ),
}
FLOW_BYTES = {
    "flow-only.json": 1154,
    "flow-full.json": 25868,
    "flow-partial.json": 3799,
    "flow-empty.json": 734,
    "flow-unknown-version.json": 1154,
}


def fixture_data(name: str) -> dict[str, Any]:
    """Read only public synthetic evidence, never host configuration."""
    return json.loads((FIXTURES / name).read_bytes())


def test_flow_fixtures_validate_and_round_trip() -> None:
    for name in FLOW_BYTES:
        if name == "flow-unknown-version.json":
            continue
        data = fixture_data(name)
        answer = Answer.model_validate_json(json.dumps(data))
        assert encode(answer) == json.dumps(data, separators=(",", ":")).encode()
        assert answer.flow is not None
    # All sixteen subsets of the four blocks are independently valid.
    data = fixture_data("flow-full.json")
    blocks = tuple(data)
    for mask in range(16):
        selected = {key: data[key] for i, key in enumerate(blocks) if mask & (1 << i)}
        assert (
            encode(Answer.model_validate_json(json.dumps(selected)))
            == json.dumps(selected, separators=(",", ":")).encode()
        )
    for name, (size, digest) in EXISTING_BYTES.items():
        compact = json.dumps(fixture_data(name), separators=(",", ":")).encode()
        assert len(compact) == size
        assert hashlib.sha256(compact).hexdigest() == digest
        if name != "unknown-version.json":
            assert encode(Answer.model_validate_json(compact)) == compact
    empty = Answer.model_validate_json(json.dumps(fixture_data("flow-empty.json"))).flow
    assert empty is not None
    assert empty.repositories[0].merged_prs == 0
    assert empty.repositories[0].carry_rate is None
    assert all(
        p.n == 0 and p.median is None and p.p90 is None
        for p in empty.repositories[0].segments.values()
    )


def test_flow_eleven_repositories_twenty_recent_and_delivery_v2_fit_32k() -> None:
    for name, size in FLOW_BYTES.items():
        compact = json.dumps(fixture_data(name), separators=(",", ":")).encode()
        assert len(compact) == size
        assert len(compact) <= MAX_BYTES
        if name != "flow-unknown-version.json":
            assert len(encode(Answer.model_validate_json(compact))) == size
    answer = Answer.model_validate_json(json.dumps(fixture_data("flow-full.json")))
    assert answer.delivery is not None and answer.autonomy is not None
    assert answer.delivery_v2 is not None and answer.flow is not None
    assert encode(Answer(delivery=answer.delivery, autonomy=answer.autonomy)) == encode(
        Answer.model_validate_json(json.dumps(fixture_data("full.json")))
    )
    assert len(answer.delivery_v2.projects) == len(answer.flow.repositories) == 11
    assert len(answer.flow.recent) == 20
    assert {r.repository.removeprefix("example/") for r in answer.flow.repositories} == {
        p.project for p in answer.autonomy.projects
    }
    assert all(
        value is not None for entry in answer.flow.recent for value in entry.model_dump().values()
    )
    assert all(
        value is not None
        for entry in answer.flow.recent
        for value in entry.segments.model_dump().values()
    )
    # Four heads per repository, with scales 1, 2, 3, 4. Head 2 carries review,
    # head 3 carries judge, head 4 has unknown ledger metadata. See README.
    expected = {
        "flow": (25.0, 40.0, 4),
        "ci_start": (2.5, 4.0, 4),
        "ci": (10.0, 16.0, 4),
        "ci_longest": (7.5, 12.0, 4),
        "review_start": (4.0, 6.0, 3),
        "review": (4.0, 6.0, 2),  # [2, 6]; exclude carried 0 and unknown.
        "judge_wait": (20.0, 32.0, 4),
        "judge": (2.0, 4.0, 3),  # [1, 2, 4]; exclude carried head 3.
        "idle": (14.0, 20.0, 4),  # [5, 10, 18, 20].
        "holder": (30.0, 48.0, 4),
    }
    for repository in answer.flow.repositories:
        assert repository.merged_prs == 4
        assert repository.carry_rate == 0.333  # 1 / 3 known flags, not 1 / 4.
        assert {
            key: (value.median, value.p90, value.n) for key, value in repository.segments.items()
        } == expected
    partial = Answer.model_validate_json(json.dumps(fixture_data("flow-partial.json"))).flow
    assert partial is not None
    assert partial.coverage.missing == ("example/missing",)
    assert partial.coverage.unread_prs == {"example/omitted": 1}
    assert partial.coverage.unread_segments == {
        "example/ledger": {"review_start": 1, "review": 1, "ledger": 1},
        "example/statuses": {"judge_wait": 1, "judge": 1, "idle": 1},
    }
    ledger, _, statuses, undeclared = partial.recent
    assert ledger.carried is None and ledger.rounds is None
    assert ledger.segments.review is None
    assert statuses.segments.judge_wait is None and statuses.segments.idle is None
    assert undeclared.segments.holder is None and undeclared.holder_approx is None
    assert "example/undeclared" not in partial.coverage.unread_segments


def test_flow_strict_fields_order_and_coverage() -> None:
    data = fixture_data("flow-full.json")
    answer = Answer.model_validate_json(json.dumps(data))
    assert answer.flow is not None
    flow = answer.flow
    for node in (
        flow,
        flow.coverage,
        flow.repositories[0],
        flow.repositories[0].segments["flow"],
        flow.recent[0],
        flow.recent[0].segments,
    ):
        assert node.model_config == {
            "extra": "forbid",
            "strict": True,
            "frozen": True,
            "allow_inf_nan": False,
        }
    with pytest.raises(ValidationError, match="frozen"):
        flow.window_days = 7

    mutations: list[tuple[tuple[str | int, ...], object]] = []
    for value in (-1.0, float("inf"), float("-inf"), float("nan"), "1", True):
        mutations.extend(
            [
                (("recent", 0, "segments", "ci"), value),
                (("repositories", 0, "segments", "ci", "median"), value),
                (("repositories", 0, "segments", "ci", "p90"), value),
                (("repositories", 0, "carry_rate"), value),
            ]
        )
    for field in ("window_days",):
        for value in (0, -1, "30", True):
            mutations.append(((field,), value))
    for field in ("number", "rounds"):
        for value in (-1, "1", True, 1.5):
            mutations.append((("recent", 0, field), value))
    mutations.append((("recent", 0, "number"), 0))
    for field in ("merged_prs",):
        for value in (-1, "4", True, 1.5):
            mutations.append((("repositories", 0, field), value))
    for value in (-1, "4", True, 1.5):
        mutations.append((("repositories", 0, "segments", "ci", "n"), value))
    for path in (("generated_at",), ("window_start",), ("recent", 0, "merged_at")):
        for value in ("bad-date", "2026-10-13T12:00:00", "2026-10-13T12:00:00+01:00", 0):
            mutations.append((path, value))
    for value in ("repo", "/repo", "owner/repo/extra", "owner/repo name", "a" * 200 + "/b"):
        mutations.extend(
            [
                (("repositories", 0, "repository"), value),
                (("recent", 0, "repository"), value),
                (("coverage", "missing"), [value]),
                (("coverage", "unread_prs"), {value: 1}),
            ]
        )
    for value in ("a" * 40, "a" * 64, "ABCDEF012345", "abcdef01234g", "abc", "a" * 12 + "\n"):
        mutations.append((("recent", 0, "head_sha"), value))
    for value in ("", "p" * 33, "bad provider", "provider/x", "é", "p\n"):
        mutations.append((("recent", 0, "judge_provider"), value))
    for field in ("carried", "judge_carried", "holder_approx"):
        for value in ("false", 0):
            mutations.append((("recent", 0, field), value))
    for measured in (
        {"median": 1.0, "p90": 2.0, "n": 0},
        {"median": None, "p90": None, "n": 1},
        {"median": 1.0, "p90": None, "n": 1},
        {"median": None, "p90": 1.0, "n": 1},
        {"median": None, "p90": 1.0, "n": 0},
    ):
        mutations.append((("repositories", 0, "segments", "ci"), measured))
    first = data["flow"]["repositories"][0]["repository"]
    for value in (1.1,):
        mutations.append((("repositories", 0, "carry_rate"), value))
    mutations.extend(
        [
            (("repositories",), list(reversed(data["flow"]["repositories"]))),
            (("repositories",), [data["flow"]["repositories"][0]] * 2),
            (("repositories", 0, "segments"), {}),
            (("recent", 0, "segments"), {}),
            (("recent",), list(reversed(data["flow"]["recent"]))),
            (("recent",), [data["flow"]["recent"][0]] * 2),
            (("recent",), data["flow"]["recent"] + [data["flow"]["recent"][-1]]),
            (("recent", 0, "repository"), "example/unread"),
            (("coverage", "missing"), ["example/z", "example/a"]),
            (("coverage", "missing"), ["example/a", "example/a"]),
            (("coverage", "missing"), [first]),
            (("coverage", "unread_prs"), {"example/unread": 1}),
            (("coverage", "unread_prs"), {first: "1"}),
            (("coverage", "unread_prs"), {first: True}),
            (("coverage", "unread_segments"), {"example/unread": {"ledger": 1}}),
            (("coverage", "unread_segments"), {first: {"unknown": 1}}),
            (("coverage", "unread_segments"), {first: {"ledger": -1}}),
            (("coverage", "unread_segments"), {first: {"ci": True}}),
            (("coverage", "unread_segments"), {first: {"ci": "1"}}),
        ]
    )
    for path, value in mutations:
        changed = deepcopy(data)
        target = changed["flow"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(ValidationError):
            Answer.model_validate_json(json.dumps(changed))

    # Accept boundary labels, legitimate zeros, nulls, and descending merge times.
    changed = deepcopy(data)
    changed["flow"]["recent"][0]["judge_provider"] = "P_0-" * 8
    changed["flow"]["recent"][0]["merged_at"] = "2026-10-13T14:00:00+00:00"
    changed["flow"]["recent"][0]["segments"] = {s: 0.0 for s in SEGMENTS}
    changed["flow"]["repositories"][0]["segments"]["flow"] = {"median": 0.0, "p90": 0.0, "n": 1}
    Answer.model_validate_json(json.dumps(changed))
    changed = fixture_data("flow-only.json")
    changed["flow"]["repositories"][0]["repository"] = "Owner_1/Repo.Name-2"
    changed["flow"]["recent"][0]["repository"] = "Owner_1/Repo.Name-2"
    changed["flow"]["coverage"]["unread_segments"] = {}
    Answer.model_validate_json(json.dumps(changed))


def test_flow_unknown_major_and_private_or_relay_fields_are_refused() -> None:
    Answer.model_validate_json(json.dumps(fixture_data("flow-only.json")))
    with pytest.raises(ValidationError):
        Answer.model_validate_json(json.dumps(fixture_data("flow-unknown-version.json")))
    for path in (
        (),
        ("flow",),
        ("flow", "repositories", 0),
        ("flow", "repositories", 0, "segments", "ci"),
        ("flow", "recent", 0),
        ("flow", "recent", 0, "segments"),
        ("flow", "coverage"),
    ):
        for key in (*RESERVED, "run_id", "description", "path", "error", "diagnostics"):
            data = fixture_data("flow-only.json")
            target = data
            for part in path:
                target = target[part]
            target[key] = "untrusted text"
            with pytest.raises(ValidationError):
                Answer.model_validate_json(json.dumps(data))


def test_answer_size_bound_is_32k() -> None:
    assert MAX_BYTES == 32768
    data = fixture_data("flow-full.json")
    # Pad an existing unrestricted prose field; do not bypass validation or the encoder.
    data["autonomy"]["not_measured"][0]["why"] = ""
    base = Answer.model_validate_json(json.dumps(data))
    padding = MAX_BYTES - len(encode(base))
    assert padding > 0
    data["autonomy"]["not_measured"][0]["why"] = "x" * padding
    exact = Answer.model_validate_json(json.dumps(data))
    assert len(encode(exact)) == 32768
    data["autonomy"]["not_measured"][0]["why"] += "x"
    above = Answer.model_validate_json(json.dumps(data))
    with pytest.raises(TooLarge) as refused:
        encode(above)
    assert refused.value.size == 32769
