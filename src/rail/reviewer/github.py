"""The reviewer's own minimal GitHub App client (ADR-0003): synchronous, no retries,
bounded responses, explicit errors. brain-v42's observer has a richer one; the rail carries
its own rather than importing brain_v42."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt

from rail.autonomy import MergedPull

API = "https://api.github.com"
API_VERSION = "2022-11-28"
JSON = "application/vnd.github+json"
DIFF = "application/vnd.github.diff"
RAW = "application/vnd.github.raw+json"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PAGES = 100  # Bound linked pagination so a faulty server cannot keep a read running forever.
MAX_COMMIT_PAGES = 3  # GitHub lists at most 250 commits on a pull request, 100 per page
MAX_FILE_PAGES = 30  # GitHub lists at most 3000 files on a pull request, 100 per page
TIMEOUT_SECONDS = 10.0
RENEW_MARGIN = timedelta(seconds=60)
ReviewEvent = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]
Conclusion = Literal["success", "failure"]  # never neutral (spec §5, fail-closed)


class GitHubError(Exception):
    """GitHub refused or answered out of shape. `status` is the HTTP status when GitHub
    answered one, so a caller never has to read it back out of the message."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@contextmanager
def _in_shape() -> Iterator[None]:
    """An answer whose entries miss a field or are not objects is a GitHubError: a caller
    counts it as one repository unread, never as a crash of the whole run."""
    try:
        yield
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise GitHubError("GitHub answer out of shape") from exc


def _github_datetime(value: str) -> datetime:
    """Parse GitHub's UTC timestamps into aware datetimes for reliable window comparisons."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        return parsed.astimezone(UTC)
    except (TypeError, ValueError) as exc:
        raise GitHubError("GitHub timestamp out of shape") from exc


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
    name: str = ""
    app_id: int | None = None


@dataclass(frozen=True, slots=True)
class CommitStatus:
    id: int
    context: str
    state: str
    description: str
    creator_id: int | None = None


@dataclass(frozen=True, slots=True)
class ComparedFile:
    filename: str
    previous_filename: str | None
    status: str
    sha: str


@dataclass(frozen=True, slots=True)
class Comparison:
    merge_base: str
    files: tuple[str, ...]
    file_details: tuple[ComparedFile, ...]


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
    app_id = (raw.get("app") or {}).get("id")
    return CheckRun(
        id=int(raw["id"]),
        status=str(raw["status"]),
        conclusion=raw.get("conclusion"),
        name=str(raw.get("name", "")),
        app_id=int(app_id) if app_id is not None else None,
    )


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
        raw: bool | Literal["bytes"] = False,
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
            raise GitHubError(
                f"{method} {path}: {response.status_code} {detail}".rstrip(),
                status=response.status_code,
            )
        if raw == "bytes":
            return response.content
        if raw:
            return response.text
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubError(f"{method} {path}: not JSON") from exc

    def _request_page(self, path: str) -> tuple[Any, str | None]:
        """Read one JSON page and accept pagination only within the GitHub API origin."""
        # Keep transport policy identical to every other API read; only inspect Link afterward.
        response = self._page_response(path)
        next_url = None
        link = response.headers.get("Link", "")
        for match in re.finditer(r"<([^>]+)>\s*;\s*rel=\"?([^\";, ]+)\"?", link):
            if match.group(2) != "next":
                continue
            candidate = match.group(1)
            parsed = urlsplit(candidate)
            api = urlsplit(API)
            if (parsed.scheme, parsed.netloc) != (api.scheme, api.netloc):
                raise GitHubError("refused pagination URL outside GitHub API")
            next_url = candidate
            break
        try:
            body = response.json()
        except ValueError as exc:
            raise GitHubError(f"GET {path}: not JSON") from exc
        return body, next_url

    def _page_response(self, path: str) -> httpx.Response:
        """Fetch a page while retaining its headers under the standard bounded GET policy."""
        sent = {
            "Accept": JSON,
            "X-GitHub-Api-Version": API_VERSION,
            "Authorization": f"Bearer {self.token()}",
        }
        try:
            response = self._http.get(path, headers=sent, timeout=TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GET {path}: {exc}") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise GitHubError(f"GET {path}: response too large")
        if response.status_code >= 400:
            raise GitHubError(f"GET {path}: {response.status_code}", status=response.status_code)
        return response

    def _paged_list(self, path: str) -> list[Any]:
        """Read every page; a partial list is never returned as a complete answer."""
        items: list[Any] = []
        seen: set[str] = set()
        while path:
            if path in seen or len(seen) >= MAX_PAGES:
                raise GitHubError("pagination repeated a cursor or exceeded the page limit")
            seen.add(path)
            body, path = self._request_page(path)
            if not isinstance(body, list):
                raise GitHubError("incomplete answer")
            items.extend(body)
        return items

    def default_branch(self, repository: str) -> str:
        """Return the repository's declared default branch, refusing malformed responses."""
        data = self._request("GET", f"/repos/{repository}")
        if not isinstance(data, dict) or not isinstance(data.get("default_branch"), str):
            raise GitHubError("repository response has no default_branch")
        return data["default_branch"]

    def ready_for_review_at(self, repository: str, number: int) -> datetime | None:
        """Return the last transition from draft to ready, if the pull was ever drafted."""
        events = self._paged_list(f"/repos/{repository}/issues/{number}/timeline?per_page=100")
        with _in_shape():
            ready = [event for event in events if event.get("event") == "ready_for_review"]
            return _github_datetime(ready[-1]["created_at"]) if ready else None

    def merged_pulls(self, repository: str, *, base: str, since: datetime) -> list[MergedPull]:
        """List merged pull requests in the window, stopping once updates predate it."""
        query = urlencode(
            {
                "state": "closed",
                "base": base,
                "sort": "updated",
                "direction": "desc",
                "per_page": "100",
            }
        )
        path: str | None = f"/repos/{repository}/pulls?{query}"
        result: list[MergedPull] = []
        seen: set[str] = set()
        while path:
            if path in seen or len(seen) >= MAX_PAGES:
                raise GitHubError("pagination repeated a cursor or exceeded the page limit")
            seen.add(path)
            page, next_page = self._request_page(path)
            if not isinstance(page, list):
                raise GitHubError("incomplete answer")
            with _in_shape():
                for raw in page:
                    merged = raw.get("merged_at")
                    if merged is None:
                        continue
                    merged_at = _github_datetime(merged)
                    if merged_at >= since:
                        number = int(raw["number"])
                        created = _github_datetime(raw["created_at"])
                        ready_at = self.ready_for_review_at(repository, number) or created
                        result.append(MergedPull(number, merged_at, ready_at))
                # sorted by update, newest first: once a page ends before `since`, no later page
                # can hold a pull request merged in the window (read in full first)
                if page and _github_datetime(page[-1]["updated_at"]) < since:
                    break
            path = next_page
        return result

    def commits_since(
        self, repository: str, *, branch: str, since: datetime
    ) -> list[tuple[datetime, str]]:
        """Read commit dates and first message lines since an ISO timestamp."""
        query = urlencode({"sha": branch, "since": since.isoformat(), "per_page": "100"})
        commits = self._paged_list(f"/repos/{repository}/commits?{query}")
        with _in_shape():
            return [
                (
                    _github_datetime(item["commit"]["committer"]["date"]),
                    item["commit"]["message"].split("\n", 1)[0],
                )
                for item in commits
            ]

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

    def compare_files(self, repository: str, base_sha: str, head_sha: str) -> Comparison:
        """Read the merge base and both names of renames, refusing GitHub's file-list cap.

        Compare lists at most 300 files, only on its first page. A saturated list cannot
        prove that the base left every PR file untouched, so carry-over fails closed.
        """
        data = self._request("GET", f"/repos/{repository}/compare/{base_sha}...{head_sha}")
        with _in_shape():
            files = data["files"]
            merge_base = data["merge_base_commit"]["sha"]
            if not isinstance(files, list) or len(files) >= 300 or not merge_base:
                raise GitHubError("incomplete comparison")
            names = []
            details = []
            for entry in files:
                names.append(entry["filename"])
                if entry.get("previous_filename"):
                    names.append(entry["previous_filename"])
                if not all(isinstance(entry[key], str) and entry[key] for key in ("status", "sha")):
                    raise GitHubError("comparison file out of shape")
                details.append(
                    ComparedFile(
                        entry["filename"],
                        entry.get("previous_filename"),
                        entry["status"],
                        entry["sha"],
                    )
                )
            if not all(isinstance(name, str) and name for name in names):
                raise GitHubError("comparison file out of shape")
            return Comparison(str(merge_base), tuple(names), tuple(details))

    def commit_shas(self, repository: str, number: int) -> list[str]:
        """The pull request's own commits, every page of them: what it already publishes.
        GitHub lists at most 250 commits on a pull request."""
        shas: list[str] = []
        for page in range(1, MAX_COMMIT_PAGES + 1):
            listed = self._request(
                "GET",
                f"/repos/{repository}/pulls/{number}/commits",
                params={"per_page": "100", "page": str(page)},
            )
            if not isinstance(listed, list) or not listed:
                break
            shas.extend(str(item["sha"]) for item in listed)
            if len(listed) < 100:
                break
        return shas

    def commit_messages(self, repository: str, number: int) -> list[str]:
        data = self._request(
            "GET", f"/repos/{repository}/pulls/{number}/commits", params={"per_page": "100"}
        )
        return [str(item["commit"]["message"]) for item in data]

    def file_at(self, repository: str, path: str, ref: str) -> str | None:
        """One file's text at a commit, None when the path does not exist there (absence is
        evidence, not a failure). Content a judge cannot read — over `MAX_RESPONSE_BYTES`, not
        UTF-8 text, or a path that is not a file — raises `GitHubError` with the reason.

        `path` is built from a judge's words, which the author's diff can steer, and it goes
        into the API path next to the App's token: anything that is not a plain relative file
        path (empty or absolute, a `.` or `..` or empty segment, a backslash, `?`, `#`, a
        control character) is refused before any request."""
        segments = path.split("/")
        if any(s in ("", ".", "..") for s in segments) or any(
            c in "\\?#" or ord(c) < 32 or ord(c) == 127 for c in path
        ):
            raise GitHubError(f"refused path {path!r}: not a plain relative file path")
        try:
            content = self._request(
                "GET",
                f"/repos/{repository}/contents/{quote(path, safe='/')}",
                params={"ref": ref},
                accept=RAW,
                raw="bytes",
            )
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GitHubError(f"{path} at {ref[:12]} is not text") from exc
        if "\x00" in text:
            raise GitHubError(f"{path} at {ref[:12]} is not text")
        return text

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

    def check_runs(self, repository: str, sha: str, *, name: str | None = None) -> list[CheckRun]:
        """Read all current checks, or only ours by name; CI evidence is never a partial page."""
        query = {"per_page": "50", "filter": "latest"}
        if name is not None:
            query.update(app_id=str(self.app_id), check_name=name)
        path: str | None = f"/repos/{repository}/commits/{sha}/check-runs?{urlencode(query)}"
        result = []
        seen: set[str] = set()
        total = 0
        while path:
            if path in seen or len(seen) >= MAX_PAGES:
                raise GitHubError("pagination repeated a cursor or exceeded the page limit")
            seen.add(path)
            data, path = self._request_page(path)
            with _in_shape():
                checks = data["check_runs"]
                total = int(data.get("total_count", len(checks)))
                if not isinstance(checks, list):
                    raise GitHubError("incomplete check runs")
                result.extend(_check(raw) for raw in checks)
        if len(result) != total:
            raise GitHubError("incomplete check runs")
        return result

    def commit_statuses(self, repository: str, sha: str) -> list[CommitStatus]:
        """Keep the latest state per context and creator; GitHub returns newest first."""
        raw = self._paged_list(f"/repos/{repository}/commits/{sha}/statuses?per_page=100")
        latest: dict[tuple[str, int | None], CommitStatus] = {}
        with _in_shape():
            for item in raw:
                context = str(item["context"])
                creator_id = (item.get("creator") or {}).get("id")
                creator_id = int(creator_id) if creator_id is not None else None
                latest.setdefault(
                    (context, creator_id),
                    CommitStatus(
                        int(item["id"]),
                        context,
                        str(item["state"]),
                        str(item.get("description") or ""),
                        creator_id,
                    ),
                )
        return list(latest.values())

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
