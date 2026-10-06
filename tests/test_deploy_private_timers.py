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
    return service_refusals(text, unit="red-backup.service", current=CURRENT)


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
