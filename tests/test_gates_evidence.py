"""Stages 5–10 read the ledger: the newest matching attestation must sit on HEAD's history."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rail import gitrepo, monitor
from rail.gates import Stage
from rail.gates.evidence import (
    GATES,
    deployed,
    drill,
    fulfilled,
    integrated,
    released,
    verdict,
    visible,
)
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from rail.monitor import AgentView, Container
from tests.helpers import commit_all, conforming_tree

T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def _ledger(repo: Path) -> FileLedger:
    ticks = [T0 + timedelta(minutes=i) for i in range(100)]
    return FileLedger(repo / RECEIPTS_DIR, clock=lambda: ticks.pop(0))


def _attest(
    ledger: FileLedger, kind: AttestationKind, key: str, *, issuer: str = "op", **data: object
) -> None:
    ledger.attest("red-beta", kind, dict(data), issuer=issuer, idempotency_key=key)


def test_registry_covers_stages_5_to_10() -> None:
    assert [(g.stage, g.code) for g in GATES] == [
        (Stage.REVIEW, "verdict"),
        (Stage.INTEGRATE, "receipt"),
        (Stage.RELEASE, "released"),
        (Stage.DEPLOY, "deployed"),
        (Stage.OBSERVE, "visible"),
        (Stage.OBSERVE, "drill"),
        (Stage.LEARN, "fulfilled"),
    ]


def test_every_gate_fails_explicitly_without_evidence(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    for gate in (verdict, integrated, released, deployed, visible, drill, fulfilled):
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
        ledger,
        AttestationKind.REVIEW_VERDICT,
        "v3",
        sha=head,
        independent=True,
        verdict="approve",
        issuer="red-rail-reviewer",
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
        issuer="red-rail-reviewer",
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


def test_a_record_without_sha_is_reported_as_missing_sha(tmp_path: Path) -> None:
    """Review finding: every history gate names a missing sha instead of 'for ? not on history'."""
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    _attest(_ledger(repo), AttestationKind.INTEGRATED, "i1", note="no sha here")
    result = integrated(repo)
    assert not result.passed and "missing sha" in result.details


def test_released_accepts_a_falsy_but_present_value(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    _attest(
        _ledger(repo),
        AttestationKind.RELEASED,
        "r1",
        sha=gitrepo.head_sha(repo),
        version=0,
        digest="sha256:aaa",
    )
    assert released(repo).passed


def test_deployed_requires_digests_on_both_sides(tmp_path: Path) -> None:
    """Sweep finding: two attestations without digest must not compare equal (None == None)."""
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    head = gitrepo.head_sha(repo)
    ledger = _ledger(repo)
    _attest(ledger, AttestationKind.RELEASED, "r1", sha=head, version="1.0.0")
    _attest(ledger, AttestationKind.DEPLOYED, "d1", sha=head)
    result = deployed(repo)
    assert not result.passed and "digest" in result.details


def test_verdict_must_come_from_the_reviewer_identity(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    head = gitrepo.head_sha(repo)
    ledger = _ledger(repo)
    ledger.attest(
        "red-beta",
        AttestationKind.REVIEW_VERDICT,
        {"sha": head, "independent": True, "verdict": "approve"},
        issuer="operator",
        idempotency_key="v1",
    )
    result = verdict(repo)
    assert not result.passed and "issued by 'operator', not 'red-rail-reviewer'" in result.details
    ledger.attest(
        "red-beta",
        AttestationKind.REVIEW_VERDICT,
        {"sha": head, "independent": True, "verdict": "approve"},
        issuer="red-rail-reviewer",
        idempotency_key="v2",
    )
    assert verdict(repo).passed


def test_reviewer_identity_is_a_declared_exception_like_any_default(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    head = gitrepo.head_sha(repo)
    manifest = (repo / "rail.yaml").read_text()
    (repo / "rail.yaml").write_text(
        manifest + "gates:\n  review.reviewer_identity:\n    value: other-bot\n    reason: pilot\n"
    )
    _ledger(repo).attest(
        "red-beta",
        AttestationKind.REVIEW_VERDICT,
        {"sha": head, "independent": True, "verdict": "approve"},
        issuer="other-bot",
        idempotency_key="v1",
    )
    assert verdict(repo).passed


def test_every_evidence_gate_is_ledger_scoped_except_the_live_check() -> None:
    assert {g.scope for g in GATES if g.code != "visible"} == {"ledger"}
    assert next(g for g in GATES if g.code == "visible").scope == "workstation"


def _agent(*containers: Container, status: str = "up") -> AgentView:
    return AgentView(agent="vps", status=status, last_seen=T0, containers=containers)


def _probe(image: str, state: str = "running") -> Container:
    return Container(name="red-beta-app-1", stack="red-beta", image=image, state=state, health="")


def test_visible_needs_a_running_container_of_the_stack_with_the_deployed_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    head = gitrepo.head_sha(repo)
    assert "no deployed" in visible(repo).details and not visible(repo).passed
    ledger = _ledger(repo)
    _attest(
        ledger,
        AttestationKind.DEPLOYED,
        "d1",
        sha=head,
        digest="sha256:" + "a" * 64,
        target="vps-traefik",
    )
    seen: list[tuple[str, str]] = []

    def fake_read(base_url: str, agent: str, **kwargs: object) -> AgentView:
        seen.append((base_url, agent))
        return fake_read.view  # type: ignore[attr-defined]

    monkeypatch.setattr(monitor, "read_agent", fake_read)
    fake_read.view = _agent()  # type: ignore[attr-defined]
    result = visible(repo)
    assert not result.passed and "no running container of stack red-beta" in result.details
    assert seen[-1] == ("http://10.100.0.2:8081", "vps")
    fake_read.view = _agent(_probe("ghcr.io/hawkixs/red-beta@sha256:" + "b" * 64))  # type: ignore[attr-defined]
    result = visible(repo)
    assert not result.passed and "ledger says sha256:" + "a" * 64 in result.details
    fake_read.view = _agent(_probe("ghcr.io/hawkixs/red-beta@sha256:" + "a" * 64))  # type: ignore[attr-defined]
    result = visible(repo)
    assert result.passed and "digest sha256:" + "a" * 64 + " confirmed" in result.details
    fake_read.view = _agent(_probe("red-beta:dev"))  # type: ignore[attr-defined]
    result = visible(repo)
    assert result.passed and "digest not reported" in result.details
    fake_read.view = _agent(_probe("red-beta:dev"), status="down")  # type: ignore[attr-defined]
    assert not visible(repo).passed and "agent vps is down" in visible(repo).details

    def broken(base_url: str, agent: str, **kwargs: object) -> AgentView:
        raise monitor.MonitorError("HTTP 503")

    monkeypatch.setattr(monitor, "read_agent", broken)
    assert "red-monitor: HTTP 503" in visible(repo).details


def test_drill_and_fulfilled_anchor_on_the_newest_release_deployment(tmp_path: Path) -> None:
    """A drill's roll-forward (`mode: drill`) and a rollback are not new deliveries: the drill
    that followed the last release still counts, and an acceptance before them still holds."""
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    head = gitrepo.head_sha(repo)
    ledger = _ledger(repo)
    _attest(ledger, AttestationKind.RELEASED, "r1", sha=head, version="1.0.0", digest="sha256:b")
    _attest(ledger, AttestationKind.DEPLOYED, "d1", sha=head, digest="sha256:b", target="t")
    _attest(ledger, AttestationKind.FULFILLED, "f1", sha=head)
    _attest(ledger, AttestationKind.INCIDENT_DETECTED, "i1", drill=True, digest="sha256:b")
    _attest(ledger, AttestationKind.ROLLED_BACK, "rb1", drill=True, from_digest="sha256:b")
    _attest(ledger, AttestationKind.RESTORED, "rs1", drill=True, digest="sha256:a")
    _attest(
        ledger,
        AttestationKind.DEPLOYED,
        "d2",
        sha=head,
        digest="sha256:b",
        target="t",
        mode="drill",
    )
    assert drill(repo).passed, drill(repo).details
    assert fulfilled(repo).passed, fulfilled(repo).details
    assert deployed(repo).passed, deployed(repo).details
    _attest(ledger, AttestationKind.DEPLOYED, "d3", sha=head, digest="sha256:c", target="t")
    assert not drill(repo).passed and not fulfilled(repo).passed
    assert not deployed(repo).passed and "differs from released" in deployed(repo).details


def test_visible_names_a_deployed_record_without_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    _attest(_ledger(repo), AttestationKind.DEPLOYED, "d1", sha=gitrepo.head_sha(repo), target="t")
    monkeypatch.setattr(
        monitor,
        "read_agent",
        lambda base_url, agent, **kwargs: _agent(_probe("ghcr.io/x/red-beta@sha256:" + "b" * 64)),
    )
    result = visible(repo)
    assert not result.passed and "carries no digest" in result.details
