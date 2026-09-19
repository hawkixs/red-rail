"""Stage 1 — intent: a delivery contract for this project exists in the ledger."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from rail.gates import GateResult, GateSpec, Stage
from rail.ledger import LedgerError, RecordKind, open_ledger
from rail.model import MANIFEST_NAME, load_rail_config


def contract(repo: Path) -> GateResult:
    try:
        cfg = load_rail_config(repo)
        records = open_ledger(repo).list(cfg.project, kind=RecordKind.CONTRACT)
    except (FileNotFoundError, ValidationError):
        return GateResult(Stage.INTENT, "contract", False, f"{MANIFEST_NAME} unreadable")
    except LedgerError as exc:
        return GateResult(Stage.INTENT, "contract", False, str(exc))
    if not records:
        return GateResult(
            Stage.INTENT,
            "contract",
            False,
            f"no contract recorded for {cfg.project} (run `rail contract set`)",
        )
    latest = records[-1]
    objective = str(latest.payload.get("contract", {}).get("objective", ""))
    return GateResult(
        Stage.INTENT, "contract", True, f"contract {latest.digest[:19]} — {objective[:60]}"
    )


GATES = [GateSpec(Stage.INTENT, "contract", contract, scope="ledger")]
