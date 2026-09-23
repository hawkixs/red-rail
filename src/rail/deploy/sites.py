"""Where a private target's machine is, known only to the host that deploys (spec
`2026-09-23-sites-on-the-host`). A repository names a site; this private file on the host says
where it is, so no machine address is ever committed: not in `rail.yaml`, not in a receipt."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from rail.deploy import DeployError
from rail.model import ADDRESS_TOKEN, SITE_PATTERN
from rail.private import PrivateFileError, read_private_file

DEFAULT_SITES_FILE = "~/.config/red-rail/sites.yaml"
SITES_FILE_VARIABLE = "RAIL_SITES_FILE"  # names the path; the environment never holds an address

Address = IPv4Address | IPv6Address


class Site(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    address: Address

    @field_validator("address", mode="before")
    @classmethod
    def _written_as_text(cls, value: object) -> object:
        # YAML 1.1 reads an all-digit IPv6 such as 2001:0:0:0:0:0:0:1 as a base-60 integer, and
        # an integer would pass for an IPv4 address: only a string is an address here
        if not isinstance(value, str):
            raise ValueError(
                f"write the address as a quoted string, not a YAML {type(value).__name__}"
            )
        return value

    @field_validator("address")
    @classmethod
    def _not_everywhere(cls, value: Address) -> Address:
        if value.is_unspecified:
            raise ValueError(
                f"{value} publishes on every interface, which a private target refuses"
            )
        return value

    @field_validator("address")
    @classmethod
    def _not_scoped(cls, value: Address) -> Address:
        if isinstance(value, IPv6Address) and value.scope_id is not None:
            raise ValueError(
                "a scoped address: Docker can neither bind it to an interface by that name, "
                "nor can a scope id be written in a URL"
            )
        return value


class SitesFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sites: dict[str, Site]

    @field_validator("sites")
    @classmethod
    def _labels(cls, value: dict[str, Site]) -> dict[str, Site]:
        bad = sorted(name for name in value if not re.fullmatch(SITE_PATTERN, name))
        if bad:
            raise ValueError(f"site names are labels ({SITE_PATTERN}): {len(bad)} name(s) are not")
        return value


def sites_file(environ: Mapping[str, str] | None = None) -> Path:
    """`RAIL_SITES_FILE` when set, else `~/.config/red-rail/sites.yaml`, user-expanded."""
    env = os.environ if environ is None else environ
    return Path(env.get(SITES_FILE_VARIABLE) or DEFAULT_SITES_FILE).expanduser()


def load_site(name: str, path: Path | None = None) -> Site:
    """The site `name` from the host's private sites file. Every failure is a `DeployError`
    naming the site, the file and the fix; the target calls this before any step is planned."""
    try:
        where = sites_file() if path is None else path
    except RuntimeError as exc:  # `~user` of an unknown user: a refusal, never a crash
        raise DeployError(f"site {name}: {exc} — name the file with {SITES_FILE_VARIABLE}") from exc
    fix = f'declare it on this host: `sites: {{{name}: {{address: "…"}}}}` in {where}, mode 0600'
    try:
        raw = read_private_file(where)
    except PrivateFileError as exc:
        raise DeployError(f"site {name}: {exc} — {fix}") from exc
    try:
        document = SitesFile.model_validate(yaml.safe_load(raw) or {})
    except ValidationError as exc:
        raise DeployError(
            f"site {name}: {where} is not a valid sites file: {_where_it_fails(exc)} — {fix}"
        ) from exc
    except yaml.YAMLError as exc:
        # a YAML error quotes the line it stopped at, which may hold an address
        raise DeployError(
            f"site {name}: {where} is not a valid sites file: not YAML ({type(exc).__name__}) "
            f"— {fix}"
        ) from exc
    site = document.sites.get(name)
    if site is None:
        known = ", ".join(sorted(document.sites)) or "none"
        raise DeployError(f"site {name} is not declared in {where} (known: {known}) — {fix}")
    return site


def _where_it_fails(exc: ValidationError) -> str:
    """Each error's location and message, never its input: pydantic's text quotes the offending
    value, and a refusal is printed where the file's addresses must not go (review finding)."""

    def shown(part: object) -> str:
        # a site's name is a key of the location, and a mistyped name may be an address
        return part if isinstance(part, str) and re.fullmatch(SITE_PATTERN, part) else "…"

    return "; ".join(
        f"{'.'.join(shown(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )


def substitute_address(url: str, address: Address) -> str:
    """The healthcheck with the rail's token replaced; an IPv6 host is bracketed in a URL."""
    host = f"[{address}]" if address.version == 6 else str(address)
    return url.replace(ADDRESS_TOKEN, host)


def redact_address(text: str, address: Address, label: str) -> str:
    """`text` with every occurrence of `address` replaced by `label`. Number boundaries are
    respected (`192.0.2.1` never matches inside `192.0.2.10`); an IPv6 address is matched
    case-insensitively, its bracketed form is replaced brackets included, and a bare address
    immediately followed by `:<port>` — Docker's own bind-conflict wording — is matched too,
    even though a bare `:` would otherwise read as more of the address."""
    literal = re.escape(str(address))
    if address.version == 4:
        return re.sub(rf"(?<![\d.]){literal}(?!\.?\d)", label, text)
    text = re.sub(rf"\[{literal}\]", label, text, flags=re.IGNORECASE)
    text = re.sub(rf"(?<![0-9A-Fa-f:]){literal}(?![0-9A-Fa-f:])", label, text, flags=re.IGNORECASE)
    return re.sub(
        rf"(?<![0-9A-Fa-f:]){literal}(?=:\d{{1,5}}(?![0-9A-Fa-f:]))",
        label,
        text,
        flags=re.IGNORECASE,
    )
