"""The fixture factory must be deterministic: same tree → same commit SHA, run after run."""

from pathlib import Path

from rail import gitrepo
from rail.gates.hygiene import ROSTER_HEADER, table_row
from tests.helpers import commit_all, conforming_tree, init_repo, write_roster


def test_conforming_tree_is_deterministic(tmp_path: Path) -> None:
    a = conforming_tree(tmp_path / "one", "red-alpha", "bootstrap")
    b = conforming_tree(tmp_path / "two", "red-alpha", "bootstrap")
    assert gitrepo.head_sha(a) == gitrepo.head_sha(b)
    assert (a / "rail.yaml").read_text() == (b / "rail.yaml").read_text()


def test_conforming_tree_has_the_structural_floor(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    for rel in ("rail.yaml", "CLAUDE.md", "Makefile", ".claude/settings.json", "README.md"):
        assert (repo / rel).is_file(), rel
    for rel in ("docs/specs", "docs/plans", "docs/adr", "tests"):
        assert (repo / rel).is_dir(), rel
    assert set(gitrepo.remotes(repo)) == {"origin", "gitlab"}
    assert repo == tmp_path / "projects" / "red-beta"


def test_write_roster_lists_projects(tmp_path: Path) -> None:
    write_roster(tmp_path, ["red-alpha", "red-beta"])
    text = (tmp_path / "CLAUDE.md").read_text()
    assert table_row(ROSTER_HEADER) in text and "| red-beta |" in text


def test_commit_all_returns_the_new_head(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "r")
    (repo / "x").write_text("x")
    sha = commit_all(repo, "feat: x")
    assert sha == gitrepo.head_sha(repo)
