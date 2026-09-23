"""This repository is public: no machine address may be committed to it, not in code, not
in a test, not in a receipt. An address that is only an example comes from the documentation
ranges (RFC 5737); a real one lives in the host's private files (`sites.yaml`)."""

import ipaddress
import subprocess
from pathlib import Path

from rail.contract_guard import _DOTTED

ROOT = Path(__file__).resolve().parents[1]

ALLOWED = tuple(
    ipaddress.ip_network(net)
    for net in (
        "192.0.2.0/24",  # TEST-NET-1
        "198.51.100.0/24",  # TEST-NET-2
        "203.0.113.0/24",  # TEST-NET-3
        "127.0.0.0/8",  # loopback
        "0.0.0.0/32",  # the unspecified address, named in refusals
    )
)


def _foreign(text: str) -> list[str]:
    """Dotted quads that parse as IPv4 and fall outside the documentation and loopback ranges.
    The same lookarounds as the contract guard: `1.2.3.4.5` and versions are not addresses."""
    found = []
    for candidate in _DOTTED.findall(text):
        try:
            address = ipaddress.IPv4Address(candidate)
        except ValueError:
            continue
        if not any(address in net for net in ALLOWED):
            found.append(candidate)
    return found


def test_the_scan_catches_a_private_address_and_spares_the_documentation_ranges() -> None:
    # built at run time, so this file never carries the literal it is looking for
    private = ".".join(["10", "0", "0", "7"])
    assert _foreign(f"deploy on {private}.") == [private]
    assert _foreign("http://192.0.2.2:8081, 198.51.100.4, 203.0.113.10:8080, 127.0.0.1") == []
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
