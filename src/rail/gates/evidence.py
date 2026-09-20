"""Stages 5–10 read evidence from the ledger. A gate passes when the newest matching
attestation is on HEAD's history; the distance in commits is reported so drift is
measured. Phase 3 adds the live check through red-monitor (`observe.visible`, spec §6
step 8) and re-anchors `drill`/`fulfilled` on the newest release deployment, not a
rollback or a drill's roll-forward; phase 1 checks the evidence chain itself."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from rail import gitrepo, monitor
from rail.gates import GateResult, GateSpec, Stage
from rail.ledger import AttestationKind, LedgerError, Record, open_ledger
from rail.model import MANIFEST_NAME, load_rail_config, manifest_problem, try_load_rail_config


def _attestations(repo: Path, kind: AttestationKind) -> list[Record] | str:
    """Chronological attestations of `kind`, or the reason they cannot be read."""
    try:
        cfg = load_rail_config(repo)
        return open_ledger(repo).list(cfg.project, attestation=kind)
    except (FileNotFoundError, ValidationError):
        return manifest_problem(repo) or f"{MANIFEST_NAME} unreadable"
    except LedgerError as exc:
        return str(exc)


def _on_history(
    stage: Stage,
    code: str,
    repo: Path,
    kind: AttestationKind,
    accept: Callable[[Record], str | None],
) -> tuple[GateResult, Record | None]:
    """Newest `kind` attestation: `accept(record)` returns a rejection reason or None; then its
    `sha` must be present and an ancestor of HEAD. Returns the record it judged."""
    records = _attestations(repo, kind)
    if isinstance(records, str):
        return GateResult(stage, code, False, records), None
    if not records:
        return GateResult(stage, code, False, f"no {kind.value} attestation"), None
    newest = records[-1]
    data = newest.data
    label = f"{kind.value} {newest.digest[:19]}"
    rejection = accept(newest)
    if rejection:
        return GateResult(stage, code, False, f"{label}: {rejection}"), newest
    sha = "" if data.get("sha") is None else str(data["sha"])
    if not sha:
        return GateResult(stage, code, False, f"{label}: missing sha"), newest
    distance = gitrepo.distance(repo, sha)
    if distance is None:
        return GateResult(
            stage, code, False, f"{label} for {sha[:12]} not on HEAD's history"
        ), newest
    return GateResult(stage, code, True, f"{label} for {sha[:12]} at distance {distance}"), newest


def verdict(repo: Path) -> GateResult:
    from rail.policy import effective

    expected, _ = effective(repo, "review.reviewer_identity")

    def accept(record: Record) -> str | None:
        data = record.data
        if not data.get("independent"):
            return "pre-review from the producing session, not an independent verdict"
        if data.get("verdict") != "approve":
            return f"verdict is {data.get('verdict')!r}"
        if record.issuer != expected:
            return f"issued by {record.issuer!r}, not {expected!r}"
        return None

    return _on_history(Stage.REVIEW, "verdict", repo, AttestationKind.REVIEW_VERDICT, accept)[0]


def integrated(repo: Path) -> GateResult:
    return _on_history(
        Stage.INTEGRATE, "receipt", repo, AttestationKind.INTEGRATED, lambda r: None
    )[0]


def released(repo: Path) -> GateResult:
    def accept(record: Record) -> str | None:
        data = record.data
        missing = [k for k in ("version", "digest") if data.get(k) in (None, "")]
        return f"missing {', '.join(missing)}" if missing else None

    result, record = _on_history(Stage.RELEASE, "released", repo, AttestationKind.RELEASED, accept)
    if result.passed and record is not None:
        return GateResult(
            result.stage, result.code, True, f"{result.details}, version {record.data['version']}"
        )
    return result


def _newest(repo: Path, kind: AttestationKind) -> Record | None | str:
    records = _attestations(repo, kind)
    if isinstance(records, str):
        return records
    return records[-1] if records else None


def _newest_release_deploy(repo: Path) -> Record | None | str:
    """The newest `deployed` that is a delivery: a rollback or a drill's roll-forward names the
    live digest but is not a new release (`mode` conventions in `rail.ledger`)."""
    records = _attestations(repo, AttestationKind.DEPLOYED)
    if isinstance(records, str):
        return records
    releases = [r for r in records if (r.data.get("mode") or "release") == "release"]
    return releases[-1] if releases else None


def deployed(repo: Path) -> GateResult:
    release = _newest(repo, AttestationKind.RELEASED)
    if isinstance(release, str):
        return GateResult(Stage.DEPLOY, "deployed", False, release)
    if release is None:
        return GateResult(Stage.DEPLOY, "deployed", False, "no released attestation to deploy")
    deploy = _newest(repo, AttestationKind.DEPLOYED)
    if isinstance(deploy, str):
        return GateResult(Stage.DEPLOY, "deployed", False, deploy)
    if deploy is None:
        return GateResult(Stage.DEPLOY, "deployed", False, "no deployed attestation")
    expected = release.data.get("digest")
    actual = deploy.data.get("digest")
    if expected in (None, "") or actual in (None, ""):
        side = "released" if expected in (None, "") else "deployed"
        return GateResult(
            Stage.DEPLOY, "deployed", False, f"the {side} attestation carries no digest to compare"
        )
    if actual != expected:
        return GateResult(
            Stage.DEPLOY,
            "deployed",
            False,
            f"deployed digest {actual} differs from released {expected}",
        )
    return GateResult(Stage.DEPLOY, "deployed", True, f"deployed {expected} ({deploy.digest[:19]})")


def visible(repo: Path) -> GateResult:
    """red-monitor sees the stack on the target's agent (spec §6 step 8); when the image
    reference is digest-pinned it must be the digest the ledger says is live."""
    from rail.policy import parameter

    cfg = try_load_rail_config(repo)
    if cfg is None:
        return GateResult(
            Stage.OBSERVE, "visible", False, manifest_problem(repo) or f"{MANIFEST_NAME} unreadable"
        )
    deploy = _newest(repo, AttestationKind.DEPLOYED)
    if isinstance(deploy, str):
        return GateResult(Stage.OBSERVE, "visible", False, deploy)
    if deploy is None:
        return GateResult(Stage.OBSERVE, "visible", False, "no deployed attestation to observe")
    url = str(parameter(repo, "observe.monitor_url"))
    agent = str(parameter(repo, "observe.monitor_agent"))
    try:
        view = monitor.read_agent(url, agent)
    except monitor.MonitorError as exc:
        return GateResult(Stage.OBSERVE, "visible", False, f"red-monitor: {exc}")
    if view.status != "up":
        return GateResult(
            Stage.OBSERVE, "visible", False, f"agent {agent} is {view.status or 'unknown'}"
        )
    running = [c for c in monitor.stack_containers(view, cfg.project) if c.state == "running"]
    if not running:
        return GateResult(
            Stage.OBSERVE,
            "visible",
            False,
            f"no running container of stack {cfg.project} on agent {agent}",
        )
    expected = str(deploy.data.get("digest") or "")
    seen = {d for d in (monitor.image_digest(c.image) for c in running) if d}
    if seen and expected not in seen:
        return GateResult(
            Stage.OBSERVE,
            "visible",
            False,
            f"red-monitor sees {', '.join(sorted(seen))} on {agent}, ledger says {expected}",
        )
    digest = (
        f"image digest {expected} confirmed" if expected in seen else "image digest not reported"
    )
    return GateResult(
        Stage.OBSERVE,
        "visible",
        True,
        f"{len(running)} running container(s) of {cfg.project} on {agent}, {digest}",
    )


def drill(repo: Path) -> GateResult:
    deploy = _newest_release_deploy(repo)
    if isinstance(deploy, str):
        return GateResult(Stage.OBSERVE, "drill", False, deploy)
    if deploy is None:
        return GateResult(Stage.OBSERVE, "drill", False, "no deployed attestation to drill")
    rollbacks = _attestations(repo, AttestationKind.ROLLED_BACK)
    restores = _attestations(repo, AttestationKind.RESTORED)
    if isinstance(rollbacks, str) or isinstance(restores, str):
        return GateResult(Stage.OBSERVE, "drill", False, "ledger unreadable")
    after = [r for r in rollbacks if r.data.get("drill") and r.recorded_at > deploy.recorded_at]
    if not after:
        return GateResult(
            Stage.OBSERVE, "drill", False, "no rollback drill after the last deployment"
        )
    restored = [
        r for r in restores if r.data.get("drill") and r.recorded_at > after[-1].recorded_at
    ]
    if not restored:
        return GateResult(
            Stage.OBSERVE, "drill", False, "no restored attestation after the drill's rollback"
        )
    return GateResult(
        Stage.OBSERVE, "drill", True, f"drill {after[-1].digest[:19]} → {restored[-1].digest[:19]}"
    )


def fulfilled(repo: Path) -> GateResult:
    deploy = _newest_release_deploy(repo)
    if isinstance(deploy, str):
        return GateResult(Stage.LEARN, "fulfilled", False, deploy)
    done = _newest(repo, AttestationKind.FULFILLED)
    if isinstance(done, str):
        return GateResult(Stage.LEARN, "fulfilled", False, done)
    if done is None:
        return GateResult(Stage.LEARN, "fulfilled", False, "no fulfilled attestation")
    if deploy is not None and done.recorded_at < deploy.recorded_at:
        return GateResult(Stage.LEARN, "fulfilled", False, "fulfilled predates the last deployment")
    return GateResult(Stage.LEARN, "fulfilled", True, f"fulfilled {done.digest[:19]}")


GATES = [
    GateSpec(Stage.REVIEW, "verdict", verdict, scope="ledger"),
    GateSpec(Stage.INTEGRATE, "receipt", integrated, scope="ledger"),
    GateSpec(Stage.RELEASE, "released", released, scope="ledger"),
    GateSpec(Stage.DEPLOY, "deployed", deployed, scope="ledger"),
    GateSpec(Stage.OBSERVE, "visible", visible, scope="workstation"),
    GateSpec(Stage.OBSERVE, "drill", drill, scope="ledger"),
    GateSpec(Stage.LEARN, "fulfilled", fulfilled, scope="ledger"),
]
