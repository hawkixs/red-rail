"""Reviewer receipts travel with a file-ledger release without altering either ledger."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from rail import gitrepo
from rail.cli import main
from rail.commands import release as release_command
from rail.ledger import RECEIPTS_DIR, AttestationKind, Record, RecordKind
from rail.ledger.file import FileLedger
from rail.release import ReleasePlan
from rail.reviewer import config as reviewer_config
from tests.helpers import write_manifest
from tests.test_release import DIGEST


def _receipt(repo: Path, name: str, kind: AttestationKind = AttestationKind.REVIEW_VERDICT) -> Path:
    record = Record.build(
        kind=RecordKind.ATTESTATION,
        project="red-example",
        issuer="red-rail-reviewer",
        idempotency_key=name,
        payload={"kind": kind.value, "data": {"summary": "Verified evidence"}},
        recorded_at=datetime(2026, 10, 14, tzinfo=UTC),
    )
    path = repo / RECEIPTS_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    # Formatting and CRLF are deliberately different from FileLedger's serialization.
    path.write_bytes((record.model_dump_json(indent=4) + "\r\n").encode())
    return path


@pytest.fixture
def checkouts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    repo = tmp_path / "release-checkout"
    source = tmp_path / "reviewer-checkout"
    repo.mkdir()
    source.mkdir()
    write_manifest(repo, project="red-example", tier="prod", deploy=True)
    (repo / "rail.yaml").write_text((repo / "rail.yaml").read_text() + "ledger: file\n")
    write_manifest(source, project="red-example", tier="prod", deploy=True)
    config = tmp_path / "reviewer.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "app_id": 1,
                "installation_id": 2,
                "private_key_file": str(tmp_path / "unused-key.pem"),
                "repositories": [{"slug": "example/red-example", "path": str(source)}],
            }
        )
    )
    config.chmod(0o600)
    load = reviewer_config.load_reviewer_config
    monkeypatch.setattr(reviewer_config, "load_reviewer_config", lambda: load(config))
    monkeypatch.setattr(
        gitrepo, "remotes", lambda repo: {"origin": "git@github.com:example/red-example.git"}
    )
    plan = ReleasePlan(
        project="red-example",
        version="0.1.0",
        sha="a" * 40,
        image_repository="ghcr.io/example/red-example",
        platform="linux/amd64",
        previous_tag=None,
        changelog=("feat: deliver the example",),
    )
    monkeypatch.setattr(release_command, "preflight", lambda *args, **kwargs: plan)
    monkeypatch.setattr(release_command, "login", lambda *args, **kwargs: None)
    monkeypatch.setattr(release_command, "build_and_push", lambda *args, **kwargs: DIGEST)
    monkeypatch.setattr(release_command, "tag_and_push", lambda *args, **kwargs: None)
    return repo, source, config


def _invoke(repo: Path, *options: str):
    return CliRunner().invoke(
        main, ["release", "--repo", str(repo), "--version", "0.1.0", "--yes", *options]
    )


def test_sweep_preserves_bytes_and_source_and_is_idempotent(checkouts) -> None:
    from rail.ledger.sweep import sweep_receipts

    repo, source, _ = checkouts
    verdict = _receipt(source, "verdict.json")
    ruling = _receipt(source, "ruling.json", AttestationKind.REVIEW_RULING)
    (source / RECEIPTS_DIR / "notes.txt").write_text("Not a receipt")
    before = {p.name: p.read_bytes() for p in (source / RECEIPTS_DIR).iterdir()}
    report = sweep_receipts(source, repo)
    assert report.copied == ("ruling.json", "verdict.json")
    assert report.counts == {"copied": 2, "already_present": 0, "refused": 0, "conflicts": 0}
    for path in (verdict, ruling):
        assert (repo / RECEIPTS_DIR / path.name).read_bytes() == before[path.name]
    assert {p.name: p.read_bytes() for p in (source / RECEIPTS_DIR).iterdir()} == before
    again = sweep_receipts(source, repo)
    assert again.copied == ()
    assert again.already_present == ("ruling.json", "verdict.json")
    assert not (repo / RECEIPTS_DIR / "notes.txt").exists()


def test_sweep_refuses_tampered_invalid_and_unreadable_receipts(checkouts, monkeypatch) -> None:
    from rail.ledger.sweep import sweep_receipts

    repo, source, _ = checkouts
    tampered = _receipt(source, "tampered.json")
    tampered.write_bytes(tampered.read_bytes().replace(b"Verified evidence", b"Altered evidence"))
    (source / RECEIPTS_DIR / "invalid.json").write_text("not JSON")
    (source / RECEIPTS_DIR / "encoding.json").write_bytes(b"\xff")
    unreadable = _receipt(source, "unreadable.json")
    read = Path.read_bytes

    def read_bytes(path):
        if path == unreadable:
            raise PermissionError("Cannot read receipt")
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    report = sweep_receipts(source, repo)
    assert report.refused == ("encoding.json", "invalid.json", "tampered.json", "unreadable.json")
    assert report.copied == ()
    assert not (repo / RECEIPTS_DIR).exists()


def test_sweep_never_overwrites_conflicts_including_dangling_links(checkouts) -> None:
    from rail.ledger.sweep import sweep_receipts

    repo, source, _ = checkouts
    _receipt(source, "conflict.json")
    _receipt(source, "link.json")
    destination = repo / RECEIPTS_DIR
    destination.mkdir(parents=True)
    (destination / "conflict.json").write_bytes(b"Existing evidence")
    (destination / "link.json").symlink_to("absent.json")
    report = sweep_receipts(source, repo)
    assert report.conflicts == ("conflict.json", "link.json")
    assert report.copied == ()
    assert (destination / "conflict.json").read_bytes() == b"Existing evidence"
    assert (destination / "link.json").is_symlink()


def test_sweep_without_source_receipts_creates_nothing(checkouts) -> None:
    from rail.ledger.sweep import sweep_receipts

    repo, source, _ = checkouts
    assert sweep_receipts(source, repo).counts == {
        "copied": 0,
        "already_present": 0,
        "refused": 0,
        "conflicts": 0,
    }
    assert not (repo / RECEIPTS_DIR).exists()


def test_release_sweeps_after_attestation_and_reports_all_outcomes(checkouts, monkeypatch) -> None:
    from rail.ledger import sweep

    repo, source, _ = checkouts
    _receipt(source, "verdict.json")
    _receipt(source, "ruling.json", AttestationKind.REVIEW_RULING)
    _receipt(source, "present.json")
    _receipt(repo, "present.json")
    _receipt(source, "conflict.json")
    conflict = _receipt(repo, "conflict.json")
    conflict.write_bytes(conflict.read_bytes() + b"\n")
    (source / RECEIPTS_DIR / "bad.json").write_text("not JSON")
    original = sweep.sweep_receipts

    def after_attestation(source, target, **kwargs):
        receipts = list((target / RECEIPTS_DIR).glob("*-released-*.json"))
        assert len(receipts) == 1, "The released receipt must exist before sweeping"
        return original(source, target, **kwargs)

    monkeypatch.setattr(sweep, "sweep_receipts", after_attestation)
    out = _invoke(repo)
    assert out.exit_code == 0, out.output
    assert (
        "2 reviewer receipt(s) swept from reviewer-checkout into docs/receipts "
        "(1 already present, 1 refused, 1 conflicts); commit them with the released "
        "receipt in the receipts PR (decision c8b0ea45)"
    ) in out.stdout
    assert "refused: bad.json" in out.stderr
    assert "conflict: conflict.json" in out.stderr
    assert str(source) not in out.output and str(repo) not in out.output
    assert (repo / RECEIPTS_DIR / "verdict.json").read_bytes() == (
        source / RECEIPTS_DIR / "verdict.json"
    ).read_bytes()


@pytest.mark.parametrize("as_json", [False, True])
def test_plan_counts_without_writes_or_attestation(checkouts, as_json) -> None:
    repo, source, _ = checkouts
    _receipt(source, "verdict.json")
    _receipt(source, "present.json")
    _receipt(repo, "present.json")
    _receipt(source, "conflict.json")
    conflict = _receipt(repo, "conflict.json")
    conflict.write_bytes(conflict.read_bytes() + b"\n")
    (source / RECEIPTS_DIR / "bad.json").write_text("not JSON")
    before = {p.name: p.read_bytes() for p in (repo / RECEIPTS_DIR).iterdir()}
    out = _invoke(repo, "--plan", *(["--json"] if as_json else []))
    assert out.exit_code == 0, out.output
    if as_json:
        report = json.loads(out.stdout)["sweep"]
        assert report["copied"] == 1
        assert report["already_present"] == report["refused"] == report["conflicts"] == 1
    else:
        assert "1 reviewer receipt(s) would be swept from reviewer-checkout" in out.stdout
        assert "1 already present, 1 refused, 1 conflicts" in out.stdout
    assert {p.name: p.read_bytes() for p in (repo / RECEIPTS_DIR).iterdir()} == before
    assert "refused: bad.json" in out.stderr and "conflict: conflict.json" in out.stderr
    assert str(source) not in out.output


def test_json_release_includes_counts_and_names_and_keeps_record_shape(checkouts) -> None:
    repo, source, _ = checkouts
    _receipt(source, "verdict.json")
    (source / RECEIPTS_DIR / "bad.json").write_text("not JSON")
    out = _invoke(repo, "--json")
    assert out.exit_code == 0, out.output
    body = json.loads(out.stdout)
    assert body["payload"]["data"]["digest"] == DIGEST
    assert body["sweep"]["copied"] == 1 and body["sweep"]["refused"] == 1
    assert body["sweep"]["files"]["copied"] == ["verdict.json"]
    assert body["sweep"]["files"]["refused"] == ["bad.json"]
    assert body["sweep"]["source"] == "reviewer-checkout"
    assert "refused: bad.json" in out.stderr
    assert str(source) not in out.output


def test_plan_does_not_create_the_target_receipts_directory(checkouts) -> None:
    repo, source, _ = checkouts
    _receipt(source, "verdict.json")
    out = _invoke(repo, "--plan")
    assert out.exit_code == 0, out.output
    assert "1 reviewer receipt(s) would be swept" in out.stdout
    assert not (repo / RECEIPTS_DIR).exists()


@pytest.mark.parametrize(
    "situation",
    ["missing", "malformed", "invalid", "scalar", "no-entry", "same", "alias", "not-private"],
)
@pytest.mark.parametrize("as_json", [False, True])
def test_unavailable_or_same_checkout_does_not_fail_release(checkouts, situation, as_json) -> None:
    repo, source, config = checkouts
    _receipt(source, "verdict.json")
    if situation == "missing":
        config.unlink()
    elif situation == "malformed":
        config.write_text("repositories: [")
    elif situation == "invalid":
        config.write_text("repositories: []")
    elif situation == "scalar":
        config.write_text("not a mapping")
    elif situation == "not-private":
        config.chmod(0o644)
    else:
        raw = yaml.safe_load(config.read_text())
        if situation == "no-entry":
            raw["repositories"][0]["slug"] = "example/red-other"
        else:
            path = repo
            if situation == "alias":
                path = repo.parent / "release-alias"
                path.symlink_to(repo, target_is_directory=True)
            raw["repositories"][0]["path"] = str(path)
        config.write_text(yaml.safe_dump(raw))
    out = _invoke(repo, *(["--json"] if as_json else []))
    assert out.exit_code == 0, out.output
    assert "nothing to sweep" in (out.stderr if as_json else out.stdout)
    if as_json:
        assert json.loads(out.stdout)["sweep"]["copied"] == 0
    assert not (repo / RECEIPTS_DIR / "verdict.json").exists()
    assert str(source) not in out.output and str(config) not in out.output


@pytest.mark.parametrize(
    "origin",
    [
        "git@github.com:example/red-example.git",
        "https://github.com/example/red-example.git",
        "ssh://git@github.com/example/red-example.git",
    ],
)
def test_lookup_uses_origin_github_slug(checkouts, monkeypatch, origin) -> None:
    repo, source, _ = checkouts
    _receipt(source, "verdict.json")
    monkeypatch.setattr(
        gitrepo,
        "remotes",
        lambda repo: {"other": "git@github.com:example/red-other.git", "origin": origin},
    )
    out = _invoke(repo)
    assert out.exit_code == 0, out.output
    assert (repo / RECEIPTS_DIR / "verdict.json").exists()


def test_non_github_origin_does_not_sweep_another_github_remote(checkouts, monkeypatch) -> None:
    repo, source, _ = checkouts
    _receipt(source, "verdict.json")
    monkeypatch.setattr(
        gitrepo,
        "remotes",
        lambda repo: {
            "other": "git@github.com:example/red-example.git",
            "origin": "https://example.invalid/example/red-example.git",
        },
    )
    out = _invoke(repo)
    assert out.exit_code == 0, out.output
    assert "nothing to sweep" in out.stdout
    assert not (repo / RECEIPTS_DIR / "verdict.json").exists()


@pytest.mark.parametrize("plan", [False, True])
def test_brain_ledger_does_not_load_config_or_sweep(checkouts, monkeypatch, plan) -> None:
    repo, source, _ = checkouts
    manifest = repo / "rail.yaml"
    manifest.write_text(
        manifest.read_text().replace("ledger: file", "ledger: brain")
        + "ticket: 00000000-0000-4000-8000-000000000001\n"
    )
    _receipt(source, "verdict.json")
    ledger = FileLedger(repo.parent / "fake-brain")
    monkeypatch.setattr(release_command, "open_ledger", lambda repo: ledger)

    def forbidden():
        pytest.fail("Brain releases must not load reviewer configuration")

    monkeypatch.setattr(reviewer_config, "load_reviewer_config", forbidden)
    out = _invoke(repo, "--json", *(["--plan"] if plan else []))
    assert out.exit_code == 0, out.output
    if not plan:
        assert "sweep" not in json.loads(out.stdout)
    assert "reviewer receipt" not in out.output
    assert not (repo / RECEIPTS_DIR).exists()
