"""Publishing uses the host's gh and glab; here they are fakes that create local bare repos."""

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


def test_publish_creates_both_repositories_and_pushes_main(
    tmp_path: Path, hosts: FakeHosts
) -> None:
    repo = _local(tmp_path)
    publish(repo, "red-probe", "A probe.", run=hosts)
    assert [c[:3] for c in hosts.calls] == [
        ["gh", "repo", "view"],
        ["glab", "repo", "view"],
        ["gh", "repo", "create"],
        ["glab", "repo", "create"],
    ]
    assert hosts.calls[2][3:] == ["hawkixs/red-probe", "--private", "--description", "A probe."]
    assert hosts.calls[3][3:5] == ["hawkixs_project/red/red-probe", "--private"]
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
        publish(repo, "red-probe", "A probe.", run=hosts)
    assert (tmp_path / "hosts" / "gh" / "red-probe.git").is_dir()
