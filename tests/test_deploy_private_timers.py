"""Target `private-timers` (spec 2026-10-06-private-timers-target). Documentation addresses only."""

import pytest

from rail.deploy.private_timers import service_refusals, timer_refusals

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
