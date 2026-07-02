"""Tests for the benchmark core: suite loading, code extraction, and the Docker executor.

The executor tests spawn real containers, so they're marked ``slow`` (excluded from the
default ``make test``; run with ``make test-slow`` / ``pytest -m slow``). They skip when
Docker isn't available so the suite still passes on a machine without it.
"""

import pytest
from kodo_mcp_benchmark import core

pytestmark = pytest.mark.filterwarnings("ignore")

# --- fast: no Docker -------------------------------------------------------


def test_suites_load_and_language_propagates() -> None:
    names = core.available_suites()
    assert {"python-basics", "rust-basics"} <= set(names)
    py = core.load_suite("python-basics")
    assert py.language == "python"
    assert py.problems and all(p.language == "python" for p in py.problems)  # inherited from the suite
    assert core.load_suite("rust-basics").problems[0].language == "rust"


def test_unknown_suite_errors() -> None:
    with pytest.raises(FileNotFoundError):
        core.load_suite("does-not-exist")


def test_extract_code_prefers_tagged_then_last_block() -> None:
    # Language-tagged block wins even if a later untagged block exists.
    text = "try this:\n```python\nprint(1)\n```\nor maybe\n```\nprint(2)\n```"
    assert core.extract_code(text, "python") == "print(1)"
    # No tag match -> last fenced block.
    assert core.extract_code("```\na\n```\n```\nb\n```", "python") == "b"
    # No fence -> whole text, trimmed.
    assert core.extract_code("  just code  ", "python") == "just code"


def test_docker_available_returns_bool() -> None:
    assert isinstance(core.docker_available(), bool)


def test_unsupported_language_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported language"):
        core.run_code("cobol", "DISPLAY 'HI'.")


# --- slow: real Docker containers ------------------------------------------

_needs_docker = pytest.mark.skipif(not core.docker_available(), reason="docker not available")


@pytest.mark.slow
@_needs_docker
def test_python_solution_scored_pass_and_fail() -> None:
    problem = next(p for p in core.load_suite("python-basics").problems if p.id == "sum-two")
    good = core.evaluate(problem, "a, b = map(int, input().split())\nprint(a + b)")
    assert good.passed and all(c.passed for c in good.cases)
    bad = core.evaluate(problem, "print(999)")
    assert not bad.passed


@pytest.mark.slow
@_needs_docker
def test_sandbox_blocks_network_and_enforces_timeout() -> None:
    net = core.run_code("python", "import socket; socket.create_connection(('1.1.1.1', 53), 2)")
    assert net.exit_code != 0  # --network=none
    hang = core.run_code("python", "while True: pass", timeout_s=3)
    assert hang.timed_out


@pytest.mark.slow
@_needs_docker
def test_rust_compiles_runs_and_reports_compile_errors() -> None:
    problem = next(p for p in core.load_suite("rust-basics").problems if p.id == "reverse-string")
    good = (
        "use std::io::*;\n"
        "fn main() { let mut s = String::new(); stdin().read_line(&mut s).unwrap(); "
        'println!("{}", s.trim().chars().rev().collect::<String>()); }'
    )
    assert core.evaluate(problem, good).passed
    broken = core.evaluate(problem, 'fn main() { let x: i32 = "nope"; }')
    assert not broken.passed and broken.cases[0].error  # compile error captured, not a crash
