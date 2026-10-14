"""The App client: JWT → installation token, then the few calls the reviewer needs. Every
request is asserted on path, method, headers and body; nothing reaches the network."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from rail.autonomy import MergedPull
from rail.reviewer import github
from rail.reviewer.github import CheckRun, CommitStatus, GitHubApp, GitHubError, PullRequest

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
PUBLIC = KEY.public_key()
T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class Recorder:
    def __init__(self, responses: dict[tuple[str, str], object]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key not in self.responses:
            return httpx.Response(404, json={"message": f"unexpected {key}"})
        body = self.responses[key]
        if isinstance(body, str):
            return httpx.Response(200, text=body, headers={"content-type": "text/plain"})
        return httpx.Response(200, json=body)


def _app(recorder: Recorder, now: datetime = T0) -> GitHubApp:
    ticks = [now + timedelta(seconds=i) for i in range(50)]
    return GitHubApp(
        app_id=123,
        installation_id=456,
        private_key_pem=PEM,
        transport=httpx.MockTransport(recorder),
        clock=lambda: ticks.pop(0),
    )


TOKEN = {"token": "ghs_installation", "expires_at": (T0 + timedelta(hours=1)).isoformat()}


def test_compare_files_reads_merge_base_and_both_rename_paths() -> None:
    recorder = Recorder(
        {
            ("POST", "/app/installations/456/access_tokens"): TOKEN,
            ("GET", "/repos/example/red-alpha/compare/base...head"): {
                "merge_base_commit": {"sha": "merge-base"},
                "files": [
                    {
                        "filename": "src/new.py",
                        "previous_filename": "src/old.py",
                        "status": "renamed",
                        "sha": "blob-sha",
                    }
                ],
            },
        }
    )
    result = _app(recorder).compare_files("example/red-alpha", "base", "head")
    assert result.merge_base == "merge-base"
    assert set(result.files) == {"src/old.py", "src/new.py"}
    assert result.file_details == (
        github.ComparedFile("src/new.py", "src/old.py", "renamed", "blob-sha"),
    )


def test_compare_files_refuses_a_saturated_or_missing_file_list() -> None:
    for files in (None, [{"filename": f"src/{n}.py"} for n in range(300)]):
        body = {"merge_base_commit": {"sha": "base"}}
        if files is not None:
            body["files"] = files
        recorder = Recorder(
            {
                ("POST", "/app/installations/456/access_tokens"): TOKEN,
                ("GET", "/repos/example/red-alpha/compare/base...head"): body,
            }
        )
        with pytest.raises(GitHubError):
            _app(recorder).compare_files("example/red-alpha", "base", "head")


def test_commit_statuses_reads_pages_and_keeps_latest_context() -> None:
    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(200, json=TOKEN)
        if request.url.params.get("page") == "2":
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "context": "lint", "state": "success", "description": "older"},
                    {"id": 2, "context": "test-unit", "state": "pending", "description": None},
                ],
            )
        return httpx.Response(
            200,
            json=[
                {"id": 3, "context": "lint", "state": "failure", "description": "latest"},
            ],
            headers={"Link": '<https://api.github.com/statuses?page=2>; rel="next"'},
        )

    app = _app(routed)
    assert app.commit_statuses("example/red-alpha", "head") == [
        CommitStatus(3, "lint", "failure", "latest"),
        CommitStatus(2, "test-unit", "pending", ""),
    ]


def test_reviewer_app_cannot_post_commit_statuses() -> None:
    assert not hasattr(GitHubApp, "post_status")


@pytest.mark.parametrize("source", [{"id": 42}, {"id": "42"}, {}, None])
@pytest.mark.parametrize("evidence", ["check", "status"])
def test_ci_evidence_parses_optional_source_ids(source, evidence) -> None:
    expected = 42 if source and "id" in source else None
    entry = {"id": 1, "name": "lint", "status": "completed", "conclusion": "success"}
    if evidence == "check":
        if source is not None:
            entry["app"] = source
        endpoint = "check-runs"
        body = {"total_count": 1, "check_runs": [entry]}
    else:
        entry = {"id": 1, "context": "lint", "state": "success"}
        if source is not None:
            entry["creator"] = source
        endpoint = "statuses"
        body = [entry]
    recorder = Recorder(
        {
            ("POST", "/app/installations/456/access_tokens"): TOKEN,
            ("GET", f"/repos/example/red-alpha/commits/head/{endpoint}"): body,
        }
    )
    app = _app(recorder)
    if evidence == "check":
        assert app.check_runs("example/red-alpha", "head")[0].app_id == expected
    else:
        assert app.commit_statuses("example/red-alpha", "head")[0].creator_id == expected


def test_commit_statuses_keep_latest_state_for_each_context_and_creator() -> None:
    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(200, json=TOKEN)
        if request.url.params.get("page") == "2":
            return httpx.Response(
                200,
                json=[
                    {"id": 2, "context": "lint", "state": "failure", "creator": {"id": 42}},
                    {"id": 1, "context": "lint", "state": "success", "creator": {"id": 42}},
                ],
            )
        return httpx.Response(
            200,
            json=[{"id": 3, "context": "lint", "state": "success", "creator": {"id": 43}}],
            headers={"Link": '<https://api.github.com/statuses?page=2>; rel="next"'},
        )

    statuses = _app(routed).commit_statuses("example/red-alpha", "head")
    assert [(s.id, s.creator_id, s.state) for s in statuses] == [
        (3, 43, "success"),
        (2, 42, "failure"),
    ]


@pytest.mark.parametrize("missing", ["status", "sha"])
def test_compare_files_refuses_missing_content_evidence(missing) -> None:
    detail = {"filename": "src/x.py", "status": "modified", "sha": "blob-sha"}
    del detail[missing]
    recorder = Recorder(
        {
            ("POST", "/app/installations/456/access_tokens"): TOKEN,
            ("GET", "/repos/example/red-alpha/compare/base...head"): {
                "merge_base_commit": {"sha": "merge-base"},
                "files": [detail],
            },
        }
    )
    with pytest.raises(GitHubError):
        _app(recorder).compare_files("example/red-alpha", "base", "head")


def test_ci_check_runs_read_every_page_without_an_app_filter() -> None:
    requests = []

    def routed(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(200, json=TOKEN)
        assert "app_id" not in request.url.params and "check_name" not in request.url.params
        if request.url.params.get("page") == "2":
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "check_runs": [
                        {"id": 2, "name": "lint", "status": "in_progress", "conclusion": None},
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "check_runs": [
                    {"id": 1, "name": "test-unit", "status": "completed", "conclusion": "success"},
                ],
            },
            headers={"Link": '<https://api.github.com/checks?page=2>; rel="next"'},
        )

    result = _app(routed).check_runs("example/red-alpha", "head")
    assert result[-1] == CheckRun(2, "in_progress", None, "lint")
    assert len(requests) == 3


def test_check_runs_refuse_incomplete_pagination() -> None:
    recorder = Recorder(
        {
            ("POST", "/app/installations/456/access_tokens"): TOKEN,
            ("GET", "/repos/example/red-alpha/commits/head/check-runs"): {
                "total_count": 2,
                "check_runs": [
                    {"id": 1, "name": "lint", "status": "completed", "conclusion": "success"}
                ],
            },
        }
    )
    with pytest.raises(GitHubError, match="incomplete check runs"):
        _app(recorder).check_runs("example/red-alpha", "head")


@pytest.mark.parametrize("source", ["list", "merged", "timeline"])
@pytest.mark.parametrize("repeat", [False, True], ids=["endless", "repeated"])
def test_pagination_is_bounded(source: str, repeat: bool) -> None:
    calls = 0
    limit = getattr(github, "MAX_PAGES", 100)

    def routed(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert calls <= limit, "pagination exceeded its request budget"
        cursor = "same" if repeat else str(calls)
        return httpx.Response(
            200,
            json=[],
            headers={"Link": f'<https://api.github.com/next?cursor={cursor}>; rel="next"'},
        )

    app = _contents_app(routed)
    with pytest.raises(GitHubError, match="pagination"):
        if source == "list":
            app._paged_list("/first")
        elif source == "merged":
            app.merged_pulls("o/r", base="main", since=T0)
        else:
            app.ready_for_review_at("o/r", 1)
    assert calls == (2 if repeat else limit)
    app.close()


def test_installation_token_comes_from_a_signed_app_jwt() -> None:
    recorder = Recorder({("POST", "/app/installations/456/access_tokens"): TOKEN})
    app = _app(recorder)
    assert app.token() == "ghs_installation"
    request = recorder.requests[0]
    bearer = request.headers["authorization"].removeprefix("Bearer ")
    claims = jwt.decode(bearer, PUBLIC, algorithms=["RS256"])
    assert claims["iss"] == "123" and claims["exp"] - claims["iat"] <= 600
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["x-github-api-version"] == "2022-11-28"
    assert app.token() == "ghs_installation" and len(recorder.requests) == 1  # cached


def test_token_is_renewed_sixty_seconds_before_expiry() -> None:
    soon = {"token": "first", "expires_at": (T0 + timedelta(seconds=70)).isoformat()}
    recorder = Recorder({("POST", "/app/installations/456/access_tokens"): soon})
    app = _app(recorder)
    assert app.token() == "first"
    recorder.responses[("POST", "/app/installations/456/access_tokens")] = TOKEN
    app._clock = lambda: T0 + timedelta(seconds=15)  # 55 s left: renew
    assert app.token() == "ghs_installation"


def test_open_pulls_and_pull_carry_what_the_reviewer_needs() -> None:
    raw = {
        "number": 7,
        "title": "feat: x",
        "body": "why",
        "draft": False,
        "user": {"login": "hawkixs"},
        "head": {"sha": "a" * 40, "ref": "feat/x"},
        "base": {"sha": "b" * 40, "ref": "main"},
        "labels": [{"name": "rail-review:rerun"}],
        "additions": 120,
        "deletions": 30,
        "changed_files": 4,
    }
    recorder = Recorder(
        {
            ("POST", "/app/installations/456/access_tokens"): TOKEN,
            ("GET", "/repos/hawkixs/red-rail/pulls"): [raw],
            ("GET", "/repos/hawkixs/red-rail/pulls/7"): raw,
        }
    )
    app = _app(recorder)
    pulls = app.open_pulls("hawkixs/red-rail")
    assert [p.number for p in pulls] == [7]
    pr = app.pull("hawkixs/red-rail", 7)
    assert pr == PullRequest(
        repository="hawkixs/red-rail",
        number=7,
        title="feat: x",
        body="why",
        draft=False,
        author="hawkixs",
        head_sha="a" * 40,
        base_sha="b" * 40,
        labels=("rail-review:rerun",),
        additions=120,
        deletions=30,
        changed_files=4,
    )
    assert recorder.requests[1].headers["authorization"] == "Bearer ghs_installation"


def test_diff_and_commit_messages() -> None:
    recorder = Recorder(
        {
            ("POST", "/app/installations/456/access_tokens"): TOKEN,
            ("GET", "/repos/hawkixs/red-rail/pulls/7"): "diff --git a/x b/x\n+1\n",
            ("GET", "/repos/hawkixs/red-rail/pulls/7/commits"): [
                {"commit": {"message": "feat: x\n\nCo-Authored-By: Claude Opus 5 <n@a>"}},
                {"commit": {"message": "fix: y"}},
            ],
        }
    )
    app = _app(recorder)
    assert app.diff("hawkixs/red-rail", 7) == "diff --git a/x b/x\n+1\n"
    assert recorder.requests[1].headers["accept"] == "application/vnd.github.diff"
    assert app.commit_messages("hawkixs/red-rail", 7) == [
        "feat: x\n\nCo-Authored-By: Claude Opus 5 <n@a>",
        "fix: y",
    ]


def test_commit_shas_are_the_pull_request_commits() -> None:
    """An agent's evidence must cite one of these: what the pull request already publishes."""
    recorder = Recorder(
        {
            ("POST", "/app/installations/456/access_tokens"): TOKEN,
            ("GET", "/repos/hawkixs/red-rail/pulls/7/commits"): [
                {"sha": "a" * 40, "commit": {"message": "feat: x"}},
                {"sha": "b" * 40, "commit": {"message": "fix: y"}},
            ],
        }
    )
    assert _app(recorder).commit_shas("hawkixs/red-rail", 7) == ["a" * 40, "b" * 40]


def test_commit_shas_reads_every_page() -> None:
    """A pull request longer than one page keeps all its commits citable (codex, red-rail#96)."""
    pages = {"1": [{"sha": f"{n:040x}"} for n in range(100)], "2": [{"sha": "f" * 40}]}

    class Paged(Recorder):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/pulls/7/commits"):
                self.requests.append(request)
                return httpx.Response(200, json=pages.get(request.url.params.get("page", "1"), []))
            return super().__call__(request)

    app = _app(Paged({("POST", "/app/installations/456/access_tokens"): TOKEN}))
    shas = app.commit_shas("hawkixs/red-rail", 7)
    assert len(shas) == 101 and shas[-1] == "f" * 40


def test_check_run_lifecycle_and_lookup() -> None:
    recorder = Recorder(
        {
            ("POST", "/app/installations/456/access_tokens"): TOKEN,
            ("POST", "/repos/hawkixs/red-rail/check-runs"): {"id": 99, "status": "in_progress"},
            ("PATCH", "/repos/hawkixs/red-rail/check-runs/99"): {
                "id": 99,
                "status": "completed",
                "conclusion": "success",
            },
            ("GET", f"/repos/hawkixs/red-rail/commits/{'a' * 40}/check-runs"): {
                "total_count": 1,
                "check_runs": [{"id": 99, "status": "completed", "conclusion": "success"}],
            },
        }
    )
    app = _app(recorder)
    started = app.start_check("hawkixs/red-rail", "a" * 40, name="red-rail/review")
    assert started == CheckRun(id=99, status="in_progress", conclusion=None)
    body = json.loads(recorder.requests[1].content)
    assert body["name"] == "red-rail/review" and body["head_sha"] == "a" * 40
    assert body["status"] == "in_progress"
    done = app.complete_check(
        "hawkixs/red-rail", 99, conclusion="success", title="approve", summary="ok", text="…"
    )
    assert done.conclusion == "success"
    patch = json.loads(recorder.requests[2].content)
    assert patch["status"] == "completed" and patch["output"]["title"] == "approve"
    found = app.check_runs("hawkixs/red-rail", "a" * 40, name="red-rail/review")
    assert found == [CheckRun(id=99, status="completed", conclusion="success")]
    assert recorder.requests[3].url.params["app_id"] == "123"
    assert recorder.requests[3].url.params["check_name"] == "red-rail/review"


def test_review_and_label_removal() -> None:
    recorder = Recorder(
        {
            ("POST", "/app/installations/456/access_tokens"): TOKEN,
            ("POST", "/repos/hawkixs/red-rail/pulls/7/reviews"): {"id": 5, "state": "APPROVED"},
            ("DELETE", "/repos/hawkixs/red-rail/issues/7/labels/rail-review:rerun"): [],
        }
    )
    app = _app(recorder)
    app.review("hawkixs/red-rail", 7, commit_id="a" * 40, event="APPROVE", body="LGTM")
    posted = json.loads(recorder.requests[1].content)
    assert posted == {"commit_id": "a" * 40, "event": "APPROVE", "body": "LGTM"}
    app.remove_label("hawkixs/red-rail", 7, "rail-review:rerun")
    assert recorder.requests[2].method == "DELETE"
    with pytest.raises(ValueError):
        app.review("hawkixs/red-rail", 7, commit_id="a" * 40, event="COMMENT_LOUDLY", body="")


def test_compare_diff_and_check_run_text() -> None:
    recorder = Recorder(
        {
            ("POST", "/app/installations/456/access_tokens"): TOKEN,
            (
                "GET",
                f"/repos/hawkixs/red-rail/compare/{'b' * 40}...{'a' * 40}",
            ): "diff --git a/x b/x\n+1\n",
            ("GET", "/repos/hawkixs/red-rail/check-runs/99"): {
                "id": 99,
                "output": {"text": "earlier verdict"},
            },
            ("GET", "/repos/hawkixs/red-rail/check-runs/1"): {"id": 1},
        }
    )
    app = _app(recorder)
    assert app.compare_diff("hawkixs/red-rail", "b" * 40, "a" * 40) == "diff --git a/x b/x\n+1\n"
    assert recorder.requests[1].headers["accept"] == "application/vnd.github.diff"
    assert app.check_run_text("hawkixs/red-rail", 99) == "earlier verdict"
    assert app.check_run_text("hawkixs/red-rail", 1) == ""


def test_errors_are_explicit_and_bounded() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(403, json={"message": "Resource not accessible by integration"})

    app = GitHubApp(
        app_id=1, installation_id=2, private_key_pem=PEM, transport=httpx.MockTransport(failing)
    )
    with pytest.raises(GitHubError, match="403 .*not accessible"):
        app.pull("hawkixs/red-rail", 7)

    def huge(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(200, text="x" * (2 * 1024 * 1024 + 1))

    app = GitHubApp(
        app_id=1, installation_id=2, private_key_pem=PEM, transport=httpx.MockTransport(huge)
    )
    with pytest.raises(GitHubError, match="too large"):
        app.diff("hawkixs/red-rail", 7)


def test_diff_falls_back_to_the_file_list_when_github_refuses_a_large_one() -> None:
    """GitHub caps the unified diff at 20 000 lines and answers 406. The first real external
    repository hit it on its first pull request — a rewrite whose bulk is the deletion of the
    previous implementation. Refusing to review is not an option the rail can take: the
    verdict gate is fail-closed, so a reviewer that cannot read a large change blocks a
    legitimate one for ever."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.url.path}?{request.url.params}")
        if request.url.path.endswith("/pulls/2") and "diff" in request.headers["Accept"]:
            return httpx.Response(
                406, json={"message": "Sorry, the diff exceeded the maximum number of lines"}
            )
        if request.url.path.endswith("/pulls/2/files"):
            page = request.url.params.get("page", "1")
            if page == "1":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "filename": "main.go",
                            "status": "added",
                            "patch": "@@ -0,0 +1 @@\n+package main",
                        },
                        {"filename": "logo.png", "status": "added"},  # binary: no patch
                    ],
                )
            return httpx.Response(200, json=[])
        raise AssertionError(request.url.path)

    def with_token(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(200, json=TOKEN)
        return handler(request)

    client = GitHubApp(
        app_id=1, installation_id=2, private_key_pem=PEM, transport=httpx.MockTransport(with_token)
    )
    diff = client.diff("hawkixs/red-alerts", 2)

    assert "diff --git a/main.go b/main.go" in diff
    assert "+package main" in diff
    # a file whose patch GitHub omits must be named, not silently dropped: the reviewer has
    # to know it changed even though it cannot read how
    assert "logo.png" in diff and "no patch" in diff
    assert any("/files" in c for c in calls), "the fallback actually asked for the file list"


def test_metrics_github_reads_follow_pages_and_fail_closed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(200, json=TOKEN)
        calls.append(str(request.url))
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"ok": 2}])
        if request.url.path == "/object":
            return httpx.Response(200, json={"bad": True})
        return httpx.Response(
            200,
            json=[{"ok": 1}],
            headers={"Link": '<https://api.github.com/next?page=2>; rel="next"'},
        )

    app = _contents_app(handler)
    assert app._paged_list("/first") == [{"ok": 1}, {"ok": 2}]
    assert len(calls) == 2

    with pytest.raises(GitHubError, match="incomplete answer"):
        app._paged_list("/object")


def test_metrics_github_rejects_failed_or_external_next_page() -> None:
    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(200, json=TOKEN)
        if request.url.path == "/first":
            return httpx.Response(
                200, json=[1], headers={"Link": '<https://api.github.com/second>; rel="next"'}
            )
        if request.url.path == "/second":
            return httpx.Response(500, json={"message": "failed"})
        return httpx.Response(
            200, json=[1], headers={"Link": '<https://example.org/steal>; rel="next"'}
        )

    app = _contents_app(routed)
    with pytest.raises(GitHubError):
        app._paged_list("/first")
    with pytest.raises(GitHubError):
        app._paged_list("/external")


def test_default_branch_and_metrics_sources() -> None:
    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(200, json=TOKEN)
        if request.url.path == "/repos/o/r":
            value = {"default_branch": "main"}
        elif request.url.path.endswith("/timeline"):
            value = [{"event": "ready_for_review", "created_at": "2026-10-10T00:00:00Z"}]
        elif request.url.path.endswith("/pulls"):
            value = [
                {
                    "number": 3,
                    "created_at": "2026-10-09T00:00:00Z",
                    "updated_at": "2026-10-12T00:00:00Z",
                    "merged_at": "2026-10-12T00:00:00Z",
                    "draft": False,
                }
            ]
        elif request.url.path.endswith("/commits"):
            value = [
                {
                    "commit": {
                        "committer": {"date": "2026-10-12T00:00:00Z"},
                        "message": 'Revert "bad change\nMore text',
                    }
                }
            ]
        else:
            value = {}
        return httpx.Response(200, json=value)

    app = _contents_app(routed)
    assert app.default_branch("o/r") == "main"
    with pytest.raises(GitHubError):
        app.default_branch("o/missing")
    since = datetime(2026, 10, 10, tzinfo=UTC)
    pulls = app.merged_pulls("o/r", base="main", since=since)
    assert pulls == [
        MergedPull(3, datetime(2026, 10, 12, tzinfo=UTC), datetime(2026, 10, 10, tzinfo=UTC))
    ]
    commits = app.commits_since("o/r", branch="main", since=since)
    assert commits == [(datetime(2026, 10, 12, tzinfo=UTC), 'Revert "bad change')]


def test_never_drafted_pull_uses_created_at_and_pulls_stop_at_since() -> None:
    seen: list[str] = []

    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(200, json=TOKEN)
        seen.append(request.url.path)
        if request.url.path.endswith("/pulls"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 1,
                        "created_at": "2026-10-12T00:00:00Z",
                        "updated_at": "2026-10-12T00:00:00Z",
                        "merged_at": "2026-10-12T00:00:00Z",
                    },
                    {
                        "number": 2,
                        "created_at": "2026-10-01T00:00:00Z",
                        "updated_at": "2026-10-01T00:00:00Z",
                        "merged_at": "2026-10-01T00:00:00Z",
                    },
                ],
            )
        return httpx.Response(200, json=[])

    app = _contents_app(routed)
    pulls = app.merged_pulls("o/r", base="main", since=datetime(2026, 10, 10, tzinfo=UTC))
    # the page whose last update predates `since` is still read in full, then the listing stops
    assert [p.number for p in pulls] == [1]
    assert pulls[0].ready_at == datetime(2026, 10, 12, tzinfo=UTC)
    assert seen == ["/repos/o/r/pulls", "/repos/o/r/issues/1/timeline"]


def _contents_app(handler) -> GitHubApp:
    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_tokens"):
            return httpx.Response(200, json=TOKEN)
        return handler(request)

    return GitHubApp(
        app_id=1, installation_id=2, private_key_pem=PEM, transport=httpx.MockTransport(routed)
    )


def test_file_at_reads_one_file_at_a_ref_as_raw_text() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"line one\nline two\n")

    app = _contents_app(handler)
    assert app.file_at("hawkixs/red-rail", "src/a b.py", "c" * 40) == "line one\nline two\n"
    [request] = seen
    assert request.url.raw_path.startswith(b"/repos/hawkixs/red-rail/contents/src/a%20b.py?")
    assert request.url.params["ref"] == "c" * 40
    assert request.headers["accept"] == "application/vnd.github.raw+json"


def test_file_at_answers_none_for_a_path_absent_at_the_ref() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    assert _contents_app(handler).file_at("hawkixs/red-rail", "404.md", "c" * 40) is None


def test_file_at_refuses_what_a_judge_cannot_read() -> None:
    """Binary or oversized content is an error with a reason, never an excerpt of garbage, and
    never confused with an absent file."""
    binary = _contents_app(lambda r: httpx.Response(200, content=b"\x89PNG\x00\xff\xfe"))
    with pytest.raises(GitHubError, match="not text"):
        binary.file_at("hawkixs/red-rail", "logo.png", "c" * 40)
    huge = _contents_app(lambda r: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)))
    with pytest.raises(GitHubError, match="too large"):
        huge.file_at("hawkixs/red-rail", "big.txt", "c" * 40)
    denied = _contents_app(lambda r: httpx.Response(403, json={"message": "no"}))
    with pytest.raises(GitHubError, match="403"):
        denied.file_at("hawkixs/red-rail", "a.py", "c" * 40)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "../../other-repo/contents/x",
        "src/../../x",
        "src/./a.py",
        "src//a.py",
        "src/",
        "src\\a.py",
        "a\x00b",
        "a\nb",
        "a?ref=main",
        "a#b",
    ],
)
def test_file_at_refuses_a_path_that_could_leave_the_file(path: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"x")

    with pytest.raises(GitHubError, match="refused path"):
        _contents_app(handler).file_at("hawkixs/red-rail", path, "c" * 40)
    assert seen == []


def test_an_empty_commit_message_reads_as_an_empty_first_line() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"commit": {"committer": {"date": "2026-10-12T00:00:00Z"}, "message": ""}}],
        )

    app = _contents_app(handler)
    since = datetime(2026, 10, 10, tzinfo=UTC)
    assert app.commits_since("o/r", branch="main", since=since) == [
        (datetime(2026, 10, 12, tzinfo=UTC), "")
    ]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/pulls", [{"number": 1, "merged_at": "2026-10-12T00:00:00Z"}]),
        ("/pulls", ["not an object"]),
        ("/commits", [{"commit": {}}]),
        ("/timeline", [{"event": "ready_for_review"}]),
    ],
    ids=["pull-without-dates", "pull-not-an-object", "commit-without-date", "event-without-date"],
)
def test_an_entry_out_of_shape_is_a_github_error_never_a_crash(path: str, body: list) -> None:
    """`publish` counts a GitHubError as one repository unread; any other exception would
    stop the whole run."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(path):
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=[])

    app = _contents_app(handler)
    since = datetime(2026, 10, 10, tzinfo=UTC)
    with pytest.raises(GitHubError):
        if path == "/commits":
            app.commits_since("o/r", branch="main", since=since)
        elif path == "/timeline":
            app.ready_for_review_at("o/r", 1)
        else:
            app.merged_pulls("o/r", base="main", since=since)
