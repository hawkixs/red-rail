"""The App client: JWT → installation token, then the few calls the reviewer needs. Every
request is asserted on path, method, headers and body; nothing reaches the network."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from rail.reviewer.github import CheckRun, GitHubApp, GitHubError, PullRequest

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
