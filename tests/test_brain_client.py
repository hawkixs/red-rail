"""`BrainClient`: one sync call, one stable code per refusal, the transport facts of
2026-09-18 (bearer, tool profile, agent label)."""

import asyncio
import gc
import os
import subprocess
import sys
import threading
import traceback
import weakref
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_address
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from rail.brain import client as brain_client
from rail.brain import settings as brain_settings
from rail.brain.client import BrainClient, BrainToolError, BrainUnreachable
from rail.brain.settings import BrainSettings
from rail.deploy.sites import Address
from rail.private import PrivateFileError
from tests.fake_brain import FakeBrain

# A private address built at run time: this public repository never carries one as a literal
# (tests/test_no_machine_address.py).
V4 = ".".join(["10", "0", "0", "7"])
V6 = ":".join(["fd7a", "", "7"])


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[Client]:
    opened = []

    class CountingClient(Client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            opened.append(self)

    monkeypatch.setattr("fastmcp.Client", CountingClient)
    return opened


def _counting_client() -> tuple[BrainClient, list[str]]:
    brain = FakeBrain()
    labels = []

    def factory(label: str):
        labels.append(label)
        return brain.server

    return BrainClient(factory, "red-rail"), labels


def _list(client: BrainClient, *, agent: str | None = None) -> dict:
    return client.call("brain_delivery_list", {"actor_project": "red-probe"}, agent=agent)


def test_calls_reuse_one_connected_session_per_label(sessions: list[Client]) -> None:
    client, labels = _counting_client()
    for _ in range(3):
        assert _list(client)["items"] == []
    assert labels == ["red-rail"]
    _list(client, agent="operator")
    _list(client)
    assert labels == ["red-rail", "operator"]
    assert len(sessions) == 2 and all(session.is_connected() for session in sessions)


@pytest.mark.parametrize("failure", ["transport", "timeout", "unreadable", "unknown_tool"])
def test_an_unreachable_session_is_closed_and_only_the_next_call_reconnects(
    monkeypatch: pytest.MonkeyPatch, sessions: list[Client], failure: str
) -> None:
    client, labels = _counting_client()
    _list(client)
    _list(client, agent="operator")
    original = Client.call_tool

    async def fail(self, *args, **kwargs):
        if failure == "transport":
            raise ConnectionError("transport lost")
        if failure == "timeout":
            await asyncio.sleep(0.3)
            return await original(self, *args, **kwargs)
        if failure == "unreadable":
            return SimpleNamespace(structured_content=None)
        return await original(self, "brain_no_such_tool", {})

    monkeypatch.setattr(Client, "call_tool", fail)
    client.timeout = 0.1
    with pytest.raises(BrainUnreachable):
        _list(client)
    assert labels == ["red-rail", "operator"]  # no replay of the failed call
    assert not sessions[0].is_connected()
    assert sessions[1].is_connected()
    monkeypatch.setattr(Client, "call_tool", original)
    client.timeout = 10
    _list(client)
    assert labels == ["red-rail", "operator", "red-rail"]


def test_a_tool_refusal_keeps_the_connected_session(sessions: list[Client]) -> None:
    client, labels = _counting_client()
    _list(client)
    with pytest.raises(BrainToolError):
        client.call("brain_delivery_get", {"ticket_id": "nope", "actor_project": "x"})
    assert sessions[0].is_connected()
    _list(client)
    assert labels == ["red-rail"]


def test_close_disconnects_all_sessions_and_a_later_call_reopens(sessions: list[Client]) -> None:
    client, labels = _counting_client()
    _list(client)
    _list(client, agent="operator")
    client.close()
    client.close()
    assert all(not session.is_connected() for session in sessions)
    _list(client)
    assert labels == ["red-rail", "operator", "red-rail"]
    assert sessions[-1].is_connected()


def test_context_manager_closes_sessions_on_exception(sessions: list[Client]) -> None:
    client, _ = _counting_client()
    with pytest.raises(ValueError, match="caller failed"), client as entered:
        assert entered is client
        _list(client)
        raise ValueError("caller failed")
    assert not sessions[0].is_connected()


@pytest.fixture
def http_transports(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    server = FastMCP("counting-brain")
    opened = []

    @server.tool()
    async def brain_delivery_list(actor_project: str) -> dict:
        return {"items": [], "next_cursor": None, "omitted_count": 0}

    def factory(url, *, auth, headers, httpx_client_factory):
        opened.append((url, auth, headers["X-Brain-Agent"]))
        return server

    monkeypatch.setattr("fastmcp.client.transports.StreamableHttpTransport", factory)
    return opened


def test_repeated_http_clients_share_sessions_after_being_dropped(
    tmp_path: Path,
    http_transports: list[tuple[str, str, str]],
    sessions: list[Client],
    _close_brain_clients: list[BrainClient],
) -> None:
    threads_before = threading.active_count()
    settings = BrainSettings(
        url="http://127.0.0.1:8765/mcp", token="t", token_file=tmp_path / "token"
    )
    for _ in range(50):
        client = BrainClient.from_settings(settings, agent="red-rail")
        assert _list(client)["items"] == []
        assert _list(client, agent="operator")["items"] == []
        reference = weakref.ref(client)
        _close_brain_clients.remove(client)
        del client
        gc.collect()
        assert reference() is None
    assert [label for _, _, label in http_transports] == ["red-rail", "operator"]
    assert len(sessions) == 2 and all(session.is_connected() for session in sessions)
    assert threading.active_count() <= threads_before + 1


def test_close_all_disconnects_every_pool_is_idempotent_and_allows_reopening(
    http_transports: list[tuple[str, str, str]], sessions: list[Client]
) -> None:
    threads_before = threading.active_count()
    first = BrainClient.http("http://127.0.0.1:8765/mcp", token="t", agent="red-rail")
    second = BrainClient.http("http://127.0.0.1:8766/mcp", token="t", agent="red-rail")
    _list(first)
    _list(first, agent="operator")
    _list(second)
    brain_client.close_all()
    brain_client.close_all()
    assert len(sessions) == 3 and all(not session.is_connected() for session in sessions)
    assert threading.active_count() <= threads_before
    _list(first)
    another = BrainClient.http("http://127.0.0.1:8765/mcp", token="t", agent="red-rail")
    _list(another)
    assert len(http_transports) == 4
    assert sessions[-1].is_connected()


def test_closing_an_http_instance_does_not_disconnect_the_shared_sessions(
    http_transports: list[tuple[str, str, str]], sessions: list[Client]
) -> None:
    first = BrainClient.http("http://127.0.0.1:8765/mcp", token="t", agent="red-rail")
    second = BrainClient.http("http://127.0.0.1:8765/mcp", token="t", agent="operator")
    with first:
        _list(first)
        _list(second)
    second.close()
    _list(first)
    _list(second)
    assert len(http_transports) == 2
    assert all(session.is_connected() for session in sessions)


def test_http_transport_identity_includes_url_and_token(
    http_transports: list[tuple[str, str, str]],
) -> None:
    for port, token in [(8765, "t"), (8765, "other"), (8766, "t"), (8765, "t")]:
        _list(BrainClient.http(f"http://127.0.0.1:{port}/mcp", token=token, agent="red-rail"))
    assert len(http_transports) == 3


def test_concurrent_http_clients_open_only_one_session_per_label(
    http_transports: list[tuple[str, str, str]], sessions: list[Client]
) -> None:
    def call(index: int) -> dict:
        client = BrainClient.http("http://127.0.0.1:8765/mcp", token="t", agent="red-rail")
        return _list(client, agent="operator" if index % 2 else "red-rail")

    with ThreadPoolExecutor(max_workers=8) as workers:
        assert all(result["items"] == [] for result in workers.map(call, range(50)))
    assert sorted(label for _, _, label in http_transports) == ["operator", "red-rail"]
    assert len(sessions) == 2


def test_process_exit_closes_shared_sessions_with_one_registered_cleanup(tmp_path: Path) -> None:
    closed = tmp_path / "closed-sessions"
    script = dedent("""\
        import atexit
        import gc
        import sys
        from pathlib import Path
        from fastmcp import Client, FastMCP
        from fastmcp.client import transports

        registered = []
        original_register = atexit.register
        def register(callback, *args, **kwargs):
            registered.append(callback)
            return original_register(callback, *args, **kwargs)
        atexit.register = register
        from rail.brain import client

        server = FastMCP("exit-cleanup")
        @server.tool()
        async def probe() -> dict:
            return {"ok": True}

        opened = []
        def factory(url, *, auth, headers, httpx_client_factory):
            opened.append(headers["X-Brain-Agent"])
            return server
        transports.StreamableHttpTransport = factory

        class CountingClient(Client):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.label = opened[-1]

            async def close(self):
                await super().close()
                with Path(sys.argv[1]).open("a") as output:
                    output.write(self.label + "\\n")

        import fastmcp
        fastmcp.Client = CountingClient
        for _ in range(50):
            caller = client.BrainClient.http(
                "http://127.0.0.1:8765/mcp", token="t", agent="red-rail"
            )
            assert caller.call("probe", {}) == {"ok": True}
            assert caller.call("probe", {}, agent="operator") == {"ok": True}
            del caller
            gc.collect()
        assert opened == ["red-rail", "operator"]
        assert registered.count(client.close_all) == 1
        assert not Path(sys.argv[1]).exists()
        """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(closed)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert closed.read_text().splitlines() == ["red-rail", "operator"]


def test_dropping_a_private_client_closes_its_sessions_and_thread(
    sessions: list[Client], _close_brain_clients: list[BrainClient]
) -> None:
    threads_before = threading.active_count()
    client, _ = _counting_client()
    _list(client)
    reference = weakref.ref(client)
    _close_brain_clients.remove(client)
    del client
    gc.collect()
    assert reference() is None
    assert not sessions[0].is_connected()
    assert threading.active_count() <= threads_before


def test_failed_session_enter_is_not_stored_or_closed_as_a_connected_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            events.append("enter")
            raise ConnectionError("enter failed")

        async def close(self):
            events.append("close")

    monkeypatch.setattr("fastmcp.Client", FailingClient)
    client, labels = _counting_client()
    for _ in range(2):
        with pytest.raises(BrainUnreachable, match="enter failed"):
            _list(client)
    client.close()
    assert events == ["enter", "enter"]
    assert labels == ["red-rail", "red-rail"]


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
    client.call("brain_delivery_list", {"actor_project": "red-probe", "limit": 1})
    assert brain.agent == "red-rail"


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


def test_http_transport_ignores_environment_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.20:8080")
    monkeypatch.setenv("ALL_PROXY", "http://192.0.2.20:8080")
    client = BrainClient.http("http://localhost:8765/mcp", token="t", agent="a")
    transport = client.transport_factory("a")
    assert transport.httpx_client_factory is not None

    async def inspect() -> None:
        timeout = httpx.Timeout(7)
        auth = httpx.BasicAuth("user", "password")
        async with transport.httpx_client_factory(
            headers={"X-Probe": "present"}, timeout=timeout, auth=auth, follow_redirects=True
        ) as http_client:
            assert http_client.trust_env is False
            # off, as in the MCP SDK: its transports follow redirects within the origin only
            assert http_client.follow_redirects is False
            assert http_client.headers["X-Probe"] == "present"
            assert http_client.timeout == timeout
            assert http_client.auth is auth

    asyncio.run(inspect())


def test_http_client_redacts_a_guard_refusal_before_creating_the_transport() -> None:
    def refuse() -> None:
        raise PrivateFileError(f"no route to {V4}")

    client = BrainClient.http(
        f"http://{V4}:8765/mcp",
        token="t",
        agent="a",
        guard=refuse,
        redact=lambda text: text.replace(V4, "brain"),
    )

    def unexpected_transport(agent: str) -> None:
        pytest.fail("a refused guard must prevent transport creation")

    client.transport_factory = unexpected_transport
    with pytest.raises(BrainUnreachable, match="no route to brain") as caught:
        client.call("brain_delivery_list", {})
    assert caught.value.__suppress_context__
    assert V4 not in "".join(traceback.format_exception(caught.value))


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
    sites = _sites(tmp_path, f"sites:\n  brain:\n    address: {V4}\n    interface: wg0\n")
    monkeypatch.setenv("RAIL_SITES_FILE", str(sites))

    def route_of(address: Address) -> str:
        assert str(address) == V4
        return "wg0"

    settings = BrainSettings.from_environment(os.environ, route_of=route_of)
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
    sites = _sites(tmp_path, f'sites:\n  brain:\n    address: "{address}"\n    interface: wg0\n')
    monkeypatch.setenv("RAIL_SITES_FILE", str(sites))
    settings = BrainSettings.from_environment(os.environ, route_of=lambda address: "wg0")
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_transports: list[tuple[str, str, str]],
) -> None:
    _token(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "RAIL_SITES_FILE",
        str(_sites(tmp_path, f"sites:\n  brain:\n    address: {V4}\n    interface: wg0\n")),
    )
    settings = BrainSettings.from_environment(os.environ, route_of=lambda address: "wg0")
    client = BrainClient.from_settings(settings, agent="red-rail")

    async def fail(self, *args, **kwargs):
        raise RuntimeError(f"connect to {settings.url} failed")

    monkeypatch.setattr(Client, "call_tool", fail)
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


@pytest.mark.parametrize("address", [V4, V6])
def test_a_private_brain_site_must_declare_its_tunnel_interface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    _token(tmp_path, monkeypatch)
    path = _sites(tmp_path, f'sites:\n  brain:\n    address: "{address}"\n')
    monkeypatch.setenv("RAIL_SITES_FILE", str(path))

    def unexpected_route(address: Address) -> str:
        pytest.fail("a missing declaration must be refused before querying the kernel")

    with pytest.raises(PrivateFileError) as caught:
        BrainSettings.from_environment(os.environ, route_of=unexpected_route)
    assert str(caught.value) == (
        "site brain: declare `interface`, the one the kernel must route its address through "
        f"(the tunnel's), in {path}"
    )
    assert address not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("address", [V4, V6])
@pytest.mark.parametrize("actual", ["eth0", None])
def test_a_private_brain_site_refuses_a_route_outside_its_declared_interface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str, actual: str | None
) -> None:
    _token(tmp_path, monkeypatch)
    path = _sites(tmp_path, f'sites:\n  brain:\n    address: "{address}"\n    interface: wg0\n')
    monkeypatch.setenv("RAIL_SITES_FILE", str(path))
    calls = []

    def route_of(destination: Address) -> str | None:
        calls.append(destination)
        return actual

    with pytest.raises(PrivateFileError) as caught:
        BrainSettings.from_environment(os.environ, route_of=route_of)
    assert calls == [ip_address(address)]
    assert str(caught.value) == (
        "site brain: the kernel gives no route to its address"
        if actual is None
        else "site brain: the kernel routes its address through eth0, not the declared wg0"
    )
    assert address not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("source", ["default", "environment", "site"])
def test_loopback_never_queries_the_kernel_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    _token(tmp_path, monkeypatch)
    loopback = ".".join(["127", "0", "0", "1"])
    if source == "environment":
        monkeypatch.setenv("RAIL_BRAIN_URL", f"http://{loopback}:8765/mcp")
    elif source == "site":
        path = _sites(tmp_path, f"sites:\n  brain:\n    address: {loopback}\n")
        monkeypatch.setenv("RAIL_SITES_FILE", str(path))

    def unexpected_route(address: Address) -> str:
        pytest.fail("loopback does not carry the bearer over a remote interface")

    settings = BrainSettings.from_environment(os.environ, route_of=unexpected_route)
    assert settings.url == f"http://{loopback}:8765/mcp"
    settings.guard()
    client = BrainClient.from_settings(settings, agent="red-rail")
    client.transport_factory = lambda agent: FakeBrain().server
    assert client.call("brain_delivery_list", {"actor_project": "red-probe"})["items"] == []


@pytest.mark.parametrize("actual", ["eth0", None])
def test_each_call_checks_the_route_again_before_creating_the_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actual: str | None
) -> None:
    _token(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "RAIL_SITES_FILE",
        str(_sites(tmp_path, f"sites:\n  brain:\n    address: {V4}\n    interface: wg0\n")),
    )
    routes = iter(["wg0", "wg0", actual])
    destinations = []

    def route_of(address: Address) -> str | None:
        destinations.append(address)
        return next(routes)

    settings = BrainSettings.from_environment(os.environ, route_of=route_of)
    client = BrainClient.from_settings(settings, agent="red-rail")
    transports = []
    brain = FakeBrain()

    def transport(agent: str) -> object:
        transports.append(agent)
        return brain.server

    client.transport_factory = transport
    assert client.call("brain_delivery_list", {"actor_project": "red-probe"})["items"] == []
    with pytest.raises(BrainUnreachable) as caught:
        client.call("brain_delivery_list", {"actor_project": "red-probe"})
    assert str(caught.value) == (
        "brain_delivery_list: site brain: the kernel gives no route to its address"
        if actual is None
        else "brain_delivery_list: site brain: the kernel routes its address through eth0, "
        "not the declared wg0"
    )
    assert destinations == [ip_address(V4)] * 3
    assert transports == ["red-rail"]
    assert V4 not in "".join(traceback.format_exception(caught.value))


def test_route_interface_queries_the_ipv4_table_for_a_mapped_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapped = ip_address("::ffff:" + V4)

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert command == ["ip", "-o", "route", "get", V4]
        return subprocess.CompletedProcess(command, 0, f"{V4} dev wg0 src {V4}\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert brain_settings.route_interface(mapped) == "wg0"


@pytest.mark.parametrize(
    ("address", "output", "interface"),
    [
        (V4, f"{V4} via 192.0.2.1 dev eth0 src 192.0.2.2 uid 1000 \\\n    cache", "eth0"),
        (V4, f"local {V4} dev lo table local src {V4} uid 1000 \\\n    cache", "lo"),
        (
            V6,
            f"{V6} from :: via {':'.join(['fe80', '', '1'])} dev wg0 proto static "
            f"src {V6} metric 1024 pref medium",
            "wg0",
        ),
    ],
)
def test_route_interface_parses_realistic_kernel_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    address: str,
    output: str,
    interface: str,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, output, ""),
    )
    assert brain_settings.route_interface(ip_address(address)) == interface
    _token(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "RAIL_SITES_FILE",
        str(_sites(tmp_path, f'sites:\n  brain:\n    address: "{address}"\n    interface: wg0\n')),
    )
    if interface == "wg0":
        BrainSettings.from_environment(os.environ)
    else:
        with pytest.raises(PrivateFileError, match=f"through {interface}, not the declared wg0"):
            BrainSettings.from_environment(os.environ)


@pytest.mark.parametrize("address", ["192.0.2.10", "2001:db8::10"])
def test_route_interface_asks_the_kernel_with_a_bounded_command(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert command == ["ip", "-o", "route", "get", address]
        assert kwargs == {"capture_output": True, "text": True, "timeout": 2, "check": False}
        return subprocess.CompletedProcess(command, 0, f"{address} dev wg0 src {address}\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert brain_settings.route_interface(ip_address(address)) == "wg0"


@pytest.mark.parametrize(
    ("returncode", "output"),
    [(1, "192.0.2.10 dev wg0"), (0, "192.0.2.10 via 192.0.2.1"), (0, "dev"), (0, "")],
)
def test_route_interface_returns_none_without_a_successful_route(
    monkeypatch: pytest.MonkeyPatch, returncode: int, output: str
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode, output, ""),
    )
    assert brain_settings.route_interface(ip_address("192.0.2.10")) is None


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("ip"),
        subprocess.TimeoutExpired("ip", 2),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        ValueError("invalid route output"),
    ],
)
def test_route_interface_returns_none_when_the_kernel_query_cannot_run(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        raise failure

    monkeypatch.setattr(subprocess, "run", run)
    assert brain_settings.route_interface(ip_address("192.0.2.10")) is None


def _local_http_server() -> tuple[object, int]:
    """A one-route HTTP server on the loopback, for the socket-binding tests (no network)."""
    import http.server
    import threading as _threading

    class Ok(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ok)
    server.daemon_threads = True
    _threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def test_the_http_client_binds_its_sockets_to_the_declared_interface() -> None:
    """Independent review F-85-5: a route checked before the call cannot stop a tunnel lost
    during it. The sockets themselves are bound to the interface (SO_BINDTODEVICE), so a lost
    tunnel fails the connection instead of sending the bearer through the default gateway."""
    import httpx

    server, port = _local_http_server()
    try:

        async def get(interface: str) -> int:
            client = BrainClient.http(
                f"http://127.0.0.1:{port}/mcp", token="t", agent="a", interface=interface
            )
            factory = client.transport_factory("a").httpx_client_factory
            async with factory(headers=None, timeout=httpx.Timeout(3)) as http_client:
                return (await http_client.get(f"http://127.0.0.1:{port}/")).status_code

        assert asyncio.run(get("lo")) == 200  # bound to the interface the route uses
        with pytest.raises(httpx.ConnectError):
            asyncio.run(get("nosuchif0"))  # the interface is gone: no other way out
    finally:
        server.shutdown()


def test_without_an_interface_the_sockets_are_not_bound() -> None:
    client = BrainClient.http("http://localhost:8765/mcp", token="t", agent="a")
    factory = client.transport_factory("a").httpx_client_factory

    async def options() -> object:
        async with factory() as http_client:
            return http_client._transport._pool._socket_options

    assert asyncio.run(options()) is None


def test_settings_hand_the_declared_interface_to_the_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "RAIL_SITES_FILE",
        str(_sites(tmp_path, f"sites:\n  brain:\n    address: {V4}\n    interface: wg0\n")),
    )
    settings = BrainSettings.from_environment(os.environ, route_of=lambda address: "wg0")
    client = BrainClient.from_settings(settings, agent="red-rail")
    factory = client.transport_factory("red-rail").httpx_client_factory

    async def options() -> object:
        async with factory() as http_client:
            return http_client._transport._pool._socket_options

    import socket

    assert asyncio.run(options()) == [(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"wg0")]


def test_a_hung_call_says_it_timed_out() -> None:
    """The pool's wait used to fire first with an empty `TimeoutError`, so a spooled
    attestation carried an empty cause (review finding)."""
    client = BrainClient.in_memory(FakeBrain(), agent="red-rail")
    client.timeout = 0.2

    async def hang(*args: object) -> dict:
        await asyncio.sleep(30)
        return {}

    client._pool._call = hang  # type: ignore[method-assign]
    try:
        with pytest.raises(BrainUnreachable, match=r"brain_delivery_list: no answer within 0\.2 s"):
            client.call("brain_delivery_list", {})
    finally:
        client.close()


def test_closing_a_pool_from_its_own_loop_thread_does_not_deadlock() -> None:
    """A finalizer may run on the loop thread itself (review finding): it must stop the loop
    without waiting on it."""
    client = BrainClient.in_memory(FakeBrain(), agent="red-rail")
    client.call("brain_delivery_list", {"actor_project": "red-probe", "limit": 1})
    pool = client._pool
    loop, thread = pool._loop, pool._thread
    assert loop is not None and thread is not None
    done = threading.Event()
    loop.call_soon_threadsafe(lambda: (pool.close(), done.set()))
    assert done.wait(5), "close() from the loop thread blocked"
    thread.join(5)
    assert not thread.is_alive()
    assert pool._sessions == {} and loop.is_closed()  # sessions closed before the loop stopped


def test_the_guard_runs_before_a_reused_session_is_called() -> None:
    """With sessions reused, a regression moving the guard into the session's opening would
    let a second call through an open session (review finding)."""
    verdicts = iter(
        [None, PrivateFileError("site brain: the kernel gives no route to its address")]
    )

    def guard() -> None:
        verdict = next(verdicts)
        if verdict is not None:
            raise verdict

    client = BrainClient.in_memory(FakeBrain(), agent="red-rail")
    client.guard = guard
    try:
        client.call("brain_delivery_list", {"actor_project": "red-probe", "limit": 1})
        session = client._pool._sessions["red-rail"]
        calls = []
        original = session.call_tool

        async def counted(*args: object, **kwargs: object) -> object:
            calls.append(args)
            return await original(*args, **kwargs)

        session.call_tool = counted  # type: ignore[method-assign]
        with pytest.raises(BrainUnreachable, match="no route"):
            client.call("brain_delivery_list", {"actor_project": "red-probe", "limit": 1})
        assert calls == []
    finally:
        client.close()
