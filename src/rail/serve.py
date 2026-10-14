"""Serve the validated metrics file on loopback without exposing raw file contents."""

from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import ValidationError

from rail.metrics_contract import MAX_BYTES, Answer, TooLarge, encode


def create_server(file: Path, *, port: int = 9230) -> ThreadingHTTPServer:
    """Fix the bind to loopback so the endpoint cannot be exposed by a host option."""

    class Handler(BaseHTTPRequestHandler):
        def __getattr__(self, name: str) -> Callable[[], None]:
            """Dispatch every method, including unknown ones, without the default HTML errors."""
            if name.startswith("do_"):
                return self._respond
            raise AttributeError(name)

        def _respond(self) -> None:
            body = b"{}"
            if self.path.partition("?")[0] != "/rail-metrics":
                status = 404
            elif self.command != "GET":
                status = 405
            else:
                try:
                    with file.open("rb") as source:
                        raw = source.read(MAX_BYTES + 1)
                    if len(raw) > MAX_BYTES:
                        raise TooLarge(len(raw))
                    body = encode(Answer.model_validate_json(raw, strict=True))
                    status = 200
                except FileNotFoundError:
                    status = 200
                except (OSError, ValidationError, TooLarge):
                    status = 503
            try:
                self.send_response(status)
                if status == 405:
                    self.send_header("Allow", "GET")
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # A disconnected probe is routine; other response failures must remain visible.
                pass

        def log_message(self, format: str, *args: object) -> None:
            """Disable the access log because each probe needs no persistent log line."""

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
