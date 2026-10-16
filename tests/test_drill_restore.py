"""Restore drills use a fake transport and ledger, never Docker, git or host settings."""

import json
import re
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from rail import metrics as metrics_module
from rail.cli import main
from rail.commands import drill as drill_command
from rail.deploy import flow
from rail.deploy import restore as restore_flow
from rail.deploy.private_compose import PrivateCompose
from rail.gates import evidence
from rail.ledger import AttestationKind as Kind
from rail.ledger.file import FileLedger
from rail.metrics import delivery_window, delivery_window_v2
from rail.model import load_rail_config

OLD, LIVE = "sha256:" + "1" * 64, "sha256:" + "2" * 64
DATABASE = "example/database@sha256:" + "3" * 64
BACKUP_SHA = "d" * 64
RELEASE_SHA = "a" * 40
T0 = datetime(2026, 10, 1, tzinfo=UTC)
RESTORE = {
    "database_image": DATABASE,
    "database_env": ["DATABASE_URL", "ASYNC_DATABASE_URL"],
    "env": {"GRAPH_ENABLED": "false", "APP_MODE": "read only; $(false)"},
    "env_deny_prefixes": ["TOKEN_", "OTHER_SERVICE_"],
    "health_command": ["app-check", "--health"],
    "read_command": ["app-check", "--read", "two words; $(false)"],
    "schema_head_file": "backup.head",
}


class Transport:
    def __init__(self):
        self.scripts = []
        self.reads = []
        self.code = 0
        self.output = b"restore_result recovery_seconds=12\n"
        self.schema = b'{"head":"067"}'

    def __call__(self, argv, **kwargs):
        if argv[0] == "git":
            self.reads.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, self.schema, b"")
        assert argv[0] == "ssh"
        self.scripts.append(kwargs["input"].decode())
        assert kwargs["timeout"] > 0
        return subprocess.CompletedProcess(argv, self.code, self.output, b"untrusted output")


@pytest.fixture
def setup_restore(tmp_path, monkeypatch):
    manifest = {
        "rail": 1,
        "project": "red-fixture",
        "brain_key": "red-fixture",
        "tier": "prod",
        "stack": "python",
        "deploy": {"target": "private-compose", "healthcheck": "https://example.invalid/health"},
        "gates": {
            "deploy.ssh_host": {"value": "fixture", "reason": "Test transport"},
            "deploy.bind_address": {"value": "127.0.0.1", "reason": "Test only"},
        },
        "drill": {"schema_head": {"path": "schema.json", "field": "head"}, "restore": RESTORE},
    }
    (tmp_path / "rail.yaml").write_text(yaml.safe_dump(manifest))
    ledger = FileLedger(tmp_path / "receipts")
    for index, (version, digest) in enumerate((("0.7.0", OLD), ("0.7.1", LIVE))):
        data = {
            "version": version,
            "digest": digest,
            "sha": RELEASE_SHA,
            "image": f"example/app@{digest}",
            "target": "private-compose",
            "mode": "release",
        }
        for kind in (Kind.RELEASED, Kind.DEPLOYED):
            ledger.attest(
                "red-fixture",
                kind,
                data,
                issuer="op",
                idempotency_key=kind.value + version,
                emitted_at=T0 + timedelta(seconds=index),
            )
    transport = Transport()
    monkeypatch.setattr(
        flow, "make_target", lambda repo, cfg: PrivateCompose(repo, cfg, run=transport)
    )
    monkeypatch.setattr(drill_command, "open_ledger", lambda repo: ledger)
    monkeypatch.setattr(evidence, "open_ledger", lambda repo: ledger)
    monkeypatch.setattr(metrics_module.gitrepo, "commit_timestamp", lambda *args: T0)
    monkeypatch.setattr(metrics_module.gitrepo, "is_ancestor", lambda *args: False)
    return tmp_path, manifest, ledger, transport


def invoke(repo, *args, yes=True):
    return CliRunner().invoke(
        main,
        [
            "drill",
            "--repo",
            str(repo),
            "--restore",
            "--backup",
            "/backups/fixture.dump",
            "--backup-sha256",
            BACKUP_SHA,
            "--json",
        ]
        + (["--yes"] if yes else [])
        + list(args),
    )


def write_manifest(repo, manifest):
    (repo / "rail.yaml").write_text(yaml.safe_dump(manifest))


@pytest.mark.parametrize(
    "missing,reason",
    [
        ("restore", "restore_not_configured"),
        ("target", "unsupported_restore_target"),
        ("schema_head", "schema_head_unknown"),
    ],
)
def test_restore_preflight_refuses_before_ssh(setup_restore, missing, reason):
    repo, manifest, ledger, transport = setup_restore
    before = ledger.list("red-fixture")
    if missing == "target":
        manifest["deploy"]["target"] = "vps-traefik"
    else:
        del manifest["drill"][missing]
    write_manifest(repo, manifest)
    result = invoke(repo)
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["reason"] == reason
    assert json.loads(result.output)["refused"] is True
    assert transport.scripts == []
    assert ledger.list("red-fixture") == before


@pytest.mark.parametrize("schema", [b"{}", b"invalid", b"\xff", b'{"head":true}'])
def test_unknown_previous_head_never_reaches_ssh(setup_restore, schema):
    repo, _, ledger, transport = setup_restore
    transport.schema = schema
    before = ledger.list("red-fixture")
    result = invoke(repo)
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["reason"] == "schema_head_unknown"
    assert transport.scripts == [] and ledger.list("red-fixture") == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("database_image", "example/database:latest"),
        ("database_image", DATABASE + "; false"),
        ("env", {"TOKEN_VALUE": "forbidden"}),
        ("env", {"OTHER_SERVICE_URL": "forbidden"}),
        ("env", {"BAD=KEY": "invalid"}),
        ("env", {"DATABASE_URL": "override"}),
        ("database_env", []),
        ("database_env", ["BAD KEY"]),
        ("health_command", []),
        ("read_command", ["read", "\0"]),
        ("disk_margin", 0.9),
        ("disk_margin", float("inf")),
    ],
)
def test_restore_model_rejects_unsafe_configuration(setup_restore, field, value):
    repo, manifest, _, transport = setup_restore
    valid = load_rail_config(repo)
    assert valid.drill.restore.database_image == DATABASE
    assert valid.drill.restore.disk_margin == 2.0
    manifest["drill"]["restore"] = {**RESTORE, field: value}
    write_manifest(repo, manifest)
    with pytest.raises(ValidationError):
        load_rail_config(repo)
    assert transport.scripts == []


def test_restore_script_is_isolated_and_ordered(setup_restore):
    repo, _, _, transport = setup_restore
    result = invoke(repo)
    assert result.exit_code == 0, result.output
    assert transport.reads == [RELEASE_SHA + ":schema.json"]
    assert len(transport.scripts) == 1
    script = transport.scripts[0]
    assert "trap cleanup EXIT" in script
    assert "TERM" in script and "HUP" in script and "timeout" in script
    assert "flock -n 9" in script
    assert "docker network create --internal" in script
    assert "docker rm --force --volumes" in script and "docker network rm" in script
    assert not re.search(r"(?:^|\s)(?:-p|--publish)(?:\s|=)", script)
    assert "--env-file" not in script
    assert "readonly" in script
    assert DATABASE in script and f"example/app@{OLD}" in script
    assert f"example/app@{LIVE}" not in script
    assert "docker compose" not in script and "ln -s" not in script
    app = next(line for line in script.splitlines() if f"example/app@{OLD}" in line)
    assert set(re.findall(r"--env ['\"]?([A-Z_]+)=", app)) == {
        "DATABASE_URL",
        "ASYNC_DATABASE_URL",
        "GRAPH_ENABLED",
        "APP_MODE",
    }
    assert "'APP_MODE=read only; $(false)'" in app
    assert script.index("sha256sum") < script.index("pg_restore")
    assert script.index("df ") < script.index("pg_restore")
    assert script.index("started=") < script.index("pg_restore")
    assert script.index("app-check --read 'two words; $(false)'") < script.index("finished=")
    assert '"$backup"' in script and "backup.head" in script
    assert "cat /" not in script
    checked = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize(
    "code,reason",
    [
        (81, "backup_schema_mismatch"),
        (82, "backup_digest_mismatch"),
        (83, "insufficient_disk"),
        (84, "database_unhealthy"),
        (85, "restore_failed"),
        (86, "health_failed"),
        (87, "read_failed"),
        (124, "restore_timeout"),
        (1, "restore_failed"),
    ],
)
def test_remote_failure_never_attests_or_records_incident(setup_restore, code, reason):
    repo, _, ledger, transport = setup_restore
    before = ledger.list("red-fixture")
    transport.code = code
    transport.output = b"restore_disk required_bytes=200 available_bytes=100\n"
    result = invoke(repo)
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["reason"] == reason
    assert "untrusted output" not in result.output
    if code == 83:
        assert data["required_bytes"] == 200 and data["available_bytes"] == 100
    assert ledger.list("red-fixture") == before


@pytest.mark.parametrize("compute", [delivery_window, delivery_window_v2])
def test_restore_attests_once_satisfies_gate_and_leaves_metrics_clean(setup_restore, compute):
    repo, _, ledger, _ = setup_restore
    cfg = load_rail_config(repo)
    ledger.attest(
        cfg.project,
        Kind.INCIDENT_DETECTED,
        {"drill": False, "digest": LIVE},
        issuer="op",
        idempotency_key="real-incident",
        emitted_at=T0 + timedelta(seconds=2),
    )
    before = ledger.list(cfg.project)
    kwargs = {"now": datetime.now(UTC) + timedelta(hours=1), "config": cfg, "window_days": 30}
    metrics = compute(ledger, cfg.project, repo, **kwargs)
    result = invoke(repo, "--backup-schema-head", "067")
    assert result.exit_code == 0, result.output
    records = ledger.list(cfg.project)
    assert records[:-1] == before
    assert records[-1].attestation is Kind.RESTORED
    assert records[-1].data == {
        "target": "private-compose",
        "drill": True,
        "restore": True,
        "artefact_digest": OLD,
        "backup_sha256": BACKUP_SHA,
        "recovery_seconds": 12,
        "live_version": "0.7.1",
        "previous_version": "0.7.0",
    }
    assert evidence.drill(repo).passed
    assert compute(ledger, cfg.project, repo, **kwargs) == metrics


@pytest.mark.parametrize("output", [b"", b"restore_result recovery_seconds=0\n", b"other=12\n"])
def test_invalid_result_never_attests(setup_restore, output):
    repo, _, ledger, transport = setup_restore
    before = ledger.list("red-fixture")
    transport.output = output
    result = invoke(repo)
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["reason"] == "restore_result_invalid"
    assert ledger.list("red-fixture") == before


def test_restore_still_requires_confirmation(setup_restore):
    repo, _, ledger, transport = setup_restore
    before = ledger.list("red-fixture")
    result = invoke(repo, yes=False)
    assert result.exit_code != 0
    assert transport.scripts == [] and ledger.list("red-fixture") == before


def test_restore_lock_keeps_exit_three(setup_restore):
    repo, _, ledger, transport = setup_restore
    before = ledger.list("red-fixture")
    transport.code = 75
    result = invoke(repo)
    assert result.exit_code == 3, result.output
    assert ledger.list("red-fixture") == before


@pytest.mark.parametrize("backup", ["relative.dump", "/backup,readonly=false", "/backup\n.dump"])
def test_unsafe_backup_paths_refuse_before_ssh(setup_restore, backup):
    repo, _, ledger, transport = setup_restore
    before = ledger.list("red-fixture")
    result = invoke(repo, "--backup", backup)
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["reason"] == "invalid_backup_path"
    assert transport.scripts == [] and ledger.list("red-fixture") == before


def test_explicit_backup_head_overrides_the_sidecar(setup_restore):
    repo, _, _, transport = setup_restore
    result = invoke(repo, "--backup-schema-head", "067")
    assert result.exit_code == 0, result.output
    script = transport.scripts[0]
    assert "backup_head=067" in script
    assert "head_file=" not in script


def test_missing_backup_schema_source_refuses_before_ssh(setup_restore):
    repo, manifest, ledger, transport = setup_restore
    manifest["drill"]["restore"] = {**RESTORE, "schema_head_file": None}
    write_manifest(repo, manifest)
    before = ledger.list("red-fixture")
    result = invoke(repo)
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["reason"] == "backup_schema_unknown"
    assert transport.scripts == [] and ledger.list("red-fixture") == before


# Exported functions stand in for target commands, including in the nested timeout shell.
# The trace records operation names only, never environment values or generated passwords.
SHELL_HOST = r"""
docker() {
  case "$1 $2" in
    'info --format') printf '%s\n' "$STUB_ROOT"; return 0 ;;
    'network create') printf 'network_create\n' >> "$TRACE" ;;
    'network rm')
      printf 'network_remove\n' >> "$TRACE"
      [[ "$STUB_FAILURE" != cleanup ]] || return 1 ;;
    'rm --force') printf 'container_remove\n' >> "$TRACE" ;;
    'run --detach')
      printf 'container_start\n' >> "$TRACE"
      printf 'private-container-output\n'
      ;;
    'exec '* )
      case "$3" in
        pg_isready)
          printf 'ready\n' >> "$TRACE"
          [[ "$STUB_FAILURE" != database ]] || return 1 ;;
        pg_restore)
          printf 'restore\n' >> "$TRACE"
          [[ "$STUB_FAILURE" != restore ]] || return 1
          if [[ "$STUB_FAILURE" == timeout ]]; then command sleep 10; fi ;;
        app-check)
          case "$4" in
            --health)
              printf 'health\n' >> "$TRACE"
              [[ "$STUB_FAILURE" != health ]] || return 1 ;;
            --read)
              [[ "$5" == 'two words; $(false)' ]] || return 99
              printf 'read\n' >> "$TRACE"
              [[ "$STUB_FAILURE" != read ]] || return 1 ;;
          esac ;;
      esac ;;
  esac
  return 0
}
cat() { printf '%s\n' "$STUB_HEAD"; }
sha256sum() { printf 'digest\n' >> "$TRACE"; printf '%s  backup\n' "$STUB_SHA"; }
stat() { printf '100\n'; }
df() { printf 'disk\n' >> "$TRACE"; printf 'Available\n%s\n' "$STUB_SPACE"; }
export -f docker cat sha256sum stat df
"""


@pytest.mark.parametrize(
    "failure,code",
    [
        ("none", 0),
        ("schema", 81),
        ("digest", 82),
        ("disk", 83),
        ("database", 84),
        ("restore", 85),
        ("health", 86),
        ("read", 87),
        ("timeout", 124),
        ("cleanup", 1),
        ("large_margin", 83),
    ],
)
def test_bash_checks_and_cleanup_without_docker(setup_restore, failure, code):
    repo, _, _, transport = setup_restore
    cfg = load_rail_config(repo)
    if failure == "large_margin":
        cfg.drill.restore.disk_margin = 1e20
    target = PrivateCompose(repo, cfg, run=transport)
    params = replace(target.params, stack_root=str(repo), healthcheck_timeout=0, remote_timeout=16)
    root = repo / cfg.project
    root.mkdir()
    script = restore_flow.script_for(
        cfg.project,
        cfg.drill.restore,
        flow.Artefact("0.7.0", RELEASE_SHA, OLD, f"example/app@{OLD}"),
        "067",
        "/backups/fixture.dump",
        BACKUP_SHA,
        None,
        params,
    )
    trace = repo / "trace"
    done = run_fake_shell(script, repo, trace, failure)
    assert done.returncode == code, (done.stdout, done.stderr)
    assert "private-container-output" not in done.stdout + done.stderr
    events = trace.read_text().splitlines() if trace.exists() else []
    if failure in {"schema", "digest", "disk", "large_margin"}:
        assert "network_create" not in events and "restore" not in events
    else:
        assert events.index("digest") < events.index("disk") < events.index("network_create")
        assert events.count("disk") == 2
        assert "container_remove" in events and "network_remove" in events
    if code == 0:
        assert events.index("restore") < events.index("health") < events.index("read")
        assert events[-1] == "network_remove"
        assert re.fullmatch(r"restore_result recovery_seconds=[1-9][0-9]*\n", done.stdout)
    else:
        assert "restore_result" not in done.stdout


def run_fake_shell(script, repo, trace, failure):
    # Pass only test controls to bash. No subprocess can reach Docker or the network:
    # every Docker operation is intercepted by the exported function above.
    return subprocess.run(
        ["bash", "-s"],
        input=SHELL_HOST + script,
        text=True,
        capture_output=True,
        timeout=8,
        env={
            "PATH": "/usr/bin:/bin",
            "STUB_ROOT": str(repo),
            "TRACE": str(trace),
            "STUB_FAILURE": failure,
            "STUB_HEAD": "068" if failure == "schema" else "067",
            "STUB_SHA": "e" * 64 if failure == "digest" else BACKUP_SHA,
            "STUB_SPACE": "100" if failure == "disk" else "1000",
        },
    )
