"""Where the rail finds brain-v42: loopback or a host site, and a private bearer token."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Address, IPv6Network, ip_address
from pathlib import Path
from urllib.parse import urlsplit

from rail.deploy import DeployError
from rail.deploy.sites import INTERFACE_PATTERN, Address, SiteBinding, declared_site, sites_file
from rail.model import ADDRESS_TOKEN
from rail.private import PrivateFileError, private_token

BRAIN_SITE = "brain"
BRAIN_PORT = 8765
DEFAULT_URL = f"http://127.0.0.1:{BRAIN_PORT}/mcp"
DEFAULT_TOKEN_FILE = "~/.config/red-rail/brain-token"  # the rail's own private file


# Where brain may live: loopback, or the private ranges of RFC 1918 and RFC 4193 — an explicit
# allow-list, because `ipaddress.is_private` also admits 6to4, Teredo, the documentation and the
# reserved ranges, which may route to a public endpoint. Built from integers: this public
# repository carries no address literal outside the documentation ranges.
ADMITTED = (
    IPv4Network((127 << 24, 8)),  # loopback
    IPv6Network((1, 128)),  # ::1
    IPv4Network((10 << 24, 8)),  # RFC 1918
    IPv4Network(((172 << 24) | (16 << 16), 12)),  # RFC 1918
    IPv4Network(((192 << 24) | (168 << 16), 16)),  # RFC 1918
    IPv6Network((0xFC00 << 112, 7)),  # RFC 4193 unique local
)


def is_reachable(url: str) -> bool:
    """Only `localhost` and literal loopback or private addresses: a DNS name may resolve
    anywhere."""
    try:
        host = urlsplit(url).hostname
        if host == "localhost":
            return True
        address = ip_address(host or "")
    except ValueError:
        return False
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address.version == net.version and address in net for net in ADMITTED)


def is_loopback(url: str) -> bool:
    try:
        host = urlsplit(url).hostname
        return host == "localhost" or ip_address(host or "").is_loopback
    except ValueError:
        return False


def route_interface(address: Address) -> str | None:
    """Ask the kernel, not the private-address allow-list, which interface carries the bearer.
    An unavailable query refuses the connection without exposing command output. An
    IPv4-mapped address travels over IPv4, so the IPv4 table answers for it (review finding)."""
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    try:
        result = subprocess.run(
            ["ip", "-o", "route", "get", str(address)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):  # ValueError: undecodable output
        return None
    if result.returncode != 0:
        return None
    tokens = result.stdout.split()
    try:
        interface = tokens[tokens.index("dev") + 1]
    except (ValueError, IndexError):
        return None
    return interface if re.fullmatch(INTERFACE_PATTERN, interface) else None


@dataclass(frozen=True, slots=True)
class BrainSettings:
    """The bearer is read from a private file and from nowhere else (reference client's
    rule): `RAIL_BRAIN_TOKEN_FILE` names the path, the environment never holds the value.
    A remote site declares its tunnel: `sites: {brain: {address: "…", interface: wg0}}`."""

    url: str
    token: str
    token_file: Path
    site: SiteBinding | None = None
    interface: str | None = None  # declared for a non-loopback site: the route is checked
    route_of: Callable[[Address], str | None] | None = None

    @property
    def shown(self) -> str:
        return self.redact(self.url)

    def redact(self, text: str) -> str:
        return self.site.redact(text) if self.site is not None else text

    def guard(self) -> None:
        """Before every call (review finding): the bearer travels as plain HTTP, so its
        confidentiality is the tunnel's, and a dropped tunnel must stop the next call, not
        let it leave through the default gateway. A no-op on loopback."""
        if self.site is None or self.interface is None:
            return
        actual = (route_interface if self.route_of is None else self.route_of)(self.site.address)
        if actual is None:
            raise PrivateFileError(f"site {BRAIN_SITE}: the kernel gives no route to its address")
        if actual != self.interface:
            raise PrivateFileError(
                self.redact(
                    f"site {BRAIN_SITE}: the kernel routes its address through {actual}, "
                    f"not the declared {self.interface}"
                )
            )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        route_of: Callable[[Address], str | None] | None = None,
    ) -> BrainSettings:
        env = os.environ if environ is None else environ
        url = env.get("RAIL_BRAIN_URL")
        binding = None
        interface = None
        if url is not None and not is_loopback(url):
            # the environment never holds a machine address, and one there would never be
            # redacted (review finding): a private address is declared as the `brain` site
            raise PrivateFileError(
                f"RAIL_BRAIN_URL names a loopback URL only; declare brain's private address as "
                f"the `{BRAIN_SITE}` site in the host's sites file"
            )
        if url is None:
            url = DEFAULT_URL
            try:
                where = sites_file(env)
                try:
                    where.lstat()
                except FileNotFoundError:
                    pass
                else:
                    site = declared_site(BRAIN_SITE, where)
                    if site is not None:
                        binding = SiteBinding(site=BRAIN_SITE, address=site.address)
                        url = binding.fill(f"http://{ADDRESS_TOKEN}:{BRAIN_PORT}/mcp")
                        if not is_reachable(url):
                            # refused here, as a settings error every caller already handles,
                            # not later as a transport error no gate expects (review finding)
                            raise PrivateFileError(
                                f"site {BRAIN_SITE}: the address is neither loopback nor in a "
                                "private range (RFC 1918, RFC 4193)"
                            )
                        if not is_loopback(url):
                            if site.interface is None:
                                raise PrivateFileError(
                                    f"site {BRAIN_SITE}: declare `interface`, the one the kernel "
                                    "must route its address through (the tunnel's), "
                                    f"in {where}"
                                )
                            interface = site.interface
            except DeployError as exc:
                raise PrivateFileError(str(exc)) from None
            except (OSError, RuntimeError) as exc:
                raise PrivateFileError(f"site {BRAIN_SITE}: {exc}") from None
        path = Path(env.get("RAIL_BRAIN_TOKEN_FILE", DEFAULT_TOKEN_FILE)).expanduser()
        try:
            token = private_token(path)
        except PrivateFileError as exc:
            # each refusal names its own cause: the token here, the site or the URL above
            raise PrivateFileError(f"brain token: {exc}") from exc
        settings = cls(
            url=url,
            token=token,
            token_file=path,
            site=binding,
            interface=interface,
            route_of=route_of,
        )
        settings.guard()  # fail early; the client checks again before every call
        return settings
