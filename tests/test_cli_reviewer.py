"""`rail reviewer once`: private config, explicit errors, no network in tests."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.ledger import RECEIPTS_DIR, AttestationKind
from rail.ledger.file import FileLedger
from rail.reviewer.verdict import Finding, ReviewVerdict
from tests.helpers import conforming_tree


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


def _awaiting_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    ledger = FileLedger(repo / RECEIPTS_DIR)
    blocker = Finding.model_validate({"severity": "blocking", "file": "a.py", "title": "t",
                                      "evidence": "e", "id": "F-7-1", "class": "blocker",
                                      "status": "still_open"})
    for n in (1, 2, 3):
        data = ReviewVerdict(verdict="request_changes", summary="s", findings=[blocker],
                             round=n, artifact="code").as_attestation_data(
            sha=str(n) * 40, check_run_id=n, repository="hawkixs/red-alpha", pr=7)
        ledger.attest("red-alpha", AttestationKind.REVIEW_VERDICT, data,
                      issuer="red-rail-reviewer", idempotency_key=f"review_verdict:{n}")
    key = tmp_path / "app.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    key.chmod(0o600)
    config = tmp_path / "reviewer.yaml"
    config.write_text(
        f"app_id: 1\ninstallation_id: 2\nprivate_key_file: {key}\n"
        f"repositories:\n  - slug: hawkixs/red-alpha\n    path: {repo}\n"
    )
    config.chmod(0o600)
    return repo, config


def _rule(config: Path, finding: str = "F-7-1", decision: str = "rename it", confirm: str = "F-7-1",
          tty: bool = True, monkeypatch=None, extra=()):
    if monkeypatch is not None:
        monkeypatch.setattr("rail.commands.reviewer._interactive", lambda: tty)
    args = ["reviewer", "rule", "--config", str(config), "--repository", "hawkixs/red-alpha",
            "--pr", "7", "--finding", finding, "--as", "fix", "--decision", decision, *extra]
    return CliRunner().invoke(main, args, input=f"{confirm}\n")


def test_rule_writes_one_ruling_after_typed_confirmation(tmp_path: Path, monkeypatch) -> None:
    repo, config = _awaiting_repo(tmp_path)
    monkeypatch.delenv("CI", raising=False)
    out = _rule(config, monkeypatch=monkeypatch)
    assert out.exit_code == 0, out.output
    rulings = FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.REVIEW_RULING
    )
    assert len(rulings) == 1 and rulings[0].issuer == "operator"
    assert rulings[0].idempotency_key == "review_ruling:hawkixs/red-alpha#7:F-7-1:1"
    assert rulings[0].data["decision"] == "rename it"


@pytest.mark.parametrize(
    ("kwargs", "env_ci", "needle"),
    [
        ({"finding": "F-7-9"}, False, "not an open blocker awaiting a ruling"),
        ({"decision": " "}, False, "decision"),
        ({"confirm": "F-7-2"}, False, "confirmation"),
        ({"tty": False}, False, "terminal"),
        ({}, True, "CI"),
    ],
)
def test_rule_refuses_without_writing(tmp_path: Path, monkeypatch, kwargs, env_ci, needle) -> None:
    repo, config = _awaiting_repo(tmp_path)
    if env_ci:
        monkeypatch.setenv("CI", "true")
    else:
        monkeypatch.delenv("CI", raising=False)
    out = _rule(config, monkeypatch=monkeypatch, **kwargs)
    assert out.exit_code == 2 and needle in out.output
    assert not FileLedger(repo / RECEIPTS_DIR).list(
        "red-alpha", attestation=AttestationKind.REVIEW_RULING
    )


def test_rule_writes_a_review_ruling_in_brain_mode(tmp_path: Path, monkeypatch) -> None:
    """The command's ledger comes from `open_ledger`, brain included (spec 2026-09-25, D9):
    it never assumes the file ledger."""
    from rail.brain.client import BrainClient
    from rail.ledger import Contract, Deliverable, open_ledger
    from tests.fake_brain import FakeBrain

    repo = conforming_tree(tmp_path, "red-alpha", "dev")
    ticket = "04bc1f4a-3c21-48eb-86bb-c3f3279a9c9f"
    (repo / "rail.yaml").write_text(
        (repo / "rail.yaml").read_text().replace(
            "ledger: file\n", f"ledger: brain\nticket: {ticket}\n"
        )
    )
    brain = FakeBrain(agent="operator")
    brain.add_ticket("red", "red-alpha", ticket)
    brain.register_repository("red-alpha", 4243, "hawkixs/red-alpha")
    ledger = open_ledger(repo, client=BrainClient.in_memory(brain, agent="operator"))
    ledger.contract_set(
        "red-alpha",
        Contract(
            objective="ship red-alpha",
            deliverables=[
                Deliverable(
                    key="main",
                    repository="hawkixs/red-alpha",
                    repository_id=4243,
                    no_checks_reason="fixture: no check declared",
                )
            ],
        ),
        reason="bootstrap",
        issuer="op",
        idempotency_key="c0",
    )
    blocker = Finding.model_validate({"severity": "blocking", "file": "a.py", "title": "t",
                                      "evidence": "e", "id": "F-7-1", "class": "blocker",
                                      "status": "still_open"})
    for n in (1, 2, 3):
        data = ReviewVerdict(verdict="request_changes", summary="s", findings=[blocker],
                             round=n, artifact="code").as_attestation_data(
            sha=str(n) * 40, check_run_id=n, repository="hawkixs/red-alpha", pr=7)
        ledger.attest("red-alpha", AttestationKind.REVIEW_VERDICT, data,
                      issuer="red-rail-reviewer", idempotency_key=f"review_verdict:{n}")

    key = tmp_path / "app.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    key.chmod(0o600)
    config = tmp_path / "reviewer.yaml"
    config.write_text(
        f"app_id: 1\ninstallation_id: 2\nprivate_key_file: {key}\n"
        f"repositories:\n  - slug: hawkixs/red-alpha\n    path: {repo}\n"
    )
    config.chmod(0o600)

    monkeypatch.setattr("rail.commands.reviewer._interactive", lambda: True)
    monkeypatch.setattr("rail.commands.reviewer.open_ledger", lambda *_a, **_k: ledger)
    monkeypatch.delenv("CI", raising=False)
    out = CliRunner().invoke(
        main,
        ["reviewer", "rule", "--config", str(config), "--repository", "hawkixs/red-alpha",
         "--pr", "7", "--finding", "F-7-1", "--as", "fix", "--decision", "rename it"],
        input="F-7-1\n",
    )
    assert out.exit_code == 0, out.output
    rulings = [a for a in brain.attestations if a["kind"] == "review_ruling"]
    assert len(rulings) == 1
    assert rulings[0]["payload"]["finding"] == "F-7-1"
    assert rulings[0]["issuer_identity"] == "operator"
