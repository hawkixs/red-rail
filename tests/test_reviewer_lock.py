"""Reviewer passes serialize across processes, without consulting the host configuration."""

import fcntl
import subprocess
import sys
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from rail.cli import main
from rail.commands import reviewer
from rail.publish import state_directory


@pytest.mark.parametrize("no_wait", [False, True])
def test_once_serializes_passes_across_processes(monkeypatch, no_wait):
    monkeypatch.setattr(reviewer, "_config", lambda path: SimpleNamespace(repositories=[]))
    seen = []

    def once(config, **kwargs):
        seen.append(kwargs)
        return 0

    monkeypatch.setattr(reviewer, "_once", once)
    runner = CliRunner()
    assert runner.invoke(main, ["reviewer", "once"]).exit_code == 0
    lock = state_directory() / "reviewer.lock"
    assert lock.parent.stat().st_mode & 0o777 == 0o700
    assert lock.stat().st_mode & 0o777 == 0o600
    inode = lock.stat().st_ino
    seen.clear()

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, sys; "
            "f = open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX); "
            "print('locked', flush=True); sys.stdin.readline()",
            str(lock),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline() == "locked\n"
        if no_wait:
            out = runner.invoke(main, ["reviewer", "once", "--no-wait"])
            assert out.exit_code == 75, out.output
            assert out.stderr == "reviewer: a running pass holds the host lock\n"
            assert not seen
        else:
            # Release only after the command reports contention, proving it waited.
            echo = reviewer.click.echo

            def release(message, **kwargs):
                if kwargs.get("err") and "waiting" in message:
                    assert not seen
                    holder.stdin.write("release\n")
                    holder.stdin.flush()
                return echo(message, **kwargs)

            monkeypatch.setattr(reviewer.click, "echo", release)
            out = runner.invoke(main, ["reviewer", "once"])
            assert out.exit_code == 0, out.output
            assert out.stderr == "reviewer: waiting for a running pass to finish\n"
            assert seen == [{"only": None, "pr": None}]
    finally:
        holder.communicate("release\n", timeout=5)
    assert lock.stat().st_ino == inode
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_run_releases_the_lock_between_iterations(monkeypatch):
    config = SimpleNamespace(poll_seconds=1)
    monkeypatch.setattr(reviewer, "_config", lambda path: config)
    passes = []

    def once(config, **kwargs):
        with (state_directory() / "reviewer.lock").open("a") as stream:
            with pytest.raises(BlockingIOError):
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        passes.append(kwargs)
        return 0

    def sleep(seconds):
        with (state_directory() / "reviewer.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if len(passes) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(reviewer, "_once", once)
    monkeypatch.setattr(reviewer.time, "sleep", sleep)
    out = CliRunner().invoke(main, ["reviewer", "run"])
    assert out.exit_code == 1
    assert len(passes) == 2


def test_lock_uses_the_default_state_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(reviewer, "_config", lambda path: SimpleNamespace(repositories=[]))
    monkeypatch.setattr(reviewer, "_once", lambda *args, **kwargs: 0)
    out = CliRunner().invoke(main, ["reviewer", "once"])
    assert out.exit_code == 0, out.output
    assert (tmp_path / ".local/state/red-rail/reviewer.lock").is_file()
