"""One synchronous call to brain-v42 over MCP, one stable code per refusal.

Transport facts (brain-v42): Streamable HTTP on loopback or a private address, bearer
mandatory, `X-Brain-Tool-Profile: native` so the delivery tools are callable by name,
`X-Brain-Agent` = the issuer label. A refusal is `ToolError("<code>: <message>")`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rail.brain.settings import BrainSettings, is_reachable

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from tests.fake_brain import FakeBrain

CALL_TIMEOUT_SECONDS = 10.0


class BrainUnreachable(Exception):
    """No answer from brain: transport, timeout, unknown tool, unreadable result."""


class BrainToolError(Exception):
    """brain answered with a stable refusal code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}" if message else code)


def parse_tool_error(text: str) -> tuple[str, str]:
    code, sep, message = text.partition(":")
    code = code.strip()
    if not sep or not code or " " in code:
        return "delivery_unavailable", text.strip()
    return code, message.strip()


def _http_client_factory(interface: str | None) -> Callable[..., Any]:
    """The MCP SDK's client factory (`mcp.shared._httpx_utils.create_mcp_http_client`), made
    safe for a bearer sent as plain HTTP through a tunnel:

    - its sockets are bound to the declared interface (`SO_BINDTODEVICE`, unprivileged since
      Linux 5.7): the route guard checks before a call, the binding holds during it, so a
      tunnel lost mid-call fails the connection instead of leaving through the default
      gateway (independent review F-85-5);
    - `trust_env=False`: a proxy variable would send the bearer elsewhere;
    - no redirects: fastmcp asks a custom factory to follow them, the SDK's own factory does
      not, and a redirect is a destination nothing checked.
    """

    def factory(
        headers: dict[str, str] | None = None,
        timeout: Any = None,
        auth: Any = None,
        follow_redirects: bool = False,  # passed by fastmcp, deliberately not honoured
        **options: Any,  # anything a later SDK passes, except what this factory decides
    ) -> Any:
        import socket

        import httpx
        from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

        del follow_redirects
        for decided in ("trust_env", "transport", "mounts", "proxy"):
            options.pop(decided, None)
        if timeout is None:
            timeout = httpx.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT)
        bound = (
            [(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode())]
            if interface is not None
            else None
        )
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            auth=auth,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(socket_options=bound),
            **options,
        )

    return factory


class BrainClient:
    """`transport_factory(agent)` builds the transport for one call under one agent label:
    the ledger sends each record's `issuer` as `X-Brain-Agent`, so brain's `issuer_identity`
    equals the mirror's `issuer` and the two digests match."""

    def __init__(
        self,
        transport_factory: Callable[[str], Any],
        agent: str,
        *,
        timeout: float = CALL_TIMEOUT_SECONDS,
        redact: Callable[[str], str] | None = None,
        guard: Callable[[], None] | None = None,
    ) -> None:
        self.transport_factory = transport_factory
        self.guard = guard
        self.agent = agent
        self.timeout = timeout
        self.redact = redact if redact is not None else lambda text: text

    @classmethod
    def http(
        cls,
        url: str,
        *,
        token: str,
        agent: str,
        redact: Callable[[str], str] | None = None,
        guard: Callable[[], None] | None = None,
        interface: str | None = None,
    ) -> BrainClient:
        from fastmcp.client.transports import StreamableHttpTransport

        if not is_reachable(url):
            raise BrainUnreachable("brain is reached on the loopback or a private address only")

        client_factory = _http_client_factory(interface)

        def factory(label: str) -> Any:
            headers = {"X-Brain-Tool-Profile": "native", "X-Brain-Agent": label}
            return StreamableHttpTransport(
                url, auth=token, headers=headers, httpx_client_factory=client_factory
            )

        return cls(factory, agent, redact=redact, guard=guard)

    @classmethod
    def from_settings(cls, settings: BrainSettings, *, agent: str) -> BrainClient:
        return cls.http(
            settings.url,
            token=settings.token,
            agent=agent,
            redact=settings.redact,
            guard=settings.guard,
            interface=settings.interface,
        )

    @classmethod
    def in_memory(cls, brain: FakeBrain | FastMCP, *, agent: str) -> BrainClient:
        """Tests: a FastMCP server (or a `FakeBrain`) in the same process; the label the
        real server would read from the header is set on the fake."""
        server = getattr(brain, "server", brain)

        def factory(label: str) -> Any:
            if hasattr(brain, "agent"):
                brain.agent = label
            return server

        return cls(factory, agent)

    def call(
        self, name: str, arguments: dict[str, Any], *, agent: str | None = None
    ) -> dict[str, Any]:
        from rail.private import PrivateFileError

        if self.guard is not None:
            try:
                self.guard()
            except PrivateFileError as exc:
                raise BrainUnreachable(self.redact(f"{name}: {exc}")) from None
        try:
            return asyncio.run(self._call(name, arguments, agent or self.agent))
        except BrainToolError:
            raise
        except Exception as exc:  # transport, timeout, shape — brain gave no answer
            # The transport's traceback may quote the private address too.
            raise BrainUnreachable(self.redact(f"{name}: {exc}")) from None

    async def _call(self, name: str, arguments: dict[str, Any], agent: str) -> dict[str, Any]:
        from fastmcp import Client
        from fastmcp.exceptions import ToolError

        async with Client(self.transport_factory(agent), timeout=self.timeout) as client:
            try:
                result = await client.call_tool(name, arguments, timeout=self.timeout)
            except ToolError as exc:
                text = str(exc)
                # fastmcp 3.4: `Unknown tool: '<name>'`; a refusal is `<code>: <message>` and its
                # message may well say "not found" (ticket_not_found, contract_not_found)
                if text.startswith(("Unknown tool", "Tool not found")):
                    raise BrainUnreachable(text) from exc
                code, message = parse_tool_error(text)
                raise BrainToolError(code, message) from exc
        content = result.structured_content
        if not isinstance(content, dict):
            raise BrainUnreachable(f"{name}: no structured content")
        return content
