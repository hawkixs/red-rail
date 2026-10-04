"""Session-wide isolation from the developer's host."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_host_sites_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`observe.visible` reads the host's private sites file. A test that does not declare one
    sees none, never the developer's own `~/.config/red-rail/sites.yaml`."""
    monkeypatch.setenv("RAIL_SITES_FILE", str(tmp_path / "no-host" / "sites.yaml"))


@pytest.fixture(autouse=True)
def _no_host_spool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pending attestations wait in `$RAIL_SPOOL_DIR/<project>`: a test never sees the
    developer's own `~/.local/state/red-rail/spool`."""
    monkeypatch.setenv("RAIL_SPOOL_DIR", str(tmp_path / "spool"))


@pytest.fixture(autouse=True)
def _operator_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test never inherits the harness running the suite: no agent marker, and the actor is
    the operator unless the test declares another one with `RAIL_ACTOR`."""
    from rail.policy import AGENT_MARKERS, AGENT_NAME_VARIABLE

    for marker in AGENT_MARKERS:
        for name in (*marker.presence, marker.session or ""):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(AGENT_NAME_VARIABLE, raising=False)
    monkeypatch.setenv("RAIL_ACTOR", "operator")
