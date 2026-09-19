"""Where the vendored brain-v42 contracts come from. `BRAIN_REF` is the brain-v42 commit or
tag the files were copied from; the operator replaces it by the release tag brain-v42
announces after migration 054, then `make contracts-check` must still pass."""

from __future__ import annotations

from pathlib import Path

# Annotated tag on 9bdb38122fb7603927f18d1aaec8544ae4b1300d (PR #151, in production since
# 2026-09-18 12:19Z); it names the CONTRACT version and moves only on a contract break.
BRAIN_REF = "delivery-attestations-v1.0"
BRAIN_CHECKOUT = Path.home() / "hawkixs_infra/git_repo/ReD_v1/projects/brain-v42"
HEADLESS_AGENTS_TAG = "headless-agents-v0.2.0"
# sha256 of the vendored files as published at BRAIN_REF (verified 2026-09-18).
CONTRACT_SHA256 = {
    "delivery_attestations.json": (
        "b654b9a3479f02c3d80baf1d49777fb93356b5d34a7cd17b70e5e2205fd9a3fe"
    ),
    "delivery_finding_codes.json": (
        "0978138aee9ae4be24b12b81e0097960eadf1e78f0936b1a7074b60516f208b4"
    ),
}
