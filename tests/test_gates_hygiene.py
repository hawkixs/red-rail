"""Hygiene: the floor every tier stands on."""

import json
from datetime import UTC, datetime
from pathlib import Path

from rail.gates import Stage
from rail.gates.hygiene import (
    GATES,
    claude_md,
    receipts,
    remotes,
    roster_entry,
    settings,
    task_runner,
)
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from tests.helpers import conforming_tree, git, init_repo, write_roster

CLOCK = lambda: datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # noqa: E731


def test_registry_order_and_scopes() -> None:
    assert [g.code for g in GATES] == [
        "rail_config",
        "docs_layout",
        "claude_md",
        "task_runner",
        "settings",
        "remotes",
        "roster_entry",
        "receipts",
    ]
    assert {g.code for g in GATES if g.scope == "workstation"} == {"remotes", "roster_entry"}
    assert all(g.stage is Stage.HYGIENE for g in GATES)


def test_claude_md_passes_on_a_conforming_tree(tmp_path: Path) -> None:
    result = claude_md(conforming_tree(tmp_path, "red-alpha", "bootstrap"))
    assert result.passed, result.details


def test_claude_md_requires_the_brain_key(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (repo / "CLAUDE.md").write_text("# red-alpha\n")
    result = claude_md(repo)
    assert not result.passed and "brain key" in result.details


def test_claude_md_rejects_commands_that_cannot_run(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (repo / "CLAUDE.md").write_text("`red-alpha`\n\n```bash\nmake nope\n```\n")
    result = claude_md(repo)
    assert not result.passed and "make nope" in result.details


def test_task_runner_wants_a_makefile_with_a_ci_target(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    assert task_runner(repo).passed
    (repo / "Makefile").write_text("test:\n\t@true\n")
    assert "ci" in task_runner(repo).details and not task_runner(repo).passed
    (repo / "Makefile").unlink()
    assert "Makefile" in task_runner(repo).details


def test_settings_wants_a_permission_allowlist(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    assert settings(repo).passed
    (repo / ".claude" / "settings.json").write_text("{not json")
    assert "invalid JSON" in settings(repo).details
    (repo / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": []}}))
    assert not settings(repo).passed
    (repo / ".claude" / "settings.json").unlink()
    assert not settings(repo).passed


def test_remotes_need_github_and_the_mirror(tmp_path: Path) -> None:
    assert remotes(conforming_tree(tmp_path, "red-alpha", "bootstrap")).passed
    lonely = init_repo(tmp_path / "lonely", remotes=False)
    git(lonely, "remote", "add", "origin", "git@github.com:hawkixs/lonely.git")
    result = remotes(lonely)
    assert not result.passed and "gitlab.hawkixs.local" in result.details
    assert "not a git repository" in remotes(tmp_path / "nowhere").details


def test_roster_entry_reads_the_red_root(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"])
    assert roster_entry(repo).passed
    write_roster(tmp_path, ["red-other"])
    result = roster_entry(repo)
    assert not result.passed and "red-alpha" in result.details


def test_roster_entry_is_standalone_without_a_roster(tmp_path: Path) -> None:
    result = roster_entry(conforming_tree(tmp_path, "red-alpha", "bootstrap"))
    assert result.passed and "standalone" in result.details


def test_receipts_pass_when_absent_or_well_formed(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    assert receipts(repo).details == "no receipts"
    FileLedger(repo / RECEIPTS_DIR, clock=CLOCK).attest(
        "red-alpha",
        AttestationKind.INTEGRATED,
        {"sha": "a" * 40},
        issuer="op",
        idempotency_key="i1",
    )
    result = receipts(repo)
    assert result.passed and "1 well-formed" in result.details


def test_receipts_report_tampering_misnaming_and_duplicates(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    ledger = FileLedger(repo / RECEIPTS_DIR, clock=CLOCK)
    ledger.attest(
        "red-alpha",
        AttestationKind.INTEGRATED,
        {"sha": "a" * 40},
        issuer="op",
        idempotency_key="i1",
    )
    path = next((repo / RECEIPTS_DIR).glob("*.json"))
    raw = json.loads(path.read_text())
    raw["payload"]["data"]["sha"] = "b" * 40
    path.write_text(json.dumps(raw))
    assert "digest" in receipts(repo).details
    path.rename(path.with_name("renamed.json"))
    assert "expected name" in receipts(repo).details
    (repo / RECEIPTS_DIR / "junk.json").write_text("{")
    assert "junk.json" in receipts(repo).details


def test_receipts_reject_a_reused_idempotency_key(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    ledger = FileLedger(repo / RECEIPTS_DIR, clock=CLOCK)
    first = ledger.attest(
        "red-alpha",
        AttestationKind.INTEGRATED,
        {"sha": "a" * 40},
        issuer="op",
        idempotency_key="i1",
    )
    later = FileLedger(tmp_path / "elsewhere", clock=lambda: datetime(2026, 9, 16, tzinfo=UTC))
    second = later.attest(
        "red-alpha",
        AttestationKind.INTEGRATED,
        {"sha": "b" * 40},
        issuer="op",
        idempotency_key="i1",
    )
    src = next((tmp_path / "elsewhere").glob("*.json"))
    src.rename(repo / RECEIPTS_DIR / src.name)
    result = receipts(repo)
    assert not result.passed and "idempotency key" in result.details
    assert first.digest != second.digest
