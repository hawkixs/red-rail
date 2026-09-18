"""Small Markdown readers. Regex-based on purpose: the gates need headings, fences and
references, not a full parser (no new dependency)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?|[IVXLC]+\.)\s+")
_FENCE = re.compile(r"^(`{3,})([\w+-]*)[^\n]*\n(.*?)^\1`*[ \t]*$", re.MULTILINE | re.DOTALL)
_DATED = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")
_SPEC_REF = re.compile(r"docs/specs/[\w.\-/]+\.md")
_TASK = re.compile(r"^###\s+Task\b.*$", re.MULTILINE)
_VERIFICATION = re.compile(
    r"\b(expect(?:ed)?|verify|assert|should (?:pass|fail)|exit code)\b", re.IGNORECASE
)


def _mask_fences(text: str) -> str:
    """Fenced code blanked out (same length, newlines kept) so `#` lines and `### Task`
    headings quoted inside a fence are never read as structure."""
    return _FENCE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def headings(text: str) -> list[str]:
    """Heading titles at every level, numbering prefixes stripped (`## 1. Problem` → `Problem`)."""
    return [_NUMBERING.sub("", m.group(2)).strip() for m in _HEADING.finditer(_mask_fences(text))]


def has_section(text: str, aliases: Iterable[str]) -> bool:
    wanted = [a.lower() for a in aliases]
    return any(alias in h.lower() for h in headings(text) for alias in wanted)


def fenced_blocks(text: str, lang: str | None = None) -> list[str]:
    return [m.group(3) for m in _FENCE.finditer(text) if lang is None or m.group(2) == lang]


def _strip_comment(line: str) -> str:
    """Drop a trailing ` # comment`, unless the `#` sits inside quotes."""
    for index in range(len(line) - 1):
        if line[index] == " " and line[index + 1] == "#":
            before = line[:index]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                return before.rstrip()
    return line


def command_lines(block: str) -> list[str]:
    """Executable lines of a shell fence: comments, blank lines and `$ ` prompts removed."""
    lines = []
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith("$ "):
            line = line[2:].strip()
        line = _strip_comment(line)
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def latest_doc(directory: Path) -> Path | None:
    """The newest `<date>-<topic>.md` in a directory (name order = date order)."""
    if not directory.is_dir():
        return None
    dated = sorted(p for p in directory.glob("*.md") if _DATED.match(p.name))
    return dated[-1] if dated else None


def spec_references(text: str) -> list[str]:
    """`docs/specs/…md` paths cited outside fenced code, first occurrence first."""
    seen: list[str] = []
    for ref in _SPEC_REF.findall(_mask_fences(text)):
        if ref not in seen:
            seen.append(ref)
    return seen


def task_sections(text: str) -> list[str]:
    """Bodies of `### Task …` sections, each starting with its heading line. Headings quoted
    inside fenced code (a plan that shows a sample plan) do not start a section."""
    starts = [m.start() for m in _TASK.finditer(_mask_fences(text))]
    if not starts:
        return []
    return [text[s:e].rstrip() for s, e in zip(starts, starts[1:] + [len(text)], strict=True)]


def has_verification(section: str) -> bool:
    return _VERIFICATION.search(section) is not None
