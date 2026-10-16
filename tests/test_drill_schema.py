"""Schema-aware drill preflight and restore evidence, without git or remote processes."""

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from rail.cli import main
from rail.commands import drill as drill_command
from rail.deploy import Artefact, LiveVersion, flow
from rail.deploy.remote import RemoteTarget
from rail.gates import evidence
from rail.ledger import AttestationKind as Kind
from rail.ledger.file import FileLedger
from rail.metrics import delivery_window, delivery_window_v2
from rail.model import load_rail_config

T0 = datetime(2026, 10, 1, tzinfo=UTC)
D1, D2 = "sha256:" + "1" * 64, "sha256:" + "2" * 64
S1, S2 = "a" * 40, "b" * 40


class SchemaTarget:
    domain = "example.invalid"
    file_at = RemoteTarget.file_at

    def __init__(self, repo: Path, files: dict[str, bytes | None]):
        self.repo = repo
        self.files = files
        self.reads: list[tuple[str, str]] = []
        self.applied: list[str] = []
        self.built: list[str] = []

    def _run(self, argv, **kwargs):
        assert argv[:4] == ["git", "-C", str(self.repo), "show"]
        sha, path = argv[4].split(":", 1)
        self.reads.append((sha, path))
        content = self.files[sha]
        return subprocess.CompletedProcess(argv, int(content is None), content or b"", b"")

    def steps(self, artefact: Artefact):
        self.built.append(artefact.digest)
        return []

    def apply(self, artefact: Artefact):
        self.applied.append(artefact.digest)
        return LiveVersion("red-fixture", artefact.version, artefact.sha, artefact.digest)

    def redact(self, text: str) -> str:
        return text


@pytest.fixture
def drill_setup(tmp_path, monkeypatch):
    manifest = tmp_path / "rail.yaml"
    manifest.write_text(
        "rail: 1\nproject: red-fixture\nbrain_key: red-fixture\ntier: prod\nstack: python\n"
        "deploy: {target: vps-traefik, healthcheck: 'https://example.invalid/healthz'}\n"
    )
    ticks = iter(T0 - timedelta(seconds=i) for i in (3, 2, 1, 0))
    ledger = FileLedger(tmp_path / "receipts", clock=lambda: next(ticks))
    for version, digest, sha in (("0.7.0", D1, S1), ("0.7.1", D2, S2)):
        data = {"version": version, "digest": digest, "sha": sha, "image": f"example/app@{digest}"}
        ledger.attest("red-fixture", Kind.RELEASED, data, issuer="op", idempotency_key=version)
        # Preflight must use the RELEASED sha, not this deployment's sha or the worktree.
        ledger.attest(
            "red-fixture",
            Kind.DEPLOYED,
            {**data, "sha": "c" * 40, "mode": "release"},
            issuer="op",
            idempotency_key="deployed:" + version,
        )
    target = SchemaTarget(tmp_path, {S1: b'{"head":"067"}', S2: b'{"head":"068"}'})
    monkeypatch.setattr(flow, "make_target", lambda *args: target)
    monkeypatch.setattr(drill_command, "open_ledger", lambda repo: ledger)
    monkeypatch.setattr(evidence, "open_ledger", lambda repo: ledger)
    return tmp_path, manifest, ledger, target


def declare_schema(manifest: Path):
    manifest.write_text(
        manifest.read_text() + "drill:\n  schema_head: {path: schema.json, field: head}\n"
    )


@pytest.mark.parametrize("as_json", [False, True])
def test_schema_mismatch_refuses_before_build_apply_or_attestation(drill_setup, as_json):
    repo, manifest, ledger, target = drill_setup
    declare_schema(manifest)
    before = ledger.list("red-fixture")
    result = CliRunner().invoke(
        main, ["drill", "--repo", str(repo), "--yes"] + (["--json"] if as_json else [])
    )
    assert result.exit_code == 1, result.output
    for value in ("0.7.1", "068", "0.7.0", "067", "rail drill --restore"):
        assert value in result.output
    if as_json:
        data = json.loads(result.output)
        assert data["refused"] is True
        assert data["reason"] == "schema_head_mismatch"
    assert target.reads == [(S2, "schema.json"), (S1, "schema.json")]
    assert target.built == target.applied == []
    assert ledger.list("red-fixture") == before


@pytest.mark.parametrize("sha", [S1, S2])
@pytest.mark.parametrize("content", [None, b"{}", b"not JSON", b"\xff", b"[]", b'{"head":null}'])
def test_unknown_schema_head_refuses_without_side_effects(drill_setup, sha, content):
    repo, manifest, ledger, target = drill_setup
    declare_schema(manifest)
    target.files[sha] = content
    before = ledger.list("red-fixture")
    result = CliRunner().invoke(main, ["drill", "--repo", str(repo), "--yes", "--json"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["refused"] is True and data["reason"] == "schema_head_unknown"
    assert "schema.json" in result.output and "head" in result.output
    assert ("0.7.0" if sha == S1 else "0.7.1") in result.output
    assert target.built == target.applied == []
    assert ledger.list("red-fixture") == before


@pytest.mark.parametrize("declared", [False, True])
def test_drill_keeps_swap_when_schema_equal_or_undeclared(drill_setup, declared):
    repo, manifest, ledger, target = drill_setup
    if declared:
        declare_schema(manifest)
        target.files[S1] = target.files[S2]
    result = CliRunner().invoke(main, ["drill", "--repo", str(repo), "--yes"])
    assert result.exit_code == 0, result.output
    assert target.applied == [D1, D2]
    assert len(target.reads) == (2 if declared else 0)
    assert evidence.drill(repo).passed
    assert "rollback drill" in evidence.drill(repo).details


@pytest.mark.parametrize("path", ["/schema.json", "../schema.json", "a/../schema.json", ""])
def test_schema_path_must_be_relative_and_inside_repository(drill_setup, path):
    repo, manifest, _, _ = drill_setup
    manifest.write_text(
        manifest.read_text()
        + "drill:\n  schema_head: "
        + json.dumps({"path": path, "field": "head"})
    )
    with pytest.raises(ValidationError, match="relative path"):
        load_rail_config(repo)


def restore_payload():
    return {
        "drill": True,
        "restore": True,
        "artefact_digest": D1,
        "backup_sha256": "d" * 64,
        "recovery_seconds": 12,
    }


def attest_restore(ledger, payload, when):
    ledger.attest(
        "red-fixture",
        Kind.RESTORED,
        payload,
        issuer="op",
        idempotency_key="restore",
        emitted_at=when,
    )


def test_complete_restore_drill_satisfies_gate_without_rollback(drill_setup):
    repo, _, ledger, _ = drill_setup
    attest_restore(ledger, restore_payload(), T0 + timedelta(seconds=1))
    result = evidence.drill(repo)
    assert result.passed, result.details
    assert "restore drill" in result.details


@pytest.mark.parametrize("seconds", [-1, 0])
def test_restore_drill_must_be_strictly_after_latest_release_deployment(drill_setup, seconds):
    repo, _, ledger, _ = drill_setup
    attest_restore(ledger, restore_payload(), T0 + timedelta(seconds=seconds))
    assert not evidence.drill(repo).passed


@pytest.mark.parametrize("missing", ["artefact_digest", "backup_sha256", "recovery_seconds"])
def test_incomplete_restore_drill_names_missing_field_even_after_rollback(drill_setup, missing):
    repo, _, ledger, _ = drill_setup
    ledger.attest(
        "red-fixture",
        Kind.ROLLED_BACK,
        {"drill": True},
        issuer="op",
        idempotency_key="rollback",
        emitted_at=T0 + timedelta(seconds=1),
    )
    payload = restore_payload()
    del payload[missing]
    attest_restore(ledger, payload, T0 + timedelta(seconds=2))
    result = evidence.drill(repo)
    assert not result.passed
    assert missing in result.details and "missing" in result.details


@pytest.mark.parametrize("seconds", [0, -1, True, "12", None])
def test_restore_drill_requires_positive_numeric_recovery(drill_setup, seconds):
    repo, _, ledger, _ = drill_setup
    attest_restore(
        ledger, {**restore_payload(), "recovery_seconds": seconds}, T0 + timedelta(seconds=1)
    )
    result = evidence.drill(repo)
    assert not result.passed and "recovery_seconds" in result.details


@pytest.mark.parametrize("field", ["artefact_digest", "backup_sha256"])
@pytest.mark.parametrize("value", [False, [], 12])
def test_restore_drill_requires_named_artefact_and_backup(drill_setup, field, value):
    repo, _, ledger, _ = drill_setup
    attest_restore(ledger, {**restore_payload(), field: value}, T0 + timedelta(seconds=1))
    result = evidence.drill(repo)
    assert not result.passed and field in result.details


@pytest.mark.parametrize("compute", [delivery_window, delivery_window_v2])
def test_metrics_ignore_restore_drill_as_real_recovery(drill_setup, compute):
    repo, _, ledger, _ = drill_setup
    ledger.attest(
        "red-fixture",
        Kind.INCIDENT_DETECTED,
        {"drill": False},
        issuer="op",
        idempotency_key="incident",
        emitted_at=T0,
    )
    cfg = load_rail_config(repo)
    before = compute(
        ledger, cfg.project, repo, now=T0 + timedelta(hours=1), window_days=7, config=cfg
    )
    attest_restore(ledger, restore_payload(), T0 + timedelta(seconds=1))
    after = compute(
        ledger, cfg.project, repo, now=T0 + timedelta(hours=1), window_days=7, config=cfg
    )
    assert after == before
