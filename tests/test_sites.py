"""The host's sites file: where a private target's machine is, never in a repository."""

from ipaddress import ip_address
from pathlib import Path

import pytest

from rail.deploy import DeployError
from rail.deploy.sites import load_site, redact_address, sites_file, substitute_address

V4 = "192.0.2.10"  # RFC 5737 and RFC 3849: documentation addresses only
V6 = "2001:db8::10"


def _sites(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    path = tmp_path / "sites.yaml"
    path.write_text(text)
    path.chmod(mode)
    return path


def test_a_private_file_gives_the_site_its_address(tmp_path: Path) -> None:
    path = _sites(tmp_path, f"sites:\n  private-1:\n    address: {V4}\n")
    assert str(load_site("private-1", path).address) == V4


def test_an_ipv6_address_is_accepted(tmp_path: Path) -> None:
    path = _sites(tmp_path, f'sites:\n  private-6:\n    address: "{V6}"\n')
    assert load_site("private-6", path).address.version == 6


def test_a_file_others_can_read_is_refused(tmp_path: Path) -> None:
    path = _sites(tmp_path, f"sites:\n  private-1:\n    address: {V4}\n", mode=0o644)
    with pytest.raises(DeployError, match=r"site private-1: .*mode 644"):
        load_site("private-1", path)


def test_a_symlinked_file_is_refused(tmp_path: Path) -> None:
    real = _sites(tmp_path, f"sites:\n  private-1:\n    address: {V4}\n")
    link = tmp_path / "link.yaml"
    link.symlink_to(real)
    with pytest.raises(DeployError, match="symlink"):
        load_site("private-1", link)


def test_a_missing_file_names_the_site_the_path_and_the_fix(tmp_path: Path) -> None:
    where = tmp_path / "absent.yaml"
    with pytest.raises(DeployError) as caught:
        load_site("private-1", where)
    message = str(caught.value)
    assert "site private-1" in message and str(where) in message and "0600" in message


def test_an_unknown_site_lists_the_known_ones(tmp_path: Path) -> None:
    text = f"sites:\n  private-1:\n    address: {V4}\n  private-2:\n    address: 192.0.2.20\n"
    path = _sites(tmp_path, text)
    with pytest.raises(
        DeployError, match=r"private-3 is not declared .*known: private-1, private-2"
    ):
        load_site("private-3", path)


@pytest.mark.parametrize("address", ["private-1.example", "0.0.0.0", "'::'", "192.0.2.300"])
def test_an_address_is_a_literal_that_does_not_publish_everywhere(
    tmp_path: Path, address: str
) -> None:
    path = _sites(tmp_path, f"sites:\n  private-1:\n    address: {address}\n")
    with pytest.raises(DeployError, match="not a valid sites file"):
        load_site("private-1", path)


def test_a_scoped_ipv6_address_is_refused(tmp_path: Path) -> None:
    """Review finding: `fe80::1%eth0` used to validate here and fail only after ssh. A scoped
    address is bound to one interface: Docker can neither bind it, nor can it be written in
    a URL, so a private target refuses it just as it refuses `0.0.0.0`."""
    path = _sites(tmp_path, 'sites:\n  private-1:\n    address: "fe80::1%eth0"\n')
    with pytest.raises(DeployError, match="scope"):
        load_site("private-1", path)


def test_an_unquoted_all_digit_ipv6_asks_to_be_quoted(tmp_path: Path) -> None:
    """Review focus 1: YAML 1.1 reads 2001:0:0:0:0:0:0:1 as a base-60 integer."""
    path = _sites(tmp_path, "sites:\n  private-1:\n    address: 2001:0:0:0:0:0:0:1\n")
    with pytest.raises(DeployError, match="quoted string"):
        load_site("private-1", path)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "sites:\n",
        f"sites:\n  Private_1:\n    address: {V4}\n",
        f"sites:\n  private-1:\n    address: {V4}\n    port: 22\n",
    ],
)
def test_an_empty_or_malformed_file_is_refused_by_name(tmp_path: Path, text: str) -> None:
    """Review focus 2."""
    path = _sites(tmp_path, text)
    with pytest.raises(DeployError, match=r"sites\.yaml is not a valid sites file") as caught:
        load_site("private-1", path)
    assert "declare it on this host" in str(caught.value)


def test_the_environment_names_the_file_never_the_address(tmp_path: Path) -> None:
    assert sites_file({}) == Path("~/.config/red-rail/sites.yaml").expanduser()
    assert sites_file({"RAIL_SITES_FILE": str(tmp_path / "x.yaml")}) == tmp_path / "x.yaml"


def test_a_relative_file_is_refused() -> None:
    """Review focus 3: never read relative to the current directory."""
    with pytest.raises(DeployError, match="absolute"):
        load_site("private-1", Path("sites.yaml"))


def test_the_token_is_replaced_and_ipv6_is_bracketed() -> None:
    url = "http://${BIND_ADDRESS}:9204/healthz?full=1"
    assert substitute_address(url, ip_address(V4)) == f"http://{V4}:9204/healthz?full=1"
    assert substitute_address(url, ip_address(V6)) == f"http://[{V6}]:9204/healthz?full=1"


def test_redaction_respects_number_boundaries() -> None:
    text = (
        "connect to host 192.0.2.1 port 22; 192.0.2.10 and 198.51.100.1.192.0.2.1 differ; "
        "192.0.2.1."
    )
    assert redact_address(text, ip_address("192.0.2.1"), "private-1") == (
        "connect to host private-1 port 22; 192.0.2.10 and 198.51.100.1.192.0.2.1 differ; "
        "private-1."
    )


def test_a_bare_ipv6_address_followed_by_a_port_is_redacted() -> None:
    """Review focus 6: Docker writes port conflicts as `Bind for <ip>:<port> failed`. The
    bare pass's lookahead rejected a match followed by `:`, so this case slipped through. A
    hex suffix, or a longer address sharing the same prefix, must still stay untouched."""
    assert redact_address("Bind for 2001:db8::10:9204 failed", ip_address(V6), "private-6") == (
        "Bind for private-6:9204 failed"
    )
    assert redact_address("2001:db8::10:abcd", ip_address(V6), "private-6") == "2001:db8::10:abcd"
    assert redact_address("2001:db8::100:9204", ip_address(V6), "private-6") == "2001:db8::100:9204"


def test_ipv6_redaction_takes_the_brackets_and_ignores_case() -> None:
    """Review focus 5."""
    text = (
        f"GET http://[{V6.upper()}]:9204/healthz failed; "
        f"ssh: connect to host {V6} port 22; 2001:db8::100 is another host"
    )
    assert redact_address(text, ip_address(V6), "private-6") == (
        "GET http://private-6:9204/healthz failed; "
        "ssh: connect to host private-6 port 22; 2001:db8::100 is another host"
    )
