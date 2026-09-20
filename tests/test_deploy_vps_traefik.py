"""The target is a remote script under a lock plus an external verification; both are
exercised here without ssh, docker or a network."""

import json
import subprocess
from pathlib import Path

import pytest

from rail.deploy import Artefact, DeployError, Locked, domain_of
from rail.deploy.vps_traefik import LOCKED, Parameters, VpsTraefik, env_file, remote_script
from rail.http import HttpError
from rail.model import load_rail_config
from tests.helpers import commit_all, conforming_tree

DIGEST = "sha256:" + "a" * 64
IMAGE = f"ghcr.io/hawkixs/red-probe@{DIGEST}"
COMPOSE = "services:\n  app:\n    image: ${IMAGE_REFERENCE:?required}\n"


def _repo(tmp_path: Path) -> Path:
    repo = conforming_tree(tmp_path, "red-probe", "prod")
    (repo / "deploy").mkdir()
    (repo / "deploy" / "compose.yaml").write_text(COMPOSE)
    commit_all(repo, "feat: the stack")
    return repo


def _artefact(repo: Path, version: str = "0.1.0") -> Artefact:
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return Artefact(
        version=version, sha=head, digest=DIGEST, image=f"ghcr.io/hawkixs/red-probe@{DIGEST}"
    )


def _live(artefact: Artefact) -> dict[str, str]:
    return {
        "project": "red-probe",
        "version": artefact.version,
        "git_sha": artefact.sha,
        "image_digest": artefact.digest,
    }


class FakeHost:
    def __init__(self, *, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.scripts: list[str] = []
        self.argv: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.argv.append(list(args))
        if args[0] == "ssh":
            self.scripts.append(kwargs.get("input") or "")
            return subprocess.CompletedProcess(
                args,
                self.exit_code,
                stdout="[]",
                stderr="locked" if self.exit_code == LOCKED else "",
            )
        return subprocess.run(args, **kwargs)


class FakeWeb:
    """`/healthz` answers 503 `unhealthy_for` times, then 200; `/version` says `live`."""

    def __init__(self, live: dict[str, str], *, unhealthy_for: int = 0) -> None:
        self.live = live
        self.unhealthy_for = unhealthy_for
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float) -> tuple[int, bytes]:
        self.urls.append(url)
        if url.endswith("/healthz"):
            if self.unhealthy_for > 0:
                self.unhealthy_for -= 1
                return 503, b"starting"
            return 200, b'{"status":"ok"}'
        if url.endswith("/version"):
            return 200, json.dumps(self.live).encode()
        return 404, b""


def _clock():
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return clock, sleep


def test_remote_script_is_locked_and_carries_the_files() -> None:
    artefact = Artefact(
        version="0.1.0", sha="c" * 40, digest=DIGEST, image=f"ghcr.io/hawkixs/red-probe@{DIGEST}"
    )
    params = Parameters(
        "red-vps", "/opt", "pls_project_default", "letsencrypt", 120, "deploy/compose.yaml"
    )
    env = env_file("red-probe", artefact, "probe.hawkixs.com", params)
    script = remote_script("red-probe", "0.1.0", COMPOSE, env, params)
    assert script.startswith("set -euo pipefail\n")
    assert 'exec 9>"$root/.deploy.lock"' in script and f"exit {LOCKED}" in script
    assert "root=/opt/red-probe\nrelease=/opt/red-probe/releases/0.1.0\n" in script
    assert "cat > compose.yaml <<'__RAIL_COMPOSE.YAML__'\n" + COMPOSE in script
    assert "cat > .env <<'__RAIL_.ENV__'\n" + env in script
    assert "chmod 600 .env" in script
    assert (
        'docker compose --project-name red-probe --project-directory "$release" pull --quiet'
        in script
    )
    assert "up --detach --remove-orphans --wait --wait-timeout 120" in script
    assert 'ln -sfn "$release" "$root/current"' in script
    assert env == (
        "COMPOSE_PROJECT_NAME=red-probe\n"
        f"IMAGE_REFERENCE=ghcr.io/hawkixs/red-probe@{DIGEST}\n"
        f"IMAGE_DIGEST={DIGEST}\n"
        f"GIT_SHA={'c' * 40}\n"
        "VERSION=0.1.0\n"
        "DOMAIN=probe.hawkixs.com\n"
        "TRAEFIK_NETWORK=pls_project_default\n"
        "TRAEFIK_CERT_RESOLVER=letsencrypt\n"
    )
    with pytest.raises(DeployError, match="marker"):
        remote_script("red-probe", "0.1.0", "__RAIL_COMPOSE.YAML__", env, params)


def test_domain_comes_from_the_healthcheck() -> None:
    assert domain_of("https://probe.hawkixs.com/healthz") == "probe.hawkixs.com"
    with pytest.raises(DeployError):
        domain_of("healthz")


def test_apply_runs_the_script_over_ssh_then_verifies_from_outside(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    artefact = _artefact(repo)
    host, web = FakeHost(), FakeWeb(_live(artefact), unhealthy_for=2)
    clock, sleep = _clock()
    target = VpsTraefik(repo, load_rail_config(repo), run=host, http=web, sleep=sleep, clock=clock)
    live = target.apply(artefact)
    assert live.version == "0.1.0" and live.image_digest == DIGEST
    assert host.argv[-1][:2] == ["ssh", "-o"] and host.argv[-1][-3:] == ["red-vps", "bash", "-s"]
    assert "BatchMode=yes" in host.argv[-1]
    assert COMPOSE in host.scripts[0]  # the compose file at the released commit
    assert web.urls[:3] == ["https://red-probe.example.invalid/healthz"] * 3
    assert web.urls[-1] == "https://red-probe.example.invalid/version"


def test_apply_reports_the_lock_and_a_failed_script(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    cfg = load_rail_config(repo)
    artefact = _artefact(repo)
    with pytest.raises(Locked):
        VpsTraefik(repo, cfg, run=FakeHost(exit_code=LOCKED), http=FakeWeb(_live(artefact))).apply(
            artefact
        )
    with pytest.raises(DeployError, match="exit 1"):
        VpsTraefik(repo, cfg, run=FakeHost(exit_code=1), http=FakeWeb(_live(artefact))).apply(
            artefact
        )


def test_verify_refuses_a_live_service_that_is_not_the_artefact(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    cfg = load_rail_config(repo)
    artefact = _artefact(repo)
    clock, sleep = _clock()
    other = {**_live(artefact), "image_digest": "sha256:" + "b" * 64}
    target = VpsTraefik(repo, cfg, run=FakeHost(), http=FakeWeb(other), sleep=sleep, clock=clock)
    with pytest.raises(DeployError, match="image_digest"):
        target.verify(artefact)

    def down(url: str, timeout: float) -> tuple[int, bytes]:
        raise HttpError("refused")

    target = VpsTraefik(repo, cfg, run=FakeHost(), http=down, sleep=sleep, clock=clock)
    with pytest.raises(DeployError, match="not healthy within 120s"):
        target.verify(artefact)
    assert clock() >= 120


def test_compose_is_read_at_the_released_commit_and_steps_are_printable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    cfg = load_rail_config(repo)
    target = VpsTraefik(repo, cfg, run=FakeHost(), http=FakeWeb(_live(_artefact(repo))))
    released = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    (repo / "deploy" / "compose.yaml").write_text("services: {}\n")
    commit_all(repo, "feat: a later change")
    artefact = Artefact("0.1.0", released, DIGEST, f"ghcr.io/hawkixs/red-probe@{DIGEST}")
    steps = target.steps(artefact)
    assert COMPOSE in (steps[0].stdin or "")
    assert steps[0].argv[0] == "ssh" and steps[1].title.startswith(
        "GET https://red-probe.example.invalid/healthz"
    )
    with pytest.raises(DeployError, match="not committed"):
        target.compose_at("0" * 40)


def test_an_artefact_carries_only_characters_the_target_script_is_safe_with() -> None:
    """The version and the image reference land in a bash script and an .env file."""
    Artefact(version="0.1.0-rc.1", sha="c" * 40, digest=DIGEST, image=IMAGE)
    for field, value in (
        ("version", "0.1.0; rm -rf /"),
        ("sha", "not a sha"),
        ("digest", "sha256:short"),
        ("image", "ghcr.io/hawkixs/red-probe:latest"),
    ):
        kwargs = {
            "version": "0.1.0",
            "sha": "c" * 40,
            "digest": DIGEST,
            "image": IMAGE,
            field: value,
        }
        with pytest.raises(DeployError, match=f"artefact {field}"):
            Artefact(**kwargs)
