"""red-rail: the ReD delivery rail. One standard, executable gates, measured drift."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("red-rail")  # one source of truth: [project].version in pyproject.toml
except PackageNotFoundError:  # the source tree on sys.path, no installed distribution
    __version__ = "0+unknown"
