"""Tier defaults live in red-rail; a manifest only declares overrides, each with a reason."""

from pathlib import Path

from rail.gates import GateResult, GateSpec, Stage, run_gate, run_gates
from rail.model import Tier
from rail.policy import (
    GATE_DEFAULTS,
    applicable_stages,
    declared_tier,
    effective,
    parameter,
    stages_for,
)
from tests.helpers import write_manifest

MINIMAL = "rail: 1\nproject: red-probe\nbrain_key: red-probe\ntier: dev\nstack: python\n"


def _manifest(tmp_path: Path, body: str) -> Path:
    (tmp_path / "rail.yaml").write_text(body)
    return tmp_path


def test_each_tier_includes_the_previous_one() -> None:
    assert set(stages_for(Tier.BOOTSTRAP)) < set(stages_for(Tier.DEV)) < set(stages_for(Tier.PROD))
    assert stages_for(Tier.PROD) == tuple(Stage)


def test_bootstrap_is_hygiene_intent_design() -> None:
    assert stages_for(Tier.BOOTSTRAP) == (Stage.HYGIENE, Stage.INTENT, Stage.DESIGN)


def test_declared_tier_is_none_without_a_readable_manifest(tmp_path: Path) -> None:
    assert declared_tier(tmp_path) is None
    _manifest(tmp_path, "rail: 1\nproject: nope\n")
    assert declared_tier(tmp_path) is None


def test_applicable_stages_fall_back_to_bootstrap(tmp_path: Path) -> None:
    assert applicable_stages(tmp_path) == stages_for(Tier.BOOTSTRAP)
    _manifest(tmp_path, MINIMAL)
    assert applicable_stages(tmp_path) == stages_for(Tier.DEV)


def test_effective_returns_the_default_without_override(tmp_path: Path) -> None:
    _manifest(tmp_path, MINIMAL)
    assert effective(tmp_path, "build.commit_window") == (
        GATE_DEFAULTS["build.commit_window"],
        None,
    )
    assert effective(tmp_path, "review.verdict") == (True, None)


def test_effective_returns_the_override_and_its_reason(tmp_path: Path) -> None:
    _manifest(
        tmp_path,
        MINIMAL + "gates:\n  review.verdict:\n    value: false\n    reason: solo spike\n",
    )
    assert effective(tmp_path, "review.verdict") == (False, "solo spike")


def test_run_gate_turns_a_false_override_into_a_declared_exception(tmp_path: Path) -> None:
    _manifest(
        tmp_path,
        MINIMAL + "gates:\n  review.verdict:\n    value: false\n    reason: solo spike\n",
    )
    spec = GateSpec(
        Stage.REVIEW, "verdict", lambda repo: GateResult(Stage.REVIEW, "verdict", False, "none")
    )
    result = run_gate(spec, tmp_path)
    assert result.passed is True
    assert result.exception == "solo spike"
    assert result.to_dict()["exception"] == "solo spike"


def test_run_gate_skips_workstation_gates_under_ci(tmp_path: Path) -> None:
    spec = GateSpec(
        Stage.HYGIENE,
        "remotes",
        lambda repo: GateResult(Stage.HYGIENE, "remotes", False, "one remote"),
        scope="workstation",
    )
    assert run_gate(spec, tmp_path, ci=True).skipped == "workstation"
    assert run_gate(spec, tmp_path).passed is False


def test_run_gate_reports_a_crash_as_a_failed_gate(tmp_path: Path) -> None:
    def boom(repo: Path) -> GateResult:
        raise RuntimeError("bug in the gate")

    result = run_gate(GateSpec(Stage.BUILD, "tests", boom), tmp_path)
    assert result.passed is False
    assert "RuntimeError" in result.details


def test_review_reviewer_identity_default() -> None:
    from rail.policy import GATE_DEFAULTS

    assert GATE_DEFAULTS["review.reviewer_identity"] == "red-rail-reviewer"


def test_run_gates_filters_by_stage(tmp_path: Path) -> None:
    assert [r.gate_id for r in run_gates(tmp_path, stages=[Stage.DESIGN])] == ["design.spec"]
    assert [r.code for r in run_gates(tmp_path, stages=[Stage.HYGIENE])] == [
        "rail_config",
        "docs_layout",
        "claude_md",
        "task_runner",
        "settings",
        "remotes",
        "roster_entry",
        "receipts",
        "mirrors",
    ]


def test_deploy_and_observe_defaults_are_versioned_here() -> None:
    assert GATE_DEFAULTS["deploy.ssh_host"] == "red-vps"
    assert GATE_DEFAULTS["deploy.stack_root"] == "/opt"
    assert GATE_DEFAULTS["deploy.traefik_network"] == "pls_project_default"
    assert GATE_DEFAULTS["deploy.cert_resolver"] == "letsencrypt"
    assert GATE_DEFAULTS["deploy.image_repository"] == "ghcr.io/hawkixs/{project}"
    assert GATE_DEFAULTS["deploy.platform"] == "linux/amd64"
    assert GATE_DEFAULTS["deploy.healthcheck_timeout_seconds"] == 120
    assert GATE_DEFAULTS["deploy.compose_path"] == "deploy/compose.yaml"
    assert GATE_DEFAULTS["observe.monitor_url"] == "http://10.100.0.2:8081"
    assert GATE_DEFAULTS["observe.monitor_agent"] == "vps"


def test_parameter_expands_the_project_and_honours_a_declared_override(tmp_path: Path) -> None:
    write_manifest(tmp_path, project="red-probe", tier="prod", deploy=True)
    assert parameter(tmp_path, "deploy.image_repository", project="red-probe") == (
        "ghcr.io/hawkixs/red-probe"
    )
    assert parameter(tmp_path, "deploy.healthcheck_timeout_seconds") == 120
    write_manifest(
        tmp_path,
        project="red-probe",
        tier="prod",
        deploy=True,
        gates={"deploy.image_repository": ("registry.example.invalid/{project}", "legacy")},
    )
    assert parameter(tmp_path, "deploy.image_repository", project="red-probe") == (
        "registry.example.invalid/red-probe"
    )
    assert effective(tmp_path, "deploy.image_repository")[1] == "legacy"
