"""red-rail passes its own rail at tier dev on the shared ledger (phase 2): brain-v42 is the
authority; one declared exception remains until brain has observed the merge of the phase-2
PR (spec §8). The review gate is live: the independent reviewer approved that PR."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.model import LedgerBackend, Tier, load_rail_config

ROOT = Path(__file__).resolve().parents[1]
TICKET = "3f78854b-1e8d-4d2c-858c-1d8be8fbba91"  # red → red-rail, "Deliver red-rail phase 2"


def test_manifest_declares_dev_on_the_brain_ledger_with_declared_exceptions() -> None:
    cfg = load_rail_config(ROOT)
    assert cfg.tier is Tier.DEV and cfg.ledger is LedgerBackend.BRAIN
    assert str(cfg.ticket) == TICKET
    # the review gate is live since the independent reviewer approved PR #3 (2026-09-19);
    # the integration receipt arrives with brain's observation of that PR's merge
    assert set(cfg.gates) == {"integrate.receipt"}
    assert cfg.gates["integrate.receipt"].value is False
    assert "phase-2 PR" in cfg.gates["integrate.receipt"].reason


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_rail_check_passes_on_this_repository_in_ci_scope() -> None:
    """CI never holds a ledger credential: with `ledger: brain` every ledger-scoped gate is
    reported as skipped, explicitly, and the remaining gates must pass."""
    out = CliRunner().invoke(main, ["check", "--repo", str(ROOT), "--ci"])
    assert out.exit_code == 0, out.output
    for gate in ("intent.contract", "hygiene.mirrors", "review.verdict", "integrate.receipt"):
        assert f"SKIP  {gate}" in out.output, gate
    assert "ledger brain is unreachable from CI" in out.output
