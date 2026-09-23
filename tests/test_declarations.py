"""The partial reading of the manifest (spec 2026-09-23): defaults only when it is absent."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from rail.gates import GateResult, Need, Stage
from rail.ledger import RECEIPTS_DIR, AttestationKind, open_ledger
from rail.ledger.file import FileLedger
from rail.model import Declarations, LedgerBackend, Stack, declarations
from tests.helpers import conforming_tree


def test_an_absent_manifest_gives_the_documented_defaults(tmp_path: Path) -> None:
    assert declarations(tmp_path) == Declarations(
        project=None, stack=None, ledger=LedgerBackend.FILE, cfg=None
    )


def test_a_valid_manifest_gives_its_fields(tmp_path: Path) -> None:
    decl = declarations(conforming_tree(tmp_path, "red-beta", "dev"))
    assert isinstance(decl, Declarations)
    assert (decl.project, decl.stack, decl.ledger) == ("red-beta", Stack.PYTHON, LedgerBackend.FILE)
    assert decl.cfg is not None and decl.cfg.project == "red-beta"


def test_an_invalid_manifest_never_falls_back_to_defaults(tmp_path: Path) -> None:
    (tmp_path / "rail.yaml").write_text("rail: 1\nproject: nope\nledger: brain\n")
    problem = declarations(tmp_path)
    assert isinstance(problem, str) and problem.startswith("rail.yaml is invalid: ")
    with pytest.raises(ValidationError):
        open_ledger(tmp_path)


def test_open_ledger_still_refuses_a_repository_without_a_manifest(tmp_path: Path) -> None:
    """The default file ledger is a gate's observation, never a write path: commands that
    open the ledger keep failing closed without a manifest."""
    with pytest.raises(FileNotFoundError):
        open_ledger(tmp_path)


def test_file_ledger_lists_every_project_when_none_is_named(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / RECEIPTS_DIR)
    for project, key in (("red-a", "k1"), ("red-b", "k2")):
        ledger.attest(
            project, AttestationKind.INTEGRATED, {"sha": "0" * 40}, issuer="op", idempotency_key=key
        )
    assert len(ledger.list(None, attestation=AttestationKind.INTEGRATED)) == 2
    assert len(ledger.list("red-a", attestation=AttestationKind.INTEGRATED)) == 1


def test_need_is_fail_closed_and_names_the_key() -> None:
    result = Need("stack", "no test file found").result(Stage.BUILD, "tests")
    assert result == GateResult(
        Stage.BUILD, "tests", False, "needs `stack:` — no test file found", needs="stack"
    )
    assert result.to_dict()["needs"] == "stack"
    assert GateResult(Stage.BUILD, "tests", True, "ok").to_dict()["needs"] is None
