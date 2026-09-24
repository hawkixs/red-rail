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
        # --- fix round 1: allow-lists instead of deny-lists (task-4-fix-1.md) ---
        ("MemoryMax=64M\n", "MemoryMax=512m\n", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=64MB\n", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=1Gi\n", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=max\n", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=0\n", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=64M # bound it\n", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=100%\n", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=infinity\nMemoryMax=64MB\n", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=\n", "MemoryMax="),
        ("TimeoutStopSec=15\n", "TimeoutStopSec=0s\n", "TimeoutStopSec="),
        ("TimeoutStopSec=15\n", "TimeoutStopSec=15\nTimeoutSec=0\n", "TimeoutSec="),
        ("User=red-monitor\n", "User=%u\n", "User="),
        ("User=red-monitor\n", "User=%U\n", "User="),
        ("User=red-monitor\n", "User=\n", "User="),
        ("Type=simple\n", "Type=simple\nPermissionsStartOnly=y\n", "PermissionsStartOnly"),
        ("Type=simple\n", "Type=simple\nPermissionsStartOnly=T\n", "PermissionsStartOnly"),
        (
            "Type=simple\n",
            "Type=simple\nExecStartPre=/bin/true ; +/bin/sh -c id\n",
            "different command",
        ),
        (
            "Type=simple\n",
            'Type=simple\nExecStartPre="+/bin/sh" -c id\n',
            "different command",
        ),
        (
            "Type=simple\n",
            "Type=simple\nExecStartPre=\\x2b/bin/sh -c id\n",
            "different command",
        ),
        (
            "/opt/red-monitor/current/red agent",
            "/opt/red-monitor/current/red ; /usr/bin/other",
            "different command",
        ),
        (
            "EnvironmentFile=/opt/red-monitor/current/release.env\n",
            "EnvironmentFile=/opt/red-monitor/current/release.env\nEnvironmentFile=\n",
            "EnvironmentFile=",
        ),
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


def test_a_semicolon_comment_behind_a_continuation_is_also_seen() -> None:
    """Fix round 1, test gap: a `;` comment is dropped inside a continuation exactly like `#`,
    then the hidden `+…` still runs with full privileges."""
    hidden = "Type=simple\nExecStartPre=\\\n; looks harmless\n  +/bin/sh -c id\n"
    refusals = _refusals(GOOD.replace("Type=simple\n", hidden, 1))
    assert any("full privileges" in refusal for refusal in refusals), refusals


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "ExecStart=/opt/red-monitor/current/red agent -config /etc/red-monitor/agent.yaml\n",
            "ExecStart=/usr/bin/other\nExecStart=\n"
            "ExecStart=/opt/red-monitor/current/red agent -config /etc/red-monitor/agent.yaml\n",
        ),
        ("ProtectSystem=strict\n", "ProtectSystem=strict\nExecPaths=+/srv\n"),
    ],
)
def test_a_reset_list_or_a_non_command_key_is_still_accepted(old: str, new: str) -> None:
    """Fix round 1: `ExecStart=` resets like any list (only what follows the last empty
    assignment counts), and `ExecPaths=` is not a command key, so a `+` prefix there is inert."""
    assert old in GOOD, "the fixture must contain what the case replaces"
    assert _refusals(GOOD.replace(old, new, 1)) == []


def test_a_backslash_before_trailing_whitespace_does_not_continue_the_line() -> None:
    """Fix round 1, finding 6: systemd continues a line only behind a backslash that is its
    very last character — trailing whitespace after it ends the line for systemd, so the next
    line is read as its own directive, not swallowed into the first."""
    unit = parse_unit("[Service]\nExecStartPre=/bin/a \\ \nExecStartPost=/bin/b\n")
    assert unit["Service"]["ExecStartPost"] == ["/bin/b"]


def test_a_double_backslash_does_not_continue_the_line() -> None:
    """Fix round 1, finding 6: an EVEN number of trailing backslashes is not a continuation for
    systemd — only an odd count is — so `\\\\` ends the line like any other."""
    unit = parse_unit("[Service]\nExecStartPre=/bin/a \\\\\nExecStartPost=/bin/b\n")
    assert unit["Service"]["ExecStartPost"] == ["/bin/b"]


def test_a_padded_section_header_is_not_merged_with_the_real_one() -> None:
    """Fix round 1, finding 6: a section name is read verbatim — `[ Service ]` is a different,
    unknown section for systemd, not `[Service]` with cosmetic padding."""
    unit = parse_unit("[Service]\nUser=red-monitor\n[ Service ]\nUser=intruder\n")
    assert unit["Service"]["User"] == ["red-monitor"]
    assert unit[" Service "]["User"] == ["intruder"]


def test_a_vertical_tab_is_not_a_systemd_line_break() -> None:
    """Fix round 1, finding 6: systemd splits a unit only on CR, LF or CRLF; `str.splitlines()`
    also breaks on `\\v`, which would wrongly cut a value in the middle."""
    unit = parse_unit("[Service]\nExecStart=/bin/a\x0b/bin/b\n")
    assert unit["Service"]["ExecStart"] == ["/bin/a\x0b/bin/b"]
