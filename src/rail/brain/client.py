"""One synchronous call to brain-v42 over MCP, one stable code per refusal.

Transport facts (brain-v42): Streamable HTTP on loopback or a private address, bearer
mandatory, `X-Brain-Tool-Profile: native` so the delivery tools are callable by name,
`X-Brain-Agent` = the issuer label. A refusal is `ToolError("<code>: <message>")`.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import threading
import weakref
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rail.brain.settings import BrainSettings, is_reachable

if TYPE_CHECKING:
    from fastmcp import Client, FastMCP

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


def _run_until_stopped(loop: asyncio.AbstractEventLoop) -> None:
    loop.run_forever()
    loop.close()  # a no-op when `close()` already closed it


class _SessionPool:
    """One loop and one session per label, independent of the calling client's lifetime."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._sessions: dict[str, Client] = {}

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        label: str,
        timeout: float,
        before_call: Callable[[str], None] | None,
        transport_factory: Callable[[str], Any],
    ) -> dict[str, Any]:
        with self._lock:
            future = None
            try:
                if self._loop is None:
                    self._loop = asyncio.new_event_loop()
                    self._thread = threading.Thread(
                        target=_run_until_stopped,
                        args=(self._loop,),
                        name="rail-brain",
                        daemon=True,
                    )
                    self._thread.start()
                future = asyncio.run_coroutine_threadsafe(
                    self._call(name, arguments, label, timeout, before_call, transport_factory),
                    self._loop,
                )
                try:
                    return future.result(timeout)
                except concurrent.futures.TimeoutError:
                    # an empty TimeoutError would leave a spooled attestation with no cause
                    raise TimeoutError(f"no answer within {timeout:g} s") from None
            except BrainToolError:
                raise
            except Exception:  # transport, timeout, shape — brain gave no answer
                self._abandon(future, label, timeout, wait=True)
                raise
            except BaseException:  # an interrupt: never leave a call in flight on a session
                self._abandon(future, label, timeout, wait=False)
                raise

    def _abandon(self, future: Any, label: str, timeout: float, *, wait: bool) -> None:
        """Cancel the call and drop its session, so no later call shares it."""
        if future is not None:
            future.cancel()
        if self._loop is not None:
            discarded = asyncio.run_coroutine_threadsafe(self._discard(label, timeout), self._loop)
            if wait:
                discarded.result()

    async def _discard(self, agent: str, timeout: float = CALL_TIMEOUT_SECONDS) -> None:
        client = self._sessions.pop(agent, None)
        if client is not None:
            try:
                await asyncio.wait_for(client.close(), timeout=timeout)
            except (Exception, asyncio.CancelledError):
                pass  # A broken transport must not hide the original failure.

    async def _close_sessions(self) -> None:
        for agent in list(self._sessions):
            await self._discard(agent)

    def close(self) -> None:
        """Release server traces before stopping the loop; the next call may reopen it."""
        if self._thread is not None and threading.current_thread() is self._thread:
            # a finalizer running on the loop thread itself cannot wait on that loop: stop
            # it and let the thread end (review finding)
            loop = self._loop
            if loop is not None:
                loop.call_soon(self._stop_from_loop)
            return
        with self._lock:
            if self._loop is None:
                return
            loop, thread = self._loop, self._thread
            try:
                asyncio.run_coroutine_threadsafe(self._close_sessions(), loop).result()
            finally:
                loop.call_soon_threadsafe(loop.stop)
                if thread is not None:
                    thread.join()
                loop.close()
                self._loop = None
                self._thread = None

    def _stop_from_loop(self) -> None:
        """Called on the loop thread: schedule the shutdown, never wait on the loop."""
        if self._loop is not None:
            self._loop.create_task(self._shutdown())

    async def _shutdown(self) -> None:
        loop = asyncio.get_running_loop()
        await self._close_sessions()  # the DELETEs are sent before the loop stops
        self._loop = None
        self._thread = None
        loop.stop()  # the thread's target closes the loop once `run_forever` returns

    async def _call(
        self,
        name: str,
        arguments: dict[str, Any],
        agent: str,
        timeout: float,
        before_call: Callable[[str], None] | None,
        transport_factory: Callable[[str], Any],
    ) -> dict[str, Any]:
        from fastmcp import Client
        from fastmcp.exceptions import ToolError

        if before_call is not None:
            before_call(agent)
        client = self._sessions.get(agent)
        if client is None:
            client = Client(transport_factory(agent), timeout=timeout)
            await client.__aenter__()
            self._sessions[agent] = client
        try:
            result = await client.call_tool(name, arguments, timeout=timeout)
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


_registry_lock = threading.Lock()
# Transport identities stay private; neither the URL nor the token is logged.
_http_pools: dict[tuple[str, str, str | None], _SessionPool] = {}
_private_pools: weakref.WeakSet[_SessionPool] = weakref.WeakSet()


def close_all() -> None:
    """Close every session and stop its loop. Existing clients may reopen their pool.

    Keep HTTP entries so a surviving client and a new client still share the same pool.
    """
    with _registry_lock:
        for pool in [*_http_pools.values(), *_private_pools]:
            pool.close()


atexit.register(close_all)


class BrainClient:
    """HTTP clients share one process-owned session per transport identity and label.

    The ledger sends each record's `issuer` as `X-Brain-Agent`, so brain's
    `issuer_identity` equals the mirror's `issuer` and the two digests match.
    """

    def __init__(
        self,
        transport_factory: Callable[[str], Any],
        agent: str,
        *,
        timeout: float = CALL_TIMEOUT_SECONDS,
        redact: Callable[[str], str] | None = None,
        guard: Callable[[], None] | None = None,
        _pool: _SessionPool | None = None,
    ) -> None:
        self.transport_factory = transport_factory
        self.guard = guard
        self.agent = agent
        self.timeout = timeout
        self.redact = redact if redact is not None else lambda text: text
        self._before_call: Callable[[str], None] | None = None
        self._shared = _pool is not None
        self._pool = _pool if _pool is not None else _SessionPool()
        if not self._shared:
            with _registry_lock:
                _private_pools.add(self._pool)
            # The finalizer owns the pool, never the client. Dropped test clients cannot leak.
            weakref.finalize(self, self._pool.close)

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
        from fastmcp.client import transports

        if not is_reachable(url):
            raise BrainUnreachable("brain is reached on the loopback or a private address only")

        client_factory = _http_client_factory(interface)

        def factory(label: str) -> Any:
            headers = {"X-Brain-Tool-Profile": "native", "X-Brain-Agent": label}
            return transports.StreamableHttpTransport(
                url, auth=token, headers=headers, httpx_client_factory=client_factory
            )

        with _registry_lock:
            identity = (url, token, interface)  # the interface shapes every socket
            pool = _http_pools.get(identity)
            if pool is None:
                pool = _http_pools[identity] = _SessionPool()
        return cls(factory, agent, redact=redact, guard=guard, _pool=pool)

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
        """Tests: a private pool for an in-process server, closed explicitly or on collection.

        The label the real server would read from the header is set on the fake.
        """
        server = getattr(brain, "server", brain)

        def set_agent(label: str) -> None:
            if hasattr(brain, "agent"):
                brain.agent = label

        client = cls(lambda label: server, agent)
        client._before_call = set_agent
        return client

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
            return self._pool.call(
                name,
                arguments,
                agent or self.agent,
                self.timeout,
                self._before_call,
                self.transport_factory,
            )
        except BrainToolError:
            raise
        except Exception as exc:
            # The transport's traceback may quote the private address too.
            raise BrainUnreachable(self.redact(f"{name}: {exc}")) from None

    def close(self) -> None:
        """Close a private pool. HTTP sessions belong to the process; use `close_all()`."""
        if not self._shared:
            self._pool.close()

    def __enter__(self) -> BrainClient:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()
