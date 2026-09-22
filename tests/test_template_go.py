"""A Go scaffold's Makefile reaches the toolchain through `$(GO)` alone, and its format check
fails whenever the formatter cannot vouch for the tree — a file it lists, or a run that never
happened. Exercised with the real `make` and a stand-in `go`: what is under test is how the
recipes read the tool's answer, not the tool."""

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from rail.model import Stack, Tier
from rail.scaffold import NewProject, render

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    src = tmp_path / "template-src"
    src.mkdir()
    shutil.copy(ROOT / "copier.yml", src / "copier.yml")
    shutil.copytree(ROOT / "template", src / "template")
    return render(
        NewProject(
            slug="red-gopher",
            description="A disposable Go service.",
            tier=Tier.BOOTSTRAP,
            stack=Stack.GO,
            brain_key="red-gopher",
            dest=tmp_path / "red-gopher",
            template=str(src),
        )
    )


def _go(bin_dir: Path, *, fmt_lists: str = "", fmt_status: int = 0) -> Path:
    """A stand-in `go`. `fmt` prints `fmt_lists` and exits `fmt_status` — the real one lists
    the files it rewrote, and exits non-zero when it cannot run gofmt at all. Every other
    subcommand succeeds, so a target that fails can only be failing on the format check."""
    bin_dir.mkdir()
    go = bin_dir / "go"
    go.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = fmt ]; then\n'
        f"  printf '%s' {shlex.quote(fmt_lists)}\n"
        f"  exit {fmt_status}\n"
        "fi\n"
        "exit 0\n"
    )
    go.chmod(0o755)
    return go


def _make(project: Path, *args: str, path: Path) -> subprocess.CompletedProcess[str]:
    """`make` with `path` as the whole PATH: no toolchain of the host answers in place of the
    stand-in, whatever the host has installed."""
    make = shutil.which("make")
    assert make, "make is required: the Makefile is exercised, not read"
    return subprocess.run(
        [make, *args],
        cwd=project,
        env={"PATH": str(path)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_lint_fails_and_names_a_file_the_formatter_lists(project: Path, tmp_path: Path) -> None:
    """A host where `go` answers and `gofmt` is not on the PATH, as on the red-alerts
    workstation: the unformatted file must still fail the check, because the toolchain that
    formats is the one that answers, not a second binary the PATH may lack."""
    bin_dir = tmp_path / "bin"
    _go(bin_dir, fmt_lists="main.go\n")

    result = _make(project, "lint", path=bin_dir)

    assert result.returncode != 0
    assert "main.go" in result.stdout


def test_lint_fails_when_the_formatter_cannot_run(project: Path, tmp_path: Path) -> None:
    """A formatter that cannot run lists nothing, and an empty list must not read as a
    formatted tree: the status of the run is checked before its output."""
    bin_dir = tmp_path / "bin"
    _go(bin_dir, fmt_status=2)

    result = _make(project, "lint", path=bin_dir)

    assert result.returncode != 0


@pytest.mark.parametrize("target", ["sync", "lint", "test", "vuln"])
def test_every_target_reaches_the_toolchain_through_the_go_variable(
    target: str, project: Path, tmp_path: Path
) -> None:
    """A host with no Go toolchain runs the targets through a wrapper, `make GO=<wrapper>` —
    a container, for instance. The PATH here holds no `go` at all, so a recipe that calls the
    bare binary fails; and a formatter that lists nothing lets `lint` through."""
    go = _go(tmp_path / "toolchain")
    empty = tmp_path / "empty"
    empty.mkdir()

    result = _make(project, target, f"GO={go}", path=empty)

    assert result.returncode == 0, result.stdout + result.stderr
