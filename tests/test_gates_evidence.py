"""Stages 5–10 read the ledger: the newest matching attestation must sit on HEAD's history."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from rail import gitrepo
from rail.gates import Stage
from rail.gates.evidence import GATES, deployed, drill, fulfilled, integrated, released, verdict
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from tests.helpers import commit_all, conforming_tree

T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def _ledger(repo: Path) -> FileLedger:
    ticks = [T0 + timedelta(minutes=i) for i in range(100)]
    return FileLedger(repo / RECEIPTS_DIR, clock=lambda: ticks.pop(0))


def _attest(ledger: FileLedger, kind: AttestationKind, key: str, **data: object) -> None:
    ledger.attest("red-beta", kind, dict(data), issuer="op", idempotency_key=key)


def test_registry_covers_stages_5_to_10() -> None:
    assert [(g.stage, g.code) for g in GATES] == [
        (Stage.REVIEW, "verdict"),
        (Stage.INTEGRATE, "receipt"),
        (Stage.RELEASE, "released"),
        (Stage.DEPLOY, "deployed"),
        (Stage.OBSERVE, "drill"),
        (Stage.LEARN, "fulfilled"),
    ]


def test_every_gate_fails_explicitly_without_evidence(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    for gate in (verdict, integrated, released, deployed, drill, fulfilled):
        result = gate(repo)
        assert not result.passed and "no " in result.details, result
    assert "rail.yaml" in verdict(tmp_path).details


def test_verdict_must_be_independent_and_approving_on_history(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    head = gitrepo.head_sha(repo)
    ledger = _ledger(repo)
    _attest(
        ledger, AttestationKind.REVIEW_VERDICT, "v1", sha=head, independent=False, verdict="approve"
    )
    assert "pre-review" in verdict(repo).details and not verdict(repo).passed
    _attest(
        ledger,
        AttestationKind.REVIEW_VERDICT,
        "v2",
        sha=head,
        independent=True,
        verdict="request_changes",
    )
    assert not verdict(repo).passed
    _attest(
        ledger, AttestationKind.REVIEW_VERDICT, "v3", sha=head, independent=True, verdict="approve"
    )
    result = verdict(repo)
    assert result.passed and "distance 0" in result.details
    commit_all(repo, "feat: more")
    assert "distance 1" in verdict(repo).details
    _attest(
        ledger,
        AttestationKind.REVIEW_VERDICT,
        "v4",
        sha="0" * 40,
        independent=True,
        verdict="approve",
    )
    assert "not on HEAD's history" in verdict(repo).details


def test_integrated_is_the_newest_receipt_on_history(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    ledger = _ledger(repo)
    _attest(ledger, AttestationKind.INTEGRATED, "i1", sha=gitrepo.head_sha(repo))
    assert integrated(repo).passed
    _attest(ledger, AttestationKind.INTEGRATED, "i2", sha="0" * 40)
    assert not integrated(repo).passed


def test_release_deploy_observe_learn_chain(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    head = gitrepo.head_sha(repo)
    ledger = _ledger(repo)
    _attest(ledger, AttestationKind.RELEASED, "r1", sha=head, version="1.0.0", digest="sha256:aaa")
    assert released(repo).passed and "1.0.0" in released(repo).details
    _attest(ledger, AttestationKind.DEPLOYED, "d0", sha=head, digest="sha256:old")
    assert "sha256:aaa" in deployed(repo).details and not deployed(repo).passed
    _attest(ledger, AttestationKind.DEPLOYED, "d1", sha=head, digest="sha256:aaa")
    assert deployed(repo).passed
    assert not drill(repo).passed
    _attest(ledger, AttestationKind.ROLLED_BACK, "rb1", drill=True, digest="sha256:old")
    assert not drill(repo).passed
    _attest(ledger, AttestationKind.RESTORED, "rs1", drill=True, digest="sha256:aaa")
    assert drill(repo).passed
    assert not fulfilled(repo).passed
    _attest(ledger, AttestationKind.FULFILLED, "f1")
    assert fulfilled(repo).passed


def test_released_without_version_or_digest_is_incomplete(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    _attest(_ledger(repo), AttestationKind.RELEASED, "r1", sha=gitrepo.head_sha(repo))
    result = released(repo)
    assert not result.passed and "version" in result.details
