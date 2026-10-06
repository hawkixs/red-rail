"""Allow-list rules for the `private-timers` units (spec 2026-10-06-private-timers-target).

The unit syntax is deliberately read through `private_systemd`'s small parser and helpers.
Values the rail cannot establish as safe are refused before any remote operation.
"""

import json
import posixpath
import shlex
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from rail.deploy import Artefact, DeployError, LiveVersion, Step
from rail.deploy.private_systemd import (
    _COMMAND_KEYS,
    _DURATION,
    _PRIVILEGED,
    _UNSAFE_IN_COMMAND,
    _USERNAME,
    _effective,
    _invisible_character_refusal,
    _memory_ok,
    _prefix,
    _program,
    _words,
    loaded_unit_checks,
    parse_unit,
    release_env,
)
from rail.deploy.remote import Parameters, RemoteTarget, _tail, heredoc, lock_preamble, ssh_argv
from rail.model import RailConfig


def _inside(program: str, directory: str) -> bool:
    """A prefix test alone admits `<app>/../..`, and systemd runs what the path resolves to
    (commit security review): the program must already be normalised and strictly below the
    payload directory."""
    return (
        program.startswith(directory)
        and posixpath.normpath(program) == program
        and len(program) > len(directory)
    )


TIMER_TRIGGERS = (
    "OnCalendar",
    "OnBootSec",
    "OnUnitActiveSec",
    "OnUnitInactiveSec",
    "OnActiveSec",
    "OnStartupSec",
)


# `[Unit]` is an ALLOW-list, like every other rule here: an unknown key, or an alias systemd
# still reads (`BindTo=` for `BindsTo=`), is refused rather than assumed harmless (commit
# security review). Ordering and documentation are always accepted.
_ORDERING = ("Description", "Documentation", "After", "Before")
# A service may depend on the release's own units and on these host units only: what a run
# needs (the Docker daemon for `docker exec` dumps, the network for remote sources).
HOST_UNITS = frozenset({"docker.service", "network.target", "network-online.target"})
_SERVICE_DEPENDS = ("Wants", "Requires")
_RUNS_DECLARED = ("OnFailure", "OnSuccess")
# PID 1 performs these as root: anything but `none` reboots, powers off or exits the host.
_ACTIONS = ("FailureAction", "SuccessAction", "StartLimitAction", "JobTimeoutAction")
_START_LIMITS = ("StartLimitIntervalSec", "StartLimitBurst")
_SERVICE_UNIT_KEYS = frozenset(
    (*_ORDERING, *_SERVICE_DEPENDS, *_RUNS_DECLARED, *_ACTIONS, *_START_LIMITS)
)
_ROOT_GROUPS = frozenset({"root", "0"})

# `[Service]` is an allow-list too (commit security review): systemd performs some keys as root
# on the service's behalf — it reads credentials, opens standard streams and `OpenFile=` paths,
# runs PAM — so an unknown key is refused, and the keys below that reach a path are checked.
_SERVICE_KEYS = frozenset(
    {
        "Type",
        "User",
        "Group",
        "SupplementaryGroups",
        "WorkingDirectory",
        "Environment",
        "EnvironmentFile",
        "UMask",
        "MemoryMax",
        "MemoryHigh",
        "TasksMax",
        "CPUQuota",
        "Nice",
        "IOWeight",
        "TimeoutStartSec",
        "TimeoutStopSec",
        "TimeoutSec",
        "RemainAfterExit",
        "SuccessExitStatus",
        "KillMode",
        "KillSignal",
        "SyslogIdentifier",
        "StandardInput",
        "StandardOutput",
        "StandardError",
        "LoadCredential",
        "LoadCredentialEncrypted",
        "SetCredential",
        "RuntimeDirectory",
        "RuntimeDirectoryMode",
        "StateDirectory",
        "StateDirectoryMode",
        "CacheDirectory",
        "LogsDirectory",
        "NoNewPrivileges",
        "ProtectSystem",
        "ProtectHome",
        "PrivateTmp",
        "PrivateDevices",
        "PrivateNetwork",
        "PrivateUsers",
        "PrivateIPC",
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectKernelLogs",
        "ProtectControlGroups",
        "ProtectClock",
        "ProtectHostname",
        "ProtectProc",
        "ProcSubset",
        "RestrictNamespaces",
        "RestrictRealtime",
        "RestrictSUIDSGID",
        "RestrictAddressFamilies",
        "LockPersonality",
        "MemoryDenyWriteExecute",
        "SystemCallFilter",
        "SystemCallArchitectures",
        "SystemCallErrorNumber",
        "CapabilityBoundingSet",
        "ReadWritePaths",
        "ReadOnlyPaths",
        "InaccessiblePaths",
        "BindPaths",
        "BindReadOnlyPaths",
        "TemporaryFileSystem",
        "ExecCondition",
        "ExecStartPre",
        "ExecStart",
        "ExecStartPost",
        "ExecReload",
        "ExecStop",
        "ExecStopPost",
        # refused below with their own reason, so they are "known" here
        "AmbientCapabilities",
        "PermissionsStartOnly",
    }
)
_STREAMS_OUT = frozenset({"journal", "null", "inherit"})


def _unit_names(values: list[str]) -> list[str]:
    return [name for value in _effective(values) for name in value.split()]


def service_refusals(text: str, *, unit: str, current: str, declared: frozenset[str]) -> list[str]:
    """Return unit-named refusals for an unsafe oneshot service; an empty list accepts it."""
    invisible = _invisible_character_refusal(text, unit)
    if invisible is not None:
        return [invisible]

    service = parse_unit(text).get("Service")
    if service is None:
        return [f"{unit} has no [Service] section"]

    refusals: list[str] = []
    section = parse_unit(text).get("Unit", {})
    refusals.extend(
        f"{unit}: {key}= is not a key this target accepts in [Unit]"
        for key in section
        if key not in _SERVICE_UNIT_KEYS
    )
    for key in _SERVICE_DEPENDS:
        refusals.extend(
            f"{unit}: {key}={name} names a unit that is neither delivered by this release nor "
            f"one of {', '.join(sorted(HOST_UNITS))}"
            for name in _unit_names(section.get(key, []))
            if name not in declared and name not in HOST_UNITS
        )
    for key in _RUNS_DECLARED:
        refusals.extend(
            f"{unit}: {key}={name} names a unit this release does not deliver"
            for name in _unit_names(section.get(key, []))
            if name not in declared
        )
    for key in _ACTIONS:
        refusals.extend(
            f"{unit}: {key}={value} makes systemd act on the host as root; only `none` is allowed"
            for value in section.get(key, [])
            if value != "none"
        )
    refusals.extend(
        f"{unit}: {key}= is not a key this target accepts in [Service]"
        for key in service
        if key not in _SERVICE_KEYS
    )
    # credentials are read by systemd as root: only the project's own host configuration
    project_etc = f"/etc/{posixpath.basename(posixpath.dirname(current))}/"
    for key in ("LoadCredential", "LoadCredentialEncrypted"):
        for value in _effective(service.get(key, [])):
            source = value.partition(":")[2]
            if not _inside(source, project_etc):
                refusals.append(
                    f"{unit}: {key}={value} must read a file under {project_etc}: systemd "
                    "reads it as root on the service's behalf"
                )
    for value in service.get("StandardInput", []):
        if value != "null":
            refusals.append(f"{unit}: StandardInput={value} — only null: systemd opens it as root")
    for key in ("StandardOutput", "StandardError"):
        refusals.extend(
            f"{unit}: {key}={value} — only {', '.join(sorted(_STREAMS_OUT))}: systemd opens "
            "a file as root"
            for value in service.get(key, [])
            if value not in _STREAMS_OUT
        )
    refusals.extend(
        f"{unit}: Group={value} must be a plain group other than root (got {value!r})"
        for value in service.get("Group", [])
        if value in _ROOT_GROUPS or not _USERNAME.fullmatch(value)
    )
    # a numeric id (`00` is GID 0 for systemd) or a specifier would slip past a name check
    refusals.extend(
        f"{unit}: SupplementaryGroups= lists plain group names other than root only (got {word!r})"
        for value in _effective(service.get("SupplementaryGroups", []))
        for word in value.split()
        if word in _ROOT_GROUPS or not _USERNAME.fullmatch(word)
    )
    for value in service.get("RemainAfterExit", []):
        if value.lower() not in ("no", "false", "0", "off"):
            refusals.append(
                f"{unit}: RemainAfterExit={value} keeps the run active, so its timer never "
                "fires it again"
            )
    for value in _effective(service.get("AmbientCapabilities", [])):
        refusals.append(f"{unit}: AmbientCapabilities={value} gives a non-root user root's powers")
    types = service.get("Type", [])
    if not types or any(value != "oneshot" for value in types):
        refusals.append(f"{unit}: Type=oneshot is required, a timer fires a run (got {types!r})")

    users = service.get("User", [])
    if not users:
        refusals.append(f"{unit}: User= must name a user other than root (no value set)")
    refusals.extend(
        f"{unit}: User={value} must be a plain username other than root (got {value!r})"
        for value in users
        if value == "root" or not _USERNAME.fullmatch(value)
    )

    memories = service.get("MemoryMax", [])
    if not memories:
        refusals.append(f"{unit}: MemoryMax= must bound the service (no value set)")
    refusals.extend(
        f"{unit}: MemoryMax={value} must be a bounded byte count (got {value!r})"
        for value in memories
        if not _memory_ok(value)
    )

    timeouts = [
        (key, value) for key in ("TimeoutStartSec", "TimeoutSec") for value in service.get(key, [])
    ]
    if not timeouts:
        refusals.append(
            f"{unit}: TimeoutStartSec= or TimeoutSec= must be set to a positive duration"
        )
    refusals.extend(
        f"{unit}: {key}={value} must be a positive duration (got {value!r})"
        for key, value in timeouts
        if not _DURATION.fullmatch(value)
    )

    starts = _effective(service.get("ExecStart", []))
    if not starts:
        refusals.append(f"{unit}: at least one ExecStart= must be set")

    app = f"{current}/app/"
    for key in _COMMAND_KEYS:
        for value in _effective(service.get(key, [])):
            if _UNSAFE_IN_COMMAND & set(value) or ";" in _words(value):
                refusals.append(f"{unit}: {key}={value} contains an unreadable command separator")
                continue
            if _PRIVILEGED & set(_prefix(value)):
                refusals.append(f"{unit}: {key}={value} runs with full privileges (prefix + or !)")
                continue
            if not _inside(_program(value), app):
                refusals.append(f"{unit}: {key} must run a program under {app} (got {value!r})")

    environment = f"{current}/release.env"
    files = _effective(service.get("EnvironmentFile", []))
    if environment not in files:
        refusals.append(f"{unit}: EnvironmentFile={environment} is required without the `-` prefix")

    if "PermissionsStartOnly" in service:
        refusals.append(f"{unit}: PermissionsStartOnly= is not allowed")
    return refusals


def timer_refusals(text: str, *, unit: str, declared: frozenset[str]) -> list[str]:
    """Return unit-named refusals for a timer that does not trigger its declared service."""
    invisible = _invisible_character_refusal(text, unit)
    if invisible is not None:
        return [invisible]

    timer = parse_unit(text).get("Timer")
    if timer is None:
        return [f"{unit} has no [Timer] section"]

    refusals: list[str] = []
    section = parse_unit(text).get("Unit", {})
    refusals.extend(
        f"{unit}: {key}= is not accepted in a timer's [Unit]: the rail restarts every timer "
        "through sudo, so anything but ordering and documentation would act on the host"
        for key in section
        if key not in _ORDERING
    )
    if not any(_effective(timer.get(key, [])) for key in TIMER_TRIGGERS):
        refusals.append(f"{unit}: at least one timer trigger must have a non-empty value")
    if "Unit" in timer:
        refusals.append(f"{unit}: Unit= is not allowed; a timer triggers its matching service")

    service = unit[: -len(".timer")] + ".service" if unit.endswith(".timer") else ""
    if service not in declared:
        refusals.append(f"{unit}: matching service {service or '(unknown)'} is not declared")
    return refusals


def remote_script(
    project: str,
    artefact: Artefact,
    units: dict[str, str],
    payload: str,
    timers: tuple[str, ...],
    params: Parameters,
) -> str:
    """Ship a release only between runs, then check what systemd loaded (spec decision 5)."""
    root = f"{params.stack_root}/{project}"
    release = f"{root}/releases/{artefact.version}"
    lines = lock_preamble(root, release)
    # a timer is always `active` while it waits: only a service can be in the middle of a run
    for unit in (name for name in units if name.endswith(".service")):
        lines.extend(
            [
                f"state=$(systemctl show --property=ActiveState --value {unit})",
                'if [ "$state" = "active" ] || [ "$state" = "activating" ]; then',
                f'  echo "{unit} is $state: a run is in progress, deploy again once it has '
                'finished" >&2',
                "  exit 1",
                "fi",
            ]
        )
    lines.extend(
        [
            *(heredoc(unit, text) for unit, text in units.items()),
            heredoc("release.env", release_env(artefact)),
            f"chmod 0644 {' '.join(units)} release.env",
            f"docker pull --quiet {artefact.image}",
            f"container=$(docker create {artefact.image} /bin/true)",
            "trap 'docker rm --force \"$container\" >/dev/null 2>&1 || true' EXIT",
            'rm -rf "$release/.app.new"',
            f'docker cp "$container:{payload}/." "$release/.app.new"',
            'rm -rf "$release/.app.old"',
            'if [ -e "$release/app" ]; then mv "$release/app" "$release/.app.old"; fi',
            'mv "$release/.app.new" "$release/app"',
            'rm -rf "$release/.app.old"',
            'ln -sfn "$release" "$root/current"',
            "sudo -n /usr/bin/systemctl daemon-reload",
        ]
    )
    for unit in units:
        lines.extend(loaded_unit_checks(unit))
    lines.extend(f"sudo -n /usr/bin/systemctl restart {timer}" for timer in timers)
    for timer in timers:
        lines.extend(
            [
                f"systemctl is-active {timer}",
                f"next=$(systemctl show --property=NextElapseUSecRealtime --value {timer})",
                '[ -n "$next" ] && [ "$next" != "n/a" ] || '
                f'{{ echo "{timer} has no next elapse" >&2; exit 1; }}',
            ]
        )
    return "\n".join([*lines, ""])


class PrivateTimers(RemoteTarget):
    """Oneshot units use a relocatable payload; its version command replaces HTTP checks."""

    ssh_host_must_be_declared = True

    def __init__(self, repo: Path, cfg: RailConfig, **kwargs: Any) -> None:
        super().__init__(repo, cfg, **kwargs)
        deploy = cfg.deploy
        if (
            deploy is None
            or deploy.units is None
            or deploy.payload is None
            or deploy.version_command is None
        ):
            # The manifest model requires these before a target can be built.
            raise DeployError(
                "target private-timers needs deploy.units, deploy.payload "
                "and deploy.version_command"
            )
        self.unit_paths = deploy.units
        self.payload = deploy.payload
        self.version_command = deploy.version_command

    @property
    def current(self) -> str:
        return f"{self.params.stack_root}/{self.cfg.project}/current"

    @property
    def services(self) -> tuple[str, ...]:
        return tuple(
            PurePosixPath(path).name for path in self.unit_paths if path.endswith(".service")
        )

    @property
    def timers(self) -> tuple[str, ...]:
        return tuple(
            PurePosixPath(path).name for path in self.unit_paths if path.endswith(".timer")
        )

    def script_for(self, artefact: Artefact) -> str:
        units = {
            PurePosixPath(path).name: self.file_at(artefact.sha, path, "the unit file")
            for path in self.unit_paths
        }
        declared = frozenset(self.services)
        refusals: list[str] = []
        for unit, text in units.items():
            refusals.extend(
                service_refusals(text, unit=unit, current=self.current, declared=frozenset(units))
                if unit.endswith(".service")
                else timer_refusals(text, unit=unit, declared=declared)
            )
        if refusals:
            raise DeployError(
                "the released units are refused before the first ssh — " + "; ".join(refusals)
            )
        return remote_script(
            self.cfg.project, artefact, units, self.payload, self.timers, self.params
        )

    def describe(self, artefact: Artefact) -> str:
        root = f"{self.params.stack_root}/{self.cfg.project}"
        return (
            f"ssh {self.params.ssh_host}: release {artefact.version} under {root}, "
            f"{len(self.unit_paths)} unit(s), restart {', '.join(self.timers)}"
        )

    def steps(self, artefact: Artefact) -> list[Step]:
        script = self.script_for(artefact)
        return [
            Step(self.describe(artefact), ssh_argv(self.params), script),
            Step(
                f"ssh {self.params.ssh_host}: {' '.join(self.version_command)} == "
                f"{artefact.version} / {artefact.sha[:12]} / {artefact.digest}",
                ssh_argv(self.params),
                self.version_script(),
            ),
        ]

    def version_script(self) -> str:
        command = " ".join(shlex.quote(word) for word in self.version_command)
        # over ssh there is no `EnvironmentFile=`: load the file the units read, which the rail
        # wrote and which carries no secret (spec decision 4), so the command answers the
        # identity the units see
        return (
            "set -euo pipefail\n"
            f"set -a; . {self.current}/release.env; set +a\n"
            f"cd {self.current}/app\n"
            f"exec ./{command}\n"
        )

    def verify(self, artefact: Artefact) -> LiveVersion:
        """Read identity from the release without starting a service or using HTTP."""
        try:
            done = self._run(
                list(ssh_argv(self.params)),
                input=self.version_script(),
                capture_output=True,
                text=True,
                check=False,
                timeout=self.params.remote_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise DeployError(
                self.redact(
                    f"remote verification of {artefact.version} timed out after "
                    f"{self.params.remote_timeout}s; the ssh session was killed"
                )
            ) from exc
        except (FileNotFoundError, OSError) as exc:
            raise DeployError(self.redact(f"ssh is not available on this host: {exc}")) from exc
        if done.returncode != 0:
            raise DeployError(
                self.redact(
                    f"the version command failed (exit {done.returncode}): "
                    + _tail(done.stderr or done.stdout)
                )
            )
        try:
            data = json.loads(done.stdout)
        except (ValueError, UnicodeDecodeError) as exc:
            raise DeployError(self.redact("the version command does not answer JSON")) from exc
        if not isinstance(data, dict):
            raise DeployError(self.redact("the version command must answer a JSON object"))
        fields = ("project", "version", "git_sha", "image_digest")
        missing = [field for field in fields if not data.get(field)]
        if missing:
            raise DeployError(self.redact("the version command misses " + ", ".join(missing)))
        live = LiveVersion(*(str(data[field]) for field in fields))
        mismatch = [
            f"{field}: live {getattr(live, field)!r} ≠ artefact {value!r}"
            for field, value in (
                ("version", artefact.version),
                ("git_sha", artefact.sha),
                ("image_digest", artefact.digest),
            )
            if getattr(live, field) != value
        ]
        if mismatch:
            raise DeployError(
                self.redact("the live service differs from the artefact — " + "; ".join(mismatch))
            )
        return live
