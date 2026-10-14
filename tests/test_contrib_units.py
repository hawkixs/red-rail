import configparser
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
UNIT_DIR = ROOT / "contrib" / "systemd" / "user"
UNIT_NAMES = (
    "rail-metrics-publish.service",
    "rail-metrics-publish.timer",
    "rail-metrics-serve.service",
    "rail-reviewer.service",
    "rail-reviewer.timer",
)


def _read_unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False)
    parser.read(UNIT_DIR / name)
    return parser


def test_units_parse() -> None:
    for name in UNIT_NAMES:
        parser = _read_unit(name)
        assert parser.sections()


def test_serve_restarts_on_failure() -> None:
    parser = _read_unit("rail-metrics-serve.service")
    assert parser["Service"]["Restart"] == "on-failure"


def test_publish_is_oneshot() -> None:
    parser = _read_unit("rail-metrics-publish.service")
    assert parser["Service"]["Type"] == "oneshot"


def test_serve_cannot_gain_privileges() -> None:
    parser = _read_unit("rail-metrics-serve.service")
    assert parser["Service"]["NoNewPrivileges"] == "yes"


def test_publish_timer_is_hourly_and_persistent() -> None:
    parser = _read_unit("rail-metrics-publish.timer")
    assert parser["Timer"]["OnCalendar"] == "hourly"
    assert parser["Timer"]["Persistent"] == "true"


def test_reviewer_pass_is_oneshot_and_bounded() -> None:
    parser = _read_unit("rail-reviewer.service")
    assert parser["Service"]["Type"] == "oneshot"
    assert parser["Service"].get("ExecStart", raw=True) == "%h/.local/bin/rail reviewer once"
    assert parser["Service"]["TimeoutStartSec"] == "3600"


def test_reviewer_path_reaches_the_judge_clis() -> None:
    # configparser keeps only the last of repeated keys: read the Environment lines whole
    lines = (UNIT_DIR / "rail-reviewer.service").read_text().splitlines()
    path = "Environment=PATH=%h/.local/bin:%h/.opencode/bin:"
    assert any(line.startswith(path) for line in lines)


def test_reviewer_timer_waits_after_each_pass() -> None:
    parser = _read_unit("rail-reviewer.timer")
    assert parser["Timer"]["OnUnitInactiveSec"] == "2min"
    assert parser["Install"]["WantedBy"] == "timers.target"


def test_no_machine_address() -> None:
    for name in UNIT_NAMES:
        contents = (UNIT_DIR / name).read_text()
        addresses = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", contents)
        assert set(addresses) <= {"127.0.0.1"}
