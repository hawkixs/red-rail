"""The reviewer reads a file's paths from its `diff --git` header in one place (ticket
b1df8461): a header is readable only when it is whole, and both of its sides count."""

import pytest

from rail.reviewer.headers import parsed, paths

PLAIN = "diff --git a/src/a.py b/src/a.py\n+x\n"
RENAMED = "diff --git a/docs/x.md b/src/evil.py\nrename from docs/x.md\nrename to src/evil.py\n"
# a file named `README.md b/src/evil.py`: git writes the path twice, spaces included
HIDDEN = "diff --git a/README.md b/src/evil.py b/README.md b/src/evil.py\n+evil\n"


def test_a_plain_header_names_its_path_once() -> None:
    assert paths(PLAIN) == ["src/a.py"] and parsed(PLAIN)


def test_a_rename_names_both_sides() -> None:
    assert paths(RENAMED) == ["docs/x.md", "src/evil.py"] and parsed(RENAMED)


def test_a_header_read_only_in_part_is_not_parsed() -> None:
    assert paths(HIDDEN) == [] and not parsed(PLAIN + HIDDEN)


def test_a_quoted_path_is_not_parsed() -> None:
    assert not parsed('diff --git "a/sp ace.py" "b/sp ace.py"\n+x\n')


def test_no_header_is_parsed_and_names_nothing() -> None:
    assert parsed("") and paths("") == []


@pytest.mark.parametrize("spelling", ["src/x.py", "./src/x.py", "a/src/x.py", "b/src/x.py"])
def test_a_finding_path_resolves_to_its_single_match(spelling: str) -> None:
    from rail.reviewer.headers import resolve

    assert resolve(spelling, {"src/x.py"}) == "src/x.py"


def test_a_path_outside_the_diff_has_no_match() -> None:
    from rail.reviewer.headers import resolve

    assert resolve("other.py", {"src/x.py"}) is None
    assert resolve("./b/src/x.py", {"src/x.py"}) is None


def test_a_literal_b_directory_can_make_a_finding_path_ambiguous() -> None:
    from rail.reviewer.headers import AMBIGUOUS, Ambiguous, resolve

    assert isinstance(AMBIGUOUS, Ambiguous)
    assert resolve("b/src/x.py", {"src/x.py", "b/src/x.py"}) is AMBIGUOUS
    assert resolve("b/src/x.py", {"b/src/x.py"}) == "b/src/x.py"
