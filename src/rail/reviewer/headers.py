"""The paths a `diff --git` header names, read in one place (ticket b1df8461).

A header is readable only when it is whole: `a/<path> b/<path>` and nothing after. A path with
a space, or one git quotes, is not readable, and a caller fails closed on it — a file whose
header we read only in part would otherwise be classified on a path that is not its own. Both
sides count: a rename's new path is as much the change as its old one."""

from __future__ import annotations

import re
from collections.abc import Collection

HEADER = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)$", re.MULTILINE)
ANY_HEADER = re.compile(r"^diff --git ", re.MULTILINE)


class Ambiguous:
    """More than one header path matches a finding's spelling (spec D2)."""


AMBIGUOUS = Ambiguous()


def resolve(spelling: str, paths: Collection[str]) -> str | None | Ambiguous:
    """Strip at most one prefix: a directory can be named `a` or `b` legitimately
    (spec 2026-10-13-reviewer-partial-view, D2)."""
    candidates = {spelling}
    for prefix in ("./", "a/", "b/"):
        if spelling.startswith(prefix):
            candidates.add(spelling[len(prefix) :])
            break
    matches = candidates.intersection(paths)
    if len(matches) > 1:
        return AMBIGUOUS
    return next(iter(matches), None)


def paths(diff: str) -> list[str]:
    """Every path a readable header names, old side then new side, each once."""
    return list(dict.fromkeys(p for m in HEADER.finditer(diff) for p in (m["a"], m["b"])))


def parsed(diff: str) -> bool:
    """Every `diff --git` line is a readable header."""
    return len(ANY_HEADER.findall(diff)) == len(HEADER.findall(diff))
