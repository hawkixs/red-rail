"""An isolated PostgreSQL restore drill; production is never applied or reconfigured."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import cast

from rail.deploy import Artefact, LiveVersion, Locked, flow
from rail.deploy.private_compose import PrivateCompose
from rail.deploy.remote import LOCKED, Parameters, _decoded, lock_preamble, ssh_argv
from rail.ledger import AttestationKind, Ledger
from rail.model import DeployTarget, RailConfig, RestoreConfig

REFUSALS = {
    81: "backup_schema_mismatch",
    82: "backup_digest_mismatch",
    83: "insufficient_disk",
    84: "database_unhealthy",
    85: "restore_failed",
    86: "health_failed",
    87: "read_failed",
    124: "restore_timeout",
    137: "restore_timeout",
}


def check_config(cfg: RailConfig) -> None:
    """Refuse unsupported shapes before constructing a target or reading host settings."""
    if cfg.deploy is None or cfg.deploy.target is not DeployTarget.PRIVATE_COMPOSE:
        name = cfg.deploy.target.value if cfg.deploy else "none"
        raise flow.DrillRefused(
            "unsupported_restore_target", f"restore drill is unsupported on target {name}"
        )
    if cfg.drill is None or cfg.drill.restore is None:
        raise flow.DrillRefused("restore_not_configured", "drill.restore is not declared")
    if cfg.drill.schema_head is None:
        raise flow.DrillRefused("schema_head_unknown", "drill.schema_head is not declared")


def script_for(
    project: str,
    config: RestoreConfig,
    previous: Artefact,
    previous_head: str,
    backup: str,
    backup_sha256: str,
    backup_head: str | None,
    params: Parameters,
) -> str:
    """Build everything before ssh. Every external value is a quoted shell word."""
    if not backup.startswith("/") or any(char in backup for char in "\0\n\r,"):
        raise flow.DrillRefused(
            "invalid_backup_path", "backup must be an absolute path without NUL, newlines or commas"
        )
    if re.fullmatch(r"[0-9a-fA-F]{64}", backup_sha256) is None:
        raise flow.DrillRefused("invalid_backup_digest", "backup-sha256 must be 64 hex characters")
    if backup_head is None and config.schema_head_file is None:
        raise flow.DrillRefused(
            "backup_schema_unknown",
            "provide --backup-schema-head or drill.restore.schema_head_file",
        )
    if "\0" in previous_head or (backup_head is not None and "\0" in backup_head):
        raise flow.DrillRefused("schema_head_unknown", "schema heads must not contain NUL")
    quote = shlex.quote
    if backup_head is not None:
        read_head = [f"backup_head={quote(backup_head)}"]
    else:
        assert config.schema_head_file is not None
        read_head = [
            f"head_file={quote(config.schema_head_file)}",
            '[[ "$head_file" = /* ]] || head_file="$(dirname -- "$backup")/$head_file"',
            'backup_head=$(cat -- "$head_file" 2>/dev/null) || exit 81',
        ]
    environment = [f'--env "{key}=$database_url"' for key in config.database_env]
    environment.extend(f"--env {quote(key + '=' + value)}" for key, value in config.env.items())
    app = (
        'docker run --detach --name "$app" --network "$network" --log-driver none '
        + " ".join(environment)
        + " "
        + quote(previous.image)
        + " >/dev/null 2>&1"
    )
    health = 'docker exec "$app" ' + shlex.join(config.health_command) + " >/dev/null 2>&1"
    read = 'docker exec "$app" ' + shlex.join(config.read_command) + " >/dev/null 2>&1"
    # The deadline lives on the target, leaving time for cleanup before the host's ssh
    # timeout. TERM reaches the whole process group, including a blocked docker client.
    deadline = max(1, params.remote_timeout - 15)
    body = [
        "set +x",
        "set -euo pipefail",
        "umask 077",
        f"backup={quote(backup)}",
        *read_head,
        f'[[ "$backup_head" == {quote(previous_head)} ]] || exit 81',
        'actual=$(sha256sum -- "$backup" 2>/dev/null) || exit 82',
        f'[[ "${{actual%% *}}" == {quote(backup_sha256.lower())} ]] || exit 82',
        'backup_size=$(stat -Lc %s -- "$backup")',
        f'required=$(awk -v size="$backup_size" -v margin={config.disk_margin} '
        "'BEGIN {n=size*margin; printf \"%.0f\", int(n)+(n>int(n))}')",
        'docker_root=$(docker info --format "{{.DockerRootDir}}" 2>/dev/null)',
        # GNU df refuses -P together with --output; one explicit column is unambiguous.
        'for filesystem in "$docker_root" "$backup"; do',
        '  available=$(df --block-size=1 --output=avail -- "$filesystem" | tail -n 1)',
        '  [[ "$available" =~ ^[[:space:]]*[0-9]+$ ]] || exit 83',
        # Compare in awk too: a valid large margin must not overflow Bash's signed integer.
        f'  if ! awk -v available="$available" -v size="$backup_size" '
        f"-v margin={config.disk_margin} 'BEGIN {{exit !(available >= size*margin)}}'; then",
        "    printf 'restore_disk required_bytes=%s available_bytes=%s\\n' "
        '"$required" "${available//[[:space:]]/}"',
        "    exit 83",
        "  fi",
        "done",
        "suffix=\"$(date +%s)-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \\n')\"",
        'network="rail-restore-$suffix"',
        'database="rail-restore-db-$suffix"',
        'app="rail-restore-app-$suffix"',
        "cleanup() {",
        "  status=$?",
        "  trap - EXIT HUP INT TERM",
        # Never print Docker errors: an application can put its disposable URL in them.
        '  docker rm --force --volumes "$app" >/dev/null 2>&1 || true',
        '  docker rm --force --volumes "$database" >/dev/null 2>&1 || true',
        '  docker network rm "$network" >/dev/null 2>&1 || true',
        '  exit "$status"',
        "}",
        "trap cleanup EXIT",
        "trap 'exit 124' HUP INT TERM",
        "password=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \\n')",
        'docker network create --internal "$network" >/dev/null 2>&1',
        'docker run --detach --name "$database" --network "$network" '
        "--network-alias database --log-driver none "
        '--env "POSTGRES_PASSWORD=$password" --env POSTGRES_USER=drill --env POSTGRES_DB=drill '
        '--mount "type=bind,source=$backup,target=/backup.dump,readonly" '
        f"{quote(config.database_image)} >/dev/null 2>&1",
        f'database_url="{config.database_url_scheme}://drill:$password@database:5432/drill"',
        # TCP readiness avoids mistaking the image's temporary initialization server for
        # the final database. No Docker or application logs are ever collected.
        f"deadline=$((SECONDS + {params.healthcheck_timeout}))",
        'until docker exec "$database" pg_isready --host 127.0.0.1 --username drill '
        "--dbname drill >/dev/null 2>&1; do",
        "  (( SECONDS < deadline )) || exit 84",
        "  sleep 1",
        "done",
        "started=$(date +%s)",
        'docker exec "$database" pg_restore --username drill --dbname drill '
        "--exit-on-error --no-owner --no-privileges /backup.dump >/dev/null 2>&1 || exit 85",
        app + " || exit 86",
        f"deadline=$((SECONDS + {params.healthcheck_timeout}))",
        f"until {health}; do",
        "  (( SECONDS < deadline )) || exit 86",
        "  sleep 1",
        "done",
        read + " || exit 87",
        "finished=$(date +%s)",
        "recovery_seconds=$((finished - started))",
        # A subsecond restore is still positive evidence in the integer-second protocol.
        "(( recovery_seconds > 0 )) || recovery_seconds=1",
        # Emit success only after cleanup, so the result never precedes removal.
        'docker rm --force --volumes "$app" "$database" >/dev/null 2>&1',
        'docker network rm "$network" >/dev/null 2>&1',
        "trap - EXIT HUP INT TERM",
        "printf 'restore_result recovery_seconds=%s\\n' \"$recovery_seconds\"",
    ]
    marker = "__RAIL_RESTORE__"
    if any(marker in line for line in body):
        raise flow.DrillRefused("invalid_restore_config", "restore config contains script marker")
    root = f"{params.stack_root}/{project}"
    return "\n".join(
        [
            "set +x",
            *lock_preamble(root, None),
            f"timeout --signal=TERM --kill-after=5 {deadline}s bash -s <<'{marker}'",
            *body,
            marker,
            "",
        ]
    )


def run_script(target: PrivateCompose, script: str) -> int:
    """One transport call. Only recognized numeric result fields may leave the target."""
    try:
        done = target._run(
            list(ssh_argv(target.params)),
            input=script.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=target.params.remote_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise flow.DrillRefused("restore_timeout", "restore drill timed out") from exc
    except OSError as exc:
        raise flow.DrillRefused("restore_failed", "restore drill transport failed") from exc
    if done.returncode == LOCKED:
        raise Locked("another deployment holds the target lock")
    output = _decoded(done.stdout)
    if done.returncode != 0:
        reason = REFUSALS.get(done.returncode, "restore_failed")
        details: dict[str, int] = {}
        if done.returncode == 83:
            for line in output.split("\n"):
                match = re.fullmatch(
                    r"restore_disk required_bytes=(\d+) available_bytes=(\d+)", line
                )
                if match:
                    details = {"required_bytes": int(match[1]), "available_bytes": int(match[2])}
        message = f"restore drill failed: {reason}"
        if details:
            message += (
                f" (required {details['required_bytes']}, "
                f"available {details['available_bytes']} bytes)"
            )
        raise flow.DrillRefused(reason, message, **details)
    results = [
        match[1]
        for line in output.split("\n")
        if (match := re.fullmatch(r"restore_result recovery_seconds=([1-9][0-9]*)", line))
    ]
    if len(results) != 1:
        raise flow.DrillRefused(
            "restore_result_invalid", "restore drill returned no unique duration"
        )
    return int(results[0])


def drill(
    repo: Path,
    cfg: RailConfig,
    ledger: Ledger,
    *,
    issuer: str,
    backup: str,
    backup_sha256: str,
    backup_head: str | None,
    target: flow.Target | None = None,
) -> flow.Outcome:
    """Attest exactly one restored record after the isolated read and cleanup succeed."""
    check_config(cfg)
    assert cfg.deploy is not None and cfg.drill is not None and cfg.drill.restore is not None
    target = target or flow.make_target(repo, cfg)
    live = flow.live_artefact(ledger, cfg.project)
    previous = flow.previous_artefact(ledger, cfg.project, live.digest) if live else None
    if live is None or previous is None:
        raise flow.DrillRefused("artefacts_unknown", "a restore drill needs two deployed artefacts")
    head = str(flow.schema_head(cfg, ledger, target, previous))
    private = cast("PrivateCompose", target)
    script = script_for(
        cfg.project,
        cfg.drill.restore,
        previous,
        head,
        backup,
        backup_sha256,
        backup_head,
        private.params,
    )
    seconds = run_script(private, script)
    attester = flow.Attester(
        ledger, cfg.project, cfg.deploy.target.value, issuer, redact=target.redact
    )
    attester.attest(
        AttestationKind.RESTORED,
        {
            "drill": True,
            "restore": True,
            "artefact_digest": previous.digest,
            "backup_sha256": backup_sha256.lower(),
            "recovery_seconds": seconds,
            "live_version": live.version,
            "previous_version": previous.version,
        },
    )
    return flow.Outcome(
        LiveVersion(cfg.project, live.version, live.sha, live.digest),
        tuple(attester.records),
        tuple(attester.unattested),
        recovery_seconds=seconds,
        ledger_failures=tuple(attester.failures),
    )
