"""Private files (tokens, App keys) are read only when they are really private."""

import os
from pathlib import Path

import pytest

from rail.private import PrivateFileError, private_token, read_private_file


def _private(path: Path, content: str, mode: int = 0o600) -> Path:
    path.write_text(content)
    path.chmod(mode)
    return path


def test_reads_a_0600_regular_file(tmp_path: Path) -> None:
    path = _private(tmp_path / "token", "abc\n")
    assert read_private_file(path) == b"abc\n"


def test_refuses_a_group_or_world_readable_file(tmp_path: Path) -> None:
    path = _private(tmp_path / "token", "abc", mode=0o640)
    with pytest.raises(PrivateFileError, match="mode 640"):
        read_private_file(path)


def test_refuses_a_symlink(tmp_path: Path) -> None:
    target = _private(tmp_path / "real", "abc")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(PrivateFileError, match="symlink"):
        read_private_file(link)


def test_refuses_a_relative_path_and_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PrivateFileError, match="absolute"):
        read_private_file(Path("relative/token"))
    with pytest.raises(PrivateFileError, match="not found"):
        read_private_file(tmp_path / "missing")


def test_refuses_a_directory(tmp_path: Path) -> None:
    with pytest.raises(PrivateFileError, match="regular file"):
        read_private_file(tmp_path)


def test_private_token_is_the_trimmed_printable_content(tmp_path: Path) -> None:
    assert private_token(_private(tmp_path / "token", "s3cret-Token_42\n")) == "s3cret-Token_42"
    with pytest.raises(PrivateFileError, match="one line"):
        private_token(_private(tmp_path / "two", "abc\ndef\n"))
    with pytest.raises(PrivateFileError, match="printable ASCII"):
        private_token(_private(tmp_path / "utf", "sécret\n"))
    with pytest.raises(PrivateFileError, match="empty"):
        private_token(_private(tmp_path / "empty", "\n"))
    with pytest.raises(PrivateFileError, match="too long"):
        private_token(_private(tmp_path / "long", "x" * 8193))


def test_owner_must_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _private(tmp_path / "token", "abc")
    monkeypatch.setattr(os, "getuid", lambda: os.stat(path).st_uid + 1)
    with pytest.raises(PrivateFileError, match="owned by"):
        read_private_file(path)
