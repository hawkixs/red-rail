"""Target `private-timers` (spec 2026-10-06-private-timers-target). Documentation addresses only."""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.deploy import Artefact, DeployError, LiveVersion
from rail.deploy.flow import implementations, make_target
from rail.deploy.private_timers import (
    IDENTITY_FILE,
    IDENTITY_MARKER,
    PrivateTimers,
    service_refusals,
    timer_refusals,
)
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from rail.model import DeployTarget, load_rail_config
from tests.helpers import commit_all, conforming_tree, write_manifest

CURRENT = "/opt/red-backup/current"

SERVICE = """\
[Unit]
Description=ReD Backup - daily run

[Service]
Type=oneshot
User=red-backup
SupplementaryGroups=docker
EnvironmentFile=/opt/red-backup/current/release.env
ExecStart=/opt/red-backup/current/app/bin/python3 -m backup --config /etc/red-backup/backup.yaml run
ExecStopPost=/opt/red-backup/current/app/bin/python3 -m backup cleanup
LoadCredential=discord-webhook:/etc/red-backup/discord-webhook
MemoryMax=1G
TimeoutStartSec=2400
NoNewPrivileges=yes
ProtectSystem=strict
"""

TIMER = """\
[Unit]
Description=ReD Backup - daily run at 03:02

[Timer]
OnCalendar=*-*-* 03:02:00
Persistent=true

[Install]
WantedBy=timers.target
"""


def _service(text: str = SERVICE) -> list[str]:
    return service_refusals(text, unit="red-backup.service", current=CURRENT, declared=DECLARED)


def test_the_hardened_oneshot_service_is_accepted() -> None:
    assert _service() == []


@pytest.mark.parametrize(
    ("old", "new", "rule"),
    [
        ("Type=oneshot", "Type=simple", "Type=oneshot"),
        ("User=red-backup", "User=root", "User="),
        ("MemoryMax=1G\n", "", "MemoryMax="),
        ("TimeoutStartSec=2400\n", "", "TimeoutStartSec="),
        ("TimeoutStartSec=2400", "TimeoutStartSec=infinity", "TimeoutStartSec="),
        (
            "ExecStopPost=/opt/red-backup/current/app/bin/python3",
            "ExecStopPost=/usr/bin/docker",
            "/opt/red-backup/current/app/",
        ),
        (
            "ExecStart=/opt/red-backup/current/app/bin/python3",
            "ExecStart=+/opt/red-backup/current/app/bin/python3",
            "full privileges",
        ),
        (
            "EnvironmentFile=/opt/red-backup/current/release.env",
            "EnvironmentFile=-/opt/red-backup/current/release.env",
            "EnvironmentFile=",
        ),
        ("NoNewPrivileges=yes", "PermissionsStartOnly=yes", "PermissionsStartOnly="),
    ],
)
def test_each_service_rule_refuses_and_says_which(old: str, new: str, rule: str) -> None:
    refusals = _service(SERVICE.replace(old, new))
    assert any(rule in r and r.startswith("red-backup.service") for r in refusals), refusals


def test_an_execstartpre_outside_the_release_is_refused() -> None:
    text = SERVICE.replace(
        "ExecStart=", "ExecStartPre=/usr/bin/docker image inspect x\nExecStart=", 1
    )
    assert any("ExecStartPre" in r for r in _service(text))


def test_the_hardened_timer_is_accepted() -> None:
    assert (
        timer_refusals(TIMER, unit="red-backup.timer", declared=frozenset({"red-backup.service"}))
        == []
    )


def test_a_timer_whose_service_is_not_declared_is_refused() -> None:
    refusals = timer_refusals(TIMER, unit="red-backup.timer", declared=frozenset())
    assert refusals and "red-backup.service" in refusals[0]


@pytest.mark.parametrize(
    ("old", "new", "rule"),
    [
        ("OnCalendar=*-*-* 03:02:00\n", "", "trigger"),
        ("Persistent=true", "Persistent=true\nUnit=other.service", "Unit="),
        ("[Timer]", "[Service]", "[Timer]"),
    ],
)
def test_each_timer_rule_refuses_and_says_which(old: str, new: str, rule: str) -> None:
    refusals = timer_refusals(
        TIMER.replace(old, new), unit="red-backup.timer", declared=frozenset({"red-backup.service"})
    )
    assert any(rule in r for r in refusals), refusals


def test_an_invisible_character_is_refused_alone() -> None:
    assert len(_service(SERVICE.replace("User=red-backup", "User=red​-backup"))) == 1


@pytest.mark.parametrize(
    "program",
    [
        "/opt/red-backup/current/app/../../../../bin/sh",
        "/opt/red-backup/current/app/./../release.env",
        "/opt/red-backup/current/app//bin/python3",
        "/opt/red-backup/current/app/",
    ],
)
def test_a_program_that_leaves_the_payload_by_its_path_is_refused(program: str) -> None:
    """A prefix test alone admits `app/../..`: systemd runs what the path resolves to (commit
    security review). The program must be a normalised path strictly inside the payload."""
    text = SERVICE.replace(
        "ExecStart=/opt/red-backup/current/app/bin/python3", f"ExecStart={program}", 1
    )
    assert any("ExecStart" in r for r in _service(text)), _service(text)


DECLARED = frozenset({"red-backup.service", "red-backup.timer", "red-backup-alert.service"})


def _declared(text: str) -> list[str]:
    return service_refusals(text, unit="red-backup.service", current=CURRENT, declared=DECLARED)


def test_a_service_kept_active_after_its_run_is_refused() -> None:
    """`RemainAfterExit=yes` keeps a oneshot `active`: the timer never fires it again, and the
    running-unit guard would then refuse every deployment (review of tasks 1-2)."""
    refusals = _declared(SERVICE.replace("Type=oneshot", "Type=oneshot\nRemainAfterExit=yes"))
    assert any("RemainAfterExit=" in r for r in refusals), refusals
    assert _declared(SERVICE.replace("Type=oneshot", "Type=oneshot\nRemainAfterExit=no")) == []


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("FailureAction=reboot", "FailureAction="),
        ("SuccessAction=poweroff-force", "SuccessAction="),
        ("StartLimitAction=reboot", "StartLimitAction="),
        ("OnFailure=other.service", "OnFailure="),
        ("OnSuccess=other.service", "OnSuccess="),
    ],
)
def test_a_service_unit_section_reaching_beyond_the_release_is_refused(
    line: str, rule: str
) -> None:
    """systemd runs `*Action=` as PID 1, and `OnFailure=` may start a unit the rail never
    delivered (review of tasks 1-2)."""
    text = SERVICE.replace(
        "Description=ReD Backup - daily run", f"Description=ReD Backup - daily run\n{line}"
    )
    assert any(rule in r for r in _declared(text)), _declared(text)


def test_a_declared_on_failure_unit_and_ordering_are_accepted() -> None:
    text = SERVICE.replace(
        "Description=ReD Backup - daily run",
        "Description=ReD Backup - daily run\nOnFailure=red-backup-alert.service\n"
        "Requires=docker.service\nAfter=docker.service\nFailureAction=none",
    )
    assert _declared(text) == []


def test_ambient_capabilities_are_refused() -> None:
    text = SERVICE.replace(
        "NoNewPrivileges=yes", "NoNewPrivileges=yes\nAmbientCapabilities=CAP_SYS_ADMIN"
    )
    assert any("AmbientCapabilities=" in r for r in _declared(text))


@pytest.mark.parametrize(
    "line",
    [
        "Wants=reboot.target",
        "Requires=x.service",
        "Conflicts=y.service",
        "OnFailure=z.service",
        "BindsTo=a.service",
    ],
)
def test_a_timer_pulls_no_other_unit(line: str) -> None:
    """The rail restarts each timer through sudo: a dependency would start or stop any unit on
    the host at deployment (review of tasks 1-2)."""
    text = TIMER.replace("Description=ReD Backup - daily run at 03:02", f"Description=x\n{line}")
    refusals = timer_refusals(text, unit="red-backup.timer", declared=DECLARED)
    assert any(line.split("=")[0] + "=" in r for r in refusals), refusals


@pytest.mark.parametrize(
    "line",
    [
        "BindTo=reboot.target",  # a legacy alias systemd still reads as BindsTo=
        "Wants=reboot.target",
        "Requires=other.service",
        "PropagatesStopTo=other.service",
        "JoinsNamespaceOf=other.service",
        "RequiresMountsFor=/",
    ],
)
def test_a_service_unit_section_is_an_allow_list(line: str) -> None:
    """An unknown or aliased [Unit] key is refused, never assumed harmless (commit security
    review): only ordering, documentation, the host units a run depends on and the release's
    own units are accepted."""
    text = SERVICE.replace(
        "Description=ReD Backup - daily run", f"Description=ReD Backup - daily run\n{line}"
    )
    assert any(line.split("=")[0] + "=" in r for r in _declared(text)), _declared(text)


@pytest.mark.parametrize(
    "line", ["BindTo=reboot.target", "Before=reboot.target", "Description=x\nUpholds=y.service"]
)
def test_a_timer_unit_section_is_an_allow_list(line: str) -> None:
    text = TIMER.replace("Description=ReD Backup - daily run at 03:02", f"Description=x\n{line}")
    if line.startswith("Before="):
        assert timer_refusals(text, unit="red-backup.timer", declared=DECLARED) == []
    else:
        assert timer_refusals(text, unit="red-backup.timer", declared=DECLARED)


@pytest.mark.parametrize(
    ("old", "new", "rule"),
    [
        ("User=red-backup", "User=red-backup\nGroup=root", "Group="),
        ("User=red-backup", "User=red-backup\nGroup=0", "Group="),
        ("SupplementaryGroups=docker", "SupplementaryGroups=docker root", "SupplementaryGroups="),
    ],
)
def test_a_root_group_is_refused(old: str, new: str, rule: str) -> None:
    assert any(rule in r for r in _declared(SERVICE.replace(old, new)))


@pytest.mark.parametrize("groups", ["docker 00", "docker 6", "docker %g", "+0", "docker root"])
def test_supplementary_groups_are_plain_names_never_root(groups: str) -> None:
    """A numeric id such as `00` is GID 0 for systemd: only plain names other than root pass
    (commit security review)."""
    text = SERVICE.replace("SupplementaryGroups=docker", f"SupplementaryGroups={groups}")
    assert any("SupplementaryGroups=" in r for r in _declared(text)), _declared(text)


# -- the target ---------------------------------------------------------------------------

BIND = "192.0.2.10"
DIGEST = "sha256:" + "c" * 64
IMAGE = f"ghcr.io/hawkixs/red-backup@{DIGEST}"
UNITS = ("red-backup.service", "red-backup.timer", "red-backup-alert.service")


def _timers_repo(tmp_path: Path, service_text: str = SERVICE) -> Path:
    repo = conforming_tree(tmp_path, "red-backup", "prod")
    write_manifest(
        repo,
        project="red-backup",
        tier="prod",
        gates={"deploy.ssh_host": ("private-1-deploy", "the host's ssh alias for the site")},
    )
    with (repo / "rail.yaml").open("a") as manifest:
        manifest.write(
            "deploy:\n  target: private-timers\n  site: private-1\n"
            "  payload: /opt/red-backup\n  units:\n"
            + "".join(f"    - deploy/systemd/{unit}\n" for unit in UNITS)
        )
    directory = repo / "deploy" / "systemd"
    directory.mkdir(parents=True)
    for unit, text in zip(
        UNITS,
        (service_text, TIMER, SERVICE.replace("daily run", "alert")),
        strict=True,
    ):
        (directory / unit).write_text(text)
    commit_all(repo, "feat: the scheduled units")
    return repo


def _host(tmp_path: Path) -> Path:
    path = tmp_path / "sites.yaml"
    path.write_text(f'sites:\n  private-1:\n    address: "{BIND}"\n')
    path.chmod(0o600)
    return path


def _artefact(repo: Path) -> Artefact:
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return Artefact(version="0.1.0", sha=head, digest=DIGEST, image=IMAGE)


def _identity(artefact: Artefact) -> dict[str, str]:
    return {
        "project": "red-backup",
        "version": artefact.version,
        "git_sha": artefact.sha,
        "image_digest": artefact.digest,
    }


def _answer(identity: dict[str, str] | str, digest: str | None = DIGEST) -> str:
    """What the identity script prints: the payload's identity file, the marker, release.env."""
    body = (
        identity
        if isinstance(identity, str)
        else json.dumps({k: v for k, v in identity.items() if k != "image_digest"})
    )
    env = "VERSION=0.1.0\n" + (f"IMAGE_DIGEST={digest}\n" if digest is not None else "")
    return f"{body}\n{IDENTITY_MARKER}\n{env}"


class RecordingHost:
    def __init__(self, identity: str = "", *, code: int = 0, error: Exception | None = None):
        self.argv: list[list[str]] = []
        self.scripts: list[str] = []
        self.identity, self.code, self.error = identity, code, error

    def __call__(self, args, **kwargs):
        self.argv.append(list(args))
        if args[0] == "ssh":
            script = kwargs["input"]
            self.scripts.append(script)
            if IDENTITY_FILE in script:
                if self.error is not None:
                    raise self.error
                return subprocess.CompletedProcess(
                    args, self.code, stdout=self.identity, stderr=f"cannot connect to {BIND}"
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.run(args, **kwargs)


def _target(repo: Path, tmp_path: Path, *, run=None) -> PrivateTimers:
    def no_http(url: str, timeout: float):
        pytest.fail("private-timers must verify without HTTP")

    return PrivateTimers(
        repo,
        load_rail_config(repo),
        sites=_host(tmp_path),
        run=run or RecordingHost(),
        http=no_http,
    )


def _script(tmp_path: Path) -> str:
    repo = _timers_repo(tmp_path / "repo")
    return _target(repo, tmp_path).steps(_artefact(repo))[0].stdin or ""


def test_the_remote_script_runs_the_spec_sequence_in_order(tmp_path: Path) -> None:
    script = _script(tmp_path)
    sequence = [
        "flock -n 9",
        "--property=ActiveState",
        *(f"cat > {unit}" for unit in UNITS),
        "cat > release.env",
        f"docker pull --quiet {IMAGE}",
        f"container=$(docker create {IMAGE} /bin/true)",
        "docker cp",
        'ln -sfn "$release" "$root/current"',
        "sudo -n /usr/bin/systemctl daemon-reload",
        "--property=FragmentPath",
        "restart red-backup.timer",
        "systemctl is-active red-backup.timer",
        "--property=NextElapseUSecRealtime",
    ]
    indexes = [script.index(part) for part in sequence]
    assert indexes == sorted(set(indexes))
    for unit in UNITS:
        assert script.index(f"--property=FragmentPath --value {unit}") < script.index(
            "restart red-backup.timer"
        )
    assert '[ -n "$next" ] && [ "$next" != "n/a" ]' in script


def test_a_running_unit_stops_the_script_before_any_change(tmp_path: Path) -> None:
    script = _script(tmp_path)
    first_write = script.index("cat >")
    # a timer is always `active` (waiting): only the services it fires can be mid-run
    assert "--value red-backup.timer)" not in script.split("cat >", 1)[0]
    for unit in [u for u in UNITS if u.endswith(".service")]:
        check = f"state=$(systemctl show --property=ActiveState --value {unit})"
        start = script.index(check)
        assert start < first_write
        block = script[start:first_write].split("\nfi\n", 1)[0]
        assert '[ "$state" = "active" ]' in block
        assert '[ "$state" = "activating" ]' in block
        assert f"{unit} is $state: a run is in progress" in block and "exit 1" in block


def test_the_only_privileged_commands_are_daemon_reload_and_one_restart_per_timer(
    tmp_path: Path,
) -> None:
    script = _script(tmp_path)
    assert [line for line in script.splitlines() if "sudo" in line] == [
        "sudo -n /usr/bin/systemctl daemon-reload",
        "sudo -n /usr/bin/systemctl restart red-backup.timer",
    ]
    for unit in ("red-backup.service", "red-backup-alert.service"):
        assert f"restart {unit}" not in script


def test_a_redeploy_replaces_the_payload_by_rename(tmp_path: Path) -> None:
    script = _script(tmp_path)
    assert 'docker cp "$container:/opt/red-backup/." "$release/.app.new"' in script
    assert 'docker cp "$container:/opt/red-backup/." "$release/app"' not in script
    assert 'mv "$release/app" "$release/.app.old"' in script
    assert 'mv "$release/.app.new" "$release/app"' in script
    assert script.count('rm -rf "$release/.app.old"') == 2


def test_the_container_is_removed_on_every_exit_path(tmp_path: Path) -> None:
    script = _script(tmp_path)
    trap = "trap 'docker rm --force \"$container\" >/dev/null 2>&1 || true' EXIT"
    assert script.index(trap) < script.index("docker cp")


def test_a_refused_unit_never_reaches_the_machine(tmp_path: Path) -> None:
    repo = _timers_repo(tmp_path / "repo", SERVICE.replace("User=red-backup", "User=root"))
    host = RecordingHost()
    with pytest.raises(DeployError, match="red-backup.service.*User="):
        _target(repo, tmp_path, run=host).steps(_artefact(repo))
    assert not any(args[0] == "ssh" for args in host.argv)


def test_the_deployment_is_verified_by_reading_the_release_identity(tmp_path: Path) -> None:
    """Nothing of the release runs outside its units (operator decision 2026-10-06): the rail
    reads the payload's identity file and the release.env it wrote, and compares."""
    repo = _timers_repo(tmp_path / "repo")
    artefact = _artefact(repo)
    host = RecordingHost(_answer(_identity(artefact)))
    target = _target(repo, tmp_path, run=host)
    assert target.healthcheck is None and target.domain == "private-1"
    assert target.services == ("red-backup.service", "red-backup-alert.service")
    assert target.timers == ("red-backup.timer",)
    assert target.apply(artefact) == LiveVersion(**_identity(artefact))
    assert len(host.scripts) == 2
    reading = host.scripts[1]
    assert f"/opt/red-backup/current/app/{IDENTITY_FILE}" in reading
    assert "exec" not in reading and "python" not in reading and ". " not in reading


@pytest.mark.parametrize(
    ("case", "problem"),
    [
        ("not json", "JSON"),
        ("[]", "object"),
        *((f"missing {field}", field) for field in ("project", "version", "git_sha")),
        ("bad project", "project"),
        ("bad version", "version"),
        ("bad git_sha", "git_sha"),
        ("bad image_digest", "image_digest"),
        ("no image_digest", "IMAGE_DIGEST"),
        ("no marker", "release.env"),
    ],
)
def test_an_unreadable_identity_fails_the_verification(
    tmp_path: Path, case: str, problem: str
) -> None:
    repo = _timers_repo(tmp_path / "repo")
    artefact = _artefact(repo)
    data = _identity(artefact)
    digest: str | None = DIGEST
    if case.startswith("missing "):
        del data[problem]
    elif case == "bad image_digest":
        digest = "sha256:" + "d" * 64
    elif case.startswith("bad "):
        data[problem] = BIND
    elif case == "no image_digest":
        digest = None
    if case in ("not json", "[]"):
        answer = _answer(case)
    elif case == "no marker":
        answer = json.dumps(data)
    else:
        answer = _answer(data, digest)
    with pytest.raises(DeployError, match=problem) as caught:
        _target(repo, tmp_path, run=RecordingHost(answer)).apply(artefact)
    assert BIND not in str(caught.value)


@pytest.mark.parametrize("failure", ["exit", "timeout", "unavailable"])
def test_a_failed_identity_read_is_a_redacted_deploy_error(tmp_path: Path, failure: str) -> None:
    repo = _timers_repo(tmp_path / "repo")
    error = {
        "exit": None,
        "timeout": subprocess.TimeoutExpired("ssh", 900),
        "unavailable": OSError(f"cannot connect to {BIND}"),
    }[failure]
    host = RecordingHost(code=1 if failure == "exit" else 0, error=error)
    problem = {"exit": "exit 1", "timeout": "timed out", "unavailable": "ssh"}[failure]
    with pytest.raises(DeployError, match=problem) as caught:
        _target(repo, tmp_path, run=host).apply(_artefact(repo))
    assert BIND not in str(caught.value)


def test_the_flows_build_the_timers_target_from_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _timers_repo(tmp_path / "repo")
    monkeypatch.setenv("RAIL_SITES_FILE", str(_host(tmp_path)))
    assert isinstance(make_target(repo, load_rail_config(repo)), PrivateTimers)
    assert set(implementations()) == set(DeployTarget)


def test_plan_names_the_site_and_the_identity_read(tmp_path: Path) -> None:
    repo = _timers_repo(tmp_path / "repo")
    artefact = _artefact(repo)
    FileLedger(repo / RECEIPTS_DIR).attest(
        "red-backup",
        AttestationKind.RELEASED,
        {
            "version": artefact.version,
            "sha": artefact.sha,
            "digest": artefact.digest,
            "image": artefact.image,
            "tag": "v0.1.0",
        },
        issuer="op",
        idempotency_key="released:0.1.0",
    )
    out = CliRunner().invoke(
        main,
        ["deploy", "--repo", str(repo), "--plan"],
        env={"RAIL_SITES_FILE": str(_host(tmp_path))},
    )
    assert out.exit_code == 0, out.output
    assert "on private-1" in out.output and "private-1-deploy" in out.output
    assert IDENTITY_FILE in out.output
    assert "restart red-backup.timer" in out.output
    assert "GET " not in out.output and BIND not in out.output


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("LoadCredential=shadow:/etc/shadow", "LoadCredential="),
        ("LoadCredential=key:/etc/red-backup/../shadow", "LoadCredential="),
        ("LoadCredentialEncrypted=key:/root/secret", "LoadCredentialEncrypted="),
        ("ImportCredential=*", "ImportCredential="),
        ("StandardOutput=file:/etc/sudoers.d/x", "StandardOutput="),
        ("StandardError=append:/etc/passwd", "StandardError="),
        ("StandardInput=file:/etc/shadow", "StandardInput="),
        ("OpenFile=/etc/shadow", "OpenFile="),
        ("DeviceAllow=/dev/sda rw", "DeviceAllow="),
        ("DynamicUser=yes", "DynamicUser="),
        ("PAMName=login", "PAMName="),
    ],
)
def test_a_service_key_systemd_performs_as_root_is_refused(line: str, rule: str) -> None:
    """systemd opens credentials, standard streams and `OpenFile=` as root on the service's
    behalf (commit security review): `[Service]` is an allow-list, and those keys may only
    reach the project's own host configuration or the journal."""
    text = SERVICE.replace("NoNewPrivileges=yes", f"NoNewPrivileges=yes\n{line}")
    assert any(rule in r for r in _declared(text)), _declared(text)


def test_the_project_credentials_and_the_journal_are_accepted() -> None:
    text = SERVICE.replace(
        "NoNewPrivileges=yes",
        "NoNewPrivileges=yes\nLoadCredential=webhook:/etc/red-backup/discord-webhook\n"
        "SetCredential=webhook:\nStandardOutput=journal\nStandardError=journal\n"
        "SyslogIdentifier=red-backup\nUMask=0077\nPrivateTmp=yes\nPrivateDevices=yes\n"
        "ProtectHome=tmpfs\nReadWritePaths=/data/backups\n"
        "WorkingDirectory=/opt/red-backup/current/app\nEnvironment=PATH=/usr/bin:/bin",
    )
    assert _declared(text) == []


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("StateDirectory=docker", "StateDirectory="),
        ("LogsDirectory=journal", "LogsDirectory="),
        ("RuntimeDirectory=red-backup/../docker", "RuntimeDirectory="),
        ("CacheDirectory=other", "CacheDirectory="),
        ("EnvironmentFile=/etc/other/secrets", "EnvironmentFile="),
        ("EnvironmentFile=-/etc/red-backup/extra", "EnvironmentFile="),
        ("KillMode=process", "KillMode="),
        ("KillMode=none", "KillMode="),
        ("BindPaths=/etc", "BindPaths="),
        ("BindReadOnlyPaths=/root", "BindReadOnlyPaths="),
        ("TemporaryFileSystem=/etc", "TemporaryFileSystem="),
        ("Group=disk", "Group="),
        ("SupplementaryGroups=shadow", "SupplementaryGroups="),
        ("SupplementaryGroups=docker sudo", "SupplementaryGroups="),
    ],
)
def test_final_review_findings_are_refused(line: str, rule: str) -> None:
    """Final review of the branch: systemd acts as root on these values (directories it owns
    or chowns, environment files it reads, mounts it builds), a kill mode lets a run outlive
    its bound, and privileged groups are not a decision the spec took."""
    text = SERVICE.replace("NoNewPrivileges=yes", f"NoNewPrivileges=yes\n{line}")
    assert any(rule in r for r in _declared(text)), _declared(text)


@pytest.mark.parametrize(
    "line",
    [
        "StateDirectory=red-backup",
        "LogsDirectory=red-backup/runs",
        "RuntimeDirectory=red-backup-pitr",
        "EnvironmentFile=/etc/red-backup/extra.env",
        "KillMode=mixed",
        "Group=red-backup",
    ],
)
def test_project_owned_values_are_accepted(line: str) -> None:
    text = SERVICE.replace("NoNewPrivileges=yes", f"NoNewPrivileges=yes\n{line}")
    assert _declared(text) == []


def test_no_new_privileges_is_required() -> None:
    refusals = _declared(SERVICE.replace("NoNewPrivileges=yes\n", ""))
    assert any("NoNewPrivileges=" in r for r in refusals), refusals


def test_install_sections_are_bounded() -> None:
    service = SERVICE + "\n[Install]\nWantedBy=multi-user.target\n"
    assert any("[Install]" in r for r in _declared(service))
    timer_ok = timer_refusals(TIMER, unit="red-backup.timer", declared=DECLARED)
    assert timer_ok == []
    timer_bad = TIMER.replace("WantedBy=timers.target", "WantedBy=timers.target\nAlso=x.service")
    assert any(
        "[Install]" in r
        for r in timer_refusals(timer_bad, unit="red-backup.timer", declared=DECLARED)
    )
    timer_alias = TIMER.replace("WantedBy=timers.target", "WantedBy=multi-user.target")
    assert any(
        "[Install]" in r
        for r in timer_refusals(timer_alias, unit="red-backup.timer", declared=DECLARED)
    )


def test_the_services_are_checked_again_just_before_current_moves(tmp_path: Path) -> None:
    """A timer may fire during the pull: the running check is repeated right before the
    switch, so the window is milliseconds, not the length of a pull."""
    script = _script(tmp_path)
    switch = script.index('ln -sfn "$release" "$root/current"')
    check = "state=$(systemctl show --property=ActiveState --value red-backup.service)"
    assert script.count(check) == 2
    assert script.index("docker cp") < script.rindex(check) < switch


@pytest.mark.parametrize(
    "value",
    ["red-backup:docker", "red-backup:../etc", "red-backup:red-backup-link:x", "red-backup:"],
)
def test_a_directory_symlink_destination_is_checked_too(value: str) -> None:
    """`StateDirectory=source:destination` makes systemd create, as root, a symlink at the
    destination: both sides must name the project (commit security review)."""
    text = SERVICE.replace("NoNewPrivileges=yes", f"NoNewPrivileges=yes\nStateDirectory={value}")
    assert any("StateDirectory=" in r for r in _declared(text)), _declared(text)


def test_a_project_symlink_destination_is_accepted() -> None:
    text = SERVICE.replace(
        "NoNewPrivileges=yes", "NoNewPrivileges=yes\nStateDirectory=red-backup:red-backup-current"
    )
    assert _declared(text) == []


def test_the_identity_file_is_read_only_when_it_is_a_regular_file(tmp_path: Path) -> None:
    """The payload comes from the image: a symlinked identity file would make the deploy
    account read whatever it points at (commit security review)."""
    repo = _timers_repo(tmp_path / "repo")
    script = _target(repo, tmp_path).identity_script()
    identity = f"/opt/red-backup/current/app/{IDENTITY_FILE}"
    guard = f"[ -f {identity} ] && [ ! -L {identity} ]"
    assert guard in script and script.index(guard) < script.index(f"head -c 65536 {identity}")


def test_an_identity_file_cannot_supply_its_own_release_env(tmp_path: Path) -> None:
    repo = _timers_repo(tmp_path / "repo")
    artefact = _artefact(repo)
    forged = (
        json.dumps({k: v for k, v in _identity(artefact).items() if k != "image_digest"})
        + f"\n{IDENTITY_MARKER}\nIMAGE_DIGEST={artefact.digest}"
    )
    answer = _answer(forged, digest="sha256:" + "d" * 64)
    with pytest.raises(DeployError):
        _target(repo, tmp_path, run=RecordingHost(answer)).apply(artefact)
