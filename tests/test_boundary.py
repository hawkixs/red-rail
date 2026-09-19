"""The two boundary rules of ADR-0001, frozen as data: brain never learns a new gate (the
evaluator's finding codes are a closed list) and the attestation API is a versioned
contract. Both files are brain-v42 publications vendored at a pinned ref; the parity test
reads that ref from the sibling checkout and skips when it is absent (CI)."""

import json
import subprocess
from pathlib import Path

import pytest

from rail.contracts import (
    ATTESTATION_CONTRACT,
    FINDING_CODES,
    attestation_contract,
    finding_codes,
    vendored_path,
)
from rail.contracts.pins import BRAIN_CHECKOUT, BRAIN_REF, CONTRACT_SHA256

EXPECTED_CODES = {
    "base_mismatch",
    "binding_identity_invalid",
    "binding_identity_mismatch",
    "binding_missing",
    "binding_unobserved",
    "check_cancelled",
    "check_failed",
    "check_missing",
    "check_neutral",
    "check_pending",
    "check_skipped",
    "completion_action_invalid",
    "context_changed",
    "context_digest_missing",
    "context_error",
    "context_missing",
    "context_predicate_duplicate",
    "context_predicate_missing",
    "context_predicate_unexpected",
    "context_proof_invalid",
    "delivery_disabled",
    "delivery_disposition_terminal",
    "delivery_terminal",
    "dependency_generation_mismatch",
    "dependency_predicate_duplicate",
    "dependency_predicate_missing",
    "dependency_predicate_unexpected",
    "dependency_receipt_mismatch",
    "dependency_receipt_missing",
    "dependency_unsuccessful",
    "head_mismatch",
    "integration_identity_missing",
    "merge_conflict",
    "mergeability_unknown",
    "observation_error",
    "observation_incomplete",
    "observation_missing",
    "observation_stale",
    "pr_draft",
    "pr_not_merged",
    "reopen_required",
    "review_approval_missing",
    "review_changes_requested",
}  # ADR-0001 amendment 1, 2026-09-15 — 43 codes


def test_the_finding_codes_are_the_closed_list_of_adr_0001() -> None:
    assert len(EXPECTED_CODES) == 43
    assert set(finding_codes()) == EXPECTED_CODES
    assert FINDING_CODES == frozenset(EXPECTED_CODES)


def test_the_attestation_contract_is_v1_with_the_two_tools_at_1_0() -> None:
    contract = attestation_contract()
    assert contract["contract_version"] == 1
    assert contract["tools"] == {
        "brain_delivery_attest": "1.0",
        "brain_delivery_attestation_list": "1.0",
    }
    assert set(contract["record_fields"]) == {
        "id",
        "ticket_id",
        "contract_revision",
        "kind",
        "payload",
        "digest",
        "issuer_project",
        "issuer_identity",
        "idempotency_key",
        "emitted_at",
        "recorded_at",
    }
    assert contract["uniqueness"] == ["ticket_id", "issuer_project", "idempotency_key"]
    assert contract["replay_equality"] == [
        "kind",
        "digest",
        "issuer_identity",
        "contract_revision",
        "emitted_at",
    ]
    assert contract["kind"]["reserved"] == ["integrated", "fulfilled"]
    assert contract["digest"]["format"].startswith("64 lowercase hexadecimal")
    assert ATTESTATION_CONTRACT["digest"]["domain_prefix"] == "brain-delivery-attestation:v1\n"


def test_the_tool_error_codes_are_the_published_closed_list() -> None:
    codes = attestation_contract()["error_codes"]
    assert set(codes["tool"]) == {
        "contract_not_found",
        "delivery_disabled",
        "idempotency_key_reused",
        "invalid_cursor",
        "invalid_emitted_at",
        "invalid_issuer",
        "invalid_kind",
        "invalid_limit",
        "invalid_payload",
        "invalid_scope",
        "invalid_window",
        "not_allowed",
        "revision_not_found",
        "ticket_not_found",
    }
    assert set(codes["transport"]) == {"invalid_arguments", "delivery_unavailable"}
    # the two vocabularies overlap on exactly one word, with one meaning: the feature is off
    assert set(codes["tool"]) & FINDING_CODES == {"delivery_disabled"}


@pytest.mark.skipif(not (BRAIN_CHECKOUT / ".git").exists(), reason="no brain-v42 checkout")
def test_vendored_files_equal_the_pinned_ref() -> None:
    for name in ("delivery_finding_codes.json", "delivery_attestations.json"):
        published = subprocess.run(
            ["git", "-C", str(BRAIN_CHECKOUT), "show", f"{BRAIN_REF}:docs/contracts/{name}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert json.loads(published) == json.loads(vendored_path(name).read_text()), name


def test_vendored_files_match_the_pinned_digests() -> None:
    """Runs everywhere (CI included): the bytes we ship are the bytes brain-v42 tagged."""
    import hashlib

    assert BRAIN_REF == "delivery-attestations-v1.0"
    for name, expected in CONTRACT_SHA256.items():
        digest = hashlib.sha256(vendored_path(name).read_bytes()).hexdigest()
        assert digest == expected, f"{name}: {digest} != pinned {expected}"


def test_vendored_files_ship_with_the_package() -> None:
    for name in ("delivery_finding_codes.json", "delivery_attestations.json"):
        assert vendored_path(name).is_file()
        assert vendored_path(name).parent == Path(vendored_path(name)).parent
