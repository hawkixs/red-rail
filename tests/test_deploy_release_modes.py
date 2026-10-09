"""The release directories every remote script creates are traversable by the unit's user."""

import stat
import subprocess
from pathlib import Path

from rail.deploy.remote import lock_preamble


def _run_preamble(root: Path, version: str = "0.1.0") -> None:
    """Run the real preamble under the restrictive umask an ssh session may carry."""
    script = "\n".join(["umask 077", *lock_preamble(str(root), f"{root}/releases/{version}")])
    subprocess.run(["bash", "-c", script], check=True)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_release_directories_are_0755_whatever_the_umask(tmp_path: Path) -> None:
    """A service running as its own user must traverse `releases/<version>` (incident: the
    first red-backup deploy failed with 203/EXEC under a 007 umask)."""
    root = tmp_path / "red-backup"
    root.mkdir()
    _run_preamble(root)
    assert _mode(root / "releases") == 0o755
    assert _mode(root / "releases" / "0.1.0") == 0o755


def test_release_directories_created_earlier_are_repaired(tmp_path: Path) -> None:
    """A directory left 0770 by an earlier deployment is set back to 0755."""
    root = tmp_path / "red-backup"
    (root / "releases" / "0.1.0").mkdir(parents=True)
    for path in (root / "releases", root / "releases" / "0.1.0"):
        path.chmod(0o770)
    _run_preamble(root)
    assert _mode(root / "releases") == 0o755
    assert _mode(root / "releases" / "0.1.0") == 0o755
