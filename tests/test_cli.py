"""`rail check`: exit code is the verdict, JSON is the contract."""

import json
from pathlib import Path

from click.testing import CliRunner

from rail.cli import main
from tests.helpers import conforming_tree, write_roster

HYGIENE_CODES = {
    "rail_config",
    "docs_layout",
    "claude_md",
    "task_runner",
    "settings",
    "remotes",
    "roster_entry",
    "receipts",
}


def _conforming_repo(tmp_path: Path) -> Path:
    write_roster(tmp_path, ["red-alpha"])
    return conforming_tree(tmp_path, "red-alpha", "bootstrap")


def test_version_is_printed() -> None:
    out = CliRunner().invoke(main, ["--version"])
    assert out.exit_code == 0
    assert "rail" in out.output


def test_check_exits_zero_on_conforming_repo(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["check", "--repo", str(_conforming_repo(tmp_path))])
    assert out.exit_code == 0, out.output
    assert "PASS" in out.output


def test_check_exits_nonzero_when_a_gate_fails(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["check", "--repo", str(tmp_path)])
    assert out.exit_code == 1
    assert "FAIL" in out.output


def test_check_json_is_machine_readable(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["check", "--repo", str(tmp_path), "--json"])
    assert out.exit_code == 1
    payload = json.loads(out.output)
    assert payload["passed"] is False
    assert {g["code"] for g in payload["gates"]} == HYGIENE_CODES
    assert all(
        set(g) == {"stage", "code", "passed", "details", "exception", "skipped"}
        for g in payload["gates"]
    )
