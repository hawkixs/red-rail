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
        # --- fix round 2: keys and values keep every whitespace systemd keeps (task-4-fix-2.md) ---
        (
            "Type=simple\n",
            "Type=simple\nExecStartPre=+/bin/sh\nExecStartPre=\xa0\n",
            "full privileges",
        ),
        # \x0c and \x0b are control characters (fix round 3, finding 1): the blanket
        # control-character refusal now fires before parsing ever reaches the "full privileges"
        # rule, so these two assert the round-3 message instead.
        (
            "Type=simple\n",
            "Type=simple\nExecStartPre=+/bin/sh\nExecStartPre=\x0c\n",
            "control character",
        ),
        (
            "Type=simple\n",
            "Type=simple\nExecStartPre=+/bin/sh\nExecStartPre=\x0b\n",
            "control character",
        ),
        ("User=red-monitor\n", "User\xa0=red-monitor\n", "User="),
        # \x0b is a control character (fix round 3, finding 1): refused before parsing, not by
        # the "MemoryMax=" unknown-key rule.
        ("MemoryMax=64M\n", "MemoryMax\x0b=64M\n", "control character"),
        ("MemoryMax=64M\n", "MemoryMax=64M\xa0\n", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=99999999999999999999\n", "MemoryMax="),
        # --- fix round 2, own finding: word-splitting also keeps every whitespace systemd keeps ---
        ("current/red agent", "current/red\xa0agent", "ExecStart="),
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


def test_good_with_crlf_line_ends_is_still_accepted() -> None:
    """Fix round 2: CRLF line endings are ordinary for systemd, not a way past the parser."""
    assert _refusals(GOOD.replace("\n", "\r\n")) == []


def test_parse_unit_keeps_unicode_whitespace_in_keys_and_values() -> None:
    """Fix round 2 (task-4-fix-2.md): `parse_unit` strips a key or a value with `" \\t"` only,
    the same set `_logical_lines` uses, never `str.strip()` with no argument. `User\\xa0` is then
    an unknown key, not `User` with cosmetic padding, and a lone `\\xa0` value is non-empty, not
    the empty value that resets a list."""
    unit = parse_unit("[Service]\nUser\xa0=x\nExecStartPre=+/bin/sh\nExecStartPre=\xa0\n")
    assert "User" not in unit["Service"]
    assert unit["Service"]["ExecStartPre"] == ["+/bin/sh", "\xa0"]


def test_a_nul_byte_is_refused_before_any_parsing() -> None:
    """Fix round 3, finding 1 (task-4-fix-3.md): systemd's `read_line_full` treats a NUL byte as
    an end of line, so `User=root` hidden behind one is systemd's own, separate, later-and-so-
    winning line — while this check, without the fix, reads it as part of `Type=`'s value and
    never evaluates it as a `User=` assignment at all. Refused outright, before parsing, naming
    the code point."""
    text = GOOD.replace("Type=simple\n", "Type=simple\x00User=root\n", 1)
    assert _refusals(text) == [f"{UNIT}: control character U+0000 is not allowed in a unit file"]


def test_a_del_character_is_refused() -> None:
    """Fix round 3, finding 1: DEL (`\\x7f`) has no legitimate use in a unit file."""
    text = GOOD.replace("Type=simple\n", "Type=simple\x7f\n", 1)
    assert _refusals(text) == [f"{UNIT}: control character U+007F is not allowed in a unit file"]


def test_a_c1_control_character_is_refused() -> None:
    """Fix round 3, finding 1: the C1 range (`\\x80`-`\\x9f`) is refused just like C0."""
    text = GOOD.replace("Type=simple\n", "Type=simple\x85\n", 1)
    assert _refusals(text) == [f"{UNIT}: control character U+0085 is not allowed in a unit file"]


def test_a_tab_inside_an_unchecked_value_is_still_accepted() -> None:
    """Fix round 3, finding 1: `\\t` is ordinary systemd whitespace, kept in `_ORDINARY_CONTROLS`
    on purpose (round 4). `Type=` is not itself examined by any rule, so a tab inside its value
    shows the new check does not over-refuse an ordinary one."""
    text = GOOD.replace("Type=simple\n", "Type=sim\tple\n", 1)
    assert _refusals(text) == []


def test_a_huge_memorymax_digit_run_is_refused_without_raising() -> None:
    """Fix round 3, finding 2 (task-4-fix-3.md): `int(digits)` raises past Python's int-string
    conversion limit (~4300 digits); a digit run longer than `_MEMORY_MAX_DIGITS` (19, `2**62`'s
    own digit count) already exceeds the byte bound regardless of any suffix, so it is refused
    before `int(...)` is ever called — no exception, just an ordinary refusal."""
    text = GOOD.replace("MemoryMax=64M\n", f"MemoryMax={'1' * 5000}\n", 1)
    refusals = _refusals(text)
    assert any("MemoryMax=" in refusal for refusal in refusals), refusals


@pytest.mark.parametrize(
    "text",
    [
        GOOD.replace("MemoryMax=64M\n", "MemoryMax=64M\n\ufeffUser=root\n", 1),
        GOOD.replace("MemoryMax=64M\n", "MemoryMax=64M\n\ufeffExecStartPre=+/bin/sh\n", 1),
        "\ufeff[Service]\nExecStartPre=+/bin/sh\n" + GOOD,
    ],
    ids=["a-last-user-root", "a-privileged-command", "a-service-section-ahead-of-good"],
)
def test_a_byte_order_mark_cannot_hide_an_assignment(text: str) -> None:
    """Fix round 4, finding 1: systemd strips a U+FEFF at the start of the file and the first one
    at the start of any line, then reads the rest as an ordinary line. This check read
    `\\ufeffUser` as an unknown key, so a last-and-winning `User=root`, a `+` command or a whole
    `[Service]` section went unseen. Refused before parsing, naming the code point."""
    assert _refusals(text) == [f"{UNIT}: format character U+FEFF is not allowed in a unit file"]


@pytest.mark.parametrize(
    ("old", "new", "character"),
    [
        (
            "Description=ReD Monitoring",
            "Description=ReD\u200bMonitoring",
            "format character U+200B",
        ),
        # a right-to-left override: the argument displays as `agent.yaml` and is not
        (
            "/etc/red-monitor/agent.yaml",
            "/etc/red-monitor/\u202elmay.tnega",
            "format character U+202E",
        ),
        # fix round 4, finding 3: a C0 control between `\x0e` and `\x1f`, here an ANSI escape
        ("Type=simple\n", "Type=simple\x1b[8m\n", "control character U+001B"),
    ],
    ids=["zero-width-space", "right-to-left-override", "escape"],
)
def test_an_invisible_character_inside_a_value_is_refused(
    old: str, new: str, character: str
) -> None:
    """Fix round 4: an invisible character reads differently for a person, a terminal, this check
    and systemd, so wherever it sits the unit is refused before parsing."""
    assert old in GOOD, "the fixture must contain what the case replaces"
    refusals = _refusals(GOOD.replace(old, new, 1))
    assert refusals == [f"{UNIT}: {character} is not allowed in a unit file"]


@pytest.mark.parametrize(
    "character",
    [
        "\ufeff",
        "\u200b",
        "\u200c",
        "\u200d",
        "\u2060",
        "\u00ad",
        *map(chr, range(0x202A, 0x202F)),
        *map(chr, range(0x2066, 0x206A)),
    ],
    ids=lambda character: f"U+{ord(character):04X}",
)
def test_every_format_character_is_refused(character: str) -> None:
    """Fix round 4, finding 1: the whole Unicode category Cf is refused, not a list of the ones
    known to be abused — the byte order mark, the zero-width characters, the soft hyphen, the
    bidirectional overrides and isolates are only the ones named by the ruling."""
    text = GOOD.replace("Description=ReD", f"Description=ReD{character}", 1)
    assert _refusals(text) == [
        f"{UNIT}: format character U+{ord(character):04X} is not allowed in a unit file"
    ]


def test_a_printable_non_ascii_letter_is_still_accepted() -> None:
    """Fix round 4: the rule targets invisible characters, not UTF-8 — an accented letter in a
    value no rule examines leaves the unit accepted."""
    text = GOOD.replace(
        "Description=ReD Monitoring Agent", "Description=Agent ReD, réseau privé", 1
    )
    assert _refusals(text) == []
