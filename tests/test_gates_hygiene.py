"""Hygiene: the floor every tier stands on."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rail.gates import Stage
from rail.gates.hygiene import (
    DOMAIN_PLACEHOLDER,
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
from tests.helpers import conforming_tree, git, init_repo, write_manifest, write_roster

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
        "mirrors",
    ]
    assert {g.code for g in GATES if g.scope == "workstation"} == {"remotes", "roster_entry"}
    assert {g.code for g in GATES if g.scope == "ledger"} == {"mirrors"}
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


def test_remotes_need_github_and_only_a_declared_mirror(tmp_path: Path) -> None:
    """ReD is GitHub only (decision 30acbbde): a lone GitHub remote passes; a mirror is required
    only where the manifest declares one, and then it must exist."""
    assert remotes(conforming_tree(tmp_path, "red-alpha", "bootstrap")).passed  # both: fine
    lonely = init_repo(tmp_path / "red-lonely", remotes=False)
    git(lonely, "remote", "add", "origin", "git@github.com:hawkixs/red-lonely.git")
    result = remotes(lonely)
    assert result.passed and result.details == "github.com"
    write_manifest(
        lonely,
        project="red-lonely",
        tier="bootstrap",
        gates={"hygiene.mirror_host": ("gitlab.hawkixs.local", "this project keeps its mirror")},
    )
    result = remotes(lonely)
    assert not result.passed and "no remote on gitlab.hawkixs.local" in result.details
    git(lonely, "remote", "add", "gitlab", "ssh://git@gitlab.hawkixs.local:2222/red/red-lonely.git")
    assert remotes(lonely).details == "github.com + gitlab.hawkixs.local"
    assert "not a git repository" in remotes(tmp_path / "nowhere").details


def test_roster_entry_reads_the_red_root(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"])
    assert roster_entry(repo).passed
    write_roster(tmp_path, ["red-other"])
    result = roster_entry(repo)
    assert not result.passed and "red-alpha" in result.details


def test_roster_entry_folds_dotdot_paths_to_the_root(tmp_path: Path) -> None:
    """`rail audit ..` hands the gate `<repo>/../<project>`: the roster must still be found."""
    conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-other"])
    via_dotdot = tmp_path / "projects" / "red-alpha" / ".." / "red-alpha"
    result = roster_entry(via_dotdot)
    assert not result.passed and "red-alpha has no row" in result.details


def test_roster_entry_is_standalone_without_a_roster(tmp_path: Path) -> None:
    """Standalone always says why (decision 4): no ReD root above `projects/`, or no
    `projects/` at all."""
    result = roster_entry(conforming_tree(tmp_path, "red-alpha", "bootstrap"))
    assert result.passed and result.details == f"standalone: no CLAUDE.md in {tmp_path.name}"
    lonely = init_repo(tmp_path / "elsewhere" / "red-lonely", remotes=False)
    result = roster_entry(lonely)
    assert result.passed and result.details == "standalone: no `projects` ancestor"


def _nested_worktree(repo: Path, nested: str) -> Path:
    """A checkout nested inside the project, carrying its own manifest as a worktree does."""
    worktree = repo / nested
    worktree.mkdir(parents=True)
    write_manifest(worktree, project=repo.name, tier="bootstrap")
    return worktree


@pytest.mark.parametrize("nested", [".claude/worktrees/w", ".worktrees/w"])
def test_roster_entry_finds_the_root_from_a_nested_worktree(tmp_path: Path, nested: str) -> None:
    """cdb725e4: two levels up from `projects/x/.claude/worktrees/w` is `projects/x/.claude`,
    so every worktree run of the gate reported standalone. The walk goes up to the root."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    worktree = _nested_worktree(repo, nested)
    write_roster(tmp_path, ["red-alpha"])
    result = roster_entry(worktree)
    assert result.passed and result.details == f"listed in {tmp_path.name}/CLAUDE.md"


def test_roster_entry_walks_up_from_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`rail check` with no `--repo` hands the gate `.`: the default invocation, from a nested
    worktree, reaches the root (review focus 1; success criterion 9 in miniature)."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    worktree = _nested_worktree(repo, ".claude/worktrees/w")
    write_roster(tmp_path, ["red-alpha"])
    monkeypatch.chdir(worktree)
    result = roster_entry(Path("."))
    assert result.passed and result.details == f"listed in {tmp_path.name}/CLAUDE.md"


def test_roster_entry_ignores_the_related_projects_table(tmp_path: Path) -> None:
    """A row counts only inside the identity table's block: a project named in a "Related
    projects" table further down is not in the roster (decision 2)."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-other"])
    with (tmp_path / "CLAUDE.md").open("a") as root:
        root.write(
            "\n## Related projects\n\n| Project | Path | Relation |\n|---|---|---|\n"
            "| red-alpha | `projects/red-alpha` | a sibling |\n"
        )
    result = roster_entry(repo)
    assert not result.passed
    assert result.details == f"red-alpha has no row in {tmp_path.name}/CLAUDE.md"


def test_roster_entry_fails_when_the_root_header_drifted(tmp_path: Path) -> None:
    """A `CLAUDE.md` above `projects/` without the identity header is a root whose format
    moved (the French header, here): a FAIL that names it, never a silent standalone."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (tmp_path / "CLAUDE.md").write_text(
        "# ReD\n\n| Projet | Domaine | Statut reel | Sante | Cle brain |\n|---|---|---|---|---|\n"
        "| red-alpha | Infra | fixture | OK | `red-alpha` |\n"
    )
    result = roster_entry(repo)
    assert not result.passed
    assert result.details == (
        f"roster header not found in {tmp_path.name}/CLAUDE.md: the root format drifted"
    )


def test_roster_entry_fails_on_a_row_still_holding_the_domain_placeholder(
    tmp_path: Path,
) -> None:
    """The row `rail new` prints was pasted unedited: the domain is the operator's call."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    write_roster(tmp_path, ["red-alpha"], domain=DOMAIN_PLACEHOLDER)
    result = roster_entry(repo)
    assert not result.passed
    assert result.details == (
        f"red-alpha's row in {tmp_path.name}/CLAUDE.md still holds `<domain>`: fill in its domain"
    )


@pytest.mark.parametrize("broken", ["undecodable", "unreadable"])
def test_roster_entry_fails_closed_on_an_unreadable_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    """The ReD position's `CLAUDE.md` cannot be read: the roster cannot be checked, so the
    gate fails and names the file. It never raises (decision 4)."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    root_md = tmp_path / "CLAUDE.md"
    if broken == "undecodable":
        root_md.write_bytes(b"# ReD\n\xff\xfe\n")
    else:
        write_roster(tmp_path, ["red-alpha"])
        read_text = Path.read_text

        def refuse(self: Path, *args: object, **kwargs: object) -> str:
            if self == root_md:
                raise PermissionError(13, "Permission denied")
            return read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", refuse)
    result = roster_entry(repo)
    assert not result.passed
    assert result.details.startswith(f"cannot read {tmp_path.name}/CLAUDE.md (")
    assert result.details.endswith("): the roster cannot be checked")


def test_roster_entry_skips_an_unreadable_unrelated_ancestor(tmp_path: Path) -> None:
    """Only the ReD position may fail the gate on a read error. The sub-project's own
    `CLAUDE.md`, met on the way up from a nested worktree, is passed over."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    worktree = _nested_worktree(repo, ".claude/worktrees/w")
    (repo / "CLAUDE.md").write_bytes(b"\xff\xfe")
    write_roster(tmp_path, ["red-alpha"])
    result = roster_entry(worktree)
    assert result.passed and result.details == f"listed in {tmp_path.name}/CLAUDE.md"


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_roster_entry_reads_a_crlf_or_compact_header(tmp_path: Path, newline: str) -> None:
    """Review focus 2: the header is recognised by its cells, not by its spacing, and a
    CRLF file is still read line by line."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    text = (
        "# ReD\n\n|Project|Domain|What it is|Brain key|\n|:---|---|---|---:|\n"
        "|red-alpha|Infra|fixture|`red-alpha`|\n"
    )
    (tmp_path / "CLAUDE.md").write_bytes(text.replace("\n", newline).encode())
    result = roster_entry(repo)
    assert result.passed and result.details == f"listed in {tmp_path.name}/CLAUDE.md"


def test_roster_entry_takes_the_nearest_projects_ancestor(tmp_path: Path) -> None:
    """Review focus 5: under `<x>/projects/outer/projects/red-alpha`, the ReD position is
    `outer`, the parent of the nearest `projects`, and the standalone message names it."""
    outer = tmp_path / "projects" / "outer"
    repo = conforming_tree(outer, "red-alpha", "bootstrap")
    result = roster_entry(repo)
    assert result.passed and result.details == "standalone: no CLAUDE.md in outer"
    write_roster(outer, ["red-alpha"])
    assert roster_entry(repo).details == "listed in outer/CLAUDE.md"


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


def test_rail_config_rejects_unknown_gate_keys(tmp_path: Path) -> None:
    """Review finding: a typo in `gates:` must be an error, not a silent no-op."""
    from rail.gates.hygiene import rail_config

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text()
        + "gates:\n  hygiene.recipts:\n    value: false\n    reason: typo\n"
    )
    result = rail_config(repo)
    assert not result.passed and "hygiene.recipts" in result.details


def test_claude_md_reads_the_target_after_make_flags(tmp_path: Path) -> None:
    """Review finding: `make -j4 ci` and `make -C . ci` name the target `ci`."""
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (repo / "CLAUDE.md").write_text(
        '`red-alpha`\n\n```bash\nmake -j4 ci\nmake -C . test\nmake check ARGS="x #y"\n```\n'
    )
    result = claude_md(repo)
    assert result.passed, result.details


def test_make_targets_reads_multi_target_lines(tmp_path: Path) -> None:
    """Sweep finding: `build ci: deps` declares two targets."""
    from rail.gates.hygiene import make_targets

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    (repo / "Makefile").write_text(".PHONY: build ci\nbuild ci: deps\n\t@true\ndeps:\n\t@true\n")
    assert make_targets(repo) == {"build", "ci", "deps"}
    assert task_runner(repo).passed


def test_mirrors_is_vacuous_on_the_file_ledger(tmp_path: Path) -> None:
    from rail.gates.hygiene import mirrors

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    result = mirrors(repo)
    assert result.passed and "file ledger" in result.details


def test_mirrors_reports_a_receipt_absent_from_the_shared_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail.gates import hygiene
    from rail.ledger import RECEIPTS_DIR, AttestationKind
    from rail.ledger.file import FileLedger

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    manifest = (repo / "rail.yaml").read_text()
    (repo / "rail.yaml").write_text(
        manifest.replace(
            "ledger: file\n", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n"
        )
    )
    local = FileLedger(repo / RECEIPTS_DIR)
    kept = local.attest(
        "red-alpha", AttestationKind.DEPLOYED, {"sha": "a" * 40}, issuer="op", idempotency_key="d1"
    )
    stray = local.attest(
        "red-alpha", AttestationKind.RELEASED, {"version": "1"}, issuer="op", idempotency_key="r1"
    )

    class SharedLedger:
        def list(self, project, *, kind=None, attestation=None):
            return [kept]

    monkeypatch.setattr(hygiene, "open_ledger", lambda repo: SharedLedger())
    result = hygiene.mirrors(repo)
    assert not result.passed
    assert "mirror without attestation" in result.details and stray.digest[7:19] in result.details

    class DownLedger:
        def list(self, project, *, kind=None, attestation=None):
            from rail.ledger import LedgerError

            raise LedgerError("brain unreachable: down")

    monkeypatch.setattr(hygiene, "open_ledger", lambda repo: DownLedger())
    result = hygiene.mirrors(repo)
    assert not result.passed and "brain unreachable" in result.details


def test_mirrors_ignores_milestone_receipts_kept_from_the_file_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured on red-rail (2026-09-19): the phase-1 `integrated` receipts have no row in
    brain because milestones are brain's receipts, never attested by the rail — they are
    history, not drift."""
    from rail.gates import hygiene
    from rail.ledger import RECEIPTS_DIR, AttestationKind
    from rail.ledger.file import FileLedger

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    manifest = (repo / "rail.yaml").read_text()
    (repo / "rail.yaml").write_text(
        manifest.replace(
            "ledger: file\n", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n"
        )
    )
    local = FileLedger(repo / RECEIPTS_DIR)
    local.attest(
        "red-alpha",
        AttestationKind.INTEGRATED,
        {"sha": "a" * 40},
        issuer="op",
        idempotency_key="i1",
    )
    local.attest(
        "red-alpha", AttestationKind.FULFILLED, {"sha": "a" * 40}, issuer="op", idempotency_key="f1"
    )

    class EmptySharedLedger:
        def list(self, project, *, kind=None, attestation=None):
            return []

    monkeypatch.setattr(hygiene, "open_ledger", lambda repo: EmptySharedLedger())
    result = hygiene.mirrors(repo)
    assert result.passed and "2 milestone receipt(s)" in result.details


def _brain_manifest(repo: Path) -> None:
    text = (repo / "rail.yaml").read_text()
    (repo / "rail.yaml").write_text(
        text.replace(
            "ledger: file\n", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n"
        )
    )


class _EmptyShared:
    def list(self, project, *, kind=None, attestation=None):
        return []


def test_mirrors_fails_while_an_attestation_waits_in_the_spool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail.gates import hygiene
    from rail.ledger import AttestationKind
    from rail.ledger.file import FileLedger
    from rail.ledger.spool import spool_directory

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    _brain_manifest(repo)
    monkeypatch.setattr(hygiene, "open_ledger", lambda repo: _EmptyShared())
    assert hygiene.mirrors(repo).passed
    FileLedger(spool_directory("red-alpha")).attest(
        "red-alpha", AttestationKind.DEPLOYED, {"sha": "a" * 40}, issuer="op", idempotency_key="d1"
    )
    result = hygiene.mirrors(repo)
    assert not result.passed
    assert "1 attestation(s) waiting in the spool" in result.details
    assert "rail ledger replay" in result.details


def test_mirrors_fails_closed_on_a_tampered_spool_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail.gates import hygiene
    from rail.ledger import AttestationKind
    from rail.ledger.file import FileLedger
    from rail.ledger.spool import spool_directory

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    _brain_manifest(repo)
    monkeypatch.setattr(hygiene, "open_ledger", lambda repo: _EmptyShared())
    spool = FileLedger(spool_directory("red-alpha"))
    record = spool.attest(
        "red-alpha", AttestationKind.DEPLOYED, {"sha": "a" * 40}, issuer="op", idempotency_key="d1"
    )
    path = spool.path_of(record)
    path.write_text(path.read_text().replace("a" * 40, "b" * 40))
    result = hygiene.mirrors(repo)
    assert not result.passed and path.name in result.details


def test_mirrors_fails_closed_when_the_spool_path_cannot_be_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`spool_directory` calls `Path.expanduser()`, which raises `RuntimeError` when no home
    directory can be found (M4): the gate reports it, it never raises."""
    from rail.gates import hygiene

    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    _brain_manifest(repo)
    monkeypatch.setattr(hygiene, "open_ledger", lambda repo: _EmptyShared())

    def boom(project: str) -> Path:
        raise RuntimeError("could not determine home directory")

    monkeypatch.setattr(hygiene, "spool_directory", boom)
    result = hygiene.mirrors(repo)
    assert not result.passed and "spool:" in result.details
