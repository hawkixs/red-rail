"""`rail check [STAGE]`: the gates of the declared tier (or one stage), exit code = verdict."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from rail.commands._options import json_option, repo_option
from rail.gates import GateResult, Stage, run_gates
from rail.model import try_load_rail_config
from rail.policy import applicable_stages, declared_tier

VERDICTS = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "exception": "EXC ", "need": "NEED"}
# CI skips the review on the brain ledger; a green job that does not say so reads as a
# reviewed change (red-alerts#2 merged an unreviewed head on exactly that green)
REVIEW_GATE = "review.verdict"
REVIEW_ELSEWHERE = (
    "the independent reviewer judges the pull request on the host, and this run proves nothing "
    "about it"
)


def _verdict(result: GateResult) -> str:
    if result.skipped:
        return VERDICTS["skip"]
    if result.exception:
        return VERDICTS["exception"]
    if result.needs:
        return VERDICTS["need"]
    return VERDICTS["pass"] if result.passed else VERDICTS["fail"]


def project_name(repo: Path) -> str:
    """The manifest's project, else the directory name (the manifest gate reports the rest)."""
    cfg = try_load_rail_config(repo)
    return cfg.project if cfg else repo.absolute().name


def report(
    repo: Path, results: list[GateResult], stages: list[Stage], *, ci: bool
) -> dict[str, object]:
    tier = declared_tier(repo)
    return {
        "repo": str(repo),
        "project": project_name(repo),
        "tier": tier.value if tier else "bootstrap",
        "declared": tier is not None,
        "ci": ci,
        "stages": [s.value for s in stages],
        "passed": all(r.passed for r in results),
        "not_evaluated": [r.gate_id for r in results if r.skipped],
        "gates": [r.to_dict() for r in results],
        "needs_declaration": [r.gate_id for r in results if r.needs],
    }


@click.command("check")
@click.argument("stage", required=False, type=click.Choice([s.value for s in Stage]))
@repo_option
@json_option
@click.option(
    "--ci", is_flag=True, help="CI checkout: workstation-only gates are skipped, visibly."
)
@click.option("--all", "everything", is_flag=True, help="Run every stage regardless of the tier.")
def command(stage: str | None, repo: Path, as_json: bool, ci: bool, everything: bool) -> None:
    """Run the gates against a repository and exit non-zero if one fails."""
    scope = list(Stage) if everything else list(applicable_stages(repo))
    if stage is not None:
        wanted = Stage(stage)
        if wanted not in scope:
            tier = declared_tier(repo)
            raise click.UsageError(
                f"stage {stage} is not applicable at tier {(tier or 'bootstrap')}; "
                "use --all to force it"
            )
        scope = [wanted]
    results = run_gates(repo, stages=scope, ci=ci)
    payload = report(repo, results, scope, ci=ci)
    if as_json:
        click.echo(json.dumps(payload, indent=2))
    else:
        declared = "" if payload["declared"] else " (undeclared)"
        click.echo(
            f"{payload['project']}  tier={payload['tier']}{declared}  "
            f"stages={','.join(payload['stages'])}"
        )
        for r in results:
            click.echo(f"{_verdict(r)}  {r.gate_id:<22} {r.details}")
        counted = [r for r in results if not r.skipped]
        summary = f"passed {sum(r.passed for r in counted)}/{len(counted)}"
        suffixes = [
            f"{label}: {', '.join(ids)}"
            for label, ids in (
                ("not evaluated here", payload["not_evaluated"]),
                ("needs a declaration", payload["needs_declaration"]),
            )
            if ids
        ]
        click.echo(f"{summary} — {'; '.join(suffixes)}" if suffixes else summary)
        if REVIEW_GATE in payload["not_evaluated"]:
            click.echo(f"review not evaluated here: {REVIEW_ELSEWHERE}")
            if os.environ.get("GITHUB_ACTIONS") == "true":
                # a workflow command: GitHub shows it as an annotation on the pull request
                click.echo(f"::notice title=Review not evaluated here::{REVIEW_ELSEWHERE}")
    raise SystemExit(0 if payload["passed"] else 1)
