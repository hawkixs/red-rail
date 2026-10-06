"""`BrainClient`: one sync call, one stable code per refusal, the transport facts of
2026-09-18 (bearer, tool profile, agent label)."""

import os
import traceback
from pathlib import Path

import pytest

from rail.brain.client import BrainClient, BrainToolError, BrainUnreachable
from rail.brain.settings import BrainSettings
from rail.private import PrivateFileError
from tests.fake_brain import FakeBrain

# A private address built at run time: this public repository never carries one as a literal
# (tests/test_no_machine_address.py).
V4 = ".".join(["10", "0", "0", "7"])
V6 = ":".join(["fd7a", "", "7"])


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


# Built at run time: this public repository never carries a literal outside the documentation
# ranges (tests/test_no_machine_address.py).
_REFUSED_HOSTS = [
    "brain.example.com",  # a name may resolve anywhere
    ".".join(["1", "1", "1", "1"]),  # a public address
    ".".join(["100", "64", "0", "1"]),  # shared address space (a tailnet), not private
    ".".join(["169", "254", "1", "1"]),  # link-local
    "[" + ":".join(["fe80", "", "1"]) + "]",
    ".".join(["0", "0", "0", "0"]),
    "192.0.2.10",  # documentation: `is_private` admits it, the rail does not
    "[" + ":".join(["2002", "808", "808", "", "1"]) + "]",  # 6to4 of a public address
    "[::ffff:" + ".".join(["1", "1", "1", "1"]) + "]",  # IPv4-mapped public address
]


@pytest.mark.parametrize("host", _REFUSED_HOSTS)
def test_http_client_refuses_anything_but_loopback_or_a_private_address(host: str) -> None:
    with pytest.raises(BrainUnreachable, match="loopback or a private address") as exc:
        BrainClient.http(f"http://{host}:8765/mcp", token="t", agent="a")
    assert host.strip("[]") not in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8765/mcp",
        "http://[::1]:8765/mcp",
        f"http://{V4}:8765/mcp",
        f"http://[{V6}]:8765/mcp",
    ],
)
def test_http_client_accepts_loopback_and_a_private_literal(url: str) -> None:
    assert BrainClient.http(url, token="t", agent="a").transport_factory("a").url == url


def _sites(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sites.yaml"
    path.write_text(text)
    path.chmod(0o600)
    return path


def _token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token_file = tmp_path / "brain-token"
    token_file.write_text("t\n")
    token_file.chmod(0o600)
    monkeypatch.setenv("RAIL_BRAIN_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("RAIL_BRAIN_URL", raising=False)


def test_a_declared_brain_site_gives_the_url_and_its_name_is_what_is_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token(tmp_path, monkeypatch)
    sites = _sites(tmp_path, f"sites:\n  brain:\n    address: {V4}\n")
    monkeypatch.setenv("RAIL_SITES_FILE", str(sites))
    settings = BrainSettings.from_environment(os.environ)
    assert settings.url == f"http://{V4}:8765/mcp"
    assert settings.shown == "http://brain:8765/mcp"
    assert settings.redact(f"connect to {V4}:8765 failed") == "connect to brain:8765 failed"


def test_a_sites_file_without_brain_keeps_the_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token(tmp_path, monkeypatch)
    sites = _sites(tmp_path, f"sites:\n  red-monitor:\n    address: {V4}\n")
    monkeypatch.setenv("RAIL_SITES_FILE", str(sites))
    settings = BrainSettings.from_environment(os.environ)
    assert settings.url == "http://127.0.0.1:8765/mcp" and settings.shown == settings.url


def test_no_sites_file_keeps_the_loopback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token(tmp_path, monkeypatch)
    monkeypatch.setenv("RAIL_SITES_FILE", str(tmp_path / "absent" / "sites.yaml"))
    assert BrainSettings.from_environment(os.environ).url == "http://127.0.0.1:8765/mcp"


def test_a_broken_sites_file_is_a_refusal_not_a_silent_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token(tmp_path, monkeypatch)
    sites = _sites(tmp_path, f"sites:\n  brain:\n    address: {V4}\n")
    sites.chmod(0o644)
    monkeypatch.setenv("RAIL_SITES_FILE", str(sites))
    with pytest.raises(PrivateFileError, match="site brain"):
        BrainSettings.from_environment(os.environ)


def test_a_declared_ipv6_brain_site_is_bracketed_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token(tmp_path, monkeypatch)
    address = V6
    sites = _sites(tmp_path, f'sites:\n  brain:\n    address: "{address}"\n')
    monkeypatch.setenv("RAIL_SITES_FILE", str(sites))
    settings = BrainSettings.from_environment(os.environ)
    client = BrainClient.from_settings(settings, agent="red-rail")
    assert client.transport_factory("red-rail").url == f"http://[{address}]:8765/mcp"
    assert settings.shown == "http://brain:8765/mcp"
    assert settings.redact(f"connect to [{address}]:8765 failed") == (
        "connect to brain:8765 failed"
    )


def test_an_explicit_url_wins_even_when_the_sites_file_is_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token(tmp_path, monkeypatch)
    sites = _sites(tmp_path, "not a valid sites file")
    sites.chmod(0o644)
    monkeypatch.setenv("RAIL_SITES_FILE", str(sites))
    monkeypatch.setenv("RAIL_BRAIN_URL", "http://localhost:8765/mcp")
    settings = BrainSettings.from_environment(os.environ)
    assert settings.url == settings.shown == "http://localhost:8765/mcp"
    assert settings.redact(f"connect to {V4}") == f"connect to {V4}"


@pytest.mark.parametrize(
    "text",
    [
        f'sites:\n  brain:\n    address: "{V4}/24"\n',
        f'sites:\n  brain:\n    address: "{V4}"\n    broken: [\n',
        f'sites:\n  other:\n    address: "{V4}/24"\n',
    ],
)
def test_an_invalid_sites_file_is_refused_without_its_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    _token(tmp_path, monkeypatch)
    monkeypatch.setenv("RAIL_SITES_FILE", str(_sites(tmp_path, text)))
    with pytest.raises(PrivateFileError, match="site brain") as caught:
        BrainSettings.from_environment(os.environ)
    assert V4 not in str(caught.value)
    assert V4 not in "".join(traceback.format_exception(caught.value))


def test_a_dangling_sites_symlink_is_refused_instead_of_using_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token(tmp_path, monkeypatch)
    link = tmp_path / "sites.yaml"
    link.symlink_to(tmp_path / "absent.yaml")
    monkeypatch.setenv("RAIL_SITES_FILE", str(link))
    with pytest.raises(PrivateFileError, match="site brain.*symlink"):
        BrainSettings.from_environment(os.environ)


def test_a_client_from_settings_redacts_a_transport_failure_and_its_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "RAIL_SITES_FILE", str(_sites(tmp_path, f"sites:\n  brain:\n    address: {V4}\n"))
    )
    settings = BrainSettings.from_environment(os.environ)
    client = BrainClient.from_settings(settings, agent="red-rail")

    async def fail(name: str, arguments: dict, agent: str) -> dict:
        raise RuntimeError(f"connect to {settings.url} failed")

    monkeypatch.setattr(client, "_call", fail)
    with pytest.raises(BrainUnreachable, match="http://brain:8765/mcp") as caught:
        client.call("brain_delivery_list", {})
    assert V4 not in "".join(traceback.format_exception(caught.value))


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


def test_a_refusal_whose_message_says_not_found_is_still_a_refusal() -> None:
    """Measured on the live brain (2026-09-19): `contract_not_found: delivery contract was not
    found` was classified as unreachable because the message contains "not found"."""
    brain = FakeBrain(agent="red-rail")
    ticket = brain.add_ticket("red", "red-probe")
    client = BrainClient.in_memory(brain, agent="red-rail")
    with pytest.raises(BrainToolError) as exc:
        client.call("brain_delivery_get", {"ticket_id": ticket, "actor_project": "red-probe"})
    assert exc.value.code == "contract_not_found"
    with pytest.raises(BrainToolError) as exc:
        client.call(
            "brain_delivery_get",
            {"ticket_id": "00000000-0000-0000-0000-000000000000", "actor_project": "x"},
        )
    assert exc.value.code == "ticket_not_found"


def test_a_brain_site_outside_the_private_ranges_is_a_settings_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tailnet address is plausible in the sites file: it must be refused as a settings
    error the ledger turns into a `LedgerError`, never crash a gate with a transport error."""
    _token(tmp_path, monkeypatch)
    shared = ".".join(["100", "64", "0", "1"])
    sites = _sites(tmp_path, f"sites:\n  brain:\n    address: {shared}\n")
    monkeypatch.setenv("RAIL_SITES_FILE", str(sites))
    with pytest.raises(PrivateFileError, match="site brain: the address is neither") as caught:
        BrainSettings.from_environment(os.environ)
    assert shared not in "".join(traceback.format_exception(caught.value))


def test_the_environment_names_a_loopback_url_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token(tmp_path, monkeypatch)
    monkeypatch.setenv("RAIL_BRAIN_URL", f"http://{V4}:8765/mcp")
    with pytest.raises(PrivateFileError, match="loopback URL only") as caught:
        BrainSettings.from_environment(os.environ)
    assert V4 not in str(caught.value)
