"""The workflows are data: callable, pinned, and they run the same gates as the workstation."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
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


def test_rail_ci_sets_up_a_pinned_go_for_the_go_stack() -> None:
    """`inputs.stack` was declared and never used, so a Go project depended on whatever Go
    the runner happened to preinstall — and a `red-ci` self-hosted runner has none at all."""
    wf = _load("rail-ci.yml")
    go = [s for s in _steps(wf) if "setup-go" in s.get("uses", "")]
    assert len(go) == 1, "exactly one Go setup step"
    step = go[0]
    assert step["if"] == "inputs.stack == 'go'", "only the Go stack pays for it"
    # the patch level matters: 1.26.5 carries stdlib advisories govulncheck reports as
    # reachable from a serving path. The count belongs to the scanned code, not to the
    # release — two ReD repositories measured three and six against the same one.
    assert step["with"]["go-version"] == "1.26.8"
    assert step["with"]["check-latest"] is False, "pinned, never the latest patch of the day"
    assert step["with"]["cache"] is False, "no go.sum is shipped, so there is nothing to key on"


def test_the_template_never_inherits_every_secret() -> None:
    """`secrets: inherit` hands the called workflow everything the repository holds, while
    `rail-ci` declares exactly one optional secret. Nothing leaked today because the pilot
    had no repository secrets — which is precisely why it was the moment to fix it: the day
    someone adds one to an onboarded repository, nobody re-reads this file. Omitting the
    key entirely leaves `secrets.RAIL_READ_TOKEN` empty, and the workflow already falls
    back to `github.token` (red-rail is public, so no token is needed to check it out)."""
    import yaml

    template = (
        ROOT / "template" / "project" / ".github" / "workflows"
    ) / "continuous-integration.yml.jinja"
    text = template.read_text()

    # the reusable workflow still declares that one secret, and still declares it optional
    call = _load("rail-ci.yml")["on"]["workflow_call"]
    assert set(call["secrets"]) == {"RAIL_READ_TOKEN"}
    assert call["secrets"]["RAIL_READ_TOKEN"]["required"] is False

    rendered = (
        text.replace("{{ stack }}", "go").replace("{% raw %}", "").replace("{% endraw %}", "")
    )
    job = yaml.safe_load(rendered)["jobs"]["rail"]
    assert job["with"]["stack"] == "go"
    # the parsed call, not the spelling: a comment explaining why inheritance is wrong would
    # fail a text search for it, and what matters is what the workflow is handed
    assert job.get("secrets") != "inherit", "hand over a declared secret, not the whole box"
    assert "secrets" not in job, "no secret is needed at all: the fallback is github.token"


def test_rail_ci_installs_rust_from_the_project_pin() -> None:
    """rustup by version and checksum, the toolchain from the project's rust-toolchain.toml
    (the one pin), a cache keyed on what decides its content, then the lock checked by
    `--locked` (spec 2026-09-24-rust-stack, decision 8)."""
    wf = _load("rail-ci.yml")
    assert "rust" in wf["on"]["workflow_call"]["inputs"]["stack"]["description"]
    steps = _steps(wf)
    rust = [s for s in steps if s.get("if") == "inputs.stack == 'rust'"]
    assert [s["name"] for s in rust] == [
        "Set up Rust (rustup pinned, checksum verified)",
        "Cache the cargo registry and the pinned cargo-deny",
        "Fetch against the committed lock, install cargo-deny",
    ]
    setup, cache, sync = rust
    assert re.fullmatch(r"[0-9a-f]{64}", setup["env"]["RUSTUP_INIT_SHA256"])
    assert setup["env"]["RUSTUP_VERSION"] == "1.29.1"
    run = setup["run"]
    assert "sha256sum -c" in run
    assert "--default-toolchain none" in run
    assert re.search(r'"\$HOME/\.cargo/bin/rustup" toolchain install\s*$', run, re.MULTILINE)
    assert '"$HOME/.cargo/bin/rustup" component add rustfmt clippy' in run
    assert run.index("cargo fmt --version") < run.index("cargo clippy --version")
    assert not re.search(r"\b1\.\d+\.\d+\b", run), "the toolchain version lives in the project"
    assert run.rstrip().endswith('echo "$HOME/.cargo/bin" >> "$GITHUB_PATH"')

    assert PINNED.match(cache["uses"])
    key = cache["with"]["key"]
    for name in ("rust-toolchain.toml", "Cargo.lock", "Makefile"):
        assert f"'{name}'" in key, name
    paths = cache["with"]["path"].split()
    assert ".cargo-tools" in paths and not any("target" in p for p in paths)
    assert sync["run"].strip() == "make sync LOCKED=--locked"

    names = [s.get("name") for s in steps]
    assert names.index(sync["name"]) < names.index("Project CI (make ci, rail gates in CI scope)")


def test_the_template_ci_passes_no_runner() -> None:
    """GitHub-hosted by default: a project moves to red-ci by its own choice (decision 9)."""
    template = (
        ROOT / "template" / "project" / ".github" / "workflows" / "continuous-integration.yml.jinja"
    )
    assert "runs-on" not in template.read_text()
