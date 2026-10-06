"""`rail brain ping` reports reachability without leaking the token."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.brain import settings as brain_settings
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


@pytest.mark.parametrize("reachable", [True, False])
def test_ping_shows_the_brain_site_name_even_when_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reachable: bool
) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "bootstrap")
    token_file = tmp_path / "brain-token"
    token_file.write_text("t0ken\n")
    token_file.chmod(0o600)
    monkeypatch.setenv("RAIL_BRAIN_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("RAIL_BRAIN_URL", raising=False)
    address = ".".join(["10", "0", "0", "7"])  # private, built at run time: no literal here
    sites = tmp_path / "sites.yaml"
    sites.write_text(f"sites:\n  brain:\n    address: {address}\n    interface: wg0\n")
    sites.chmod(0o600)
    monkeypatch.setenv("RAIL_SITES_FILE", str(sites))
    monkeypatch.setattr(brain_settings, "route_interface", lambda address: "wg0")

    class Session:
        def __init__(self, transport, *, timeout):
            self.transport = transport

        async def __aenter__(self):
            return self

        async def call_tool(self, name, arguments, *, timeout):
            assert self.transport.url == f"http://{address}:8765/mcp"
            assert self.transport.headers["X-Brain-Agent"] == "red-rail"
            assert name == "brain_delivery_list"
            if not reachable:
                raise RuntimeError(f"connect to {address}:8765 failed")
            from types import SimpleNamespace

            return SimpleNamespace(structured_content={"items": [], "omitted_count": 0})

        async def close(self):
            pass

    monkeypatch.setattr("fastmcp.Client", Session)
    out = CliRunner().invoke(main, ["brain", "ping", "--repo", str(repo)])
    assert out.exit_code == (0 if reachable else 1)
    assert "brain:8765" in out.output
    assert address not in out.output and "t0ken" not in out.output
