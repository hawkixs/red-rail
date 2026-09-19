"""brain-v42's published contracts, vendored as data (ADR-0001 rule 1, ADR-0002).

red-rail never imports `brain_v42`: the evaluator's finding codes and the attestation API
are read from JSON files copied from the brain-v42 repository at `pins.BRAIN_REF`. A change
in either file is a change of contract, justified on both sides.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent


def vendored_path(name: str) -> Path:
    return _HERE / name


@cache
def _load(name: str) -> dict[str, Any]:
    return json.loads(vendored_path(name).read_text(encoding="utf-8"))


def finding_codes() -> tuple[str, ...]:
    """The closed vocabulary of `DeliveryFinding.code` brain's evaluator can emit."""
    return tuple(_load("delivery_finding_codes.json")["codes"])


def attestation_contract() -> dict[str, Any]:
    """`brain_delivery_attest` / `brain_delivery_attestation_list` v1.0 as published."""
    return _load("delivery_attestations.json")


FINDING_CODES: frozenset[str] = frozenset(finding_codes())
ATTESTATION_CONTRACT: dict[str, Any] = attestation_contract()
TOOL_ERROR_CODES: frozenset[str] = frozenset(ATTESTATION_CONTRACT["error_codes"]["tool"])
TRANSPORT_ERROR_CODES: frozenset[str] = frozenset(ATTESTATION_CONTRACT["error_codes"]["transport"])
