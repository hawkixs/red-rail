"""The package version has one source of truth — `[project].version` in pyproject.toml — read
from the installed distribution: `rail --version` can never lag behind the release metadata
again (it reported 0.2.0 for two releases before the phase-3 closing pre-review caught it)."""

import importlib
import importlib.metadata
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

import rail
from rail.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _declared_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_package_version_is_the_one_declared_in_pyproject() -> None:
    assert rail.__version__ == _declared_version()


def test_cli_reports_the_declared_version() -> None:
    out = CliRunner().invoke(main, ["--version"])
    assert out.exit_code == 0
    assert out.output.strip() == f"rail, version {_declared_version()}"


def test_version_falls_back_when_the_distribution_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    try:
        assert importlib.reload(rail).__version__ == "0+unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(rail)
