"""The repository's one address policy (spec 2026-09-24-template-alignment, decision 15). An
address that is only an example comes from the documentation ranges (RFC 5737, RFC 3849);
loopback, unspecified and link-local are never a machine's. Shared by the scan of the tracked
files and by the scan of every rendered scaffold."""

import ipaddress
import re

from rail.contract_guard import _DOTTED

# Colon-separated hex groups, at least three groups; parsing decides whether it is an address,
# so a time of day (`12:34:56`) or a slice (`[::2]`) never counts.
_COLONS = re.compile(r"(?<![\w:.])([0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})(?![\w:.])")

ALLOWED = tuple(
    ipaddress.ip_network(net)
    for net in (
        "192.0.2.0/24",  # TEST-NET-1
        "198.51.100.0/24",  # TEST-NET-2
        "203.0.113.0/24",  # TEST-NET-3
        "127.0.0.0/8",  # loopback
        "0.0.0.0/32",  # the unspecified address, named in refusals
        "2001:db8::/32",  # IPv6 documentation
        # loopback, unspecified and the deprecated IPv4-compatible block: never a machine's
        # address, and a slice such as `[::2]` parses into it
        "::/96",
        "fe80::/10",  # link-local: an interface's, never a machine's reachable address
        # YAML 1.1 reads an all-digit IPv6 as a base-60 integer, and no documentation address
        # is written in digits only: the one example that test needs, and nothing around it
        "2001::1/128",
    )
)


def _candidates(text: str) -> list[str]:
    return _DOTTED.findall(text) + _COLONS.findall(text)


def _foreign(text: str) -> list[str]:
    """Literals that parse as an IP address and fall outside the documentation, loopback and
    link-local ranges. The contract guard's lookarounds: `1.2.3.4.5` and versions are not
    addresses."""
    found = []
    for candidate in _candidates(text):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not any(address.version == net.version and address in net for net in ALLOWED):
            found.append(candidate)
    return found
