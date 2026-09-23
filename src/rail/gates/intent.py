"""Stage 1 — intent: a delivery contract for this project exists in the ledger."""

from __future__ import annotations

from pathlib import Path

from rail.gates import GateResult, GateSpec, Need, Stage
from rail.ledger import RECEIPTS_DIR, TERMINAL_TICKET_STATUSES, LedgerError, RecordKind, open_ledger
from rail.ledger.file import FileLedger
from rail.model import declarations


def contract(repo: Path) -> GateResult:
    decl = declarations(repo)
    if isinstance(decl, str):
        return GateResult(Stage.INTENT, "contract", False, decl)
    try:
        if decl.project is None:
            found = FileLedger(repo / RECEIPTS_DIR).list(None, kind=RecordKind.CONTRACT)
            if found:
                return Need(
                    "project",
                    f"{len(found)} contract receipt(s) in {RECEIPTS_DIR} — `project:` says "
                    "which are this repository's",
                ).result(Stage.INTENT, "contract")
            return GateResult(
                Stage.INTENT,
                "contract",
                False,
                f"no contract recorded in {RECEIPTS_DIR} (default file ledger)",
            )
        project = decl.project
        ledger = open_ledger(repo)
        records = ledger.list(project, kind=RecordKind.CONTRACT)
        status = ledger.coordination_status()
    except LedgerError as exc:
        return GateResult(Stage.INTENT, "contract", False, str(exc))
    if not records:
        return GateResult(
            Stage.INTENT,
            "contract",
            False,
            f"no contract recorded for {project} (run `rail contract set`)",
        )
    if status in TERMINAL_TICKET_STATUSES:
        # A contract record proves an intention was once declared, not that it still covers
        # this work. A terminal ticket takes no further pull request (`rail bind` answers
        # `ticket_not_contractable`), so work continuing under it is covered by nothing —
        # and the gate used to stay green throughout, which is how it went unnoticed.
        cfg = decl.cfg
        assert cfg is not None  # a status is only ever returned once a manifest was loaded
        return GateResult(
            Stage.INTENT,
            "contract",
            False,
            f"the delivery ticket {str(cfg.ticket)[:8]} is {status}: it takes no further "
            "pull request, so it covers no new work. A new phase needs a new ticket, opened "
            "by the requester `red`; then point `ticket:` at it in rail.yaml",
        )
    latest = records[-1]
    objective = str(latest.payload.get("contract", {}).get("objective", ""))
    return GateResult(
        Stage.INTENT, "contract", True, f"contract {latest.digest[:19]} — {objective[:60]}"
    )


GATES = [GateSpec(Stage.INTENT, "contract", contract, scope="ledger")]
