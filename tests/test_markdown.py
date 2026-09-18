"""Markdown helpers shared by the design, plan and CLAUDE.md gates."""

from pathlib import Path

from rail import markdown

SPEC = """# red-probe — Design

## 1. Problem
text

## 2. Decisions taken during the brainstorm
| a | b |

### Non-goals of the POC
none

## 8. Proof
### Success criteria
- one
"""

PLAN = """# Plan

Spec: [design](../specs/2026-09-14-red-probe-design.md) — docs/specs/2026-09-14-red-probe-design.md

### Task 1.1: first
- [ ] Run `uv run pytest -q`, expect PASS

### Task 1.2: second
- [ ] Write the code
"""

FENCE = "`" * 3  # built at run time so this file never contains a Markdown fence
CLAUDE_MD = (
    "## Commands\n\n"
    f"{FENCE}bash\nmake ci        # what CI runs\nuv run pytest -q\n$ make nope\n{FENCE}\n\n"
    f'{FENCE}python\nprint("not a command")\n{FENCE}\n'
)


def test_headings_strip_levels_and_numbering() -> None:
    assert markdown.headings(SPEC) == [
        "red-probe — Design",
        "Problem",
        "Decisions taken during the brainstorm",
        "Non-goals of the POC",
        "Proof",
        "Success criteria",
    ]


def test_has_section_matches_aliases_case_insensitively() -> None:
    assert markdown.has_section(SPEC, ("non-goal", "hors périmètre"))
    assert markdown.has_section(SPEC, ("SUCCESS CRITERI",))
    assert not markdown.has_section(SPEC, ("rollback",))


def test_fenced_blocks_filter_by_language() -> None:
    expected = "make ci        # what CI runs\nuv run pytest -q\n$ make nope\n"
    assert markdown.fenced_blocks(CLAUDE_MD, "bash") == [expected]
    assert len(markdown.fenced_blocks(CLAUDE_MD)) == 2


def test_command_lines_drop_comments_blank_lines_and_prompts() -> None:
    block = markdown.fenced_blocks(CLAUDE_MD, "bash")[0]
    assert markdown.command_lines(block) == ["make ci", "uv run pytest -q", "make nope"]


def test_latest_doc_picks_the_newest_dated_file(tmp_path: Path) -> None:
    assert markdown.latest_doc(tmp_path) is None
    (tmp_path / "README.md").write_text("x")
    (tmp_path / "2026-09-01-old.md").write_text("x")
    (tmp_path / "2026-09-14-new.md").write_text("x")
    (tmp_path / "notes.md").write_text("x")
    assert markdown.latest_doc(tmp_path) == tmp_path / "2026-09-14-new.md"


def test_spec_references_are_repository_relative_paths() -> None:
    assert markdown.spec_references(PLAN) == ["docs/specs/2026-09-14-red-probe-design.md"]


def test_task_sections_and_verification() -> None:
    sections = markdown.task_sections(PLAN)
    assert [s.splitlines()[0] for s in sections] == ["### Task 1.1: first", "### Task 1.2: second"]
    assert markdown.has_verification(sections[0]) is True
    assert markdown.has_verification(sections[1]) is False


def test_structure_inside_fences_is_ignored() -> None:
    quoted = (
        f"# Plan\n\n{FENCE}markdown\n## Not a heading\n### Task 9.9: quoted\n{FENCE}\n\n"
        "### Task 1.1: real\nexpect PASS\n"
    )
    assert markdown.headings(quoted) == ["Plan", "Task 1.1: real"]
    assert [s.splitlines()[0] for s in markdown.task_sections(quoted)] == ["### Task 1.1: real"]
    four = f"{FENCE}`python\nx = 1\n{FENCE}\ny = 2\n{FENCE}`\n"
    assert markdown.fenced_blocks(four, "python") == [f"x = 1\n{FENCE}\ny = 2\n"]
    cited = f"see docs/specs/2026-09-14-a.md\n{FENCE}\ndocs/specs/2026-01-01-quoted.md\n{FENCE}\n"
    assert markdown.spec_references(cited) == ["docs/specs/2026-09-14-a.md"]


def test_a_fence_may_close_with_more_backticks() -> None:
    """Review finding: CommonMark closes a fence with at least as many backticks."""
    text = f"{FENCE}bash\nmake ci\n{FENCE}`\n\n### Task 1.1: real\nexpect PASS\n"
    assert markdown.fenced_blocks(text, "bash") == ["make ci\n"]
    assert [s.splitlines()[0] for s in markdown.task_sections(text)] == ["### Task 1.1: real"]


def test_command_lines_keep_a_hash_inside_quotes() -> None:
    assert markdown.command_lines('make check ARGS="x #y"  # comment\n') == [
        'make check ARGS="x #y"'
    ]
