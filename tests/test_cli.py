"""`rail check`: exit code is the verdict, JSON is the contract, the tier decides the scope."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.gates import build as build_gates
from rail.ledger import RECEIPTS_DIR, AttestationKind, Contract, Deliverable
from rail.ledger.file import FileLedger
from tests.helpers import conforming_tree, git, write_roster

CLOCK = lambda: datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # noqa: E731


@pytest.fixture(autouse=True)
def _no_real_gitleaks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_gates, "run_gitleaks", lambda repo: (0, "no leaks found"))


def _bootstrap_repo(tmp_path: Path) -> Path:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"])
    FileLedger(repo / RECEIPTS_DIR, clock=CLOCK).contract_set(
        "red-alpha",
        Contract(
            objective="fixture",
            deliverables=[Deliverable(key="main", repository="hawkixs/red-alpha")],
        ),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c1",
    )
    return repo


def _dev_repo(tmp_path: Path) -> Path:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    write_roster(tmp_path, ["red-beta"])
    ledger = FileLedger(repo / RECEIPTS_DIR, clock=CLOCK)
    ledger.contract_set(
        "red-beta",
        Contract(
            objective="fixture",
            deliverables=[Deliverable(key="main", repository="hawkixs/red-beta")],
        ),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c1",
    )
    head = git(repo, "rev-parse", "HEAD")
    ledger.attest(
        "red-beta",
        AttestationKind.REVIEW_VERDICT,
        {"sha": head, "independent": True, "verdict": "approve"},
        issuer="red-rail-reviewer",
        idempotency_key="v1",
    )
    ledger.attest(
        "red-beta", AttestationKind.INTEGRATED, {"sha": head}, issuer="op", idempotency_key="i1"
    )
    return repo


def test_version_and_command_discovery() -> None:
    out = CliRunner().invoke(main, ["--version"])
    assert out.exit_code == 0 and "rail" in out.output
    assert "check" in CliRunner().invoke(main, ["--help"]).output


def test_check_scores_a_bootstrap_repo_against_its_tier(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["check", "--repo", str(_bootstrap_repo(tmp_path))])
    assert out.exit_code == 0, out.output
    assert "tier=bootstrap" in out.output
    assert "design.spec" in out.output and "plan.plan" not in out.output


def test_check_scores_a_dev_repo_through_integrate(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["check", "--repo", str(_dev_repo(tmp_path)), "--json"])
    assert out.exit_code == 0, out.output
    report = json.loads(out.output)
    assert report["project"] == "red-beta" and report["tier"] == "dev" and report["declared"]
    assert report["stages"] == [
        "hygiene",
        "intent",
        "design",
        "plan",
        "build",
        "review",
        "integrate",
    ]
    assert report["passed"] is True
    assert {g["stage"] for g in report["gates"]} == set(report["stages"])
    assert all(
        set(g) == {"stage", "code", "passed", "details", "exception", "skipped"}
        for g in report["gates"]
    )


def test_check_exits_nonzero_and_names_the_failing_gate(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["check", "--repo", str(tmp_path)])
    assert out.exit_code == 1
    assert "FAIL  hygiene.rail_config" in out.output
    assert "tier=bootstrap (undeclared)" in out.output


def test_check_one_stage(tmp_path: Path) -> None:
    out = CliRunner().invoke(
        main, ["check", "design", "--repo", str(_bootstrap_repo(tmp_path)), "--json"]
    )
    assert out.exit_code == 0, out.output
    report = json.loads(out.output)
    assert report["stages"] == ["design"]
    assert [g["code"] for g in report["gates"]] == ["spec"]


def test_check_rejects_a_stage_outside_the_tier_unless_all(tmp_path: Path) -> None:
    repo = _bootstrap_repo(tmp_path)
    out = CliRunner().invoke(main, ["check", "deploy", "--repo", str(repo)])
    assert out.exit_code == 2 and "not applicable at tier bootstrap" in out.output
    out = CliRunner().invoke(main, ["check", "--all", "--repo", str(repo), "--json"])
    assert json.loads(out.output)["stages"][-1] == "learn"
    assert out.exit_code == 1  # the evidence gates fail on a bootstrap fixture, visibly


def test_check_ci_skips_workstation_gates(tmp_path: Path) -> None:
    repo = _bootstrap_repo(tmp_path)
    git(repo, "remote", "remove", "origin")  # no GitHub remote: hygiene.remotes fails on the host
    assert CliRunner().invoke(main, ["check", "--repo", str(repo)]).exit_code == 1
    out = CliRunner().invoke(main, ["check", "--repo", str(repo), "--ci", "--json"])
    assert out.exit_code == 0, out.output
    skipped = [g["code"] for g in json.loads(out.output)["gates"] if g["skipped"]]
    assert skipped == ["remotes", "roster_entry"]


def test_check_shows_declared_exceptions(tmp_path: Path) -> None:
    repo = _dev_repo(tmp_path)
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text()
        + "gates:\n  review.verdict:\n    value: false\n    reason: reviewer arrives in phase 2\n"
    )
    out = CliRunner().invoke(main, ["check", "--repo", str(repo)])
    assert out.exit_code == 0, out.output
    assert "EXC   review.verdict" in out.output and "reviewer arrives in phase 2" in out.output
