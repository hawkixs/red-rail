"""Target `private-systemd`: a binary that systemd runs (spec
2026-09-24-private-systemd-target). Addresses are RFC 5737 documentation addresses only."""

import pytest

from rail.deploy.private_systemd import parse_unit, unit_refusals

CURRENT = "/opt/red-monitor/current"
UNIT = "red-agent.service"

GOOD = """\
[Unit]
Description=ReD Monitoring Agent
After=network-online.target docker.service

[Service]
Type=simple
User=red-monitor
Group=red-monitor
SupplementaryGroups=docker
EnvironmentFile=/opt/red-monitor/current/release.env
ExecStart=/opt/red-monitor/current/red agent -config /etc/red-monitor/agent.yaml
Restart=on-failure
TimeoutStopSec=15
MemoryMax=64M
NoNewPrivileges=true
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
"""


def _refusals(text: str) -> list[str]:
    return unit_refusals(text, unit=UNIT, current=CURRENT, binary_name="red")


def test_the_hardened_unit_is_accepted() -> None:
    assert _refusals(GOOD) == []


def test_the_parser_reads_sections_keys_and_continuations() -> None:
    unit = parse_unit(
        "[Service]\n# a comment\nExecStart=/bin/a \\\n  --flag\n; another\nUser = svc\n"
    )
    assert unit["Service"]["ExecStart"] == ["/bin/a --flag"]
    assert unit["Service"]["User"] == ["svc"]


@pytest.mark.parametrize(
    ("old", "new", "rule"),
    [
        ("User=red-monitor\n", "", "User="),
        ("User=red-monitor\n", "User=root\n", "User="),
        ("User=red-monitor\n", "User=0\n", "User="),
        ("MemoryMax=64M\n", "", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=infinity\n", "MemoryMax="),
        ("TimeoutStopSec=15\n", "", "TimeoutStopSec="),
        ("TimeoutStopSec=15\n", "TimeoutStopSec=infinity\n", "TimeoutStopSec="),
        ("TimeoutStopSec=15\n", "TimeoutStopSec=0\n", "TimeoutStopSec="),
        ("/opt/red-monitor/current/red agent", "/opt/red-monitor/red agent", "ExecStart="),
        ("ExecStart=/opt", "ExecStart=/opt/red-monitor/current/red\nExecStart=/opt", "ExecStart="),
        ("EnvironmentFile=/opt", "EnvironmentFile=-/opt", "EnvironmentFile="),
        ("EnvironmentFile=/opt/red-monitor/current/release.env\n", "", "EnvironmentFile="),
        ("Type=simple\n", "Type=simple\nExecStartPre=+/bin/chmod 777 /etc\n", "full privileges"),
        ("Type=simple\n", "Type=simple\nExecStartPost=!!/bin/true\n", "full privileges"),
        ("ExecStart=/opt", "ExecStart=+/opt", "full privileges"),
        ("Type=simple\n", "Type=simple\nPermissionsStartOnly=yes\n", "PermissionsStartOnly"),
    ],
)
def test_each_rule_refuses_the_unit_and_says_which(old: str, new: str, rule: str) -> None:
    assert old in GOOD, "the fixture must contain what the case replaces"
    refusals = _refusals(GOOD.replace(old, new, 1))
    assert any(rule in refusal for refusal in refusals), refusals
    assert all(refusal.startswith(UNIT) for refusal in refusals), refusals


def test_a_unit_without_a_service_section_is_refused() -> None:
    assert _refusals("[Unit]\nDescription=x\n") == [f"{UNIT} has no [Service] section"]


def test_the_last_assignment_wins_as_it_does_for_systemd() -> None:
    """Review focus 1: `User=` twice, the root one last and spaced — systemd runs as root."""
    text = GOOD.replace("User=red-monitor\n", "User=red-monitor\nUser = root\n")
    assert any("User=" in refusal for refusal in _refusals(text))


def test_a_privilege_prefix_behind_a_continuation_and_a_comment_is_seen() -> None:
    """Review focus 2: systemd drops a comment inside a continuation, then runs `+…` as root."""
    hidden = "Type=simple\nExecStartPre=\\\n# looks harmless\n  +/bin/sh -c id\n"
    refusals = _refusals(GOOD.replace("Type=simple\n", hidden, 1))
    assert any("full privileges" in refusal for refusal in refusals), refusals
