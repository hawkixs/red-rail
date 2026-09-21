"""The reviewer's own minimal GitHub App client (ADR-0003): synchronous, no retries,
bounded responses, explicit errors. brain-v42's observer has a richer one; the rail carries
its own rather than importing brain_v42."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import jwt

API = "https://api.github.com"
API_VERSION = "2022-11-28"
JSON = "application/vnd.github+json"
DIFF = "application/vnd.github.diff"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_FILE_PAGES = 30  # GitHub lists at most 3000 files on a pull request, 100 per page
TIMEOUT_SECONDS = 10.0
RENEW_MARGIN = timedelta(seconds=60)
ReviewEvent = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]
Conclusion = Literal["success", "failure"]  # never neutral (spec §5, fail-closed)


class GitHubError(Exception):
    """GitHub refused or answered out of shape."""


@dataclass(frozen=True, slots=True)
class PullRequest:
    repository: str
    number: int
    title: str
    body: str
    draft: bool
    author: str
    head_sha: str
    base_sha: str
    labels: tuple[str, ...]
    additions: int
    deletions: int
    changed_files: int

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


@dataclass(frozen=True, slots=True)
class CheckRun:
    id: int
    status: str
    conclusion: str | None


def _pull(repository: str, raw: dict[str, Any]) -> PullRequest:
    try:
        return PullRequest(
            repository=repository,
            number=int(raw["number"]),
            title=str(raw.get("title") or ""),
            body=str(raw.get("body") or ""),
            draft=bool(raw.get("draft", False)),
            author=str((raw.get("user") or {}).get("login") or ""),
            head_sha=str(raw["head"]["sha"]),
            base_sha=str(raw["base"]["sha"]),
            labels=tuple(str(label["name"]) for label in raw.get("labels") or []),
            additions=int(raw.get("additions") or 0),
            deletions=int(raw.get("deletions") or 0),
            changed_files=int(raw.get("changed_files") or 0),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GitHubError(f"pull request out of shape: {exc}") from exc


def _check(raw: dict[str, Any]) -> CheckRun:
    return CheckRun(id=int(raw["id"]), status=str(raw["status"]), conclusion=raw.get("conclusion"))


class GitHubApp:
    def __init__(
        self,
        *,
        app_id: int,
        installation_id: int,
        private_key_pem: str,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        api: str = API,
    ) -> None:
        self.app_id = app_id
        self.installation_id = installation_id
        self._pem = private_key_pem
        self._clock = clock or (lambda: datetime.now(UTC))
        self._http = httpx.Client(base_url=api, transport=transport, timeout=TIMEOUT_SECONDS)
        self._token: str | None = None
        self._expires_at: datetime | None = None

    # -- auth ----------------------------------------------------------------------------

    def _jwt(self) -> str:
        # GitHub validates iat/exp against its own real clock, not the injectable one used
        # here for cache-staleness bookkeeping (tests freeze that one) — always real time.
        now = int(time.time())
        claims = {"iat": now - 60, "exp": now + 9 * 60, "iss": str(self.app_id)}
        return jwt.encode(claims, self._pem, algorithm="RS256")

    def token(self) -> str:
        now = self._clock()
        if self._token and self._expires_at and now < self._expires_at - RENEW_MARGIN:
            return self._token
        data = self._request(
            "POST",
            f"/app/installations/{self.installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self._jwt()}"},
            authenticated=False,
        )
        try:
            self._token = str(data["token"])
            self._expires_at = datetime.fromisoformat(
                str(data["expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubError(f"installation token out of shape: {exc}") from exc
        return self._token

    # -- transport -----------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        accept: str = JSON,
        authenticated: bool = True,
        raw: bool = False,
    ) -> Any:
        sent = {"Accept": accept, "X-GitHub-Api-Version": API_VERSION, **(headers or {})}
        if authenticated:
            sent["Authorization"] = f"Bearer {self.token()}"
        try:
            response = self._http.request(method, path, headers=sent, params=params, json=json_body)
        except httpx.HTTPError as exc:
            raise GitHubError(f"{method} {path}: {exc}") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise GitHubError(f"{method} {path}: response too large")
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("message", ""))
            except ValueError:
                pass
            raise GitHubError(f"{method} {path}: {response.status_code} {detail}".rstrip())
        if raw:
            return response.text
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubError(f"{method} {path}: not JSON") from exc

    # -- pull requests -------------------------------------------------------------------

    def open_pulls(self, repository: str) -> list[PullRequest]:
        data = self._request(
            "GET", f"/repos/{repository}/pulls", params={"state": "open", "per_page": "50"}
        )
        return [_pull(repository, raw) for raw in data]

    def pull(self, repository: str, number: int) -> PullRequest:
        return _pull(repository, self._request("GET", f"/repos/{repository}/pulls/{number}"))

    def diff(self, repository: str, number: int) -> str:
        """The unified diff, reassembled from the file list when GitHub refuses it.

        GitHub caps the diff media type at 20 000 lines and answers 406 past it. Refusing to
        review is not a choice the rail can make — `review.verdict` is fail-closed, so a
        reviewer that cannot read a large change blocks a legitimate one for ever, and a
        rewrite is exactly the change that most deserves reading."""
        try:
            return str(
                self._request("GET", f"/repos/{repository}/pulls/{number}", accept=DIFF, raw=True)
            )
        except GitHubError as exc:
            if "406" not in str(exc):
                raise
            return self._diff_from_files(repository, number)

    def _diff_from_files(self, repository: str, number: int) -> str:
        """Per-file patches, stitched back into something a reader can read. A file whose
        patch GitHub omits (binary, or too large on its own) is NAMED rather than dropped:
        the reviewer must know it changed even when it cannot see how."""
        chunks: list[str] = []
        for page in range(1, MAX_FILE_PAGES + 1):
            listed = self._request(
                "GET",
                f"/repos/{repository}/pulls/{number}/files",
                params={"per_page": "100", "page": str(page)},
            )
            if not isinstance(listed, list) or not listed:
                break
            for entry in listed:
                name = str(entry.get("filename", "?"))
                header = f"diff --git a/{name} b/{name}"
                patch = entry.get("patch")
                status = str(entry.get("status", "modified"))
                if patch:
                    chunks.append(f"{header}\n{patch}")
                else:
                    chunks.append(f"{header}\n# {status}, no patch available from GitHub")
            if len(listed) < 100:
                break
        if not chunks:
            raise GitHubError(f"pull request {repository}#{number}: no file list to review")
        return "\n".join(chunks) + "\n"

    def compare_diff(self, repository: str, base_sha: str, head_sha: str) -> str:
        """The changes between two commits of the repository as a diff (`GitHubError` when
        the base is gone — a rebased or force-pushed pull request)."""
        return str(
            self._request(
                "GET",
                f"/repos/{repository}/compare/{base_sha}...{head_sha}",
                accept=DIFF,
                raw=True,
            )
        )

    def commit_messages(self, repository: str, number: int) -> list[str]:
        data = self._request(
            "GET", f"/repos/{repository}/pulls/{number}/commits", params={"per_page": "100"}
        )
        return [str(item["commit"]["message"]) for item in data]

    # -- checks and reviews --------------------------------------------------------------

    def start_check(self, repository: str, head_sha: str, *, name: str) -> CheckRun:
        body = {
            "name": name,
            "head_sha": head_sha,
            "status": "in_progress",
            "started_at": self._clock().astimezone(UTC).isoformat(),
        }
        return _check(self._request("POST", f"/repos/{repository}/check-runs", json_body=body))

    def complete_check(
        self,
        repository: str,
        check_id: int,
        *,
        conclusion: Conclusion,
        title: str,
        summary: str,
        text: str = "",
    ) -> CheckRun:
        body = {
            "status": "completed",
            "conclusion": conclusion,
            "completed_at": self._clock().astimezone(UTC).isoformat(),
            "output": {"title": title[:255], "summary": summary[:65535], "text": text[:65535]},
        }
        return _check(
            self._request("PATCH", f"/repos/{repository}/check-runs/{check_id}", json_body=body)
        )

    def check_runs(self, repository: str, sha: str, *, name: str) -> list[CheckRun]:
        data = self._request(
            "GET",
            f"/repos/{repository}/commits/{sha}/check-runs",
            params={"app_id": str(self.app_id), "check_name": name, "per_page": "50"},
        )
        return [_check(raw) for raw in data.get("check_runs", [])]

    def check_run_text(self, repository: str, check_id: int) -> str:
        """The `output.text` our App published on a check run (the rendered verdict)."""
        data = self._request("GET", f"/repos/{repository}/check-runs/{check_id}")
        output = data.get("output") if isinstance(data, dict) else None
        return str((output or {}).get("text") or "")

    def review(
        self, repository: str, number: int, *, commit_id: str, event: ReviewEvent, body: str
    ) -> None:
        if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
            raise ValueError(f"unknown review event {event!r}")
        self._request(
            "POST",
            f"/repos/{repository}/pulls/{number}/reviews",
            json_body={"commit_id": commit_id, "event": event, "body": body[:65535]},
        )

    def remove_label(self, repository: str, number: int, label: str) -> None:
        self._request("DELETE", f"/repos/{repository}/issues/{number}/labels/{label}")

    def close(self) -> None:
        self._http.close()
