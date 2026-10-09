"""Allow-list rules for the `private-timers` units (spec 2026-10-06-private-timers-target).

The unit syntax is deliberately read through `private_systemd`'s small parser and helpers.
Values the rail cannot establish as safe are refused before any remote operation.
"""

import json
import posixpath
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


# The release's identity, read and never executed (operator decision 2026-10-06): a static file
# the project's image writes into the payload at build, and the release.env the rail wrote.
IDENTITY_FILE = ".rail-identity.json"
IDENTITY_MARKER = "--- release.env ---"
_IDENTITY_LIMIT = 65536


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
# directories systemd creates as root, chowns to `User=` when they exist, and may remove
_DIRECTORY_KEYS = ("RuntimeDirectory", "StateDirectory", "CacheDirectory", "LogsDirectory")
# a run must not outlive its bound: the whole control group is killed at the timeout
_KILL_MODES = frozenset({"control-group", "mixed"})
# the one supplementary group the spec takes knowingly (`docker exec` dumps)
_SUPPLEMENTARY_GROUPS = frozenset({"docker"})
_TRUE = frozenset({"yes", "true", "1", "on"})


def _project_name(name: str, project: str) -> bool:
    return (
        bool(name)
        and posixpath.normpath(name) == name
        and not name.startswith("/")
        and (name == project or name.startswith((f"{project}/", f"{project}-")))
    )


def _project_directory(word: str, project: str) -> bool:
    """`<project>`, `<project>-…` or `<project>/…`, normalised; with `source:destination`
    systemd also creates, as root, a symlink at the destination, so both sides are checked
    (commit security review)."""
    parts = word.split(":")
    return 1 <= len(parts) <= 2 and all(_project_name(part, project) for part in parts)


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
    project = posixpath.basename(posixpath.dirname(current))
    project_etc = f"/etc/{project}/"
    for key in _DIRECTORY_KEYS:
        refusals.extend(
            f"{unit}: {key}={word} must name {project}, {project}-… or {project}/…: systemd "
            "creates and chowns it as root"
            for value in _effective(service.get(key, []))
            for word in value.split()
            if not _project_directory(word, project)
        )
    for value in _effective(service.get("EnvironmentFile", [])):
        if value != f"{current}/release.env" and not _inside(value, project_etc):
            refusals.append(
                f"{unit}: EnvironmentFile={value} — only {current}/release.env or a file under "
                f"{project_etc}, without `-`: systemd reads it as root"
            )
    refusals.extend(
        f"{unit}: KillMode={value} lets a run outlive its timeout; only control-group or mixed"
        for value in service.get("KillMode", [])
        if value not in _KILL_MODES
    )
    no_new = service.get("NoNewPrivileges", [])
    if not no_new or no_new[-1].lower() not in _TRUE:
        refusals.append(f"{unit}: NoNewPrivileges=yes is required")
    if "Install" in parse_unit(text):
        refusals.append(
            f"{unit}: an [Install] section is not accepted on a service: its timer fires it"
        )
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
    users = service.get("User", [])
    refusals.extend(
        f"{unit}: Group={value} must be the user's own group ({users[-1] if users else 'User='})"
        for value in service.get("Group", [])
        if not users or value != users[-1]
    )
    refusals.extend(
        f"{unit}: SupplementaryGroups= accepts {', '.join(sorted(_SUPPLEMENTARY_GROUPS))} only "
        f"(got {word!r})"
        for value in _effective(service.get("SupplementaryGroups", []))
        for word in value.split()
        if word not in _SUPPLEMENTARY_GROUPS
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
    install = parse_unit(text).get("Install", {})
    refusals.extend(
        f"{unit}: [Install] {key}= is not accepted: only WantedBy=timers.target"
        for key in install
        if key != "WantedBy"
    )
    refusals.extend(
        f"{unit}: [Install] WantedBy={value} — only timers.target"
        for value in install.get("WantedBy", [])
        if value != "timers.target"
    )
    if not any(_effective(timer.get(key, [])) for key in TIMER_TRIGGERS):
        refusals.append(f"{unit}: at least one timer trigger must have a non-empty value")
    if "Unit" in timer:
        refusals.append(f"{unit}: Unit= is not allowed; a timer triggers its matching service")

    service = unit[: -len(".timer")] + ".service" if unit.endswith(".timer") else ""
    if service not in declared:
        refusals.append(f"{unit}: matching service {service or '(unknown)'} is not declared")
    return refusals


def wants_link_checks(timer: str, unit_dir: str = "/etc/systemd/system") -> list[str]:
    """Refuse, before any change, a boot link that does not name the timer's stable path.
    `systemctl enable` writes `timers.target.wants/<timer>` to the RESOLVED unit, inside one
    release: once that release is gone the link dangles and the timer no longer starts at boot
    (ticket 2adbbadb). The link must read `<unit_dir>/<timer>`, which follows `current`; no link
    yet is fine — the migration enables the timers after the first delivery."""
    wants = f"{unit_dir}/timers.target.wants/{timer}"
    stable = f"{unit_dir}/{timer}"
    return [
        f"if [ -e {wants} ] || [ -L {wants} ]; then",
        f"  wants=$(readlink {wants} || true)",
        f'  if [ "$wants" != "{stable}" ]; then',
        f'    echo "{wants} points at ${{wants:-no link target}}, not {stable}: replace it '
        f'with ln -sfn {stable} {wants}" >&2',
        "    exit 1",
        "  fi",
        "fi",
    ]


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
    for timer in timers:
        lines.extend(wants_link_checks(timer))
    # a timer is always `active` while it waits: only a service can be in the middle of a run
    running_check = [
        line
        for unit in (name for name in units if name.endswith(".service"))
        for line in (
            f"state=$(systemctl show --property=ActiveState --value {unit})",
            'if [ "$state" = "active" ] || [ "$state" = "activating" ]; then',
            f'  echo "{unit} is $state: a run is in progress, deploy again once it has '
            'finished" >&2',
            "  exit 1",
            "fi",
        )
    ]
    lines.extend(running_check)
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
            # a timer may have fired during the pull: check again, right before the switch
            *running_check,
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
        if deploy is None or deploy.units is None or deploy.payload is None:
            # The manifest model requires these before a target can be built.
            raise DeployError("target private-timers needs deploy.units and deploy.payload")
        self.unit_paths = deploy.units
        self.payload = deploy.payload

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
                f"ssh {self.params.ssh_host}: read app/{IDENTITY_FILE} and release.env == "
                f"{artefact.version} / {artefact.sha[:12]} / {artefact.digest}",
                ssh_argv(self.params),
                self.identity_script(),
            ),
        ]

    def identity_script(self) -> str:
        """Read, never run: the payload's identity file and the release.env the rail wrote,
        through `current`, as the deploy account. Running the release's own code here would
        give it the deploy account's rights, outside every rule its units obey."""
        identity = f"{self.current}/app/{IDENTITY_FILE}"
        return (
            "set -euo pipefail\n"
            # the payload comes from the image: a symlink would make this account read
            # whatever it points at
            f"[ -f {identity} ] && [ ! -L {identity} ] || "
            f"{{ echo 'app/{IDENTITY_FILE} must be a regular file' >&2; exit 1; }}\n"
            f"head -c {_IDENTITY_LIMIT} {identity}\n"
            f"printf '\\n%s\\n' '{IDENTITY_MARKER}'\n"
            f"head -c {_IDENTITY_LIMIT} {self.current}/release.env\n"
        )

    def verify(self, artefact: Artefact) -> LiveVersion:
        """The identity the payload was built with, and the digest of the release `current`
        points at, compared with the artefact (spec decision 8). No HTTP, no execution."""
        try:
            done = self._run(
                list(ssh_argv(self.params)),
                input=self.identity_script(),
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
                    f"reading the release identity failed (exit {done.returncode}): "
                    + _tail(done.stderr or done.stdout)
                )
            )
        # the LAST marker is the one this script printed: the identity file comes from the
        # image and must not be able to supply its own release.env (final review, round 2)
        identity, marker, environment = done.stdout.rpartition(f"\n{IDENTITY_MARKER}\n")
        if not marker:
            raise DeployError(
                self.redact("the identity read returned no release.env: the release is incomplete")
            )
        try:
            data = json.loads(identity)
        except ValueError as exc:
            raise DeployError(
                self.redact(f"app/{IDENTITY_FILE} is not JSON: the image must write it")
            ) from exc
        if not isinstance(data, dict):
            raise DeployError(self.redact(f"app/{IDENTITY_FILE} must hold a JSON object"))
        fields = ("project", "version", "git_sha")
        missing = [
            field for field in fields if not isinstance(data.get(field), str) or not data[field]
        ]
        if missing:
            raise DeployError(self.redact(f"app/{IDENTITY_FILE} misses " + ", ".join(missing)))
        digest = next(
            (
                line.partition("=")[2]
                for line in environment.splitlines()
                if line.startswith("IMAGE_DIGEST=")
            ),
            "",
        )
        if not digest:
            raise DeployError(self.redact("release.env carries no IMAGE_DIGEST"))
        data = {**{field: data[field] for field in fields}, "image_digest": digest}
        if data["project"] != self.cfg.project:
            raise DeployError(
                self.redact(
                    f"the payload's project: live {data['project']!r} ≠ {self.cfg.project!r}"
                )
            )
        fields = ("project", "version", "git_sha", "image_digest")
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
