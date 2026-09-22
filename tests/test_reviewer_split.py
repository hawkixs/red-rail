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
