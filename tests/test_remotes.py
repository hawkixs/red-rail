"""Publishing uses the host's gh — and glab only for a declared mirror; here they are fakes that
create local bare repos."""

import json
import subprocess
from pathlib import Path

import pytest

from rail import remotes
from rail.remotes import RemoteError, publish
from tests.helpers import commit_all, init_repo


class FakeHosts:
    """`gh`/`glab` calls are simulated; every other command runs for real."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[list[str]] = []

    def _bare(self, tool: str, path: str) -> Path:
        return self.root / tool / f"{path.rsplit('/', 1)[-1]}.git"

    def __call__(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[0] in ("gh", "glab"):
            self.calls.append(args)
            if args[0] == "glab":
                env = kwargs.get("env")
                assert isinstance(env, dict) and env.get("GITLAB_HOST") == "gitlab.hawkixs.local"
            bare = self._bare(args[0], args[3])
            if args[2] == "view":
                return subprocess.CompletedProcess(args, 0 if bare.exists() else 1, "", "not found")
            if args[2] == "create":
                subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
                return subprocess.CompletedProcess(args, 0, str(bare), "")
            raise AssertionError(args)
        return subprocess.run(args, **kwargs)  # type: ignore[call-overload]


@pytest.fixture
def hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeHosts:
    fake = FakeHosts(tmp_path / "hosts")
    monkeypatch.setattr(remotes, "CANONICAL_URL", str(tmp_path / "hosts" / "gh" / "{slug}.git"))
    monkeypatch.setattr(remotes, "MIRROR_URL", str(tmp_path / "hosts" / "glab" / "{slug}.git"))
    return fake


def _local(tmp_path: Path) -> Path:
    repo = init_repo(tmp_path / "red-probe", remotes=False)
    (repo / "README.md").write_text("# red-probe\n")
    commit_all(repo, "chore: bootstrap red-probe with the ReD rail")
    return repo


def test_publish_creates_the_github_repository_only_by_default(
    tmp_path: Path, hosts: FakeHosts
) -> None:
    """ReD is GitHub only (decision 30acbbde): no glab call, no `gitlab` remote."""
    repo = _local(tmp_path)
    publish(repo, "red-probe", "A probe.", run=hosts)
    assert [c[:3] for c in hosts.calls] == [["gh", "repo", "view"], ["gh", "repo", "create"]]
    assert not (tmp_path / "hosts" / "glab").exists()
    names = subprocess.run(
        ["git", "-C", str(repo), "remote"], check=True, capture_output=True, text=True
    ).stdout.split()
    assert names == ["origin"]
    head = subprocess.run(
        ["git", "-C", str(tmp_path / "hosts" / "gh" / "red-probe.git"), "rev-parse", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert (
        head
        == subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


def test_publish_with_a_declared_mirror_creates_both_repositories_and_pushes_main(
    tmp_path: Path, hosts: FakeHosts
) -> None:
    repo = _local(tmp_path)
    publish(repo, "red-probe", "A probe.", mirror="gitlab.hawkixs.local", run=hosts)
    assert [c[:3] for c in hosts.calls] == [
        ["gh", "repo", "view"],
        ["glab", "repo", "view"],
        ["gh", "repo", "create"],
        ["glab", "repo", "create"],
    ]
    assert hosts.calls[2][3:] == ["hawkixs/red-probe", "--private", "--description", "A probe."]
    assert hosts.calls[3][3:5] == ["hawkixs_project/red/red-probe", "--private"]
    assert all("GITLAB_HOST" not in str(c) for c in hosts.calls)  # the host travels in the env
    assert "--skipGitInit" in hosts.calls[3] and "--defaultBranch" in hosts.calls[3]
    for tool in ("gh", "glab"):
        head = subprocess.run(
            ["git", "-C", str(tmp_path / "hosts" / tool / "red-probe.git"), "rev-parse", "main"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert (
            head
            == subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "config", "remote.pushDefault"], capture_output=True, text=True
        ).stdout.strip()
        == "origin"
    )


def test_publish_refuses_a_taken_slug(tmp_path: Path, hosts: FakeHosts) -> None:
    repo = _local(tmp_path)
    subprocess.run(
        ["git", "init", "-q", "--bare", str(tmp_path / "hosts" / "gh" / "red-probe.git")],
        check=True,
    )
    with pytest.raises(RemoteError, match="GitHub already has red-probe"):
        publish(repo, "red-probe", "A probe.", run=hosts)
    assert len(hosts.calls) == 1  # stopped at the first collision, nothing created


def test_second_push_failure_never_rewrites_the_first(
    tmp_path: Path, hosts: FakeHosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _local(tmp_path)
    monkeypatch.setattr(remotes, "MIRROR_URL", str(tmp_path / "nowhere" / "{slug}.git"))
    with pytest.raises(RemoteError, match="do not rewrite"):
        publish(repo, "red-probe", "A probe.", mirror="gitlab.hawkixs.local", run=hosts)
    assert (tmp_path / "hosts" / "gh" / "red-probe.git").is_dir()


def test_a_mirror_out_of_parity_after_the_pushes_is_an_error(
    tmp_path: Path, hosts: FakeHosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parity check is the last word of a mirrored publish: a mismatch is reported, never
    ignored (and the check does not run at all without a mirror)."""
    repo = _local(tmp_path)
    monkeypatch.setattr(remotes, "parity", lambda repo, *, run: False)
    with pytest.raises(RemoteError, match="main differs between GitHub and GitLab"):
        publish(repo, "red-probe", "A probe.", mirror="gitlab.hawkixs.local", run=hosts)
    publish(_local(tmp_path / "again"), "red-again", "A probe.", run=hosts)  # no mirror: no check


def test_a_missing_tool_is_a_remote_error(tmp_path: Path) -> None:
    """Review finding: gh/glab absent from PATH must not surface as a raw traceback."""
    repo = _local(tmp_path)

    def no_tools(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(args[0])

    with pytest.raises(RemoteError, match="gh"):
        publish(repo, "red-probe", "A probe.", run=no_tools)


def test_ensure_absent_distinguishes_absent_from_broken(tmp_path: Path) -> None:
    """Review finding: an auth or network failure of `gh repo view` is not 'the slug is free'."""
    calls: list[list[str]] = []

    def broken(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 4, "", "gh: authentication required (HTTP 401)")

    with pytest.raises(RemoteError, match="cannot tell"):
        remotes.ensure_absent("red-probe", run=broken)
    assert len(calls) == 1  # stopped at the first unanswerable question

    def absent(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 1, "", "GraphQL: Could not resolve to a Repository"
        )

    remotes.ensure_absent("red-probe", run=absent)  # no error: GitHub answered "not found"


def test_ensure_absent_asks_the_mirror_only_when_declared() -> None:
    asked: list[str] = []

    def absent(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        asked.append(args[0])
        return subprocess.CompletedProcess(args, 1, "", "not found")

    remotes.ensure_absent("red-probe", run=absent)
    assert asked == ["gh"]
    remotes.ensure_absent("red-probe", mirror="gitlab.hawkixs.local", run=absent)
    assert asked == ["gh", "gh", "glab"]


def _apps(answers: dict[str, str], calls: list[list[str]]):
    """A `gh` that answers `gh api apps/<slug>` from `answers`, 404 for a private App."""

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        slug = args[2].removeprefix("apps/")
        if slug in answers:
            return subprocess.CompletedProcess(args, 0, f"{answers[slug]}\n", "")
        return subprocess.CompletedProcess(args, 1, "", "gh: Not Found (HTTP 404)")

    return run


def _reviewer_config(tmp_path: Path, app_id: object = 424242) -> Path:
    """A private reviewer.yaml with a made-up App id; the key file is named, never read."""
    path = tmp_path / "reviewer.yaml"
    path.write_text(f"app_id: {app_id}\ninstallation_id: 2\nprivate_key_file: /nowhere/key.pem\n")
    path.chmod(0o600)
    return path


def test_the_reviewer_apps_id_comes_from_the_host_reviewer_config(tmp_path: Path) -> None:
    """The reviewer App is private: `gh api apps/<slug>` 404s for it, so its id is read from the
    reviewer config of the host — the one place it already is — and the public page is not asked."""
    calls: list[list[str]] = []
    found = remotes.app_id(
        remotes.REVIEWER_APP_SLUG, run=_apps({}, calls), reviewer_config=_reviewer_config(tmp_path)
    )
    assert found == 424242
    assert calls == []


def test_the_reviewer_apps_id_falls_back_to_the_public_page_without_a_config(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    found = remotes.app_id(
        remotes.REVIEWER_APP_SLUG,
        run=_apps({remotes.REVIEWER_APP_SLUG: "77"}, calls),
        reviewer_config=tmp_path / "absent.yaml",
    )
    assert found == 77
    assert [c[2] for c in calls] == [f"apps/{remotes.REVIEWER_APP_SLUG}"]


def test_the_reviewer_apps_id_error_names_both_sources(tmp_path: Path) -> None:
    config = tmp_path / "absent.yaml"
    with pytest.raises(RemoteError) as raised:
        remotes.app_id(remotes.REVIEWER_APP_SLUG, run=_apps({}, []), reviewer_config=config)
    message = str(raised.value)
    assert str(config) in message
    assert f"gh api apps/{remotes.REVIEWER_APP_SLUG}" in message
    assert "app_id" in message and "public" in message


@pytest.mark.parametrize("content", ["app_id: nope\n", "- a list\n", "installation_id: 2\n"])
def test_a_reviewer_config_without_a_usable_app_id_is_refused_not_skipped(
    tmp_path: Path, content: str
) -> None:
    """A config that is there but says nothing usable is a defect to name, not an absence to
    paper over with the public page."""
    config = tmp_path / "reviewer.yaml"
    config.write_text(content)
    config.chmod(0o600)
    calls: list[list[str]] = []
    with pytest.raises(RemoteError, match="app_id"):
        remotes.app_id(
            remotes.REVIEWER_APP_SLUG,
            run=_apps({remotes.REVIEWER_APP_SLUG: "77"}, calls),
            reviewer_config=config,
        )
    assert calls == []


def test_a_reviewer_config_that_is_not_private_is_refused(tmp_path: Path) -> None:
    config = _reviewer_config(tmp_path)
    config.chmod(0o644)
    with pytest.raises(RemoteError, match="mode"):
        remotes.app_id(remotes.REVIEWER_APP_SLUG, run=_apps({}, []), reviewer_config=config)


def test_any_other_app_keeps_the_public_endpoint(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    found = remotes.app_id(
        "github-actions",
        run=_apps({"github-actions": "15368"}, calls),
        reviewer_config=_reviewer_config(tmp_path),
    )
    assert found == 15368
    assert [c[2] for c in calls] == ["apps/github-actions"]


def test_protect_main_pins_the_reviewer_check_to_the_configured_app_id(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    bodies: list[dict] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[2].startswith("apps/"):
            return _apps({"github-actions": "101"}, [])(args, **kwargs)
        bodies.append(json.loads(str(kwargs["input"])))
        return subprocess.CompletedProcess(args, 0, "", "")

    remotes.protect_main(
        "red-probe",
        [("rail / ci", "github-actions"), ("red-rail/review", remotes.REVIEWER_APP_SLUG)],
        run=run,
        reviewer_config=_reviewer_config(tmp_path),
    )
    assert bodies[0]["required_status_checks"]["checks"] == [
        {"context": "rail / ci", "app_id": 101},
        {"context": "red-rail/review", "app_id": 424242},
    ]
    assert [c[2] for c in calls if c[2].startswith("apps/")] == ["apps/github-actions"]
