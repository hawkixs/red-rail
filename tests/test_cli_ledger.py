"""Writing and reading evidence from the command line, file ledger only in phase 1."""

import json
from pathlib import Path

import pytest
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
            "--no-checks-reason",
            "fixture: no check declared",
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
            "no_checks_reason": "fixture: no check declared",
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
        "--no-checks-reason",
        "fixture: no check declared",
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
            "--no-checks-reason",
            "fixture: no check declared",
        ],
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
            "--no-checks-reason",
            "fixture: no check declared",
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


def test_attest_in_brain_mode_without_a_token_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml")
        .read_text()
        .replace("ledger: file\n", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n")
    )
    monkeypatch.setenv("RAIL_BRAIN_TOKEN_FILE", str(tmp_path / "none"))
    out = CliRunner().invoke(main, ["attest", "deployed", "--repo", str(repo), "--data", "sha=abc"])
    assert out.exit_code == 1 and "brain token" in out.output
    assert not list((repo / RECEIPTS_DIR).glob("*-deployed-*.json"))


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
            "--no-checks-reason",
            "fixture: no check declared",
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


def test_contract_set_names_required_checks_and_reviewers(tmp_path: Path) -> None:
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
            "--required-check",
            "check_run:red-rail/review@red-rail-reviewer",
            "--required-check",
            "commit_status:ci/build#15368",
            "--allowed-reviewer",
            "red-rail-reviewer[bot]",
            "--required-approvals",
            "1",
            "--json",
        ],
    )
    assert out.exit_code == 0, out.output
    deliverable = json.loads(out.output)["payload"]["contract"]["deliverables"][0]
    assert deliverable["required_checks"] == [
        {
            "kind": "check_run",
            "name": "red-rail/review",
            "app_slug": "red-rail-reviewer",
            "provider_id": None,
        },
        {"kind": "commit_status", "name": "ci/build", "app_slug": None, "provider_id": 15368},
    ]
    assert deliverable["review"] == {
        "required_approvals": 1,
        "allowed_reviewers": ["red-rail-reviewer[bot]"],
    }
    bad = CliRunner().invoke(
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
            "--required-check",
            "red-rail/review",
        ],
    )
    assert bad.exit_code == 2 and "KIND:NAME@APP_SLUG" in bad.output


def test_contract_key_names_the_ticket_and_the_next_revision_in_brain_mode(tmp_path: Path) -> None:
    from rail.brain.client import BrainClient
    from rail.ledger import Contract, Deliverable, open_ledger
    from tests.fake_brain import FakeBrain

    repo = _repo(tmp_path)
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml")
        .read_text()
        .replace("ledger: file\n", "ledger: brain\nticket: 04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f\n")
    )
    brain = FakeBrain(agent="operator")
    brain.add_ticket("red", "red-alpha", "04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f")
    ledger = open_ledger(repo, client=BrainClient.in_memory(brain, agent="operator"))
    first = Contract(
        objective="v1",
        deliverables=[
            Deliverable(
                key="main",
                repository="hawkixs/red-alpha",
                no_checks_reason="fixture: no check declared",
            )
        ],
    )
    record = ledger.contract_set("red-alpha", first, reason="r", issuer="op", idempotency_key="k1")
    assert record.idempotency_key == "contract:04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f:1"
    from rail.commands.contract import next_contract_key

    assert next_contract_key(ledger, "red-alpha", "04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f") == (
        "contract:04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f:2"
    )
    assert (
        next_contract_key(FileLedger(tmp_path / "empty"), "red-alpha", None)
        == "contract:red-alpha:1"
    )


def test_contract_set_names_the_missing_flag_instead_of_refusing_late(tmp_path: Path) -> None:
    """A deliverable with no required check must say why. Without `--no-checks-reason` the
    command stops before the ledger and names the flag; brain answered `invalid_arguments:
    delivery arguments are invalid` without naming the field, which cost a pilot half an
    hour of reading `deploy/flow.py` and another project's receipt."""
    repo = _repo(tmp_path)
    out = CliRunner().invoke(
        main,
        ["contract", "set", "--repo", str(repo), "--objective", "x", "--reason", "r"],
    )
    assert out.exit_code == 2, out.output
    assert "--no-checks-reason" in out.output and "--required-check" in out.output
    assert not list((repo / RECEIPTS_DIR).glob("*.json"))


def test_contract_set_records_the_declared_absence_of_checks(tmp_path: Path) -> None:
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
            "--no-checks-reason",
            "tier bootstrap: no pull request is reviewed before the design stage",
            "--json",
        ],
    )
    assert out.exit_code == 0, out.output
    deliverable = json.loads(out.output)["payload"]["contract"]["deliverables"][0]
    assert deliverable["required_checks"] == []
    assert deliverable["no_checks_reason"].startswith("tier bootstrap")


def test_contract_set_with_a_required_check_needs_no_reason(tmp_path: Path) -> None:
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
            "--required-check",
            "check_run:red-rail/review@red-rail-reviewer",
            "--json",
        ],
    )
    assert out.exit_code == 0, out.output
    deliverable = json.loads(out.output)["payload"]["contract"]["deliverables"][0]
    assert deliverable["no_checks_reason"] is None
    assert deliverable["required_checks"][0]["app_slug"] == "red-rail-reviewer"


def test_contract_set_treats_a_blank_reason_as_no_reason(tmp_path: Path) -> None:
    """`--no-checks-reason "   "` satisfied the guard by truthiness and stored a reason that
    explains nothing — in an append-only ledger. Blank is absent."""
    repo = _repo(tmp_path)
    base = ["contract", "set", "--repo", str(repo), "--objective", "x", "--reason", "r"]
    for blank in ("   ", "\t", "\n ", ""):
        out = CliRunner().invoke(main, [*base, "--no-checks-reason", blank])
        assert out.exit_code == 2, f"{blank!r}: {out.output}"
        assert "--no-checks-reason" in out.output
    assert not list((repo / RECEIPTS_DIR).glob("*.json"))


def test_contract_set_refuses_a_reason_that_contradicts_a_required_check(tmp_path: Path) -> None:
    """The two flags are the two sides of one declaration; a blank reason is not a side."""
    repo = _repo(tmp_path)
    base = [
        "contract",
        "set",
        "--repo",
        str(repo),
        "--objective",
        "x",
        "--reason",
        "r",
        "--required-check",
        "check_run:red-rail/review@red-rail-reviewer",
    ]
    out = CliRunner().invoke(main, [*base, "--no-checks-reason", "because"])
    assert out.exit_code == 2 and "contradicts" in out.output

    # blank is absent, so this is the plain `--required-check` case and must be accepted —
    # and must not exit 1 with a raw pydantic ValidationError
    out = CliRunner().invoke(main, [*base, "--no-checks-reason", "  ", "--json"])
    assert out.exit_code == 0, out.output
    assert (
        json.loads(out.output)["payload"]["contract"]["deliverables"][0]["no_checks_reason"] is None
    )


def test_contract_set_refuses_the_same_required_check_twice(tmp_path: Path) -> None:
    """brain rejects a duplicate selector; the file ledger must not accept what brain would
    refuse, or the wall only appears at the switch."""
    repo = _repo(tmp_path)
    spec = "check_run:red-rail/review@red-rail-reviewer"
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
            "--required-check",
            spec,
            "--required-check",
            spec,
        ],
    )
    assert out.exit_code == 1 and "duplicate required check selector" in out.output
    assert not list((repo / RECEIPTS_DIR).glob("*.json"))
