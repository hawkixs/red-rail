"""Stages 5–10 read evidence from the ledger. A history gate judges the newest matching
attestation on HEAD's history; the distance in commits is reported so drift is
measured. Phase 3 adds the live check through red-monitor (`observe.visible`, spec §6
step 8) and re-anchors `drill`/`fulfilled` on the newest release deployment, not a
rollback or a drill's roll-forward; phase 1 checks the evidence chain itself."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from rail import gitrepo, monitor
from rail.deploy import DeployError
from rail.deploy.sites import load_site, redact_address, substitute_address
from rail.gates import GateResult, GateSpec, Need, Stage
from rail.ledger import RECEIPTS_DIR, AttestationKind, LedgerError, Record, open_ledger
from rail.ledger.file import FileLedger
from rail.model import (
    ADDRESS_TOKEN,
    SITE_PATTERN,
    Declarations,
    DeployTarget,
    declarations,
    token_is_the_host,
)


def _attestations(repo: Path, kind: AttestationKind) -> list[Record] | str | Need:
    """Chronological attestations of `kind`; the reason they cannot be read; or, with no
    declared project, what the default file ledger holds (spec 2026-09-23): nothing is an
    observed gap, receipts are a `Need` — the rail does not guess whose they are."""
    decl = declarations(repo)
    if isinstance(decl, str):
        return decl
    try:
        if decl.project is None:
            found = FileLedger(repo / RECEIPTS_DIR).list(None, attestation=kind)
            if found:
                return Need(
                    "project",
                    f"{len(found)} {kind.value} receipt(s) in {RECEIPTS_DIR} — `project:` says "
                    "which are this repository's",
                )
            return []
        return open_ledger(repo).list(decl.project, attestation=kind)
    except LedgerError as exc:
        return str(exc)


def _absent(repo: Path, what: str) -> str:
    """`what`, plus where it was looked for when no manifest names the ledger."""
    decl = declarations(repo)
    if isinstance(decl, Declarations) and decl.cfg is None:
        return f"{what} in {RECEIPTS_DIR} (default file ledger)"
    return what


def _on_history(
    stage: Stage,
    code: str,
    repo: Path,
    kind: AttestationKind,
    accept: Callable[[Record], str | None],
) -> tuple[GateResult, Record | None]:
    """Newest `kind` attestation ON HEAD's history, not the newest of the whole ledger: with two
    pull requests in flight, the newest belongs to the other line (ticket b37c1ea7). Walking back
    from the newest, a record whose `sha` is not an ancestor of HEAD is skipped; one without a
    `sha` cannot be placed on any line and fails closed. `accept(record)` then judges the record
    found: a rejection reason or None. Returns the record it judged."""
    records = _attestations(repo, kind)
    if isinstance(records, str):
        return GateResult(stage, code, False, records), None
    if isinstance(records, Need):
        return records.result(stage, code), None
    if not records:
        return GateResult(stage, code, False, _absent(repo, f"no {kind.value} attestation")), None
    for record in reversed(records):
        label = f"{kind.value} {record.digest[:19]}"
        sha = "" if record.data.get("sha") is None else str(record.data["sha"])
        if not sha:
            return GateResult(stage, code, False, f"{label}: missing sha"), record
        distance = gitrepo.distance(repo, sha)
        if distance is None:
            continue
        rejection = accept(record)
        if rejection:
            return GateResult(stage, code, False, f"{label}: {rejection}"), record
        return GateResult(
            stage, code, True, f"{label} for {sha[:12]} at distance {distance}"
        ), record
    newest = records[-1]
    return GateResult(
        stage,
        code,
        False,
        f"{kind.value} {newest.digest[:19]} for {str(newest.data['sha'])[:12]} not on HEAD's "
        f"history — none of the {len(records)} in the ledger is",
    ), newest


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
        if data.get("diff_truncated"):
            # The reviewer records this when it cut the diff at `max_diff_chars`, and says
            # so in the review body too. A verdict that covers part of the change is not a
            # verdict on the change: read literally, it approves what happened to fit.
            return (
                "the reviewer saw only part of the change (diff_truncated) — split the pull "
                "request, or raise `max_diff_chars` in the reviewer's configuration, then "
                "review it again"
            )
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


def _newest(repo: Path, kind: AttestationKind) -> Record | None | str | Need:
    records = _attestations(repo, kind)
    if isinstance(records, str | Need):
        return records
    return records[-1] if records else None


def _newest_release_deploy(repo: Path) -> Record | None | str | Need:
    """The newest `deployed` that is a delivery: a rollback or a drill's roll-forward names the
    live digest but is not a new release (`mode` conventions in `rail.ledger`)."""
    records = _attestations(repo, AttestationKind.DEPLOYED)
    if isinstance(records, str | Need):
        return records
    releases = [r for r in records if (r.data.get("mode") or "release") == "release"]
    return releases[-1] if releases else None


def deployed(repo: Path) -> GateResult:
    release = _newest(repo, AttestationKind.RELEASED)
    if isinstance(release, Need):
        return release.result(Stage.DEPLOY, "deployed")
    if isinstance(release, str):
        return GateResult(Stage.DEPLOY, "deployed", False, release)
    if release is None:
        return GateResult(
            Stage.DEPLOY, "deployed", False, _absent(repo, "no released attestation to deploy")
        )
    deploy = _newest(repo, AttestationKind.DEPLOYED)
    if isinstance(deploy, Need):
        return deploy.result(Stage.DEPLOY, "deployed")
    if isinstance(deploy, str):
        return GateResult(Stage.DEPLOY, "deployed", False, deploy)
    if deploy is None:
        return GateResult(Stage.DEPLOY, "deployed", False, _absent(repo, "no deployed attestation"))
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

    decl = declarations(repo)
    if isinstance(decl, str):
        return GateResult(Stage.OBSERVE, "visible", False, decl)
    deploy = _newest(repo, AttestationKind.DEPLOYED)
    if isinstance(deploy, Need):
        return deploy.result(Stage.OBSERVE, "visible")
    if isinstance(deploy, str):
        return GateResult(Stage.OBSERVE, "visible", False, deploy)
    if deploy is None:
        return GateResult(
            Stage.OBSERVE, "visible", False, _absent(repo, "no deployed attestation to observe")
        )
    project = decl.project
    if project is None:  # unreachable: no project means _newest returned a Need or None
        return Need("project", "it names the stack red-monitor watches").result(
            Stage.OBSERVE, "visible"
        )
    url = str(parameter(repo, "observe.monitor_url"))
    agent = str(parameter(repo, "observe.monitor_agent"))
    site = str(parameter(repo, "observe.monitor_site"))
    address = None
    if ADDRESS_TOKEN in url:
        # the address is a host fact (spec 2026-09-23-sites-on-the-host): without it the gate
        # stays closed and says where to declare it, never guessing one of its own. It fills
        # the URL's host only — anywhere else (a DNS label, a query, userinfo) it would be sent
        # to another machine — and the site is a label, as `deploy.site` is.
        if not token_is_the_host(url):
            return GateResult(
                Stage.OBSERVE,
                "visible",
                False,
                f"observe.monitor_url: {ADDRESS_TOKEN} must be the URL's host (got {url})",
            )
        if not re.fullmatch(SITE_PATTERN, site):
            return GateResult(
                Stage.OBSERVE,
                "visible",
                False,
                f"observe.monitor_site must be a label ({SITE_PATTERN}), got {site!r}",
            )
        try:
            address = load_site(site).address
        except DeployError as exc:
            return GateResult(Stage.OBSERVE, "visible", False, f"red-monitor: {exc}")
        url = substitute_address(url, address)
    try:
        view = monitor.read_agent(url, agent)
    except monitor.MonitorError as exc:
        reason = str(exc) if address is None else redact_address(str(exc), address, site)
        return GateResult(Stage.OBSERVE, "visible", False, f"red-monitor: {reason}")
    if view.status != "up":
        return GateResult(
            Stage.OBSERVE, "visible", False, f"agent {agent} is {view.status or 'unknown'}"
        )
    shape = decl.cfg.deploy if decl.cfg is not None else None
    if (
        shape is not None
        and shape.target is DeployTarget.PRIVATE_SYSTEMD
        and shape.unit is not None
    ):
        return _unit_visible(view, agent, PurePosixPath(shape.unit).name)
    running = [c for c in monitor.stack_containers(view, project) if c.state == "running"]
    if not running:
        return GateResult(
            Stage.OBSERVE,
            "visible",
            False,
            f"no running container of stack {project} on agent {agent}",
        )
    expected = str(deploy.data.get("digest") or "")
    if not expected:
        return GateResult(
            Stage.OBSERVE,
            "visible",
            False,
            "the deployed attestation carries no digest to compare with the running image",
        )
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
        f"{len(running)} running container(s) of {project} on {agent}, {digest}",
    )


def _unit_visible(view: monitor.AgentView, agent: str, unit: str) -> GateResult:
    """A systemd target runs no container: red-monitor's view of the unit is the observation
    (spec 2026-09-24-private-systemd-target, decision 11). The digest was proven at deployment
    by `/version`; this proves the service stayed up."""
    found = monitor.find_unit(view, unit)
    if found is None:
        return GateResult(
            Stage.OBSERVE, "visible", False, f"red-monitor lists no unit {unit} on agent {agent}"
        )
    state = f"{found.active_state}/{found.sub_state}"
    if state != "active/running":
        return GateResult(
            Stage.OBSERVE, "visible", False, f"unit {unit} on agent {agent} is {state}"
        )
    return GateResult(
        Stage.OBSERVE,
        "visible",
        True,
        f"unit {unit} active/running on {agent}; its digest was verified at deployment by /version",
    )


def drill(repo: Path) -> GateResult:
    deploy = _newest_release_deploy(repo)
    if isinstance(deploy, Need):
        return deploy.result(Stage.OBSERVE, "drill")
    if isinstance(deploy, str):
        return GateResult(Stage.OBSERVE, "drill", False, deploy)
    if deploy is None:
        return GateResult(
            Stage.OBSERVE, "drill", False, _absent(repo, "no deployed attestation to drill")
        )
    rollbacks = _attestations(repo, AttestationKind.ROLLED_BACK)
    restores = _attestations(repo, AttestationKind.RESTORED)
    if isinstance(rollbacks, Need):
        return rollbacks.result(Stage.OBSERVE, "drill")
    if isinstance(restores, Need):
        return restores.result(Stage.OBSERVE, "drill")
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
    if isinstance(deploy, Need):
        return deploy.result(Stage.LEARN, "fulfilled")
    if isinstance(deploy, str):
        return GateResult(Stage.LEARN, "fulfilled", False, deploy)
    done = _newest(repo, AttestationKind.FULFILLED)
    if isinstance(done, Need):
        return done.result(Stage.LEARN, "fulfilled")
    if isinstance(done, str):
        return GateResult(Stage.LEARN, "fulfilled", False, done)
    if done is None:
        return GateResult(
            Stage.LEARN, "fulfilled", False, _absent(repo, "no fulfilled attestation")
        )
    if deploy is not None and done.recorded_at < deploy.recorded_at:
        return GateResult(Stage.LEARN, "fulfilled", False, "fulfilled predates the last deployment")
    return GateResult(Stage.LEARN, "fulfilled", True, f"fulfilled {done.digest[:19]}")


def carry_forward(repo: Path) -> GateResult:
    """Every approving code verdict accounted for each carry-forward open before it (spec
    2026-09-25-review-loop-closure, D12). The reviewer enforces this at review time; the gate
    catches a receipt written some other way and a reviewer regression — never raises, even on
    a malformed `carry_forwards` or `findings` field."""
    from pydantic import ValidationError

    from rail.reviewer import rounds

    verdicts = _attestations(repo, AttestationKind.REVIEW_VERDICT)
    if isinstance(verdicts, str):
        return GateResult(Stage.REVIEW, "carry_forward", False, verdicts)
    if isinstance(verdicts, Need):
        return verdicts.result(Stage.REVIEW, "carry_forward")
    rulings = _attestations(repo, AttestationKind.REVIEW_RULING)
    rulings = rulings if isinstance(rulings, list) else []
    # validated up front, so a malformed `carry_forwards` is reported against the receipt
    # that carries it, not against whichever `rounds` call happens to touch it first
    for v in verdicts:
        try:
            rounds.carry_forwards_of(v)
        except (ValidationError, ValueError, TypeError) as exc:
            return GateResult(
                Stage.REVIEW, "carry_forward", False,
                f"malformed carry-forward receipt on {v.data.get('repository')}#"
                f"{v.data.get('pr')}: {exc}",
            )
    try:
        for index, v in enumerate(verdicts):
            data = v.data
            if data.get("artifact") != "code" or data.get("verdict") != "approve":
                continue
            before = [r for r in rulings if r.recorded_at < v.recorded_at]
            open_ = rounds.open_carry_forwards(
                verdicts[:index], before, repository=str(data.get("repository")),
                excluding_pr=data.get("pr"),
            )
            carry = rounds.carry_forwards_of(v)
            accounted = set(carry.addressed) | set(carry.deferred)
            missing = [i for i in open_ if i not in accounted]
            if missing:
                return GateResult(
                    Stage.REVIEW, "carry_forward", False,
                    f"approving verdict on {data.get('repository')}#{data.get('pr')} left "
                    f"{', '.join(missing)} unaccounted",
                )
        if not verdicts:
            return GateResult(Stage.REVIEW, "carry_forward", True, "no carry-forward recorded")
        repositories = sorted({str(v.data.get("repository")) for v in verdicts})
        open_all = [i for repo_slug in repositories
                    for i in rounds.open_carry_forwards(verdicts, rulings, repository=repo_slug)]
    except (ValidationError, ValueError, TypeError) as exc:
        return GateResult(Stage.REVIEW, "carry_forward", False, f"malformed receipt: {exc}")
    if not open_all and not any(
        isinstance(v.data.get("findings"), list)
        and any(f.get("class") == "carry_forward" for f in v.data["findings"])
        for v in verdicts
    ):
        return GateResult(Stage.REVIEW, "carry_forward", True, "no carry-forward recorded")
    more = f" and {len(open_all) - 10} more" if len(open_all) > 10 else ""
    shown = ", ".join(open_all[:10]) + more
    return GateResult(
        Stage.REVIEW, "carry_forward", True,
        f"{len(open_all)} open" + (f": {shown}" if open_all else ""),
    )


GATES = [
    GateSpec(Stage.REVIEW, "verdict", verdict, scope="ledger"),
    GateSpec(Stage.REVIEW, "carry_forward", carry_forward, scope="ledger"),
    GateSpec(Stage.INTEGRATE, "receipt", integrated, scope="ledger"),
    GateSpec(Stage.RELEASE, "released", released, scope="ledger"),
    GateSpec(Stage.DEPLOY, "deployed", deployed, scope="ledger"),
    GateSpec(Stage.OBSERVE, "visible", visible, scope="workstation"),
    GateSpec(Stage.OBSERVE, "drill", drill, scope="ledger"),
    GateSpec(Stage.LEARN, "fulfilled", fulfilled, scope="ledger"),
]
