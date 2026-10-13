"""Splitting a diff so every file is read, instead of cutting it where the budget ends."""

import pytest

from rail.reviewer.split import OversizedFile, split_diff


def _file(name: str, lines: int) -> str:
    body = "".join(f"+line {i}\n" for i in range(lines))
    return f"diff --git a/{name} b/{name}\n@@ -0,0 +1,{lines} @@\n{body}"


def test_a_diff_within_budget_is_one_chunk_and_is_not_touched() -> None:
    """The common case must be byte-identical to what the judge saw before: a change that
    fits is reviewed exactly as it always was."""
    diff = _file("a.py", 3) + _file("b.py", 3)
    assert split_diff(diff, budget=10_000) == [diff]


def test_files_are_grouped_up_to_the_budget_and_never_cut() -> None:
    """A half-file patch is unreadable, and a judge that receives one reports on code it
    cannot see the shape of. File boundaries are the only honest cut."""
    diff = _file("a.py", 40) + _file("b.py", 40) + _file("c.py", 40)
    chunks = split_diff(diff, budget=len(_file("a.py", 40)) + 10)

    assert len(chunks) == 3
    assert "".join(chunks) == diff, "every byte of the change reaches a judge"
    for chunk in chunks:
        assert chunk.count("diff --git") == 1


def test_a_file_larger_than_the_budget_is_still_sent_whole() -> None:
    """Truncating it is what the old behaviour did. Sending it alone is the only way to
    read it; the caller is told so it can say the review was partial if it must."""
    big = _file("huge.py", 500)
    chunks = split_diff(big + _file("small.py", 2), budget=len(big) // 4)

    assert big in chunks, "sent whole, not cut"
    assert "".join(chunks) == big + _file("small.py", 2)


def test_an_oversized_file_is_reported_not_hidden() -> None:
    big = _file("huge.py", 500)
    with pytest.raises(OversizedFile) as raised:
        split_diff(big, budget=100, refuse_oversized=True)
    assert "huge.py" in str(raised.value)


def test_a_diff_with_no_file_header_is_one_chunk() -> None:
    """Never lose content because it did not match the expected shape."""
    odd = "something that is not a unified diff\n"
    assert split_diff(odd, budget=10) == [odd]


def test_an_empty_diff_yields_nothing_to_review() -> None:
    assert split_diff("", budget=100) == []


def test_a_whole_piece_has_every_file_complete() -> None:
    from rail.reviewer.split import Sight, sight_of

    diff = _file("a.py", 3) + _file("b.py", 3)
    assert sight_of(diff, diff) == Sight(frozenset({"a.py", "b.py"}), {})


def test_a_cut_second_file_keeps_only_fully_visible_hunks() -> None:
    from rail.reviewer.split import sight_of

    first = _file("a.py", 2)
    header = "diff --git a/b.py b/b.py\n"
    hunk = "@@ -10,2 +10,3 @@\n context\n-old\n+new\n+added\n"
    tail = "@@ -30,2 +31,2 @@\n-one\n-two\n+three\n+four\n"
    diff = first + header + hunk + tail
    sight = sight_of(first + header + hunk + tail[:-6], diff)
    assert sight.complete == frozenset({"a.py"})
    assert sight.cut == {"b.py": ((10, 12),)}


@pytest.mark.parametrize("body", ["+line 0\n", "+line 0\n+line 1"])
def test_a_hunk_cut_midway_has_no_visible_range(body: str) -> None:
    from rail.reviewer.split import sight_of

    diff = _file("a.py", 3)
    visible = diff.split("+line 0", 1)[0] + body
    sight = sight_of(visible, diff)
    assert sight.complete == frozenset()
    assert sight.cut == {"a.py": ()}


def test_a_pure_deletion_hunk_adds_no_new_side_range() -> None:
    from rail.reviewer.split import sight_of

    visible = "diff --git a/a.py b/a.py\n@@ -1,2 +0,0 @@\n-one\n-two\n"
    diff = visible + "@@ -10 +8 @@\n-old\n+new\n"
    assert sight_of(visible, diff).cut == {"a.py": ()}


def test_a_complete_single_line_hunk_uses_the_implicit_count() -> None:
    from rail.reviewer.split import sight_of

    visible = "diff --git a/a.py b/a.py\n@@ -10 +10 @@ section\n-old\n+new\n"
    diff = visible + "@@ -20 +20 @@\n-old\n+new\n"
    assert sight_of(visible, diff).cut == {"a.py": ((10, 10),)}


def test_a_rename_is_seen_under_both_header_paths() -> None:
    from rail.reviewer.split import sight_of

    diff = _file("old.py", 3).replace("b/old.py", "b/new.py")
    assert sight_of(diff, diff).complete == frozenset({"old.py", "new.py"})
    visible = diff.rsplit("+line 2", 1)[0]
    assert sight_of(visible, diff).cut == {"old.py": (), "new.py": ()}


def test_a_directory_named_b_is_kept_as_part_of_the_header_path() -> None:
    from rail.reviewer.split import sight_of

    diff = _file("b/src/x.py", 2) + _file("src/x.py", 2)
    assert sight_of(diff, diff).complete == frozenset({"b/src/x.py", "src/x.py"})


@pytest.mark.parametrize(
    "diff",
    [
        "garbage\n",
        'diff --git "a/sp ace.py" "b/sp ace.py"\n+x\n',
        _file("a.py", 2) + "diff --git a/b.py b/b.py trailing\n+x\n",
    ],
)
def test_unreadable_headers_give_an_empty_sight(diff: str) -> None:
    from rail.reviewer.split import sight_of

    sight = sight_of(diff, diff)
    assert sight.complete == frozenset() and sight.cut == {}
