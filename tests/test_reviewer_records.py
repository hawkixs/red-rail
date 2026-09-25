"""A records-only pull request is judged mechanically, on its form (spec
2026-09-25-spool-replaces-committed-mirrors, decision 5)."""

from pathlib import Path

import pytest

from rail.ledger import AttestationKind
from rail.ledger.file import FileLedger
from rail.reviewer.records import mechanical_verdict
from tests.helpers import added_receipt_diff

DELETED = (
    "diff --git a/docs/receipts/old.json b/docs/receipts/old.json\ndeleted file mode 100644\n"
    "index 1111111..0000000\n--- a/docs/receipts/old.json\n+++ /dev/null\n@@ -1 +0,0 @@\n-{}\n"
)
RENAMED = (
    "diff --git a/docs/receipts/old.json b/docs/receipts/new.json\nsimilarity index 100%\n"
    "rename from docs/receipts/old.json\nrename to docs/receipts/new.json\n"
)
MODIFIED = (
    "diff --git a/docs/receipts/old.json b/docs/receipts/old.json\n"
    "index 1111111..2222222 100644\n--- a/docs/receipts/old.json\n+++ b/docs/receipts/old.json\n"
    '@@ -1 +1 @@\n-{}\n+{"x": 1}\n'
)
UNPARSED = (
    "diff --git a/src/pwn me.py b/src/pwn me.py\nnew file mode 100644\n"
    "index 0000000..1111111\n--- /dev/null\n+++ b/src/pwn me.py\n@@ -0,0 +1 @@\n+print(1)\n"
)


def _receipt(root: Path, *, project: str = "red-alpha", key: str = "d1", sha: str = "a") -> Path:
    ledger = FileLedger(root)
    record = ledger.attest(
        project, AttestationKind.DEPLOYED, {"sha": sha * 40}, issuer="op", idempotency_key=key
    )
    return ledger.path_of(record)


def test_well_formed_receipts_are_approved(tmp_path: Path) -> None:
    verdict = mechanical_verdict(added_receipt_diff(_receipt(tmp_path)), project="red-alpha")
    assert verdict.verdict == "approve" and verdict.findings == []
    assert (verdict.mode, verdict.round, verdict.artifact, verdict.providers) == (
        "mechanical",
        "mechanical",
        "records",
        (),
    )


def test_a_tampered_receipt_is_refused(tmp_path: Path) -> None:
    path = _receipt(tmp_path)
    path.write_text(path.read_text().replace("a" * 40, "b" * 40))
    verdict = mechanical_verdict(added_receipt_diff(path), project="red-alpha")
    assert verdict.verdict == "request_changes"
    assert [f.title for f in verdict.findings] == ["digest does not match the content"]


@pytest.mark.parametrize(
    ("diff", "path", "change"),
    [
        (DELETED, "docs/receipts/old.json", "deleted"),
        (RENAMED, "docs/receipts/new.json", "renamed"),
        (MODIFIED, "docs/receipts/old.json", "modified"),
    ],
)
def test_an_existing_receipt_change_is_refused_next_to_a_valid_one(
    tmp_path: Path, diff: str, path: str, change: str
) -> None:
    verdict = mechanical_verdict(added_receipt_diff(_receipt(tmp_path)) + diff, project="red-alpha")
    assert verdict.verdict == "request_changes"
    assert [(f.file, f.title) for f in verdict.findings] == [
        (path, f"an existing receipt is {change}")
    ]


def test_an_unparsed_header_is_refused(tmp_path: Path) -> None:
    verdict = mechanical_verdict(
        UNPARSED + added_receipt_diff(_receipt(tmp_path)), project="red-alpha"
    )
    assert verdict.verdict == "request_changes"
    assert [f.title for f in verdict.findings] == ["unparsed diff header"]


def test_another_project_a_wrong_name_and_a_reused_key_are_refused(tmp_path: Path) -> None:
    foreign = added_receipt_diff(_receipt(tmp_path / "f", project="red-beta"))
    misnamed = added_receipt_diff(_receipt(tmp_path / "m", key="d2"), name="x.json")
    twice = added_receipt_diff(_receipt(tmp_path / "one", key="k")) + added_receipt_diff(
        _receipt(tmp_path / "two", key="k", sha="c")
    )
    titles = [
        f.title
        for f in mechanical_verdict(foreign + misnamed + twice, project="red-alpha").findings
    ]
    assert titles == [
        "receipt of another project",
        "misnamed receipt",
        "idempotency key used twice",
    ]


def test_an_empty_diff_is_refused() -> None:
    verdict = mechanical_verdict("", project="red-alpha")
    assert verdict.verdict == "request_changes"
    assert [f.title for f in verdict.findings] == ["no receipt in the diff"]
