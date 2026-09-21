"""Stage 1 — intent: a delivery contract for this project exists in the ledger."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from rail.gates import GateResult, GateSpec, Stage
from rail.ledger import LedgerError, RecordKind, open_ledger
from rail.model import MANIFEST_NAME, load_rail_config, manifest_problem


def contract(repo: Path) -> GateResult:
    try:
        cfg = load_rail_config(repo)
        ledger = open_ledger(repo)
        records = ledger.list(cfg.project, kind=RecordKind.CONTRACT)
        status = ledger.coordination_status()
    except (FileNotFoundError, ValidationError):
        return GateResult(
            Stage.INTENT, "contract", False, manifest_problem(repo) or f"{MANIFEST_NAME} unreadable"
        )
    except LedgerError as exc:
        return GateResult(Stage.INTENT, "contract", False, str(exc))
    if not records:
        return GateResult(
            Stage.INTENT,
            "contract",
            False,
            f"no contract recorded for {cfg.project} (run `rail contract set`)",
        )
    if status == "closed":
        # A contract record proves an intention was once declared, not that it still covers
        # this work. A terminal ticket takes no further pull request (`rail bind` answers
        # `ticket_not_contractable`), so work continuing under it is covered by nothing —
        # and the gate used to stay green throughout, which is how it went unnoticed.
        return GateResult(
            Stage.INTENT,
            "contract",
            False,
            f"the delivery ticket {str(cfg.ticket)[:8]} is closed: its phase is accepted and "
            "takes no further pull request. A new phase needs a new ticket, opened by the "
            "requester `red`; then point `ticket:` at it in rail.yaml",
        )
    latest = records[-1]
    objective = str(latest.payload.get("contract", {}).get("objective", ""))
    return GateResult(
        Stage.INTENT, "contract", True, f"contract {latest.digest[:19]} — {objective[:60]}"
    )


GATES = [GateSpec(Stage.INTENT, "contract", contract, scope="ledger")]
