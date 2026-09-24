"""This repository is public: no machine address may be committed to it, not in code, not
in a test, not in a receipt. An address that is only an example comes from the documentation
ranges (RFC 5737, RFC 3849); a real one lives in the host's private files (`sites.yaml`). The
policy itself lives in `tests/addresses.py`, shared with the scan of rendered scaffolds."""

import subprocess
from pathlib import Path

from tests.addresses import _foreign

ROOT = Path(__file__).resolve().parents[1]


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
