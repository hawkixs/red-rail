"""`rail bind --pr N`: the pull request is bound to the delivery contract at its opening —
the ledger's view is read first, a PR already bound is reported and never re-bound."""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.brain.client import BrainClient
from rail.cli import main
from rail.commands import bind as bind_command
from rail.ledger import RECEIPTS_DIR, RecordKind
from rail.ledger.brain import BrainLedger
from rail.ledger.file import FileLedger
from tests.fake_brain import FakeBrain
from tests.helpers import conforming_tree
from tests.test_ledger_brain import CONTRACT, _clock

HEAD = "a" * 40


def test_bind_on_the_file_ledger_records_the_pull_request(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    out = CliRunner().invoke(
        main, ["bind", "--repo", str(repo), "--pr", "7", "--head", HEAD, "--json"]
    )
    assert out.exit_code == 0, out.output
    record = json.loads(out.output)
    assert record["kind"] == "binding"
    assert record["payload"] == {"repository": "hawkixs/red-alpha", "number": 7, "head_sha": HEAD}
    assert record["idempotency_key"] == f"bind:hawkixs/red-alpha:7:{HEAD[:12]}"
    bindings = FileLedger(repo / RECEIPTS_DIR).list("red-alpha", kind=RecordKind.BINDING)
    assert len(bindings) == 1


def test_a_bound_pull_request_is_reported_never_rebound(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    runner = CliRunner()
    first = runner.invoke(main, ["bind", "--repo", str(repo), "--pr", "7", "--head", HEAD])
    assert first.exit_code == 0, first.output
    again = runner.invoke(main, ["bind", "--repo", str(repo), "--pr", "7", "--head", "b" * 40])
    assert again.exit_code == 0, again.output
    assert "already bound" in again.output
    assert len(FileLedger(repo / RECEIPTS_DIR).list("red-alpha", kind=RecordKind.BINDING)) == 1


def test_the_head_comes_from_github_through_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    calls: list[list[str]] = []

    def gh(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="c" * 40 + "\n", stderr="")

    monkeypatch.setattr(bind_command, "RUN", gh)
    out = CliRunner().invoke(main, ["bind", "--repo", str(repo), "--pr", "9", "--json"])
    assert out.exit_code == 0, out.output
    assert calls == [["gh", "api", "repos/hawkixs/red-alpha/pulls/9", "--jq", ".head.sha"]]
    assert json.loads(out.output)["payload"]["head_sha"] == "c" * 40

    def broken(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="HTTP 404: Not Found")

    monkeypatch.setattr(bind_command, "RUN", broken)
    out = CliRunner().invoke(main, ["bind", "--repo", str(repo), "--pr", "10"])
    assert out.exit_code == 1 and "404" in out.output


def test_bind_needs_a_github_remote(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    subprocess.run(["git", "-C", str(repo), "remote", "remove", "origin"], check=True)
    out = CliRunner().invoke(main, ["bind", "--repo", str(repo), "--pr", "7", "--head", HEAD])
    assert out.exit_code == 1 and "GitHub remote" in out.output


def test_bind_on_the_brain_ledger_reads_the_view_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = conforming_tree(tmp_path, "red-probe", "dev")
    brain = FakeBrain(agent="operator")
    ticket = brain.add_ticket("red", "red-probe")
    brain.register_repository("red-probe", 4242, "hawkixs/red-probe")
    ledger = BrainLedger(
        BrainClient.in_memory(brain, agent="operator"),
        ticket=ticket,
        project="red-probe",
        receipts_dir=repo / RECEIPTS_DIR,
        clock=_clock(),
        repository_id=lambda slug: 4242,
    )
    monkeypatch.setattr(bind_command, "open_ledger", lambda repo: ledger)
    ledger.contract_set("red-probe", CONTRACT, reason="r", issuer="red", idempotency_key="c1")
    runner = CliRunner()
    out = runner.invoke(main, ["bind", "--repo", str(repo), "--pr", "3", "--head", HEAD])
    assert out.exit_code == 0, out.output
    binds = [n for n, _ in brain.calls if n == "brain_delivery_bind_pr"]
    assert binds == ["brain_delivery_bind_pr"]
    assert brain.tickets[ticket].bindings[0]["pr_number"] == 3
    out = runner.invoke(main, ["bind", "--repo", str(repo), "--pr", "3", "--head", HEAD])
    assert out.exit_code == 0 and "already bound" in out.output
    assert len([n for n, _ in brain.calls if n == "brain_delivery_bind_pr"]) == 1
