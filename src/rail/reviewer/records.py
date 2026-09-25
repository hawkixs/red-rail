"""A records-only pull request, judged on its form with no judge (spec
2026-09-25-spool-replaces-committed-mirrors, decision 5): every file is an added receipt that
loads, verifies, is named after its digest, names this project, and uses a key no other added
receipt uses. A collision with a receipt already on the base branch is `hygiene.receipts`' in CI."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from rail.ledger import Record
from rail.ledger.file import receipt_filename
from rail.reviewer.verdict import Finding, ReviewVerdict

_FILE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)$", re.MULTILINE)
_ANY_HEADER = re.compile(r"^diff --git ", re.MULTILINE)
_MAX_FINDINGS = 100  # ReviewVerdict.findings' own bound


def _starts(diff: str) -> list[re.Match[str]]:
    return list(_FILE.finditer(diff))


def _patches(diff: str) -> list[tuple[str, str, str]]:
    starts = _starts(diff)
    if not starts:
        return []
    ends = [m.start() for m in starts[1:]] + [len(diff)]
    return [
        (m.group("a"), m.group("b"), diff[m.start() : end])
        for m, end in zip(starts, ends, strict=True)
    ]


def _added_content(patch: str) -> str:
    _, _, hunks = patch.partition("\n@@")
    lines = hunks.split("\n")[1:]  # the rest of the first hunk header goes
    return "\n".join(line[1:] for line in lines if line.startswith("+"))


def _change(patch: str) -> str:
    if "\ndeleted file mode " in patch:
        return "deleted"
    return "renamed" if "\nrename from " in patch else "modified"


def mechanical_verdict(diff: str, *, project: str) -> ReviewVerdict:
    findings: list[Finding] = []
    seen: dict[tuple[str, str], str] = {}

    def refuse(path: str, title: str, evidence: str) -> None:
        findings.append(
            Finding(severity="blocking", file=path, title=title, evidence=evidence[:2000])
        )

    starts = _starts(diff)
    if not starts:
        refuse(
            "(diff)",
            "no receipt in the diff",
            "a records-only pull request adds at least one receipt",
        )
    elif len(_ANY_HEADER.findall(diff)) != len(starts) or diff[: starts[0].start()].strip():
        # C1, fail closed: a path a space or a git quote defeats `_FILE` hides a whole file
        # from every check below it — never classify on the paths we could read.
        refuse(
            "(diff)",
            "unparsed diff header",
            "every file of a records-only pull request is a receipt named in a plain "
            "diff --git a/<path> b/<path> header",
        )
    else:
        for a, b, patch in _patches(diff):
            path = b
            if "\nnew file mode " not in patch:
                refuse(
                    path,
                    f"an existing receipt is {_change(patch)}",
                    "the ledger is append-only: a pull request may only add receipts",
                )
                continue
            if a != b:
                refuse(path, "receipt path changes", "an added receipt keeps one path")
                continue
            try:  # a JSON or a validation error is a ValueError
                record = Record.model_validate(json.loads(_added_content(patch)))
            except ValueError as exc:
                refuse(path, "not a receipt", str(exc))
                continue
            if not record.verify():
                refuse(path, "digest does not match the content", f"it claims {record.digest}")
                continue
            expected = receipt_filename(record)
            if PurePosixPath(path).name != expected:
                refuse(path, "misnamed receipt", f"its content names it {expected}")
            if record.project != project:
                refuse(path, "receipt of another project", f"{record.project!r}, not {project!r}")
            key = (record.project, record.idempotency_key)
            if key in seen:
                refuse(
                    path,
                    "idempotency key used twice",
                    f"{record.idempotency_key!r}, also in {seen[key]}",
                )
            seen.setdefault(key, path)
    shown = findings[:_MAX_FINDINGS]
    summary = (
        f"mechanical: {len(starts)} receipt(s) added, all well formed"
        if not findings
        else f"mechanical: {len(findings)} problem(s) in the receipts"
        + (f", the first {_MAX_FINDINGS} listed" if len(findings) > _MAX_FINDINGS else "")
    )
    return ReviewVerdict(
        verdict="request_changes" if findings else "approve",
        summary=summary,
        findings=shown,
        mode="mechanical",
        providers=(),
        round="mechanical",
        artifact="records",
    )
