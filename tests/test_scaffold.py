"""A fresh scaffold passes its own `rail check` at `bootstrap`; `rail upgrade` follows the tags."""

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import pytest
from click.testing import CliRunner

from rail import gitrepo
from rail.brain.client import BrainClient
from rail.cli import main
from rail.commands.new import BRAIN_KEY
from rail.gates.hygiene import DOMAIN_PLACEHOLDER, ROSTER_HEADER, roster_row, table_cells
from rail.ledger import RECEIPTS_DIR, RecordKind
from rail.ledger.file import FileLedger
from rail.model import DeployTarget, LedgerBackend, Stack, Tier
from rail.remotes import RemoteError
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


# the Apps that publish the checks main requires, as `gh api apps/<slug>` answers here; the ids
# are made up, so a pinned id can only have come from that lookup
APP_IDS = {"github-actions": 101, "red-rail-reviewer": 202}
CI_CHECK = {"context": "rail / make ci + rail check", "app_id": 101}
REVIEW_CHECK = {"context": "red-rail/review", "app_id": 202}


def _github(calls: list[list[str]], bodies: list[dict], *, refuse: str | None = None):
    """A fake host: GitHub does not know the repository yet, knows the Apps by slug, and takes
    the branch protection put on `main` — or refuses `refuse`: the App lookup or the
    protection itself."""

    def run(args, **kwargs):
        calls.append(list(args))
        if args[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="not found")
        if args[:2] == ["gh", "api"] and args[2].startswith("apps/"):
            if refuse == "app":
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="HTTP 404")
            app = APP_IDS[args[2].removeprefix("apps/")]
            return subprocess.CompletedProcess(args, 0, stdout=f"{app}\n", stderr="")
        if args[:4] == ["gh", "api", "-X", "PUT"]:
            bodies.append(json.loads(kwargs["input"]))
            if refuse == "protection":
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="HTTP 403")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    return run


def test_new_project_publishes_github_only(template_dir: Path, tmp_path: Path) -> None:
    """The publish path of `rail new`: GitHub is asked, created, pushed and its main protected;
    glab is never called and no `gitlab` remote is added (decision 30acbbde)."""
    calls: list[list[str]] = []

    project = _project(template_dir, tmp_path / "red-probe")
    results = new_project(project, publish=True, clock=CLOCK, run=_github(calls, []), resolve=_pin)
    assert all(r.passed for r in results)
    assert [c[:3] for c in calls if c[0] in ("gh", "glab")] == [
        ["gh", "repo", "view"],
        ["gh", "repo", "create"],
        ["gh", "api", "apps/github-actions"],
        ["gh", "api", "-X"],
    ]
    remote_adds = [c for c in calls if c[:3] == ["git", "remote", "add"]]
    assert [c[3] for c in remote_adds] == ["origin"]
    assert ["git", "push", "-u", "origin", "main"] in calls
    assert not any("gitlab" in c for c in calls)
    spec = next((project.dest / "docs" / "specs").glob("*-bootstrap-design.md")).read_text()
    assert "gitlab" not in spec.lower() and "hawkixs/red-probe" in spec


@pytest.mark.parametrize(
    ("tier", "required"),
    [(Tier.BOOTSTRAP, [CI_CHECK]), (Tier.DEV, [CI_CHECK, REVIEW_CHECK])],
    ids=["bootstrap", "dev"],
)
def test_new_project_protects_main_with_the_checks_of_its_tier(
    template_dir: Path, tmp_path: Path, tier: Tier, required: list[dict]
) -> None:
    """Decision a3846910: main requires the CI job from day 0, and the independent reviewer's
    check wherever the contract requires it (tier dev and up) — each pinned to the App that
    publishes it, resolved by slug, so a same-named check from another App never satisfies it.
    Admins are not bound, so merging on judgment stays the operator's gesture; strict is off,
    so a main that moves forces no extra review pass."""
    calls: list[list[str]] = []
    bodies: list[dict] = []
    project = _project(template_dir, tmp_path / "red-probe", tier=tier)

    new_project(project, publish=True, clock=CLOCK, run=_github(calls, bodies), resolve=_pin)

    assert [c for c in calls if c[:4] == ["gh", "api", "-X", "PUT"]] == [
        [
            "gh",
            "api",
            "-X",
            "PUT",
            "repos/hawkixs/red-probe/branches/main/protection",
            "--input",
            "-",
        ]
    ]
    assert bodies == [
        {
            "required_status_checks": {"strict": False, "checks": required},
            "enforce_admins": False,
            "required_pull_request_reviews": None,
            "restrictions": None,
        }
    ]


def test_new_project_protects_main_only_after_its_last_direct_push(
    template_dir: Path, tmp_path: Path
) -> None:
    """On the brain ledger `rail new` pushes to main twice — the bootstrap, then the mirror of
    the contract. The protection comes after both: from then on main takes pull requests."""
    brain = FakeBrain(agent="rail new")
    ticket = brain.add_ticket("red", "red-probe")
    brain.register_repository("red-probe", 4242, "hawkixs/red-probe")
    project = _project(
        template_dir,
        tmp_path / "red-probe",
        tier=Tier.DEV,
        ledger=LedgerBackend.BRAIN,
        ticket=ticket,
    )
    calls: list[list[str]] = []

    new_project(
        project,
        publish=True,
        clock=CLOCK,
        client=BrainClient.in_memory(brain, agent="rail new"),
        run=_github(calls, []),
        resolve=_pin,
    )

    pushes = [i for i, c in enumerate(calls) if c[:2] == ["git", "push"]]
    protection = next(i for i, c in enumerate(calls) if c[:4] == ["gh", "api", "-X", "PUT"])
    assert len(pushes) == 2 and protection > max(pushes)


@pytest.mark.parametrize("refuse", ["app", "protection"])
def test_a_refused_protection_says_main_is_left_unprotected(
    template_dir: Path, tmp_path: Path, refuse: str
) -> None:
    """The repository is created and pushed before main is protected. When that last step is
    refused — resolving an App or putting the protection — the error says what exists and
    what does not: an unprotected main is exactly what let red-alerts#2 merge an unreviewed
    head."""
    project = _project(template_dir, tmp_path / "red-probe", tier=Tier.DEV)

    with pytest.raises(RemoteError, match="main is NOT protected"):
        new_project(
            project, publish=True, clock=CLOCK, run=_github([], [], refuse=refuse), resolve=_pin
        )


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


def test_cli_new_prints_a_roster_row_shaped_like_the_header(
    template_dir: Path, tmp_path: Path
) -> None:
    """The row fits the root's four-column table: slug first, the domain left to the operator,
    the brain key last. The output says what fails until the row is in (decision 5)."""
    out = CliRunner().invoke(
        main,
        [
            "new",
            "red-probe",
            "--description",
            "A disposable HTTP probe.",
            "--brain-key",
            "red_probe",
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
    row = next(line for line in out.output.splitlines() if line.startswith("| red-probe |"))
    cells = table_cells(row)
    assert cells is not None and len(cells) == len(ROSTER_HEADER)
    assert cells[:2] == ("red-probe", DOMAIN_PLACEHOLDER)
    assert cells[-1] == "`red_probe`"
    assert "`rail check` fails hygiene.roster_entry" in out.output


def test_a_pipe_in_the_description_keeps_the_row_as_wide_as_the_header() -> None:
    """Review focus 3: a `|` in the description is escaped, so the pasted row does not
    shift the root's columns, and the gate reads it back as one cell."""
    cells = table_cells(roster_row("red-probe", "reads a | b", "red-probe"))
    assert cells is not None and len(cells) == len(ROSTER_HEADER)
    assert cells[2] == "reads a \\| b"


@pytest.mark.parametrize("key", ["Red-Probe", "red.probe"], ids=["uppercase", "dotted"])
def test_new_refuses_a_brain_key_outside_the_pattern(
    template_dir: Path, tmp_path: Path, key: str
) -> None:
    """The key is rendered into `brain_session_start("<key>", …)` and checked by one regex
    (decision 8): the CLI callback and the copier validator refuse the same keys, and use
    the same pattern."""
    out = CliRunner().invoke(
        main,
        [
            "new",
            "red-probe",
            "--description",
            "x.",
            "--brain-key",
            key,
            "--no-remotes",
            "--dest",
            str(tmp_path / "cli"),
        ],
    )
    assert out.exit_code == 2 and "[a-z0-9][a-z0-9_-]*" in out.output
    assert not (tmp_path / "cli").exists()
    with pytest.raises(ScaffoldError, match="brain_key"):
        render(_project(template_dir, tmp_path / "copier", brain_key=key))
    assert BRAIN_KEY.pattern in (ROOT / "copier.yml").read_text()


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

    questions = yaml.safe_load((ROOT / "copier.yml").read_text())
    assert set(questions["deploy_target"]["choices"]) == {t.value for t in DeployTarget}


def test_every_stack_the_cli_offers_is_a_copier_choice() -> None:
    """`rail new --stack` offers every `Stack`, and copier must accept exactly those: today a
    drift fails only when `rail new --stack X` runs, inside copier. Spec B adds `rust` to both
    or this fails (spec 2026-09-24-template-alignment, decision 12)."""
    import yaml

    questions = yaml.safe_load((ROOT / "copier.yml").read_text())
    assert set(questions["stack"]["choices"]) == {s.value for s in Stack}


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


@pytest.mark.parametrize(
    "ref",
    ["0123456789abcdef0123456789abcdef01234567", "1234567890" * 4, "main"],
    ids=["hex-sha", "all-digit-sha", "main"],
)
def test_render_forwards_the_pin_to_rail_ref(template_dir: Path, tmp_path: Path, ref: str) -> None:
    """The workflow called at `ref` must install the rail at `ref` too, or the gates float on
    rail-ci's `main` default while the workflow reads as pinned (ticket 2a6781cb). Read as
    GitHub reads it, parsed: an unquoted all-digit SHA would be a number, not a ref."""
    import yaml

    dest = render(_project(template_dir, tmp_path / "red-beta", slug="red-beta", rail_ref=ref))
    workflow = yaml.safe_load(
        (dest / ".github" / "workflows" / "continuous-integration.yml").read_text()
    )
    job = workflow["jobs"]["rail"]
    assert job["uses"].endswith(f"@{ref}")
    assert job["with"]["rail-ref"] == ref and isinstance(job["with"]["rail-ref"], str)


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


# -- rendered guidance (spec 2026-09-24-template-alignment) ------------------------------

PRIVATE_HEALTHCHECK = "http://192.0.2.10:9204/healthz"  # RFC 5737: typed, never assumed
BRAIN_TICKET = "04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f"
TARGET_FAMILIES = (DeployTarget.VPS_TRAEFIK.value, DeployTarget.PRIVATE_COMPOSE.value)


class Combo(NamedTuple):
    """One answer set of the template. Every guidance test reads the same renders."""

    stack: Stack
    tier: Tier
    ledger: LedgerBackend
    target: str | None = None  # prod only: one target per family, public and private
    brain_key: str = "red-probe"

    @property
    def label(self) -> str:
        parts = [self.stack.value, self.tier.value, self.ledger.value]
        parts += [self.target] if self.target else []
        parts += [self.brain_key] if self.brain_key != "red-probe" else []
        return "-".join(parts)


COMBOS = [
    Combo(stack, tier, ledger, target)
    for stack in Stack
    for tier in Tier
    for ledger in LedgerBackend
    for target in (TARGET_FAMILIES if tier is Tier.PROD else (None,))
] + [Combo(Stack.PYTHON, Tier.BOOTSTRAP, LedgerBackend.FILE, brain_key="red_probe")]
ROOT_TITLES = (
    "Workflows — the operator picks the method",
    "Invariants — true whatever the method",
)
STACK_LINE = {
    Stack.PYTHON: "Python 3.12+, uv, pytest, ruff.",
    Stack.GO: "Go 1.22+, `go test`, `go vet`, `gofmt`.",
    Stack.DOCS: "Documentation only (Markdown).",
}
STACK_CHAIN = re.compile(
    r"\{#-?\s*stack-chain:\s*(?P<name>[a-z-]+)\s*-?#\}(?P<body>.*?)\{#-?\s*/stack-chain\s*-?#\}",
    re.DOTALL,
)
STACK_CHAINS = {"CLAUDE.md.jinja": {"stack", "structure"}, "AGENTS.md.jinja": {"gates"}}
_ELSE = re.compile(r"\{%-?\s*else\s*-?%\}")
GUIDANCE = ("CLAUDE.md", "AGENTS.md")
STACK_GATES = {
    Stack.PYTHON: "`uv run pytest -q`",
    Stack.GO: "`go test -race -count=1 ./...`",
    Stack.DOCS: "no stack command of its own",
}
# D9's sections and D10's rows, in the order AGENTS.md must hold them
AGENTS_ORDER = (
    "Read `CLAUDE.md` first",
    'names as "Parent project"',
    "the `graph` method is **unavailable**",
    "A pre-review never satisfies the review gate",
    "## The invariants you must not break",
    "**Always: `rail check` is the verdict**",
    "**Always: the only bypass is a `gates:` override",
    "**With `ledger: file`:",
    "**With `ledger: brain`:",
    "**At `tier: prod`:",
    "**At `prod` with `deploy.target: vps-traefik`:",
    "**At `prod` with any other target:",
    "| project-specific: fill in |",
    "## Gates",
    "make ci        # exactly what CI runs",
    "rail check     # the rail's gates",
    "## Brain MCP",
    "never `brain_learn` by default",
    "## Subagents",
    "Every subagent prompt names its perimeter",
)


def _in_order(text: str, needles: tuple[str, ...]) -> None:
    position = 0
    for needle in needles:
        found = text.find(needle, position)
        assert found >= 0, f"missing, or out of order: {needle!r}"
        position = found + len(needle)


def _render_combo(template: Path, dest: Path, combo: Combo) -> Path:
    private = combo.target == DeployTarget.PRIVATE_COMPOSE
    return render(
        NewProject(
            slug="red-probe",
            description="A disposable HTTP probe.",
            tier=combo.tier,
            stack=combo.stack,
            brain_key=combo.brain_key,
            dest=dest,
            template=str(template),
            deploy_target=combo.target or DeployTarget.VPS_TRAEFIK.value,
            healthcheck=PRIVATE_HEALTHCHECK if private else None,
            ledger=combo.ledger,
            ticket=BRAIN_TICKET if combo.ledger is LedgerBackend.BRAIN else None,
        )
    )


@pytest.fixture(scope="module")
def renders(tmp_path_factory: pytest.TempPathFactory) -> dict[Combo, Path]:
    """Every combination rendered once for the module, from a copy of the working tree's
    template (uncommitted edits included, as with `template_dir`)."""
    base = tmp_path_factory.mktemp("renders")
    template = base / "template-src"
    template.mkdir()
    shutil.copy(ROOT / "copier.yml", template / "copier.yml")
    shutil.copytree(ROOT / "template", template / "template")
    return {combo: _render_combo(template, base / combo.label, combo) for combo in COMBOS}


@pytest.mark.parametrize("combo", COMBOS, ids=[c.label for c in COMBOS])
def test_rendered_guidance_points_at_the_root(renders: dict[Combo, Path], combo: Combo) -> None:
    """The method, the review and the invariants that hold whatever the method live once, in
    the ReD root: `CLAUDE.md` points at their sections and copies neither (decisions 6-8).
    `AGENTS.md` carries what Codex cannot reach from a sub-project's git root, and points at
    the rest (decisions 9-10)."""
    key = combo.brain_key
    call = f'brain_session_start("{key}", client_key="<harness>-{key}-<YYYY-MM-DD>")'

    claude = (renders[combo] / "CLAUDE.md").read_text()
    for title in ROOT_TITLES:
        assert f'§ "{title}"' in claude, title
    assert "## Working principles" not in claude
    assert not re.search(r"^\|\s*`(?:spec|graph|direct)`\s*\|", claude, re.MULTILINE)
    table = "## Where things live\n\n| Question | Where to look |\n|---|---|\n"
    assert table in claude
    assert claude.index("## Project") < claude.index(table) < claude.index("## Language")
    assert "| What tier, which ledger, which target? | `rail.yaml` |" in claude
    assert f"`{call}`" in claude and claude.count("brain_session_start(") == 1
    lesson = f'brain_learn(topic, insight, project_key="{key}")'
    assert claude.index("## Brain MCP") < claude.index(lesson)
    assert f"## Stack\n\n{STACK_LINE[combo.stack]}\n\n## Commands" in claude
    assert "stack-chain" not in claude

    agents = (renders[combo] / "AGENTS.md").read_text()
    _in_order(agents, AGENTS_ORDER)
    assert f"`{call}`" in agents and agents.count("brain_session_start(") == 1
    assert agents.index("## Brain MCP") < agents.index(call)
    assert STACK_GATES[combo.stack] in agents
    assert "../../AGENTS.md" not in agents and "~/" not in agents
    assert "stack-chain" not in agents
    for target in DeployTarget:
        if target is not DeployTarget.VPS_TRAEFIK:
            assert target.value not in agents, f"AGENTS.md names the private {target.value}"


def test_agents_md_does_not_depend_on_tier_ledger_or_target(renders: dict[Combo, Path]) -> None:
    """Copier answers freeze at scaffold time and nothing re-answers `tier` on promotion, so
    the invariant rows are unconditional, each prefixed by the value it applies to (decision
    10): for one stack and one key, one `AGENTS.md`, whatever the tier, ledger and target."""
    texts: dict[tuple[Stack, str], set[str]] = {}
    for combo, dest in renders.items():
        texts.setdefault((combo.stack, combo.brain_key), set()).add(
            (dest / "AGENTS.md").read_text()
        )
    assert {stack for stack, _ in texts} == set(Stack)
    varying = sorted(f"{s.value}/{key}" for (s, key), seen in texts.items() if len(seen) != 1)
    assert not varying, f"AGENTS.md varies with tier, ledger or target for {varying}"


def test_every_stack_chain_names_every_stack() -> None:
    """A render cannot see a missing stack branch (§ Structure and § Gates always render
    stack-independent lines). Each marked chain names every `Stack` member and has no
    catch-all `else`, so spec B's `rust` is refused chain by chain until it is added
    (decision 11). This test reads the template source on purpose."""
    for source, expected in STACK_CHAINS.items():
        text = (ROOT / "template" / "project" / source).read_text()
        chains = {m["name"]: m["body"] for m in STACK_CHAIN.finditer(text)}
        assert set(chains) == expected, f"{source}: marked chains {sorted(chains)}"
        for name, body in chains.items():
            missing = [s.value for s in Stack if f"stack == '{s.value}'" not in body]
            assert not missing, f"{source}, chain {name!r}: no branch for {missing}"
            assert not _ELSE.search(body), f"{source}, chain {name!r}: a catch-all else"
