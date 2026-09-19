"""`BrainClient`: one sync call, one stable code per refusal, the transport facts of
2026-09-18 (bearer, tool profile, agent label)."""

import os
from pathlib import Path

import pytest

from rail.brain.client import BrainClient, BrainToolError, BrainUnreachable
from rail.brain.settings import BrainSettings
from rail.private import PrivateFileError
from tests.fake_brain import FakeBrain


def test_call_returns_the_structured_content() -> None:
    brain = FakeBrain(agent="red-rail")
    client = BrainClient.in_memory(brain, agent="red-rail")
    page = client.call("brain_delivery_list", {"actor_project": "red-probe", "limit": 1})
    assert page == {"items": [], "next_cursor": None, "omitted_count": 0}
    assert brain.agent == "red-rail"


def test_the_agent_label_can_be_set_per_call() -> None:
    brain = FakeBrain(agent="red-rail")
    client = BrainClient.in_memory(brain, agent="red-rail")
    client.call("brain_delivery_list", {"actor_project": "red-probe", "limit": 1})
    assert brain.agent == "red-rail"
    client.call("brain_delivery_list", {"actor_project": "red-probe", "limit": 1}, agent="operator")
    assert brain.agent == "operator"


def test_a_refusal_keeps_its_code_and_message() -> None:
    brain = FakeBrain(agent="red-rail")
    client = BrainClient.in_memory(brain, agent="red-rail")
    with pytest.raises(BrainToolError) as exc:
        client.call("brain_delivery_get", {"ticket_id": "nope", "actor_project": "x"})
    assert exc.value.code == "invalid_arguments"
    with pytest.raises(BrainToolError) as exc:
        client.call("brain_delivery_attestation_list", {"actor_project": "x"})
    assert exc.value.code == "invalid_scope" and "invalid_scope" in str(exc.value)


def test_an_unknown_tool_or_transport_failure_is_unreachable() -> None:
    brain = FakeBrain()
    client = BrainClient.in_memory(brain, agent="red-rail")
    with pytest.raises(BrainUnreachable):
        client.call("brain_no_such_tool", {})


def test_http_client_sends_bearer_profile_and_agent_headers() -> None:
    client = BrainClient.http("http://127.0.0.1:8765/mcp", token="t0k", agent="red-rail")
    transport = client.transport_factory("red-rail")
    assert transport.url == "http://127.0.0.1:8765/mcp"
    headers = {k.lower(): v for k, v in transport.headers.items()}
    assert headers["x-brain-tool-profile"] == "native"
    assert headers["x-brain-agent"] == "red-rail"
    other = client.transport_factory("operator")
    assert {k.lower(): v for k, v in other.headers.items()}["x-brain-agent"] == "operator"
    assert "authorization" not in headers  # the bearer travels through `auth`, never a header
    assert transport.auth is not None


def test_http_client_refuses_a_non_loopback_url() -> None:
    with pytest.raises(BrainUnreachable, match="loopback"):
        BrainClient.http("http://brain.example.com/mcp", token="t", agent="a")


def test_settings_read_the_token_from_a_private_file_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "brain-token"
    token_file.write_text("from-file\n")
    token_file.chmod(0o600)
    monkeypatch.setenv("RAIL_BRAIN_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("RAIL_BRAIN_URL", raising=False)
    settings = BrainSettings.from_environment(os.environ)
    assert settings.token == "from-file" and settings.url == "http://127.0.0.1:8765/mcp"
    assert settings.token_file == token_file
    monkeypatch.setenv("MCP_HTTP_TOKEN", "from-env")  # never read: the value is not an env var
    assert BrainSettings.from_environment(os.environ).token == "from-file"


def test_settings_fail_closed_without_a_private_token_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_BRAIN_TOKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(PrivateFileError, match="not found"):
        BrainSettings.from_environment(os.environ)
    loose = tmp_path / "loose"
    loose.write_text("t\n")
    loose.chmod(0o644)
    monkeypatch.setenv("RAIL_BRAIN_TOKEN_FILE", str(loose))
    with pytest.raises(PrivateFileError, match="mode 644"):
        BrainSettings.from_environment(os.environ)
