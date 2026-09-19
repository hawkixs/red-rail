"""Writing and reading evidence from the command line, file ledger only in phase 1."""

import json
from pathlib import Path

from click.testing import CliRunner

from rail.cli import main
from rail.ledger import RECEIPTS_DIR, AttestationKind, RecordKind
from rail.ledger.file import FileLedger, load_receipt
from tests.helpers import conforming_tree, git


def _repo(tmp_path: Path) -> Path:
    return conforming_tree(tmp_path, "red-alpha", "bootstrap")


def test_contract_set_records_a_contract_from_the_canonical_remote(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    out = CliRunner().invoke(
        main,
        [
            "contract",
            "set",
            "--repo",
            str(repo),
            "--objective",
            "ship red-alpha",
            "--criterion",
            "rail check passes",
            "--criterion",
            "deployed once",
            "--reason",
            "bootstrap",
            "--json",
        ],
    )
    assert out.exit_code == 0, out.output
    record = json.loads(out.output)
    assert record["kind"] == "contract"
    assert record["payload"]["contract"]["deliverables"] == [
        {
            "key": "main",
            "repository": "hawkixs/red-alpha",
            "repository_id": None,
            "target_branch": "main",
            "required_checks": [],
            "no_checks_reason": None,
            "review": {"required_approvals": 1, "allowed_reviewers": []},
        }
    ]
    assert record["payload"]["contract"]["acceptance_criteria"] == [
        "rail check passes",
        "deployed once",
    ]
    assert record["payload"]["contract"]["acceptance_mode"] == "explicit"
    assert record["payload"]["contract"]["priority"] == 0
    records = FileLedger(repo / RECEIPTS_DIR).list("red-alpha", kind=RecordKind.CONTRACT)
    assert len(records) == 1 and records[0].verify()


def test_contract_set_is_idempotent_by_key(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    args = [
        "contract",
        "set",
        "--repo",
        str(repo),
        "--objective",
        "x",
        "--reason",
        "r",
        "--key",
        "c1",
    ]
    assert CliRunner().invoke(main, args).exit_code == 0
    assert CliRunner().invoke(main, args).exit_code == 0
    assert len(list((repo / RECEIPTS_DIR).glob("*.json"))) == 1
    conflict = CliRunner().invoke(main, [*args[:-2], "--objective", "y", "--key", "c1"])
    assert conflict.exit_code == 1 and "idempotency" in conflict.output


def test_contract_set_needs_a_deliverable_when_no_github_remote(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    git(repo, "remote", "remove", "origin")
    out = CliRunner().invoke(
        main, ["contract", "set", "--repo", str(repo), "--objective", "x", "--reason", "r"]
    )
    assert out.exit_code == 2 and "--deliverable" in out.output
    out = CliRunner().invoke(
        main,
        [
            "contract",
            "set",
            "--repo",
            str(repo),
            "--objective",
            "x",
            "--reason",
            "r",
            "--deliverable",
            "hawkixs/other:release",
        ],
    )
    assert out.exit_code == 0, out.output


def test_attest_writes_a_receipt_with_typed_data(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    out = CliRunner().invoke(
        main,
        [
            "attest",
            "released",
            "--repo",
            str(repo),
            "--data",
            f"sha={head}",
            "--data",
            "version=1.0.0",
            "--data",
            "digest=sha256:abc",
            "--data",
            "drill=false",
            "--data",
            "attempts=2",
            "--issuer",
            "op",
            "--json",
        ],
    )
    assert out.exit_code == 0, out.output
    record = json.loads(out.output)
    assert record["payload"] == {
        "kind": "released",
        "data": {
            "sha": head,
            "version": "1.0.0",
            "digest": "sha256:abc",
            "drill": False,
            "attempts": 2,
        },
    }
    assert record["idempotency_key"] == "released:1.0.0"
    path = next((repo / RECEIPTS_DIR).glob("*-released-*.json"))
    assert load_receipt(path).verify()


def test_attest_data_json_and_explicit_key(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    out = CliRunner().invoke(
        main,
        [
            "attest",
            "incident_detected",
            "--repo",
            str(repo),
            "--data-json",
            '{"severity": "high"}',
            "--key",
            "inc-1",
            "--json",
        ],
    )
    assert out.exit_code == 0, out.output
    record = json.loads(out.output)
    assert (
        record["payload"]["data"] == {"severity": "high"} and record["idempotency_key"] == "inc-1"
    )


def test_attest_default_keys_follow_the_event_scheme(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    runner = CliRunner()
    out = runner.invoke(
        main,
        [
            "attest",
            "deployed",
            "--repo",
            str(repo),
            "--data",
            "digest=sha256:abc",
            "--data",
            "target=vps-traefik",
            "--json",
        ],
    )
    assert out.exit_code == 0, out.output
    key = json.loads(out.output)["idempotency_key"]
    assert key.startswith("deployed:vps-traefik:sha256:abc:") and key.endswith("Z")
    out = runner.invoke(main, ["attest", "gate_passed", "--repo", str(repo), "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["idempotency_key"].startswith("gate_passed:")


def test_attest_from_replays_a_receipt(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    elsewhere = FileLedger(tmp_path / "elsewhere")
    record = elsewhere.attest(
        "red-alpha",
        AttestationKind.DEPLOYED,
        {"digest": "sha256:abc"},
        issuer="op",
        idempotency_key="d1",
    )
    receipt = next((tmp_path / "elsewhere").glob("*.json"))
    out = CliRunner().invoke(
        main, ["attest", "deployed", "--repo", str(repo), "--from", str(receipt), "--json"]
    )
    assert out.exit_code == 0, out.output
    replayed = json.loads(out.output)
    assert replayed["idempotency_key"] == "d1" and replayed["payload"] == record.payload
    assert (
        CliRunner()
        .invoke(main, ["attest", "deployed", "--repo", str(repo), "--from", str(receipt)])
        .exit_code
        == 0
    )
    assert len(list((repo / RECEIPTS_DIR).glob("*.json"))) == 1
    replayed_records = FileLedger(repo / RECEIPTS_DIR).list("red-alpha")
    assert len(replayed_records) == 1 and replayed_records[0].recorded_at == record.recorded_at


def test_attest_refuses_a_brain_ledger_in_phase_1(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml")
        .read_text()
        .replace("ledger: file", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f")
    )
    out = CliRunner().invoke(main, ["attest", "fulfilled", "--repo", str(repo)])
    assert out.exit_code == 1 and "phase 2" in out.output


def test_ledger_list_reads_back(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    CliRunner().invoke(main, ["attest", "fulfilled", "--repo", str(repo), "--key", "f1"])
    CliRunner().invoke(
        main,
        ["attest", "deployed", "--repo", str(repo), "--key", "d1", "--data", "digest=sha256:x"],
    )
    out = CliRunner().invoke(main, ["ledger", "list", "--repo", str(repo)])
    assert out.exit_code == 0 and "fulfilled" in out.output and "deployed" in out.output
    out = CliRunner().invoke(
        main, ["ledger", "list", "--repo", str(repo), "--attestation", "deployed", "--json"]
    )
    rows = json.loads(out.output)
    assert [r["payload"]["kind"] for r in rows] == ["deployed"]


def test_attest_data_keeps_identifiers_as_text(tmp_path: Path) -> None:
    """Review finding: a version, a sha prefix or a digest must never be re-typed."""
    repo = _repo(tmp_path)
    out = CliRunner().invoke(
        main,
        [
            "attest",
            "released",
            "--repo",
            str(repo),
            "--json",
            "--data",
            "version=1.20",
            "--data",
            "sha=0123456789ab",
            "--data",
            "digest=00",
            "--data",
            "attempts=2",
            "--data",
            "ratio=0.5",
            "--data",
            "drill=false",
        ],
    )
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)["payload"]["data"]
    assert data == {
        "version": "1.20",
        "sha": "0123456789ab",
        "digest": "00",
        "attempts": 2,
        "ratio": "0.5",
        "drill": False,
    }


def test_attest_refuses_a_float_in_the_data(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    out = CliRunner().invoke(
        main, ["attest", "deployed", "--repo", str(repo), "--data-json", '{"ratio": 1.5}']
    )
    assert out.exit_code == 1 and "float" in out.output
    assert not list((repo / RECEIPTS_DIR).glob("*.json"))


def test_contract_set_accepts_priority_and_acceptance_mode(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    out = CliRunner().invoke(
        main,
        [
            "contract",
            "set",
            "--repo",
            str(repo),
            "--objective",
            "x",
            "--reason",
            "r",
            "--priority",
            "7",
            "--acceptance-mode",
            "automatic",
            "--json",
        ],
    )
    assert out.exit_code == 0, out.output
    contract = json.loads(out.output)["payload"]["contract"]
    assert contract["priority"] == 7 and contract["acceptance_mode"] == "automatic"
