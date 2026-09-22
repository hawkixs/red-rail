"""A fresh scaffold passes its own `rail check` at `bootstrap`; `rail upgrade` follows the tags."""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from rail import gitrepo
from rail.brain.client import BrainClient
from rail.cli import main
from rail.ledger import RECEIPTS_DIR, RecordKind
from rail.ledger.file import FileLedger
from rail.model import LedgerBackend, Stack, Tier
from rail.scaffold import ANSWERS_FILE, NewProject, ScaffoldError, new_project, render, upgrade
from tests.fake_brain import FakeBrain

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


# `resolve_rail_ref` talks to a real remote; the fixture template is a plain directory, and
# refusing to pin against it is the fail-closed behaviour under test elsewhere.
FIXTURE_PIN = "f" * 40


def _pin(template: str, **kwargs: object) -> str:
    return FIXTURE_PIN


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
    # GitHub only (decision 30acbbde): no mirror is described anywhere in the rendered tree
    for name in ("CLAUDE.md", "README.md"):
        assert "gitlab" not in (dest / name).read_text().lower(), name
    # what red-probe's first `make ci` on a fresh clone taught (2026-09-20): the dev tools are a
    # uv dependency group — installed by `uv run` — not an extra that nothing installs
    assert "[dependency-groups]" in (dest / "pyproject.toml").read_text()
    assert "optional-dependencies" not in (dest / "pyproject.toml").read_text()
    assert "\tuv sync\n" in (dest / "Makefile").read_text()
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


def test_render_wraps_the_module_docstring_and_reads_the_version_from_the_install(
    template_dir: Path, tmp_path: Path
) -> None:
    """A long description rendered verbatim into the module docstring was 127 characters on
    red-probe (E501 on the first `make ci`); the version was a second hard-coded copy."""
    description = (
        "A disposable HTTP probe (/healthz, /version, /metrics) behind Traefik at "
        "probe.hawkixs.com — the rail's end-to-end proof, described at some length."
    )
    dest = render(_project(template_dir, tmp_path / "red-probe", description=description))
    module = (dest / "src" / "red_probe" / "__init__.py").read_text()
    assert max(len(line) for line in module.splitlines()) <= 100
    assert module.startswith('"""red-probe: A disposable HTTP probe')
    assert 'version("red-probe")' in module and "0+unknown" in module
    assert '__version__ = "0.1.0"' not in module
    smoke = (dest / "tests" / "test_smoke.py").read_text()
    assert "tomllib" in smoke and '["project"]["version"]' in smoke
    assert 'distribution("red-probe")' in smoke  # compared only where the package is installed
    assert "assert __version__\n" in smoke  # the fallback is still asserted truthy
    # a long slug must not push the version line past 100 characters either
    long_slug = "red-a-project-whose-slug-is-forty-four-chars"
    dest = render(_project(template_dir, tmp_path / long_slug, slug=long_slug, brain_key=long_slug))
    module = (dest / "src" / long_slug.replace("-", "_") / "__init__.py").read_text()
    assert max(len(line) for line in module.splitlines()) <= 100


def test_a_description_that_would_break_the_rendered_files_is_refused(
    template_dir: Path, tmp_path: Path
) -> None:
    """The description is spliced into a docstring and a TOML string: a double quote, a
    backslash or a line break would break the module or pyproject — refused up front."""
    for bad in ('Implements the "outbox" pattern', "a back\\slash", "two\nlines"):
        with pytest.raises(ScaffoldError, match="description"):
            _project(template_dir, tmp_path / "red-probe", description=bad)


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


def test_new_project_publishes_github_only(template_dir: Path, tmp_path: Path) -> None:
    """The publish path of `rail new`: GitHub is asked, created and pushed; glab is never
    called and no `gitlab` remote is added (decision 30acbbde)."""
    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(list(args))
        stdout = "not found" if args[:3] == ["gh", "repo", "view"] else ""
        code = 1 if args[:3] == ["gh", "repo", "view"] else 0
        return subprocess.CompletedProcess(args, code, stdout=stdout, stderr="not found")

    project = _project(template_dir, tmp_path / "red-probe")
    results = new_project(project, publish=True, clock=CLOCK, run=run, resolve=_pin)
    assert all(r.passed for r in results)
    assert [c[:3] for c in calls if c[0] in ("gh", "glab")] == [
        ["gh", "repo", "view"],
        ["gh", "repo", "create"],
    ]
    remote_adds = [c for c in calls if c[:3] == ["git", "remote", "add"]]
    assert [c[3] for c in remote_adds] == ["origin"]
    assert ["git", "push", "-u", "origin", "main"] in calls
    assert not any("gitlab" in c for c in calls)
    spec = next((project.dest / "docs" / "specs").glob("*-bootstrap-design.md")).read_text()
    assert "gitlab" not in spec.lower() and "hawkixs/red-probe" in spec


def test_new_project_passes_bootstrap_without_remotes(template_dir: Path, tmp_path: Path) -> None:
    project = _project(template_dir, tmp_path / "red-probe")
    results = new_project(project, publish=False, clock=CLOCK, resolve=_pin)
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
        new_project(
            _project(template_dir, tmp_path / "red-probe"),
            publish=False,
            clock=CLOCK,
            resolve=_pin,
        )


def test_upgrade_requires_a_versioned_template(template_dir: Path, tmp_path: Path) -> None:
    project = _project(template_dir, tmp_path / "red-probe")
    new_project(project, publish=False, clock=CLOCK, resolve=_pin)
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
    new_project(project, publish=False, clock=CLOCK, resolve=_pin)
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
            "--rail-ref",
            FIXTURE_PIN,
            "--no-remotes",
        ],
    )
    assert out.exit_code == 0, out.output
    assert "| red-probe |" in out.output and "PASS  hygiene.rail_config" in out.output
    out = CliRunner().invoke(main, ["new", "Bad_Name", "--description", "x", "--no-remotes"])
    assert out.exit_code == 2 and "red-<kebab-case>" in out.output
    out = CliRunner().invoke(main, ["upgrade", "--repo", str(tmp_path / "red-probe")])
    assert out.exit_code == 1 and "_commit" in out.output


def test_render_wraps_copier_failures(template_dir: Path, tmp_path: Path) -> None:
    """Review finding: a copier failure (bad ref, clone error) is a ScaffoldError, never a
    traceback."""

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("could not clone template")

    with pytest.raises(ScaffoldError, match="could not clone template"):
        render(_project(template_dir, tmp_path / "red-probe"), copy=broken)


def test_init_git_failures_are_scaffold_errors(
    template_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail import scaffold

    project = _project(template_dir, tmp_path / "red-probe")
    render(project)
    monkeypatch.setattr(scaffold, "GIT", "git-that-does-not-exist")
    with pytest.raises(ScaffoldError, match="git-that-does-not-exist"):
        scaffold.init_git(project)


def test_git_identity_fallback_covers_a_missing_name(
    template_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding: a host with user.email but no user.name must still commit."""
    project = _project(template_dir, tmp_path / "red-probe")
    render(project)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\temail = someone@example.invalid\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.delenv("GIT_AUTHOR_NAME", raising=False)
    monkeypatch.delenv("GIT_COMMITTER_NAME", raising=False)
    from rail.scaffold import init_git

    assert len(init_git(project)) == 40


def test_verify_scores_the_bootstrap_floor_regardless_of_the_declared_tier(
    template_dir: Path, tmp_path: Path
) -> None:
    """`verify` is the floor a fresh tree can pass; the declared tier is what `rail check`
    demands next, not what `rail new` scores itself against."""
    from rail.gates import Stage
    from rail.scaffold import record_contract, verify, write_bootstrap_spec

    project = _project(template_dir, tmp_path / "red-probe")
    render(project)
    write_bootstrap_spec(project)
    record_contract(project, clock=CLOCK)
    manifest = project.dest / "rail.yaml"
    manifest.write_text(manifest.read_text().replace("tier: bootstrap", "tier: dev"))
    stages = {r.stage for r in verify(project)}
    assert stages == {Stage.HYGIENE, Stage.INTENT, Stage.DESIGN}


def test_render_prod_python_on_the_brain_ledger(template_dir: Path, tmp_path: Path) -> None:
    ticket = "04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f"
    dest = render(
        _project(
            template_dir,
            tmp_path / "red-probe",
            tier=Tier.PROD,
            ledger=LedgerBackend.BRAIN,
            ticket=ticket,
        )
    )
    manifest = (dest / "rail.yaml").read_text()
    assert "ledger: brain\n" in manifest and f"ticket: {ticket}\n" in manifest
    assert "target: vps-traefik" in manifest
    assert "ledger `brain`" in (dest / "CLAUDE.md").read_text()
    assert (dest / "Dockerfile").is_file() and (dest / "deploy" / "compose.yaml").is_file()


def test_a_prod_scaffold_is_verified_at_the_bootstrap_floor(
    template_dir: Path, tmp_path: Path
) -> None:
    project = _project(template_dir, tmp_path / "red-probe", tier=Tier.PROD)
    results = new_project(project, publish=False, clock=CLOCK, resolve=_pin)
    assert all(r.passed for r in results), [r for r in results if not r.passed]
    assert {r.stage.value for r in results} == {"hygiene", "intent", "design"}
    contract = FileLedger(project.dest / RECEIPTS_DIR).list("red-probe", kind=RecordKind.CONTRACT)
    deliverable = contract[0].payload["contract"]["deliverables"][0]
    assert deliverable["required_checks"] == [
        {
            "kind": "check_run",
            "name": "red-rail/review",
            "app_slug": "red-rail-reviewer",
            "provider_id": None,
        }
    ]
    assert deliverable["review"] == {
        "required_approvals": 1,
        "allowed_reviewers": ["red-rail-reviewer[bot]"],
    }
    out = CliRunner().invoke(main, ["check", "--repo", str(project.dest), "--ci"])
    assert out.exit_code == 1  # release, deploy, observe, learn: the declared tier's debt
    assert "FAIL  release.released" in out.output


def test_a_bootstrap_contract_needs_no_check_and_no_approval(
    template_dir: Path, tmp_path: Path
) -> None:
    project = _project(template_dir, tmp_path / "red-probe")
    new_project(project, publish=False, clock=CLOCK, resolve=_pin)
    contract = FileLedger(project.dest / RECEIPTS_DIR).list("red-probe", kind=RecordKind.CONTRACT)
    deliverable = contract[0].payload["contract"]["deliverables"][0]
    assert deliverable["required_checks"] == [] and deliverable["no_checks_reason"]
    assert deliverable["review"]["required_approvals"] == 0


def test_brain_mode_records_the_contract_after_the_remotes_and_mirrors_it(
    template_dir: Path, tmp_path: Path
) -> None:
    brain = FakeBrain(agent="rail new")
    ticket = brain.add_ticket("red", "red-probe")
    brain.register_repository("red-probe", 4242, "hawkixs/red-probe")
    project = _project(
        template_dir,
        tmp_path / "red-probe",
        tier=Tier.PROD,
        ledger=LedgerBackend.BRAIN,
        ticket=ticket,
    )
    calls: list[list[str]] = []

    def run(args, **kwargs):  # the remotes are faked: gh/glab/git push never run here
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    results = new_project(
        project,
        publish=False,
        clock=CLOCK,
        client=BrainClient.in_memory(brain, agent="rail new"),
        run=run,
        resolve=_pin,
    )
    assert all(r.passed for r in results)
    assert brain.tickets[ticket].revisions, "the contract is set in brain"
    revision = brain.tickets[ticket].revisions[-1]
    assert revision["deliverables"][0]["review"]["required_approvals"] == 1
    subjects = gitrepo.recent_subjects(project.dest, 2)
    assert subjects == [
        "chore(rail): mirror the delivery contract",
        "chore: bootstrap red-probe with the ReD rail",
    ]
    assert (project.dest / RECEIPTS_DIR).glob("*-contract-*.json")
    assert calls == []  # publish=False: nothing pushed


def test_render_go_ships_a_resolvable_module_and_a_sync_that_pins_the_analysers(
    template_dir: Path, tmp_path: Path
) -> None:
    """A `tool` directive carries no version — `go get -tool` writes it together with its
    `require`, and a `tool` block alone cannot resolve (`go.dev/ref/mod`). The template
    therefore ships neither, and `make sync` is what pins the analysers into go.mod and
    go.sum. Until it has run, `build.lint` fails and names the command — the gate telling
    the repository what awaits it.

    Before this, the Go template was rendered by no test at all, so a broken jinja
    conditional went unnoticed. Versions measured on the red-alerts pilot (2026-09-21)."""
    from rail.gates.build import lint

    dest = render(_project(template_dir, tmp_path / "red-beta", slug="red-beta", stack=Stack.GO))

    go_mod = (dest / "go.mod").read_text()
    # not 1.26.5: it carries stdlib advisories reachable from a serving path (how many
    # depends on the code scanned, not on the release)
    assert "go 1.26.8" in go_mod
    # no unresolvable `tool` block: it would break `make sync`, `make lint` and `make vuln`
    # on the very first run of every scaffolded Go project
    assert "tool (" not in go_mod and "require" not in go_mod

    makefile = (dest / "Makefile").read_text()
    assert "$(GO) get -tool honnef.co/go/tools/cmd/staticcheck@v0.8.1" in makefile
    assert "$(GO) get -tool golang.org/x/vuln/cmd/govulncheck@v1.8.0" in makefile
    assert "$(GO) tool staticcheck ./..." in makefile
    assert "$(GO) tool govulncheck ./..." in makefile
    # a cached green is worse than no test: `go test` serves a package from cache on inputs
    # it can observe, so a guard that shells out stops guarding
    assert "$(GO) test -race -count=1 ./..." in makefile
    assert "ci: lint test vuln check" in makefile

    assert (dest / "main.go").is_file() and (dest / "main_test.go").is_file()
    assert "stack: go" in (dest / "rail.yaml").read_text()
    assert (
        "stack: go" in (dest / ".github" / "workflows" / "continuous-integration.yml").read_text()
    )

    # the gate fails on a fresh scaffold and names the way out — `make sync` is that way
    result = lint(dest)
    assert not result.passed and "go get -tool" in result.details


def test_render_prod_private_compose_never_points_at_the_public_internet(
    template_dir: Path, tmp_path: Path
) -> None:
    """A host with no public route must not be scaffolded with a public healthcheck, and has
    no guessable default either — the address is stated by the operator, never assumed by the
    scaffold, and no machine address is written down in this repository."""
    dest = render(
        _project(
            template_dir,
            tmp_path / "red-alerts",
            slug="red-alerts",
            tier=Tier.PROD,
            deploy_target="private-compose",
            healthcheck="http://192.0.2.10:9204/healthz",
        )
    )
    manifest = (dest / "rail.yaml").read_text()
    assert "target: private-compose" in manifest
    assert "hawkixs.com" not in manifest, "a private target has no public domain"
    assert "healthcheck: http://192.0.2." in manifest

    # and the contract must not promise a route this shape does not have
    from rail.scaffold import bootstrap_contract

    criteria = bootstrap_contract(
        _project(
            template_dir,
            tmp_path / "unused",
            slug="red-alerts",
            tier=Tier.PROD,
            deploy_target="private-compose",
            healthcheck="http://192.0.2.10:9204/healthz",
        )
    ).acceptance_criteria
    assert not any("Traefik" in c for c in criteria), criteria
    assert any("private address" in c for c in criteria), criteria


def test_every_deploy_target_the_cli_offers_is_a_copier_choice() -> None:
    """`rail new --deploy-target` offers every `DeployTarget`; copier must accept them all,
    or the command fails inside copier after the operator has already answered."""
    import yaml

    from rail.model import DeployTarget

    questions = yaml.safe_load((ROOT / "copier.yml").read_text())
    assert set(questions["deploy_target"]["choices"]) == {t.value for t in DeployTarget}


def test_the_healthcheck_default_follows_the_target_in_the_template_itself() -> None:
    """`rail new` always passes a healthcheck, so copier's own default is only reached by a
    human running `copier copy` directly — and nothing exercised it. Render the expression."""
    import yaml
    from jinja2.sandbox import SandboxedEnvironment

    questions = yaml.safe_load((ROOT / "copier.yml").read_text())
    template = SandboxedEnvironment().from_string(questions["healthcheck"]["default"])
    private = template.render(deploy_target="private-compose", project="red-alerts")
    public = template.render(deploy_target="vps-traefik", project="red-alerts")
    assert private == "", "a private target has no guessable default address"
    assert public == "https://alerts.hawkixs.com/healthz"


def test_a_private_target_refuses_to_invent_an_address(template_dir: Path, tmp_path: Path) -> None:
    """No machine address lives in this repository, so there is nothing to fall back to."""
    project = _project(
        template_dir,
        tmp_path / "red-alerts",
        slug="red-alerts",
        tier=Tier.PROD,
        deploy_target="private-compose",
    )
    with pytest.raises(ScaffoldError, match="--healthcheck"):
        _ = project.answers


def test_render_pins_the_reusable_workflow_to_a_resolved_sha(
    template_dir: Path, tmp_path: Path
) -> None:
    """`@main` means the gates guarding a repository change without a commit in it — this
    rail's main moved eleven times in one day. The pin is an explicit answer, recorded in
    `.copier-answers.yml`, so `rail upgrade` bumps it as a reviewable line."""
    sha = "a" * 40
    dest = render(_project(template_dir, tmp_path / "red-beta", slug="red-beta", rail_ref=sha))

    workflow = (dest / ".github" / "workflows" / "continuous-integration.yml").read_text()
    assert f"rail-ci.yml@{sha}" in workflow
    assert "@main" not in workflow
    assert sha in (dest / ".copier-answers.yml").read_text()


def test_render_without_a_pin_still_calls_main(template_dir: Path, tmp_path: Path) -> None:
    """A human running `copier copy` directly, and every project already scaffolded, keep
    working: pinning is what `rail new` does, not what the template imposes."""
    dest = render(_project(template_dir, tmp_path / "red-beta", slug="red-beta"))
    workflow = (dest / ".github" / "workflows" / "continuous-integration.yml").read_text()
    assert "rail-ci.yml@main" in workflow


def test_resolving_the_template_sha_never_falls_back_to_a_branch(tmp_path: Path) -> None:
    """A pin that quietly becomes a moving branch is worse than no pin: it reads as pinned.
    `git describe` output is refused for the same reason — `v0.4.0-44-g4c257be` resolves
    locally but is neither a tag nor a branch on the remote, so GitHub cannot use it."""
    from rail.scaffold import resolve_rail_ref

    def ls_remote(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, stdout=f"{'b' * 40}\trefs/heads/main\n", stderr=""
        )

    assert resolve_rail_ref("git@github.com:hawkixs/red-rail.git", run=ls_remote) == "b" * 40

    def refuses(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 128, stdout="", stderr="repository not found")

    with pytest.raises(ScaffoldError, match="could not resolve"):
        resolve_rail_ref("git@github.com:hawkixs/absent.git", run=refuses)

    def describes(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="v0.4.0-44-g4c257be\trefs/heads/main\n")

    with pytest.raises(ScaffoldError, match="could not resolve"):
        resolve_rail_ref("git@github.com:hawkixs/red-rail.git", run=describes)


def test_upgrade_rebumps_the_pin_so_the_change_is_a_reviewable_line(tmp_path: Path) -> None:
    """The bump lands in the diff of an upgrade, beside the template drift it already
    resorbs — one explicit gesture rather than a silent effect. A project that never
    upgrades keeps the gates it was scaffolded with; one that upgrades takes the current."""
    from rail.scaffold import upgrade

    repo = tmp_path / "red-beta"
    (repo / ".github" / "workflows").mkdir(parents=True)
    old, new = "a" * 40, "c" * 40
    workflow = repo / ".github" / "workflows" / "continuous-integration.yml"
    workflow.write_text(f"jobs:\n  rail:\n    uses: x/y/.github/workflows/rail-ci.yml@{old}\n")
    (repo / ANSWERS_FILE).write_text(
        f"_commit: v0.4.0\n_src_path: git@github.com:hawkixs/red-rail.git\nrail_ref: {old}\n"
    )

    seen: dict[str, object] = {}

    def update(dest: Path, **kwargs: object) -> None:
        seen.update(kwargs)
        (repo / ANSWERS_FILE).write_text(
            f"_commit: v0.5.0\n_src_path: git@github.com:hawkixs/red-rail.git\nrail_ref: {new}\n"
        )
        workflow.write_text(f"jobs:\n  rail:\n    uses: x/y/.github/workflows/rail-ci.yml@{new}\n")

    upgrade(repo, update=update, resolve=lambda template, **kw: new)

    assert seen["data"] == {"rail_ref": new}, "the new pin is handed to copier as an answer"
    assert f"@{new}" in workflow.read_text() and old not in workflow.read_text()


def test_new_project_resolves_the_pin_before_rendering(template_dir: Path, tmp_path: Path) -> None:
    """`rail new` pins; the template only carries what it is handed. A project scaffolded
    today therefore records which gates guarded it, in its own tree."""
    from rail.scaffold import new_project

    sha = "d" * 40
    project = _project(template_dir, tmp_path / "red-beta", slug="red-beta")
    new_project(
        project,
        publish=False,
        run=lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, stdout="", stderr=""),
        resolve=lambda template, **kw: sha,
    )
    workflow = project.dest / ".github" / "workflows" / "continuous-integration.yml"
    assert f"rail-ci.yml@{sha}" in workflow.read_text()
