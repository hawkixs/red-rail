"""red-rail passes its own rail at tier dev, with the review verdict as a declared exception
until the independent reviewer exists (ADR-0003)."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.model import LedgerBackend, Tier, load_rail_config

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_declares_dev_on_the_file_ledger_with_one_exception() -> None:
    cfg = load_rail_config(ROOT)
    assert cfg.tier is Tier.DEV and cfg.ledger is LedgerBackend.FILE
    assert set(cfg.gates) == {"review.verdict"}
    assert cfg.gates["review.verdict"].value is False
    assert "ADR-0003" in cfg.gates["review.verdict"].reason


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_rail_check_passes_on_this_repository_in_ci_scope() -> None:
    out = CliRunner().invoke(main, ["check", "--repo", str(ROOT), "--ci"])
    assert out.exit_code == 0, out.output
    assert "EXC   review.verdict" in out.output
