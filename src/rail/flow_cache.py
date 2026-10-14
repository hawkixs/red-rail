"""Cache only GitHub inputs; failed or immature reads remain retryable on the host."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import UnionType
from typing import TYPE_CHECKING, Any, get_args, get_origin, get_type_hints

from rail.flow import FlowPull, GitHubEvidence
from rail.reviewer.github import GitHubError

if TYPE_CHECKING:
    from rail.reviewer.github import GitHubApp

VERSION = "rail-flow/v1"


def default_cache_root() -> Path:
    """Keep private evidence in host state, honoring the XDG state directory."""
    state = os.environ.get("XDG_STATE_HOME")
    root = Path(state) if state else Path.home() / ".local" / "state"
    return root / "red-rail" / "flow"


def cache_key(pull: FlowPull, *, version: str = "rail-flow/v1") -> str:
    """Hash canonical identity inputs so repository names never become paths."""
    identity = (pull.repository, pull.number, pull.merge_sha, version)
    canonical = json.dumps(identity, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CachedGitHub:
    """Preserve fetch-time completeness; aging an early fetch cannot prove absence."""

    evidence: GitHubEvidence
    fetched_at: datetime
    complete: bool


def is_complete(evidence: GitHubEvidence, *, now: datetime) -> bool:
    """Require successful mature reads and actual completion of every pre-merge run."""
    if evidence.suites is None or evidence.runs is None or evidence.statuses is None:
        return False
    merged_at = evidence.pull.merged_at
    if now - merged_at <= timedelta(hours=24):
        return False
    for run in evidence.runs:
        if run.started_at is None:
            return False
        if run.started_at < merged_at and (
            run.status != "completed" or run.completed_at is None or run.completed_at > now
        ):
            return False
    return True


def _decode(value: Any, kind: Any) -> Any:
    """Validate exact persisted fields and types before trusting a complete claim."""
    origin = get_origin(kind)
    if origin is UnionType:
        if value is None and type(None) in get_args(kind):
            return None
        return _decode(value, next(option for option in get_args(kind) if option is not type(None)))
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError("cache sequence is not a list")
        return tuple(_decode(item, get_args(kind)[0]) for item in value)
    if kind is datetime:
        if not isinstance(value, str):
            raise ValueError("cache timestamp is not text")
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() != timedelta(0):
            raise ValueError("cache timestamp is not UTC")
        return parsed.astimezone(UTC)
    if is_dataclass(kind):
        names = {field.name for field in fields(kind)}
        if not isinstance(value, dict) or set(value) != names:
            raise ValueError("cache fields do not match")
        hints = get_type_hints(kind)
        return kind(**{name: _decode(value[name], hints[name]) for name in names})
    if type(value) is not kind or (kind is int and value <= 0):
        raise ValueError("cache field has the wrong type")
    return value


def _json_default(value: Any) -> str:
    """Serialize timestamps explicitly rather than coercing unknown object types."""
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("unsupported cache value")


class FlowCache:
    """Cache misses and storage failures never remove a discovered merged pull."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _load(self, pull: FlowPull, *, now: datetime) -> CachedGitHub | None:
        """Reuse only matching entries that were already complete when fetched."""
        try:
            key = cache_key(pull)
            raw = json.loads((self.root / f"{key}.json").read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {
                "key",
                "version",
                "evidence",
                "fetched_at",
                "complete",
            }:
                return None
            if raw["key"] != key or raw["version"] != VERSION:
                return None
            cached = _decode(
                {name: raw[name] for name in ("evidence", "fetched_at", "complete")}, CachedGitHub
            )
            if (
                not cached.complete
                or cached.evidence.pull != pull
                or cached.fetched_at > now
                or not is_complete(cached.evidence, now=cached.fetched_at)
            ):
                return None
            return cached
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError):
            return None

    def _write(self, cached: CachedGitHub) -> None:
        """Replace privately and atomically so interrupted writes cannot prove absence."""
        temporary: Path | None = None
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.root.chmod(0o700)
            key = cache_key(cached.evidence.pull)
            payload = {"key": key, "version": VERSION, **asdict(cached)}
            encoded = json.dumps(payload, default=_json_default, separators=(",", ":"))
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.root, prefix=".flow-", delete=False
            ) as handle:
                temporary = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.root / f"{key}.json")
        except (OSError, UnicodeError, ValueError, TypeError):
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def get_or_read(self, pull: FlowPull, *, github: GitHubApp, now: datetime) -> GitHubEvidence:
        """Read final-head sources independently; incomplete entries retry all three."""
        cached = self._load(pull, now=now)
        if cached is not None:
            return cached.evidence

        def read(reader: Callable[[str, str], list[Any]], step: str) -> tuple[Any, ...] | None:
            try:
                return tuple(reader(pull.repository, pull.head_sha))
            except GitHubError as exc:
                print(pull.repository, step, type(exc).__name__, file=sys.stderr)
                return None

        evidence = GitHubEvidence(
            pull,
            read(github.check_suites, "checks"),
            read(github.flow_check_runs, "checks"),
            read(github.status_history, "statuses"),
        )
        self._write(CachedGitHub(evidence, now, is_complete(evidence, now=now)))
        return evidence
