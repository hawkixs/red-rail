"""The day-0 snapshot of the real ReD projects is committed evidence: its shape is tested,
its content is the drift table of that day and is expected to change."""

import json
from pathlib import Path

AUDITS = Path(__file__).resolve().parents[1] / "docs" / "audits"


def _latest() -> dict:
    snapshots = sorted(AUDITS.glob("*-projects.json"))
    assert snapshots, "no snapshot under docs/audits/ — run `make audit`"
    return json.loads(snapshots[-1].read_text())


def test_snapshot_shape() -> None:
    data = _latest()
    assert data["schema_version"] == 1
    names = [p["name"] for p in data["projects"]]
    assert len(names) >= 20 and names == sorted(names)
    for project in data["projects"]:
        assert {
            "name",
            "declared_tier",
            "tier_used",
            "template_version",
            "stages",
            "passed",
            "applicable",
            "exceptions",
            "gates",
        } <= set(project)
        assert 0 <= project["passed"] <= project["applicable"]


def test_red_rail_is_in_the_snapshot_at_tier_dev() -> None:
    rail = next(p for p in _latest()["projects"] if p["name"] == "red-rail")
    assert rail["declared_tier"] == "dev"
    assert rail["passed"] == rail["applicable"]
