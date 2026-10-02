"""The reviewer reads a file's paths from its `diff --git` header in one place (ticket
b1df8461): a header is readable only when it is whole, and both of its sides count."""

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
