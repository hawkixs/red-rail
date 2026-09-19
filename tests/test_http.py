"""One bounded GET on the standard library: statuses come back, no redirect is followed,
no answer at all is an `HttpError`."""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from rail.http import HttpError, http_get


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # http.server's name
        if self.path == "/healthz":
            body = b'{"status":"ok"}'
            self.send_response(200)
        elif self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/healthz")
            body = b""
        else:
            body = b"not found"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture
def server() -> Iterator[str]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_statuses_and_bodies_come_back(server: str) -> None:
    assert http_get(f"{server}/healthz", 2.0) == (200, b'{"status":"ok"}')
    assert http_get(f"{server}/nope", 2.0) == (404, b"not found")


def test_a_redirect_is_a_status_not_a_hop(server: str) -> None:
    status, _ = http_get(f"{server}/moved", 2.0)
    assert status == 302


def test_no_answer_is_an_http_error() -> None:
    with pytest.raises(HttpError):
        http_get("http://127.0.0.1:9/healthz", 0.5)
    with pytest.raises(HttpError, match="http"):
        http_get("ftp://example.invalid/x", 0.5)
