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
from tests.helpers import conforming_tree, git, init_repo, write_roster

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
            deliverables=[
                Deliverable(
                    key="main",
                    repository="hawkixs/red-alpha",
                    no_checks_reason="fixture: no check declared",
                )
            ],
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
            deliverables=[
                Deliverable(
                    key="main",
                    repository="hawkixs/red-beta",
                    no_checks_reason="fixture: no check declared",
                )
            ],
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
        set(g) == {"stage", "code", "passed", "details", "exception", "skipped", "needs"}
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


def test_check_ci_names_what_it_did_not_evaluate(tmp_path: Path) -> None:
    """A skipped gate is not a passed gate: the run names the gates it did not evaluate, in
    text and in JSON, instead of a bare count that reads as all green."""
    repo = _bootstrap_repo(tmp_path)

    out = CliRunner().invoke(main, ["check", "--repo", str(repo), "--ci"])

    assert out.exit_code == 0, out.output
    assert "not evaluated here: hygiene.remotes, hygiene.roster_entry" in out.output
    out = CliRunner().invoke(main, ["check", "--repo", str(repo), "--ci", "--json"])
    assert json.loads(out.output)["not_evaluated"] == ["hygiene.remotes", "hygiene.roster_entry"]


def _brain_dev_repo(tmp_path: Path) -> Path:
    """Tier dev on the brain ledger: under `--ci` the ledger gates, review.verdict among them,
    are not evaluated — CI holds no ledger credential."""
    repo = _dev_repo(tmp_path)
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text()
        + "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n"
    )
    return repo


def test_check_ci_says_the_review_is_not_evaluated_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A green CI job that does not say it skipped the review reads as a reviewed change:
    red-alerts#2 merged an unreviewed head on exactly that green. The run says so, and under
    GitHub Actions it says so as an annotation on the pull request, where the green is read."""
    repo = _brain_dev_repo(tmp_path)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    out = CliRunner().invoke(main, ["check", "--repo", str(repo), "--ci"])

    assert "review not evaluated here" in out.output
    assert "::notice" not in out.output
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    out = CliRunner().invoke(main, ["check", "--repo", str(repo), "--ci"])
    assert "::notice title=Review not evaluated here::" in out.output


def test_check_ci_json_stays_json_under_github_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--json` is the contract for machines: no annotation leaks into it."""
    repo = _brain_dev_repo(tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    out = CliRunner().invoke(main, ["check", "--repo", str(repo), "--ci", "--json"])

    assert "review.verdict" in json.loads(out.output)["not_evaluated"]


def test_check_shows_declared_exceptions(tmp_path: Path) -> None:
    repo = _dev_repo(tmp_path)
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text()
        + "gates:\n  review.verdict:\n    value: false\n    reason: reviewer arrives in phase 2\n"
    )
    out = CliRunner().invoke(main, ["check", "--repo", str(repo)])
    assert out.exit_code == 0, out.output
    assert "EXC   review.verdict" in out.output and "reviewer arrives in phase 2" in out.output


def test_check_tags_a_needed_declaration_and_lists_it(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "bare", remotes=False)
    out = CliRunner().invoke(main, ["check", "build", "--all", "--repo", str(repo)])
    assert out.exit_code == 1
    assert "NEED  build.tests" in out.output and "NEED  build.lint" in out.output
    assert "— needs a declaration: build.tests, build.lint" in out.output
    data = json.loads(
        CliRunner().invoke(main, ["check", "build", "--all", "--repo", str(repo), "--json"]).output
    )
    assert data["needs_declaration"] == ["build.tests", "build.lint"]
    assert data["passed"] is False


def test_check_joins_what_it_did_not_evaluate_and_what_needs_a_declaration(
    tmp_path: Path,
) -> None:
    """One summary line carries both suffixes, joined by `; ` (spec of ticket 2cbbdd22, section
    3): without a manifest, `--ci` skips the workstation gates while `build.*` need `stack:`."""
    repo = init_repo(tmp_path / "bare", remotes=False)
    args = ["check", "--all", "--ci", "--repo", str(repo)]
    data = json.loads(CliRunner().invoke(main, [*args, "--json"]).output)
    skipped, needs = data["not_evaluated"], data["needs_declaration"]
    assert skipped and needs, "the fixture must exercise both suffixes"

    out = CliRunner().invoke(main, args)

    assert out.exit_code == 1
    summary = next(line for line in out.output.splitlines() if line.startswith("passed "))
    assert summary.endswith(
        f" — not evaluated here: {', '.join(skipped)}; needs a declaration: {', '.join(needs)}"
    )


def _verdict_lines(output: str) -> list[str]:
    return [
        line for line in output.splitlines() if line[:4] in ("PASS", "FAIL", "NEED", "SKIP", "EXC ")
    ]


@pytest.mark.parametrize("ci", [False, True])
def test_without_a_manifest_only_the_manifest_gate_names_it(tmp_path: Path, ci: bool) -> None:
    """Ticket 2cbbdd22, criterion 1 of 513e109b — red runs the same check itself."""
    repo = init_repo(tmp_path / "bare", remotes=False)
    args = ["check", "--all", "--repo", str(repo)] + (["--ci"] if ci else [])
    out = CliRunner().invoke(main, args)
    assert out.exit_code == 1
    lines = _verdict_lines(out.output)
    naming = [line for line in lines if "rail.yaml" in line]
    assert len(naming) == 1 and "hygiene.rail_config" in naming[0], naming
    for line in lines:
        if line.startswith("NEED"):
            assert "needs `" in line, line
    assert {line.split()[1] for line in lines if line.startswith("NEED")} == {
        "build.tests",
        "build.lint",
    }
    ledger_gates = (
        "intent.contract",
        "review.verdict",
        "integrate.receipt",
        "release.released",
        "deploy.deployed",
        "observe.drill",
        "learn.fulfilled",
    )
    for gate_id in ledger_gates:  # review focus 3: the file default runs under --ci too
        line = next(line for line in lines if line.split()[1] == gate_id)
        assert line.startswith("FAIL") and "docs/receipts" in line, line


def test_a_manifest_never_yields_a_need(tmp_path: Path) -> None:
    """Review focus 4: with a manifest present nothing changes. `--all` so build and evidence
    gates run too — the only ones that ever NEED — not just the bootstrap tier's own three."""
    repo = _bootstrap_repo(tmp_path)
    out = CliRunner().invoke(main, ["check", "--all", "--repo", str(repo), "--json"])
    data = json.loads(out.output)
    assert data["needs_declaration"] == []
    assert all(g["needs"] is None for g in data["gates"])


def test_attest_writes_nothing_without_a_manifest(tmp_path: Path) -> None:
    """`rail attest` opens the ledger before anything else: without a manifest that write path
    stays fail-closed (item 1 of the final fix wave), so no receipt is ever written."""
    repo = init_repo(tmp_path / "bare", remotes=False)
    out = CliRunner().invoke(
        main, ["attest", "integrated", "--repo", str(repo), "--data", f"sha={'0' * 40}"]
    )
    assert out.exit_code != 0
    assert not (repo / RECEIPTS_DIR).exists()
