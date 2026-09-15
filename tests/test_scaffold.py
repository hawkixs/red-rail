"""A fresh scaffold passes its own `rail check` at `bootstrap`; `rail upgrade` follows the tags."""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail import gitrepo
from rail.cli import main
from rail.ledger import RECEIPTS_DIR, RecordKind
from rail.ledger.file import FileLedger
from rail.model import Stack, Tier
from rail.scaffold import NewProject, ScaffoldError, new_project, render, upgrade

ROOT = Path(__file__).resolve().parents[1]
CLOCK = lambda: datetime(2026, 9, 15, 8, 0, tzinfo=UTC)  # noqa: E731
GIT = ["git", "-c", "user.name=rail", "-c", "user.email=rail@example.invalid"]


@pytest.fixture
def template_dir(tmp_path: Path) -> Path:
    """A plain-directory copy of the template: uncommitted edits are visible, no git needed."""
    src = tmp_path / "template-src"
    src.mkdir()
    shutil.copy(ROOT / "copier.yml", src / "copier.yml")
    shutil.copytree(ROOT / "template", src / "template")
    return src


def _project(template: Path, dest: Path, **overrides: object) -> NewProject:
    fields: dict[str, object] = {
        "slug": "red-probe",
        "description": "A disposable HTTP probe.",
        "tier": Tier.BOOTSTRAP,
        "stack": Stack.PYTHON,
        "brain_key": "red-probe",
        "dest": dest,
        "template": str(template),
    }
    fields.update(overrides)
    return NewProject(**fields)  # type: ignore[arg-type]


def test_render_python_bootstrap(template_dir: Path, tmp_path: Path) -> None:
    dest = render(_project(template_dir, tmp_path / "red-probe"))
    assert (dest / "rail.yaml").read_text().startswith("rail: 1\nproject: red-probe\n")
    assert "tier: bootstrap" in (dest / "rail.yaml").read_text()
    assert "`red-probe`" in (dest / "CLAUDE.md").read_text()
    assert (dest / "src" / "red_probe" / "__init__.py").is_file()
    assert (dest / "tests" / "test_smoke.py").is_file()
    assert (dest / ".copier-answers.yml").is_file()
    assert (
        "rail-ci.yml@main"
        in (dest / ".github" / "workflows" / "continuous-integration.yml").read_text()
    )
    assert not (dest / "go.mod").exists()
    for rel in ("docs/specs", "docs/plans", "docs/adr", "docs/receipts"):
        assert (dest / rel).is_dir(), rel
    recipes = [
        line
        for line in (dest / "Makefile").read_text().splitlines()
        if line.startswith(("\t", " "))
    ]
    assert recipes and all(line.startswith("\t") for line in recipes)


def test_render_go_prod_and_docs(template_dir: Path, tmp_path: Path) -> None:
    go = render(
        _project(
            template_dir, tmp_path / "red-gopher", slug="red-gopher", tier=Tier.PROD, stack=Stack.GO
        )
    )
    assert (go / "go.mod").is_file() and (go / "main_test.go").is_file()
    assert "target: vps-traefik" in (go / "rail.yaml").read_text()
    assert "healthcheck: https://gopher.hawkixs.com/healthz" in (go / "rail.yaml").read_text()
    assert not (go / "pyproject.toml").exists()
    docs = render(
        _project(template_dir, tmp_path / "red-notes", slug="red-notes", stack=Stack.DOCS)
    )
    assert not (docs / "src").exists() and not (docs / "go.mod").exists()
    assert "ci: lint test check" in (docs / "Makefile").read_text()


def test_render_refuses_an_existing_destination(template_dir: Path, tmp_path: Path) -> None:
    (tmp_path / "red-probe").mkdir()
    with pytest.raises(ScaffoldError, match="already exists"):
        render(_project(template_dir, tmp_path / "red-probe"))


def test_new_project_passes_bootstrap_without_remotes(template_dir: Path, tmp_path: Path) -> None:
    project = _project(template_dir, tmp_path / "red-probe")
    results = new_project(project, publish=False, clock=CLOCK)
    assert all(r.passed for r in results), [r for r in results if not r.passed]
    dest = project.dest
    assert gitrepo.recent_subjects(dest, 1) == ["chore: bootstrap red-probe with the ReD rail"]
    assert len(FileLedger(dest / RECEIPTS_DIR).list("red-probe", kind=RecordKind.CONTRACT)) == 1
    spec = next((dest / "docs" / "specs").glob("*-red-probe-bootstrap-design.md"))
    assert "A disposable HTTP probe." in spec.read_text()
    out = CliRunner().invoke(main, ["check", "--repo", str(dest), "--ci"])
    assert out.exit_code == 0, out.output


def test_new_project_reports_failing_gates(template_dir: Path, tmp_path: Path) -> None:
    (template_dir / "template" / "project" / "Makefile.jinja").unlink()
    with pytest.raises(ScaffoldError, match="hygiene.task_runner"):
        new_project(_project(template_dir, tmp_path / "red-probe"), publish=False, clock=CLOCK)


def test_upgrade_requires_a_versioned_template(template_dir: Path, tmp_path: Path) -> None:
    project = _project(template_dir, tmp_path / "red-probe")
    new_project(project, publish=False, clock=CLOCK)
    with pytest.raises(ScaffoldError, match="_commit"):
        upgrade(project.dest)
    (tmp_path / "plain").mkdir()
    with pytest.raises(ScaffoldError, match="copier-answers"):
        upgrade(tmp_path / "plain")


def test_upgrade_follows_the_template_tags(template_dir: Path, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(template_dir)], check=True)
    subprocess.run([*GIT, "-C", str(template_dir), "add", "-A"], check=True)
    subprocess.run(
        [*GIT, "-C", str(template_dir), "commit", "-q", "-m", "chore: v0.1.0"], check=True
    )
    subprocess.run(["git", "-C", str(template_dir), "tag", "v0.1.0"], check=True)
    project = _project(template_dir, tmp_path / "red-probe", template_ref="v0.1.0")
    new_project(project, publish=False, clock=CLOCK)
    assert "_commit: v0.1.0" in (project.dest / ".copier-answers.yml").read_text()
    readme = template_dir / "template" / "project" / "README.md.jinja"
    readme.write_text(readme.read_text() + "\nUpgraded line.\n")
    subprocess.run(
        [*GIT, "-C", str(template_dir), "commit", "-q", "-am", "feat: v0.2.0"], check=True
    )
    subprocess.run(["git", "-C", str(template_dir), "tag", "v0.2.0"], check=True)
    upgrade(project.dest)
    assert "_commit: v0.2.0" in (project.dest / ".copier-answers.yml").read_text()
    assert "Upgraded line." in (project.dest / "README.md").read_text()


def test_cli_new_and_upgrade(template_dir: Path, tmp_path: Path) -> None:
    out = CliRunner().invoke(
        main,
        [
            "new",
            "red-probe",
            "--description",
            "A disposable HTTP probe.",
            "--dest",
            str(tmp_path / "red-probe"),
            "--template",
            str(template_dir),
            "--no-remotes",
        ],
    )
    assert out.exit_code == 0, out.output
    assert "| red-probe |" in out.output and "PASS  hygiene.rail_config" in out.output
    out = CliRunner().invoke(main, ["new", "Bad_Name", "--description", "x", "--no-remotes"])
    assert out.exit_code == 2 and "red-<kebab-case>" in out.output
    out = CliRunner().invoke(main, ["upgrade", "--repo", str(tmp_path / "red-probe")])
    assert out.exit_code == 1 and "_commit" in out.output
