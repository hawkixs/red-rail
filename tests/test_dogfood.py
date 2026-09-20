"""red-rail passes its own rail at tier dev on the shared ledger: brain-v42 is the authority.
Phase 2 was observed and accepted on its own ticket (2026-09-19); the independent reviewer
approved the phase-3 PR (#6) and brain observed its merge (integration receipt d550cf50,
2026-09-20) — no declared exception remains (spec §8, phase-3 proof)."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.model import LedgerBackend, Tier, load_rail_config

ROOT = Path(__file__).resolve().parents[1]
TICKET = "22a72dcf-1cf4-4c73-9476-8414da5484ab"  # red → red-rail, "Deliver red-rail phase 3"


def test_manifest_declares_dev_on_the_brain_ledger_without_exceptions() -> None:
    cfg = load_rail_config(ROOT)
    assert cfg.tier is Tier.DEV and cfg.ledger is LedgerBackend.BRAIN
    assert str(cfg.ticket) == TICKET
    assert cfg.gates == {}  # phase 3 closed: every gate of tier dev runs for real


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_rail_check_passes_on_this_repository_in_ci_scope() -> None:
    """CI never holds a ledger credential: with `ledger: brain` every ledger-scoped gate is
    reported as skipped, explicitly, and the remaining gates must pass."""
    out = CliRunner().invoke(main, ["check", "--repo", str(ROOT), "--ci"])
    assert out.exit_code == 0, out.output
    for gate in ("intent.contract", "hygiene.mirrors", "review.verdict", "integrate.receipt"):
        assert f"SKIP  {gate}" in out.output, gate
    assert "ledger brain is unreachable from CI" in out.output
