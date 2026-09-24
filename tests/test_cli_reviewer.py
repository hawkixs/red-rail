"""`rail reviewer once`: private config, explicit errors, no network in tests."""

from pathlib import Path

from click.testing import CliRunner

from rail.cli import main


def _config(tmp_path: Path, mode: int = 0o600) -> Path:
    key = tmp_path / "app.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    key.chmod(0o600)
    path = tmp_path / "reviewer.yaml"
    path.write_text(
        f"app_id: 1\ninstallation_id: 2\nprivate_key_file: {key}\n"
        f"repositories:\n  - slug: hawkixs/red-alpha\n    path: {tmp_path / 'red-alpha'}\n"
    )
    path.chmod(mode)
    return path


def test_once_refuses_a_world_readable_config(tmp_path: Path) -> None:
    out = CliRunner().invoke(main, ["reviewer", "once", "--config", str(_config(tmp_path, 0o644))])
    assert out.exit_code == 1 and "mode 644" in out.output


def test_pr_needs_repository(tmp_path: Path) -> None:
    out = CliRunner().invoke(
        main, ["reviewer", "once", "--config", str(_config(tmp_path)), "--pr", "7"]
    )
    assert out.exit_code == 2 and "--pr needs --repository" in out.output


def test_once_reports_the_error_of_an_unreadable_checkout(tmp_path: Path, monkeypatch) -> None:
    class NoGitHub:
        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr("rail.reviewer.github.GitHubApp", NoGitHub)
    out = CliRunner().invoke(main, ["reviewer", "once", "--config", str(_config(tmp_path))])
    assert out.exit_code != 0 and "rail.yaml" in out.output


def test_once_refuses_a_repository_the_config_does_not_watch(tmp_path: Path, monkeypatch) -> None:
    """Reported by red-arena (c964c791): a slug missing from reviewer.yaml used to review
    nothing and exit 0, which reads as "nothing to review" rather than "not watched"."""

    class NoGitHub:
        def __init__(self, **kwargs):
            raise AssertionError("an unwatched repository must be refused before GitHub")

    monkeypatch.setattr("rail.reviewer.github.GitHubApp", NoGitHub)
    config = str(_config(tmp_path))
    unwatched = ["reviewer", "once", "--config", config, "--repository", "hawkixs/red-beta"]
    for extra in ([], ["--pr", "7"]):
        out = CliRunner().invoke(main, [*unwatched, *extra])
        assert out.exit_code == 2, out.output
        assert "not watched by" in out.output and "reviewer.yaml" in out.output
        assert "hawkixs/red-alpha" in out.output
        assert "reviewed 0" not in out.output
