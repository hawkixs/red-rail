"""One synchronous call to brain-v42 over MCP, one stable code per refusal.

Transport facts (brain-v42, 2026-09-18): Streamable HTTP on the host loopback, bearer
mandatory, `X-Brain-Tool-Profile: native` so the delivery tools are callable by name,
`X-Brain-Agent` = the issuer label. A refusal is `ToolError("<code>: <message>")`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rail.brain.settings import is_loopback

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
    ) -> None:
        self.transport_factory = transport_factory
        self.agent = agent
        self.timeout = timeout

    @classmethod
    def http(cls, url: str, *, token: str, agent: str) -> BrainClient:
        from fastmcp.client.transports import StreamableHttpTransport

        if not is_loopback(url):
            raise BrainUnreachable(f"{url}: brain is reached on the host loopback only")

        def factory(label: str) -> Any:
            headers = {"X-Brain-Tool-Profile": "native", "X-Brain-Agent": label}
            return StreamableHttpTransport(url, auth=token, headers=headers)

        return cls(factory, agent)

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
        try:
            return asyncio.run(self._call(name, arguments, agent or self.agent))
        except BrainToolError:
            raise
        except Exception as exc:  # transport, timeout, shape — brain gave no answer
            raise BrainUnreachable(f"{name}: {exc}") from exc

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
