"""A declared docs root (`gates: docs.root`, ticket 7daf7462): brain-v42 keeps its specs and
plans in a private clone that a public checkout and CI never hold. The design, plan and docs
layout gates read the declared root, and skip visibly when it is absent; the default `docs`
root keeps failing when absent."""

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from rail import markdown
from rail.cli import main
from rail.gates.design import spec
from rail.gates.hygiene import docs_layout
from rail.gates.plan import plan
from rail.model import RailConfig
from tests.helpers import conforming_tree, write_manifest

ROOT = "internal/docs/superpowers"
MINIMAL = {"rail": 1, "project": "red-alpha", "brain_key": "red-alpha", "tier": "dev"}


def _private(tmp_path: Path) -> Path:
    """A conforming dev tree whose specs, plans and ADRs moved under the declared root."""
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    write_manifest(
        repo, project="red-alpha", tier="dev", gates={"docs.root": (ROOT, "private clone")}
    )
    target = repo / ROOT
    target.mkdir(parents=True)
    for sub in ("specs", "plans", "adr"):
        shutil.move(repo / "docs" / sub, target / sub)
    plan_doc = target / "plans" / "2026-09-15-red-alpha-plan.md"
    plan_doc.write_text(plan_doc.read_text().replace("docs/specs/", f"{ROOT}/specs/"))
    return repo


def _config(project: str, gates: dict | None = None) -> RailConfig:
    data = {**MINIMAL, "project": project, "stack": "python"}
    if gates is not None:
        data["gates"] = gates
    return RailConfig.model_validate(data)


def test_brain_v42_is_an_admitted_project_name() -> None:
    assert _config("brain-v42").project == "brain-v42"


@pytest.mark.parametrize("name", ["blue-v42", "brain-v43", "brain_v42"])
def test_other_names_outside_the_red_pattern_are_refused(name: str) -> None:
    with pytest.raises(ValidationError, match="project"):
        _config(name)


@pytest.mark.parametrize(
    "value",
    [
        "/srv/docs",
        "../docs",
        "internal/../../docs",
        "",
        3,
        ["docs"],
        "internal\\docs",
        "docs",
        "docs/",
    ],
)
def test_the_docs_root_is_a_relative_path_inside_the_repository(value: object) -> None:
    with pytest.raises(ValidationError, match="docs.root"):
        _config("red-alpha", {"docs.root": {"value": value, "reason": "private clone"}})


def test_the_docs_gates_read_the_declared_root(tmp_path: Path) -> None:
    repo = _private(tmp_path)
    design = spec(repo)
    assert design.passed and design.skipped is None, design.details
    planned = plan(repo)
    assert planned.passed and planned.skipped is None, planned.details
    assert f"{ROOT}/specs/2026-09-15-red-alpha-design.md" in planned.details
    layout = docs_layout(repo)
    assert layout.passed and ROOT in layout.details, layout.details


def test_an_absent_declared_root_is_skipped_by_name(tmp_path: Path) -> None:
    repo = _private(tmp_path)
    shutil.rmtree(repo / "internal")
    for result in (spec(repo), plan(repo), docs_layout(repo)):
        assert result.skipped == "private_docs_root", result
        assert f"{ROOT} absent" in result.details


def test_rail_check_reports_the_skip_rather_than_a_pass(tmp_path: Path) -> None:
    repo = _private(tmp_path)
    shutil.rmtree(repo / "internal")
    out = CliRunner().invoke(main, ["check", "design", "--repo", str(repo), "--json"])
    report = json.loads(out.output)
    assert "design.spec" in report["not_evaluated"]
    text = CliRunner().invoke(main, ["check", "design", "--repo", str(repo)]).output
    assert "SKIP" in text and f"{ROOT} absent" in text


def test_the_default_root_still_fails_when_absent(tmp_path: Path) -> None:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    shutil.rmtree(repo / "docs")
    for result in (spec(repo), plan(repo), docs_layout(repo)):
        assert not result.passed and result.skipped is None, result


def test_a_missing_adr_directory_under_the_declared_root_fails(tmp_path: Path) -> None:
    repo = _private(tmp_path)
    shutil.rmtree(repo / ROOT / "adr")
    result = docs_layout(repo)
    assert not result.passed and f"{ROOT}/adr" in result.details


def test_a_plan_under_a_declared_root_must_cite_a_spec_under_that_root(tmp_path: Path) -> None:
    repo = _private(tmp_path)
    plan_doc = repo / ROOT / "plans" / "2026-09-15-red-alpha-plan.md"
    plan_doc.write_text(plan_doc.read_text().replace(f"{ROOT}/specs/", "docs/specs/"))
    result = plan(repo)
    assert not result.passed and f"{ROOT}/specs/" in result.details


def test_spec_references_follow_the_specs_directory() -> None:
    text = f"Spec: {ROOT}/specs/2026-10-03-x.md and docs/specs/2026-09-01-y.md\n"
    assert markdown.spec_references(text, f"{ROOT}/specs") == [f"{ROOT}/specs/2026-10-03-x.md"]
    assert markdown.spec_references(text) == ["docs/specs/2026-09-01-y.md"]
