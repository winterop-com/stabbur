"""Tests for the sandbox library (no container runs — those need Docker + images)."""

import pytest
from heim_sandbox import RUNTIMES, SUPPORTED_LANGUAGES, docker_available, run_code


def test_docker_available_returns_bool() -> None:
    assert isinstance(docker_available(), bool)


def test_supported_languages() -> None:
    assert set(SUPPORTED_LANGUAGES) == {"python", "rust"}
    assert RUNTIMES["python"].image == "python:3.13-slim"
    assert RUNTIMES["python"].filename == "main.py"


def test_run_code_rejects_unknown_language() -> None:
    with pytest.raises(ValueError, match="unsupported language"):
        run_code("cobol", "print(1)")


# Live container smoke tests: they run real Docker, so they're marked `slow` (deselected by
# `make check` / CI's `-m "not slow"`) and skip in-body when no daemon is reachable. They pin
# the security guarantees end-to-end — the hardening flags (--user, --network=none, --read-only,
# timeout) are only meaningful if a real container actually enforces them, and running as an
# unprivileged user on a read-only rootfs is exactly where a runtime (notably rustc compiling to
# /tmp) can break. Run them with Docker up via `uv run pytest -m slow packages/heim-sandbox`.


def _need_docker() -> None:
    if not docker_available():
        pytest.skip("docker daemon not reachable")


@pytest.mark.slow
def test_python_runs_as_unprivileged_user() -> None:
    _need_docker()
    r = run_code("python", "import os; print(os.getuid())")
    assert r.exit_code == 0
    assert r.stdout.strip() == "65534"  # --user 65534:65534 (nobody), not root


@pytest.mark.slow
def test_python_stdin_and_args_roundtrip() -> None:
    _need_docker()
    r = run_code("python", "import sys; print(sys.stdin.read().upper(), sys.argv[1:])", stdin="ok", args=["a"])
    assert r.exit_code == 0
    assert r.stdout.strip() == "OK ['a']"


@pytest.mark.slow
def test_rust_compiles_to_tmpfs_and_runs() -> None:
    # The risky path: rustc must write the binary to the exec tmpfs at /tmp and run it as an
    # unprivileged user on an otherwise read-only rootfs (HOME=/tmp makes the toolchain happy).
    _need_docker()
    r = run_code("rust", 'fn main() { for a in std::env::args().skip(1) { println!("{a}"); } }', args=["hi"])
    assert r.exit_code == 0
    assert r.stdout.strip() == "hi"


@pytest.mark.slow
def test_network_is_blocked() -> None:
    _need_docker()
    r = run_code("python", "import socket; socket.create_connection(('1.1.1.1', 80), 2); print('LEAK')")
    assert r.exit_code != 0
    assert "LEAK" not in r.stdout  # --network=none: no egress


@pytest.mark.slow
def test_rootfs_and_work_mount_are_read_only() -> None:
    _need_docker()
    root = run_code("python", "open('/oops', 'w'); print('WROTE')")
    assert root.exit_code != 0 and "WROTE" not in root.stdout  # --read-only rootfs
    work = run_code("python", "open('/work/x', 'w'); print('WROTE')")
    assert work.exit_code != 0 and "WROTE" not in work.stdout  # /work mounted :ro


@pytest.mark.slow
def test_wall_clock_timeout_is_enforced() -> None:
    _need_docker()
    r = run_code("python", "import time; time.sleep(30)", timeout_s=3)
    assert r.timed_out is True
    assert r.exit_code == 124
