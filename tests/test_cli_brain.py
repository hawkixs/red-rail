"""`rail brain ping` reports reachability without leaking the token."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from tests.helpers import conforming_tree


def test_ping_fails_closed_without_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    monkeypatch.setenv("RAIL_BRAIN_TOKEN_FILE", str(tmp_path / "none"))
    out = CliRunner().invoke(main, ["brain", "ping", "--repo", str(repo)])
    assert out.exit_code == 1 and "not found" in out.output


def test_ping_refuses_a_remote_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    token_file = tmp_path / "brain-token"
    token_file.write_text("t0ken\n")
    token_file.chmod(0o600)
    monkeypatch.setenv("RAIL_BRAIN_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("RAIL_BRAIN_URL", "http://brain.example.com/mcp")
    out = CliRunner().invoke(main, ["brain", "ping", "--repo", str(repo)])
    assert out.exit_code == 1 and "loopback" in out.output and "t0ken" not in out.output
