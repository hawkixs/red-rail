"""Hygiene gates: the floor every tier stands on, starting with `bootstrap`."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from rail.gates import GateResult, Stage
from rail.model import MANIFEST_NAME, load_rail_config

DOCS_DIRS = ("docs/specs", "docs/plans", "docs/adr")


def rail_config(repo: Path) -> GateResult:
    try:
        cfg = load_rail_config(repo)
    except FileNotFoundError:
        return GateResult(Stage.HYGIENE, "rail_config", False, f"{MANIFEST_NAME} is missing")
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        return GateResult(Stage.HYGIENE, "rail_config", False, f"{location}: {first['msg']}")
    return GateResult(Stage.HYGIENE, "rail_config", True, f"tier={cfg.tier.value}")


def docs_layout(repo: Path) -> GateResult:
    missing = [d for d in DOCS_DIRS if not (repo / d).is_dir()]
    if missing:
        return GateResult(Stage.HYGIENE, "docs_layout", False, "missing: " + ", ".join(missing))
    return GateResult(Stage.HYGIENE, "docs_layout", True, "docs/{specs,plans,adr} present")
