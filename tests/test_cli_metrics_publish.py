"""Publication is read-only, partial on read failures, and atomic on contract failures."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner, Result

from rail.autonomy import SINCE, MergedPull
from rail.brain.settings import DEFAULT_REVIEWER_TOKEN_FILE
from rail.cli import main
from rail.commands import metrics as command_module
from rail.ledger import AttestationKind, LedgerError, Record, RecordKind, open_ledger
from rail.ledger.file import FileLedger
from rail.metrics import Conformance, Metrics
from rail.metrics_contract import MAX_BYTES, Answer
from rail.reviewer.config import DEFAULT_CONFIG
from rail.reviewer.github import GitHubApp, GitHubError

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self) -> None:
        self.value = NOW
        self.calls = 0
        self.events: list[str] = []

    def advance(self, event: str) -> None:
        self.events.append(event)
        self.value += timedelta(seconds=1)

    def __call__(self) -> datetime:
        self.calls += 1
        self.advance("clock")
        return self.value


class ReadLedger:
    def __init__(self, records: list[Record]) -> None:
        self.records = records

    def list(
        self,
        project: str,
        *,
        kind: RecordKind | None = None,
        attestation: AttestationKind | None = None,
    ) -> list[Record]:
        return [
            record
            for record in self.records
            if record.project == project
            and (kind is None or record.kind == kind)
            and (attestation is None or record.attestation == attestation)
        ]

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected ledger operation: {name}")


class ReadGitHub:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.fail: set[str] = set()
        self.inactive: set[str] = set()
        self.closed = False

    def default_branch(self, repository: str) -> str:
        self.clock.advance(f"branch:{repository}")
        if repository in self.fail:
            raise GitHubError("forbidden", status=403)
        return "main"

    def merged_pulls(self, repository: str, *, base: str, since: datetime) -> list[MergedPull]:
        assert base == "main" and since == SINCE
        self.clock.advance(f"pulls:{repository}")
        if repository in self.inactive:
            return []
        return [MergedPull(1, NOW - timedelta(days=1), NOW - timedelta(days=2))]

    def commits_since(
        self, repository: str, *, branch: str, since: datetime
    ) -> list[tuple[datetime, str]]:
        assert branch == "main" and since == SINCE
        self.clock.advance(f"commits:{repository}")
        if repository in self.inactive:
            return []
        return [(NOW, 'Revert "change"\nbody'), (NOW, 'fix: mention Revert "change"')]

    def close(self) -> None:
        self.closed = True

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected GitHub operation: {name}")


def manifest(path: Path, project: str, tier: str = "prod") -> None:
    path.mkdir()
    path.joinpath("rail.yaml").write_text(
        f"rail: 1\nproject: {project}\nbrain_key: {project}\ntier: {tier}\nstack: python\n"
        + (
            "deploy:\n  target: private-compose\n  healthcheck: http://127.0.0.1/health\n"
            if tier == "prod"
            else ""
        )
    )


def released(project: str) -> Record:
    return Record.build(
        kind=RecordKind.ATTESTATION,
        project=project,
        issuer="operator",
        idempotency_key="release:1",
        payload={"kind": "released", "data": {}},
        recorded_at=NOW - timedelta(days=1),
    )


@pytest.fixture
def publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    clock = Clock()
    github = ReadGitHub(clock)
    paths = [tmp_path / "red-zeta", tmp_path / "red-alpha"]
    for path in paths:
        manifest(path, path.name)
        manifest_path = path / "rail.yaml"
        manifest_path.write_text(
            manifest_path.read_text()
            + "ledger: brain\nticket: 00000000-0000-0000-0000-000000000001\n"
        )
    config = tmp_path / "reviewer.yaml"
    key = tmp_path / "app.pem"
    key.write_text("fixture key")
    key.chmod(0o600)
    config.write_text(
        f"app_id: 1\ninstallation_id: 2\nprivate_key_file: {key}\nrepositories:\n"
        + "".join(f"  - slug: example/{p.name}\n    path: {p}\n" for p in paths)
    )
    config.chmod(0o600)
    ledgers = {p: ReadLedger([released(p.name)]) for p in paths}
    failures: set[Path] = set()

    def read_ledger(path: Path, *, agent: str, token_file: str) -> ReadLedger:
        assert agent == "red-rail-reviewer" and token_file == DEFAULT_REVIEWER_TOKEN_FILE
        if path in failures:
            raise LedgerError("unreachable")
        return ledgers[path]

    def github_app(**kwargs: Any) -> ReadGitHub:
        assert kwargs == {"app_id": 1, "installation_id": 2, "private_key_pem": "fixture key"}
        return github

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(command_module, "datetime", FrozenDatetime)
    monkeypatch.setattr(command_module, "open_ledger", read_ledger)
    monkeypatch.setattr("rail.reviewer.github.GitHubApp", github_app)
    monkeypatch.setattr("rail.gitrepo.fetch_main", lambda repo: True)

    def file_at(repo: Path, ref: str, path: str) -> str:
        assert ref == "refs/remotes/origin/main" and path == "rail.yaml"
        return (repo / path).read_text()

    monkeypatch.setattr("rail.gitrepo.file_at", file_at)
    return {
        "config": config,
        "out": tmp_path / "state" / "metrics.json",
        "paths": paths,
        "ledgers": ledgers,
        "failures": failures,
        "clock": clock,
        "github": github,
        "open_ledger": read_ledger,
    }


def invoke(publication: dict[str, Any], *, out_option: bool = True) -> Result:
    args = ["metrics", "publish", "--config", str(publication["config"])]
    if out_option:
        args += ["--out", str(publication["out"])]
    return CliRunner().invoke(main, args)


def test_publish_two_repositories(publication: dict[str, Any]) -> None:
    result = invoke(publication)
    assert result.exit_code == 0, result.output
    raw = publication["out"].read_bytes()
    answer = Answer.model_validate_json(raw)
    assert answer.autonomy is not None and answer.delivery is not None
    assert answer.autonomy.coverage.read == answer.autonomy.coverage.expected == 2
    assert [p.project for p in answer.autonomy.projects] == ["red-alpha", "red-zeta"]
    assert [p.project for p in answer.delivery.projects] == ["red-alpha", "red-zeta"]
    assert answer.autonomy.total is not None
    assert answer.autonomy.total.d7.reverts == 2
    fixture = json.loads((ROOT / "contracts/rail-metrics-v1/full.json").read_text())
    assert json.loads(raw)["autonomy"]["not_measured"] == fixture["autonomy"]["not_measured"]
    assert result.output == f"{publication['out']} {len(raw)} bytes 2/2 read\n"
    assert publication["github"].closed


def test_publish_carries_both_delivery_versions(publication: dict[str, Any]) -> None:
    result = invoke(publication)
    assert result.exit_code == 0, result.output
    answer = Answer.model_validate_json(publication["out"].read_bytes())
    assert answer.delivery is not None and answer.delivery_v2 is not None
    assert answer.delivery.version == "rail-delivery/v1"
    assert answer.delivery_v2.version == "rail-delivery/v2"
    assert answer.delivery.generated_at == answer.delivery_v2.generated_at
    assert [p.project for p in answer.delivery_v2.projects] == ["red-alpha", "red-zeta"]
    assert all(p.status == "measured" for p in answer.delivery_v2.projects)


def test_publish_targetless_repository_has_explicit_v2_status(publication: dict[str, Any]) -> None:
    path = publication["paths"][0]
    path.joinpath("rail.yaml").write_text(
        f"rail: 1\nproject: {path.name}\nbrain_key: {path.name}\ntier: dev\nstack: python\n"
        "ledger: brain\nticket: 00000000-0000-0000-0000-000000000001\n"
    )
    before = path.joinpath("rail.yaml").read_bytes()
    result = invoke(publication)
    assert result.exit_code == 0, result.output
    answer = Answer.model_validate_json(publication["out"].read_bytes())
    assert answer.delivery is not None and answer.delivery_v2 is not None
    assert [p.project for p in answer.delivery.projects] == ["red-alpha"]
    project = answer.delivery_v2.projects[1]
    assert project.project == "red-zeta"
    assert project.status == "no_deployment"
    assert project.target is project.d7 is project.d30 is None
    assert path.joinpath("rail.yaml").read_bytes() == before
    assert not path.joinpath("docs/receipts").exists()


@pytest.mark.parametrize(
    "step,error_class",
    [
        ("fetch", "OSError"),
        ("manifest", "OSError"),
        ("ledger", "LedgerError"),
        ("github", "GitHubError"),
    ],
)
def test_missing_repository_logs_only_slug_step_and_exception_class(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch, step: str, error_class: str
) -> None:
    path = publication["paths"][0]
    config = publication["config"]
    config.write_text(config.read_text().replace("example/red-zeta", "example/Probe_ZETA"))
    if step == "fetch":
        monkeypatch.setattr("rail.gitrepo.fetch_main", lambda repo: repo != path)
    elif step == "manifest":
        original = Path.read_text

        def unreadable(repo: Path, *args: Any, **kwargs: Any) -> str:
            if repo == path / "rail.yaml":
                raise OSError("sensitive error detail http://192.0.2.1/private")
            return original(repo, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", unreadable)
    elif step == "ledger":
        publication["failures"].add(path)
    else:
        publication["github"].fail.add("example/Probe_ZETA")
    result = invoke(publication)
    assert result.exit_code == 0, result.output
    # the manifest names the project; before it is read, only the slug can
    name = "probe-zeta" if step in ("fetch", "manifest") else "red-zeta"
    assert result.stderr == f"missing {name}: {step} {error_class}\n"
    answer = Answer.model_validate_json(publication["out"].read_bytes())
    assert answer.autonomy is not None
    assert answer.autonomy.coverage.missing == (name,)
    assert answer.delivery is not None and answer.delivery_v2 is not None
    assert [p.project for p in answer.delivery.projects] == ["red-alpha"]
    assert [p.project for p in answer.delivery_v2.projects] == ["red-alpha"]


def test_invalid_reviewer_yaml_prints_error(publication: dict[str, Any]) -> None:
    publication["config"].write_text("repositories: [\n")
    publication["config"].chmod(0o600)
    result = invoke(publication)
    assert result.exit_code == 1
    assert result.stderr.startswith("error: ")
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)
    assert not publication["out"].exists()


def test_computation_value_error_fails_run_and_keeps_previous_file(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail import publish
    from rail.reviewer.config import load_reviewer_config

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("computation bug")

    monkeypatch.setattr(publish, "window_samples", fail)
    with pytest.raises(ValueError, match="computation bug"):
        publish.collect(
            load_reviewer_config(publication["config"]),
            now=NOW,
            clock=publication["clock"],
            open_ledger=publication["open_ledger"],
            github=publication["github"],
        )
    publication["out"].parent.mkdir()
    publication["out"].write_bytes(b"previous")
    result = invoke(publication)
    assert result.exit_code == 1 and "error: computation bug" in result.output
    assert publication["out"].read_bytes() == b"previous"


def test_generated_at_is_one_clock_read_after_all_computations(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail import publish
    from rail.reviewer.config import load_reviewer_config

    original = publish.delivery_window

    def delivery(*args: Any, **kwargs: Any) -> Any:
        value = original(*args, **kwargs)
        publication["clock"].advance("delivery")
        return value

    monkeypatch.setattr(publish, "delivery_window", delivery)
    answer = publish.collect(
        load_reviewer_config(publication["config"]),
        now=NOW,
        clock=publication["clock"],
        open_ledger=publication["open_ledger"],
        github=publication["github"],
    )
    assert publication["clock"].calls == 1
    assert publication["clock"].events[-1] == "clock"
    assert answer.delivery is not None and answer.autonomy is not None
    assert (
        answer.delivery.generated_at == answer.autonomy.generated_at == NOW + timedelta(seconds=11)
    )
    assert answer.autonomy.total is not None
    assert answer.autonomy.total.d7.window_start == NOW - timedelta(days=7)


@pytest.mark.parametrize(
    "failure",
    [
        "ledger",
        "ledger_list",
        "delivery",
        "github",
        "missing_manifest",
        "invalid_manifest",
        "invalid_yaml",
        "invalid_utf8",
    ],
)
def test_read_failure_is_partial(publication: dict[str, Any], failure: str) -> None:
    path = publication["paths"][0]
    if failure == "ledger":
        publication["failures"].add(path)
    elif failure in ("ledger_list", "delivery"):

        class FailingLedger(ReadLedger):
            def list(
                self,
                project: str,
                *,
                kind: RecordKind | None = None,
                attestation: AttestationKind | None = None,
            ) -> list[Record]:
                if failure == "ledger_list" or attestation is not None:
                    raise LedgerError("unreadable records")
                return super().list(project, kind=kind, attestation=attestation)

        publication["ledgers"][path] = FailingLedger([released(path.name)])
    elif failure == "github":
        publication["github"].fail.add(f"example/{path.name}")
    elif failure == "missing_manifest":
        path.joinpath("rail.yaml").unlink()
    elif failure == "invalid_yaml":
        path.joinpath("rail.yaml").write_text("rail: [\n")
    elif failure == "invalid_utf8":
        path.joinpath("rail.yaml").write_bytes(b"\xff")
    else:
        path.joinpath("rail.yaml").write_text("rail: 1\nproject: invalid\n")
    result = invoke(publication)
    assert result.exit_code == 0, result.output
    payload = json.loads(publication["out"].read_bytes())
    assert payload["autonomy"]["coverage"] == {"expected": 2, "read": 1, "missing": ["red-zeta"]}
    assert payload["autonomy"]["total"] is None
    for block in ("autonomy", "delivery"):
        assert [p["project"] for p in payload[block]["projects"]] == ["red-alpha"]


@pytest.mark.parametrize("error", [OSError, PermissionError])
def test_manifest_read_error_is_partial(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch, error: type[OSError]
) -> None:
    original = Path.read_text
    manifest_path = publication["paths"][0] / "rail.yaml"

    def read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == manifest_path:
            raise error("manifest is unreadable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    result = invoke(publication)
    assert result.exit_code == 0, result.output
    answer = Answer.model_validate_json(publication["out"].read_bytes())
    assert answer.autonomy is not None
    assert answer.autonomy.coverage.read == 1
    assert answer.autonomy.coverage.missing == ("red-zeta",)


@pytest.mark.parametrize(
    ("slug_tail", "expected"),
    [
        ("_Red__ZETA..", "red-zeta"),
        ("___", "unknown"),
        ("É", "unknown"),
        ("A" * 65, "a" * 64),
    ],
)
def test_missing_manifest_slug_is_a_valid_coverage_name(
    publication: dict[str, Any], slug_tail: str, expected: str
) -> None:
    path = publication["paths"][0]
    path.joinpath("rail.yaml").unlink()
    config = publication["config"]
    config.write_text(
        config.read_text().replace("slug: example/red-zeta", f"slug: example/{slug_tail}")
    )
    result = invoke(publication)
    assert result.exit_code == 0, result.output
    answer = Answer.model_validate_json(publication["out"].read_bytes())
    assert answer.autonomy is not None
    assert answer.autonomy.coverage.read == 1
    assert answer.autonomy.coverage.missing == (expected,)


def test_computation_value_error_is_not_partial(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail import publish

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("computation bug")

    monkeypatch.setattr(publish, "window_samples", fail)
    result = invoke(publication)
    assert result.exit_code == 1 and "computation bug" in result.output
    assert not publication["out"].exists()


def test_inactive_read_repository_is_in_total_only(publication: dict[str, Any]) -> None:
    path = publication["paths"][0]
    publication["ledgers"][path].records.clear()
    publication["github"].inactive.add(f"example/{path.name}")
    result = invoke(publication)
    assert result.exit_code == 0, result.output
    payload = json.loads(publication["out"].read_bytes())["autonomy"]
    assert payload["coverage"] == {"expected": 2, "read": 2, "missing": []}
    assert [p["project"] for p in payload["projects"]] == ["red-alpha"]
    assert payload["total"]["d30"]["releases"] == 1


def test_development_repository_has_no_delivery_entry(publication: dict[str, Any]) -> None:
    path = publication["paths"][0]
    path.joinpath("rail.yaml").write_text(
        f"rail: 1\nproject: {path.name}\nbrain_key: {path.name}\ntier: dev\nstack: python\n"
        "ledger: brain\nticket: 00000000-0000-0000-0000-000000000001\n"
    )
    result = invoke(publication)
    assert result.exit_code == 0, result.output
    answer = Answer.model_validate_json(publication["out"].read_bytes())
    assert answer.autonomy is not None and answer.delivery is not None
    assert len(answer.autonomy.projects) == 2
    assert [project.project for project in answer.delivery.projects] == ["red-alpha"]


def test_file_ledger_uses_checkout_receipts(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for path in publication["paths"]:
        manifest_path = path / "rail.yaml"
        manifest_path.write_text(manifest_path.read_text().split("ledger: brain")[0])
        FileLedger(path / "docs/receipts", clock=lambda: NOW).attest(
            path.name, AttestationKind.RELEASED, {}, issuer="operator", idempotency_key="release"
        )

    def files_at(repo: Path, ref: str, directory: str) -> dict[str, str]:
        assert ref == "refs/remotes/origin/main" and directory == "docs/receipts"
        return {}

    monkeypatch.setattr("rail.gitrepo.files_at", files_at)
    monkeypatch.setattr(command_module, "open_ledger", open_ledger)
    result = invoke(publication)
    assert result.exit_code == 0, result.output
    answer = Answer.model_validate_json(publication["out"].read_bytes())
    assert answer.autonomy is not None and answer.autonomy.total is not None
    assert answer.autonomy.total.d30.releases == 2


def test_invalid_computation_keeps_previous_file(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail import publish

    original = publish.window_values

    def invalid(*args: Any, **kwargs: Any) -> dict[str, object]:
        return {**original(*args, **kwargs), "agent_gesture_ratio": 1.5}

    monkeypatch.setattr(publish, "window_values", invalid)
    publication["out"].parent.mkdir()
    publication["out"].write_bytes(b"previous")
    result = invoke(publication)
    assert result.exit_code == 1 and "agent_gesture_ratio" in result.output
    assert publication["out"].read_bytes() == b"previous"
    assert publication["github"].closed


@pytest.mark.parametrize("previous", [False, True])
def test_every_read_failing_writes_nothing(publication: dict[str, Any], previous: bool) -> None:
    publication["failures"].update(publication["paths"])
    if previous:
        publication["out"].parent.mkdir()
        publication["out"].write_bytes(b"previous")
    result = invoke(publication)
    assert result.exit_code == 1 and "no repository" in result.output
    if previous:
        assert publication["out"].read_bytes() == b"previous"
    else:
        assert not publication["out"].parent.exists()


def test_private_config_error_is_reported(publication: dict[str, Any]) -> None:
    publication["config"].chmod(0o644)
    result = invoke(publication)
    assert result.exit_code == 1 and "mode 644" in result.output
    assert not publication["out"].exists()


def test_malformed_app_private_key_is_reported(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = publication["config"].parent / "app.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nmalformed\n-----END PRIVATE KEY-----\n")
    monkeypatch.setattr("rail.reviewer.github.GitHubApp", GitHubApp)
    result = invoke(publication)
    assert result.exit_code == 1
    assert result.output.startswith("error: ")
    assert isinstance(result.exception, SystemExit)
    assert not publication["out"].exists()


def test_existing_output_directory_keeps_its_permissions(publication: dict[str, Any]) -> None:
    publication["out"].parent.mkdir(mode=0o755)
    publication["out"].parent.chmod(0o755)
    result = invoke(publication)
    assert result.exit_code == 0, result.output
    assert publication["out"].parent.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("fail_directory_sync", [False, True])
def test_publication_syncs_parent_after_replace_and_closes_it(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch, fail_directory_sync: bool
) -> None:
    from rail import publish

    events: list[str] = []
    directories: list[int] = []
    original_fsync = os.fsync
    original_replace = os.replace

    def fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directories.append(fd)
            events.append("directory sync")
            if fail_directory_sync:
                raise OSError("directory sync failed")
        else:
            events.append("file sync")
        original_fsync(fd)

    def replace(source: Path, destination: Path) -> None:
        events.append("replace")
        original_replace(source, destination)

    monkeypatch.setattr(publish.os, "fsync", fsync)
    monkeypatch.setattr(publish.os, "replace", replace)
    result = invoke(publication)
    assert events == ["file sync", "replace", "directory sync"]
    assert result.exit_code == (1 if fail_directory_sync else 0), result.output
    assert publication["out"].exists()
    assert list(publication["out"].parent.iterdir()) == [publication["out"]]
    for fd in directories:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_failed_fsync_keeps_previous_file_and_cleans_temporary(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail import publish

    def fail(fd: int) -> None:
        raise OSError("fsync failed")

    publication["out"].parent.mkdir()
    publication["out"].write_bytes(b"previous")
    monkeypatch.setattr(publish.os, "fsync", fail)
    result = invoke(publication)
    assert result.exit_code == 1 and "fsync failed" in result.output
    assert publication["out"].read_bytes() == b"previous"
    assert list(publication["out"].parent.iterdir()) == [publication["out"]]


def test_oversized_answer_keeps_previous_file(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail import publish

    original = publish.collect

    def oversized(*args: Any, **kwargs: Any) -> Answer:
        answer = original(*args, **kwargs)
        assert answer.autonomy is not None
        large = answer.autonomy.not_measured[0].model_copy(update={"why": "x" * MAX_BYTES})
        return answer.model_copy(
            update={"autonomy": answer.autonomy.model_copy(update={"not_measured": (large,)})}
        )

    monkeypatch.setattr(publish, "collect", oversized)
    publication["out"].parent.mkdir()
    publication["out"].write_bytes(b"previous")
    result = invoke(publication)
    assert result.exit_code == 1 and "bytes" in result.output and str(MAX_BYTES) in result.output
    assert publication["out"].read_bytes() == b"previous"
    assert list(publication["out"].parent.iterdir()) == [publication["out"]]


def test_publication_modes_and_default_output(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(publication["out"].parent.parent))
    result = invoke(publication, out_option=False)
    assert result.exit_code == 0, result.output
    out = publication["out"].parent.parent / "red-rail/metrics.json"
    assert out.stat().st_mode & 0o777 == 0o644
    assert out.parent.stat().st_mode & 0o777 == 0o700
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(publication["out"].parent.parent))
    result = invoke(publication, out_option=False)
    assert result.exit_code == 0, result.output
    assert (publication["out"].parent.parent / ".local/state/red-rail/metrics.json").exists()


def test_publish_without_a_manifest_and_default_config(
    publication: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rail.reviewer import config as config_module

    original = config_module.load_reviewer_config

    def load(path: Path) -> Any:
        assert path == DEFAULT_CONFIG
        return original(publication["config"])

    monkeypatch.setattr(config_module, "load_reviewer_config", load)
    monkeypatch.chdir(publication["out"].parent.parent)
    result = CliRunner().invoke(main, ["metrics", "publish", "--out", str(publication["out"])])
    assert result.exit_code == 0, result.output


def test_group_supports_serve_help_without_a_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The serve task registers its command on this group without triggering the old callback."""
    assert isinstance(command_module.command, click.Group)
    monkeypatch.chdir(tmp_path)

    @click.command("serve")
    def serve() -> None:
        pass

    existing = command_module.command.commands.get("serve")
    command_module.command.add_command(serve)
    try:
        result = CliRunner().invoke(main, ["metrics", "serve", "--help"])
        assert result.exit_code == 0 and "rail.yaml" not in result.output
    finally:
        command_module.command.commands.pop("serve")
        if existing is not None:
            command_module.command.add_command(existing)


@pytest.mark.parametrize("as_json", [False, True])
def test_historical_output_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_json: bool
) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW

    monkeypatch.setattr(command_module, "datetime", FrozenDatetime)
    value = Metrics(
        "red-alpha",
        7,
        (NOW - timedelta(days=7)).isoformat(),
        2,
        2.0,
        3.0,
        None,
        0.5,
        None,
        2.0,
        Conformance(1, 2, 0),
    )
    monkeypatch.setattr(
        command_module,
        "load_rail_config",
        lambda repo: type("Config", (), {"project": "red-alpha"})(),
    )
    monkeypatch.setattr(command_module, "open_ledger", lambda repo: object())

    def compute(*args: Any, **kwargs: Any) -> Metrics:
        assert kwargs["window_days"] == 7 and kwargs["ci"] is True and kwargs["now"] == NOW
        return value

    monkeypatch.setattr(command_module, "compute_metrics", compute)
    result = CliRunner().invoke(
        main,
        ["metrics", "--repo", str(tmp_path), "--window", "7", "--ci"]
        + (["--json"] if as_json else []),
    )
    assert result.exit_code == 0, result.output
    expected = (
        json.dumps(value.to_dict(), indent=2) + "\n"
        if as_json
        else (
            "red-alpha  window 7d since 2026-10-06T12:00:00+00:00\n"
            "deployments                 2 (2/week)\n"
            "lead time commit → deploy   3 h\n"
            "lead time contract → deploy n/a\n"
            "change failure rate         0.5\n"
            "recovery time               n/a\n"
            "drill recovery time         2 min\n"
            "conformance                 1/2 (0 declared exception(s))\n"
        )
    )
    assert result.stdout_bytes == expected.encode()


@pytest.mark.parametrize("as_json", [False, True])
def test_historical_errors_are_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_json: bool
) -> None:
    def fail(repo: Path) -> None:
        raise FileNotFoundError("rail.yaml is missing")

    monkeypatch.setattr(command_module, "load_rail_config", fail)
    result = CliRunner().invoke(
        main, ["metrics", "--repo", str(tmp_path)] + (["--json"] if as_json else [])
    )
    assert result.exit_code == 1
    if as_json:
        assert result.stdout_bytes == b'{"error": "rail.yaml is missing"}\n'
    else:
        assert result.stderr_bytes == b"error: rail.yaml is missing\n"
