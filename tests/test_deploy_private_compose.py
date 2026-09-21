"""Target `private-compose`: no public route, verification over the private address, and a
compose file that may not publish where the firewall cannot reach."""

from pathlib import Path

import pytest

from rail.deploy import DeployError
from rail.deploy.private_compose import published_ports_are_private

BIND = "10.100.0.4"


def _compose(ports: str) -> str:
    return f"services:\n  app:\n    image: x\n{ports}"


def test_a_bare_port_mapping_publishes_on_every_interface(tmp_path: Path) -> None:
    """Docker bypasses ufw: `"8080:8080"` binds 0.0.0.0 whatever the firewall says. The
    machine defends itself twice over, but both defences live in its own configuration where
    the rail cannot see them and a later change can remove them silently."""
    offenders = published_ports_are_private(_compose('    ports:\n      - "8080:8080"\n'), BIND)
    assert offenders == [("app", "8080:8080")]


def test_an_explicit_private_address_is_accepted(tmp_path: Path) -> None:
    for mapping in (f'"{BIND}:8080:8080"', '"127.0.0.1:8080:8080"', '"[::1]:8080:8080"'):
        text = _compose(f"    ports:\n      - {mapping}\n")
        assert published_ports_are_private(text, BIND) == [], mapping


def test_another_host_address_is_refused(tmp_path: Path) -> None:
    text = _compose('    ports:\n      - "10.100.0.9:8080:8080"\n')
    assert published_ports_are_private(text, BIND) == [("app", "10.100.0.9:8080:8080")]


def test_the_long_form_is_read_as_well_as_the_short_one(tmp_path: Path) -> None:
    bad = _compose("    ports:\n      - target: 8080\n        published: 8080\n")
    assert published_ports_are_private(bad, BIND) == [("app", "8080")]
    good = _compose(
        f"    ports:\n      - target: 8080\n        published: 8080\n        host_ip: {BIND}\n"
    )
    assert published_ports_are_private(good, BIND) == []


def test_expose_is_not_publishing_and_no_ports_is_fine(tmp_path: Path) -> None:
    assert published_ports_are_private(_compose('    expose:\n      - "8080"\n'), BIND) == []
    assert published_ports_are_private(_compose(""), BIND) == []


def test_every_offending_service_is_named(tmp_path: Path) -> None:
    text = (
        "services:\n"
        '  api:\n    image: x\n    ports:\n      - "8080:8080"\n'
        f'  worker:\n    image: y\n    ports:\n      - "{BIND}:9000:9000"\n'
        '  ui:\n    image: z\n    ports:\n      - "3000:3000"\n'
    )
    assert published_ports_are_private(text, BIND) == [("api", "8080:8080"), ("ui", "3000:3000")]


def test_an_unreadable_compose_file_is_refused_rather_than_assumed_safe(tmp_path: Path) -> None:
    """Failing open on a parse error would make the whole check decorative."""
    with pytest.raises(DeployError, match="compose"):
        published_ports_are_private("services: [", BIND)


# -- the target ---------------------------------------------------------------------------

import json  # noqa: E402
import subprocess  # noqa: E402

from rail.deploy import Artefact  # noqa: E402
from rail.deploy.private_compose import PrivateCompose  # noqa: E402
from rail.model import load_rail_config  # noqa: E402
from tests.helpers import commit_all, conforming_tree, write_manifest  # noqa: E402

DIGEST = "sha256:" + "b" * 64
PRIVATE = f"http://{BIND}:9100/healthz"


def _private_repo(tmp_path: Path, compose: str) -> Path:
    repo = conforming_tree(tmp_path, "red-alerts", "prod")
    write_manifest(
        repo,
        project="red-alerts",
        tier="prod",
        gates={
            "deploy.ssh_host": ("red-base", "red-alerts lives on red-base, WireGuard only"),
            "deploy.bind_address": (BIND, "the machine publishes on its WireGuard address only"),
        },
        deploy=True,
    )
    manifest = (
        (repo / "rail.yaml")
        .read_text()
        .replace("  target: vps-traefik\n", "  target: private-compose\n")
    )
    manifest = "\n".join(
        f"  healthcheck: {PRIVATE}" if line.strip().startswith("healthcheck:") else line
        for line in manifest.splitlines()
    )
    (repo / "rail.yaml").write_text(manifest + "\n")
    (repo / "deploy").mkdir(exist_ok=True)
    (repo / "deploy" / "compose.yaml").write_text(compose)
    commit_all(repo, "feat: the stack")
    return repo


class RecordingHost:
    def __init__(self) -> None:
        self.argv: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.argv.append(list(args))
        if args[0] == "ssh":
            return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")
        return subprocess.run(args, **kwargs)


def _artefact(repo: Path) -> Artefact:
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return Artefact(
        version="0.1.0", sha=head, digest=DIGEST, image=f"ghcr.io/hawkixs/red-alerts@{DIGEST}"
    )


SAFE = f'services:\n  app:\n    image: x\n    ports:\n      - "{BIND}:9100:9100"\n'
PUBLIC = 'services:\n  app:\n    image: x\n    ports:\n      - "9100:9100"\n'


def test_the_steps_name_the_private_address_and_no_traefik(tmp_path: Path) -> None:
    repo = _private_repo(tmp_path, SAFE)
    target = PrivateCompose(repo, load_rail_config(repo), run=RecordingHost())
    steps = target.steps(_artefact(repo))
    assert "red-base" in steps[0].title
    assert steps[1].argv == ("GET", PRIVATE)
    assert steps[2].argv == ("GET", f"http://{BIND}:9100/version")
    env = target.env_file(_artefact(repo))
    assert f"BIND_ADDRESS={BIND}\n" in env
    assert "TRAEFIK" not in env and "DOMAIN=" not in env


def test_a_public_compose_is_refused_before_the_first_ssh(tmp_path: Path) -> None:
    """Refusing after the deployment would be a report, not a guard."""
    repo = _private_repo(tmp_path, PUBLIC)
    host = RecordingHost()
    # a fake clock so this can never hang on the health poll: if the guard ever stops
    # guarding, the test must fail fast rather than wait out the healthcheck timeout
    now = [0.0]
    target = PrivateCompose(
        repo,
        load_rail_config(repo),
        run=host,
        http=lambda url, timeout: (503, b"down"),
        clock=lambda: now[0],
        sleep=lambda s: now.__setitem__(0, now[0] + s),
    )
    with pytest.raises(DeployError, match="9100:9100"):
        target.apply(_artefact(repo))
    assert [a for a in host.argv if a[0] == "ssh"] == [], "nothing may reach the machine"


def test_a_missing_bind_address_is_an_error_not_a_fallback(tmp_path: Path) -> None:
    """Rebuilt from the helper rather than filtered line by line: a gate is three lines, and
    dropping two of them leaves an orphan `value:` that corrupts the manifest — the test
    would then pass because nothing loads, not because the parameter is missing."""
    repo = _private_repo(tmp_path, SAFE)
    write_manifest(
        repo,
        project="red-alerts",
        tier="prod",
        gates={"deploy.ssh_host": ("red-base", "red-alerts lives on red-base")},
        deploy=True,
    )
    manifest = (
        (repo / "rail.yaml")
        .read_text()
        .replace("  target: vps-traefik\n", "  target: private-compose\n")
    )
    manifest = "\n".join(
        f"  healthcheck: {PRIVATE}" if line.strip().startswith("healthcheck:") else line
        for line in manifest.splitlines()
    )
    (repo / "rail.yaml").write_text(manifest + "\n")

    # the manifest must still LOAD — otherwise the test proves nothing about the parameter
    cfg = load_rail_config(repo)
    assert "deploy.bind_address" not in cfg.gates

    with pytest.raises(DeployError, match="deploy.bind_address"):
        PrivateCompose(repo, cfg, run=RecordingHost())


def test_the_deployment_verifies_over_the_private_origin(tmp_path: Path) -> None:
    repo = _private_repo(tmp_path, SAFE)
    artefact = _artefact(repo)
    asked: list[str] = []

    def web(url: str, timeout: float) -> tuple[int, bytes]:
        asked.append(url)
        if url.endswith("/healthz"):
            return 200, b'{"status":"ok"}'
        return 200, json.dumps(
            {
                "project": "red-alerts",
                "version": artefact.version,
                "git_sha": artefact.sha,
                "image_digest": artefact.digest,
            }
        ).encode()

    target = PrivateCompose(repo, load_rail_config(repo), run=RecordingHost(), http=web)
    live = target.apply(artefact)
    assert live.version == "0.1.0"
    assert asked == [PRIVATE, f"http://{BIND}:9100/version"]
    assert not any(url.startswith("https://") for url in asked), "nothing goes through the internet"


# -- dispatch (task 4) --------------------------------------------------------------------


def test_the_flows_build_either_target_from_the_manifest(tmp_path: Path) -> None:
    from rail.deploy.flow import make_target
    from rail.deploy.vps_traefik import VpsTraefik

    private = _private_repo(tmp_path / "private", SAFE)
    assert isinstance(make_target(private, load_rail_config(private)), PrivateCompose)

    public = conforming_tree(tmp_path / "public", "red-probe", "prod")
    assert isinstance(make_target(public, load_rail_config(public)), VpsTraefik)


def test_an_unimplemented_target_is_still_refused_by_name(tmp_path: Path) -> None:
    repo = _private_repo(tmp_path, SAFE)
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text().replace("private-compose", "pc-server-systemd")
    )
    from rail.deploy.flow import make_target

    with pytest.raises(DeployError, match="pc-server-systemd"):
        make_target(repo, load_rail_config(repo))


# -- what "private" must mean, pinned ------------------------------------------------------


@pytest.mark.parametrize(
    "mapping",
    [
        "0.0.0.0:9204:9204",  # names an address AND publishes everywhere
        "[::]:9204:9204",  # the IPv6 spelling of the same thing
        "10.100.0.2:9204:9204",  # an address, but not this machine's
        "[fd00::4]:9204:9204",  # bracketed IPv6 that is not the bind address
        "0.0.0.0:9204-9210:9204-9210",  # a range does not hide the wildcard
        "${FOO}:9204:9204",  # a variable the rail does not write
        "${BIND_ADDRESS:-0.0.0.0}:9204:9204",  # a default turns the variable into a wildcard
    ],
)
def test_publishing_anywhere_but_the_bind_address_is_refused(mapping: str) -> None:
    """The rule is equality with `deploy.bind_address`, not the presence of an address:
    `0.0.0.0` names one and publishes everywhere, which is what the guard exists to stop."""
    assert published_ports_are_private(_compose(f'    ports:\n      - "{mapping}"\n'), BIND) == [
        ("app", mapping)
    ]


@pytest.mark.parametrize(
    "mapping",
    [
        f"{BIND}:9204:9204",
        f"{BIND}:9204-9210:9204-9210",  # a port range parses: two dashes, one host address
        "127.0.0.1:9204:9204",
        "${BIND_ADDRESS}:9204:9204",  # the rail writes this variable into the .env itself
        "$BIND_ADDRESS:9204:9204",
    ],
)
def test_the_bind_address_and_the_variable_the_rail_writes_are_accepted(mapping: str) -> None:
    """Accepting `${BIND_ADDRESS}` keeps the machine's address out of the repository and
    leaves one source of truth — and it is safe precisely because the rail, not the project,
    writes that variable's value into the generated `.env`."""
    assert published_ports_are_private(_compose(f'    ports:\n      - "{mapping}"\n'), BIND) == []


# -- the ways to publish that are not a `ports:` entry -------------------------------------


def test_host_networking_bypasses_port_mapping_entirely() -> None:
    """`network_mode: host` puts the container in the host's network namespace: it binds every
    interface directly, outside Docker's NAT, and needs no `ports:` at all. A guard that only
    reads `ports:` is fully bypassed by the normal way to use host networking."""
    text = "services:\n  app:\n    image: x\n    network_mode: host\n"
    assert published_ports_are_private(text, BIND) == [("app", "network_mode: host")]


def test_host_networking_is_refused_even_with_a_compliant_port() -> None:
    text = (
        "services:\n  app:\n    image: x\n    network_mode: host\n"
        f'    ports:\n      - "{BIND}:9204:9204"\n'
    )
    assert published_ports_are_private(text, BIND) == [("app", "network_mode: host")]


def test_other_network_modes_are_not_publishing() -> None:
    for mode in ("bridge", "none", "default"):
        text = f"services:\n  app:\n    image: x\n    network_mode: {mode}\n"
        assert published_ports_are_private(text, BIND) == [], mode


def test_the_long_form_without_published_takes_an_ephemeral_port_on_every_interface() -> None:
    """Verified under real Docker: `ports: [{target: 8080}]` yields
    `0.0.0.0:32768->8080/tcp, [::]:32768->8080/tcp`. Omitting `published` does not mean
    "nothing is published" — it means "Docker picks the port, and publishes it everywhere"."""
    text = "services:\n  app:\n    image: x\n    ports:\n      - target: 9204\n"
    assert published_ports_are_private(text, BIND) == [("app", "target 9204, no published")]


def test_a_scalar_ports_value_is_refused_rather_than_iterated_by_character() -> None:
    """`ports: "9204:9204"` is malformed compose; iterating the string would test each
    character and find no offender — failing open on bad input again."""
    text = 'services:\n  app:\n    image: x\n    ports: "9204:9204"\n'
    with pytest.raises(DeployError, match="ports"):
        published_ports_are_private(text, BIND)


def test_a_healthcheck_without_a_host_is_refused_at_construction(tmp_path: Path) -> None:
    """`Field(pattern=r"^https?://")` is match-at-start, not fully anchored, so `https://`
    passes validation — the comment claiming netloc must be present was simply false.
    `VpsTraefik` already fails fast on this through `domain_of`; both shapes must, or the
    failure surfaces only after the whole healthcheck timeout, once ssh has already changed
    the machine."""
    repo = _private_repo(tmp_path, SAFE)
    manifest = "\n".join(
        "  healthcheck: https://" if line.strip().startswith("healthcheck:") else line
        for line in (repo / "rail.yaml").read_text().splitlines()
    )
    (repo / "rail.yaml").write_text(manifest + "\n")
    with pytest.raises(DeployError, match="has no host"):
        PrivateCompose(repo, load_rail_config(repo), run=RecordingHost())
