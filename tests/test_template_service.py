"""A prod/python scaffold is a deployable service on day 0: its own tests pass, its image
is pinned by digest and runs as uid 10001, its compose publishes no port and carries the
Traefik labels of the border VPS."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rail.model import Stack, Tier
from rail.scaffold import NewProject, render

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def template_dir(tmp_path: Path) -> Path:
    src = tmp_path / "template-src"
    src.mkdir()
    shutil.copy(ROOT / "copier.yml", src / "copier.yml")
    shutil.copytree(ROOT / "template", src / "template")
    return src


def _render(template: Path, dest: Path, tier: Tier) -> Path:
    return render(
        NewProject(
            slug="red-probe",
            description="A disposable HTTP probe.",
            tier=tier,
            stack=Stack.PYTHON,
            brain_key="red-probe",
            dest=dest,
            template=str(template),
        )
    )


def test_bootstrap_python_has_no_service_files(template_dir: Path, tmp_path: Path) -> None:
    dest = _render(template_dir, tmp_path / "red-probe", Tier.BOOTSTRAP)
    assert not (dest / "src" / "red_probe" / "service.py").exists()
    assert not (dest / "Dockerfile").exists() and not (dest / "deploy").exists()


def test_prod_python_renders_a_service_whose_own_tests_pass(
    template_dir: Path, tmp_path: Path
) -> None:
    dest = _render(template_dir, tmp_path / "red-probe", Tier.PROD)
    for rel in (
        "src/red_probe/service.py",
        "src/red_probe/__main__.py",
        "tests/test_service.py",
        "Dockerfile",
        ".dockerignore",
        "deploy/compose.yaml",
    ):
        assert (dest / rel).is_file(), rel
    env = {**os.environ, "PYTHONPATH": str(dest / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(dest / "tests")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_image_and_the_stack_follow_the_border_conventions(
    template_dir: Path, tmp_path: Path
) -> None:
    dest = _render(template_dir, tmp_path / "red-probe", Tier.PROD)
    dockerfile = (dest / "Dockerfile").read_text()
    assert "FROM python:3.12-slim@sha256:" in dockerfile
    assert "USER 10001:10001" in dockerfile and "ARG GIT_SHA" in dockerfile
    assert 'CMD ["python", "-m", "red_probe"]' in dockerfile
    compose = (dest / "deploy" / "compose.yaml").read_text()
    assert "ports:" not in compose
    assert "image: ${IMAGE_REFERENCE:?" in compose
    assert "APP_IMAGE_DIGEST: ${IMAGE_DIGEST:?" in compose
    assert "traefik.http.routers.red-probe.rule=Host(`${DOMAIN:?" in compose
    assert "traefik.http.routers.red-probe.tls.certresolver=${TRAEFIK_CERT_RESOLVER:?" in compose
    assert "traefik.http.services.red-probe.loadbalancer.server.port=8080" in compose
    assert "name: ${TRAEFIK_NETWORK:?" in compose
    assert "read_only: true" in compose and "no-new-privileges:true" in compose
    makefile = (dest / "Makefile").read_text()
    assert "\nserve:\n" in makefile and "\nimage:\n" in makefile
    assert "/version" in (dest / "CLAUDE.md").read_text()
