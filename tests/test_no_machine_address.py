"""This repository is public: no machine address may be committed to it, not in code, not
in a test, not in a receipt. An address that is only an example comes from the documentation
ranges (RFC 5737, RFC 3849); a real one lives in the host's private files (`sites.yaml`)."""

import ipaddress
import re
import subprocess
from pathlib import Path

from rail.contract_guard import _DOTTED

ROOT = Path(__file__).resolve().parents[1]

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


def test_the_scan_catches_a_private_address_and_spares_the_documentation_ranges() -> None:
    # built at run time, so this file never carries the literals it is looking for
    private = ".".join(["10", "0", "0", "7"])
    unique_local = ":".join(["fd7a", "", "7"])
    assert _foreign(f"deploy on {private}.") == [private]
    assert _foreign(f"reach [{unique_local}]:8081") == [unique_local]
    assert _foreign("http://192.0.2.2:8081, 198.51.100.4, 203.0.113.10:8080, 127.0.0.1") == []
    assert _foreign("http://[2001:db8::10]:9204, ::1, fe80::1%eth0, a[::2], at 12:34:56") == []
    assert _foreign(f"the build id is {private}.5 and version 1.26.8") == []


def test_no_tracked_file_carries_a_machine_address() -> None:
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.split(b"\0")
    offenders = []
    for name in filter(None, listed):
        path = ROOT / name.decode()
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if _foreign(line):
                # the location only: the report must not print the address a second time
                offenders.append(f"{name.decode()}:{number}")
    assert not offenders, "machine addresses committed to a public repository: " + ", ".join(
        offenders
    )
