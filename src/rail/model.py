"""The per-project manifest (`rail.yaml`).

Deliberately tiny: anything not declared here is a tier default versioned in red-rail,
which is what keeps twenty manifests from diverging.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

MANIFEST_NAME = "rail.yaml"

# A site is a label the repository may carry; its address lives on the host that deploys
# (spec 2026-09-23-sites-on-the-host). No dot and no colon: no address, no domain fits.
SITE_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
# the rail's own variable: in a compose file (`${BIND_ADDRESS}:9204:9204`) and, behind a site,
# as the healthcheck's host
ADDRESS_TOKEN = "${BIND_ADDRESS}"
# match-at-start only: a token followed by `:9204@other.example` used to pass (`:` opened the
# allowed set) even though that is userinfo, not a port — the URL's real host is
# `other.example`. An optional port is now the only thing allowed between the token and the
# next path/query/fragment boundary or the end of the string.
_TOKEN_HOST = re.compile(rf"^https?://{re.escape(ADDRESS_TOKEN)}(?::\d+)?(?=[/?#]|\Z)")
# A unit file inside the repository: relative, no `.`/`..` segment, and a plain service name
# (no template, no timer). The file name is the unit's name for systemd and for the sudoers
# rule alike.
UNIT_NAME_PATTERN = r"[a-z0-9]+(?:-[a-z0-9]+)*\.service"
_UNIT_PATH = re.compile(rf"^(?:[A-Za-z0-9._-]+/)*{UNIT_NAME_PATTERN}$")
TIMER_UNIT_NAME_PATTERN = r"[a-z0-9]+(?:-[a-z0-9]+)*\.(?:service|timer)"
_TIMER_UNIT_PATH = re.compile(rf"^(?:[A-Za-z0-9._-]+/)*{TIMER_UNIT_NAME_PATTERN}$")
_VERSION_WORD = re.compile(r"^[A-Za-z0-9._/=-]+$")
# The binary's path inside the released image: absolute and of safe characters, because it
# reaches the remote script.
_BINARY_PATH = re.compile(r"^(?:/[A-Za-z0-9._-]+)+$")
# What a systemd release carries next to the binary and the unit: the values `/version`
# answers, read through the unit's `EnvironmentFile=`.
RELEASE_ENV = "release.env"


def _has_dot_segment(path: str) -> bool:
    return any(part in {".", ".."} for part in path.split("/"))


def token_is_the_host(url: str) -> bool:
    """The token is the URL's host and nothing else: a site's address may fill only that."""
    return _TOKEN_HOST.match(url) is not None


class Tier(StrEnum):
    """Maturity tier. Each tier includes the previous one; the audit scores against it."""

    BOOTSTRAP = "bootstrap"
    DEV = "dev"
    PROD = "prod"


class Stack(StrEnum):
    PYTHON = "python"
    GO = "go"
    RUST = "rust"
    TYPESCRIPT = "typescript"
    DOCS = "docs"


class LedgerBackend(StrEnum):
    """Where evidence is authoritative: the repository's receipts, or brain-v42 (shared)."""

    FILE = "file"
    BRAIN = "brain"


class DeployTarget(StrEnum):
    VPS_TRAEFIK = "vps-traefik"
    PRIVATE_COMPOSE = "private-compose"
    PRIVATE_SYSTEMD = "private-systemd"
    PRIVATE_TIMERS = "private-timers"


PRIVATE_TARGETS = frozenset(
    {DeployTarget.PRIVATE_COMPOSE, DeployTarget.PRIVATE_SYSTEMD, DeployTarget.PRIVATE_TIMERS}
)


class DeployConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: DeployTarget
    healthcheck: str | None = Field(default=None, pattern=r"^https?://")
    site: str | None = Field(default=None, pattern=SITE_PATTERN)
    unit: str | None = None  # private-systemd: the unit file, read at the released commit
    binary: str | None = None  # private-systemd: the binary's path inside the released image
    payload: str | None = None
    units: tuple[str, ...] | None = None
    version_command: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _a_site_and_its_token_go_together(self) -> DeployConfig:
        if self.site is not None and self.target not in PRIVATE_TARGETS:
            raise ValueError(
                "deploy.site applies to a private target only "
                "(private-compose, private-systemd, private-timers)"
            )
        if self.target is DeployTarget.PRIVATE_TIMERS:
            # verified without HTTP (spec 2026-10-06-private-timers-target, decision 8): a
            # healthcheck here would be ignored silently
            if self.healthcheck is not None:
                raise ValueError("deploy.healthcheck does not apply to target private-timers")
            return self
        if self.healthcheck is None:
            raise ValueError(f"deploy.healthcheck is required by target {self.target.value}")
        if self.site is None and ADDRESS_TOKEN in self.healthcheck:
            raise ValueError(
                f"deploy.healthcheck uses {ADDRESS_TOKEN} but no deploy.site says whose "
                "address it is"
            )
        if self.site is not None and not token_is_the_host(self.healthcheck):
            raise ValueError(
                f"behind deploy.site the healthcheck host is {ADDRESS_TOKEN}, filled from the "
                f"host's sites file (got {self.healthcheck})"
            )
        return self

    @model_validator(mode="after")
    def _a_systemd_target_names_its_unit_and_its_binary(self) -> DeployConfig:
        systemd = self.target is DeployTarget.PRIVATE_SYSTEMD
        for name, value in (("unit", self.unit), ("binary", self.binary)):
            if systemd and value is None:
                raise ValueError(f"deploy.{name} is required by target private-systemd")
            if not systemd and value is not None and self.target is DeployTarget.PRIVATE_TIMERS:
                raise ValueError(f"deploy.{name} does not apply to target private-timers")
            if not systemd and value is not None:
                raise ValueError(f"deploy.{name} applies to target private-systemd only")
        if self.unit is not None and (
            not _UNIT_PATH.fullmatch(self.unit) or _has_dot_segment(self.unit)
        ):
            raise ValueError(
                "deploy.unit must be a relative path inside the repository to a plain service "
                f"file ({UNIT_NAME_PATTERN}), got {self.unit!r}"
            )
        if self.binary is not None and (
            not _BINARY_PATH.fullmatch(self.binary) or _has_dot_segment(self.binary)
        ):
            raise ValueError(
                "deploy.binary must be an absolute path of safe characters inside the image, "
                f"got {self.binary!r}"
            )
        if self.unit is not None and self.binary is not None:
            # the release directory holds the binary, the unit and `release.env` side by side
            # (spec 2026-09-24-private-systemd-target, decision 4), each under its file name
            name = self.binary.rsplit("/", 1)[-1]
            held = {self.unit.rsplit("/", 1)[-1]: "the unit", RELEASE_ENV: RELEASE_ENV}
            if name in held:
                raise ValueError(
                    f"deploy.binary is copied into the release as {name!r}, which would "
                    f"overwrite {held[name]}: the binary's file name must differ from the "
                    f"unit's and from {RELEASE_ENV}"
                )
        return self

    @model_validator(mode="after")
    def _a_timers_target_names_its_payload_its_units_and_its_version(self) -> DeployConfig:
        for name in ("payload", "units", "version_command"):
            value = getattr(self, name)
            if self.target is DeployTarget.PRIVATE_TIMERS and value is None:
                raise ValueError(f"deploy.{name} is required by target private-timers")
            if self.target is not DeployTarget.PRIVATE_TIMERS and value is not None:
                raise ValueError(f"deploy.{name} applies to target private-timers only")

        if self.payload is not None and (
            not _BINARY_PATH.fullmatch(self.payload) or _has_dot_segment(self.payload)
        ):
            raise ValueError(
                "deploy.payload must be an absolute path of safe characters without dot segments"
            )
        if self.units is not None:
            names = []
            for path in self.units:
                name = path.rsplit("/", 1)[-1]
                if not _TIMER_UNIT_PATH.fullmatch(path) or _has_dot_segment(path):
                    raise ValueError(f"deploy.units contains an invalid unit path: {path!r}")
                names.append(name)
            if not 1 <= len(self.units) <= 32:
                raise ValueError("deploy.units must contain between 1 and 32 entries")
            if len(names) != len(set(names)):
                raise ValueError("deploy.units file names must be unique")
            available = set(names)
            missing = [
                name[:-6] + ".service"
                for name in names
                if name.endswith(".timer") and name[:-6] + ".service" not in available
            ]
            if missing:
                raise ValueError(
                    "deploy.units timers require matching services: " + ", ".join(missing)
                )
        if self.version_command is not None:
            command = self.version_command
            if (
                not 1 <= len(command) <= 16
                or not all(_VERSION_WORD.fullmatch(word) for word in command)
                or command[0].startswith("/")
                or _has_dot_segment(command[0])
            ):
                raise ValueError(
                    "deploy.version_command must contain safe words and a relative executable"
                )
        return self


# The generated files a repository declares the reviewer never sends to a judge, ADDED to the
# reviewer's own list (`policy.ignored_globs`), and the directories no such glob may reach.
IGNORED_GLOBS = "review.ignored_globs"
JUDGED_DIRECTORIES = ("src", ".github")

# A project is named `red-<slug>`, the name `rail new` gives. A repository that predates ReD's
# naming keeps its public name by being listed here (ticket 7daf7462).
PROJECT_PATTERN = r"^red-[a-z0-9]+(-[a-z0-9]+)*$"
ADMITTED_PROJECTS = frozenset({"brain-v42"})

# Where the design, plan and docs layout gates read `specs/`, `plans/` and `adr/`. A project
# whose docs live in a private clone declares that root (ticket 7daf7462).
DOCS_ROOT = "docs.root"


class GateOverride(BaseModel):
    """A declared exception. `reason` is mandatory so the audit can show it instead of hiding it."""

    model_config = ConfigDict(extra="forbid")

    value: Any
    reason: str = Field(min_length=1)


class RailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rail: Literal[1]
    project: str
    brain_key: str = Field(min_length=1, max_length=50)
    tier: Tier
    stack: Stack
    ledger: LedgerBackend = LedgerBackend.FILE
    ticket: UUID | None = None  # the delivery ticket (`red → <project>`), brain ledger only
    deploy: DeployConfig | None = None
    gates: dict[str, GateOverride] = Field(default_factory=dict)

    @field_validator("project")
    @classmethod
    def _project_name(cls, value: str) -> str:
        if value in ADMITTED_PROJECTS or re.fullmatch(PROJECT_PATTERN, value):
            return value
        raise ValueError(
            f"must match {PROJECT_PATTERN} or be one of {', '.join(sorted(ADMITTED_PROJECTS))}"
        )

    @model_validator(mode="after")
    def _prod_requires_deploy(self) -> RailConfig:
        if self.tier is Tier.PROD and self.deploy is None:
            raise ValueError("tier 'prod' requires a 'deploy' target")
        return self

    @model_validator(mode="after")
    def _ticket_follows_the_ledger(self) -> RailConfig:
        if self.ledger is LedgerBackend.BRAIN and self.ticket is None:
            raise ValueError("ledger 'brain' requires 'ticket' (the delivery ticket UUID)")
        if self.ledger is LedgerBackend.FILE and self.ticket is not None:
            raise ValueError("'ticket' is only meaningful with ledger 'brain'")
        return self

    @model_validator(mode="after")
    def _one_source_for_the_address(self) -> RailConfig:
        if self.deploy is not None and self.deploy.site is not None:
            if "deploy.bind_address" in self.gates:
                raise ValueError(
                    "deploy.site and a gates override of deploy.bind_address are two sources "
                    "for one address: the site's comes from the host, drop the override"
                )
        return self

    @model_validator(mode="after")
    def _docs_root_stays_in_the_repository(self) -> RailConfig:
        override = self.gates.get(DOCS_ROOT)
        if override is None:
            return self
        root = override.value
        if (
            not isinstance(root, str)
            or not root
            or "\\" in root
            or Path(root).is_absolute()
            or ".." in Path(root).parts
        ):
            raise ValueError(
                f"{DOCS_ROOT}: {root!r} must be a relative path inside the repository, "
                "such as 'internal/docs'"
            )
        if Path(root) == Path("docs"):
            # declared, an absent root is skipped; the default's absence must stay a failure
            raise ValueError(f"{DOCS_ROOT}: 'docs' is the default, drop the override")
        return self

    @model_validator(mode="after")
    def _ignored_globs_stay_out_of_the_code(self) -> RailConfig:
        """`review.ignored_globs` hides files from every judge (ticket f0aa9c29). Each glob
        must start with a literal directory other than src/ and .github/: a glob anchored
        there can never reach the code under src/, the CI workflows or rail.yaml itself."""
        override = self.gates.get(IGNORED_GLOBS)
        if override is None:
            return self
        globs = override.value
        if not isinstance(globs, list) or not globs or not all(isinstance(g, str) for g in globs):
            raise ValueError(f"{IGNORED_GLOBS}: the value is a non-empty list of globs")
        for glob in globs:
            head, slash, _ = glob.partition("/")
            if not slash or not head or any(c in head for c in "*?["):
                raise ValueError(
                    f"{IGNORED_GLOBS}: {glob!r} must start with a literal directory, such as "
                    "'web/dist/*': without one it can reach the code at the root and under src/"
                )
            if head in JUDGED_DIRECTORIES:
                raise ValueError(
                    f"{IGNORED_GLOBS}: {glob!r} would hide {head}/, which a judge always reads"
                )
        return self


MISSING_HINT = (
    f"{MANIFEST_NAME} is missing (run `rail new` for a new project, or write it for an "
    "existing repository)"
)


def try_load_rail_config(repo: Path) -> RailConfig | None:
    """The manifest when it is present and valid, else None — the one definition of
    "unreadable" shared by every gate and command (`manifest_problem` says why)."""
    try:
        return load_rail_config(repo)
    except (FileNotFoundError, ValidationError):
        return None


def manifest_problem(repo: Path) -> str | None:
    """Why the manifest cannot be read, in the words every gate and command repeat: missing
    (with the way out) or invalid (with the first error); None when it loads."""
    try:
        load_rail_config(repo)
    except FileNotFoundError:
        return MISSING_HINT
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        return f"{MANIFEST_NAME} is invalid: {location}: {first['msg']}"
    return None


@dataclass(frozen=True, slots=True)
class Declarations:
    """What the manifest declares, field by field; with no manifest at all, the documented
    defaults (`ledger: file`) and None where there is no default. `cfg` is the full manifest."""

    project: str | None
    stack: Stack | None
    ledger: LedgerBackend
    cfg: RailConfig | None


def declarations(repo: Path) -> Declarations | str:
    """The manifest's declarations, or why it cannot be read. Defaults apply ONLY when the file
    is absent: an invalid manifest that declares `ledger: brain` must never make a gate judge
    `docs/receipts` as authoritative (spec decision 4)."""
    if not (repo / MANIFEST_NAME).exists():
        return Declarations(project=None, stack=None, ledger=LedgerBackend.FILE, cfg=None)
    cfg = try_load_rail_config(repo)
    if cfg is None:
        return manifest_problem(repo) or f"{MANIFEST_NAME} unreadable"
    return Declarations(project=cfg.project, stack=cfg.stack, ledger=cfg.ledger, cfg=cfg)


def load_rail_config(repo: Path) -> RailConfig:
    """Read and validate `<repo>/rail.yaml`. A missing manifest is an error, not a default."""
    path = repo / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(
            f"{MISSING_HINT.replace(' is missing', f' is missing in {repo}', 1)}"
        )
    raw = yaml.safe_load(path.read_text()) or {}
    return RailConfig.model_validate(raw)
