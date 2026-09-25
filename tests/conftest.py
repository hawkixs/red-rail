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
