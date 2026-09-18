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
            "target_branch": "main",
            "required_checks": [],
            "review": {"required_approvals": 1, "allowed_reviewers": []},
        }
    ]
    assert record["payload"]["contract"]["acceptance_criteria"] == [
        "rail check passes",
        "deployed once",
    ]
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
    assert record["idempotency_key"] == f"released:{head}"
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


def test_attest_without_sha_or_key_gets_a_random_key(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    out = CliRunner().invoke(main, ["attest", "fulfilled", "--repo", str(repo)])
    assert out.exit_code == 0, out.output
    assert "fulfilled:" in out.output and "docs/receipts/" in out.output


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


def test_attest_refuses_a_brain_ledger_in_phase_1(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text().replace("ledger: file", "ledger: brain")
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
