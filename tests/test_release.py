"""Stage 7 from the host: every subprocess goes through an injectable runner, so the tests
exercise the real git repository and fake `gh`, `docker`, `git fetch/push/ls-remote`."""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from rail.release import ReleaseError, build_and_push, preflight, release
from tests.helpers import commit_all, conforming_tree, git, with_evidence, write_manifest

DIGEST = "sha256:" + "d" * 64


class FakeHost:
    """`gh`, `docker` and the network side of git are answered here; everything else runs."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.stdins: list[str | None] = []
        self.pushed_tags: list[str] = []
        self.fail: set[str] = set()

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        self.stdins.append(kwargs.get("input"))
        head = args[:2]
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 0, stdout="gho_secret\n", stderr="")
        if args[0] == "docker":
            if "push" in args and "push" in self.fail:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="denied")
            if "inspect" in args:
                repo = next(a for a in args if a.startswith("ghcr.io/")).split(":")[0]
                return subprocess.CompletedProcess(
                    args, 0, stdout=json.dumps([f"{repo}@{DIGEST}"]), stderr=""
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if head == ["git", "fetch"] or (args[0] == "git" and "ls-remote" in args):
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[0] == "git" and "push" in args:
            if "gitlab" in args and "gitlab" in self.fail:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="mirror down")
            self.pushed_tags.append(args[-1])
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.run(args, **kwargs)


def _repo(tmp_path: Path, *, integrated: bool = True) -> Path:
    repo = conforming_tree(tmp_path, "red-probe", "prod")
    (repo / "Dockerfile").write_text("FROM scratch\n")
    commit_all(repo, "feat: the probe")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    if integrated:
        with_evidence(repo, through="integrate")
    return repo


def test_preflight_measures_everything_before_touching_anything(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    host = FakeHost()
    plan = preflight(repo, "0.1.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=host)
    assert plan.project == "red-probe" and plan.tag == "v0.1.0"
    assert plan.image_repository == "ghcr.io/hawkixs/red-probe"
    assert plan.image_tag == "ghcr.io/hawkixs/red-probe:0.1.0"
    assert plan.sha == git(repo, "rev-parse", "HEAD")
    assert plan.changelog[0] == "feat: the probe" and plan.previous_tag is None
    with pytest.raises(ReleaseError, match="semantic version"):
        preflight(repo, "v1", ledger=FileLedger(repo / RECEIPTS_DIR), run=host)


def test_preflight_refusals(tmp_path: Path) -> None:
    host = FakeHost()
    repo = _repo(tmp_path, integrated=False)
    with pytest.raises(ReleaseError, match="no integration receipt"):
        preflight(repo, "0.1.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=host)
    repo = _repo(tmp_path / "b")
    (repo / "README.md").write_text("dirty\n")
    with pytest.raises(ReleaseError, match="outside docs/receipts"):
        preflight(repo, "0.1.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=host)
    git(repo, "checkout", "-q", "--", "README.md")
    (repo / "docs" / "receipts" / "stray.txt").write_text("notes")  # a mirror is not code
    git(repo, "tag", "v0.1.0", "HEAD~1")  # a tag that names another commit is taken
    with pytest.raises(ReleaseError, match="already exists locally and names"):
        preflight(repo, "0.1.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=host)
    git(repo, "checkout", "-q", "-b", "feat/x")
    with pytest.raises(ReleaseError, match="from main"):
        preflight(repo, "0.2.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=host)


def test_build_and_push_logs_in_on_stdin_and_reads_the_digest(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    host = FakeHost()
    plan = preflight(repo, "0.1.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=host)
    from rail.release import login

    login(plan, run=host)
    digest = build_and_push(plan, repo, run=host)
    assert digest == DIGEST
    login_call = next(c for c in host.calls if c[:2] == ["docker", "login"])
    assert "--password-stdin" in login_call and "gho_secret" not in " ".join(login_call)
    assert host.stdins[host.calls.index(login_call)] == "gho_secret\n"
    build = next(c for c in host.calls if c[:2] == ["docker", "build"])
    assert f"GIT_SHA={plan.sha}" in build and "VERSION=0.1.0" in build
    assert "--platform" in build and "linux/amd64" in build
    assert ["docker", "push", plan.image_tag] in host.calls


def test_release_end_to_end_attests_and_tags_github(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    host = FakeHost()
    outcome = release(repo, "0.1.0", run=host, issuer="operator")
    assert outcome.digest == DIGEST
    assert host.pushed_tags == ["refs/tags/v0.1.0"]  # GitHub only: no mirror declared
    assert git(repo, "tag", "-l", "v0.1.0") == "v0.1.0"
    assert "feat: the probe" in git(repo, "tag", "-l", "--format=%(contents)", "v0.1.0")
    released = FileLedger(repo / RECEIPTS_DIR).list(
        "red-probe", attestation=AttestationKind.RELEASED
    )
    assert len(released) == 1
    assert released[0].data["digest"] == DIGEST
    assert released[0].data["image"] == f"ghcr.io/hawkixs/red-probe@{DIGEST}"
    assert released[0].data["version"] == "0.1.0" and released[0].data["tag"] == "v0.1.0"
    assert released[0].idempotency_key == "released:0.1.0"


def _declare_mirror(repo: Path, host: str = "gitlab.hawkixs.local") -> None:
    """The manifest keeps a mirror (decision 30acbbde made GitHub the only default)."""
    write_manifest(
        repo,
        project="red-probe",
        tier="prod",
        deploy=True,
        gates={"hygiene.mirror_host": (host, "this project keeps its mirror")},
    )
    commit_all(repo, "chore(rail): keep the mirror")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


def test_the_tag_reaches_the_mirror_only_when_the_manifest_declares_one(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    host = FakeHost()
    plan = preflight(repo, "0.1.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=host)
    assert plan.mirror_remote is None
    pushes = [step for step in plan.steps() if step.startswith("git push")]
    assert pushes == ["git push origin refs/tags/v0.1.0"]
    release(repo, "0.1.0", run=host, issuer="operator")
    assert host.pushed_tags == ["refs/tags/v0.1.0"]
    _declare_mirror(repo)
    host = FakeHost()
    plan = preflight(repo, "0.1.1", ledger=FileLedger(repo / RECEIPTS_DIR), run=host)
    assert plan.mirror_remote == "gitlab"
    assert plan.steps().count("git push gitlab refs/tags/v0.1.1") == 1
    release(repo, "0.1.1", run=host, issuer="operator")
    assert host.pushed_tags == ["refs/tags/v0.1.1", "refs/tags/v0.1.1"]


def test_a_declared_mirror_without_its_remote_stops_the_preflight(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _declare_mirror(repo, host="mirror.example.invalid")
    with pytest.raises(ReleaseError, match="mirror.example.invalid"):
        preflight(repo, "0.1.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=FakeHost())


def test_the_mirror_remote_is_found_by_its_exact_host_whatever_its_name(tmp_path: Path) -> None:
    """Pre-review finding on PR #9: a substring match would push the tag to a remote whose URL
    merely contains the declared host (in its path, or as a longer hostname)."""
    repo = _repo(tmp_path)
    git(repo, "remote", "remove", "gitlab")
    git(repo, "remote", "add", "a-decoy", "ssh://git@gitlab.hawkixs.local.example/red/x.git")
    git(repo, "remote", "add", "b-decoy", "git@github.com:hawkixs/gitlab.hawkixs.local-tools.git")
    _declare_mirror(repo)
    with pytest.raises(ReleaseError, match="no remote points there"):
        preflight(repo, "0.1.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=FakeHost())
    git(repo, "remote", "add", "mirror", "ssh://git@GitLab.hawkixs.local:2222/red/red-probe.git")
    plan = preflight(repo, "0.1.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=FakeHost())
    assert plan.mirror_remote == "mirror"
    assert "git push mirror refs/tags/v0.1.0" in plan.steps()


def test_a_mirror_failure_after_github_says_what_not_to_do(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _declare_mirror(repo)
    host = FakeHost()
    host.fail.add("gitlab")
    with pytest.raises(ReleaseError, match="do not delete the GitHub tag"):
        release(repo, "0.1.0", run=host, issuer="operator")


def test_cli_plan_and_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    from rail.commands import release as release_command

    host = FakeHost()
    monkeypatch.setattr(release_command, "RUN", host)  # the command's injectable runner
    out = CliRunner().invoke(main, ["release", "--repo", str(repo), "--version", "0.1.0", "--plan"])
    assert out.exit_code == 0, out.output
    assert "docker push ghcr.io/hawkixs/red-probe:0.1.0" in out.output
    assert not any(c[0] == "docker" for c in host.calls)
    out = CliRunner().invoke(
        main, ["release", "--repo", str(repo), "--version", "0.1.0", "--yes", "--json"]
    )
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["payload"]["data"]["digest"] == DIGEST


def test_a_release_whose_tag_already_names_head_resumes(tmp_path: Path) -> None:
    """A mirror push or an attestation failed last time: the tag exists and names HEAD, the
    build and the push are idempotent, the release completes instead of refusing."""
    repo = _repo(tmp_path)
    host = FakeHost()
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "-a", "v0.1.0", "-m", "first attempt")
    plan = preflight(repo, "0.1.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=host)
    assert plan.tag_local and not plan.tag_on_origin
    outcome = release(repo, "0.1.0", run=host, issuer="operator")
    assert outcome.digest == DIGEST
    assert not any(c[:2] == ["git", "tag"] for c in host.calls)  # not created twice
    assert host.pushed_tags == ["refs/tags/v0.1.0"]  # GitHub only: no mirror declared
    commit_all(repo, "feat: later")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    with pytest.raises(ReleaseError, match="already exists locally and names"):
        preflight(repo, "0.1.0", ledger=FileLedger(repo / RECEIPTS_DIR), run=host)
    assert head != git(repo, "rev-parse", "HEAD")
