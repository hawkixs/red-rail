"""Splitting a diff at file boundaries, so a large change is read rather than cut short.

The reviewer used to hand the judge `diff[:max_diff_chars]` and record `diff_truncated`.
Measured on the first external pull request: 972 686 characters against a 200 000 budget, so
the judge ruled on 21% of the change and said `approve`. Raising the budget only moves the
number — every threshold has a bigger pull request behind it, and a judge that swallows
27 000 lines at once does not read them either. The unit of review has to be bounded, not the
change.

Pure text in, list of chunks out: no network, no model, nothing to stub.
"""

from __future__ import annotations

import re

FILE_HEADER = re.compile(r"^diff --git ", re.MULTILINE)


class OversizedFile(Exception):
    """One file's own patch exceeds the budget; it cannot be grouped, only sent alone."""


def _files(diff: str) -> list[str]:
    """The diff cut at `diff --git` boundaries, content preserved exactly.

    Anything before the first header — and a diff with no header at all — is kept as its own
    piece: never lose content because it did not match the expected shape."""
    starts = [m.start() for m in FILE_HEADER.finditer(diff)]
    if not starts:
        return [diff] if diff else []
    bounds = ([0] if starts[0] > 0 else []) + starts
    return [diff[a:b] for a, b in zip(bounds, bounds[1:] + [len(diff)], strict=True)]


def _name(chunk: str) -> str:
    first = chunk.split("\n", 1)[0]
    return first.removeprefix("diff --git ").split(" b/", 1)[0].removeprefix("a/") or "?"


def split_diff(diff: str, *, budget: int, refuse_oversized: bool = False) -> list[str]:
    """`diff` as chunks no larger than `budget`, cutting only between files.

    A file whose own patch exceeds the budget gets its own chunk, ALONE — not whole: nothing
    here can make a patch smaller than a model will take, and `build_prompt` still bounds it.
    What isolating it buys is that the cut falls inside one file instead of swallowing the
    files behind it, and that `oversized()` can name it, so the verdict says it was cut rather
    than claiming the change was read. `refuse_oversized` turns that case into an error for a
    caller that would rather stop than review a file it cannot bound.
    """
    if not diff:
        return []
    pieces = _files(diff)
    if len(diff) <= budget:
        return [diff]

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if len(piece) > budget:
            if refuse_oversized:
                raise OversizedFile(f"{_name(piece)}: {len(piece)} characters exceeds {budget}")
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece)  # alone: the cut stays inside this file, and is reported
            continue
        if current and len(current) + len(piece) > budget:
            chunks.append(current)
            current = ""
        current += piece
    if current:
        chunks.append(current)
    return chunks


def oversized(chunks: list[str], *, budget: int) -> list[str]:
    """The names of files that could not be bounded — what a caller reports instead of
    claiming the whole change was read."""
    return [_name(c) for c in chunks if len(c) > budget]
