"""One bounded HTTP GET on the standard library, for the checks the rail makes from the host
(`/healthz`, `/version`, red-monitor). Injectable everywhere (`Http`), so no test opens a
socket by accident. No redirect is followed: a 3xx is a status like any other."""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

MAX_BODY = 1 << 20  # the rail reads JSON and health words, never a payload

Http = Callable[[str, float], tuple[int, bytes]]


class HttpError(Exception):
    """No HTTP answer at all: refused, timed out, unresolved, not an http(s) URL."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def http_get(url: str, timeout: float) -> tuple[int, bytes]:
    if not url.startswith(("http://", "https://")):
        raise HttpError(f"not an http(s) URL: {url}")
    request = urllib.request.Request(
        url, headers={"User-Agent": "red-rail", "Accept": "application/json, text/plain;q=0.5"}
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return int(response.status), response.read(MAX_BODY)
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(MAX_BODY)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HttpError(f"{url}: {getattr(exc, 'reason', exc)}") from exc
