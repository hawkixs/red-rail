"""Private files: the MCP token, the GitHub App key. Read only when really private.

Every component of the path is opened without following symlinks on the final one; the
file must be a regular file owned by the current user with no group/other permission bits.
The brain-v42 observer applies the same rules (`delivery_observer/auth.py`); the rail
carries its own copy rather than importing brain_v42 (ADR-0003).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class PrivateFileError(Exception):
    """The file is not private enough to trust, or cannot be read."""


def read_private_file(path: Path) -> bytes:
    if not path.is_absolute():
        raise PrivateFileError(f"{path}: an absolute path is required")
    try:
        if path.is_symlink():
            raise PrivateFileError(f"{path}: refusing a symlink")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError as exc:
        raise PrivateFileError(f"{path}: not found") from exc
    except OSError as exc:
        raise PrivateFileError(f"{path}: {exc.strerror}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise PrivateFileError(f"{path}: not a regular file")
        if info.st_uid != os.getuid():
            raise PrivateFileError(f"{path}: must be owned by uid {os.getuid()}")
        if info.st_mode & 0o077:
            raise PrivateFileError(
                f"{path}: mode {oct(info.st_mode & 0o777)[2:]} — group/other bits must be 0"
            )
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)


def private_token(path: Path) -> str:
    """A bearer token as the brain-v42 reference client reads it: the raw content of a
    private file, trimmed, one line of printable ASCII, at most 8192 bytes."""
    raw = read_private_file(path)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise PrivateFileError(f"{path}: a token is printable ASCII") from exc
    value = text.strip()
    if not value:
        raise PrivateFileError(f"{path}: empty token file")
    if len(value) > 8192:
        raise PrivateFileError(f"{path}: token too long")
    if "\n" in value or "\r" in value:
        raise PrivateFileError(f"{path}: a token file holds one line")
    if any(not 33 <= ord(c) <= 126 for c in value):
        raise PrivateFileError(f"{path}: a token is printable ASCII without spaces")
    return value
