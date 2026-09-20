"""hawkixs/red-rail is public (2026-09-20, red-watcher's conditions): the licence travels with
the package, and the drift snapshots — a map of every ReD project — never land in the tree."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_licence_and_notice_are_present_and_declared() -> None:
    assert (ROOT / "LICENSE").read_text().lstrip().startswith("Apache License")
    assert "Copyright 2026 Hawixs" in (ROOT / "NOTICE").read_text()
    with (ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)["project"]
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE", "NOTICE"]


def test_drift_snapshots_are_written_outside_the_repository() -> None:
    makefile = (ROOT / "Makefile").read_text()
    default = re.search(r"^AUDIT_DIR \?= (\S+)$", makefile, re.MULTILINE)
    assert default is not None, "AUDIT_DIR default missing"
    assert default.group(1).startswith("../../")  # the ReD root, not this tree
    audit = makefile[makefile.index("\naudit:") :]
    assert "$(AUDIT_DIR)/$(DATE)-projects.json" in audit and "docs/audits" not in audit
    assert "docs/audits/" in (ROOT / ".gitignore").read_text().splitlines()
    assert not list((ROOT / "docs" / "audits").glob("*-projects.*"))  # none left behind
