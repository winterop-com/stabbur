"""Tests for the runtime supervisor (A4) — spawn/stop, process-group kill, port retry, sweep.

Model-agnostic: these spawn trivial Python processes that bind a port and sleep, so they run in
the normal (non-slow) gate. POSIX-only, like the supervisor itself.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from stabbur.runtime import supervisor

# Binds the given port and sleeps; exits 1 with a bind-error message if the port is taken.
_BIND_SCRIPT = (
    "import socket,sys,time\n"
    "s=socket.socket()\n"
    "try:\n"
    "    s.bind(('127.0.0.1',int(sys.argv[1]))); s.listen()\n"
    "except OSError:\n"
    "    sys.stderr.write('bind: address already in use\\n'); sys.stderr.flush(); sys.exit(1)\n"
    "time.sleep(300)\n"
)

# Binds, forks a child (both in the new session's process group), records the child's pid.
_GROUP_SCRIPT = (
    "import socket,sys,time,os\n"
    "s=socket.socket(); s.bind(('127.0.0.1',int(sys.argv[1]))); s.listen()\n"
    "pid=os.fork()\n"
    "if pid==0:\n"
    "    time.sleep(300)\n"
    "else:\n"
    "    open(sys.argv[2],'w').write(str(pid)); time.sleep(300)\n"
)


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(supervisor, "_runtimes_root", lambda: tmp_path / "runtimes")


def _bind_cmd(port: int) -> list[str]:
    return [sys.executable, "-c", _BIND_SCRIPT, str(port)]


def _reap(pid: int) -> None:
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def _stub_ps(monkeypatch: pytest.MonkeyPatch, pid: int, command: str) -> None:
    """Make the cmdline read deterministic for one pid.

    The sweep *decision* (owner-dead + pid-alive + cmdline match) is what these tests exercise;
    the real ps read is covered separately by ``test_process_command_reads_an_established_process``.
    Stubbing it avoids a macOS-only flake where a *freshly spawned* framework-Python launcher
    intermittently reports a blank command to ps (a real orphan has long since settled).
    """
    monkeypatch.setattr(supervisor, "_process_command", lambda p: command if p == pid else "")


def test_spawn_binds_then_stop_terminates() -> None:
    handle = supervisor.spawn(_bind_cmd, name="test")
    try:
        assert handle.poll() is None  # still running
        assert supervisor._port_open("127.0.0.1", handle.port)  # bound the port
        assert (handle.state_dir / "meta.json").is_file()
    finally:
        handle.stop()
    assert handle.poll() is not None  # terminated
    assert not handle.state_dir.exists()  # state cleaned up
    assert handle not in supervisor._live


def test_spawn_kills_the_whole_process_group(tmp_path: Path) -> None:
    # A runtime that forks a worker: stop() must killpg the group, not just the direct child.
    child_pidfile = tmp_path / "child.pid"

    def cmd(port: int) -> list[str]:
        return [sys.executable, "-c", _GROUP_SCRIPT, str(port), str(child_pidfile)]

    handle = supervisor.spawn(cmd, name="group")
    try:
        for _ in range(50):  # wait for the child pid to be recorded
            if child_pidfile.is_file():
                break
            time.sleep(0.05)
        child_pid = int(child_pidfile.read_text())
        assert supervisor._pid_alive(child_pid)
        handle.stop()
        time.sleep(0.3)
        assert not supervisor._pid_alive(child_pid)  # the forked worker died with the group
    finally:
        handle.stop()
        _reap(int(child_pidfile.read_text()) if child_pidfile.is_file() else 0)


def test_spawn_retries_past_an_occupied_port(monkeypatch: pytest.MonkeyPatch) -> None:
    # Occupy a port, force spawn to pick it first, and confirm it retries onto a free one (V-6).
    occupied = socket.socket()
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    taken_port = occupied.getsockname()[1]
    free_port = supervisor.find_free_port()
    picks = iter([taken_port, free_port, free_port])
    monkeypatch.setattr(supervisor, "find_free_port", lambda: next(picks))

    handle = supervisor.spawn(_bind_cmd, name="retry")
    try:
        assert handle.port == free_port  # skipped the occupied port
        assert handle.poll() is None
    finally:
        handle.stop()
        occupied.close()


def test_spawn_raises_on_immediate_non_bind_exit() -> None:
    # A command that exits at once for a non-port reason surfaces the error (no infinite retry).
    def cmd(_port: int) -> list[str]:
        return [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]

    with pytest.raises(RuntimeError, match="exited immediately"):
        supervisor.spawn(cmd, name="boom")


def _raw_sleeper(port: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        _bind_cmd(port), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )


def _write_meta(root: Path, *, owner_pid: int, proc: subprocess.Popen[bytes], port: int) -> Path:
    d = root / f"entry-{proc.pid}"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(
        json.dumps(
            {
                "owner_pid": owner_pid,
                "pid": proc.pid,
                "pgid": os.getpgid(proc.pid),
                "cmd": _bind_cmd(port),
                "port": port,
            }
        )
    )
    return d


# A sentinel owner pid the tests treat as a crashed (dead) stabbur. Using a real just-exited pid is
# racy — a busy CI runner reuses it, so the owner then looks alive and the orphan is skipped.
_DEAD_OWNER = 999_999_999


def _owner_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    real = supervisor._pid_alive
    monkeypatch.setattr(supervisor, "_pid_alive", lambda p: False if p == _DEAD_OWNER else real(p))


def test_sweep_reaps_orphan_of_a_dead_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "runtimes"
    _owner_dead(monkeypatch)  # the recorded owner is treated as a crashed stabbur
    port = supervisor.find_free_port()
    sleeper = _raw_sleeper(port)
    _stub_ps(monkeypatch, sleeper.pid, " ".join(_bind_cmd(port)))  # live cmdline matches the meta
    try:
        entry = _write_meta(root, owner_pid=_DEAD_OWNER, proc=sleeper, port=port)
        reaped = supervisor.sweep_orphans()
        assert sleeper.pid in reaped  # owner dead + pid alive + cmdline match → real killpg
        # The test owns the sleeper, so reap the zombie the signal left behind before checking
        # (in production the orphan is init's child and auto-reaped).
        sleeper.wait(timeout=2)
        assert not supervisor._pid_alive(sleeper.pid)  # actually killed
        assert not entry.exists()  # state dir cleaned
    finally:
        _reap(sleeper.pid)


def test_sweep_skips_runtime_of_a_live_owner(tmp_path: Path) -> None:
    root = tmp_path / "runtimes"
    port = supervisor.find_free_port()
    sleeper = _raw_sleeper(port)
    try:
        # owner_pid = this test process (alive) → sweep must NOT touch it.
        entry = _write_meta(root, owner_pid=os.getpid(), proc=sleeper, port=port)
        assert supervisor.sweep_orphans() == []
        assert supervisor._pid_alive(sleeper.pid)  # left running
        assert entry.exists()  # dir preserved (a live stabbur owns it)
    finally:
        _reap(sleeper.pid)


def test_sweep_leaves_a_reused_pid_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "runtimes"
    _owner_dead(monkeypatch)
    port = supervisor.find_free_port()
    sleeper = _raw_sleeper(port)
    # The live process really is the sleeper; its cmdline won't match the (other-binary) meta below.
    _stub_ps(monkeypatch, sleeper.pid, " ".join(_bind_cmd(port)))
    try:
        # Dead owner, but the recorded cmd/port don't match the live pid → treat as a reused pid.
        d = root / f"entry-{sleeper.pid}"
        d.mkdir(parents=True)
        (d / "meta.json").write_text(
            json.dumps(
                {
                    "owner_pid": _DEAD_OWNER,
                    "pid": sleeper.pid,
                    "pgid": os.getpgid(sleeper.pid),
                    "cmd": ["/some/other-binary", "--port", "1"],
                    "port": 1,
                }
            )
        )
        assert supervisor.sweep_orphans() == []  # cmdline guard prevented a wrong kill
        assert supervisor._pid_alive(sleeper.pid)
        assert not d.exists()  # stale entry still cleaned up
    finally:
        _reap(sleeper.pid)


def test_process_command_reads_an_established_process() -> None:
    # The real ps read (used by the PID-reuse guard) returns a non-empty command line for a
    # long-established process — here the test suite's own, which is stable unlike a just-spawned
    # framework-Python launcher. This is the real-ps coverage the stubbed sweep tests don't give.
    assert supervisor._process_command(os.getpid())
