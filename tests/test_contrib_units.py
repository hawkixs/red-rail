import configparser
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
UNIT_DIR = ROOT / "contrib" / "systemd" / "user"
UNIT_NAMES = (
    "rail-metrics-publish.service",
    "rail-metrics-publish.timer",
    "rail-metrics-serve.service",
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


def test_no_machine_address() -> None:
    for name in UNIT_NAMES:
        contents = (UNIT_DIR / name).read_text()
        addresses = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", contents)
        assert set(addresses) <= {"127.0.0.1"}
