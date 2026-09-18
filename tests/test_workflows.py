"""The workflows are data: callable, pinned, and they run the same gates as the workstation."""

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def _load(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    data["on"] = data.pop(True, data.get("on"))  # PyYAML reads the bare key `on` as boolean True
    return data


def _steps(workflow: dict) -> list[dict]:
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def test_rail_ci_is_a_reusable_workflow_with_a_stack_input() -> None:
    wf = _load("rail-ci.yml")
    call = wf["on"]["workflow_call"]
    assert call["inputs"]["stack"]["required"] is True
    assert call["inputs"]["rail-ref"]["default"] == "main"
    assert "RAIL_READ_TOKEN" in call["secrets"]
    runs = [s["run"] for s in _steps(wf) if "run" in s]
    assert any("make ci" in r for r in runs)
    assert any("rail check --ci --json" in r for r in runs)


def test_every_action_is_pinned_to_a_commit_sha() -> None:
    for name in ("rail-ci.yml", "continuous-integration.yml"):
        for step in _steps(_load(name)):
            if "uses" in step:
                assert PINNED.match(step["uses"]), f"{name}: {step['uses']} is not pinned to a SHA"


def test_gitleaks_is_installed_with_a_checksum_in_both_workflows() -> None:
    for name in ("rail-ci.yml", "continuous-integration.yml"):
        installs = [s for s in _steps(_load(name)) if "gitleaks" in s.get("run", "")]
        assert installs, f"{name}: no gitleaks install step"
        assert "sha256sum -c" in installs[0]["run"]
        assert installs[0]["env"]["GITLEAKS_VERSION"] == "8.30.1"


def test_red_rail_ci_runs_rail_check_in_ci_scope() -> None:
    runs = [s["run"] for s in _steps(_load("continuous-integration.yml")) if "run" in s]
    assert any("rail check --ci --json" in r for r in runs)


def test_a_container_job_trusts_the_workspace_before_running_git() -> None:
    """actions/checkout adds `safe.directory` to a temporary global config that later steps
    of a container job never see; git then refuses the runner-owned workspace and every
    history gate reports "not a git repository" (PR #1, run 35033439270)."""
    for name in ("rail-ci.yml", "continuous-integration.yml"):
        for job_name, job in _load(name)["jobs"].items():
            if "container" not in job:
                continue
            steps = job["steps"]
            checkout = next(i for i, s in enumerate(steps) if "checkout" in s.get("uses", ""))
            trust = [
                i
                for i, s in enumerate(steps)
                if "safe.directory" in s.get("run", "") and "$GITHUB_WORKSPACE" in s["run"]
            ]
            assert trust and trust[0] > checkout, (
                f"{name}/{job_name}: no `git config --global --add safe.directory "
                f'"$GITHUB_WORKSPACE"` step after the checkout'
            )
            first_git_user = next(
                i
                for i, s in enumerate(steps)
                if "uv run" in s.get("run", "") or "rail" in s.get("run", "")
            )
            assert trust[0] < first_git_user, f"{name}/{job_name}: workspace trusted too late"
