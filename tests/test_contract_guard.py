"""What may never enter a contract, checked where it is written rather than after.

The contract is the one artefact the rail makes immutable — mirrored in the repository and
in brain, correctable only by an amendment that leaves the faulty revision in history. It
was also the least validated at write time: `hygiene.receipts` checks the digest and the
filename, never the content.

Only facts are refused here, never judgements. "This objective is well phrased" is not
checkable and is not attempted.
"""

import pytest

from rail.contract_guard import Unwritable, refuse_unwritable


@pytest.mark.parametrize(
    "text",
    [
        "deploy on 198.51.100.4 behind the mesh",
        "deploy on 198.51.100.4.",  # a sentence ending in an address: the natural way to write it
        "reach 198.51.100.4, then the proxy",
        "(198.51.100.4)",
        "198.51.100.4 is the target",
    ],
)
def test_an_address_is_refused_wherever_it_sits_in_a_sentence(text: str) -> None:
    """A guard bypassed by a full stop guards nothing: the most natural way to write an
    address is at the end of a sentence."""
    with pytest.raises(Unwritable, match=r"198\.51\.100\.4"):
        refuse_unwritable([text])


def test_a_longer_dotted_run_is_not_an_address() -> None:
    """Five groups is not an address, and must not be reported as one."""
    refuse_unwritable(["the build id is 198.51.100.4.5 and it is not an address"])


def test_a_literal_address_is_refused() -> None:
    """The operator's standing rule is that no address belongs in a spec. A contract is a
    stronger case: a spec can be edited, a contract revision cannot."""
    with pytest.raises(Unwritable, match="198.51.100.4"):
        refuse_unwritable(["deploy on 198.51.100.4 behind the mesh"])
    with pytest.raises(Unwritable, match="address"):
        refuse_unwritable(["the service answers on 203.0.113.10:8080"])


def test_version_numbers_and_digests_are_not_addresses() -> None:
    """Four dotted numbers are an address; three are a version, and a digest is neither."""
    refuse_unwritable(
        [
            "pin Go 1.26.8 and staticcheck v0.8.1",
            "the image is named by sha256:" + "a" * 64,
            "the criterion covers 99.9 percent of runs",
        ]
    )


def test_a_documentation_address_is_allowed() -> None:
    """RFC 5737 exists to be written down; refusing it would push people to invent a real
    one, which is the opposite of the intent."""
    refuse_unwritable(["the probe answers on 192.0.2.10:9204"])


def test_a_credential_shaped_string_is_refused() -> None:
    for secret in (
        "ghp_" + "a" * 36,
        "github_pat_" + "b" * 22,
        "AKIA" + "C" * 16,
        "-----BEGIN OPENSSH PRIVATE KEY-----",
    ):
        with pytest.raises(Unwritable, match="credential|secret"):
            refuse_unwritable([f"use {secret} to authenticate"])


def test_the_message_names_what_was_found_and_where() -> None:
    """A refusal that does not say which field and which string costs more than it saves."""
    with pytest.raises(Unwritable) as raised:
        refuse_unwritable(["fine"], constraints=["reach 198.51.100.4 first"])
    message = str(raised.value)
    assert "198.51.100.4" in message and "constraint" in message


def test_empty_and_absent_fields_are_fine() -> None:
    refuse_unwritable([])
    refuse_unwritable(["an objective"], constraints=None)
