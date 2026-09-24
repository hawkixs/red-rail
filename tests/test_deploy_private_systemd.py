"""Target `private-systemd`: a binary that systemd runs (spec
2026-09-24-private-systemd-target). Addresses are RFC 5737 documentation addresses only."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.deploy import Artefact, DeployError
from rail.deploy.flow import implementations, make_target
from rail.deploy.private_systemd import PrivateSystemd, parse_unit, unit_refusals
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from rail.model import DeployTarget, load_rail_config
from tests.helpers import commit_all, conforming_tree, write_manifest

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
        # --- allow-lists: a value this check cannot read with certainty is refused ---
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
        # --- keys and values are stripped of space and tab only, as systemd strips them ---
        (
            "Type=simple\n",
            "Type=simple\nExecStartPre=+/bin/sh\nExecStartPre=\xa0\n",
            "full privileges",
        ),
        # \x0c and \x0b are control characters, refused before parsing: these two cases never
        # reach the "full privileges" rule
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
        # \x0b is a control character: refused before parsing, not by the "MemoryMax=" rule
        ("MemoryMax=64M\n", "MemoryMax\x0b=64M\n", "control character"),
        ("MemoryMax=64M\n", "MemoryMax=64M\xa0\n", "MemoryMax="),
        ("MemoryMax=64M\n", "MemoryMax=99999999999999999999\n", "MemoryMax="),
        # --- command words are split on space and tab only, as systemd splits them ---
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
    """A `;` comment is dropped inside a continuation exactly like `#`, then the hidden
    `+…` still runs with full privileges."""
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
    """`ExecStart=` resets like any list (only what follows the last empty assignment counts),
    and `ExecPaths=` is not a command key, so a `+` prefix there is inert."""
    assert old in GOOD, "the fixture must contain what the case replaces"
    assert _refusals(GOOD.replace(old, new, 1)) == []


def test_a_backslash_before_trailing_whitespace_does_not_continue_the_line() -> None:
    """systemd continues a line only behind a backslash that is its very last character:
    trailing whitespace after it ends the line for systemd, so the next line is read as its own
    directive, not swallowed into the first."""
    unit = parse_unit("[Service]\nExecStartPre=/bin/a \\ \nExecStartPost=/bin/b\n")
    assert unit["Service"]["ExecStartPost"] == ["/bin/b"]


def test_a_double_backslash_does_not_continue_the_line() -> None:
    """An EVEN number of trailing backslashes is not a continuation for systemd, only an odd
    count is, so `\\\\` ends the line like any other."""
    unit = parse_unit("[Service]\nExecStartPre=/bin/a \\\\\nExecStartPost=/bin/b\n")
    assert unit["Service"]["ExecStartPost"] == ["/bin/b"]


def test_a_padded_section_header_is_not_merged_with_the_real_one() -> None:
    """A section name is read verbatim: `[ Service ]` is a different, unknown section for
    systemd, not `[Service]` with cosmetic padding."""
    unit = parse_unit("[Service]\nUser=red-monitor\n[ Service ]\nUser=intruder\n")
    assert unit["Service"]["User"] == ["red-monitor"]
    assert unit[" Service "]["User"] == ["intruder"]


def test_a_vertical_tab_is_not_a_systemd_line_break() -> None:
    """systemd splits a unit only on CR, LF or CRLF; `str.splitlines()` also breaks on `\\v`,
    which would wrongly cut a value in the middle."""
    unit = parse_unit("[Service]\nExecStart=/bin/a\x0b/bin/b\n")
    assert unit["Service"]["ExecStart"] == ["/bin/a\x0b/bin/b"]


def test_good_with_crlf_line_ends_is_still_accepted() -> None:
    """CRLF line endings are ordinary for systemd, not a way past the parser."""
    assert _refusals(GOOD.replace("\n", "\r\n")) == []


def test_parse_unit_keeps_unicode_whitespace_in_keys_and_values() -> None:
    """`parse_unit` strips a key or a value with `" \\t"` only, the same set `_logical_lines`
    uses, never `str.strip()` with no argument. `User\\xa0` is then an unknown key, not `User`
    with cosmetic padding, and a lone `\\xa0` value is non-empty, not the empty value that
    resets a list."""
    unit = parse_unit("[Service]\nUser\xa0=x\nExecStartPre=+/bin/sh\nExecStartPre=\xa0\n")
    assert "User" not in unit["Service"]
    assert unit["Service"]["ExecStartPre"] == ["+/bin/sh", "\xa0"]


def test_a_nul_byte_is_refused_before_any_parsing() -> None:
    """systemd's `read_line_full` treats a NUL byte as an end of line, so `User=root` hidden
    behind one is systemd's own, separate, later-and-so-winning line, while a reader that breaks
    lines on CR and LF only takes it as part of `Type=`'s value and never sees a `User=`
    assignment at all. Refused outright, before parsing, naming the code point."""
    text = GOOD.replace("Type=simple\n", "Type=simple\x00User=root\n", 1)
    assert _refusals(text) == [f"{UNIT}: control character U+0000 is not allowed in a unit file"]


def test_a_del_character_is_refused() -> None:
    """DEL (`\\x7f`) has no legitimate use in a unit file."""
    text = GOOD.replace("Type=simple\n", "Type=simple\x7f\n", 1)
    assert _refusals(text) == [f"{UNIT}: control character U+007F is not allowed in a unit file"]


def test_a_c1_control_character_is_refused() -> None:
    """The C1 range (`\\x80`-`\\x9f`) is refused just like C0."""
    text = GOOD.replace("Type=simple\n", "Type=simple\x85\n", 1)
    assert _refusals(text) == [f"{UNIT}: control character U+0085 is not allowed in a unit file"]


def test_a_tab_inside_an_unchecked_value_is_still_accepted() -> None:
    """`\\t` is ordinary systemd whitespace, kept in `_ORDINARY_CONTROLS` on purpose. `Type=`
    is not itself examined by any rule, so a tab inside its value shows that the refusal of
    control characters does not over-refuse an ordinary one."""
    text = GOOD.replace("Type=simple\n", "Type=sim\tple\n", 1)
    assert _refusals(text) == []


def test_a_huge_memorymax_digit_run_is_refused_without_raising() -> None:
    """`int(digits)` raises past Python's int-string conversion limit (~4300 digits). A digit
    run longer than `_MEMORY_MAX_DIGITS` (19, `2**62`'s own digit count) already exceeds the
    byte bound whatever the suffix, so it is refused before `int(...)` is ever called: no
    exception, just an ordinary refusal."""
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
    """systemd strips a U+FEFF at the start of the file and the first one at the start of any
    line, then reads the rest as an ordinary line. A reader that did not would take
    `\\ufeffUser` for an unknown key and miss a last-and-winning `User=root`, a `+` command or
    a whole `[Service]` section. Refused before parsing, naming the code point."""
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
        # a C0 control between `\x0e` and `\x1f`, here an ANSI escape
        ("Type=simple\n", "Type=simple\x1b[8m\n", "control character U+001B"),
    ],
    ids=["zero-width-space", "right-to-left-override", "escape"],
)
def test_an_invisible_character_inside_a_value_is_refused(
    old: str, new: str, character: str
) -> None:
    """An invisible character reads differently for a person, a terminal, this check and
    systemd, so wherever it sits the unit is refused before parsing."""
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
    """The format characters known to be abused: the byte order mark, the zero-width
    characters, the soft hyphen, the bidirectional overrides and isolates. The rule reads the
    Unicode category (Cf), so it refuses the whole category, not only this list."""
    text = GOOD.replace("Description=ReD", f"Description=ReD{character}", 1)
    assert _refusals(text) == [
        f"{UNIT}: format character U+{ord(character):04X} is not allowed in a unit file"
    ]


def test_a_printable_non_ascii_letter_is_still_accepted() -> None:
    """The rule targets invisible characters, not UTF-8: an accented letter in a value no rule
    examines leaves the unit accepted."""
    text = GOOD.replace(
        "Description=ReD Monitoring Agent", "Description=Agent ReD, réseau privé", 1
    )
    assert _refusals(text) == []


# -- the target ---------------------------------------------------------------------------

BIND = "192.0.2.10"  # RFC 5737 TEST-NET-1: a documentation address, never a real host
DIGEST = "sha256:" + "c" * 64
IMAGE = f"ghcr.io/hawkixs/red-monitor@{DIGEST}"


def _unit_for(stack_root: str) -> str:
    return GOOD.replace("/opt/red-monitor", f"{stack_root}/red-monitor")


def _systemd_repo(tmp_path: Path, unit_text: str = GOOD, *, stack_root: str | None = None) -> Path:
    repo = conforming_tree(tmp_path, "red-monitor", "prod")
    gates: dict[str, tuple[object, str]] = {
        "deploy.ssh_host": ("private-1-deploy", "the host's ssh alias for the site")
    }
    if stack_root is not None:
        gates["deploy.stack_root"] = (stack_root, "a scratch root for a test run")
    write_manifest(repo, project="red-monitor", tier="prod", gates=gates, deploy=True)
    manifest = (
        (repo / "rail.yaml")
        .read_text()
        .replace(
            "  target: vps-traefik\n",
            "  target: private-systemd\n  site: private-1\n"
            "  unit: deploy/red-agent.service\n  binary: /usr/local/bin/red\n",
        )
    )
    manifest = "\n".join(
        '  healthcheck: "http://${BIND_ADDRESS}:9100/health"'
        if line.strip().startswith("healthcheck:")
        else line
        for line in manifest.splitlines()
    )
    (repo / "rail.yaml").write_text(manifest + "\n")
    (repo / "deploy").mkdir(exist_ok=True)
    (repo / "deploy" / "red-agent.service").write_text(unit_text)
    commit_all(repo, "feat: the agent's unit")
    return repo


def _host(tmp_path: Path) -> Path:
    path = tmp_path / "sites.yaml"
    path.write_text(f'sites:\n  private-1:\n    address: "{BIND}"\n')
    path.chmod(0o600)
    return path


class RecordingHost:
    def __init__(self) -> None:
        self.argv: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.argv.append(list(args))
        if args[0] == "ssh":
            return subprocess.CompletedProcess(args, 0, stdout="active\n", stderr="")
        return subprocess.run(args, **kwargs)


def _artefact(repo: Path) -> Artefact:
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return Artefact(version="0.1.0", sha=head, digest=DIGEST, image=IMAGE)


def _target(repo: Path, tmp_path: Path, *, run=None, http=None) -> PrivateSystemd:
    kwargs: dict[str, object] = {"run": run or RecordingHost()}
    if http is not None:
        kwargs["http"] = http
    return PrivateSystemd(repo, load_rail_config(repo), sites=_host(tmp_path), **kwargs)


def _script(tmp_path: Path) -> str:
    repo = _systemd_repo(tmp_path / "repo")
    return _target(repo, tmp_path).steps(_artefact(repo))[0].stdin or ""


def test_the_steps_name_the_site_and_restart_the_unit(tmp_path: Path) -> None:
    repo = _systemd_repo(tmp_path / "repo")
    steps = _target(repo, tmp_path).steps(_artefact(repo))
    assert "private-1-deploy" in steps[0].title and "restart red-agent.service" in steps[0].title
    assert steps[0].argv[0] == "ssh"
    assert steps[1].argv == ("GET", f"http://{BIND}:9100/health")
    assert steps[2].argv == ("GET", f"http://{BIND}:9100/version")


def test_the_remote_script_runs_the_spec_sequence_in_order(tmp_path: Path) -> None:
    script = _script(tmp_path)
    sequence = [
        "flock -n 9",
        "cat > red-agent.service",
        "cat > release.env",
        f"docker pull --quiet {IMAGE}",
        f"container=$(docker create {IMAGE} /usr/local/bin/red)",
        "trap ",
        'docker cp --follow-link "$container:/usr/local/bin/red"',
        'mv --force "$release/.red.new" "$release/red"',
        'ln -sfn "$release" "$root/current"',
        "sudo -n /usr/bin/systemctl daemon-reload",
        # what systemd loaded, read back before the restart: no drop-in, the same file as
        # current — never just a path that merely looks right
        "systemctl show --property=DropInPaths --value red-agent.service",
        "systemctl show --property=FragmentPath --value red-agent.service",
        '"$fragment" -ef "$root/current/red-agent.service"',
        "sudo -n /usr/bin/systemctl restart red-agent.service",
        "systemctl is-active red-agent.service",
    ]
    positions = [script.index(fragment) for fragment in sequence]
    assert positions == sorted(positions), list(zip(sequence, positions, strict=True))


def test_the_only_privileged_commands_are_the_two_the_sudoers_file_allows(tmp_path: Path) -> None:
    script = _script(tmp_path)
    assert [line for line in script.splitlines() if "sudo" in line] == [
        "sudo -n /usr/bin/systemctl daemon-reload",
        "sudo -n /usr/bin/systemctl restart red-agent.service",
    ]
    # the unit is linked at migration, never copied: the script names the link once, and only
    # compares what systemd reports having loaded against the release's own file, by identity
    lines = script.splitlines()
    assert [line for line in lines if "/etc/systemd" in line] == [
        "link=/etc/systemd/system/red-agent.service"
    ]
    assert [line.split(";")[0] for line in lines if "$link" in line] == [
        '  echo "red-agent.service: systemd loads ${fragment:-no unit file}, not the same '
        "file as $root/current/red-agent.service — $link must be a link to it, not a copy "
        'or a stale link" >&2',
    ]


def test_release_env_carries_what_version_reads_and_no_secret(tmp_path: Path) -> None:
    script = _script(tmp_path)
    body = script.split("cat > release.env <<'__RAIL_RELEASE.ENV__'\n", 1)[1].split("__RAIL_")[0]
    keys = [line.split("=", 1)[0] for line in body.splitlines()]
    assert keys == ["VERSION", "GIT_SHA", "IMAGE_DIGEST", "IMAGE_REFERENCE"]


def test_a_running_binary_is_replaced_by_rename_never_written_in_place(tmp_path: Path) -> None:
    """Review focus 3: redeploying the running version must not write into the executable
    systemd is running (`Text file busy`). The copy lands under a temporary name, then a
    rename swaps it in."""
    script = _script(tmp_path)
    assert 'docker cp --follow-link "$container:/usr/local/bin/red" "$release/.red.new"' in script
    assert '"$release/red"' not in script.split("mv --force", 1)[0]


def test_a_refused_unit_never_reaches_the_machine(tmp_path: Path) -> None:
    repo = _systemd_repo(tmp_path / "repo", GOOD.replace("User=red-monitor\n", "User=root\n"))
    host = RecordingHost()
    with pytest.raises(DeployError, match="User="):
        _target(repo, tmp_path, run=host).apply(_artefact(repo))
    assert [argv for argv in host.argv if argv[0] == "ssh"] == []


def test_a_unit_that_is_not_utf8_is_refused_before_the_first_ssh(tmp_path: Path) -> None:
    """`RemoteTarget.file_at` decodes the released file strictly as UTF-8. A unit committed with
    an invalid byte is refused by name before the first ssh, never decoded best-effort and
    shipped mangled to the machine."""
    repo = _systemd_repo(tmp_path / "repo")
    (repo / "deploy" / "red-agent.service").write_bytes(GOOD.encode("utf-8") + b"\xff")
    commit_all(repo, "fix: corrupt the unit with an invalid UTF-8 byte")
    host = RecordingHost()
    with pytest.raises(DeployError, match=r"deploy/red-agent\.service at \w+ is not UTF-8"):
        _target(repo, tmp_path, run=host).apply(_artefact(repo))
    assert [argv for argv in host.argv if argv[0] == "ssh"] == []


def test_the_deployment_is_verified_like_every_other_target(tmp_path: Path) -> None:
    repo = _systemd_repo(tmp_path / "repo")
    artefact = _artefact(repo)
    asked: list[str] = []

    def web(url: str, timeout: float) -> tuple[int, bytes]:
        asked.append(url)
        if url.endswith("/health"):
            return 200, b'{"status":"ok"}'
        return 200, json.dumps(
            {
                "project": "red-monitor",
                "version": artefact.version,
                "git_sha": artefact.sha,
                "image_digest": artefact.digest,
            }
        ).encode()

    live = _target(repo, tmp_path, http=web).apply(artefact)
    assert live.image_digest == DIGEST
    assert asked == [f"http://{BIND}:9100/health", f"http://{BIND}:9100/version"]


def test_records_name_the_site_never_its_address(tmp_path: Path) -> None:
    repo = _systemd_repo(tmp_path / "repo")
    target = _target(repo, tmp_path)
    assert target.domain == "private-1"
    assert target.redact(f"connect to host {BIND} port 22") == "connect to host private-1 port 22"


def test_the_flows_build_the_systemd_target_from_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _systemd_repo(tmp_path / "repo")
    monkeypatch.setenv("RAIL_SITES_FILE", str(_host(tmp_path)))
    assert isinstance(make_target(repo, load_rail_config(repo)), PrivateSystemd)


def test_every_target_the_manifest_can_declare_is_implemented() -> None:
    """513e109b, criterion 1: a declarable target without an implementation must show. With
    `private-systemd` none is left, and this keeps it so."""
    assert set(implementations()) == set(DeployTarget)


def _released(repo: Path) -> None:
    artefact = _artefact(repo)
    FileLedger(repo / RECEIPTS_DIR).attest(
        "red-monitor",
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


def test_plan_names_the_site_and_prints_the_resolved_steps(tmp_path: Path) -> None:
    repo = _systemd_repo(tmp_path / "repo")
    _released(repo)
    env = {"RAIL_SITES_FILE": str(_host(tmp_path))}
    out = CliRunner().invoke(main, ["deploy", "--repo", str(repo), "--plan"], env=env)
    assert out.exit_code == 0, out.output
    assert "on private-1" in out.output and "private-1-deploy" in out.output
    assert f"GET http://{BIND}:9100/health" in out.output
    assert f"GET http://{BIND}:9100/version" in out.output
    assert BIND not in (repo / "rail.yaml").read_text()


# -- the script, run under bash with stubs ---------------------------------------------------

STUB = """#!/bin/sh
echo "$(basename "$0") $*" >> "$RAIL_TEST_LOG"
case "$(basename "$0") $1 $2" in
  "docker create "*) echo fake-container ;;
  "docker cp "*)
    [ -n "$RAIL_TEST_CP_FAILS" ] && exit 1
    for last in "$@"; do :; done
    echo binary > "$last" ;;
  "systemctl show --property=DropInPaths") echo "$RAIL_TEST_DROPINS" ;;
  "systemctl show --property=FragmentPath") echo "$RAIL_TEST_FRAGMENT" ;;
esac
exit 0
"""

LINK = f"/etc/systemd/system/{UNIT}"


def _run_remote(
    tmp_path: Path,
    *,
    cp_fails: bool = False,
    dropins: str = "",
    fragment: str | None = None,
) -> tuple[subprocess.CompletedProcess, list[str], Path]:
    """The script under bash, with `docker`, `sudo` and `systemctl` stubbed on PATH. The
    `systemctl show` stub reports `dropins` and `fragment`; `{root}` in `fragment` is the
    scratch stack root. Default `fragment`: a real symlink in the scratch tree standing in for
    `/etc/systemd/system/<unit>`, itself linked to `current` — systemd 249's own way of
    reporting a unit loaded through a link (measured), the one `-ef` must still accept."""
    if shutil.which("flock") is None or shutil.which("bash") is None:
        pytest.skip("the remote script needs bash and flock (util-linux)")
    root = tmp_path / "opt"
    repo = _systemd_repo(tmp_path / "repo", _unit_for(str(root)), stack_root=str(root))
    script = _target(repo, tmp_path).steps(_artefact(repo))[0].stdin or ""
    if fragment is None:
        search_path = tmp_path / "systemd-search-path" / UNIT
        search_path.parent.mkdir()
        search_path.symlink_to(root / "red-monitor" / "current" / UNIT)
        fragment = str(search_path)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name in ("docker", "sudo", "systemctl"):
        (stubs / name).write_text(STUB)
        (stubs / name).chmod(0o755)
    log = tmp_path / "calls.log"
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "RAIL_TEST_LOG": str(log),
        "RAIL_TEST_DROPINS": dropins,
        "RAIL_TEST_FRAGMENT": fragment.replace("{root}", str(root)),
    }
    if cp_fails:
        env["RAIL_TEST_CP_FAILS"] = "1"
    done = subprocess.run(
        ["bash", "-s"], input=script, env=env, capture_output=True, text=True, timeout=30
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return done, calls, root / "red-monitor"


def test_the_remote_script_delivers_the_binary_and_restarts_through_sudo(tmp_path: Path) -> None:
    done, calls, project = _run_remote(tmp_path, cp_fails=False)
    assert done.returncode == 0, done.stderr
    release = project / "releases" / "0.1.0"
    assert (project / "current").resolve() == release.resolve()
    assert (release / "red").read_text() == "binary\n"
    assert (release / "red").stat().st_mode & 0o777 == 0o755
    assert not (release / ".red.new").exists()
    assert "VERSION=0.1.0" in (release / "release.env").read_text()
    reload = calls.index("sudo -n /usr/bin/systemctl daemon-reload")
    assert reload < calls.index("sudo -n /usr/bin/systemctl restart red-agent.service")
    assert calls[-1] == "docker rm --force fake-container"


RESTART = f"sudo -n /usr/bin/systemctl restart {UNIT}"


def test_a_unit_linked_to_current_is_accepted_under_either_name(tmp_path: Path) -> None:
    """systemd reports a linked unit's fragment as the link (measured on systemd 249) or, on
    other versions, as the file the link names: both are the released unit."""
    done, calls, _ = _run_remote(tmp_path, fragment=f"{{root}}/red-monitor/current/{UNIT}")
    assert done.returncode == 0, done.stderr
    assert RESTART in calls


def test_a_drop_in_stops_the_script_before_the_restart(tmp_path: Path) -> None:
    """A drop-in (`systemctl set-property` writes one under `system.control/`, an old
    `override.conf` may linger) overrides the checked unit once systemd reloads it, so its
    `MemoryMax=` or `User=` would drift outside the rail (spec decision 3)."""
    dropin = f"/etc/systemd/system.control/{UNIT}.d/50-MemoryMax.conf"
    done, calls, _ = _run_remote(tmp_path, dropins=dropin)
    assert done.returncode != 0
    assert dropin in done.stderr and "drop-in" in done.stderr
    assert RESTART not in calls, "the service must not be restarted"
    assert f"systemctl show --property=DropInPaths --value {UNIT}" in calls


@pytest.mark.parametrize(
    "fragment",
    [
        f"/lib/systemd/system/{UNIT}",  # a packaged copy wins over the link
        f"/etc/systemd/system/other-{UNIT}",  # the name is an alias of another unit
        "",  # systemd knows no such unit: the link is missing
    ],
    ids=["another-directory", "another-file", "none"],
)
def test_a_unit_loaded_from_anywhere_else_stops_the_script_before_the_restart(
    tmp_path: Path, fragment: str
) -> None:
    done, calls, _ = _run_remote(tmp_path, fragment=fragment)
    assert done.returncode != 0
    assert "systemd loads" in done.stderr and LINK in done.stderr
    if fragment:
        assert fragment in done.stderr
    assert RESTART not in calls, "the service must not be restarted"


def test_a_regular_file_copy_at_the_reported_path_stops_the_script_before_the_restart(
    tmp_path: Path,
) -> None:
    """`systemctl edit --full` (or any hand-edit) leaves a real, ordinary file at the unit's
    search path: `FragmentPath` then genuinely names it, a path a name-only check would accept
    on sight. `-ef` compares the file itself against the release's (`$root/current/<unit>`),
    so a copy is refused however faithful its reported name."""
    copy = tmp_path / "opt" / "not-the-release" / UNIT
    copy.parent.mkdir(parents=True)
    copy.write_text("a hand-edited copy, not a link to current\n")
    done, calls, _ = _run_remote(tmp_path, fragment=str(copy))
    assert done.returncode != 0
    assert "not the same file as" in done.stderr and str(copy) in done.stderr
    assert RESTART not in calls, "the service must not be restarted"


def test_a_symlink_pinned_to_an_old_release_stops_the_script_before_the_restart(
    tmp_path: Path,
) -> None:
    """A link never repointed after a later release still names a real file on disk. `-ef`
    follows it to the old release's file, not `current`'s, and refuses it even though its
    reported path looks exactly like the one migration created."""
    old_unit = tmp_path / "opt" / "red-monitor" / "releases" / "0.0.9" / UNIT
    old_unit.parent.mkdir(parents=True)
    old_unit.write_text("an old release's unit, current has since moved on\n")
    stale_link = tmp_path / "opt" / "stale-search-path" / UNIT
    stale_link.parent.mkdir(parents=True)
    stale_link.symlink_to(old_unit)
    done, calls, _ = _run_remote(tmp_path, fragment=str(stale_link))
    assert done.returncode != 0
    assert "not the same file as" in done.stderr
    assert RESTART not in calls, "the service must not be restarted"


def test_the_container_is_removed_even_when_the_copy_fails(tmp_path: Path) -> None:
    """Review focus 4, run rather than read: with a failing `docker cp` the script exits
    non-zero, the trap still removes the container, and nothing is restarted."""
    done, calls, project = _run_remote(tmp_path, cp_fails=True)
    assert done.returncode != 0
    assert calls[-1] == "docker rm --force fake-container"
    assert not any(call.startswith("sudo") for call in calls)
    assert not (project / "current").exists()
