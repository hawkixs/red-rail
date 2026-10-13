"""The loopback endpoint serves only bounded, validated contract serializations."""

from __future__ import annotations

import json
from collections.abc import Iterator
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.metrics_contract import MAX_BYTES, Answer, encode

CONTRACT = Path(__file__).resolve().parents[1] / "contracts/rail-metrics-v1"


@pytest.fixture
def endpoint(tmp_path: Path) -> Iterator[tuple[ThreadingHTTPServer, Path]]:
    from rail.serve import create_server

    file = tmp_path / "metrics.json"
    with create_server(file, port=0) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield server, file
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


def request(
    server: ThreadingHTTPServer, method: str = "GET", path: str = "/rail-metrics"
) -> tuple[int, dict[str, str], bytes]:
    connection = HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def assert_answer(response: tuple[int, dict[str, str], bytes], status: int, body: bytes) -> None:
    actual_status, headers, actual_body = response
    assert actual_status == status
    assert headers["Content-Type"] == "application/json"
    assert headers["Cache-Control"] == "no-store"
    assert actual_body == body


def test_serve_help_without_a_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["metrics", "serve", "--help"])
    assert result.exit_code == 0, result.output
    assert "--file" in result.output and "--port" in result.output
    assert "9230" in result.output and "--host" not in result.output


def test_bound_address_is_loopback(endpoint: tuple[ThreadingHTTPServer, Path]) -> None:
    server, _ = endpoint
    assert server.server_address[0] == "127.0.0.1"


def test_absent_file_is_empty(endpoint: tuple[ThreadingHTTPServer, Path]) -> None:
    server, _ = endpoint
    assert_answer(request(server), 200, b"{}")


@pytest.mark.parametrize(
    "fixture", ["full", "delivery-only", "partial", "partial-delivery", "empty"]
)
def test_serves_reserialized_contract(
    endpoint: tuple[ThreadingHTTPServer, Path], fixture: str
) -> None:
    server, file = endpoint
    raw = (
        b"\n"
        + json.dumps(json.loads((CONTRACT / f"{fixture}.json").read_bytes()), indent=1).encode()
    )
    assert len(raw) <= MAX_BYTES
    file.write_bytes(raw)
    expected = encode(Answer.model_validate_json(raw, strict=True))
    assert_answer(request(server), 200, expected)
    assert expected != raw


@pytest.mark.parametrize("raw", [b"not json", b"\xff", b"[]", b" " * (MAX_BYTES + 1)])
def test_invalid_or_oversized_file_is_unavailable(
    endpoint: tuple[ThreadingHTTPServer, Path], raw: bytes
) -> None:
    server, file = endpoint
    file.write_bytes(raw)
    assert_answer(request(server), 503, b"{}")


def test_file_at_size_cap_is_valid(endpoint: tuple[ThreadingHTTPServer, Path]) -> None:
    server, file = endpoint
    file.write_bytes(b"{}" + b" " * (MAX_BYTES - 2))
    assert_answer(request(server), 200, b"{}")


def test_file_read_is_bounded(
    endpoint: tuple[ThreadingHTTPServer, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    server, file = endpoint
    limits: list[int] = []
    original_open = Path.open

    class Reader(BytesIO):
        def read(self, size: int = -1) -> bytes:
            limits.append(size)
            return super().read(size)

    def open_file(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == file:
            return Reader(b" " * (MAX_BYTES * 2))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    assert_answer(request(server), 503, b"{}")
    assert limits == [MAX_BYTES + 1]


@pytest.mark.parametrize("invalid", ["string_count", "extra_field", "unknown_version"])
def test_strict_contract_is_enforced(
    endpoint: tuple[ThreadingHTTPServer, Path], invalid: str
) -> None:
    server, file = endpoint
    payload = json.loads((CONTRACT / "delivery-only.json").read_bytes())
    if invalid == "string_count":
        payload["delivery"]["projects"][0]["d7"]["deployments"] = "3"
    elif invalid == "extra_field":
        payload["delivery"]["unknown"] = 3
    else:
        payload["delivery"]["version"] = "rail-delivery/v2"
    file.write_text(json.dumps(payload))
    assert_answer(request(server), 503, b"{}")


@pytest.mark.parametrize("method", ["HEAD", "POST", "PUT", "DELETE", "OPTIONS", "BREW"])
def test_other_methods_are_not_allowed(
    endpoint: tuple[ThreadingHTTPServer, Path], method: str
) -> None:
    server, _ = endpoint
    assert_answer(request(server, method), 405, b"" if method == "HEAD" else b"{}")


@pytest.mark.parametrize("method", ["GET", "POST", "BREW"])
@pytest.mark.parametrize("path", ["/", "/metrics.json", "/rail-metrics/", "/other?x=1"])
def test_other_paths_are_not_found(
    endpoint: tuple[ThreadingHTTPServer, Path], method: str, path: str
) -> None:
    server, _ = endpoint
    assert_answer(request(server, method, path), 404, b"{}")


def test_query_is_ignored(endpoint: tuple[ThreadingHTTPServer, Path]) -> None:
    server, file = endpoint
    file.write_bytes(b"{}")
    assert_answer(request(server, path="/rail-metrics?file=/other&bad=%ZZ"), 200, b"{}")
    assert_answer(request(server, "BREW", "/rail-metrics?ignored=1"), 405, b"{}")


def test_atomic_replacement_is_read_on_next_request(
    endpoint: tuple[ThreadingHTTPServer, Path], tmp_path: Path
) -> None:
    server, file = endpoint
    first = encode(Answer.model_validate_json((CONTRACT / "delivery-only.json").read_bytes()))
    second = encode(Answer.model_validate_json((CONTRACT / "full.json").read_bytes()))
    file.write_bytes(first)
    assert_answer(request(server), 200, first)
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(second)
    replacement.replace(file)
    assert_answer(request(server), 200, second)


def test_access_log_is_silent(
    endpoint: tuple[ThreadingHTTPServer, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    server, _ = endpoint
    request(server)
    assert capsys.readouterr().err == ""


def test_serve_uses_default_file_and_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from rail import serve

    calls: list[tuple[Path, int]] = []

    class Server:
        def __enter__(self) -> Server:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def serve_forever(self) -> None:
            pass

    def create(file: Path, *, port: int) -> Server:
        calls.append((file, port))
        return Server()

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(serve, "create_server", create)
    result = CliRunner().invoke(main, ["metrics", "serve"])
    assert result.exit_code == 0, result.output
    assert calls == [(tmp_path / "red-rail/metrics.json", 9230)]
    file = tmp_path / "custom.json"
    result = CliRunner().invoke(main, ["metrics", "serve", "--file", str(file), "--port", "0"])
    assert result.exit_code == 0, result.output
    assert calls[-1] == (file, 0)
