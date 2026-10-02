"""The paths a `diff --git` header names, read in one place (ticket b1df8461).

A header is readable only when it is whole: `a/<path> b/<path>` and nothing after. A path with
a space, or one git quotes, is not readable, and a caller fails closed on it — a file whose
header we read only in part would otherwise be classified on a path that is not its own. Both
sides count: a rename's new path is as much the change as its old one."""

from __future__ import annotations

import re

HEADER = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)$", re.MULTILINE)
ANY_HEADER = re.compile(r"^diff --git ", re.MULTILINE)


def paths(diff: str) -> list[str]:
    """Every path a readable header names, old side then new side, each once."""
    return list(dict.fromkeys(p for m in HEADER.finditer(diff) for p in (m["a"], m["b"])))


def parsed(diff: str) -> bool:
    """Every `diff --git` line is a readable header."""
    return len(ANY_HEADER.findall(diff)) == len(HEADER.findall(diff))
