"""Stages 5–10 read the ledger: a history gate judges the newest matching attestation that
sits on HEAD's history, never one from another line."""

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
from tests.helpers import commit_all, conforming_tree, git

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
    bare = verdict(tmp_path)
    assert "no review_verdict attestation in docs/receipts (default file ledger)" in bare.details
    assert bare.needs is None and "rail.yaml" not in bare.details


def test_without_a_manifest_receipts_need_the_project(tmp_path: Path) -> None:
    FileLedger(tmp_path / RECEIPTS_DIR).attest(
        "red-beta",
        AttestationKind.REVIEW_VERDICT,
        {"sha": "0" * 40},
        issuer="op",
        idempotency_key="v1",
    )
    result = verdict(tmp_path)
    assert not result.passed and result.needs == "project"
    assert "1 review_verdict receipt(s) in docs/receipts" in result.details
    assert "rail.yaml" not in result.details


def test_without_a_manifest_a_deployed_receipt_needs_the_project(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / RECEIPTS_DIR)
    ledger.attest(
        "red-beta",
        AttestationKind.RELEASED,
        {"sha": "0" * 40, "version": "1.0.0", "digest": "sha256:a"},
        issuer="op",
        idempotency_key="r1",
    )
    ledger.attest(
        "red-beta",
        AttestationKind.DEPLOYED,
        {"sha": "0" * 40, "digest": "sha256:a"},
        issuer="op",
        idempotency_key="d1",
    )
    for gate in (deployed, visible, drill, fulfilled):
        result = gate(tmp_path)
        assert not result.passed and result.needs == "project", result
        assert "rail.yaml" not in result.details


def test_without_a_manifest_every_evidence_gate_names_an_observed_gap(tmp_path: Path) -> None:
    for gate in (verdict, integrated, released, deployed, visible, drill, fulfilled):
        result = gate(tmp_path)
        assert not result.passed and result.needs is None, result
        assert "docs/receipts (default file ledger)" in result.details, result
        assert "rail.yaml" not in result.details, result


def test_an_invalid_manifest_declaring_brain_never_reads_the_receipts(tmp_path: Path) -> None:
    """Review focus 1: the receipts of a brain ledger are mirrors; an invalid manifest must not
    turn them into the authority."""
    FileLedger(tmp_path / RECEIPTS_DIR).attest(
        "red-beta",
        AttestationKind.REVIEW_VERDICT,
        {"sha": "0" * 40},
        issuer="op",
        idempotency_key="v1",
    )
    (tmp_path / "rail.yaml").write_text("rail: 1\nproject: nope\nledger: brain\n")
    for gate in (verdict, integrated, visible):
        result = gate(tmp_path)
        assert not result.passed and result.needs is None
        assert result.details.startswith("rail.yaml is invalid: "), result


def test_a_tampered_receipt_without_a_manifest_is_a_failure_not_a_need(tmp_path: Path) -> None:
    """Review focus 2."""
    ledger = FileLedger(tmp_path / RECEIPTS_DIR)
    ledger.attest(
        "red-beta", AttestationKind.INTEGRATED, {"sha": "0" * 40}, issuer="op", idempotency_key="i1"
    )
    receipt = next((tmp_path / RECEIPTS_DIR).glob("*.json"))
    receipt.write_text(receipt.read_text().replace("0" * 40, "1" * 40))
    result = integrated(tmp_path)
    assert not result.passed and result.needs is None and "tampered" in result.details


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


def _commit_on_another_line(repo: Path) -> str:
    """A sibling of HEAD — a pull request still in flight — made without touching the working
    tree, where the file ledger lives: a branch switch would take the receipts with it."""
    return git(repo, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "feat: another line")


def _approve(ledger: FileLedger, key: str, sha: str, decision: str = "approve") -> None:
    _attest(
        ledger,
        AttestationKind.REVIEW_VERDICT,
        key,
        sha=sha,
        independent=True,
        verdict=decision,
        issuer="red-rail-reviewer",
    )


def test_a_verdict_on_another_line_does_not_mask_the_one_on_head(tmp_path: Path) -> None:
    """Ticket b37c1ea7: reviewing another pull request turned main red, because the gate judged
    the newest verdict of the whole ledger instead of the newest one on HEAD's history."""
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    head = gitrepo.head_sha(repo)
    ledger = _ledger(repo)
    _approve(ledger, "v1", head)
    other = _commit_on_another_line(repo)
    _approve(ledger, "v2", other, decision="request_changes")
    _approve(ledger, "v3", other)
    result = verdict(repo)
    assert result.passed, result
    assert f"for {head[:12]} at distance 0" in result.details


def test_a_newer_verdict_on_heads_line_still_wins(tmp_path: Path) -> None:
    """The reverse direction: request_changes on HEAD's line beats an older approve, and a
    newer approve on another line does not rescue it."""
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    ledger = _ledger(repo)
    _approve(ledger, "v1", gitrepo.head_sha(repo))
    head = commit_all(repo, "feat: more")
    _approve(ledger, "v2", head, decision="request_changes")
    _approve(ledger, "v3", _commit_on_another_line(repo))
    result = verdict(repo)
    assert not result.passed and "request_changes" in result.details, result


def test_no_attestation_on_heads_line_fails(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    ledger = _ledger(repo)
    other = _commit_on_another_line(repo)
    _approve(ledger, "v1", other)
    _attest(ledger, AttestationKind.INTEGRATED, "i1", sha=other)
    for gate in (verdict, integrated):
        result = gate(repo)
        assert not result.passed, result
        assert f"for {other[:12]} not on HEAD's history" in result.details


def test_integrated_is_the_newest_receipt_on_history(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    ledger = _ledger(repo)
    head = gitrepo.head_sha(repo)
    _attest(ledger, AttestationKind.INTEGRATED, "i1", sha=head)
    assert integrated(repo).passed
    _attest(ledger, AttestationKind.INTEGRATED, "i2", sha=_commit_on_another_line(repo))
    result = integrated(repo)
    assert result.passed and f"for {head[:12]}" in result.details, result


def test_a_record_without_sha_fails_closed_even_over_an_older_one_on_history(
    tmp_path: Path,
) -> None:
    """A record that names no commit cannot be placed on a line, so it is never skipped."""
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    ledger = _ledger(repo)
    _attest(ledger, AttestationKind.INTEGRATED, "i1", sha=gitrepo.head_sha(repo))
    _attest(ledger, AttestationKind.INTEGRATED, "i2", note="no sha here")
    result = integrated(repo)
    assert not result.passed and "missing sha" in result.details, result


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


MONITOR = "192.0.2.2"  # RFC 5737: red-monitor's real address lives only in the host's file


def _sites(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str | None) -> Path:
    """Point the rail at a private sites file in `tmp_path`; `None` leaves it absent, so a
    developer's own `~/.config/red-rail/sites.yaml` can never leak into a test."""
    path = tmp_path / "host" / "sites.yaml"
    monkeypatch.setenv("RAIL_SITES_FILE", str(path))
    if text is not None:
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        path.chmod(0o600)
    return path


def _deployed_tree(tmp_path: Path) -> Path:
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    _attest(
        _ledger(repo),
        AttestationKind.DEPLOYED,
        "d1",
        sha=gitrepo.head_sha(repo),
        digest="sha256:" + "a" * 64,
        target="vps-traefik",
    )
    return repo


def test_visible_needs_a_running_container_of_the_stack_with_the_deployed_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sites(monkeypatch, tmp_path, f'sites:\n  red-monitor:\n    address: "{MONITOR}"\n')
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
    assert seen[-1] == (f"http://{MONITOR}:8081", "vps")
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


def _unreachable(base_url: str, agent: str, **kwargs: object) -> AgentView:
    raise AssertionError(f"red-monitor must not be queried, got {base_url}")


def test_visible_fails_closed_naming_the_file_when_the_host_declares_no_monitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The monitor's address is a host fact: without it the gate says where to declare it,
    and never falls back to an address of its own."""
    path = _sites(monkeypatch, tmp_path, None)
    repo = _deployed_tree(tmp_path)
    monkeypatch.setattr(monitor, "read_agent", _unreachable)
    result = visible(repo)
    assert not result.passed
    assert "site red-monitor" in result.details and str(path) in result.details


def test_visible_lists_the_known_sites_when_red_monitor_is_not_one_of_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sites(monkeypatch, tmp_path, 'sites:\n  red-base:\n    address: "192.0.2.4"\n')
    repo = _deployed_tree(tmp_path)
    monkeypatch.setattr(monitor, "read_agent", _unreachable)
    result = visible(repo)
    assert not result.passed and "known: red-base" in result.details


def test_visible_never_prints_the_monitor_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable monitor's error names its URL; the gate's text names the site instead,
    because `rail check` output is pasted into issues and pull requests."""
    _sites(monkeypatch, tmp_path, f'sites:\n  red-monitor:\n    address: "{MONITOR}"\n')
    repo = _deployed_tree(tmp_path)

    def refused(base_url: str, agent: str, **kwargs: object) -> AgentView:
        raise monitor.MonitorError(f"{base_url}/api/latest: HTTP 503")

    monkeypatch.setattr(monitor, "read_agent", refused)
    details = visible(repo).details
    assert "red-monitor:8081/api/latest: HTTP 503" in details
    assert MONITOR not in details


def test_a_literal_monitor_url_in_the_manifest_needs_no_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sites(monkeypatch, tmp_path, None)
    repo = _deployed_tree(tmp_path)
    manifest = (repo / "rail.yaml").read_text()
    (repo / "rail.yaml").write_text(
        manifest
        + "gates:\n  observe.monitor_url:\n    value: http://192.0.2.9:8081\n"
        + "    reason: this project watches another monitor\n"
    )
    seen: list[str] = []

    def fake_read(base_url: str, agent: str, **kwargs: object) -> AgentView:
        seen.append(base_url)
        return _agent()

    monkeypatch.setattr(monitor, "read_agent", fake_read)
    assert "no running container" in visible(repo).details
    assert seen == ["http://192.0.2.9:8081"]


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
    _sites(monkeypatch, tmp_path, f'sites:\n  red-monitor:\n    address: "{MONITOR}"\n')
    repo = conforming_tree(tmp_path, "red-beta", "prod")
    _attest(_ledger(repo), AttestationKind.DEPLOYED, "d1", sha=gitrepo.head_sha(repo), target="t")
    monkeypatch.setattr(
        monitor,
        "read_agent",
        lambda base_url, agent, **kwargs: _agent(_probe("ghcr.io/x/red-beta@sha256:" + "b" * 64)),
    )
    result = visible(repo)
    assert not result.passed and "carries no digest" in result.details


def test_verdict_refuses_a_review_that_did_not_see_the_whole_change(tmp_path: Path) -> None:
    """The reviewer truncates a diff past `max_diff_chars`, records `diff_truncated: true`
    and even writes it into the review body — and the gate read none of it. Measured on the
    first external pull request: `verdict: approve, diff_truncated: true` on a Python→Go
    rewrite, with the reviewer's own summary saying the Go code was not visible. The gate
    that carries "verifiable, not declarative" was satisfied by a verdict that declares it
    did not read what it was asked to read.

    The field exists because truncation is a fact that counts. If it were not meant to
    affect the verdict it would not need to be in the receipt."""
    repo = conforming_tree(tmp_path, "red-beta", "dev")
    head = gitrepo.head_sha(repo)
    ledger = _ledger(repo)
    ledger.attest(
        "red-beta",
        AttestationKind.REVIEW_VERDICT,
        {"sha": head, "independent": True, "verdict": "approve", "diff_truncated": True},
        issuer="red-rail-reviewer",
        idempotency_key="v-truncated",
    )
    result = verdict(repo)
    assert not result.passed, result.details
    assert "truncated" in result.details
    assert "part of the change" in result.details or "did not see" in result.details

    # the same verdict on a whole diff is what the gate is for
    ledger.attest(
        "red-beta",
        AttestationKind.REVIEW_VERDICT,
        {"sha": head, "independent": True, "verdict": "approve", "diff_truncated": False},
        issuer="red-rail-reviewer",
        idempotency_key="v-whole",
    )
    assert verdict(repo).passed, verdict(repo).details
